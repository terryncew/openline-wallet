"""A bounded, observation-driven advisor for completed agent attempts.

The caller owns execution, trusted evaluation, metering, and reconciliation.
This module does not execute actions, observe a running worker, or retry effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from math import isfinite
from typing import Callable, List, Optional


def _positive_int(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(name + " must be a positive integer")
    return value


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(name + " must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(name + " must be a finite number") from None
    if not isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(name + " must be finite and nonnegative" +
                         (" and nonzero" if positive else ""))
    return result


def _money(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise ValueError(name + " must be a finite monetary amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(name + " must be a finite monetary amount") from None
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise ValueError(name + " must be finite and nonnegative" +
                         (" and nonzero" if positive else ""))
    return amount


@dataclass(frozen=True)
class Attempt:
    """One completed attempt. Result and outcome are not progress evidence."""
    action: object
    result: object = None
    outcome: str = "unknown"
    external_mutation: str = "none"  # none, confirmed, uncertain

    def __post_init__(self):
        if self.outcome not in {"success", "failure", "unknown"}:
            raise ValueError("outcome must be success, failure, or unknown")
        if self.external_mutation not in {"none", "confirmed", "uncertain"}:
            raise ValueError("external_mutation must be none, confirmed, or uncertain")


@dataclass(frozen=True)
class ProgressEvidence:
    """A task-specific, independently verified monotonic objective measurement.

    The verifier defines the metric and stable objective. Changing output text,
    a hypothesis name, or a state fingerprint is not itself progress.
    """
    objective_id: str
    state_fingerprint: str
    progress: int

    def __post_init__(self):
        for name in ("objective_id", "state_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(name + " must be a nonempty string")
        if type(self.progress) is not int or self.progress < 0:
            raise ValueError("progress must be a nonnegative integer")


@dataclass(frozen=True)
class WatchdogStatus:
    status: str
    freshness: float  # Diagnostic action diversity; never a stop criterion.
    burn_rate: Optional[Decimal]  # Actual spend units / elapsed second, if known.
    recommendation: str
    reason: str = "INSUFFICIENT_PROGRESS_EVIDENCE"
    progress_evidence: str = "INSUFFICIENT_PROGRESS_EVIDENCE"
    no_progress_steps: int = 0
    observed_steps: int = 0
    progress: Optional[int] = None
    state_fingerprint: Optional[str] = None
    elapsed_seconds: Optional[float] = None
    spent: Optional[Decimal] = None
    spend_unit: str = "USD"
    mutation_pending: bool = False


class AgentWatchdog:
    """Standalone advisor over completed attempts and caller-supplied usage.

    A trusted task evaluator must return ProgressEvidence. The evaluator must
    measure objective advance, not the agent's assertion that it made progress.
    A new action, output, hypothesis, or changed state cannot reset the
    non-progress allowance. Hard ceilings are independent and cumulative.
    """

    def __init__(
        self,
        kill_threshold: float = 0.25,
        window_size: int = 15,
        min_steps: int = 5,
        normalizer: Optional[Callable[[object], str]] = None,
        *,
        objective_id: Optional[str] = None,
        verifier: Optional[Callable[[Attempt], Optional[ProgressEvidence]]] = None,
        baseline: Optional[ProgressEvidence] = None,
        max_no_progress_steps: Optional[int] = None,
        max_elapsed_seconds: Optional[float] = None,
        max_spend=None,
        spend_unit: str = "USD",
        reconciliation_verifier: Optional[Callable[[object], bool]] = None,
    ):
        self.kill_threshold = _number(kill_threshold, "kill_threshold", positive=True)
        if self.kill_threshold > 1:
            raise ValueError("kill_threshold must not exceed one")
        self.window_size = _positive_int(window_size, "window_size")
        self.min_steps = _positive_int(min_steps, "min_steps")
        self.max_no_progress_steps = _positive_int(
            self.min_steps if max_no_progress_steps is None else max_no_progress_steps,
            "max_no_progress_steps")
        if normalizer is not None and not callable(normalizer):
            raise ValueError("normalizer must be callable")
        if verifier is not None and not callable(verifier):
            raise ValueError("verifier must be callable")
        if reconciliation_verifier is not None and not callable(reconciliation_verifier):
            raise ValueError("reconciliation_verifier must be callable")
        if objective_id is not None and (not isinstance(objective_id, str) or
                                         not objective_id.strip()):
            raise ValueError("objective_id must be a nonempty string")
        if verifier is not None and objective_id is None:
            raise ValueError("a trusted verifier requires a fixed objective_id")
        if baseline is not None:
            if not isinstance(baseline, ProgressEvidence):
                raise ValueError("baseline must be ProgressEvidence")
            if objective_id is None or baseline.objective_id != objective_id:
                raise ValueError("baseline objective mismatch")
        if not isinstance(spend_unit, str) or not spend_unit.strip():
            raise ValueError("spend_unit must be a nonempty string")

        self.normalizer = normalizer or self._default_normalizer
        self.verifier = verifier
        self.reconciliation_verifier = reconciliation_verifier
        self.objective_id = objective_id
        self.history: List[str] = []
        self.attempt_count = 0
        self.observed_steps = 0
        self.no_progress_steps = 0
        self.progress = baseline.progress if baseline is not None else None
        self.state_fingerprint = (baseline.state_fingerprint if baseline is not None
                                  else None)
        self._last_evidence = "INSUFFICIENT_PROGRESS_EVIDENCE"
        self._observation_error = None
        self._stop_reason = None
        self._mutation_pending = False
        self.elapsed_seconds = None
        self.spent = None
        self.spend_unit = spend_unit
        self.max_elapsed_seconds = (None if max_elapsed_seconds is None else
                                    _number(max_elapsed_seconds, "max_elapsed_seconds",
                                            positive=True))
        self.max_spend = (None if max_spend is None else
                          _money(max_spend, "max_spend", positive=True))

    @staticmethod
    def _default_normalizer(action: object) -> str:
        if action is None:
            return ""
        return str(action).strip()

    def _append_action(self, action):
        sig = self.normalizer(action)
        if not isinstance(sig, str):
            raise ValueError("normalizer must return a string")
        self.attempt_count += 1
        if sig:
            self.history.append(sig)

    def log_action(self, action: object) -> None:
        """Legacy action-only input: diagnostic history, never progress."""
        self._append_action(action)
        self._last_evidence = "INSUFFICIENT_PROGRESS_EVIDENCE"
        self._observation_error = None

    def log_attempt(self, attempt: Attempt) -> None:
        """Observe a completed attempt through the configured trusted evaluator."""
        if not isinstance(attempt, Attempt):
            raise TypeError("log_attempt requires Attempt")
        if self._mutation_pending:
            raise RuntimeError("RECONCILIATION_REQUIRED")
        self._append_action(attempt.action)
        self._last_evidence = "INSUFFICIENT_PROGRESS_EVIDENCE"
        self._observation_error = None
        if attempt.external_mutation == "uncertain":
            self._mutation_pending = True
            return
        # A terminal decision cannot be refreshed within the same run.
        if self._stop_reason is not None:
            return
        if self.verifier is None:
            return
        try:
            evidence = self.verifier(attempt)
            if evidence is None:
                return
            if not isinstance(evidence, ProgressEvidence):
                raise ValueError("verifier must return ProgressEvidence or None")
            if evidence.objective_id != self.objective_id:
                raise ValueError("verifier objective mismatch")
        except Exception as exc:
            self._observation_error = type(exc).__name__
            return
        self.observed_steps += 1
        self.state_fingerprint = evidence.state_fingerprint
        if self.progress is None:
            # The first observation establishes a baseline, not an invented gain.
            self.progress = evidence.progress
            self._last_evidence = "BASELINE_OBSERVED"
        elif evidence.progress > self.progress:
            self.progress = evidence.progress
            self.no_progress_steps = 0
            self._last_evidence = "OBSERVED_PROGRESS"
        else:
            # A changed state or regressed metric never refreshes the allowance.
            self.no_progress_steps += 1
            self._last_evidence = "OBSERVED_NO_PROGRESS"
            if self.no_progress_steps >= self.max_no_progress_steps:
                self._stop_reason = "OBSERVED_STAGNATION"

    def resolve_uncertainty(self, evidence: object) -> bool:
        """Clear a held mutation only after caller-owned, read-only reconciliation.

        The callback's exact True means the specific uncertain outcome has been
        reconciled. It must not dispatch or retry a mutation. Resolution does not
        reset the progress allowance or a terminal stop.
        """
        if not self._mutation_pending:
            raise RuntimeError("NO_UNCERTAIN_MUTATION")
        if self.reconciliation_verifier is None:
            raise RuntimeError("RECONCILIATION_VERIFIER_REQUIRED")
        if self.reconciliation_verifier(evidence) is not True:
            return False
        self._mutation_pending = False
        self._last_evidence = "INSUFFICIENT_PROGRESS_EVIDENCE"
        self._observation_error = None
        return True

    def record_usage(self, *, elapsed_seconds=None, spent=None) -> None:
        """Record cumulative actual usage, never action-derived estimates."""
        next_elapsed = self.elapsed_seconds
        next_spent = self.spent
        if elapsed_seconds is not None:
            next_elapsed = _number(elapsed_seconds, "elapsed_seconds")
            if self.elapsed_seconds is not None and next_elapsed < self.elapsed_seconds:
                raise ValueError("elapsed_seconds must be cumulative and nondecreasing")
        if spent is not None:
            next_spent = _money(spent, "spent")
            if self.spent is not None and next_spent < self.spent:
                raise ValueError("spent must be cumulative and nondecreasing")
        # Validate both before updating either meter.
        self.elapsed_seconds, self.spent = next_elapsed, next_spent

    @staticmethod
    def _freshness(seq: List[str]) -> float:
        if not seq:
            return 1.0
        return len(set(seq)) / len(seq)

    def min_window_freshness(self) -> float:
        if not self.history:
            return 1.0
        if len(self.history) <= self.window_size:
            return self._freshness(self.history)
        return min(self._freshness(self.history[i-self.window_size:i])
                   for i in range(self.window_size, len(self.history)+1))

    def audit(self, use_window: bool = True, *, elapsed_seconds=None,
              spent=None) -> WatchdogStatus:
        self.record_usage(elapsed_seconds=elapsed_seconds, spent=spent)
        scope = (self.history[-self.window_size:] if use_window else self.history)
        freshness = self._freshness(scope)
        burn_rate = None
        if (self.spent is not None and self.elapsed_seconds is not None
                and self.elapsed_seconds > 0):
            with localcontext() as context:
                context.prec = max(28, len(self.spent.as_tuple().digits) + 18)
                burn_rate = self.spent / Decimal(str(self.elapsed_seconds))
        reason = self._stop_reason
        if self.max_elapsed_seconds is not None and self.elapsed_seconds is not None:
            if self.elapsed_seconds >= self.max_elapsed_seconds:
                reason = "HARD_TIME_LIMIT"
        if self.max_spend is not None and self.spent is not None:
            if self.spent >= self.max_spend:
                reason = "HARD_SPEND_LIMIT"
        if reason is not None:
            self._stop_reason = reason
            status, recommendation = "RED", ("STOP AND RECONCILE" if self._mutation_pending
                                             else "STOP RUN")
        elif self._mutation_pending:
            status, reason, recommendation = "HOLD", "EXTERNAL_MUTATION_UNCERTAIN", "RECONCILE"
        elif self._last_evidence == "INSUFFICIENT_PROGRESS_EVIDENCE":
            status, reason, recommendation = ("UNKNOWN", "INSUFFICIENT_PROGRESS_EVIDENCE",
                                              "OBSERVE RESULTS")
        elif self._last_evidence == "OBSERVED_NO_PROGRESS":
            status, reason, recommendation = "AMBER", "OBSERVED_NO_PROGRESS", "INSPECT PROGRESS"
        elif self._last_evidence == "BASELINE_OBSERVED":
            status, reason, recommendation = "UNKNOWN", "INSUFFICIENT_PROGRESS_EVIDENCE", "OBSERVE RESULTS"
        else:
            status, reason, recommendation = "GREEN", "OBSERVED_PROGRESS", "CONTINUE"
        if self._observation_error is not None and reason == "INSUFFICIENT_PROGRESS_EVIDENCE":
            recommendation = "CHECK PROGRESS VERIFIER"
        return WatchdogStatus(
            status, freshness, burn_rate, recommendation, reason,
            self._last_evidence, self.no_progress_steps, self.observed_steps,
            self.progress, self.state_fingerprint, self.elapsed_seconds, self.spent,
            self.spend_unit, self._mutation_pending)

    @staticmethod
    def calibrate(labeled_logs, window_size=15, min_steps=5,
                  objective="avoid_killing_winners", thresholds=None) -> float:
        """Retired unsafe action-only kill-line calibration.

        Success/failure labels and action diversity do not establish progress.
        A task-specific evidence-backed stopping policy must be chosen instead.
        """
        raise ValueError("ACTION_ONLY_CALIBRATION_UNSUPPORTED")
