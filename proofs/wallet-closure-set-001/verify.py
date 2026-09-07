"""Recompute the frozen historical evidence; never trust a verdict label alone."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from openline_wallet.canonical import strict_json_load
from openline_wallet.clock import parse_time
from openline_wallet.closure_set import evaluate_closure_set, verify_set_report
from openline_wallet.crypto import public_key_hex, record_hash, verify_record
from openline_wallet.errors import WalletError
from openline_wallet.wallet import verify_bundle

EXPERIMENT = "WALLET-CLOSURE-SET-001"
VERDICT = "FIXED_SET_LOCAL_CLOSURE_ENFORCED"
BASE = "a8633858a04d33c0e2930214d7c93105f67a3398"
FILES = (
    "membership.json", "request.json", "grant-bundle.json", "revoked-bundle.json",
    "witnesses.json", "partial.json", "report.json", "ledgers.json", "prior-result.json",
    "inflight-result.json", "post-closure.json", "wallet-final.json", "fresh-request.json",
    "forged-witness.json", "negative-controls.json", "trace.json", "result.json",
)


def require(condition, reason):
    if not condition:
        raise WalletError("CLOSURE_SET_PROOF_INVALID", reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(directory, source_root=None):
    directory = Path(directory)
    hashes = {}
    for line in (directory / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ", 1)
        require(name in FILES and name not in hashes, "MANIFEST_PATH")
        hashes[name] = digest
    require(set(hashes) == set(FILES), "MANIFEST_SET")
    for name, digest in hashes.items():
        require(sha(directory / name) == digest, "HASH_" + name)
    records = {name: strict_json_load(directory / name) for name in FILES}
    result = records["result.json"]
    require(result["experiment_id"] == EXPERIMENT and result["base_commit"] == BASE, "IDENTITY")
    require(result["verdict"] == VERDICT and all(result["checks"].values()), "VERDICT")
    require(verify_record(result, expected_public_key=result["auditor_public_key"])[0], "RESULT_SIGNATURE")
    require(result["evidence_sha256"] == {name: hashes[name] for name in FILES if name != "result.json"}, "RESULT_BINDINGS")
    if source_root is not None:
        root = Path(source_root)
        for name, digest in result["source_sha256"].items():
            require(sha(root / name) == digest, "SOURCE_" + name)

    manifest, request = records["membership.json"], records["request.json"]
    grant, revoked = records["grant-bundle.json"], records["revoked-bundle.json"]
    witnesses, report = records["witnesses.json"], records["report.json"]
    audit_time = parse_time(report["evaluated_at"])
    complete = evaluate_closure_set(manifest, request, revoked, witnesses, now=audit_time)
    require(complete["status"] == "EFFECT_CLOSED" and complete["required_receivers"] == 3
            and complete["verified_receivers"] == 3, "CLOSURE")
    require(verify_set_report(report, result["auditor_public_key"], manifest, request, revoked,
                              witnesses, now=audit_time) == complete, "AUDITOR")
    require(len({w["gate_public_key"] for w in witnesses}) == 3, "DISTINCT_KEYS")
    _, initial = verify_bundle(grant, now=parse_time(grant["issued_at"]), require_fresh=False)
    _, terminal = verify_bundle(revoked, now=parse_time(revoked["issued_at"]), require_fresh=False)
    require(initial.mandates["grant-a"]["status"] == "ACTIVE" and
            terminal.mandates["grant-a"]["status"] == "REVOKED", "AUTHORITY_TRANSITION")
    require(initial.head_sequence < terminal.head_sequence, "REVOCATION_ORDER")

    partial = evaluate_closure_set(manifest, request, revoked, witnesses[:2],
                                   now=parse_time(records["partial.json"]["evaluated_at"]))
    require(partial == records["partial.json"] and partial["status"] == "CLOSURE_INCOMPLETE"
            and partial["missing"] == ["receiver-2"], "PARTIAL")
    negative = records["negative-controls.json"]
    variants = {
        "missing": (request, witnesses[:2]),
        "forged": (request, witnesses[:2] + [records["forged-witness.json"]]),
        "duplicate": (request, witnesses + [witnesses[0]]),
        "replay": (records["fresh-request.json"], witnesses),
        "unreachable": (request, witnesses[:2]),
    }
    for name, (target, supplied) in variants.items():
        expected = evaluate_closure_set(manifest, target, revoked, supplied,
                                        now=parse_time(negative[name]["evaluated_at"]))
        require(expected == negative[name] and expected["status"] == "CLOSURE_INCOMPLETE", name)

    ledgers = records["ledgers.json"]
    require([len(ledgers[f"receiver-{i}"]) for i in range(3)] == [0, 1, 1], "LEDGER_COUNTS")
    require({e["release"] for values in ledgers.values() for e in values} ==
            {"prior-effect", "inflight"}, "LEDGER_CONTENTS")
    prior, inflight = records["prior-result.json"], records["inflight-result.json"]
    for action in (prior, inflight):
        require(action["decision"] == "ALLOWED" and action["effect_applied"], "PRIOR_EFFECT")
        require(record_hash(action["receipt"]) == action["receipt_hash"] and
                verify_record(action["receipt"])[0], "EFFECT_SIGNATURE")
        require(verify_record(action["admission_receipt"])[0] and
                record_hash(action["admission_receipt"]) == action["admission_receipt_hash"], "ADMISSION_LINK")
        require(verify_record(action["effect_receipt"])[0] and
                action["effect_receipt"]["frontier_receipt_hash"] == action["receipt_hash"], "EFFECT_LINK")
        require(any(e["receipt_hash"] == action["receipt_hash"] for values in ledgers.values() for e in values), "EFFECT_MISSING")
    require(parse_time(witnesses[2]["local_closure"]["closed_at"]) >=
            parse_time(inflight["effect_receipt"]["completed_at"]), "DRAIN_ORDER")
    stops = records["post-closure.json"]
    require(len(stops) == 2, "STOP_COUNT")
    ledger_hashes = {e["receipt_hash"] for values in ledgers.values() for e in values}
    for action in stops:
        require(action["decision"] == "STOPPED" and action["reason_codes"] == ["MANDATE_REVOKED"]
                and action["effect_applied"] is False, "LATE_EFFECT")
        require(record_hash(action["receipt"]) == action["receipt_hash"] and
                verify_record(action["receipt"])[0] and action["receipt_hash"] not in ledger_hashes,
                "STOP_LINK")
        require(verify_record(action["effect_receipt"])[0] and
                action["effect_receipt"]["effect_applied"] is False, "STOP_EVIDENCE")
    final, history = verify_bundle(records["wallet-final.json"],
                                    now=parse_time(records["wallet-final.json"]["issued_at"]), require_fresh=False)
    require(history.mandates["grant-a"]["status"] == "REVOKED", "FINAL_STANDING")
    require(result["observed"]["wallet_receipts"] == len(final["receipts"]) == 7, "RECEIPT_CONTINUITY")
    require({record_hash(r) for r in final["receipts"]} >=
            {prior["receipt_hash"], inflight["receipt_hash"], *[r["receipt_hash"] for r in stops]},
            "RECEIPT_PRESERVATION")

    trace = records["trace.json"]
    require([x["sequence"] for x in trace] == list(range(1, len(trace) + 1)), "TRACE_SEQUENCE")
    order = [x["event"] for x in trace]
    required = ["grant_admitted", "inflight_effect_held_at_ledger", "revocation_signed",
                "two_receivers_admitted_revocation", "two_of_three_incomplete", "closure_waits_for_actual_frontier",
                "inflight_effect_completed_before_closure", "third_receiver_closed", "set_closure_verified",
                "all_post_closure_actions_stopped"]
    require(all(name in order for name in required) and
            [order.index(name) for name in required] == sorted(order.index(name) for name in required), "TRACE_ORDER")
    require(all(parse_time(trace[i]["observed_at"]) <= parse_time(trace[i + 1]["observed_at"])
                for i in range(len(trace) - 1)), "TRACE_CLOCK")
    return {"verdict": VERDICT, "result_hash": record_hash(result),
            "verification": "SELF_ATTESTED_HISTORICAL_EVIDENCE_VERIFIED",
            "claim_scope": "FIXED_RECEIVER_SET_OF_LOCAL_STAGING_LEDGERS"}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--source-root", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args(argv)
    import json
    print(json.dumps(verify(args.directory, args.source_root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
