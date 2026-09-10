"""Reproduce AUTHORITY-IN-TIME-001 against the current Wallet/receiver APIs.

All time after temporal admission is represented as virtual monotonic offsets
from one case T0. UTC timestamps used by Wallet are derived from that same T0.
No money, network, provider, or production effect is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.canonical import canonical_json, pretty_json
from openline_wallet.crypto import public_key_hex, record_hash, sign_record, verify_record
from openline_wallet.receiver import ReferenceGate, create_presentation
from openline_wallet.wallet import Wallet

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("authority_in_time_experiment", HERE / "experiment.py")
exp = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = exp
SPEC.loader.exec_module(exp)

EXPERIMENT_ID = "AUTHORITY-IN-TIME-001"
BASE_COMMIT = "69dfdcd1d229fb887660a347018b3422c1020527"
BASE = datetime(2026, 9, 10, 3, 30, 0, tzinfo=timezone.utc)
ACTION = "deploy:staging"
TEMPORAL_SCHEMA = "openline.wallet.temporal_admission_receipt.v0"
CASE_SCHEMA = "openline.wallet.authority_in_time_case.v1"
RESULT_SCHEMA = "openline.wallet.authority_in_time_experiment.v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("AUTHORITY_IN_TIME_PROOF_FAILED:" + message)


def t(offset_ms: int) -> datetime:
    return BASE + timedelta(milliseconds=offset_ms)


def policy(values=(10, 20, 10, 10, 10), horizon=100, *, supported: dict[str, bool] | None = None) -> dict[str, Any]:
    support = supported or {name: True for name in exp.REQUIRED_BOUNDS}
    return {
        **dict(zip(exp.REQUIRED_BOUNDS, values)),
        "consequence_horizon_ms": horizon,
        "bounds_supported": support,
        "uncertainty_accounting": "QUEUEING_AND_JITTER_INCLUDED_ONCE",
    }


def bounds_basis(p: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "value_ms": p.get(name),
            "supported": p.get("bounds_supported", {}).get(name) is True,
            "basis": "CONTROLLED_FIXTURE_ASSUMPTION_NOT_A_NETWORK_WORST_CASE",
        }
        for name in exp.REQUIRED_BOUNDS
    }


def fixture(root: Path, case_id: str) -> tuple[Wallet, Ed25519PrivateKey, ReferenceGate, dict[str, Any], dict[str, Any]]:
    wallet = Wallet.create(root / "wallet", now=t(-5000))
    holder_key = Ed25519PrivateKey.generate()
    mandate_id = "grant-" + case_id
    wallet.grant(
        subject_id="agent-a",
        subject_public_key=public_key_hex(holder_key),
        scopes=[ACTION],
        expires_at=t(60_000),
        now=t(-4000),
        mandate_id=mandate_id,
    )
    bundle = wallet.export_bundle(now=t(-1000))
    gate = ReferenceGate("authority-time-" + case_id)
    gate.pin_principal(wallet.principal_id, wallet.root_public_key)
    gate.admit_bundle(bundle, now=t(-900))
    challenge = gate.issue_challenge(
        principal_id=wallet.principal_id,
        subject_id="agent-a",
        action=ACTION,
        now=t(-100),
    )
    presentation = create_presentation(
        bundle=bundle,
        mandate_id=mandate_id,
        subject_id="agent-a",
        subject_key=holder_key,
        action=ACTION,
        receiver_challenge=challenge,
        now=t(-100),
    )
    authority_receipt = gate.evaluate(presentation, expected_action=ACTION, now=t(-50))
    require(authority_receipt["decision"] == "ALLOWED", case_id + ":initial authority not allowed")
    require(verify_record(authority_receipt, expected_public_key=gate.public_key)[0], case_id + ":initial receipt invalid")
    return wallet, holder_key, gate, bundle, authority_receipt


def temporal_receipt(
    gate: ReferenceGate,
    *,
    case_id: str,
    action_id: str,
    p: dict[str, Any],
    budget: dict[str, Any],
    authority_receipt: dict[str, Any],
    decision_override: str | None = None,
) -> dict[str, Any]:
    decision = decision_override or budget["admission_decision"]
    receipt = sign_record(
        {
            "schema": TEMPORAL_SCHEMA,
            "experiment_id": EXPERIMENT_ID,
            "case_id": case_id,
            "action_id": action_id,
            "policy_digest": record_hash(p),
            "clock_model": exp.CLOCK_MODEL,
            "bounds_basis": bounds_basis(p),
            "assumed_bounds_ms": {name: p.get(name) for name in exp.REQUIRED_BOUNDS},
            "consequence_horizon_ms": p.get("consequence_horizon_ms"),
            "required_time_ms": budget.get("required_time_ms"),
            "remaining_margin_ms": budget.get("remaining_margin_ms"),
            "admission_decision": decision,
            "reason_codes": list(budget.get("reason_codes", [])),
            "authority_receipt_hash": record_hash(authority_receipt),
            "decided_at": t(0).isoformat().replace("+00:00", "Z"),
            "decision_authority": "RECEIVER_GATE",
        },
        gate.gate_key,
    )
    require(verify_record(receipt, expected_public_key=gate.public_key)[0], case_id + ":temporal receipt invalid")
    return receipt


def authentic_revocation(
    wallet: Wallet,
    holder_key: Ed25519PrivateKey,
    gate: ReferenceGate,
    *,
    mandate_id: str,
    issued_ms: int,
    observed_ms: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    wallet.revoke(mandate_id, now=t(issued_ms))
    revoked_bundle = wallet.export_bundle(now=t(issued_ms + 1))
    gate.admit_bundle(revoked_bundle, now=t(observed_ms))
    challenge = gate.issue_challenge(
        principal_id=wallet.principal_id,
        subject_id="agent-a",
        action=ACTION,
        now=t(observed_ms),
    )
    presentation = create_presentation(
        bundle=revoked_bundle,
        mandate_id=mandate_id,
        subject_id="agent-a",
        subject_key=holder_key,
        action=ACTION,
        receiver_challenge=challenge,
        now=t(observed_ms),
    )
    stopped = gate.evaluate(presentation, expected_action=ACTION, now=t(observed_ms))
    require(stopped["decision"] == "STOPPED", mandate_id + ":revocation did not stop receiver evaluation")
    require("MANDATE_REVOKED" in stopped["reason_codes"], mandate_id + ":wrong revocation reason")
    require(verify_record(stopped, expected_public_key=gate.public_key)[0], mandate_id + ":stopped receipt invalid")
    return revoked_bundle, stopped


def base_case(case_id: str, p: dict[str, Any], root: Path, *, decision_override: str | None = None):
    wallet, holder_key, gate, _bundle, authority_receipt = fixture(root, case_id)
    budget = exp.evaluate_budget(p)
    action_id = "simulated:" + case_id
    temporal = temporal_receipt(
        gate,
        case_id=case_id,
        action_id=action_id,
        p=p,
        budget=budget,
        authority_receipt=authority_receipt,
        decision_override=decision_override,
    )
    return wallet, holder_key, gate, authority_receipt, budget, temporal, action_id


def run_fit(root: Path) -> dict[str, Any]:
    case_id = "supported-fit"
    p = policy()
    wallet, key, gate, authority, budget, temporal, action_id = base_case(case_id, p, root)
    action = exp.SimulatedAction(action_id, 100, 150)
    action.arm(budget["admission_decision"])
    events = {
        "revocation_issued_ms": 0,
        "revocation_detected_ms": 8,
        "propagation_complete_ms": 18,
        "receiver_observed_ms": 25,
        "stop_complete_ms": 35,
    }
    revoked_bundle, stopped = authentic_revocation(
        wallet, key, gate, mandate_id="grant-" + case_id, issued_ms=0, observed_ms=25
    )
    require(exp.violated_bounds(p, events) == [], case_id + ":fixture violated a supported bound")
    require(action.apply_stop(events["stop_complete_ms"]), case_id + ":stop missed cutoff")
    ledger = action.observe_effects(150)
    require(ledger == [] and action.has_closure_evidence(), case_id + ":protected effect observed or closure missing")
    return sign_record({
        "schema": CASE_SCHEMA,
        "case_id": case_id,
        "action_id": action_id,
        "policy": p,
        "policy_digest": record_hash(p),
        "clock_model": exp.CLOCK_MODEL,
        "bounds_basis": bounds_basis(p),
        "authority_receipt": authority,
        "temporal_receipt": temporal,
        "admission_decision": budget["admission_decision"],
        "required_time_ms": budget["required_time_ms"],
        "remaining_margin_ms": budget["remaining_margin_ms"],
        "assumed_bounds_ms": {name: p[name] for name in exp.REQUIRED_BOUNDS},
        "measured_intervals_ms": exp.measured_intervals(events),
        "observed_event_times": events,
        "cancellation_cutoff": {"offset_ms": 100, "meaning": "LAST_PREVENTABLE_INSTANT_EXCLUSIVE"},
        "visible_completion_ms": 150,
        "revoked_bundle": revoked_bundle,
        "revocation_observation_receipt": stopped,
        "violated_bounds": [],
        "reconciliation_triggered": False,
        "effect_evidence": {"ledger_checked_at_ms": 150, "ledger": ledger},
        "effect_observed": False,
        "evidence_facts": {
            "revocation_issued": {"status": True, "evidence": "revoked_bundle.events[-1]"},
            "revocation_observed": {"status": True, "evidence": "revocation_observation_receipt"},
            "outstanding_effects_closed": {"status": True, "evidence": "effect_evidence sampled after visible completion"},
        },
        "closure_status": "FIXTURE_EFFECT_CLOSED_NO_EFFECT",
        "protection_status": "VALID_FOR_THIS_CONTROLLED_RUN",
        "verdict": "PROTECTED_PREVENTION",
    }, gate.gate_key)


def run_refused(root: Path, *, equality: bool) -> dict[str, Any]:
    case_id = "exact-boundary" if equality else "budget-exceeds-cutoff"
    values = (20, 20, 20, 20, 20) if equality else (20, 30, 20, 20, 20)
    p = policy(values=values)
    _wallet, _key, gate, authority, budget, temporal, action_id = base_case(case_id, p, root)
    action = exp.SimulatedAction(action_id, 100, 150)
    action.arm(budget["admission_decision"])
    require(not action.armed, case_id + ":unsafe/equality action armed")
    ledger = action.observe_effects(150)
    require(ledger == [] and action.has_closure_evidence(), case_id + ":refused action produced effect")
    return sign_record({
        "schema": CASE_SCHEMA,
        "case_id": case_id,
        "action_id": action_id,
        "policy": p,
        "policy_digest": record_hash(p),
        "clock_model": exp.CLOCK_MODEL,
        "bounds_basis": bounds_basis(p),
        "authority_receipt": authority,
        "temporal_receipt": temporal,
        "admission_decision": budget["admission_decision"],
        "required_time_ms": budget["required_time_ms"],
        "remaining_margin_ms": budget["remaining_margin_ms"],
        "assumed_bounds_ms": {name: p[name] for name in exp.REQUIRED_BOUNDS},
        "measured_intervals_ms": None,
        "observed_event_times": {"admission_refused_ms": 0, "ledger_checked_at_ms": 150},
        "cancellation_cutoff": {"offset_ms": 100, "meaning": "LAST_PREVENTABLE_INSTANT_EXCLUSIVE"},
        "visible_completion_ms": 150,
        "violated_bounds": [],
        "reconciliation_triggered": False,
        "effect_evidence": {"ledger_checked_at_ms": 150, "ledger": ledger},
        "effect_observed": False,
        "evidence_facts": {
            "revocation_issued": {"status": "NOT_APPLICABLE", "evidence": None},
            "revocation_observed": {"status": "NOT_APPLICABLE", "evidence": None},
            "outstanding_effects_closed": {"status": True, "evidence": "action never armed; ledger sampled after visible completion"},
        },
        "closure_status": "NO_EFFECT_NOT_ADMITTED",
        "protection_status": "REFUSED",
        "verdict": "EQUALITY_REFUSED" if equality else "UNSAFE_BUDGET_REFUSED",
    }, gate.gate_key)


def run_late_bypass(root: Path) -> dict[str, Any]:
    case_id = "late-revocation-negative-control"
    # Use an unsafe budget so this arm is unambiguously a bypass of a refusal.
    p = policy(values=(20, 30, 20, 20, 20))
    wallet, key, gate, authority, budget, temporal, action_id = base_case(
        case_id, p, root, decision_override="TEST_ONLY_BYPASS"
    )
    action = exp.SimulatedAction(action_id, 100, 150)
    action.arm("REFUSE_REVOCATION_PROTECTION", test_only_bypass=True)
    events = {
        "revocation_issued_ms": 90,
        "revocation_detected_ms": 98,
        "propagation_complete_ms": 115,
        "receiver_observed_ms": 122,
        "stop_complete_ms": 130,
    }
    revoked_bundle, stopped = authentic_revocation(
        wallet, key, gate, mandate_id="grant-" + case_id, issued_ms=90, observed_ms=122
    )
    require(not action.apply_stop(events["stop_complete_ms"]), case_id + ":late stop incorrectly prevented consequence")
    ledger = action.observe_effects(150)
    require(len(ledger) == 1 and action.effect_observed, case_id + ":negative effect missing")
    return sign_record({
        "schema": CASE_SCHEMA,
        "case_id": case_id,
        "action_id": action_id,
        "policy": p,
        "policy_digest": record_hash(p),
        "clock_model": exp.CLOCK_MODEL,
        "bounds_basis": bounds_basis(p),
        "authority_receipt": authority,
        "temporal_receipt": temporal,
        "admission_decision": "TEST_ONLY_BYPASS",
        "required_time_ms": budget["required_time_ms"],
        "remaining_margin_ms": budget["remaining_margin_ms"],
        "assumed_bounds_ms": {name: p[name] for name in exp.REQUIRED_BOUNDS},
        "measured_intervals_ms": exp.measured_intervals(events),
        "observed_event_times": events,
        "cancellation_cutoff": {"offset_ms": 100, "meaning": "LAST_PREVENTABLE_INSTANT_EXCLUSIVE"},
        "visible_completion_ms": 150,
        "revoked_bundle": revoked_bundle,
        "revocation_observation_receipt": stopped,
        "violated_bounds": exp.violated_bounds(p, events),
        "reconciliation_triggered": False,
        "test_only_bypass": True,
        "effect_evidence": {"ledger_checked_at_ms": 150, "ledger": ledger},
        "effect_observed": True,
        "evidence_facts": {
            "revocation_issued": {"status": True, "evidence": "revoked_bundle.events[-1]"},
            "revocation_observed": {"status": True, "evidence": "revocation_observation_receipt"},
            "outstanding_effects_closed": {"status": True, "evidence": "effect_evidence shows completed irreversible effect"},
        },
        "closure_status": "EFFECT_OBSERVED_AFTER_IRREVERSIBLE_CUTOFF",
        "protection_status": "NOT_CLAIMED",
        "verdict": "LATE_REVOCATION_EFFECT_OCCURRED",
    }, gate.gate_key)


def run_bound_violation(root: Path) -> dict[str, Any]:
    case_id = "violated-propagation-assumption"
    p = policy()
    wallet, key, gate, authority, budget, temporal, action_id = base_case(case_id, p, root)
    action = exp.SimulatedAction(action_id, 100, 150)
    action.arm(budget["admission_decision"])
    events = {
        "revocation_issued_ms": 0,
        "revocation_detected_ms": 8,
        "propagation_complete_ms": 33,
        "receiver_observed_ms": 40,
        "stop_complete_ms": 50,
    }
    revoked_bundle, stopped = authentic_revocation(
        wallet, key, gate, mandate_id="grant-" + case_id, issued_ms=0, observed_ms=40
    )
    violated = exp.violated_bounds(p, events)
    require(violated == ["propagation_bound_ms"], case_id + ":wrong violated bound")
    # The effect happens not to occur, but the guarantee is invalidated anyway.
    require(action.apply_stop(events["stop_complete_ms"]), case_id + ":fixture stop unexpectedly missed cutoff")
    ledger = action.observe_effects(150)
    require(ledger == [] and action.has_closure_evidence(), case_id + ":reconciliation evidence incomplete")
    return sign_record({
        "schema": CASE_SCHEMA,
        "case_id": case_id,
        "action_id": action_id,
        "policy": p,
        "policy_digest": record_hash(p),
        "clock_model": exp.CLOCK_MODEL,
        "bounds_basis": bounds_basis(p),
        "authority_receipt": authority,
        "temporal_receipt": temporal,
        "admission_decision": budget["admission_decision"],
        "required_time_ms": budget["required_time_ms"],
        "remaining_margin_ms": budget["remaining_margin_ms"],
        "assumed_bounds_ms": {name: p[name] for name in exp.REQUIRED_BOUNDS},
        "measured_intervals_ms": exp.measured_intervals(events),
        "observed_event_times": events,
        "cancellation_cutoff": {"offset_ms": 100, "meaning": "LAST_PREVENTABLE_INSTANT_EXCLUSIVE"},
        "visible_completion_ms": 150,
        "revoked_bundle": revoked_bundle,
        "revocation_observation_receipt": stopped,
        "violated_bounds": violated,
        "reconciliation_triggered": True,
        "effect_evidence": {"ledger_checked_at_ms": 150, "ledger": ledger},
        "effect_observed": False,
        "evidence_facts": {
            "revocation_issued": {"status": True, "evidence": "revoked_bundle.events[-1]"},
            "revocation_observed": {"status": True, "evidence": "revocation_observation_receipt"},
            "outstanding_effects_closed": {"status": True, "evidence": "reconciliation ledger sampled after visible completion"},
        },
        "closure_status": "RECONCILED_NO_EFFECT",
        "protection_status": "INVALIDATED_BOUND_VIOLATION",
        "verdict": "BOUND_VIOLATION_INVALIDATED_PROTECTION",
    }, gate.gate_key)


def source_hashes() -> dict[str, str]:
    result = {}
    for name in ("experiment.py", "reproduce.py", "verify.py", "test_authority_in_time.py"):
        path = HERE / name
        result["proofs/authority-in-time-001/" + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("evidence output must be empty")
    cases_root = output / "cases"
    cases_root.mkdir()

    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        cases = [
            run_fit(root / "fit"),
            run_refused(root / "exceeds", equality=False),
            run_refused(root / "equality", equality=True),
            run_late_bypass(root / "late"),
            run_bound_violation(root / "violation"),
        ]

    for case in cases:
        (cases_root / (case["case_id"] + ".json")).write_text(pretty_json(case), encoding="ascii")

    verdicts = {case["case_id"]: case["verdict"] for case in cases}
    case_receipt_hashes = {case["case_id"]: record_hash(case) for case in cases}
    receipt = sign_record(
        {
            "schema": RESULT_SCHEMA,
            "experiment_id": EXPERIMENT_ID,
            "base_commit": BASE_COMMIT,
            "source_sha256": source_hashes(),
            "clock_model": exp.CLOCK_MODEL,
            "cases": verdicts,
            "case_receipt_hashes": case_receipt_hashes,
            "checks": {
                "supported_budget_admitted_and_prevented": True,
                "unsafe_budget_refused_pre_effect": True,
                "equality_refused_pre_effect": True,
                "late_authentic_revocation_reported_without_prevention_claim": True,
                "bound_violation_invalidated_protection_and_reconciled": True,
                "effect_evidence_required_for_closure": True,
            },
            "earned_claim": "In this controlled fixture, the receiver admitted revocation-protected work only within a supported timing budget and distinguished prevention from late revocation.",
            "verdict": "CONTROLLED_TEMPORAL_ADMISSION_ENFORCED",
            "limitations": [
                "All timing bounds are controlled fixture assumptions, not measured worst-case network or provider bounds.",
                "One in-process receiver and a virtual local effect are exercised; no production write or external service is used.",
                "The test does not prove clock synchronization, distributed queue cancellation, provider-side fencing, or multi-receiver temporal guarantees.",
            ],
            "verification": "Self-attested disposable experiment key; not an independent witness.",
        },
        Ed25519PrivateKey.generate(),
    )
    require(verify_record(receipt)[0], "aggregate result signature invalid")
    (output / "result.json").write_text(pretty_json(receipt), encoding="ascii")

    lines = []
    for path in sorted(p for p in output.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt"):
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output).as_posix()}")
    (output / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = run(args.output)
    print(json.dumps({"experiment": receipt["experiment_id"], "verdict": receipt["verdict"], "cases": receipt["cases"]}, sort_keys=True))


if __name__ == "__main__":
    main()
