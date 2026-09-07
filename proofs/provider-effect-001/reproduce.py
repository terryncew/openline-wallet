"""PROVIDER-EFFECT-001: unsafe admission-only control and repaired HTTP boundary.

All HTTP traffic is routed to a loopback fixture. This is never a live GitHub
result. For a real disposable PR use openline_wallet.github_effect_live.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from github_http_fixture import Fixture
from test_github_effect import TARGET

from openline_wallet.canonical import pretty_json
from openline_wallet.clock import isoformat, utc_now
from openline_wallet.crypto import public_key_hex, record_hash, sign_record, verify_record
from openline_wallet.effect_closure import EffectGate
from openline_wallet.github_effect import GitHubClient
from openline_wallet.github_effect_live import BASE_COMMIT, run_experiment
from openline_wallet.receiver import create_presentation
from openline_wallet.wallet import Wallet

SOURCES = [
    "src/openline_wallet/github_effect.py",
    "src/openline_wallet/github_effect_live.py",
    "tests/github_http_fixture.py", "tests/test_github_effect.py", "tests/test_github_http.py",
    "proofs/provider-effect-001/reproduce.py", "proofs/provider-effect-001/verify.py",
]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save(path, value):
    path.write_text(pretty_json(value), encoding="ascii")


def unsafe_control(root: Path) -> dict:
    """An explicitly unsafe control, not a claim that legacy Wallet did this."""
    fixture = Fixture()
    try:
        client = GitHubClient("disposable-fixture-token")
        client._opener = fixture.opener()
        wallet = Wallet.create(root / "wallet")
        holder = Ed25519PrivateKey.generate()
        now = utc_now()
        wallet.grant(subject_id="agent-a", subject_public_key=public_key_hex(holder),
            scopes=[TARGET.action], expires_at=now + timedelta(hours=1), now=now, mandate_id="grant-a")
        bundle = wallet.export_bundle()
        gate = EffectGate("unsafe-admission-only")
        gate.pin_principal(wallet.principal_id, wallet.root_public_key)
        gate.admit_bundle(bundle)
        issued = utc_now()
        challenge = gate.issue_challenge(principal_id=wallet.principal_id, subject_id="agent-a",
                                          action=TARGET.action, now=issued)
        presentation = create_presentation(bundle=bundle, mandate_id="grant-a", subject_id="agent-a",
            subject_key=holder, action=TARGET.action, receiver_challenge=challenge, now=issued)
        admission = gate.evaluate(presentation, expected_action=TARGET.action)
        require(admission["decision"] == "ALLOWED", "unsafe control did not admit")
        entered, release = Event(), Event()
        result, errors = {}, []
        def worker():
            entered.set()
            if not release.wait(5):
                errors.append("HOLD_TIMEOUT")
                return
            try:
                result.update(client.merge(TARGET))
            except Exception as exc:
                errors.append(type(exc).__name__)
        thread = Thread(target=worker)
        thread.start()
        try:
            require(entered.wait(2), "unsafe control did not hold")
            wallet.revoke("grant-a")
            revoked = wallet.export_bundle()
            gate.admit_bundle(revoked)
            # This is deliberately not an OpenLine closure receipt. It models
            # the wrong claim that a newer authorization head drains old work.
            claimed = {"claim": "ADMISSION_ONLY_EFFECTIVE", "at": isoformat(utc_now()),
                       "head_hash": revoked["head"]["event_hash"]}
        finally:
            release.set()
            thread.join(5)
        require(not thread.is_alive() and not errors and result.get("merged") is True,
                "unsafe control did not reproduce the late merge")
        after = client.pr(TARGET)
        require(after["merged"] is True and fixture.provider.mutation_count == 1,
                "unsafe control remote state mismatch")
        return {"admission": admission, "revoked_bundle": revoked, "claimed": claimed,
                "response": result, "after": after, "merge_requests": 1,
                "late_effect_reproduced": True}
    finally:
        fixture.close()


def run(output: Path) -> dict:
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("EVIDENCE_DIRECTORY_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    baseline = None
    repaired = None
    try:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = unsafe_control(root / "unsafe")
            fixture = Fixture()
            try:
                client = GitHubClient("disposable-fixture-token")
                client._opener = fixture.opener()
                repaired = run_experiment(client, TARGET, root / "private", output / "repaired")
                require(repaired["verdict"] == "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED",
                        "repaired experiment failed")
                require(fixture.provider.mutation_count == 1, "unexpected remote mutation count")
            finally:
                fixture.close()
    except Exception as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))
    if baseline is not None:
        save(output / "unsafe-control.json", baseline)
    source_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    complete = not errors and baseline is not None and repaired is not None
    verdict = "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED" if complete else "INCONCLUSIVE"
    result_body = {"schema": "openline.wallet.provider_effect_experiment.v1",
        "experiment_id": "PROVIDER-EFFECT-001", "base_commit": BASE_COMMIT,
        "verdict": verdict, "transport": "LOOPBACK_HTTP_FIXTURE", "live_github_run": False,
        "source_sha256": source_hashes, "errors": errors,
        "checks": {"unsafe_late_merge_reproduced": bool(baseline and baseline["late_effect_reproduced"]),
            "prefrontier_rejected_without_put": bool(complete and repaired["merge_requests_after_a"] == 0),
            "real_http_json_path_exercised": bool(complete),
            "exact_head_merge_reconciled": bool(complete and repaired["after_b"]["head_sha"] == TARGET.head_sha),
            "held_acknowledgement_drained": bool(complete and repaired["closure_blocked_during_ack"]),
            "one_mutation_no_retry": bool(complete and repaired["merge_requests"] == 1),
            "receiver_closure_signed": bool(complete and repaired["closure_b"]["status"] == "EFFECT_CLOSED")},
        "claim_boundary": "Controlled loopback HTTP and exact disposable PR state. Real GitHub merge pending. No external queue, other-writer, exact-base CAS, or production recovery claim.",
        "verification": "Disposable self-attestation; not an independent provider witness.",
        "completed_at": isoformat(utc_now())}
    evidence_hashes = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(output.rglob("*.json")) if p != output / "result.json"}
    result_body["evidence_sha256"] = evidence_hashes
    receipt = sign_record(result_body, Ed25519PrivateKey.generate())
    require(verify_record(receipt)[0], "result signature invalid")
    save(output / "result.json", receipt)
    sums = [hashlib.sha256(p.read_bytes()).hexdigest() + "  " + str(p.relative_to(output))
            for p in sorted(output.rglob("*.json"))]
    (output / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="ascii")
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.output)
    print(pretty_json({"verdict": result["verdict"], "result_hash": record_hash(result)}))
    return 0 if result["verdict"] == "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
