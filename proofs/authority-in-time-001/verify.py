"""Verify AUTHORITY-IN-TIME-001 captured evidence and its falsifiers."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path
import sys
from typing import Any

from openline_wallet.canonical import strict_json_load
from openline_wallet.crypto import record_hash, verify_record
from openline_wallet.wallet import verify_bundle
from openline_wallet.clock import parse_time

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("authority_in_time_experiment", HERE / "experiment.py")
exp = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = exp
SPEC.loader.exec_module(exp)

EXPECTED_CASES = {
    "supported-fit": "PROTECTED_PREVENTION",
    "budget-exceeds-cutoff": "UNSAFE_BUDGET_REFUSED",
    "exact-boundary": "EQUALITY_REFUSED",
    "late-revocation-negative-control": "LATE_REVOCATION_EFFECT_OCCURRED",
    "violated-propagation-assumption": "BOUND_VIOLATION_INVALIDATED_PROTECTION",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("AUTHORITY_IN_TIME_VERIFY_FAILED:" + message)


def verify_hashes(root: Path) -> None:
    lines = (root / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
    require(bool(lines), "empty SHA256SUMS")
    for line in lines:
        digest, relative = line.split("  ", 1)
        path = root / relative
        require(path.is_file(), "missing evidence file:" + relative)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "hash mismatch:" + relative)


def verify_revocation(case: dict[str, Any]) -> None:
    bundle = case.get("revoked_bundle")
    receipt = case.get("revocation_observation_receipt")
    require(isinstance(bundle, dict) and isinstance(receipt, dict), case["case_id"] + ":missing revocation evidence")
    verified, timeline = verify_bundle(bundle, now=parse_time(bundle["issued_at"], "revoked bundle issued_at"))
    mandate_id = case["authority_receipt"]["mandate_id"]
    require(timeline.mandates[mandate_id]["status"] == "REVOKED", case["case_id"] + ":bundle does not prove revocation")
    gate_key = case["authority_receipt"]["gate_public_key"]
    require(verify_record(receipt, expected_public_key=gate_key)[0], case["case_id"] + ":revocation receipt signature")
    require(receipt["decision"] == "STOPPED" and "MANDATE_REVOKED" in receipt["reason_codes"], case["case_id"] + ":revocation observation not STOPPED")
    require(verified["principal"]["principal_id"] == receipt["principal_id"], case["case_id"] + ":revocation principal mismatch")


def verify_case(case: dict[str, Any]) -> None:
    cid = case["case_id"]
    require(case.get("verdict") == EXPECTED_CASES[cid], cid + ":wrong verdict")
    require(case.get("clock_model") == exp.CLOCK_MODEL, cid + ":clock model mismatch")
    require(record_hash(case["policy"]) == case["policy_digest"], cid + ":policy digest mismatch")

    authority = case["authority_receipt"]
    gate_key = authority["gate_public_key"]
    require(verify_record(authority, expected_public_key=gate_key)[0], cid + ":authority receipt signature")
    require(authority["decision"] == "ALLOWED", cid + ":base authority was not allowed")

    temporal = case["temporal_receipt"]
    require(verify_record(temporal, expected_public_key=gate_key)[0], cid + ":temporal receipt signature")
    require(verify_record(case, expected_public_key=gate_key)[0], cid + ":final case receipt signature")
    require(temporal["policy_digest"] == case["policy_digest"], cid + ":temporal receipt policy mismatch")
    require(temporal["action_id"] == case["action_id"], cid + ":temporal action mismatch")
    require(temporal["authority_receipt_hash"] == record_hash(authority), cid + ":temporal authority binding mismatch")
    require(temporal["admission_decision"] == case["admission_decision"], cid + ":temporal/final admission decision mismatch")
    require(temporal["required_time_ms"] == case["required_time_ms"], cid + ":temporal required time mismatch")
    require(temporal["remaining_margin_ms"] == case["remaining_margin_ms"], cid + ":temporal margin mismatch")

    budget = exp.evaluate_budget(case["policy"])
    require(case["required_time_ms"] == budget["required_time_ms"], cid + ":required time mismatch")
    require(case["remaining_margin_ms"] == budget["remaining_margin_ms"], cid + ":remaining margin mismatch")

    cutoff = case["cancellation_cutoff"]["offset_ms"]
    visible = case["visible_completion_ms"]
    require(visible > cutoff, cid + ":visible completion must follow cutoff")
    facts = case.get("evidence_facts")
    require(isinstance(facts, dict), cid + ":missing separated evidence facts")
    require(set(facts) == {"revocation_issued", "revocation_observed", "outstanding_effects_closed"}, cid + ":evidence fact set mismatch")
    evidence = case.get("effect_evidence")
    require(isinstance(evidence, dict), cid + ":missing effect evidence")
    require(evidence.get("ledger_checked_at_ms", -1) >= visible, cid + ":effect evidence sampled too early")
    observed = bool(evidence.get("ledger"))
    require(observed is case["effect_observed"], cid + ":effect_observed inconsistent with ledger")

    if cid == "supported-fit":
        verify_revocation(case)
        require(budget["admission_decision"] == "ADMIT_REVOCATION_PROTECTED", cid + ":safe budget was not admitted")
        require(case["admission_decision"] == "ADMIT_REVOCATION_PROTECTED", cid + ":receipt did not claim protected admission")
        require(case["remaining_margin_ms"] > 0, cid + ":nonpositive safe margin")
        require(exp.violated_bounds(case["policy"], case["observed_event_times"]) == [], cid + ":supported bound violated")
        require(case["observed_event_times"]["stop_complete_ms"] < cutoff, cid + ":protected stop not before cutoff")
        require(not observed, cid + ":protected action produced an effect")
        require(facts["revocation_issued"]["status"] is True and facts["revocation_observed"]["status"] is True, cid + ":revocation facts not separately evidenced")
        require(facts["outstanding_effects_closed"]["status"] is True, cid + ":outstanding effect closure missing")
        require(case["closure_status"] == "FIXTURE_EFFECT_CLOSED_NO_EFFECT", cid + ":wrong closure")

    elif cid in {"budget-exceeds-cutoff", "exact-boundary"}:
        require(budget["admission_decision"] == "REFUSE_REVOCATION_PROTECTION", cid + ":unsafe/equality budget earned protection")
        require(case["admission_decision"] == "REFUSE_REVOCATION_PROTECTION", cid + ":unsafe/equality admission mismatch")
        require(case["remaining_margin_ms"] <= 0, cid + ":refusal does not test boundary")
        require(not observed, cid + ":refused action produced effect")
        require(facts["revocation_issued"]["status"] == "NOT_APPLICABLE" and facts["revocation_observed"]["status"] == "NOT_APPLICABLE", cid + ":refused case invented revocation evidence")
        require(facts["outstanding_effects_closed"]["status"] is True, cid + ":refused case not closed")
        require(case["closure_status"] == "NO_EFFECT_NOT_ADMITTED", cid + ":refused case closure mismatch")

    elif cid == "late-revocation-negative-control":
        verify_revocation(case)
        require(budget["admission_decision"] == "REFUSE_REVOCATION_PROTECTION", cid + ":negative control did not bypass an actual refusal")
        require(case.get("test_only_bypass") is True, cid + ":negative control is not marked fixture-only")
        require(case["admission_decision"] == "TEST_ONLY_BYPASS", cid + ":negative control falsely admitted as protected")
        require(case["protection_status"] == "NOT_CLAIMED", cid + ":late revocation described as protected")
        require(case["observed_event_times"]["receiver_observed_ms"] >= cutoff, cid + ":revocation did not arrive after cutoff")
        require(case["observed_event_times"]["stop_complete_ms"] >= cutoff, cid + ":late stop unexpectedly before cutoff")
        require(observed, cid + ":late bypass negative effect not observed")
        require(facts["revocation_issued"]["status"] is True and facts["revocation_observed"]["status"] is True, cid + ":late revocation facts missing")
        require(facts["outstanding_effects_closed"]["status"] is True, cid + ":late effect outcome not closed")
        require(case["closure_status"] == "EFFECT_OBSERVED_AFTER_IRREVERSIBLE_CUTOFF", cid + ":late effect misreported")

    elif cid == "violated-propagation-assumption":
        verify_revocation(case)
        violations = exp.violated_bounds(case["policy"], case["observed_event_times"])
        require(violations == ["propagation_bound_ms"], cid + ":expected propagation violation missing")
        require(case["violated_bounds"] == violations, cid + ":violation evidence mismatch")
        require(case["protection_status"] == "INVALIDATED_BOUND_VIOLATION", cid + ":bound violation retained protection")
        require(case["reconciliation_triggered"] is True, cid + ":bound violation did not trigger reconciliation")
        require(facts["revocation_issued"]["status"] is True and facts["revocation_observed"]["status"] is True, cid + ":bound-violation revocation facts missing")
        require(facts["outstanding_effects_closed"]["status"] is True, cid + ":bound-violation reconciliation not evidenced")
        require(case["closure_status"] == "RECONCILED_NO_EFFECT", cid + ":reconciliation not closed")
        require(not observed, cid + ":reconciled fixture produced effect")


def verify(root: Path) -> None:
    verify_hashes(root)
    result = strict_json_load(root / "result.json")
    require(verify_record(result)[0], "aggregate result signature invalid")
    require(result.get("experiment_id") == "AUTHORITY-IN-TIME-001", "wrong experiment")
    require(result.get("base_commit") == "69dfdcd1d229fb887660a347018b3422c1020527", "wrong base")
    require(result.get("cases") == EXPECTED_CASES, "aggregate case map mismatch")
    expected_sources = {
        "proofs/authority-in-time-001/experiment.py",
        "proofs/authority-in-time-001/reproduce.py",
        "proofs/authority-in-time-001/verify.py",
        "proofs/authority-in-time-001/test_authority_in_time.py",
    }
    require(set(result.get("source_sha256", {})) == expected_sources, "source hash set mismatch")
    for relative, expected in result["source_sha256"].items():
        # source_sha256 paths are rooted at this proof directory; verify exact current bytes.
        name = relative.split("/")[-1]
        path = HERE / name
        require(path.is_file(), "missing source:" + relative)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, "source hash mismatch:" + relative)

    cases = {}
    for path in sorted((root / "cases").glob("*.json")):
        case = strict_json_load(path)
        cases[case["case_id"]] = case
    require(set(cases) == set(EXPECTED_CASES), "case set mismatch")
    require(result.get("case_receipt_hashes") == {cid: record_hash(cases[cid]) for cid in EXPECTED_CASES}, "aggregate case receipt binding mismatch")
    for cid in EXPECTED_CASES:
        verify_case(cases[cid])

    # Frozen falsifiers. Any one of these being false means the earned claim is not valid.
    fit = cases["supported-fit"]
    late = cases["late-revocation-negative-control"]
    violated = cases["violated-propagation-assumption"]
    require(fit["admission_decision"] == "ADMIT_REVOCATION_PROTECTED" and not fit["effect_observed"], "protected path falsified")
    require(late["effect_observed"] and late["protection_status"] == "NOT_CLAIMED", "late revocation prevention claim falsified")
    require(violated["reconciliation_triggered"] and violated["protection_status"].startswith("INVALIDATED"), "bound violation handling falsified")
    print("AUTHORITY-IN-TIME-001: PASS (5/5 discriminating cases; receipts and closure evidence verified)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    verify(args.root)


if __name__ == "__main__":
    main()
