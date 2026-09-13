"""GITHUB-ACK-CLOSURE-LIVE-002.

A real disposable GitHub merge is acknowledged once. Immediate settlement is
then deliberately interrupted after the acknowledgement has been durably
journaled. A fresh receiver instance must close the exact acknowledged effect
using read-only reconciliation and without a second mutation.
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
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.clock import utc_now
from openline_wallet.crypto import (
    public_key_hex,
    record_hash,
    save_private_key,
    verify_record,
)
from openline_wallet.effect_closure import EffectGate
from openline_wallet.errors import WalletError
from openline_wallet.github_ack_reconcile import settle_acknowledged_merges
from openline_wallet.github_effect import (
    GitHubClient,
    GitHubMergeReceiver,
    MergeTarget,
    _snapshot,
)
from openline_wallet.receiver import create_presentation
from openline_wallet.wallet import Wallet

EXPERIMENT = "GITHUB-ACK-CLOSURE-LIVE-002"
PASS = "GITHUB_ACKNOWLEDGED_EFFECT_CLOSURE_ENFORCED"
FAIL = "GITHUB_ACKNOWLEDGED_EFFECT_CLOSURE_FAILED"
INCONCLUSIVE = "GITHUB_ACKNOWLEDGED_EFFECT_CLOSURE_UNRESOLVED"
WALLET_PIN = "323c11f55c9c6bcc6d618ce4c0f3f19cbbc627bd"
SANDBOX = "terryncew/openline-provider-sandbox"
CONFIRM = "RUN GITHUB ACK CLOSURE"
SUBJECT = "worker-ack-closure"
MANDATE = "github-ack-closure-live-002"
SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_LIVE = ROOT / "scripts" / "provider_live.py"
PREREG = ROOT / "PREREGISTRATION_GITHUB_ACK_CLOSURE_LIVE_002.json"


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
    """Count actual GitHub merge mutations; all other calls are reads."""
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
        "OpenLine GitHub acknowledged-effect closure test " + run_id + "\n",
    )
    head_sha = commit["commit"]["sha"]
    require(isinstance(head_sha, str) and SHA.fullmatch(head_sha) is not None, "TEST_COMMIT_INVALID")

    pr = api.request(
        "POST",
        "/pulls",
        {
            "title": "Disposable GitHub acknowledged-effect closure test " + run_id,
            "head": head,
            "base": base,
            "body": "Harmless disposable PR for GITHUB-ACK-CLOSURE-LIVE-002.",
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


def _presentation(
    receiver: GitHubMergeReceiver,
    wallet: Wallet,
    holder: Ed25519PrivateKey,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    now = utc_now()
    challenge = receiver.gate.issue_challenge(
        principal_id=wallet.principal_id,
        subject_id=SUBJECT,
        action=receiver.target.action,
        now=now,
    )
    return create_presentation(
        bundle=bundle,
        mandate_id=MANDATE,
        subject_id=SUBJECT,
        subject_key=holder,
        action=receiver.target.action,
        receiver_challenge=challenge,
        now=now,
    )


def _new_gate(
    wallet: Wallet,
    bundle: dict[str, Any],
    gate_key: Ed25519PrivateKey,
) -> EffectGate:
    gate = EffectGate("github-ack-closure-live-002", gate_key=gate_key)
    gate.pin_principal(wallet.principal_id, wallet.root_public_key)
    gate.admit_bundle(bundle)
    return gate


def run_boundary(
    client: Any,
    target: MergeTarget,
    state: Path,
    output: Path,
    *,
    transport: str,
) -> dict[str, Any]:
    state, output = Path(state).resolve(), Path(output).resolve()
    require(
        state != output and state not in output.parents and output not in state.parents,
        "PRIVATE_EVIDENCE_DIRECTORY_OVERLAP",
    )
    require(not state.exists() and not output.exists(), "OUTPUT_ALREADY_EXISTS")
    state.mkdir(parents=True, mode=0o700)
    output.mkdir(parents=True, mode=0o700)

    wallet = Wallet.create(state / "wallet")
    holder = Ed25519PrivateKey.generate()
    gate_key = Ed25519PrivateKey.generate()
    save_private_key(state / "subject.key", holder)
    save_private_key(state / "gate.key", gate_key)

    now = utc_now()
    wallet.grant(
        subject_id=SUBJECT,
        subject_public_key=public_key_hex(holder),
        scopes=[target.action],
        expires_at=now + timedelta(hours=1),
        mandate_id=MANDATE,
        now=now,
    )
    bundle = wallet.export_bundle()

    counted = CountingClient(client)
    gate = _new_gate(wallet, bundle, gate_key)
    journal = state / "receiver.sqlite"
    receiver = GitHubMergeReceiver(gate, counted, target, journal)

    evidence: dict[str, Any] = {
        "schema": "openline.github-ack-closure-live-002.evidence.v1",
        "experiment_id": EXPERIMENT,
        "wallet_commit": WALLET_PIN,
        "transport": transport,
        "target": target.to_record(),
        "verdict": FAIL,
    }

    prepared = receiver.prepare(_presentation(receiver, wallet, holder, bundle))
    require(prepared.get("decision") == "PREPARED", "PREPARE_FAILED")

    # The interruption is deliberately placed after GitHub's successful merge
    # response has been durably journaled. It suppresses only immediate read
    # settlement. It does not alter or retry the provider mutation.
    forced_error = None
    with patch(
        "openline_wallet.github_effect._settled_merge",
        side_effect=WalletError("GITHUB_MERGE_NOT_RECONCILED"),
    ):
        try:
            receiver.finish(prepared["ticket"])
        except WalletError as exc:
            forced_error = exc.code

    require(forced_error == "GITHUB_MERGE_NOT_RECONCILED", "FORCED_INTERRUPTION_NOT_OBSERVED")
    records = receiver._records()
    require(len(records) == 1, "JOURNAL_CARDINALITY_INVALID")
    interrupted = records[0]
    response = interrupted.get("response")
    ack_sha = response.get("sha") if isinstance(response, dict) else None

    evidence["interruption"] = {
        "error": forced_error,
        "journal_status": interrupted.get("status"),
        "provider_acknowledged": (
            isinstance(response, dict)
            and response.get("merged") is True
            and isinstance(ack_sha, str)
            and SHA.fullmatch(ack_sha) is not None
        ),
        "acknowledged_sha": ack_sha,
        "effect_receipt_present": interrupted.get("effect") is not None,
        "merge_requests": counted.merge_requests,
    }
    write_json(output / "interruption.json", evidence["interruption"])

    interruption_ok = (
        evidence["interruption"]["provider_acknowledged"] is True
        and evidence["interruption"]["journal_status"] == "UNCERTAIN"
        and evidence["interruption"]["effect_receipt_present"] is False
        and counted.merge_requests == 1
    )
    if not interruption_ok:
        evidence["verdict"] = FAIL
        write_json(output / "evidence.json", evidence)
        receiver.shutdown()
        return evidence

    # Simulate receiver loss/restart. A fresh receiver object gets the same
    # durable journal and signing identity, but execution capability is never
    # restored. The repair is read-only.
    receiver.shutdown()
    recovered_gate = _new_gate(wallet, bundle, gate_key)
    recovered = GitHubMergeReceiver(recovered_gate, counted, target, journal)

    late = settle_acknowledged_merges(recovered, wait_seconds=60.0)
    write_json(output / "late-settlement.json", late)

    confirmed = [row for row in late if row.get("status") == "CONFIRMED"]
    if len(confirmed) != 1:
        evidence["recovery"] = {
            "receiver_restarted": True,
            "merge_requests": counted.merge_requests,
            "late_settlement": late,
        }
        evidence["verdict"] = INCONCLUSIVE
        write_json(output / "evidence.json", evidence)
        recovered.shutdown()
        return evidence

    result = confirmed[0]["result"]
    effect = confirmed[0]["effect_receipt"]
    write_json(output / "frontier-receipt.json", result["receipt"])
    write_json(output / "admission-receipt.json", result["admission_receipt"])
    write_json(output / "effect-receipt.json", effect)

    effect_valid, effect_reason = verify_record(
        effect, expected_public_key=effect["gate_public_key"]
    )
    frontier_valid, frontier_reason = verify_record(
        result["receipt"], expected_public_key=effect["gate_public_key"]
    )
    admission_valid, admission_reason = verify_record(
        result["admission_receipt"], expected_public_key=effect["gate_public_key"]
    )

    wallet.add_receipt(result["receipt"])
    wallet.revoke(MANDATE)
    revoked = wallet.export_bundle()
    closure = recovered.close(revoked, MANDATE)
    write_json(output / "closure-receipt.json", closure)
    write_json(output / "final-wallet.olw", revoked)

    closure_valid, closure_reason = verify_record(
        closure, expected_public_key=effect["gate_public_key"]
    )
    after = _snapshot(counted.pr(target), target)
    write_json(output / "provider-after-recovery.json", after)

    effect_hash = record_hash(effect)
    evidence["recovery"] = {
        "receiver_restarted": True,
        "merge_requests": counted.merge_requests,
        "effect_receipt_valid": effect_valid,
        "effect_receipt_reason": effect_reason,
        "frontier_receipt_valid": frontier_valid,
        "frontier_receipt_reason": frontier_reason,
        "admission_receipt_valid": admission_valid,
        "admission_receipt_reason": admission_reason,
        "effect_status": effect.get("status"),
        "effect_sha": effect.get("merge_commit_sha"),
        "effect_hash": effect_hash,
        "closure_valid": closure_valid,
        "closure_reason": closure_reason,
        "closure_status": closure.get("status"),
        "active_frontiers": closure.get("active_frontiers"),
        "confirmed_effect_hashes": closure.get("confirmed_effect_hashes"),
        "unattributed_merge_observations": closure.get("unattributed_merge_observations"),
        "provider_after": after,
    }

    known = (
        counted.merge_requests == 1
        and effect_valid is True
        and frontier_valid is True
        and admission_valid is True
        and effect.get("status") == "MERGE_CONFIRMED"
        and effect.get("merge_commit_sha") == ack_sha
        and effect.get("target") == target.to_record()
        and isinstance(effect.get("merge_commit"), dict)
        and effect["merge_commit"].get("parents", [None, None])[0] == target.base_sha
        and effect["merge_commit"].get("parents", [None, None])[1] == target.head_sha
        and effect["merge_commit"].get("base_drift") is False
        and closure_valid is True
        and closure.get("status") == "EFFECT_CLOSED"
        and closure.get("active_frontiers") == 0
        and closure.get("confirmed_effect_hashes") == [effect_hash]
        and closure.get("unattributed_merge_observations") == []
        and after.get("merged") is True
        and after.get("head_sha") == target.head_sha
        and after.get("merge_commit_sha") == ack_sha
    )
    evidence["verdict"] = PASS if known else FAIL
    write_json(output / "evidence.json", evidence)
    recovered.shutdown()
    return evidence


def add_independent_provider_observation(
    api: Any,
    plan: dict[str, Any],
    evidence: dict[str, Any],
    output: Path,
) -> None:
    if evidence.get("verdict") != PASS:
        return
    effect = json.loads((output / "effect-receipt.json").read_text(encoding="utf-8"))
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
    evidence["independent_provider_read"] = observation
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
        "schema": "openline.github-ack-closure-live-002.result.v1",
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
        "mutation_retry_allowed": False,
        "claim_boundary": (
            "One real disposable GitHub PR merge; deliberate receiver settlement "
            "interruption after acknowledged SHA; fresh receiver restart; read-only "
            "late effect attribution; signed effect and closure evidence."
        ),
    }
    write_json(output / "result.json", report)
    files = [p for p in sorted(output.iterdir()) if p.is_file()]
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(p)}  {p.name}\\n" for p in files),
        encoding="ascii",
    )


def verify(output: Path, *, require_live: bool = False) -> None:
    output = Path(output)
    report = json.loads((output / "result.json").read_text(encoding="utf-8"))
    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))

    require(report["schema"] == "openline.github-ack-closure-live-002.result.v1", "RESULT_SCHEMA_INVALID")
    require(report["experiment_id"] == EXPERIMENT and evidence["experiment_id"] == EXPERIMENT,
            "EXPERIMENT_ID_MISMATCH")
    require(report["verdict"] == evidence["verdict"], "VERDICT_MISMATCH")
    require(report["verdict"] in {PASS, FAIL, INCONCLUSIVE}, "VERDICT_INVALID")
    require(report["wallet_commit"] == WALLET_PIN, "WALLET_PIN_MISMATCH")
    require(report["mutation_retry_allowed"] is False, "MUTATION_RETRY_POLICY_INVALID")
    if require_live:
        require(report["mode"] == "live", "LIVE_RESULT_REQUIRED")
    require(report["preregistration_sha256"] == sha256(PREREG), "PREREGISTRATION_HASH_MISMATCH")
    require(report["source_sha256"] == sha256(Path(__file__)), "SOURCE_HASH_MISMATCH")
    for name, expected in report["files_sha256"].items():
        require((output / name).is_file() and sha256(output / name) == expected,
                "EVIDENCE_HASH_MISMATCH")

    require(evidence["interruption"]["merge_requests"] == 1, "MERGE_REQUEST_CARDINALITY")
    require(evidence["interruption"]["provider_acknowledged"] is True, "PROVIDER_ACK_MISSING")
    require(evidence["interruption"]["journal_status"] == "UNCERTAIN", "INTERRUPTION_STATE_INVALID")
    require(evidence["interruption"]["effect_receipt_present"] is False, "EFFECT_PREMATURELY_PRESENT")

    if report["verdict"] == PASS:
        effect = json.loads((output / "effect-receipt.json").read_text(encoding="utf-8"))
        closure = json.loads((output / "closure-receipt.json").read_text(encoding="utf-8"))
        frontier = json.loads((output / "frontier-receipt.json").read_text(encoding="utf-8"))
        admission = json.loads((output / "admission-receipt.json").read_text(encoding="utf-8"))
        key = effect["gate_public_key"]
        for value in (effect, closure, frontier, admission):
            valid, _reason = verify_record(value, expected_public_key=key)
            require(valid is True, "SIGNED_EVIDENCE_INVALID")
        require(evidence["recovery"]["receiver_restarted"] is True, "RESTART_NOT_PROVED")
        require(evidence["recovery"]["merge_requests"] == 1, "MERGE_RETRIED")
        require(effect["status"] == "MERGE_CONFIRMED", "EFFECT_STATUS_INVALID")
        require(effect["merge_commit_sha"] == evidence["interruption"]["acknowledged_sha"],
                "ACK_EFFECT_SHA_MISMATCH")
        require(closure["status"] == "EFFECT_CLOSED" and closure["active_frontiers"] == 0,
                "CLOSURE_INVALID")
        require(record_hash(effect) in closure["confirmed_effect_hashes"],
                "CLOSURE_EFFECT_BINDING_INVALID")
        require(closure["unattributed_merge_observations"] == [],
                "UNATTRIBUTED_OBSERVATION_PRESENT")
        if require_live:
            require(evidence.get("independent_provider_matches") is True,
                    "INDEPENDENT_PROVIDER_READ_MISMATCH")

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
    require(output != state and output not in state.parents and state not in output.parents, "PATH_OVERLAP")
    require(not output.exists() and not state.exists(), "OUTPUT_ALREADY_EXISTS")
    output.mkdir(parents=True, mode=0o700)
    output.rmdir()

    provider_live = load_provider_live()
    api = provider_live.GitHubAPI(os.environ.get("OPENLINE_GITHUB_TOKEN", ""))
    summary: dict[str, Any] = {
        "schema": "openline.github-ack-closure-live-002.launch.v1",
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
            "".join(f"{value}  {name}\\n" for name, value in hashes.items()),
            encoding="ascii",
        )
    return return_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExperimentError as exc:
        print("STOPPED — " + exc.code, file=sys.stderr)
        raise SystemExit(2)
