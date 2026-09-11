"""Independent verifier for AGT-EXIT-COLD-001."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
RUN_PATH = HERE / "run.py"
PREREG_PATH = HERE / "prereg.json"
EXPECTED_WALLET = "24c92b6588410a5e3537bdccb2f0cefda387b2a5"
EXPECTED_AGT = "0533ceaf6c5b0975bfc71bff42f6ccd2d34c8adf"
ALLOWED = {
    "INCONCLUSIVE_AGT_ENVIRONMENT",
    "INCONCLUSIVE_OPENLINE_BASELINE",
    "AGT_MATCHES_PORTABLE_CURRENT_AUTHORITY",
    "AGT_CURRENT_STANDING_PORTABLE_TEMPORAL_ADMISSIBILITY_UNEARNED",
    "AGT_MODEL_EXIT_OFFLINE_RECEIPT_PASS_CURRENT_STANDING_REQUIRES_LIVE_STATUS_SOURCE",
    "INCONCLUSIVE_AGT_SEMANTICS",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_runner():
    spec = importlib.util.spec_from_file_location("_agt_exit_verify_runner", RUN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir")
    args = parser.parse_args(argv)
    artifact = Path(args.artifact_dir).resolve()
    result = _load_json(artifact / "result.json")
    sums = _load_json(artifact / "SHA256SUMS.json")
    prereg = _load_json(PREREG_PATH)

    if result.get("experiment_id") != "AGT-EXIT-COLD-001":
        raise AssertionError("experiment id mismatch")
    if result["pins"]["openline_wallet"] != EXPECTED_WALLET:
        raise AssertionError("Wallet pin mismatch")
    if result["pins"]["agt"] != EXPECTED_AGT:
        raise AssertionError("AGT pin mismatch")
    if prereg["pins"]["openline_wallet_main"] != EXPECTED_WALLET:
        raise AssertionError("preregistered Wallet pin mismatch")
    if prereg["pins"]["microsoft_agent_governance_toolkit_main"] != EXPECTED_AGT:
        raise AssertionError("preregistered AGT pin mismatch")
    if result["prereg_sha256"] != _sha(PREREG_PATH):
        raise AssertionError("prereg hash mismatch")
    if result.get("verdict") not in ALLOWED:
        raise AssertionError("unknown verdict")

    runner = _load_runner()
    runner.self_test()
    if runner._classify(result) != result["verdict"]:
        raise AssertionError("verdict does not reproduce from frozen classifier")

    for name, expected in sums.items():
        if name == "nested_files":
            for rel, nested_expected in expected.items():
                if _sha(artifact / rel) != nested_expected:
                    raise AssertionError(f"nested artifact hash mismatch: {rel}")
        else:
            if _sha(artifact / name) != expected:
                raise AssertionError(f"artifact hash mismatch: {name}")

    env = result["environment"]
    if result["verdict"] == "INCONCLUSIVE_AGT_ENVIRONMENT":
        if env["agt_pin_ok"] and env["agt_selected_upstream_tests_passed"]:
            raise AssertionError("environment-inconclusive without failed environment premise")
        print("AGT-EXIT-COLD-001 verified: INCONCLUSIVE_AGT_ENVIRONMENT")
        return 0

    if not env["agt_pin_ok"] or not env["agt_selected_upstream_tests_passed"]:
        raise AssertionError("semantic verdict produced without valid AGT environment")

    receipts = _load_json(artifact / "agt-receipts.json")
    trusted = result["agt"]["criterion_7a"]["independent_verifier"][
        "trusted_signer_public_key"
    ]
    receipt_check = runner.independent_verify_receipt_export(receipts, trusted)
    if receipt_check["valid"] != result["agt"]["criterion_7a"]["passed"]:
        raise AssertionError("7a external receipt verification mismatch")

    before = _load_json(artifact / "agt-revocations-before.json")
    after = _load_json(artifact / "agt-revocations-after.json")
    tampered = _load_json(artifact / "agt-revocations-tampered.json")
    if not isinstance(before, list) or not isinstance(after, list) or not isinstance(tampered, list):
        raise AssertionError("unexpected AGT revocation serialization")
    did = "did:mesh:portable-worker"
    if any(item.get("agent_did") == did for item in before):
        raise AssertionError("before snapshot already revoked")
    if not any(item.get("agent_did") == did for item in after):
        raise AssertionError("after snapshot missing revocation")
    if any(item.get("agent_did") == did for item in tampered):
        raise AssertionError("tampered negative control did not remove revocation")

    criterion_7b = result["agt"]["criterion_7b"]
    if not criterion_7b["portable_current_standing_passed"]:
        local = criterion_7b["local_file_backed_revocation"]
        if local["top_level_authenticated_freshness_envelope_present"]:
            raise AssertionError("7b negative classification contradicts local envelope observation")
        online = criterion_7b["online_external_jwks_control"]
        if online["control_plane_exit_result"] != "FAIL_CLOSED_STATUS_UNKNOWN":
            raise AssertionError("7b negative classification missing status-source exit condition")

    control = result["agt"]["online_revocation_control"]
    if control["status"] == "PASS":
        if not (
            control["valid_token_accepted"]
            and control["revoked_token_denied"]
            and control["status_source_failure_failed_closed"]
        ):
            raise AssertionError("online revocation control pass is internally inconsistent")

    if result["openline"]["status"] == "EVALUATED":
        ol = result["openline"]
        if ol["platform_exit_passed"]:
            if not ol["platform_exit_checks"]["stale_pre_exit_bundle_rejected"]:
                raise AssertionError("OpenLine stale-bundle premise missing")
            if not ol["platform_exit_checks"]["old_authority_stopped_on_platform_b"]:
                raise AssertionError("OpenLine revoked-authority premise missing")
        if ol["unsafe_timing_refused_protection"]:
            timing = ol["unsafe_timing"]
            if timing["admission_decision"] != "REFUSE_REVOCATION_PROTECTION":
                raise AssertionError("OpenLine timing classification mismatch")
            if timing["required_time_ms"] != 110 or timing["remaining_margin_ms"] != -10:
                raise AssertionError("OpenLine timing values mismatch")

    print(
        "AGT-EXIT-COLD-001 verified: "
        + result["verdict"]
        + f"; 7a={result['agt']['criterion_7a']['status']}"
        + f"; 7b={result['agt']['criterion_7b']['status']}"
        + f"; timing={result['agt']['unsafe_timing']['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
