from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("authority_in_time_experiment", HERE / "experiment.py")
exp = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = exp
SPEC.loader.exec_module(exp)


def policy(*, values=(10, 20, 10, 10, 10), horizon=100):
    names = exp.REQUIRED_BOUNDS
    return {
        **dict(zip(names, values)),
        "consequence_horizon_ms": horizon,
        "bounds_supported": {name: True for name in names},
        "uncertainty_accounting": "QUEUEING_AND_JITTER_INCLUDED_ONCE",
    }


class TimingBudgetTests(unittest.TestCase):
    def test_supported_budget_with_positive_margin_is_admitted(self):
        result = exp.evaluate_budget(policy())
        self.assertEqual(result["required_time_ms"], 60)
        self.assertEqual(result["remaining_margin_ms"], 40)
        self.assertEqual(result["admission_decision"], "ADMIT_REVOCATION_PROTECTED")

    def test_exceeded_budget_refuses(self):
        result = exp.evaluate_budget(policy(values=(20, 30, 20, 20, 20)))
        self.assertEqual(result["required_time_ms"], 110)
        self.assertEqual(result["remaining_margin_ms"], -10)
        self.assertEqual(result["admission_decision"], "REFUSE_REVOCATION_PROTECTION")

    def test_exact_equality_refuses(self):
        result = exp.evaluate_budget(policy(values=(20, 20, 20, 20, 20)))
        self.assertEqual(result["remaining_margin_ms"], 0)
        self.assertEqual(result["admission_decision"], "REFUSE_REVOCATION_PROTECTION")

    def test_missing_bound_refuses(self):
        p = policy()
        del p["propagation_bound_ms"]
        result = exp.evaluate_budget(p)
        self.assertEqual(result["admission_decision"], "REFUSE_REVOCATION_PROTECTION")
        self.assertIn("BOUND_MISSING:propagation_bound_ms", result["reason_codes"])

    def test_unsupported_bound_refuses(self):
        p = policy()
        p["bounds_supported"]["receiver_bound_ms"] = False
        result = exp.evaluate_budget(p)
        self.assertIn("BOUND_UNSUPPORTED:receiver_bound_ms", result["reason_codes"])

    def test_queueing_or_jitter_cannot_be_counted_as_extra_component(self):
        p = policy()
        p["queueing_bound_ms"] = 5
        result = exp.evaluate_budget(p)
        self.assertEqual(result["admission_decision"], "REFUSE_REVOCATION_PROTECTION")
        self.assertIn("UNRECOGNIZED_TIMING_COMPONENT:queueing_bound_ms", result["reason_codes"])

    def test_bound_violation_is_detected_even_if_total_path_still_beats_cutoff(self):
        times = {
            "revocation_issued_ms": 5,
            "revocation_detected_ms": 13,
            "propagation_complete_ms": 38,
            "receiver_observed_ms": 45,
            "stop_complete_ms": 53,
        }
        self.assertEqual(exp.violated_bounds(policy(), times), ["propagation_bound_ms"])


class SimulatedActionTests(unittest.TestCase):
    def test_protected_action_stopped_before_cutoff_has_no_effect(self):
        action = exp.SimulatedAction("fit", 100, 150)
        action.arm("ADMIT_REVOCATION_PROTECTED")
        self.assertTrue(action.apply_stop(45))
        self.assertEqual(action.observe_effects(150), [])
        self.assertFalse(action.effect_observed)
        self.assertTrue(action.has_closure_evidence())

    def test_refused_action_never_arms(self):
        action = exp.SimulatedAction("refused", 100, 150)
        action.arm("REFUSE_REVOCATION_PROTECTION")
        self.assertFalse(action.armed)
        self.assertEqual(action.observe_effects(150), [])

    def test_stop_at_exact_cutoff_is_too_late(self):
        action = exp.SimulatedAction("boundary", 100, 150)
        action.arm("ADMIT_REVOCATION_PROTECTED")
        self.assertFalse(action.apply_stop(100))
        self.assertEqual(len(action.observe_effects(150)), 1)
        self.assertTrue(action.effect_observed)

    def test_negative_control_bypass_can_show_late_authentic_revocation(self):
        action = exp.SimulatedAction("bypass", 100, 150)
        action.arm("REFUSE_REVOCATION_PROTECTION", test_only_bypass=True)
        self.assertTrue(action.test_only_bypass)
        self.assertFalse(action.apply_stop(130))
        self.assertEqual(len(action.observe_effects(150)), 1)

    def test_missing_effect_evidence_is_not_closure(self):
        action = exp.SimulatedAction("open", 100, 150)
        action.arm("ADMIT_REVOCATION_PROTECTED")
        action.apply_stop(45)
        self.assertFalse(action.has_closure_evidence())
        with self.assertRaisesRegex(ValueError, "EFFECT_EVIDENCE_TOO_EARLY"):
            action.observe_effects(149)


if __name__ == "__main__":
    unittest.main()
