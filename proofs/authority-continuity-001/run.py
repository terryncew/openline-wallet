#!/usr/bin/env python3
"""Deterministic apparatus for AUTHORITY-CONTINUITY-001."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

HERE = Path(__file__).resolve().parent
PROFILE = HERE / "frozen" / "OPENLINE-AUTHORITY-CONTINUITY-v0.1.md"
EXPECTED_PREREG = "3807bc088bc0236e2ce869018f9355c05581fa6082c3f948daddab90adb62e1a"
EXPECTED_PROFILE = "2f96264c7dbc92cf13587ab932eb4d14aa876f6cfce6950d2f96909f3efe0c26"
PREFIX = "OPENLINE-LEVERAGE-001/PREREG-v1.0/"
DESCRIPTION = "Prepare and execute a vendor transfer of 100 units in two phases. Phase 1 (prepare): assemble the transfer order. Phase 2 (execute): commit the transfer to the receiver-guarded ledger."
CRITERIA = "phase-1 evidence must contain a well-formed transfer order (payer, payee, amount = 100, phase = 1); phase-2 evidence must reference the accepted phase-1 receipt and contain a matching execution record (phase = 2)."


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key(role: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256((PREFIX + role).encode()).digest())


def pub(private: Ed25519PrivateKey) -> str:
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def signed(value: dict, private: Ed25519PrivateKey, include_hash: bool = False) -> dict:
    body = {k: v for k, v in value.items() if k not in ("signature", "payload_hash")}
    payload_hash = digest(body)
    result = dict(body)
    if include_hash:
        result["payload_hash"] = payload_hash
    result["signature"] = private.sign(bytes.fromhex(payload_hash)).hex()
    return result


def verify_artifact(artifact: dict, public_key: str) -> bool:
    body = {k: v for k, v in artifact.items() if k not in ("signature", "payload_hash")}
    try:
        if "payload_hash" in artifact and artifact["payload_hash"] != digest(body):
            return False
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(bytes.fromhex(artifact["signature"]), bytes.fromhex(digest(body)))
        return True
    except (ValueError, KeyError, InvalidSignature):
        return False


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def worker(role: str, phase: int, receipt_id: str | None) -> int:
    # Worker output crosses a process boundary. Its HOME is recorded so the
    # parent can prove that A and B did not share a runtime home.
    evidence = ({"payer": "owner", "payee": "vendor", "amount": 100, "phase": 1}
                if phase == 1 else
                {"payer": "owner", "payee": "vendor", "amount": 100, "phase": 2,
                 "phase_1_receipt_id": receipt_id})
    print(json.dumps({"role": role, "worker_pubkey": pub(key(role)), "home": os.environ["HOME"], "evidence": evidence}, sort_keys=True))
    return 0


class Receiver:
    def __init__(self, owner_pubkey: str, receiver_key: Ed25519PrivateKey, mandate_id: str):
        self.owner_pubkey = owner_pubkey
        self.receiver_key = receiver_key
        self.mandate_id = mandate_id
        self.seq = 0
        self.grants: dict[str, dict] = {}
        self.revoked: dict[str, int] = {}
        self.receipts: list[dict] = []
        self.run_log: list[dict] = []
        self.ledger: list[dict] = []
        self.used: set[tuple[str, str]] = set()

    def verify_owner(self, artifact: dict) -> bool:
        return artifact.get("issuer_pubkey", artifact.get("owner_pubkey")) == self.owner_pubkey and verify_artifact(artifact, self.owner_pubkey)

    def receipt(self, worker_pubkey: str, action: str, grant_id: str | None, decision: str, reasons: list[str]) -> dict:
        previous = digest(self.receipts[-1]) if self.receipts else None
        rid = "R-" + str(len(self.receipts) + 1).zfill(3)
        receipt = signed({"receipt_id": rid, "mandate_id": self.mandate_id,
                          "worker_pubkey": worker_pubkey, "action": action,
                          "grant_id": grant_id, "decision": decision,
                          "reason_codes": reasons, "prev_receipt_hash": previous}, self.receiver_key, True)
        self.receipts.append(receipt)
        return receipt

    def record(self, kind: str, request: dict, decision: str, reasons: list[str], receipt: dict | None = None) -> dict:
        self.seq += 1
        entry = {"seq": self.seq, "kind": kind, "request": request, "decision": decision,
                 "reason_codes": reasons, "receipt_id": receipt["receipt_id"] if receipt else None,
                 "receipt_log_length": len(self.receipts), "ledger_length": len(self.ledger)}
        self.run_log.append(entry)
        return entry

    def admit_mandate(self, mandate: dict) -> None:
        ok = self.verify_owner(mandate) and mandate["mandate_id"] == self.mandate_id
        receipt = self.receipt(self.owner_pubkey, "mandate-admitted", None, "ACCEPTED", []) if ok else None
        self.record("mandate", {"artifact": mandate}, "ACCEPTED" if ok else "REFUSED", [] if ok else ["invalid_mandate"], receipt)

    def admit_grant(self, grant: dict, kind: str = "grant") -> None:
        ok = self.verify_owner(grant) and grant["mandate_id"] == self.mandate_id
        if ok:
            self.grants[grant["grant_id"]] = grant
        receipt = self.receipt(grant["subject_pubkey"], "grant-admitted", grant["grant_id"], "ACCEPTED", []) if ok else None
        self.record(kind, {"artifact": grant}, "ACCEPTED" if ok else "REFUSED", [] if ok else ["invalid_grant"], receipt)

    def revoke(self, revocation: dict) -> None:
        ok = self.verify_owner(revocation) and revocation["grant_id"] in self.grants and revocation["seq"] == self.seq + 1
        if ok:
            self.revoked[revocation["grant_id"]] = self.seq + 1
        receipt = self.receipt(self.owner_pubkey, "revocation-recorded", revocation["grant_id"], "ACCEPTED", []) if ok else None
        self.record("revocation", {"artifact": revocation}, "ACCEPTED" if ok else "REFUSED", [] if ok else ["invalid_revocation"], receipt)

    def attempt(self, control: str | None, worker_pubkey: str, action: str, grant: dict | None, evidence: dict,
                expected: str, special: str | None = None) -> dict:
        reasons: list[str] = []
        next_seq = self.seq + 1
        if "chain_tip" in evidence and not verify_artifact(evidence["chain_tip"], pub(self.receiver_key)):
            reasons = ["receipt_signature_invalid"]
        elif grant is None:
            reasons = ["missing_grant"]
        elif not self.verify_owner(grant):
            reasons = ["authority_not_rooted_at_owner"]
        elif grant["subject_pubkey"] != worker_pubkey:
            reasons = ["worker_mismatch"]
        elif grant["mandate_id"] != self.mandate_id:
            reasons = ["mandate_mismatch"]
        elif grant["grant_id"] not in self.grants:
            reasons = ["grant_not_admitted"]
        elif grant["scope"] not in ("full", action):
            reasons = ["scope_mismatch"]
        elif next_seq < grant["valid_from_seq"] or (grant["valid_until_seq"] is not None and next_seq > grant["valid_until_seq"]):
            reasons = ["grant_outside_validity"]
        elif (grant["grant_id"], action) in self.used:
            reasons = ["replayed_authorization"]
        elif grant["grant_id"] in self.revoked and next_seq > self.revoked[grant["grant_id"]]:
            reasons = ["grant_revoked"]
        elif action == "phase-1" and evidence != {"payer": "owner", "payee": "vendor", "amount": 100, "phase": 1}:
            reasons = ["invalid_evidence"]
        elif action == "phase-2" and (evidence.get("payer"), evidence.get("payee"), evidence.get("amount"), evidence.get("phase")) != ("owner", "vendor", 100, 2):
            reasons = ["invalid_evidence"]
        elif action == "phase-2" and evidence.get("phase_1_receipt_id") != "R-003":
            reasons = ["phase_1_reference_invalid"]
        decision = "REFUSED" if reasons else "ACCEPTED"
        request = {"control": control, "worker_pubkey": worker_pubkey, "action": action,
                   "grant": grant, "evidence": evidence, "special": special}
        receipt = None
        if decision == "ACCEPTED":
            self.used.add((grant["grant_id"], action))
            receipt = self.receipt(worker_pubkey, action, grant["grant_id"], decision, [])
            if action == "phase-2":
                self.ledger.append({"mandate_id": self.mandate_id, "worker_pubkey": worker_pubkey,
                                    "grant_id": grant["grant_id"], "amount": 100, "phase_1_receipt_id": evidence["phase_1_receipt_id"]})
        entry = self.record("attempt", request, decision, reasons, receipt)
        if decision != expected:
            raise AssertionError(f"{control or action}: expected {expected}, got {decision}")
        return entry


def make_grant(grant_id: str, subject: str, mandate_id: str, owner_key: Ed25519PrivateKey) -> dict:
    return signed({"grant_id": grant_id, "issuer_pubkey": pub(owner_key), "subject_pubkey": subject,
                   "mandate_id": mandate_id, "scope": "full", "valid_from_seq": 1,
                   "valid_until_seq": None}, owner_key)


def invoke_worker(role: str, phase: int, receipt_id: str | None, home: Path) -> tuple[dict, dict]:
    home.mkdir(parents=True)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, str(HERE / "run.py"), "--worker", role, "--phase", str(phase)]
    if receipt_id:
        cmd += ["--receipt-id", receipt_id]
    result = subprocess.run(cmd, env=env, text=True, capture_output=True, check=False, timeout=20)
    if result.returncode:
        raise RuntimeError(f"worker subprocess failed: {result.stderr}")
    return json.loads(result.stdout), {"command": cmd, "returncode": result.returncode, "home": str(home),
                                       "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest()}


def main() -> int:
    artifacts, export = HERE / "artifacts", HERE / "export"
    for directory in (artifacts, export):
        if directory.exists(): shutil.rmtree(directory)
        directory.mkdir()
    prereg = HERE / "frozen" / "PREREG-v1.0.md"
    hashes = {"PREREG-v1.0.md": file_hash(prereg), PROFILE.name: file_hash(PROFILE)}
    if hashes != {"PREREG-v1.0.md": EXPECTED_PREREG, PROFILE.name: EXPECTED_PROFILE}:
        raise RuntimeError("FROZEN_HASH_MISMATCH")
    owner, receiver = key("owner"), key("receiver")
    pubs = {role: pub(key(role)) for role in ("owner", "receiver", "worker-a", "worker-b", "worker-c")}
    job = {"description": DESCRIPTION, "phases": ["prepare", "execute"], "acceptance_criteria": CRITERIA}
    mandate_id = digest(job)
    mandate = signed({"mandate_id": mandate_id, "job": job, "owner_pubkey": pubs["owner"], "issued_seq": 1}, owner)
    ga = make_grant("G-A", pubs["worker-a"], mandate_id, owner)
    receiver_state = Receiver(pubs["owner"], receiver, mandate_id)
    receiver_state.admit_mandate(mandate)
    receiver_state.admit_grant(ga)
    with tempfile.TemporaryDirectory(prefix="authority-continuity-workers-") as root:
        root = Path(root)
        a, process_a = invoke_worker("worker-a", 1, None, root / "worker-a-home")
        receiver_state.attempt(None, pubs["worker-a"], "phase-1", ga, a["evidence"], "ACCEPTED")
        ra1 = receiver_state.receipts[-1]
        rev = signed({"revocation_id": "REV-A", "grant_id": "G-A", "issuer_pubkey": pubs["owner"],
                      "seq": receiver_state.seq + 1, "reason": "worker A removed from job"}, owner)
        receiver_state.revoke(rev)
        phase2 = {"payer": "owner", "payee": "vendor", "amount": 100, "phase": 2, "phase_1_receipt_id": ra1["receipt_id"]}
        receiver_state.attempt("C1", pubs["worker-a"], "phase-2", ga, phase2, "REFUSED")
        before_controls = {"receipts": len(receiver_state.receipts), "ledger": len(receiver_state.ledger)}
        receiver_state.attempt("C2", pubs["worker-a"], "phase-1", ga, a["evidence"], "REFUSED")
        receiver_state.attempt("C3-no-grant", pubs["worker-b"], "phase-2", None, phase2, "REFUSED")
        receiver_state.attempt("C3-a-grant", pubs["worker-b"], "phase-2", ga, phase2, "REFUSED")
        c4 = make_grant("G-C4", pubs["worker-b"], mandate_id, owner)
        receiver_state.attempt("C4", pubs["worker-a"], "phase-2", c4, phase2, "REFUSED")
        wrong_id = digest({"description": "different job"})
        c5 = make_grant("G-C5", pubs["worker-a"], wrong_id, owner)
        receiver_state.attempt("C5", pubs["worker-a"], "phase-2", c5, phase2, "REFUSED")
        tampered = dict(ra1); tampered["action"] = "phase-2"
        receiver_state.attempt("C6", pubs["worker-a"], "phase-2", ga, {"chain_tip": tampered}, "REFUSED")
        self_install = signed({"grant_id": "SELF-INSTALL", "issuer_pubkey": pubs["worker-b"],
                               "subject_pubkey": pubs["worker-b"], "mandate_id": mandate_id,
                               "scope": "full", "valid_from_seq": 1, "valid_until_seq": None}, key("worker-b"))
        receiver_state.attempt("C7", pubs["worker-b"], "phase-2", self_install, phase2, "REFUSED")
        b_pre, process_b_pre = invoke_worker("worker-b", 2, ra1["receipt_id"], root / "worker-b-pre-home")
        receiver_state.attempt("S7", pubs["worker-b"], "phase-2", None, b_pre["evidence"], "REFUSED")
        if (len(receiver_state.receipts), len(receiver_state.ledger)) != (before_controls["receipts"], before_controls["ledger"]):
            raise AssertionError("refused control changed receipt log or ledger")
        gb = make_grant("G-B", pubs["worker-b"], mandate_id, owner)
        receiver_state.admit_grant(gb, "successor-grant")
        b, process_b = invoke_worker("worker-b", 2, ra1["receipt_id"], root / "worker-b-home")
        receiver_state.attempt(None, pubs["worker-b"], "phase-2", gb, b["evidence"], "ACCEPTED")
    bundle = {"experiment": "AUTHORITY-CONTINUITY-001", "frozen_hashes": hashes,
              "public_keys": {"owner": pubs["owner"], "receiver": pubs["receiver"]},
              "worker_public_keys": {k: pubs[k] for k in ("worker-a", "worker-b", "worker-c")},
              "mandate": mandate, "grants": [ga, gb], "revocation": rev,
              "receipts": receiver_state.receipts, "run_log": receiver_state.run_log,
              "ledger": receiver_state.ledger,
              "processes": [process_a, process_b_pre, process_b],
              "ac09_reused_evidence": {"result": "PASS", "source": "frozen prereg assertion",
                                        "produced_by_this_run": False,
                                        "note": "Trust-root succession did not occur in this run."}}
    write_json(export / "bundle.json", bundle)
    for name, value in (("mandate.json", mandate), ("grant-a.json", ga), ("grant-b.json", gb),
                        ("revocation-a.json", rev), ("receipts.json", receiver_state.receipts),
                        ("run-log.json", receiver_state.run_log), ("ledger.json", receiver_state.ledger),
                        ("public-keys.json", bundle["public_keys"]), ("processes.json", bundle["processes"])):
        write_json(export / name, value)
    manifest = {p.name: file_hash(p) for p in sorted(export.iterdir()) if p.is_file()}
    write_json(export / "manifest.json", manifest)
    clean_env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
    with tempfile.TemporaryDirectory(prefix="authority-continuity-verifier-") as temp:
        temp = Path(temp); home = temp / "home"; home.mkdir()
        isolated_bundle = temp / "bundle"; shutil.copytree(export, isolated_bundle)
        isolated_profile = temp / PROFILE.name; shutil.copy2(PROFILE, isolated_profile)
        isolated_verify = temp / "verify.py"; shutil.copy2(HERE / "verify.py", isolated_verify)
        for path in [isolated_bundle, *isolated_bundle.rglob("*"), isolated_profile, isolated_verify]:
            path.chmod(0o555 if path.is_dir() else 0o444)
        clean_env["HOME"] = str(home)
        result = subprocess.run([sys.executable, "-I", str(isolated_verify), "--bundle", str(isolated_bundle),
                                 "--profile", str(isolated_profile)], cwd=temp, env=clean_env,
                                text=True, capture_output=True, check=False, timeout=30)
    report = {"command": "python -I verify.py --bundle <read-only-export> --profile <frozen-profile>",
              "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
              "clean_home": True, "network_used": False, "inputs": {"bundle_manifest": file_hash(export / "manifest.json"),
                                                                        "profile": file_hash(PROFILE)}}
    write_json(artifacts / "frozen-input-verification.json", {"status": "PASS", "hashes": hashes})
    write_json(artifacts / "verifier-report.json", report)
    write_json(artifacts / "environment.json", {"python": sys.version.split()[0], "platform": platform.platform(),
                                                  "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=HERE, text=True).strip()})
    result_class = "PASS" if result.returncode == 0 else "INCONCLUSIVE_APPARATUS"
    write_json(artifacts / "result.json", {"result": result_class, "negative_controls": {f"C{i}": "REFUSED" for i in range(1, 8)},
                                            "export_only_verifier": "PASS" if result.returncode == 0 else "FAIL"})
    print(result_class)
    return 0 if result_class == "PASS" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("worker-a", "worker-b"))
    parser.add_argument("--phase", type=int, choices=(1, 2))
    parser.add_argument("--receipt-id")
    args = parser.parse_args()
    raise SystemExit(worker(args.worker, args.phase, args.receipt_id) if args.worker else main())
