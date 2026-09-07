"""Independent hard ceilings, input validation, and reconciliation invariants."""
from decimal import Decimal
import unittest

from agent_watchdog import AgentWatchdog, Attempt, ProgressEvidence
from test_watchdog import TrustedLedger, make_watchdog


class BudgetBoundaries(unittest.TestCase):
    def test_actual_spend_and_time_are_independent_of_progress(self):
        dog, ledger = make_watchdog(max_elapsed_seconds=100, max_spend="1.00")
        ledger.complete("first")
        dog.log_attempt(Attempt("test", result="accepted", outcome="success"))
        status = dog.audit(elapsed_seconds=10, spent="0.25")
        self.assertEqual(status.status, "GREEN")
        self.assertEqual(status.spent, Decimal("0.25"))
        self.assertEqual(status.burn_rate, Decimal("0.025"))
        ledger.complete("second")
        dog.log_attempt(Attempt("test", result="accepted", outcome="success"))
        status = dog.audit(elapsed_seconds=20, spent="1.00")
        self.assertEqual(status.reason, "HARD_SPEND_LIMIT")
        self.assertEqual(status.status, "RED")
        self.assertEqual(status.progress, 2)

    def test_time_limit_stops_without_any_actions_or_progress(self):
        dog = AgentWatchdog(max_elapsed_seconds=60)
        self.assertEqual(dog.audit(elapsed_seconds=59).status, "UNKNOWN")
        status = dog.audit(elapsed_seconds=60)
        self.assertEqual(status.reason, "HARD_TIME_LIMIT")
        self.assertEqual(status.recommendation, "STOP RUN")

    def test_spend_limit_stops_without_time_or_progress(self):
        dog = AgentWatchdog(max_spend="0.50")
        status = dog.audit(spent="0.50")
        self.assertEqual(status.reason, "HARD_SPEND_LIMIT")
        self.assertIsNone(status.burn_rate)
        self.assertIsNone(status.elapsed_seconds)

    def test_unknown_spend_is_not_zero_or_inferred_from_actions(self):
        dog = AgentWatchdog(max_spend="1")
        for _ in range(100):
            dog.log_action("pytest")
        status = dog.audit(elapsed_seconds=10)
        self.assertIsNone(status.spent)
        self.assertIsNone(status.burn_rate)
        self.assertEqual(status.reason, "INSUFFICIENT_PROGRESS_EVIDENCE")

    def test_usage_is_cumulative_not_additive(self):
        dog = AgentWatchdog()
        dog.record_usage(elapsed_seconds=4, spent="0.10")
        dog.record_usage(elapsed_seconds=4, spent="0.10")
        self.assertEqual(dog.audit().spent, Decimal("0.10"))
        self.assertEqual(dog.audit().elapsed_seconds, 4)
        self.assertEqual(dog.audit().burn_rate, Decimal("0.025"))

    def test_invalid_and_regressing_usage_is_rejected_atomically(self):
        dog = AgentWatchdog()
        dog.record_usage(elapsed_seconds=4, spent="0.10")
        for value in (-1, True, float("nan"), float("inf"), "5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dog.record_usage(elapsed_seconds=value)
        for value in (-1, True, float("nan"), float("inf"), "nan", "inf", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dog.record_usage(spent=value)
        with self.assertRaises(ValueError):
            dog.record_usage(elapsed_seconds=3)
        with self.assertRaises(ValueError):
            dog.record_usage(spent="0.09")
        with self.assertRaises(ValueError):
            dog.record_usage(elapsed_seconds=5, spent="0.09")
        self.assertEqual(dog.audit().spent, Decimal("0.10"))
        self.assertEqual(dog.audit().elapsed_seconds, 4)

    def test_zero_elapsed_does_not_invent_a_rate(self):
        dog = AgentWatchdog()
        status = dog.audit(elapsed_seconds=0, spent="0.01")
        self.assertIsNone(status.burn_rate)
        self.assertEqual(status.spent, Decimal("0.01"))

    def test_progress_cannot_clear_a_hard_limit(self):
        dog, ledger = make_watchdog(max_spend="1")
        dog.record_usage(spent="1")
        for index in range(3):
            ledger.complete(str(index))
            dog.log_attempt(Attempt("test", result="accepted"))
        self.assertEqual(dog.audit().reason, "HARD_SPEND_LIMIT")

    def test_burn_rate_is_not_one_minus_freshness(self):
        dog = AgentWatchdog()
        for _ in range(5):
            dog.log_action("pytest")
        status = dog.audit(elapsed_seconds=10, spent="2.00")
        self.assertEqual(status.freshness, 0.2)
        self.assertEqual(status.burn_rate, Decimal("0.20"))
        self.assertEqual(status.reason, "INSUFFICIENT_PROGRESS_EVIDENCE")

    def test_rolling_window_does_not_reset_global_stagnation(self):
        dog, ledger = make_watchdog(window_size=3)
        for index in range(5):
            dog.log_attempt(Attempt("command-" + str(index), result="same"))
        self.assertEqual(dog.audit().reason, "OBSERVED_STAGNATION")
        self.assertEqual(dog.audit().freshness, 1.0)
        self.assertEqual(dog.audit(use_window=False).no_progress_steps, 5)

    def test_large_valid_spend_keeps_decimal_precision(self):
        dog = AgentWatchdog(max_spend="1000000000000000000000000000000")
        status = dog.audit(elapsed_seconds=1, spent="999999999999999999999999999999")
        self.assertEqual(status.status, "UNKNOWN")
        self.assertEqual(status.burn_rate, Decimal("999999999999999999999999999999"))


class ReconciliationBoundaries(unittest.TestCase):
    def test_uncertain_external_mutation_enters_sticky_hold(self):
        calls = []
        def verifier(attempt):
            calls.append(attempt)
            return ProgressEvidence("task-1", "changed", 100)
        dog = AgentWatchdog(objective_id="task-1", verifier=verifier,
                            baseline=ProgressEvidence("task-1", "initial", 0))
        dog.log_attempt(Attempt("merge", result="timeout", external_mutation="uncertain"))
        status = dog.audit()
        self.assertEqual(status.status, "HOLD")
        self.assertEqual(status.reason, "EXTERNAL_MUTATION_UNCERTAIN")
        self.assertEqual(status.recommendation, "RECONCILE")
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(RuntimeError, "RECONCILIATION_REQUIRED"):
            dog.log_attempt(Attempt("merge", result="retry", external_mutation="confirmed"))
        self.assertEqual(dog.audit().progress, 0)

    def test_reconciliation_requires_independent_confirmation(self):
        dog, ledger = make_watchdog(
            reconciliation_verifier=lambda evidence: evidence == "receiver-verified")
        for _ in range(4):
            dog.log_attempt(Attempt("test", result="same", outcome="failure"))
        dog.log_attempt(Attempt("merge", external_mutation="uncertain"))
        self.assertFalse(dog.resolve_uncertainty("agent says done"))
        self.assertEqual(dog.audit().status, "HOLD")
        self.assertTrue(dog.resolve_uncertainty("receiver-verified"))
        self.assertEqual(dog.audit().no_progress_steps, 4)
        dog.log_attempt(Attempt("test", result="same", outcome="failure"))
        self.assertEqual(dog.audit().reason, "OBSERVED_STAGNATION")

    def test_missing_reconciliation_verifier_cannot_clear_hold(self):
        dog = AgentWatchdog()
        dog.log_attempt(Attempt("merge", external_mutation="uncertain"))
        with self.assertRaisesRegex(RuntimeError, "RECONCILIATION_VERIFIER_REQUIRED"):
            dog.resolve_uncertainty("done")
        self.assertEqual(dog.audit().status, "HOLD")

    def test_reconciliation_failure_keeps_hold(self):
        def fail(evidence):
            raise RuntimeError("observer offline")
        dog = AgentWatchdog(reconciliation_verifier=fail)
        dog.log_attempt(Attempt("merge", external_mutation="uncertain"))
        with self.assertRaisesRegex(RuntimeError, "observer offline"):
            dog.resolve_uncertainty("read")
        self.assertEqual(dog.audit().status, "HOLD")
        with self.assertRaisesRegex(RuntimeError, "NO_UNCERTAIN_MUTATION"):
            AgentWatchdog(reconciliation_verifier=lambda e: True).resolve_uncertainty("read")

    def test_hard_ceiling_remains_active_during_uncertainty(self):
        dog = AgentWatchdog(max_elapsed_seconds=10)
        dog.log_attempt(Attempt("merge", external_mutation="uncertain"))
        status = dog.audit(elapsed_seconds=10)
        self.assertEqual(status.status, "RED")
        self.assertEqual(status.reason, "HARD_TIME_LIMIT")
        self.assertEqual(status.recommendation, "STOP AND RECONCILE")
        self.assertTrue(status.mutation_pending)

    def test_resolution_cannot_clear_a_terminal_limit(self):
        dog = AgentWatchdog(max_spend="1", reconciliation_verifier=lambda e: True)
        dog.log_attempt(Attempt("merge", external_mutation="uncertain"))
        dog.audit(spent="1")
        self.assertTrue(dog.resolve_uncertainty("verified"))
        self.assertEqual(dog.audit().reason, "HARD_SPEND_LIMIT")

    def test_confirmed_external_result_still_requires_progress_evidence(self):
        dog, ledger = make_watchdog()
        dog.log_attempt(Attempt("merge", result="accepted", outcome="success",
                                external_mutation="confirmed"))
        self.assertEqual(dog.audit().no_progress_steps, 1)
        self.assertEqual(dog.audit().status, "AMBER")


if __name__ == "__main__":
    unittest.main()
