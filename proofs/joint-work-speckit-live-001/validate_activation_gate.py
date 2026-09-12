"""Stdlib-only activation validation for JOINT-WORK-SPECKIT-LIVE-001."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ORIGINAL = HERE / "LIVE_ARM.json"
RETRY = HERE / "LIVE_ARM_RETRY_001.json"
FAILURE = HERE / "LIVE_ACTIVATION_001_SETUP_FAILURE.json"
RETRY_PREREG = HERE / "LIVE_ACTIVATION_RETRY_001_PREREG.json"

EXPECTED_ORIGINAL = {
    "activation": "AUTHORIZED_ONCE",
    "base_merge_commit": "d6cf339495790fe1f3a1822921c0819694cd773e",
    "experiment_id": "JOINT-WORK-SPECKIT-LIVE-001",
    "provider_call_caps": {"anthropic": 1, "openai": 2},
    "repair_calls": 0,
}

EXPECTED_FAILURE_SHA256 = "d683fccae339155e5109ba476b3a658625ff170dfdcd31f4ce7547c3cc365bb1"
EXPECTED_RETRY_PREREG_SHA256 = "9e9f649d9a02122a819598d33d2773043c3c4606cfee44038a6b3929ae5e65ca"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    if not path.is_file():
        raise RuntimeError(f"MISSING:{path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_original() -> None:
    if load(ORIGINAL) != EXPECTED_ORIGINAL:
        raise RuntimeError("ORIGINAL_LIVE_ACTIVATION_MARKER_INVALID")
    print("ORIGINAL_LIVE_ACTIVATION_VALID")


def validate_retry() -> None:
    if sha256(FAILURE) != EXPECTED_FAILURE_SHA256:
        raise RuntimeError("ACTIVATION_SETUP_FAILURE_RECORD_CHANGED")
    if sha256(RETRY_PREREG) != EXPECTED_RETRY_PREREG_SHA256:
        raise RuntimeError("ACTIVATION_RETRY_PREREG_CHANGED")

    failure = load(FAILURE)
    if failure.get("classification") != "INCONCLUSIVE_PROVIDER_SETUP":
        raise RuntimeError("PRIOR_CLASSIFICATION_INVALID")
    if failure.get("provider_calls_consumed") != 0:
        raise RuntimeError("PRIOR_PROVIDER_CALLS_NOT_ZERO")
    if failure.get("live_job_conclusion") != "skipped":
        raise RuntimeError("PRIOR_LIVE_JOB_NOT_SKIPPED")

    expected_retry = {
        "activation": "AUTHORIZED_ONCE_AFTER_SETUP_REPAIR",
        "experiment_id": "JOINT-WORK-SPECKIT-LIVE-001",
        "prior_activation_commit": "74c4589d2a2477dbac820340dc1e56a7718b78c2",
        "prior_failure_run_id": 34674406014,
        "prior_failure_job_id": 103501560630,
        "prior_provider_calls": 0,
        "provider_call_caps": {"anthropic": 1, "openai": 2},
        "repair_calls": 0,
        "failure_record_sha256": EXPECTED_FAILURE_SHA256,
        "retry_prereg_sha256": EXPECTED_RETRY_PREREG_SHA256,
    }
    if load(RETRY) != expected_retry:
        raise RuntimeError("RETRY_LIVE_ACTIVATION_MARKER_INVALID")
    print("RETRY_LIVE_ACTIVATION_VALID")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"original", "retry"}:
        raise SystemExit("usage: validate_activation_gate.py original|retry")
    if sys.argv[1] == "original":
        validate_original()
    else:
        validate_retry()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
