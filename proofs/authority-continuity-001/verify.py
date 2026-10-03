#!/usr/bin/env python3
"""Export-only verifier for AUTHORITY-CONTINUITY-001."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PROFILE_HASH = "2f96264c7dbc92cf13587ab932eb4d14aa876f6cfce6950d2f96909f3efe0c26"

def canonical(v): return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def digest(v): return hashlib.sha256(canonical(v)).hexdigest()
def file_hash(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def valid(artifact, public_key):
    body = {k: v for k, v in artifact.items() if k not in ("signature", "payload_hash")}
    try:
        if "payload_hash" in artifact and artifact["payload_hash"] != digest(body): return False
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(bytes.fromhex(artifact["signature"]), bytes.fromhex(digest(body)))
        return True
    except (KeyError, ValueError, InvalidSignature): return False

def fail(message): print(json.dumps({"result": "FAIL", "first_disagreement": message}, sort_keys=True)); return 1

def main(bundle_dir: Path, profile: Path):
    if file_hash(profile) != PROFILE_HASH: return fail("profile hash")
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    for name, expected in manifest.items():
        if file_hash(bundle_dir / name) != expected: return fail("manifest:" + name)
    b = json.loads((bundle_dir / "bundle.json").read_text()); owner=b["public_keys"]["owner"]; receiver=b["public_keys"]["receiver"]
    if b["frozen_hashes"][profile.name] != PROFILE_HASH: return fail("bundle profile binding")
    if not valid(b["mandate"], owner): return fail("mandate signature")
    mandate_id=b["mandate"]["mandate_id"]
    grants={g["grant_id"]:g for g in b["grants"]}
    if any(not valid(g, owner) or g["mandate_id"] != mandate_id for g in grants.values()): return fail("grant signature/binding")
    rev=b["revocation"]
    if not valid(rev, owner): return fail("revocation signature")
    previous=None
    receipts={}
    for r in b["receipts"]:
        if not valid(r, receiver): return fail("receipt signature:"+r["receipt_id"])
        if r["prev_receipt_hash"] != previous: return fail("receipt chain:"+r["receipt_id"])
        if r["mandate_id"] != mandate_id: return fail("receipt mandate:"+r["receipt_id"])
        previous=digest(r); receipts[r["receipt_id"]]=r
    admitted=set(); revoked={}; expected_receipt_index=0; ledger=[]
    control_seen=set(); used=set()
    for entry in b["run_log"]:
        req=entry["request"]; expected="ACCEPTED"; reasons=[]
        if entry["kind"] == "mandate": pass
        elif entry["kind"] in ("grant", "successor-grant"):
            g=req["artifact"]
            if valid(g, owner) and g["mandate_id"] == mandate_id: admitted.add(g["grant_id"])
            else: expected="REFUSED"; reasons=["invalid_grant"]
        elif entry["kind"] == "revocation":
            artifact=req["artifact"]
            if valid(artifact, owner) and artifact["grant_id"] in admitted and artifact["seq"] == entry["seq"]: revoked[artifact["grant_id"]]=entry["seq"]
            else: expected="REFUSED"; reasons=["invalid_revocation"]
        else:
            g=req["grant"]; worker=req["worker_pubkey"]; special=req["special"]; action=req["action"]; evidence=req["evidence"]
            if req["control"] and req["control"].startswith("C"): control_seen.add(req["control"].split("-")[0])
            if "chain_tip" in evidence and not valid(evidence["chain_tip"], receiver): reasons=["receipt_signature_invalid"]
            elif g is None: reasons=["missing_grant"]
            elif not valid(g, owner): reasons=["authority_not_rooted_at_owner"]
            elif g["subject_pubkey"] != worker: reasons=["worker_mismatch"]
            elif g["mandate_id"] != mandate_id: reasons=["mandate_mismatch"]
            elif g["grant_id"] not in admitted: reasons=["grant_not_admitted"]
            elif g["scope"] not in ("full", action): reasons=["scope_mismatch"]
            elif entry["seq"] < g["valid_from_seq"] or (g["valid_until_seq"] is not None and entry["seq"] > g["valid_until_seq"]): reasons=["grant_outside_validity"]
            elif (g["grant_id"], action) in used: reasons=["replayed_authorization"]
            elif g["grant_id"] in revoked and entry["seq"] > revoked[g["grant_id"]]: reasons=["grant_revoked"]
            elif action == "phase-1" and evidence != {"payer":"owner","payee":"vendor","amount":100,"phase":1}: reasons=["invalid_evidence"]
            elif action == "phase-2" and (evidence.get("payer"),evidence.get("payee"),evidence.get("amount"),evidence.get("phase")) != ("owner","vendor",100,2): reasons=["invalid_evidence"]
            elif action == "phase-2" and evidence.get("phase_1_receipt_id") != "R-003": reasons=["phase_1_reference_invalid"]
            if reasons: expected="REFUSED"
            else: used.add((g["grant_id"], action))
            if not reasons and action == "phase-2": ledger.append({"mandate_id":mandate_id,"worker_pubkey":worker,"grant_id":g["grant_id"],"amount":100,"phase_1_receipt_id":evidence["phase_1_receipt_id"]})
        if entry["decision"] != expected or entry["reason_codes"] != reasons: return fail("decision seq "+str(entry["seq"]))
        if expected == "ACCEPTED":
            expected_receipt_index += 1
            if entry["receipt_id"] != f"R-{expected_receipt_index:03d}": return fail("accepted receipt seq "+str(entry["seq"]))
        elif entry["receipt_id"] is not None: return fail("refusal emitted acceptance receipt")
        if entry["receipt_log_length"] != expected_receipt_index: return fail("append-only length seq "+str(entry["seq"]))
    if control_seen != {f"C{i}" for i in range(1,8)}: return fail("negative control coverage")
    if ledger != b["ledger"] or len(ledger) != 1 or ledger[0]["worker_pubkey"] != b["worker_public_keys"]["worker-b"]: return fail("ledger")
    if b["ac09_reused_evidence"]["produced_by_this_run"] is not False: return fail("AC-09 classification")
    print(json.dumps({"result":"PASS","decisions_verified":len(b["run_log"]),"receipts_verified":len(b["receipts"]),"controls_verified":sorted(control_seen)}, sort_keys=True)); return 0

if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("--bundle",type=Path,required=True); p.add_argument("--profile",type=Path,required=True); a=p.parse_args(); raise SystemExit(main(a.bundle,a.profile))
