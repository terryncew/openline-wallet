"""GITHUB-CREDENTIAL-OVERREACH-LIVE-001.

One valid-but-out-of-scope worker credential is stopped before any GitHub merge
request. A different, exactly authorized worker may then issue one disposable
GitHub merge through the existing receiver and must produce signed effect and
closure evidence.

The bootstrap API cannot call the merge endpoint. The receiver is the only
component with a code path to GitHub's merge mutation, and that mutation is
never retried.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import traceback
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.clock import utc_now
from openline_wallet.crypto import public_key_hex, record_hash, save_private_key, verify_record
from openline_wallet.effect_closure import EffectGate
from openline_wallet.github_effect import GitHubClient, GitHubMergeReceiver, MergeTarget, _snapshot
from openline_wallet.receiver import create_presentation
from openline_wallet.wallet import Wallet

EXPERIMENT = "GITHUB-CREDENTIAL-OVERREACH-LIVE-001"
PASS = "GITHUB_CREDENTIAL_OVERREACH_CONTAINMENT_ENFORCED"
FAIL = "GITHUB_CREDENTIAL_OVERREACH_CONTAINMENT_FAILED"
INCONCLUSIVE = "GITHUB_PROVIDER_OUTCOME_UNRESOLVED"
WALLET_PIN = "ebe6b2882095aba64611f5802ad5fdcf8e65961a"
SANDBOX = "terryncew/openline-provider-sandbox"
MARKER = "openline.wallet.disposable_provider_sandbox.v1"
CONFIRM = "RUN GITHUB CREDENTIAL OVERREACH"
A_SUBJECT = "worker-a"
A_MANDATE = "github-credential-overreach-a"
A_SCOPE = "approved-job:handoff"
B_SUBJECT = "worker-b"
B_MANDATE = "github-credential-overreach-b"
SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_LIVE = ROOT / "scripts" / "provider_live.py"
PREREG = ROOT / "PREREGISTRATION_GITHUB_CREDENTIAL_OVERREACH_LIVE_001.json"


class ExperimentError(Exception):
    def __init__(self, code: str, *, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(code)


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ExperimentError(code)


def write_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_provider_live():
    spec = importlib.util.spec_from_file_location("provider_effect_live_bootstrap", PROVIDER_LIVE)
    if spec is None or spec.loader is None:
        raise ExperimentError("PROVIDER_BOOTSTRAP_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CountingClient:
    """Count only actual GitHub merge mutations issued by the receiver."""
    def __init__(self, client: Any):
        self.client = client
        self.merge_requests = 0

    def __getattr__(self, name: str):
        return getattr(self.client, name)

    def merge(self, target: MergeTarget) -> dict[str, Any]:
        self.merge_requests += 1
        return self.client.merge(target)


def bootstrap(api: Any, run_id: str, provider_live: Any) -> dict[str, Any]:
    require(RUN_ID.fullmatch(run_id) is not None, "RUN_ID_INVALID")
    repo = provider_live.validate_sandbox(api)
    base_sha = api.ref("main")["object"]["sha"]
    require(isinstance(base_sha, str) and SHA.fullmatch(base_sha) is not None, "BASE_SHA_INVALID")

    base = f"olp-test-{run_id}-base"
    head = f"olp-test-{run_id}-head"
    api.create_ref(base, base_sha)
    api.create_ref(head, base_sha)
    commit = api.put_test_file(
        head,
        run_id,
        "OpenLine GitHub credential-overreach disposable test " + run_id + "\n",
    )
    head_sha = commit["commit"]["sha"]
    require(isinstance(head_sha, str) and SHA.fullmatch(head_sha) is not None, "TEST_COMMIT_INVALID")

    pr = api.request(
        "POST",
        "/pulls",
        {
            "title": "Disposable GitHub credential-overreach test " + run_id,
            "head": head,
            "base": base,
            "body": (
                "Harmless test marker. This PR exists only for "
                "GITHUB-CREDENTIAL-OVERREACH-LIVE-001."
            ),
            "draft": False,
            "maintainer_can_modify": False,
        },
    )
    require(
        type(pr.get("number")) is int
        and pr.get("base", {}).get("ref") == base
        and pr.get("head", {}).get("sha") == head_sha,
        "TEST_PR_RESPONSE_INVALID",
    )
    return {
        "repository": SANDBOX,
        "repository_id": repo["id"],
        "number": pr["number"],
        "base_ref": base,
        "base_sha": base_sha,
        "head_ref": head,
        "head_sha": head_sha,
        "run_id": run_id,
        "wallet_commit": WALLET_PIN,
        "bootstrap_observations": list(api.observations),
    }


def _grant(
    wallet: Wallet,
    holder: Ed25519PrivateKey,
    *,
    subject: str,
    mandate_id: str,
    scopes: list[str],
) -> dict[str, Any]:
    now = utc_now()
    wallet.grant(
        subject_id=subject,
        subject_public_key=public_key_hex(holder),
        scopes=scopes,
        expires_at=now + timedelta(hours=1),
        mandate_id=mandate_id,
        now=now,
    )
    return wallet.export_bundle()


def _receiver(
    wallet: Wallet,
    client: Any,
    target: MergeTarget,
    *,
    name: str,
    state: Path,
    bundle: dict[str, Any],
) -> tuple[GitHubMergeReceiver, Ed25519PrivateKey]:
    directory = state / name
    directory.mkdir(parents=True, exist_ok=True)
    gate_key = Ed25519PrivateKey.generate()
    holder = Ed25519PrivateKey.generate()
    save_private_key(directory / "gate.key", gate_key)
    save_private_key(directory / "subject.key", holder)
    gate = EffectGate("github-credential-overreach-" + name, gate_key=gate_key)
    gate.pin_principal(wallet.principal_id, wallet.root_public_key)
    gate.admit_bundle(bundle)
    return GitHubMergeReceiver(gate, client, target, directory / "journal.sqlite"), holder


def _presentation(
    receiver: GitHubMergeReceiver,
    wallet: Wallet,
    holder: Ed25519PrivateKey,
    bundle: dict[str, Any],
    *,
    subject: str,
    mandate_id: str,
) -> dict[str, Any]:
    now = utc_now()
    challenge = receiver.gate.issue_challenge(
        principal_id=wallet.principal_id,
        subject_id=subject,
        action=receiver.target.action,
        now=now,
    )
    return create_presentation(
        bundle=bundle,
        mandate_id=mandate_id,
        subject_id=subject,
        subject_key=holder,
        action=receiver.target.action,
        receiver_challenge=challenge,
        now=now,
    )


def _new_phase(
    wallet: Wallet,
    counted: CountingClient,
    target: MergeTarget,
    state: Path,
    *,
    name: str,
    subject: str,
    mandate_id: str,
    scopes: list[str],
) -> tuple[GitHubMergeReceiver, Ed25519PrivateKey, dict[str, Any]]:
    # Create the holder key first, then grant exactly the requested scopes.
    directory = state / name
    directory.mkdir(parents=True, exist_ok=True)
    gate_key = Ed25519PrivateKey.generate()
    holder = Ed25519PrivateKey.generate()
    save_private_key(directory / "gate.key", gate_key)
    save_private_key(directory / "subject.key", holder)
    bundle = _grant(wallet, holder, subject=subject, mandate_id=mandate_id, scopes=scopes)
    gate = EffectGate("github-credential-overreach-" + name, gate_key=gate_key)
    gate.pin_principal(wallet.principal_id, wallet.root_public_key)
    gate.admit_bundle(bundle)
    receiver = GitHubMergeReceiver(gate, counted, target, directory / "journal.sqlite")
    return receiver, holder, bundle


def _mandate_status(wallet: Wallet, mandate_id: str) -> str | None:
    for row in wallet.summary()["mandates"]:
        if row.get("mandate_id") == mandate_id:
            return row.get("status")
    return None


def run_boundary(
    client: Any,
    target: MergeTarget,
    state: Path,
    output: Path,
    *,
    transport: str,
) -> dict[str, Any]:
    state, output = Path(state).resolve(), Path(output).resolve()
    require(state != output and state not in output.parents and output not in state.parents,
            "PRIVATE_EVIDENCE_DIRECTORY_OVERLAP")
    require(not state.exists() and not output.exists(), "OUTPUT_ALREADY_EXISTS")
    state.mkdir(parents=True, mode=0o700)
    output.mkdir(parents=True, mode=0o700)

    wallet = Wallet.create(state / "wallet")
    counted = CountingClient(client)
    receivers: list[GitHubMergeReceiver] = []
    evidence: dict[str, Any] = {
        "schema": "openline.github-credential-overreach-live-001.evidence.v1",
        "experiment_id": EXPERIMENT,
        "wallet_commit": WALLET_PIN,
        "transport": transport,
        "target": target.to_record(),
        "verdict": FAIL,
    }

    # Phase A: a real, current credential deliberately asks for an action its
    # current mandate does not contain. Receiver.prepare must stop before any
    # provider preflight or merge mutation.
    a, holder_a, bundle_a = _new_phase(
        wallet, counted, target, state,
        name="worker-a", subject=A_SUBJECT, mandate_id=A_MANDATE, scopes=[A_SCOPE],
    )
    receivers.append(a)
    require(target.action not in [A_SCOPE], "CONTROL_SCOPE_ACCIDENTALLY_AUTHORIZED")
    control = a.prepare(
        _presentation(a, wallet, holder_a, bundle_a, subject=A_SUBJECT, mandate_id=A_MANDATE)
    )
    control_valid, control_reason = verify_record(
        control["receipt"], expected_public_key=control["receipt"]["gate_public_key"]
    )
    after_a = _snapshot(counted.pr(target), target)
    evidence["control"] = {
        "credential_valid_current": True,
        "mandate_active_before_request": _mandate_status(wallet, A_MANDATE) == "ACTIVE",
        "mandate_contains_merge_action": False,
        "decision": control.get("decision"),
        "reason_codes": control.get("reason_codes"),
        "effect_applied": control.get("effect_applied"),
        "receipt_valid": control_valid,
        "receipt_verification_reason": control_reason,
        "merge_requests_after_worker_a": counted.merge_requests,
        "provider_merged_after_worker_a": after_a["merged"],
    }
    write_json(output / "worker-a-stop-receipt.json", control["receipt"])
    write_json(output / "provider-after-worker-a.json", after_a)

    control_pass = (
        control.get("decision") == "STOPPED"
        and control.get("reason_codes") == ["ACTION_OUTSIDE_MANDATE"]
        and control.get("effect_applied") is False
        and control_valid is True
        and counted.merge_requests == 0
        and after_a["merged"] is False
    )
    if not control_pass:
        evidence["verdict"] = FAIL
        write_json(output / "evidence.json", evidence)
        for receiver in reversed(receivers):
            receiver.shutdown()
        return evidence

    wallet.add_receipt(control["receipt"])
    wallet.revoke(A_MANDATE)
    evidence["worker_a_revoked"] = _mandate_status(wallet, A_MANDATE) == "REVOKED"
    a.shutdown()
    receivers.remove(a)

    # Phase B: a different subject receives the exact target action and may
    # cross the provider mutation frontier once.
    b, holder_b, bundle_b = _new_phase(
        wallet, counted, target, state,
        name="worker-b", subject=B_SUBJECT, mandate_id=B_MANDATE, scopes=[target.action],
    )
    receivers.append(b)
    prepared = b.prepare(
        _presentation(b, wallet, holder_b, bundle_b, subject=B_SUBJECT, mandate_id=B_MANDATE)
    )
    evidence["successor"] = {
        "different_subject": A_SUBJECT != B_SUBJECT,
        "mandate_active_before_request": _mandate_status(wallet, B_MANDATE) == "ACTIVE",
        "mandate_exact_scope": [target.action],
        "prepare_decision": prepared.get("decision"),
    }
    require(prepared.get("decision") == "PREPARED", "SUCCESSOR_PREPARE_FAILED")

    effect_result: dict[str, Any] | None = None
    reconciliation: list[dict[str, Any]] | None = None
    mutation_error: str | None = None
    try:
        effect_result = b.finish(prepared["ticket"])
    except Exception as exc:
        mutation_error = getattr(exc, "code", type(exc).__name__)
        # Never retry finish. Reconciliation is provider-read-only.
        try:
            reconciliation = b.reconcile()
        except Exception as reconcile_exc:
            reconciliation = [{
                "status": "UNCERTAIN",
                "reason": getattr(reconcile_exc, "code", type(reconcile_exc).__name__),
            }]

    evidence["mutation"] = {
        "merge_requests": counted.merge_requests,
        "error": mutation_error,
        "reconciliation": reconciliation,
    }

    if effect_result is None:
        # We cannot attribute and sign a provider effect if finish did not return
        # the signed effect result. Preserve uncertainty and stop.
        if _mandate_status(wallet, B_MANDATE) == "ACTIVE":
            wallet.revoke(B_MANDATE)
        evidence["worker_b_revoked"] = _mandate_status(wallet, B_MANDATE) == "REVOKED"
        evidence["verdict"] = INCONCLUSIVE
        write_json(output / "final-wallet.olw", wallet.export_bundle())
        write_json(output / "evidence.json", evidence)
        for receiver in reversed(receivers):
            receiver.shutdown()
        return evidence

    write_json(output / "worker-b-admission-receipt.json", effect_result["admission_receipt"])
    write_json(output / "worker-b-frontier-receipt.json", effect_result["receipt"])
    write_json(output / "worker-b-effect-receipt.json", effect_result["effect_receipt"])

    wallet.add_receipt(effect_result["receipt"])
    wallet.revoke(B_MANDATE)
    revoked_b = wallet.export_bundle()
    closure = b.close(revoked_b, B_MANDATE)
    write_json(output / "worker-b-closure-receipt.json", closure)
    write_json(output / "final-wallet.olw", revoked_b)

    after_b = _snapshot(counted.pr(target), target)
    write_json(output / "provider-after-worker-b.json", after_b)

    effect = effect_result["effect_receipt"]
    effect_valid, effect_reason = verify_record(effect, expected_public_key=effect["gate_public_key"])
    frontier_valid, frontier_reason = verify_record(
        effect_result["receipt"], expected_public_key=effect["gate_public_key"]
    )
    admission_valid, admission_reason = verify_record(
        effect_result["admission_receipt"], expected_public_key=effect["gate_public_key"]
    )
    closure_valid, closure_reason = verify_record(closure, expected_public_key=effect["gate_public_key"])
    effect_hash = record_hash(effect)

    evidence["successor"].update({
        "decision": effect_result.get("decision"),
        "effect_applied": effect_result.get("effect_applied"),
        "admission_receipt_valid": admission_valid,
        "admission_receipt_reason": admission_reason,
        "frontier_receipt_valid": frontier_valid,
        "frontier_receipt_reason": frontier_reason,
        "effect_receipt_valid": effect_valid,
        "effect_receipt_reason": effect_reason,
        "effect_status": effect.get("status"),
        "merge_commit_sha": effect.get("merge_commit_sha"),
        "merge_commit": effect.get("merge_commit"),
    })
    evidence["closure"] = {
        "valid": closure_valid,
        "verification_reason": closure_reason,
        "status": closure.get("status"),
        "active_frontiers": closure.get("active_frontiers"),
        "confirmed_effect_hashes": closure.get("confirmed_effect_hashes"),
        "effect_hash": effect_hash,
    }
    evidence["worker_b_revoked"] = _mandate_status(wallet, B_MANDATE) == "REVOKED"
    evidence["provider_after_successor"] = after_b

    known = (
        evidence["worker_a_revoked"] is True
        and evidence["worker_b_revoked"] is True
        and counted.merge_requests == 1
        and effect_result.get("decision") == "ALLOWED"
        and effect_result.get("effect_applied") is True
        and admission_valid is True
        and frontier_valid is True
        and effect_valid is True
        and effect.get("status") == "MERGE_CONFIRMED"
        and effect.get("action") == target.action
        and effect.get("target") == target.to_record()
        and after_b["merged"] is True
        and after_b["head_sha"] == target.head_sha
        and after_b["merge_commit_sha"] == effect.get("merge_commit_sha")
        and isinstance(effect.get("merge_commit"), dict)
        and effect["merge_commit"].get("parents", [None, None])[1] == target.head_sha
        and effect["merge_commit"].get("parents", [None])[0] == target.base_sha
        and effect["merge_commit"].get("base_drift") is False
        and closure_valid is True
        and closure.get("status") == "EFFECT_CLOSED"
        and closure.get("active_frontiers") == 0
        and closure.get("mandate_id") == B_MANDATE
        and effect_hash in closure.get("confirmed_effect_hashes", [])
    )
    evidence["verdict"] = PASS if known else FAIL
    write_json(output / "evidence.json", evidence)

    for receiver in reversed(receivers):
        receiver.shutdown()
    return evidence


def add_independent_provider_observation(
    api: Any,
    plan: dict[str, Any],
    evidence: dict[str, Any],
    output: Path,
) -> None:
    if evidence.get("verdict") != PASS:
        return
    effect = json.loads((output / "worker-b-effect-receipt.json").read_text(encoding="utf-8"))
    current = api.pr(plan["number"])
    commit = api.commit(effect["merge_commit_sha"])
    parents = [row["sha"] for row in commit["parents"]]
    observation = {
        "pr_number": plan["number"],
        "merged": current.get("merged"),
        "merge_commit_sha": current.get("merge_commit_sha"),
        "head_sha": current.get("head", {}).get("sha"),
        "base_ref": current.get("base", {}).get("ref"),
        "repository_id": current.get("base", {}).get("repo", {}).get("id"),
        "commit_sha": commit.get("sha"),
        "parents": parents,
    }
    write_json(output / "independent-provider-read.json", observation)
    evidence["independent_provider_read"] = observation
    exact = (
        observation["merged"] is True
        and observation["merge_commit_sha"] == effect["merge_commit_sha"]
        and observation["head_sha"] == plan["head_sha"]
        and observation["base_ref"] == plan["base_ref"]
        and observation["repository_id"] == plan["repository_id"]
        and observation["commit_sha"] == effect["merge_commit_sha"]
        and parents == effect["merge_commit"]["parents"]
        and parents[0] == plan["base_sha"]
        and parents[1] == plan["head_sha"]
    )
    evidence["independent_provider_matches"] = exact
    if not exact:
        evidence["verdict"] = FAIL
    write_json(output / "evidence.json", evidence)


def write_result(output: Path, evidence: dict[str, Any], plan: dict[str, Any], *, mode: str) -> None:
    names = [
        p.name for p in sorted(output.iterdir())
        if p.is_file() and p.name not in {"result.json", "SHA256SUMS.txt"}
    ]
    report = {
        "schema": "openline.github-credential-overreach-live-001.result.v1",
        "experiment_id": EXPERIMENT,
        "verdict": evidence["verdict"],
        "mode": mode,
        "wallet_commit": WALLET_PIN,
        "target": {
            k: plan[k] for k in (
                "repository", "repository_id", "number", "base_ref", "base_sha", "head_ref", "head_sha"
            )
        },
        "preregistration_sha256": sha256(PREREG),
        "source_sha256": sha256(Path(__file__)),
        "files_sha256": {name: sha256(output / name) for name in names},
        "production_effect": False,
        "real_money": False,
        "claim_boundary": (
            "One disposable sandbox PR; one valid-but-out-of-scope worker presentation; "
            "zero merge requests before successor authorization; one exactly authorized "
            "successor merge; signed receiver effect and closure evidence."
        ),
    }
    write_json(output / "result.json", report)
    files = [p for p in sorted(output.iterdir()) if p.is_file()]
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(p)}  {p.name}\n" for p in files),
        encoding="ascii",
    )


def verify(output: Path, *, require_live: bool = False) -> None:
    output = Path(output)
    report = json.loads((output / "result.json").read_text(encoding="utf-8"))
    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    require(report["schema"] == "openline.github-credential-overreach-live-001.result.v1",
            "RESULT_SCHEMA_INVALID")
    require(report["experiment_id"] == EXPERIMENT and evidence["experiment_id"] == EXPERIMENT,
            "EXPERIMENT_ID_MISMATCH")
    require(report["verdict"] == evidence["verdict"], "VERDICT_MISMATCH")
    require(report["verdict"] in {PASS, FAIL, INCONCLUSIVE}, "VERDICT_INVALID")
    if require_live:
        require(report["mode"] == "live", "LIVE_RESULT_REQUIRED")
    require(report["wallet_commit"] == WALLET_PIN, "WALLET_PIN_MISMATCH")
    require(report["preregistration_sha256"] == sha256(PREREG), "PREREGISTRATION_HASH_MISMATCH")
    require(report["source_sha256"] == sha256(Path(__file__)), "SOURCE_HASH_MISMATCH")
    for name, expected in report["files_sha256"].items():
        require((output / name).is_file() and sha256(output / name) == expected,
                "EVIDENCE_HASH_MISMATCH")

    control = json.loads((output / "worker-a-stop-receipt.json").read_text(encoding="utf-8"))
    ok, _reason = verify_record(control, expected_public_key=control["gate_public_key"])
    require(ok is True, "CONTROL_RECEIPT_INVALID")
    require(control["decision"] == "STOPPED"
            and control["reason_codes"] == ["ACTION_OUTSIDE_MANDATE"],
            "CONTROL_DECISION_INVALID")
    require(evidence["control"]["merge_requests_after_worker_a"] == 0
            and evidence["control"]["provider_merged_after_worker_a"] is False,
            "CONTROL_REACHED_PROVIDER")

    if report["verdict"] == PASS:
        effect = json.loads((output / "worker-b-effect-receipt.json").read_text(encoding="utf-8"))
        closure = json.loads((output / "worker-b-closure-receipt.json").read_text(encoding="utf-8"))
        frontier = json.loads((output / "worker-b-frontier-receipt.json").read_text(encoding="utf-8"))
        admission = json.loads((output / "worker-b-admission-receipt.json").read_text(encoding="utf-8"))
        key = effect["gate_public_key"]
        for value in (effect, closure, frontier, admission):
            valid, _why = verify_record(value, expected_public_key=key)
            require(valid is True, "SUCCESSOR_SIGNED_EVIDENCE_INVALID")
        require(evidence["mutation"]["merge_requests"] == 1, "MERGE_REQUEST_CARDINALITY")
        require(effect["status"] == "MERGE_CONFIRMED"
                and effect["action"] == evidence["target"]["action"],
                "EFFECT_BINDING_INVALID")
        require(closure["status"] == "EFFECT_CLOSED"
                and closure["active_frontiers"] == 0
                and record_hash(effect) in closure["confirmed_effect_hashes"],
                "CLOSURE_BINDING_INVALID")
        require(evidence["worker_a_revoked"] is True and evidence["worker_b_revoked"] is True,
                "REVOCATION_INVALID")
        if require_live:
            require(evidence.get("independent_provider_matches") is True,
                    "INDEPENDENT_PROVIDER_READ_MISMATCH")
            require((output / "independent-provider-read.json").is_file(),
                    "INDEPENDENT_PROVIDER_READ_MISSING")

    # Public evidence must never contain key files.
    require(not any(p.suffix == ".key" for p in output.rglob("*") if p.is_file()),
            "PRIVATE_KEY_IN_PUBLIC_EVIDENCE")

    print(json.dumps({
        "verified": True,
        "experiment_id": EXPERIMENT,
        "verdict": report["verdict"],
        "mode": report["mode"],
    }, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id")
    group.add_argument("--verify", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--confirm")
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args(argv)

    if args.verify is not None:
        verify(args.verify, require_live=args.require_live)
        return 0

    require(args.output is not None and args.state is not None, "OUTPUT_AND_STATE_REQUIRED")
    require(args.confirm == CONFIRM, "EXPLICIT_CONFIRMATION_REQUIRED")
    require(args.run_id is not None and RUN_ID.fullmatch(args.run_id) is not None, "RUN_ID_INVALID")

    output, state = args.output.resolve(), args.state.resolve()
    require(output != state and output not in state.parents and state not in output.parents,
            "PATH_OVERLAP")
    require(not output.exists() and not state.exists(), "OUTPUT_ALREADY_EXISTS")
    output.mkdir(parents=True, mode=0o700)
    # run_boundary owns state creation; remove the public directory temporarily
    # only after bootstrap metadata is ready.
    output.rmdir()

    provider_live = load_provider_live()
    api = provider_live.GitHubAPI(os.environ.get("OPENLINE_GITHUB_TOKEN", ""))
    summary: dict[str, Any] = {
        "schema": "openline.github-credential-overreach-live-001.launch.v1",
        "experiment_id": EXPERIMENT,
        "status": "HARNESS_FAILURE",
        "wallet_commit": WALLET_PIN,
    }
    try:
        plan = bootstrap(api, args.run_id, provider_live)
        output.mkdir(parents=True, mode=0o700)
        write_json(output / "plan.json", plan)

        client = GitHubClient(api.token)
        target, safety = provider_live.wait_for_target(
            client, plan, output / "target-preflight.json"
        )
        write_json(output / "target.json", {
            "target": target.to_record(),
            "safety": safety,
            "effect_authority": "NONE",
        })
        # run_boundary requires a new output directory. Move bootstrap material
        # aside, run the proof, then restore it into the proof packet.
        pre = output.parent / (output.name + "-pre")
        output.rename(pre)
        evidence = run_boundary(client, target, state, output, transport="github")
        for p in sorted(pre.iterdir()):
            p.replace(output / p.name)
        pre.rmdir()

        add_independent_provider_observation(api, plan, evidence, output)
        write_result(output, evidence, plan, mode="live")
        verify(output, require_live=True)
        summary["status"] = "COMPLETE"
        summary["verdict"] = evidence["verdict"]
        return_code = 0
    except Exception as exc:
        # Never issue a second provider mutation here. If public evidence exists,
        # preserve the harness failure alongside it.
        code = getattr(exc, "code", None)
        summary["error_code"] = code if isinstance(code, str) else type(exc).__name__
        if isinstance(exc, ExperimentError):
            summary["error_details"] = exc.details
        else:
            frames = traceback.extract_tb(exc.__traceback__)
            if frames:
                frame = frames[-1]
                summary["error_location"] = {
                    "file": Path(frame.filename).name,
                    "function": frame.name,
                    "line": frame.lineno,
                }
        return_code = 2
    finally:
        output.mkdir(parents=True, exist_ok=True)
        summary["bootstrap_mutations"] = len(getattr(api, "mutations", []))
        write_json(output / "bootstrap-http.json", getattr(api, "observations", []))
        write_json(output / "launch-summary.json", summary)
        hashes = {
            str(p.relative_to(output)): sha256(p)
            for p in sorted(output.rglob("*"))
            if p.is_file() and p.name != "SHA256SUMS.txt"
        }
        (output / "SHA256SUMS.txt").write_text(
            "".join(f"{value}  {name}\n" for name, value in hashes.items()),
            encoding="ascii",
        )
    return return_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExperimentError as exc:
        print("STOPPED — " + exc.code, file=sys.stderr)
        raise SystemExit(2)
