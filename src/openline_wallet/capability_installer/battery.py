"""Buyer-owned acceptance battery for the capability installer preview.

Adapted from the buyer battery frozen in CAPABILITY-EXCHANGE-001 (read-only
source; that experiment is unchanged). The buyer (receiver-side) evaluates the
exact seller-delivered artifact; the seller never defines, alters, or grades
this battery.

T1 schema: output has the documented shape and value domains.
T2 determinism: two runs on identical input give identical output.
T3 accuracy: fault_hint and counts match the frozen expected labels on the
    12 fixtures; bar >= 10/12.
T4 no_regression: accuracy equals the frozen baseline accuracy (10/12).
T5 fixture_integrity: fixtures unchanged by the candidate under test.

The buyer's declared threshold is compared against the observed score; the
score is reported plainly and is not presented as universal correctness.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
BATTERY_FIXTURES = sorted(
    n for n in os.listdir(FIXTURES)
    if n.endswith(".json") and n.startswith("f")
)
ACCURACY_BAR = 10 / 12
BASELINE_ACCURACY = 10 / 12  # frozen no-regression bar from the exchange study
FAULT_HINTS = {"none", "unknown", "trust", "producer", "evaluator", "selector"}
CHECK_IDS = ("T1", "T2", "T3", "T4", "T5")


def battery_digest() -> str:
    """Pinned identity of the acceptance policy: battery code + fixtures."""
    h = hashlib.sha256()
    for name in BATTERY_FIXTURES:
        h.update(open(os.path.join(FIXTURES, name), "rb").read())
    h.update(open(__file__, "rb").read())
    return h.hexdigest()


def load_module_from_bytes(name: str, data: bytes):
    import tempfile

    fd, path = tempfile.mkstemp(prefix="capinstall_eval_", suffix=".py")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _check_schema(out) -> list:
    problems = []
    if not isinstance(out, dict):
        return ["not-a-dict"]
    for k in ("version", "lines", "counts", "fault_hint", "per_task"):
        if k not in out:
            problems.append("missing:" + k)
    c = out.get("counts", {})
    if not isinstance(c, dict) or not all(
        k in c for k in ("completed", "not_completed", "absent_files")
    ):
        problems.append("counts-shape")
    if out.get("fault_hint") not in FAULT_HINTS:
        problems.append("fault-hint-domain")
    return problems


def run_battery(artifact_bytes: bytes, checks=CHECK_IDS) -> dict:
    """Run the buyer battery on the exact artifact bytes. Returns verdict."""
    checks = tuple(c.upper() for c in checks)
    unknown = [c for c in checks if c not in CHECK_IDS]
    if unknown:
        raise ValueError("unknown checks: %s" % ",".join(unknown))
    names = BATTERY_FIXTURES
    pre_hashes = {
        n: hashlib.sha256(open(os.path.join(FIXTURES, n), "rb").read()).hexdigest()
        for n in names
    }
    mod = load_module_from_bytes("cap_candidate", artifact_bytes)
    if not hasattr(mod, "summarize"):
        return {
            "verdict": False,
            "tests": {"T1": {"pass": False, "detail": "no summarize() entry point"}},
            "accuracy": 0.0,
            "correct": 0,
        }
    fx0 = json.load(open(os.path.join(FIXTURES, names[0])))
    try:
        out_a = mod.summarize(fx0["probe_results"], fx0["absent_files"])
        out_b = mod.summarize(fx0["probe_results"], fx0["absent_files"])
    except Exception as e:  # noqa: BLE001 - any crash is a battery failure
        return {
            "verdict": False,
            "tests": {"T1": {"pass": False, "detail": "call failed: %r" % e}},
            "accuracy": 0.0,
            "correct": 0,
        }
    tests = {}
    if "T2" in checks:
        tests["T2"] = {
            "pass": out_a == out_b,
            "detail": "identical outputs" if out_a == out_b else "diverged outputs",
        }
    correct = 0
    schema_ok = True
    schema_notes = []
    for n in names:
        fx = json.load(open(os.path.join(FIXTURES, n)))
        out = mod.summarize(fx["probe_results"], fx["absent_files"])
        if "T1" in checks:
            probs = _check_schema(out)
            if probs:
                schema_ok = False
                schema_notes.append((n, probs))
        exp = fx["expected"]
        if out.get("fault_hint") == exp["fault_hint"] and out.get("counts") == exp["counts"]:
            correct += 1
    if "T1" in checks:
        tests["T1"] = {
            "pass": schema_ok,
            "detail": "all fixtures" if schema_ok else schema_notes,
        }
    accuracy = correct / len(names)
    if "T3" in checks:
        tests["T3"] = {
            "pass": accuracy >= ACCURACY_BAR,
            "detail": "%d/12 correct, bar >= 10/12" % correct,
        }
    if "T4" in checks:
        tests["T4"] = {
            "pass": accuracy == BASELINE_ACCURACY,
            "detail": "candidate %.3f vs frozen baseline %.3f" % (accuracy, BASELINE_ACCURACY),
        }
    if "T5" in checks:
        post_hashes = {
            n: hashlib.sha256(open(os.path.join(FIXTURES, n), "rb").read()).hexdigest()
            for n in names
        }
        tampered = [n for n in names if pre_hashes[n] != post_hashes[n]]
        tests["T5"] = {
            "pass": not tampered,
            "detail": "fixtures untouched" if not tampered else "modified: %s" % tampered,
        }
    verdict = all(t["pass"] for t in tests.values())
    return {
        "verdict": verdict,
        "tests": tests,
        "accuracy": accuracy,
        "correct": correct,
        "checks": list(checks),
    }
