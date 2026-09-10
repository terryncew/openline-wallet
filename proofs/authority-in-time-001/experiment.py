"""Pure timing model for AUTHORITY-IN-TIME-001.

This module is deliberately test-fixture-only. It does not add a production
bypass or a new Wallet protocol surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

REQUIRED_BOUNDS = (
    "detect_bound_ms",
    "propagation_bound_ms",
    "receiver_bound_ms",
    "stop_bound_ms",
    "uncertainty_bound_ms",
)

ALLOWED_POLICY_KEYS = set(REQUIRED_BOUNDS) | {
    "consequence_horizon_ms",
    "bounds_supported",
    "uncertainty_accounting",
}

CLOCK_MODEL = (
    "VIRTUAL_MONOTONIC_OFFSETS_FROM_CASE_T0; Wallet UTC timestamps are derived "
    "from the same case T0"
)


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def evaluate_budget(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the frozen strict timing admission rule.

    Missing/unsupported bounds and non-positive margin fail closed. Queueing and
    jitter are represented exactly once inside uncertainty_bound_ms.
    """
    if not isinstance(policy, Mapping):
        return {
            "admission_decision": "REFUSE_REVOCATION_PROTECTION",
            "reason_codes": ["POLICY_INVALID"],
            "required_time_ms": None,
            "remaining_margin_ms": None,
        }

    extras = sorted(set(policy) - ALLOWED_POLICY_KEYS)
    if extras:
        return {
            "admission_decision": "REFUSE_REVOCATION_PROTECTION",
            "reason_codes": ["UNRECOGNIZED_TIMING_COMPONENT:" + name for name in extras],
            "required_time_ms": None,
            "remaining_margin_ms": None,
        }

    missing = [name for name in REQUIRED_BOUNDS if name not in policy]
    if "consequence_horizon_ms" not in policy:
        missing.append("consequence_horizon_ms")
    if missing:
        return {
            "admission_decision": "REFUSE_REVOCATION_PROTECTION",
            "reason_codes": ["BOUND_MISSING:" + name for name in missing],
            "required_time_ms": None,
            "remaining_margin_ms": None,
        }

    invalid = [name for name in REQUIRED_BOUNDS if not _positive_int(policy[name])]
    if not _positive_int(policy["consequence_horizon_ms"]):
        invalid.append("consequence_horizon_ms")
    if invalid:
        return {
            "admission_decision": "REFUSE_REVOCATION_PROTECTION",
            "reason_codes": ["BOUND_INVALID:" + name for name in invalid],
            "required_time_ms": None,
            "remaining_margin_ms": None,
        }

    support = policy.get("bounds_supported")
    if not isinstance(support, Mapping):
        return {
            "admission_decision": "REFUSE_REVOCATION_PROTECTION",
            "reason_codes": ["BOUNDS_SUPPORT_MISSING"],
            "required_time_ms": None,
            "remaining_margin_ms": None,
        }
    unsupported = [name for name in REQUIRED_BOUNDS if support.get(name) is not True]
    if unsupported:
        return {
            "admission_decision": "REFUSE_REVOCATION_PROTECTION",
            "reason_codes": ["BOUND_UNSUPPORTED:" + name for name in unsupported],
            "required_time_ms": None,
            "remaining_margin_ms": None,
        }

    if policy.get("uncertainty_accounting") != "QUEUEING_AND_JITTER_INCLUDED_ONCE":
        return {
            "admission_decision": "REFUSE_REVOCATION_PROTECTION",
            "reason_codes": ["UNCERTAINTY_ACCOUNTING_INVALID"],
            "required_time_ms": None,
            "remaining_margin_ms": None,
        }

    required = sum(int(policy[name]) for name in REQUIRED_BOUNDS)
    remaining = int(policy["consequence_horizon_ms"]) - required
    if remaining <= 0:
        return {
            "admission_decision": "REFUSE_REVOCATION_PROTECTION",
            "reason_codes": ["NONPOSITIVE_REMAINING_MARGIN"],
            "required_time_ms": required,
            "remaining_margin_ms": remaining,
        }
    return {
        "admission_decision": "ADMIT_REVOCATION_PROTECTED",
        "reason_codes": [],
        "required_time_ms": required,
        "remaining_margin_ms": remaining,
    }


def measured_intervals(event_times: Mapping[str, int]) -> dict[str, int]:
    required = (
        "revocation_issued_ms",
        "revocation_detected_ms",
        "propagation_complete_ms",
        "receiver_observed_ms",
        "stop_complete_ms",
    )
    for name in required:
        if type(event_times.get(name)) is not int:
            raise ValueError("EVENT_TIME_MISSING:" + name)
    values = [event_times[name] for name in required]
    if values != sorted(values):
        raise ValueError("EVENT_TIME_REVERSED")
    return {
        "detect_ms": event_times["revocation_detected_ms"] - event_times["revocation_issued_ms"],
        "propagation_ms": event_times["propagation_complete_ms"] - event_times["revocation_detected_ms"],
        "receiver_ms": event_times["receiver_observed_ms"] - event_times["propagation_complete_ms"],
        "stop_ms": event_times["stop_complete_ms"] - event_times["receiver_observed_ms"],
    }


def violated_bounds(policy: Mapping[str, Any], event_times: Mapping[str, int]) -> list[str]:
    actual = measured_intervals(event_times)
    pairs = (
        ("detect_bound_ms", "detect_ms"),
        ("propagation_bound_ms", "propagation_ms"),
        ("receiver_bound_ms", "receiver_ms"),
        ("stop_bound_ms", "stop_ms"),
    )
    return [bound for bound, observed in pairs if actual[observed] > int(policy[bound])]


@dataclass
class SimulatedAction:
    """Local fixture with a cancellation cutoff before visible completion."""

    action_id: str
    cancellation_cutoff_ms: int
    effect_visible_complete_ms: int
    armed: bool = False
    cancelled: bool = False
    test_only_bypass: bool = False
    effect_observed: bool = False
    effect_ledger: list[dict[str, Any]] = field(default_factory=list)
    ledger_checked_at_ms: int | None = None

    def __post_init__(self) -> None:
        if not _positive_int(self.cancellation_cutoff_ms):
            raise ValueError("CUTOFF_INVALID")
        if not _positive_int(self.effect_visible_complete_ms):
            raise ValueError("EFFECT_TIME_INVALID")
        if self.effect_visible_complete_ms <= self.cancellation_cutoff_ms:
            raise ValueError("EFFECT_MUST_COMPLETE_AFTER_CUTOFF")

    def arm(self, admission_decision: str, *, test_only_bypass: bool = False) -> None:
        if test_only_bypass:
            self.armed = True
            self.test_only_bypass = True
            return
        if admission_decision != "ADMIT_REVOCATION_PROTECTED":
            return
        self.armed = True

    def apply_stop(self, stop_complete_ms: int) -> bool:
        if not self.armed:
            return False
        # Strict boundary: equality is not accepted as safely cancellable.
        if stop_complete_ms < self.cancellation_cutoff_ms:
            self.cancelled = True
            return True
        return False

    def observe_effects(self, check_at_ms: int) -> list[dict[str, Any]]:
        if check_at_ms < self.effect_visible_complete_ms:
            raise ValueError("EFFECT_EVIDENCE_TOO_EARLY")
        if self.armed and not self.cancelled and not self.effect_observed:
            self.effect_observed = True
            self.effect_ledger.append({
                "action_id": self.action_id,
                "irreversible_commit_ms": self.cancellation_cutoff_ms,
                "visible_complete_ms": self.effect_visible_complete_ms,
            })
        self.ledger_checked_at_ms = check_at_ms
        return list(self.effect_ledger)

    def has_closure_evidence(self) -> bool:
        return (
            self.ledger_checked_at_ms is not None
            and self.ledger_checked_at_ms >= self.effect_visible_complete_ms
        )
