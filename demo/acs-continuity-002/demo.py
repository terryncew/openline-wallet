#!/usr/bin/env python3
"""ACS-CONTINUITY-002 flagship demo — a presentation of the frozen result.

This is NOT another experiment. It renders the already-frozen,
already-sealed ACS-CONTINUITY-002 result
(PASS_ACS_CONTINUITY_PLANNED_HOST_HANDOFF_CONSERVES_AUTHORITY) as a
human-readable transcript, or with --json as a deterministic JSON receipt
sequence. It reads proofs/acs-continuity-002/result-summary.json and refuses
to present anything unless that frozen result is the PASS verdict.

No model calls. No network. No new authority logic. Deterministic output.

Usage:
    python demo.py            # human transcript
    python demo.py --json     # deterministic JSON receipt sequence
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUMMARY = os.path.normpath(os.path.join(
    HERE, "..", "..", "proofs", "acs-continuity-002", "result-summary.json"))

EXPECTED_TERMINAL = "PASS_ACS_CONTINUITY_PLANNED_HOST_HANDOFF_CONSERVES_AUTHORITY"


def load_summary():
    with open(SUMMARY) as f:
        s = json.load(f)
    if s["terminal_class"] != EXPECTED_TERMINAL:
        sys.exit("refusing to present: frozen result is not the PASS verdict "
                 "(%s)" % s["terminal_class"])
    cases = {c["id"]: c for c in s["cases"]}
    missing = [k for k in ["B0", "T1", "T3", "T4", "T6", "T10"]
               if cases.get(k, {}).get("verdict") != "pass"]
    if missing:
        sys.exit("refusing to present: frozen cases not all PASS: %s" % missing)
    return s


def transcript(s):
    L = []
    A = L.append
    A("Switching AI hosts should not give the replacement a fresh credit limit.")
    A("")
    A("Host A starts with 100 units of owner authority.")
    A("A commits 60  ->  60 committed / 0 encumbered / 40 available.")
    A("")
    A("The owner switches hosts. A closes and emits a successor-bound,")
    A("signed continuity receipt (one-way, planned — not failover):")
    A("")
    A('  { "original": 100, "committed": 60, "encumbered": 0,')
    A('    "available": 40, "successor": "B" }   # Ed25519-signed')
    A("")
    A("Fresh host B imports the receipt. B does NOT start at 100.")
    A("B starts from 40 available — the 60 already committed travels too.")
    A("")
    A("  B tries to commit 60            ->  REFUSED (only 40 available)")
    A("  B tries to commit 40            ->  ADMITTED (60 + 40 stays 100)")
    A("  replay of A's receipt           ->  REFUSED (stale sequence)")
    A("  receipt presented to host C     ->  REFUSED (wrong successor)")
    A("  A tries to spend after closure  ->  REFUSED (source is closed)")
    A("  10 units encumbered on A        ->  still encumbered on B")
    A("                                     (not spendable by either host)")
    A("")
    A("Invariant, both hosts: committed + encumbered + available = 100.")
    A("")
    A("Scope: one planned, one-way host replacement. Not arbitrary failover,")
    A("not active-active, not a consensus claim.")
    A("")
    A("Evidence: proofs/acs-continuity-002/ — frozen PASS, one $0 run,")
    A("RESULT.json f8f6ef34639a13b98482c686653280f6428925c030164198383433a6acc0fdcf")
    return "\n".join(L) + "\n"


def receipt_sequence(s):
    seq = [
        {"step": 1, "event": "host_a_start",
         "state": {"committed": 0, "encumbered": 0, "available": 100}},
        {"step": 2, "event": "host_a_commit_60",
         "state": {"committed": 60, "encumbered": 0, "available": 40}},
        {"step": 3, "event": "host_a_close_emit_receipt",
         "receipt": {"original": 100, "committed": 60, "encumbered": 0,
                     "available": 40, "successor": "B",
                     "signature": "ed25519/canonical-json"}},
        {"step": 4, "event": "host_b_import",
         "state": {"committed": 60, "encumbered": 0, "available": 40},
         "note": "B starts from 40 available, not 100"},
        {"step": 5, "event": "host_b_commit_60_attempt",
         "decision": "REFUSED", "reason": "only 40 available"},
        {"step": 6, "event": "host_b_commit_40",
         "decision": "ADMITTED",
         "state": {"committed": 100, "encumbered": 0, "available": 0}},
        {"step": 7, "event": "replay_receipt_attempt",
         "decision": "REFUSED", "reason": "stale sequence"},
        {"step": 8, "event": "receipt_to_wrong_successor_c",
         "decision": "REFUSED", "reason": "successor binding mismatch"},
        {"step": 9, "event": "host_a_spend_after_closure",
         "decision": "REFUSED", "reason": "source closed"},
        {"step": 10, "event": "encumbered_across_cutover",
         "state": {"committed": 50, "encumbered": 10, "available": 40},
         "note": "encumbered 10 stays encumbered; 60 and 41 refused, 40 admitted"},
        {"step": 11, "event": "scope",
         "claim": s["maximum_claim"],
         "not_claimed": s["scope_exclusions"]},
    ]
    return json.dumps({"study": s["study"],
                       "terminal_class": s["terminal_class"],
                       "receipts": seq},
                      indent=2, sort_keys=True) + "\n"


def main(argv):
    s = load_summary()
    if "--json" in argv:
        sys.stdout.write(receipt_sequence(s))
    else:
        sys.stdout.write(transcript(s))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
