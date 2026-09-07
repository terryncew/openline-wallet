"""The paired falsifier and the original action-only false kill."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

from agent_watchdog import AgentWatchdog, Attempt, ProgressEvidence

ROOT = Path(__file__).resolve().parents[1]
BASE = "eb313155eaa5f08bdcc1cdc371f2487c3fa67876"


class TrustedLedger:
    """Test-owned objective state. Agent text cannot change the measurement."""
    def __init__(self, objective="task-1"):
        self.objective = objective
        self.completed = set()
        self.state = "initial"
        self.fail = False

    def __call__(self, attempt):
        if self.fail:
            raise RuntimeError("observer unavailable")
        if attempt.result is None:
            return None
        return ProgressEvidence(self.objective, self.state, len(self.completed))

    def complete(self, work_id):
        self.completed.add(work_id)
        self.state = "verified:" + ",".join(sorted(self.completed))


def make_watchdog(**kwargs):
    ledger = TrustedLedger()
    dog = AgentWatchdog(
        objective_id=ledger.objective, verifier=ledger,
        baseline=ProgressEvidence(ledger.objective, "initial", 0), **kwargs)
    return dog, ledger


class PairedFalsifier(unittest.TestCase):
    def test_original_source_reproduces_the_false_kill(self):
        source = ROOT / "proofs/watchdog-001/legacy-core.py"
        data = source.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        self.assertEqual(blob, "8bf6e2bc740ef8939f80b1ac1d25df2363b0b975")
        spec = importlib.util.spec_from_file_location("watchdog_legacy", source)
        legacy = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = legacy
        spec.loader.exec_module(legacy)
        dog = legacy.AgentWatchdog()
        for name in "abcde":
            dog.log_action("pytest test_" + name + ".py")
        status = dog.audit()
        self.assertEqual((status.status, status.freshness, status.recommendation),
                         ("RED", 0.2, "KILL RUN"))

    def test_action_only_is_insufficient_even_after_repetition(self):
        dog = AgentWatchdog()
        for name in "abcde":
            dog.log_action("pytest test_" + name + ".py")
        status = dog.audit()
        self.assertEqual(status.status, "UNKNOWN")
        self.assertEqual(status.reason, "INSUFFICIENT_PROGRESS_EVIDENCE")
        self.assertNotIn("KILL", status.recommendation)
        self.assertIsNone(status.burn_rate)
        self.assertEqual(status.freshness, 1.0)
        self.assertEqual(dog._default_normalizer("pytest Test_A.py"),
                         "pytest Test_A.py")

    def test_productive_test_sweep_survives(self):
        dog, ledger = make_watchdog()
        for name in "abcde":
            # The trusted evaluator sees each independently accepted work unit.
            ledger.complete("test_" + name)
            dog.log_attempt(Attempt("pytest test_" + name + ".py",
                                    result={"claimed_progress": 999}, outcome="success"))
            self.assertEqual(dog.audit().status, "GREEN")
        self.assertEqual(dog.audit().progress, 5)
        self.assertEqual(dog.audit().no_progress_steps, 0)
        self.assertEqual(dog.audit().observed_steps, 5)

    def test_identical_actions_can_make_real_progress(self):
        dog, ledger = make_watchdog()
        for index in range(8):
            ledger.complete("batch-" + str(index))
            dog.log_attempt(Attempt("pytest", result={"receipt": index}, outcome="success"))
            self.assertEqual(dog.audit().status, "GREEN")
        self.assertEqual(dog.audit().freshness, 0.125)

    def test_stalled_attempts_cannot_refresh_through_superficial_changes(self):
        dog, ledger = make_watchdog()
        actions = ["pytest test_a.py", "pytest test_b.py", "git status",
                   "read_file new_name.py", "try another hypothesis"]
        for index, action in enumerate(actions):
            ledger.state = "changed-wording-" + str(index)
            dog.log_attempt(Attempt(action, result={"new_hypothesis": index,
                               "claimed_progress": 1000 + index}, outcome="failure"))
            status = dog.audit()
            self.assertEqual(status.no_progress_steps, index + 1)
            if index < 4:
                self.assertNotEqual(status.status, "RED")
        self.assertEqual(status.status, "RED")
        self.assertEqual(status.reason, "OBSERVED_STAGNATION")
        self.assertEqual(status.recommendation, "STOP RUN")
        self.assertEqual(status.freshness, 1.0)

    def test_unknown_results_cannot_reset_a_stalled_allowance(self):
        dog, ledger = make_watchdog()
        for index in range(4):
            dog.log_attempt(Attempt("test", result={"n": index}, outcome="failure"))
        self.assertEqual(dog.audit().no_progress_steps, 4)
        for index in range(12):
            dog.log_action("new hypothesis " + str(index))
            self.assertEqual(dog.audit().status, "UNKNOWN")
            self.assertEqual(dog.audit().no_progress_steps, 4)
        dog.log_attempt(Attempt("test", result={"n": 5}, outcome="failure"))
        self.assertEqual(dog.audit().reason, "OBSERVED_STAGNATION")

    def test_only_verified_advance_resets_the_allowance(self):
        dog, ledger = make_watchdog()
        for _ in range(4):
            dog.log_attempt(Attempt("test", result="unchanged", outcome="failure"))
        ledger.complete("verified-fix")
        dog.log_attempt(Attempt("test", result="accepted", outcome="success"))
        self.assertEqual(dog.audit().no_progress_steps, 0)
        self.assertEqual(dog.audit().status, "GREEN")
        ledger.state = "different output"
        dog.log_attempt(Attempt("new action", result="different", outcome="success"))
        self.assertEqual(dog.audit().no_progress_steps, 1)

    def test_a_terminal_stall_is_not_cleared_by_later_claims(self):
        dog, ledger = make_watchdog()
        for _ in range(5):
            dog.log_attempt(Attempt("test", result="same", outcome="failure"))
        ledger.complete("late improvement")
        dog.log_attempt(Attempt("new hypothesis", result="accepted", outcome="success"))
        self.assertEqual(dog.audit().reason, "OBSERVED_STAGNATION")
        self.assertEqual(dog.audit().no_progress_steps, 5)


class ObservationContract(unittest.TestCase):
    def test_first_observation_establishes_a_baseline(self):
        ledger = TrustedLedger()
        dog = AgentWatchdog(objective_id=ledger.objective, verifier=ledger)
        ledger.complete("first")
        dog.log_attempt(Attempt("test", result="accepted", outcome="success"))
        self.assertEqual(dog.audit().status, "UNKNOWN")
        self.assertEqual(dog.audit().progress, 1)
        ledger.complete("second")
        dog.log_attempt(Attempt("test", result="accepted", outcome="success"))
        self.assertEqual(dog.audit().status, "GREEN")

    def test_success_is_not_automatically_progress(self):
        dog, _ = make_watchdog()
        for _ in range(5):
            dog.log_attempt(Attempt("pytest", result="same", outcome="success"))
        self.assertEqual(dog.audit().reason, "OBSERVED_STAGNATION")

    def test_state_change_and_metric_regression_are_not_progress(self):
        dog, ledger = make_watchdog()
        ledger.complete("one")
        ledger.complete("two")
        dog.log_attempt(Attempt("test", result="accepted"))
        ledger.completed.clear()
        for i in range(5):
            ledger.state = "state-" + str(i)
            dog.log_attempt(Attempt("test", result="changed"))
        self.assertEqual(dog.audit().reason, "OBSERVED_STAGNATION")
        self.assertEqual(dog.audit().progress, 2)

    def test_missing_verifier_or_result_is_unknown(self):
        dog = AgentWatchdog()
        dog.log_attempt(Attempt("test", result="success", outcome="success"))
        self.assertEqual(dog.audit().reason, "INSUFFICIENT_PROGRESS_EVIDENCE")
        dog, ledger = make_watchdog()
        dog.log_attempt(Attempt("test", result=None, outcome="success"))
        self.assertEqual(dog.audit().status, "UNKNOWN")
        self.assertEqual(dog.audit().observed_steps, 0)

    def test_verifier_error_or_invalid_return_is_not_progress(self):
        dog, ledger = make_watchdog()
        ledger.fail = True
        dog.log_attempt(Attempt("test", result="observed"))
        self.assertEqual(dog.audit().recommendation, "CHECK PROGRESS VERIFIER")
        self.assertEqual(dog.audit().no_progress_steps, 0)
        bad = AgentWatchdog(objective_id="task", verifier=lambda a: {"progress": 900})
        bad.log_attempt(Attempt("test", result="claimed"))
        self.assertEqual(bad.audit().status, "UNKNOWN")
        wrong = AgentWatchdog(objective_id="task", verifier=lambda a:
                              ProgressEvidence("other", "state", 100))
        wrong.log_attempt(Attempt("test", result="claimed"))
        self.assertEqual(wrong.audit().status, "UNKNOWN")

    def test_invalid_observations_are_rejected(self):
        for value in (True, -1, 1.5, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ProgressEvidence("task", "state", value)
        with self.assertRaises(ValueError):
            ProgressEvidence("", "state", 0)
        with self.assertRaises(ValueError):
            Attempt("test", outcome="made-progress")
        with self.assertRaises(ValueError):
            Attempt("test", external_mutation="retry")
        with self.assertRaises(TypeError):
            AgentWatchdog().log_attempt("test")

    def test_invalid_configuration_is_rejected(self):
        for kwargs in ({"min_steps": 0}, {"window_size": 0},
                       {"max_no_progress_steps": 0}, {"kill_threshold": 0},
                       {"kill_threshold": 1.1}, {"max_spend": 0},
                       {"max_elapsed_seconds": -1}, {"max_spend": float("nan")},
                       {"max_spend": float("inf")}, {"max_spend": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AgentWatchdog(**kwargs)
        with self.assertRaises(ValueError):
            AgentWatchdog(verifier=lambda a: None)
        with self.assertRaises(ValueError):
            AgentWatchdog(objective_id="task",
                          baseline=ProgressEvidence("other", "s", 0))

    def test_custom_normalizer_is_diagnostic_only(self):
        dog = AgentWatchdog(normalizer=lambda action: str(action).split()[0])
        for name in "abcde":
            dog.log_action("pytest test_" + name + ".py")
        self.assertEqual(dog.audit().freshness, 0.2)
        self.assertEqual(dog.audit().status, "UNKNOWN")
        self.assertEqual(dog.min_window_freshness(), 0.2)

    def test_legacy_calibration_cannot_generate_a_kill_line(self):
        with self.assertRaisesRegex(ValueError, "ACTION_ONLY_CALIBRATION_UNSUPPORTED"):
            AgentWatchdog.calibrate([{"actions": ["ls"] * 5, "success": False}])
