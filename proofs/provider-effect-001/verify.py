"""Independent reappraisal of the controlled transport evidence packet."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
from pathlib import Path
import sys

from openline_wallet.canonical import strict_json_load
from openline_wallet.clock import parse_time
from openline_wallet.crypto import record_hash, verify_record
from openline_wallet.wallet import verify_bundle

ROOT = Path(__file__).resolve().parents[2]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verified(record, key=None):
    require(verify_record(record, expected_public_key=key)[0], "signed evidence invalid")
    return record


def run(output: Path) -> dict:
    output = Path(output)
    result = verified(strict_json_load(output / "result.json"))
    require(result["experiment_id"] == "PROVIDER-EFFECT-001" and
            result["verdict"] == "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED" and
            result["transport"] == "LOOPBACK_HTTP_FIXTURE" and result["live_github_run"] is False,
            "claim boundary invalid")
    for name, digest in result["source_sha256"].items():
        require(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, "source changed: " + name)
    expected = {}
    for name, digest in result["evidence_sha256"].items():
        require(".." not in Path(name).parts and not Path(name).is_absolute(), "unsafe evidence path")
        require(hashlib.sha256((output / name).read_bytes()).hexdigest() == digest, "evidence hash mismatch")
        expected[name] = digest
    actual = {str(p.relative_to(output)) for p in output.rglob("*.json") if p != output / "result.json"}
    require(actual == set(expected), "evidence file set changed")
    sums = {line.split("  ", 1)[1]: line.split("  ", 1)[0]
            for line in (output / "SHA256SUMS.txt").read_text().splitlines()}
    require(set(sums) == actual | {"result.json"}, "manifest file set mismatch")
    require(sums["result.json"] == hashlib.sha256((output / "result.json").read_bytes()).hexdigest(), "result hash mismatch")
    require(all(sums[name] == digest for name, digest in expected.items()), "manifest mismatch")
    unsafe = strict_json_load(output / "unsafe-control.json")
    admission = verified(unsafe["admission"])
    bundle = unsafe["revoked_bundle"]
    verified_bundle, timeline = verify_bundle(bundle, now=parse_time(bundle["issued_at"]), require_fresh=False)
    require(timeline.mandates[admission["mandate_id"]]["status"] == "REVOKED", "unsafe control not revoked")
    require(admission["decision"] == "ALLOWED" and unsafe["claimed"]["claim"] == "ADMISSION_ONLY_EFFECTIVE" and
            unsafe["response"]["merged"] is True and unsafe["after"]["merged"] is True and
            unsafe["after"]["merge_commit_sha"] == unsafe["response"]["sha"] and
            unsafe["merge_requests"] == 1, "late-effect negative control invalid")
    require(admission["principal_id"] == verified_bundle["principal"]["principal_id"], "principal mismatch")
    repaired = output / "repaired"
    repair_result = verified(strict_json_load(repaired / "result.json"))
    require(repair_result["verdict"] == "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED", "repaired verdict invalid")
    for name, digest in repair_result["evidence_sha256"].items():
        require(hashlib.sha256((repaired / name).read_bytes()).hexdigest() == digest, "nested evidence mismatch")
    def read(name):
        return strict_json_load(repaired / (name + ".json"))
    target = read("target")
    a, b = read("admission_a"), read("admission_b")
    closure_a, closure_b = read("closure_a"), read("closure_b")
    stopped, effect = read("stopped_a"), read("effect_b")
    for record in [a, b, closure_a, closure_b, stopped["receipt"], effect["receipt"], effect["effect_receipt"]]:
        verified(record)
    require(a["decision"] == b["decision"] == "ALLOWED" and
            stopped["decision"] == "STOPPED" and stopped["reason_codes"] == ["MANDATE_REVOKED"] and
            stopped["effect_applied"] is False and read("merge_requests_after_a") == 0 and
            read("after_a")["merged"] is False, "prefrontier control invalid")
    require(closure_a["pending_fenced"] == 1 and closure_a["active_frontiers"] == 0 and
            closure_a["status"] == "EFFECT_CLOSED", "first closure invalid")
    receipt = effect["effect_receipt"]
    require(effect["effect_applied"] is True and effect["decision"] == "ALLOWED" and
            receipt["action"] == target["action"] and receipt["target"] == target and
            receipt["admission_receipt_hash"] == record_hash(b) and
            receipt["frontier_receipt_hash"] == record_hash(effect["receipt"]), "effect binding invalid")
    require(receipt["merge_commit"]["parents"][1] == target["head_sha"] and
            receipt["merge_commit"]["sha"] == receipt["merge_commit_sha"] == read("after_b")["merge_commit_sha"] and
            read("after_b")["merged"] is True, "remote merge state invalid")
    require(closure_b["confirmed_effect_hashes"] == [record_hash(receipt)] and
            closure_b["principal_id"] == b["principal_id"] and closure_b["mandate_id"] == b["mandate_id"] and
            closure_b["status"] == "EFFECT_CLOSED" and closure_b["active_frontiers"] == 0 and
            parse_time(closure_b["closed_at"]) >= parse_time(receipt["confirmed_at"]), "second closure invalid")
    timing = read("timing")
    require(read("closure_blocked_during_ack") is True and
            timing["ack_observed_ns"] <= timing["revocation_requested_ns"] <= timing["closure_requested_ns"] <=
            timing["ack_released_ns"] <= timing["closure_returned_ns"], "frontier ordering invalid")
    require(read("merge_requests") == 1 and read("errors") == [] and
            read("wallet_receipt_count") == 2, "effect count or history invalid")
    return {"verdict": result["verdict"], "result_hash": record_hash(result),
            "checks": len(result["checks"]), "source_files": len(result["source_sha256"]),
            "evidence_files": len(expected)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(run(args.output))


if __name__ == "__main__":
    main()
