"""Verify the self-attested experiment evidence and all linked records."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
from openline_wallet.canonical import strict_json_load
from openline_wallet.crypto import record_hash, verify_record
from openline_wallet.errors import WalletError

RECORDS = ("baseline-admission.json", "baseline-effects.json", "closure.json",
           "admission.json", "frontier.json", "effect-receipt.json",
           "patched-effects.json", "result.json")

def require(condition, reason):
    if not condition:
        raise WalletError("EFFECT_PROOF_INVALID", reason)

def verify(directory):
    directory = Path(directory)
    manifest = (directory / "SHA256SUMS.txt").read_text().splitlines()
    hashes = {}
    for line in manifest:
        digest, name = line.split("  ", 1)
        require(name not in hashes and "/" not in name and "\\" not in name, "MANIFEST_PATH")
        hashes[name] = digest
    require(set(hashes) == set(RECORDS), "MANIFEST_SET")
    for name, digest in hashes.items():
        require(hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest, name)
    records = {name: strict_json_load(directory / name) for name in RECORDS}
    result = records["result.json"]
    require(verify_record(result)[0], "RESULT_SIGNATURE")
    require(result["experiment_id"] == "WALLET-EFFECT-CLOSURE-001", "EXPERIMENT")
    require(result["verdict"] == "LOCAL_EFFECT_CLOSURE_ENFORCED", "VERDICT")
    require(result["base_commit"] == "9df63798c0f520e9d6c8dc85dd293ebdeaa9c11f", "BASE")
    require(all(result["checks"].values()), "CHECKS")
    baseline = records["baseline-admission.json"]
    admission = records["admission.json"]
    frontier = records["frontier.json"]
    effect = records["effect-receipt.json"]
    closure = records["closure.json"]
    key = closure["gate_public_key"]
    for name, record in (("baseline", baseline), ("admission", admission),
                         ("frontier", frontier), ("effect", effect), ("closure", closure)):
        expected = None if name == "baseline" else key
        require(verify_record(record, expected_public_key=expected)[0], name + "_SIGNATURE")
    require(baseline["decision"] == "ALLOWED", "BASELINE")
    require(len(records["baseline-effects.json"]) == 1, "BASELINE_EFFECT_COUNT")
    require(records["baseline-effects.json"][0]["receipt_hash"] == record_hash(baseline), "BASELINE_LINK")
    require(admission["decision"] == "ALLOWED", "ADMISSION")
    require(closure["status"] == "EFFECT_CLOSED" and closure["pending_fenced"] == 1, "CLOSURE")
    require(closure["active_frontiers"] == 0, "ACTIVE_FRONTIERS")
    require(frontier["decision"] == "STOPPED" and frontier["reason_codes"] == ["MANDATE_REVOKED"], "FRONTIER")
    require(effect["effect_applied"] is False, "EFFECT_STATUS")
    require(effect["admission_receipt_hash"] == record_hash(admission), "ADMISSION_LINK")
    require(effect["frontier_receipt_hash"] == record_hash(frontier), "FRONTIER_LINK")
    require(effect["gate_id"] == closure["gate_id"] == frontier["gate_id"], "GATE_LINK")
    require(effect["mandate_id"] == closure["mandate_id"] == admission["mandate_id"], "MANDATE_LINK")
    require(records["patched-effects.json"] == [], "LATE_EFFECT")
    require(result["patched"]["effects_after_signed_closure"] == 0, "RESULT_COUNT")
    return {"verdict": result["verdict"], "result_hash": record_hash(result),
            "verification": "SELF_ATTESTED_EVIDENCE_CONSISTENT"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    import json
    print(json.dumps(verify(args.directory), indent=2, sort_keys=True))

if __name__ == "__main__":
    main()
