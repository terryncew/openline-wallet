"""AGT-EXIT-COLD-001 external comparator.

Runs pinned Microsoft Agent Governance Toolkit source without modifying it.
This is proof-only code. It does not add an OpenLine production path.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
from unittest.mock import patch

EXPERIMENT_ID = "AGT-EXIT-COLD-001"
WALLET_PIN = "24c92b6588410a5e3537bdccb2f0cefda387b2a5"
AGT_PIN = "0533ceaf6c5b0975bfc71bff42f6ccd2d34c8adf"
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PREREG = HERE / "prereg.json"

RECEIPT_REL = Path(
    "agent-governance-python/agentmesh-integrations/"
    "mcp-receipt-governed/mcp_receipt_governed/receipt.py"
)
RECEIPT_ADAPTER_REL = Path(
    "agent-governance-python/agentmesh-integrations/"
    "mcp-receipt-governed/mcp_receipt_governed/adapter.py"
)
RECEIPT_TEST_REL = Path(
    "agent-governance-python/agentmesh-integrations/mcp-receipt-governed/tests"
)
REVOCATION_REL = Path(
    "agent-governance-python/agent-mesh/src/agentmesh/identity/revocation.py"
)
EXTERNAL_JWKS_REL = Path(
    "agent-governance-python/agent-mesh/src/agentmesh/identity/external_jwks.py"
)
CREDENTIALS_REL = Path(
    "agent-governance-python/agent-mesh/src/agentmesh/identity/credentials.py"
)

RECEIPT_SIGNING_KEY_HEX = "41" * 32
JWKS_SIGNING_KEY_HEX = "52" * 32
PARTNER_DOMAIN = "partner.example.com"
PARTNER_JWKS_URL = f"https://{PARTNER_DOMAIN}/.well-known/jwks.json"
PARTNER_REVOCATION_URL = f"https://{PARTNER_DOMAIN}/.well-known/jwks-revoked.json"
JWKS_KID = "agt-exit-cold-key-1"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _git_sha(repo: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _receipt_payload_from_export(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "agent_did": item["agent_did"],
        "args_hash": item["args_hash"],
        "cedar_decision": item["cedar_decision"],
        "cedar_policy_id": item["cedar_policy_id"],
        "receipt_id": item["receipt_id"],
        "timestamp": item["timestamp"],
        "tool_name": item["tool_name"],
    }
    if item.get("parent_receipt_hash") is not None:
        payload["parent_receipt_hash"] = item["parent_receipt_hash"]
    if item.get("session_id") is not None:
        payload["session_id"] = item["session_id"]
    return payload


def independent_verify_receipt_export(
    receipts: list[dict[str, Any]], expected_public_key_hex: str
) -> dict[str, Any]:
    """Verify AGT receipts without importing AGT code."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    errors: list[str] = []
    if not receipts:
        return {"valid": False, "errors": ["EMPTY_RECEIPT_SET"], "count": 0}

    public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(expected_public_key_hex))
    seen: set[str] = set()
    previous_hash: str | None = None
    for index, item in enumerate(receipts):
        rid = str(item.get("receipt_id", ""))
        if not rid or rid in seen:
            errors.append(f"[{index}] RECEIPT_ID_INVALID_OR_DUPLICATE")
        seen.add(rid)

        payload_bytes = _canonical_bytes(_receipt_payload_from_export(item))
        payload_hash = _sha256_bytes(payload_bytes)
        if item.get("payload_hash") != payload_hash:
            errors.append(f"[{index}] PAYLOAD_HASH_MISMATCH")

        parent = item.get("parent_receipt_hash")
        if index == 0 and parent is not None:
            errors.append(f"[{index}] FIRST_RECEIPT_HAS_PARENT")
        if index > 0 and parent != previous_hash:
            errors.append(f"[{index}] CHAIN_PARENT_MISMATCH")

        if item.get("signer_public_key") != expected_public_key_hex:
            errors.append(f"[{index}] SIGNER_KEY_MISMATCH")
        try:
            public_key.verify(bytes.fromhex(item["signature"]), payload_bytes)
        except Exception:
            errors.append(f"[{index}] SIGNATURE_INVALID")
        previous_hash = payload_hash

    return {
        "valid": not errors,
        "errors": errors,
        "count": len(receipts),
        "trusted_signer_public_key": expected_public_key_hex,
        "last_payload_hash": previous_hash,
    }


def _classify(result: dict[str, Any]) -> str:
    env = result["environment"]
    if not env["agt_pin_ok"] or not env["agt_selected_upstream_tests_passed"]:
        return "INCONCLUSIVE_AGT_ENVIRONMENT"

    ol = result["openline"]
    if not (
        ol["platform_exit_passed"]
        and ol["unsafe_timing_refused_protection"]
    ):
        return "INCONCLUSIVE_OPENLINE_BASELINE"

    agt = result["agt"]
    model = agt["model_exit"]["passed"]
    receipt = agt["criterion_7a"]["passed"]
    portable = agt["criterion_7b"]["portable_current_standing_passed"]
    timing = agt["unsafe_timing"]["comparable_temporal_admission_surface"]
    timing_refused = agt["unsafe_timing"]["unsafe_budget_refused_protection"]

    if model and receipt and portable and timing and timing_refused:
        return "AGT_MATCHES_PORTABLE_CURRENT_AUTHORITY"
    if model and receipt and portable and not timing:
        return "AGT_CURRENT_STANDING_PORTABLE_TEMPORAL_ADMISSIBILITY_UNEARNED"
    if model and receipt and not portable:
        control = agt["online_revocation_control"]
        if (
            control["valid_token_accepted"]
            and control["revoked_token_denied"]
            and control["status_source_failure_failed_closed"]
        ):
            return (
                "AGT_MODEL_EXIT_OFFLINE_RECEIPT_PASS_CURRENT_STANDING_"
                "REQUIRES_LIVE_STATUS_SOURCE"
            )
    return "INCONCLUSIVE_AGT_SEMANTICS"


def self_test() -> None:
    base = {
        "environment": {
            "agt_pin_ok": True,
            "agt_selected_upstream_tests_passed": True,
        },
        "openline": {
            "platform_exit_passed": True,
            "unsafe_timing_refused_protection": True,
        },
        "agt": {
            "model_exit": {"passed": True},
            "criterion_7a": {"passed": True},
            "criterion_7b": {"portable_current_standing_passed": False},
            "online_revocation_control": {
                "valid_token_accepted": True,
                "revoked_token_denied": True,
                "status_source_failure_failed_closed": True,
            },
            "unsafe_timing": {
                "comparable_temporal_admission_surface": False,
                "unsafe_budget_refused_protection": False,
            },
        },
    }
    assert _classify(base) == (
        "AGT_MODEL_EXIT_OFFLINE_RECEIPT_PASS_CURRENT_STANDING_"
        "REQUIRES_LIVE_STATUS_SOURCE"
    )

    matched = copy.deepcopy(base)
    matched["agt"]["criterion_7b"]["portable_current_standing_passed"] = True
    matched["agt"]["unsafe_timing"]["comparable_temporal_admission_surface"] = True
    matched["agt"]["unsafe_timing"]["unsafe_budget_refused_protection"] = True
    assert _classify(matched) == "AGT_MATCHES_PORTABLE_CURRENT_AUTHORITY"

    no_timing = copy.deepcopy(matched)
    no_timing["agt"]["unsafe_timing"]["comparable_temporal_admission_surface"] = False
    no_timing["agt"]["unsafe_timing"]["unsafe_budget_refused_protection"] = False
    assert _classify(no_timing) == (
        "AGT_CURRENT_STANDING_PORTABLE_TEMPORAL_ADMISSIBILITY_UNEARNED"
    )

    env = copy.deepcopy(base)
    env["environment"]["agt_selected_upstream_tests_passed"] = False
    assert _classify(env) == "INCONCLUSIVE_AGT_ENVIRONMENT"

    ol = copy.deepcopy(base)
    ol["openline"]["platform_exit_passed"] = False
    assert _classify(ol) == "INCONCLUSIVE_OPENLINE_BASELINE"


def _run_upstream_receipt_tests(agt_root: Path, output: Path) -> tuple[bool, int]:
    cmd = [sys.executable, "-m", "pytest", str(agt_root / RECEIPT_TEST_REL), "-q"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=agt_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
        text = (
            "$ " + " ".join(cmd) + "\n\nSTDOUT\n" + proc.stdout
            + "\nSTDERR\n" + proc.stderr
        )
        output.write_text(text, encoding="utf-8")
        return proc.returncode == 0, proc.returncode
    except subprocess.TimeoutExpired as exc:
        output.write_text(
            "$ " + " ".join(cmd) + "\n\nTIMEOUT\n" + str(exc), encoding="utf-8"
        )
        return False, 124
    except OSError as exc:
        output.write_text(
            "$ " + " ".join(cmd) + "\n\nOSERROR\n" + repr(exc), encoding="utf-8"
        )
        return False, 125


def _run_agt_model_exit(output: Path) -> dict[str, Any]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from mcp_receipt_governed import ReceiptStore
    from mcp_receipt_governed.adapter import McpReceiptAdapter

    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(RECEIPT_SIGNING_KEY_HEX))
    public_key_hex = key.public_key().public_bytes_raw().hex()

    policy = 'permit(principal, action == Action::"DeployStaging", resource);'
    store = ReceiptStore()
    args = {"target": "staging", "artifact": "same-job-v1"}
    calls: list[str] = []

    def provider_a_worker(**kwargs):
        calls.append("provider-a")
        return {"provider": "provider-a", "args": kwargs}

    def provider_b_worker(**kwargs):
        calls.append("provider-b")
        return {"provider": "provider-b", "args": kwargs}

    common = {
        "cedar_policy": policy,
        "cedar_policy_id": "agt-exit-cold-policy-v1",
        "signing_key_hex": RECEIPT_SIGNING_KEY_HEX,
        "store": store,
    }
    adapter_a = McpReceiptAdapter(session_id="provider-a", **common)
    adapter_b = McpReceiptAdapter(session_id="provider-b", **common)

    r_a, out_a = adapter_a.govern_and_execute(
        agent_did="did:mesh:portable-worker",
        tool_name="DeployStaging",
        tool_fn=provider_a_worker,
        tool_args=args,
    )
    r_b, out_b = adapter_b.govern_and_execute(
        agent_did="did:mesh:portable-worker",
        tool_name="DeployStaging",
        tool_fn=provider_b_worker,
        tool_args=args,
    )

    exported = store.export()
    _write_json(output / "agt-receipts.json", exported)
    independent = independent_verify_receipt_export(exported, public_key_hex)
    passed = (
        r_a.cedar_decision == "allow"
        and r_b.cedar_decision == "allow"
        and out_a is not None
        and out_b is not None
        and calls == ["provider-a", "provider-b"]
        and independent["valid"]
    )
    return {
        "passed": passed,
        "provider_a_decision": r_a.cedar_decision,
        "provider_b_decision": r_b.cedar_decision,
        "provider_calls": calls,
        "same_policy_id": r_a.cedar_policy_id == r_b.cedar_policy_id,
        "same_agent_did": r_a.agent_did == r_b.agent_did,
        "receipt_chain_external_verification": independent,
        "receipt_file": "agt-receipts.json",
    }


def _run_local_revocation_portability(agt_root: Path, output: Path) -> dict[str, Any]:
    module = _load_module(agt_root / REVOCATION_REL, "_agt_exit_revocation")
    live_path = output / "agt-revocations-live.json"
    before_path = output / "agt-revocations-before.json"
    after_path = output / "agt-revocations-after.json"
    tampered_path = output / "agt-revocations-tampered.json"

    did = "did:mesh:portable-worker"
    revocations = module.RevocationList(storage=str(live_path))
    revocations.save(str(live_path))
    shutil.copyfile(live_path, before_path)

    revocations.revoke(
        did,
        reason="CONTROL_PLANE_EXIT_TEST",
        revoked_by="did:mesh:owner",
    )
    shutil.copyfile(live_path, after_path)

    before = module.RevocationList(storage=str(before_path))
    after = module.RevocationList(storage=str(after_path))

    tampered_data = json.loads(after_path.read_text(encoding="utf-8"))
    tampered_data = [
        item for item in tampered_data if item.get("agent_did") != did
    ]
    _write_json(tampered_path, tampered_data)
    tampered = module.RevocationList(storage=str(tampered_path))

    raw_after = json.loads(after_path.read_text(encoding="utf-8"))
    root_is_list = isinstance(raw_after, list)
    top_level_authenticated_envelope = isinstance(raw_after, dict) and any(
        name in raw_after
        for name in (
            "signature",
            "signer_public_key",
            "payload_hash",
            "state_root",
            "head_hash",
            "sequence",
            "issued_at",
            "expires_at",
        )
    )
    return {
        "before_reports_revoked": before.is_revoked(did),
        "after_reports_revoked": after.is_revoked(did),
        "tampered_post_state_accepted_and_reports_not_revoked": not tampered.is_revoked(did),
        "serialized_root_type": "list" if root_is_list else type(raw_after).__name__,
        "top_level_authenticated_freshness_envelope_present": top_level_authenticated_envelope,
        "before_file": before_path.name,
        "after_file": after_path.name,
        "tampered_file": tampered_path.name,
        "portable_authenticated_current_standing_from_file": (
            after.is_revoked(did)
            and not before.is_revoked(did)
            and not tampered.is_revoked(did)
            and top_level_authenticated_envelope
        ),
        "interpretation": (
            "The file-backed RevocationList persists useful local state, but the ordinary "
            "serialized file is accepted after an entry is removed and supplies no list-level "
            "cryptographic freshness/authentication envelope."
        ),
    }


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_jwt(private_key, payload: dict[str, Any], kid: str) -> str:
    header = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    header_b64 = _b64url(_canonical_bytes(header))
    payload_b64 = _b64url(_canonical_bytes(payload))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input)
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"


def _run_online_external_jwks(agt_root: Path) -> dict[str, Any]:
    import httpx
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    module = _load_module(agt_root / EXTERNAL_JWKS_REL, "_agt_exit_external_jwks")
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(JWKS_SIGNING_KEY_HEX))
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": _b64url(public_bytes),
        "kid": JWKS_KID,
        "use": "sig",
        "alg": "EdDSA",
    }
    now = int(time.time())
    payload = {
        "iss": PARTNER_DOMAIN,
        "sub": f"did:web:{PARTNER_DOMAIN}:agents:portable-worker",
        "iat": now,
        "exp": now + 900,
        "delegation_claims": {
            "authority_scope": ["deploy:staging"],
            "revocation_check_url": PARTNER_REVOCATION_URL,
        },
    }
    token = _sign_jwt(private_key, payload, JWKS_KID)

    policy = module.FederationPolicy(
        trusted_endpoints=[
            module.TrustedEndpoint(
                domain=PARTNER_DOMAIN,
                jwks_url=PARTNER_JWKS_URL,
                trust_tier="verified_partner",
            )
        ],
        unknown_endpoint_policy="deny",
        revocation_cache_ttl_seconds=0,
    )

    async def evaluate(mode: str):
        async def fake_get(self, url, *args, **kwargs):
            url_s = str(url)
            request = httpx.Request("GET", url_s)
            if url_s.endswith("/jwks.json"):
                return httpx.Response(200, json={"keys": [jwk]}, request=request)
            if "jwks-revoked.json" in url_s:
                if mode == "offline":
                    raise httpx.ConnectError("status source offline", request=request)
                revoked = (
                    [{"kid": JWKS_KID, "ts": now}] if mode == "revoked" else []
                )
                return httpx.Response(200, json={"revoked": revoked}, request=request)
            return httpx.Response(404, request=request)

        provider = module.ExternalJWKSProvider(policy=policy)
        with patch.object(httpx.AsyncClient, "get", fake_get):
            return await provider.verify(token)

    valid_identity = asyncio.run(evaluate("valid"))
    revoked_identity = asyncio.run(evaluate("revoked"))
    offline_identity = asyncio.run(evaluate("offline"))
    return {
        "configuration": {
            "revocation_cache_ttl_seconds": 0,
            "trusted_endpoint": PARTNER_JWKS_URL,
            "revocation_endpoint": PARTNER_REVOCATION_URL,
        },
        "valid_token_accepted": valid_identity is not None,
        "revoked_token_denied": revoked_identity is None,
        "status_source_failure_failed_closed": offline_identity is None,
        "control_plane_exit_result": (
            "FAIL_CLOSED_STATUS_UNKNOWN" if offline_identity is None
            else "UNEXPECTED_ACCEPT_ON_STATUS_FAILURE"
        ),
        "interpretation": (
            "AGT has a real online revocation path. With the cache window removed for this "
            "comparison, it observes a revoked kid and fails closed if the revocation status "
            "source disappears. The latter is safe refusal, not portable evidence of current standing."
        ),
    }


def _agt_temporal_surface(agt_root: Path) -> dict[str, Any]:
    selected = {
        "receipt_adapter": agt_root / RECEIPT_ADAPTER_REL,
        "external_jwks": agt_root / EXTERNAL_JWKS_REL,
        "credentials": agt_root / CREDENTIALS_REL,
    }
    needles = (
        "consequence_horizon",
        "remaining_margin",
        "cancellation_cutoff",
        "revocation_protected",
        "revocation-protected",
        "admit_revocation",
    )
    hits: dict[str, list[str]] = {}
    for label, path in selected.items():
        text = path.read_text(encoding="utf-8")
        found = [needle for needle in needles if needle.lower() in text.lower()]
        if found:
            hits[label] = found

    credential_text = selected["credentials"].read_text(encoding="utf-8")
    m = re.search(r"REVOCATION_PROPAGATION_TARGET\s*=\s*(\d+)", credential_text)
    target = int(m.group(1)) if m else None

    comparable = bool(hits)
    return {
        "unsafe_policy_ms": {
            "detect_bound_ms": 10,
            "propagation_bound_ms": 60,
            "receiver_bound_ms": 20,
            "stop_bound_ms": 10,
            "uncertainty_bound_ms": 10,
            "required_time_ms": 110,
            "consequence_horizon_ms": 100,
            "remaining_margin_ms": -10,
        },
        "selected_source_files": {
            label: str(path.relative_to(agt_root)) for label, path in selected.items()
        },
        "timing_admission_search_terms": list(needles),
        "source_hits": hits,
        "credential_manager_revocation_propagation_target_seconds": target,
        "comparable_temporal_admission_surface": comparable,
        "unsafe_budget_refused_protection": False,
        "status": (
            "COMPARABLE_SURFACE_FOUND_REQUIRES_ADDITIONAL_PROBE"
            if comparable
            else "NO_COMPARABLE_PROTECTION_CLAIM_SURFACE"
        ),
        "interpretation": (
            "The selected ordinary AGT receipt/credential/JWKS paths expose revocation timing "
            "and cache controls but no discovered admission rule that labels work revocation-protected "
            "only when a composed bound beats a consequence horizon. Absence is recorded as an unearned "
            "comparator criterion, not as an AGT security defect."
        ),
    }


def _run_openline(output: Path) -> dict[str, Any]:
    from openline_wallet.demo import VERDICT, run_platform_exit

    platform_dir = output / "openline-platform-exit"
    platform_dir.mkdir(parents=True, exist_ok=True)
    platform_result = run_platform_exit(platform_dir)
    _write_json(output / "openline-platform-exit-summary.json", platform_result)

    authority_module = _load_module(
        REPO_ROOT / "proofs/authority-in-time-001/experiment.py",
        "_agt_exit_openline_authority_in_time",
    )
    policy = {
        "detect_bound_ms": 10,
        "propagation_bound_ms": 60,
        "receiver_bound_ms": 20,
        "stop_bound_ms": 10,
        "uncertainty_bound_ms": 10,
        "consequence_horizon_ms": 100,
        "bounds_supported": {
            "detect_bound_ms": True,
            "propagation_bound_ms": True,
            "receiver_bound_ms": True,
            "stop_bound_ms": True,
            "uncertainty_bound_ms": True,
        },
        "uncertainty_accounting": "QUEUEING_AND_JITTER_INCLUDED_ONCE",
    }
    timing = authority_module.evaluate_budget(policy)
    return {
        "platform_exit_passed": (
            platform_result["verdict"] == VERDICT
            and platform_result["checks"]["stale_pre_exit_bundle_rejected"]
            and platform_result["checks"]["old_authority_stopped_on_platform_b"]
            and platform_result["checks"]["successor_authority_allowed_on_platform_b"]
        ),
        "platform_exit_verdict": platform_result["verdict"],
        "platform_exit_checks": platform_result["checks"],
        "unsafe_timing": timing,
        "unsafe_timing_refused_protection": (
            timing["admission_decision"] == "REFUSE_REVOCATION_PROTECTION"
            and timing["required_time_ms"] == 110
            and timing["remaining_margin_ms"] == -10
        ),
        "claim_limit": (
            "This does not prove global hidden-revocation discovery. The receiver must receive "
            "a newer signed bundle; freshness and monotonic-head rules govern what it may then accept."
        ),
    }


def run(agt_root: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg["pins"]["openline_wallet_main"] != WALLET_PIN:
        raise AssertionError("preregistered Wallet pin mismatch")
    if prereg["pins"]["microsoft_agent_governance_toolkit_main"] != AGT_PIN:
        raise AssertionError("preregistered AGT pin mismatch")

    agt_root = agt_root.resolve()
    actual_agt_sha = _git_sha(agt_root)
    pin_ok = actual_agt_sha == AGT_PIN

    required_paths = [
        agt_root / RECEIPT_REL,
        agt_root / RECEIPT_ADAPTER_REL,
        agt_root / RECEIPT_TEST_REL,
        agt_root / REVOCATION_REL,
        agt_root / EXTERNAL_JWKS_REL,
        agt_root / CREDENTIALS_REL,
    ]
    paths_ok = all(path.exists() for path in required_paths)

    upstream_log = output / "agt-upstream-receipt-tests.txt"
    if pin_ok and paths_ok:
        upstream_tests_passed, upstream_rc = _run_upstream_receipt_tests(
            agt_root, upstream_log
        )
    else:
        upstream_tests_passed, upstream_rc = False, 126
        upstream_log.write_text(
            f"AGT pin/path preflight failed\nexpected={AGT_PIN}\nactual={actual_agt_sha}\n"
            + "\n".join(f"{p}: {p.exists()}" for p in required_paths)
            + "\n",
            encoding="utf-8",
        )

    result: dict[str, Any] = {
        "schema": "openline.agt-exit-cold-001.result.v1",
        "experiment_id": EXPERIMENT_ID,
        "pins": {
            "openline_wallet": WALLET_PIN,
            "agt": AGT_PIN,
            "agt_actual": actual_agt_sha,
        },
        "prereg_sha256": _sha256_file(PREREG),
        "environment": {
            "agt_pin_ok": pin_ok,
            "agt_required_paths_present": paths_ok,
            "agt_selected_upstream_tests_passed": upstream_tests_passed,
            "agt_selected_upstream_tests_returncode": upstream_rc,
            "agt_selected_upstream_tests_log": upstream_log.name,
            "agt_pinned_upstream_monorepo_ci_run": 34460169275,
            "agt_pinned_upstream_monorepo_ci_conclusion": "failure",
            "upstream_ci_scope_note": (
                "Pinned AGT main was globally red. This comparator does not count that as a "
                "semantic loss; selected comparator-path upstream receipt tests are rerun here."
            ),
        },
        "agt": {
            "model_exit": {"passed": False, "status": "NOT_RUN"},
            "criterion_7a": {"passed": False, "status": "NOT_RUN"},
            "criterion_7b": {
                "portable_current_standing_passed": False,
                "status": "NOT_RUN",
            },
            "online_revocation_control": {
                "valid_token_accepted": False,
                "revoked_token_denied": False,
                "status_source_failure_failed_closed": False,
                "status": "NOT_RUN",
            },
            "unsafe_timing": {
                "comparable_temporal_admission_surface": False,
                "unsafe_budget_refused_protection": False,
                "status": "NOT_RUN",
            },
        },
        "openline": {
            "platform_exit_passed": False,
            "unsafe_timing_refused_protection": False,
            "status": "NOT_RUN",
        },
        "claim_limits": prereg["claim_limits"],
    }

    if pin_ok and paths_ok and upstream_tests_passed:
        model = _run_agt_model_exit(output)
        result["agt"]["model_exit"] = {
            **model,
            "status": "PASS" if model["passed"] else "FAIL",
        }
        result["agt"]["criterion_7a"] = {
            "passed": model["receipt_chain_external_verification"]["valid"],
            "status": (
                "PASS_LOW_SIGNAL"
                if model["receipt_chain_external_verification"]["valid"]
                else "FAIL"
            ),
            "independent_verifier": model["receipt_chain_external_verification"],
        }

        local_revocation = _run_local_revocation_portability(agt_root, output)
        online_control = _run_online_external_jwks(agt_root)
        result["agt"]["online_revocation_control"] = {
            **online_control,
            "status": (
                "PASS"
                if (
                    online_control["valid_token_accepted"]
                    and online_control["revoked_token_denied"]
                    and online_control["status_source_failure_failed_closed"]
                )
                else "FAIL"
            ),
        }

        portable = (
            local_revocation["portable_authenticated_current_standing_from_file"]
            and online_control["control_plane_exit_result"]
            != "FAIL_CLOSED_STATUS_UNKNOWN"
        )
        result["agt"]["criterion_7b"] = {
            "portable_current_standing_passed": portable,
            "status": "PASS" if portable else "CURRENT_STANDING_REQUIRES_LIVE_STATUS_SOURCE",
            "local_file_backed_revocation": local_revocation,
            "online_external_jwks_control": {
                "valid_token_accepted": online_control["valid_token_accepted"],
                "revoked_token_denied": online_control["revoked_token_denied"],
                "status_source_failure_failed_closed": online_control[
                    "status_source_failure_failed_closed"
                ],
                "control_plane_exit_result": online_control["control_plane_exit_result"],
            },
            "interpretation": (
                "Historical signed receipts remain verifiable, and AGT's live JWKS path safely "
                "checks revocation while the status source exists. In the tested ordinary paths, "
                "control-plane/status-source exit leaves no portable authenticated current-standing "
                "artifact equivalent to a signed fresh authority-state head."
            ),
        }
        result["agt"]["unsafe_timing"] = _agt_temporal_surface(agt_root)
        result["openline"] = {**_run_openline(output), "status": "EVALUATED"}

    result["verdict"] = _classify(result)
    result["earned_claim"] = (
        "No claim until the frozen verifier validates the produced artifacts. "
        "The result is scoped to the pinned source revisions and tested ordinary paths."
    )
    _write_json(output / "result.json", result)

    manifest: dict[str, Any] = {
        path.name: _sha256_file(path)
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "SHA256SUMS.json"
    }
    nested: dict[str, str] = {}
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.parent != output:
            nested[str(path.relative_to(output))] = _sha256_file(path)
    if nested:
        manifest["nested_files"] = nested
    _write_json(output / "SHA256SUMS.json", manifest)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agt-root")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        print("AGT-EXIT-COLD-001 classifier self-test: PASS")
        return 0
    if not args.agt_root or not args.output:
        parser.error("--agt-root and --output are required unless --self-test is used")
    result = run(Path(args.agt_root), Path(args.output).resolve())
    print(json.dumps({
        "experiment_id": EXPERIMENT_ID,
        "verdict": result["verdict"],
        "agt_7a": result["agt"]["criterion_7a"]["status"],
        "agt_7b": result["agt"]["criterion_7b"]["status"],
        "agt_timing": result["agt"]["unsafe_timing"]["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
