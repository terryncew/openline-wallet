"""Disposable, explicit-opt-in GitHub merge experiment and read-only recovery.

No repository is created, no production branch is selected automatically, and
no private key or provider credential is written to the public evidence packet.
A real run needs an operator-owned disposable repository and a merge-only token.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import os
from time import monotonic_ns
from pathlib import Path
from threading import Event, Thread
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import pretty_json, strict_json_load
from .clock import isoformat, utc_now
from .crypto import load_private_key, public_key_hex, record_hash, save_private_key, sign_record, verify_record
from .effect_closure import EffectGate
from .errors import WalletError
from .github_effect import GitHubClient, GitHubMergeReceiver, MergeTarget, _preflight, _snapshot
from .receiver import create_presentation
from .storage import atomic_write_json
from .wallet import Wallet

BASE_COMMIT = "ec385ce2c0cdc253d5e634d1963f254203205e30"
EXPERIMENT = "PROVIDER-EFFECT-001"


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise WalletError(code)


def _empty(path: Path) -> Path:
    path = Path(path).resolve()
    _require(not path.exists() or not any(path.iterdir()), "EVIDENCE_DIRECTORY_NOT_EMPTY")
    path.mkdir(parents=True, exist_ok=True)
    return path


def discover_target(client: GitHubClient, repository: str, number: int) -> tuple[MergeTarget, dict[str, Any]]:
    """Read-only discovery. The default branch and protected bases are excluded."""
    repo = client.repository(repository)
    _require(repo.get("full_name", "").lower() == repository.lower() and
             type(repo.get("id")) is int and not repo.get("archived", False), "GITHUB_SANDBOX_INVALID")
    provisional = MergeTarget(repository.lower(), repo["id"], number, "0" * 40, "scratch", "0" * 40)
    pr = client.pr(provisional)
    _require(pr.get("draft") is False and pr.get("state") == "open" and pr.get("merged") is False,
             "GITHUB_PR_NOT_READY")
    target = MergeTarget(repository.lower(), repo["id"], number, pr["head"]["sha"],
                         pr["base"]["ref"], pr["base"]["sha"])
    _preflight(pr, target)
    _require(target.base_ref != repo.get("default_branch") and target.base_ref.startswith("olp-test-"),
             "GITHUB_DISPOSABLE_BASE_REQUIRED")
    branch = client.branch(repository, target.base_ref)
    _require(branch.get("protected") is False and branch.get("name") == target.base_ref and
             branch.get("commit", {}).get("sha") == target.base_sha,
             "GITHUB_DISPOSABLE_BASE_REQUIRED")
    _require(pr.get("mergeable") is True and pr.get("mergeable_state") not in {"blocked", "dirty", "unstable"},
             "GITHUB_PR_NOT_READY")
    return target, {"repository_id": repo["id"], "default_branch": repo.get("default_branch"),
                    "base_protected": branch["protected"], "draft": pr["draft"],
                    "mergeable": pr["mergeable"], "observed_at": isoformat(utc_now())}


class CountingClient:
    """Count mutation dispatches without exposing the provider token."""
    def __init__(self, client: Any):
        self.client = client
        self.merge_requests = 0

    def __getattr__(self, name: str):
        return getattr(self.client, name)

    def merge(self, target: MergeTarget) -> dict[str, Any]:
        self.merge_requests += 1
        return self.client.merge(target)


class HeldAcknowledgement:
    """Hold after the real merge response, while the receiver still owns the frontier."""
    def __init__(self, client: CountingClient):
        self.client = client
        self.entered, self.release = Event(), Event()
        self.response: dict[str, Any] | None = None
        self.ack_observed_ns: int | None = None

    def __getattr__(self, name: str):
        return getattr(self.client, name)

    def merge(self, target: MergeTarget) -> dict[str, Any]:
        response = self.client.merge(target)
        self.response = response
        self.ack_observed_ns = monotonic_ns()
        self.entered.set()
        if not self.release.wait(60):
            raise WalletError("GITHUB_ACK_HOLD_TIMEOUT")
        return response


def _presentation(receiver: GitHubMergeReceiver, wallet: Wallet, holder: Ed25519PrivateKey,
                  bundle: Mapping[str, Any], mandate: str) -> dict[str, Any]:
    now = utc_now()
    challenge = receiver.gate.issue_challenge(principal_id=wallet.principal_id,
        subject_id="agent-" + mandate[-1], action=receiver.target.action, now=now)
    return create_presentation(bundle=bundle, mandate_id=mandate,
        subject_id="agent-" + mandate[-1], subject_key=holder, action=receiver.target.action,
        receiver_challenge=challenge, now=now)


def _grant(wallet: Wallet, holder: Ed25519PrivateKey, target: MergeTarget, name: str) -> dict[str, Any]:
    now = utc_now()
    wallet.grant(subject_id="agent-" + name, subject_public_key=public_key_hex(holder),
                 scopes=[target.action], expires_at=now + timedelta(hours=1), now=now,
                 mandate_id="grant-" + name)
    return wallet.export_bundle()


def _private_phase(state: Path, name: str, wallet: Wallet, client: Any,
                   target: MergeTarget, config: dict[str, Any]) -> tuple[GitHubMergeReceiver, Ed25519PrivateKey, dict[str, Any]]:
    # Persist both keys before creating a grant, so a crash leaves recovery possible.
    directory = state / name
    directory.mkdir(mode=0o700)
    gate_key, holder = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    save_private_key(directory / "gate.key", gate_key)
    save_private_key(directory / "subject.key", holder)
    bundle = _grant(wallet, holder, target, name)
    gate = EffectGate("provider-effect-" + name, gate_key=gate_key)
    gate.pin_principal(wallet.principal_id, wallet.root_public_key)
    gate.admit_bundle(bundle)
    receiver = GitHubMergeReceiver(gate, client, target, directory / "journal.sqlite")
    config["phases"].append({"name": name, "mandate_id": "grant-" + name})
    atomic_write_json(state / "config.json", config)
    return receiver, holder, bundle


def run_experiment(client: Any, target: MergeTarget, state: Path, output: Path,
                   *, transport: str = "fixture") -> dict[str, Any]:
    """One safe prefrontier control followed by one real merge/held acknowledgement.

    The caller must separately validate the sandbox and authorize the mutation.
    This function never creates a repository, branch, or PR and never retries PUT.
    """
    state, output = Path(state).resolve(), Path(output).resolve()
    _require(state != output and state not in output.parents and output not in state.parents,
             "PRIVATE_EVIDENCE_DIRECTORY_OVERLAP")
    state, output = _empty(state), _empty(output)
    state.chmod(0o700)
    config: dict[str, Any] = {"schema": "openline.wallet.github_experiment_state.v1",
        "target": target.to_record(), "phases": [], "transport": transport}
    atomic_write_json(state / "config.json", config)
    wallet = Wallet.create(state / "wallet")
    counted = CountingClient(client)
    evidence: dict[str, Any] = {"target": target.to_record(), "transport": transport}
    receivers: list[GitHubMergeReceiver] = []
    errors: list[str] = []
    verdict = "INCONCLUSIVE"
    try:
        # Arm A: admit, hold before HTTP, revoke, close, then release the ticket.
        a, holder_a, bundle_a = _private_phase(state, "a", wallet, counted, target, config)
        receivers.append(a)
        prepared = a.prepare(_presentation(a, wallet, holder_a, bundle_a, "grant-a"))
        _require(prepared["decision"] == "PREPARED", "GITHUB_PREPARE_FAILED")
        evidence["admission_a"] = prepared["admission_receipt"]
        wallet.revoke("grant-a")
        revoked_a = wallet.export_bundle()
        evidence["closure_a"] = a.close(revoked_a, "grant-a")
        stopped = a.finish(prepared["ticket"])
        evidence["stopped_a"] = stopped
        wallet.add_receipt(stopped["receipt"])
        _require(stopped["decision"] == "STOPPED" and not stopped["effect_applied"]
                 and counted.merge_requests == 0, "GITHUB_PRE_FRONTIER_FALSIFIED")
        evidence["merge_requests_after_a"] = counted.merge_requests
        evidence["after_a"] = _snapshot(counted.pr(target), target)
        _require(evidence["after_a"]["merged"] is False, "GITHUB_SANDBOX_CHANGED")
        a.shutdown()
        # Arm B: GitHub actually merges the exact head. Hold its acknowledgement,
        # revoke while the frontier is occupied, then let reconciliation finish.
        held = HeldAcknowledgement(counted)
        b, holder_b, bundle_b = _private_phase(state, "b", wallet, held, target, config)
        receivers.append(b)
        prepared_b = b.prepare(_presentation(b, wallet, holder_b, bundle_b, "grant-b"))
        _require(prepared_b["decision"] == "PREPARED", "GITHUB_PREPARE_FAILED")
        evidence["admission_b"] = prepared_b["admission_receipt"]
        worker_done, closure_done = Event(), Event()
        results: dict[str, Any] = {}
        def finish():
            try:
                results["effect"] = b.finish(prepared_b["ticket"])
            except Exception as exc:
                errors.append(exc.code if isinstance(exc, WalletError) else type(exc).__name__)
            finally:
                worker_done.set()
        worker = Thread(target=finish, daemon=True)
        worker.start()
        ack_held = False
        try:
            # The real PUT must have returned before the successful held-ack arm.
            deadline = 60
            while not held.entered.is_set() and not worker_done.is_set() and deadline > 0:
                held.entered.wait(.1)
                deadline -= .1
            ack_held = held.entered.is_set()
            evidence["timing"] = {"ack_observed_ns": held.ack_observed_ns,
                                  "revocation_requested_ns": monotonic_ns()}
            wallet.revoke("grant-b")
            revoked_b = wallet.export_bundle()
            evidence["revoked_b"] = revoked_b
            if ack_held:
                evidence["merge_response"] = held.response
                def close():
                    try:
                        results["closure"] = b.close(revoked_b, "grant-b")
                    except Exception as exc:
                        errors.append(exc.code if isinstance(exc, WalletError) else type(exc).__name__)
                    finally:
                        closure_done.set()
                closer = Thread(target=close, daemon=True)
                evidence["timing"]["closure_requested_ns"] = monotonic_ns()
                closer.start()
                evidence["closure_blocked_during_ack"] = not closure_done.wait(.1)
                evidence["timing"]["ack_released_ns"] = monotonic_ns()
                held.release.set()
                worker.join(65)
                closer.join(65)
                _require(not worker.is_alive() and not closer.is_alive(), "GITHUB_WORKER_TIMEOUT")
                evidence["timing"]["closure_returned_ns"] = monotonic_ns()
            else:
                held.release.set()
                worker.join(65)
                evidence["closure_blocked_during_ack"] = False
            if errors:
                evidence["reconciliation"] = b.reconcile()
                try:
                    results["closure"] = b.close(revoked_b, "grant-b")
                except WalletError as exc:
                    evidence["closure_error"] = exc.code
            if "effect" in results:
                evidence["effect_b"] = results["effect"]
                wallet.add_receipt(results["effect"]["receipt"])
            if "closure" in results:
                evidence["closure_b"] = results["closure"]
            evidence["after_b"] = _snapshot(counted.pr(target), target)
            evidence["merge_requests"] = counted.merge_requests
            evidence["wallet_receipt_count"] = wallet.summary()["receipt_count"]
            success = (ack_held and not errors and evidence["closure_blocked_during_ack"]
                       and "effect" in results and "closure" in results
                       and results["effect"]["decision"] == "ALLOWED"
                       and results["effect"]["effect_applied"] is True
                       and results["closure"]["status"] == "EFFECT_CLOSED"
                       and evidence["after_b"]["merged"] is True
                       and evidence["after_b"]["merge_commit_sha"] == results["effect"]["effect_receipt"]["merge_commit_sha"]
                       and counted.merge_requests == 1)
            verdict = "LIVE_GITHUB_MERGE_OBSERVED" if success and transport == "github" else (
                "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED" if success else "INCONCLUSIVE")
        finally:
            held.release.set()
            if worker.is_alive():
                worker.join(65)
    except Exception as exc:
        errors.append(exc.code if isinstance(exc, WalletError) else type(exc).__name__)
    finally:
        for receiver in reversed(receivers):
            try:
                receiver.shutdown()
            except Exception:
                errors.append("GITHUB_RECEIVER_SHUTDOWN_FAILED")
        evidence["errors"] = errors
        evidence["verdict"] = verdict if not errors else "INCONCLUSIVE"
        evidence["experiment_id"] = EXPERIMENT
        evidence["base_commit"] = BASE_COMMIT
        evidence["claim_boundary"] = "One receiver-controlled GitHub PR merge. No merge queue, Actions, other writers, exact-base CAS, or production recovery claim."
        evidence["completed_at"] = isoformat(utc_now())
        # Public evidence contains no private keys or provider credential.
        for name, value in evidence.items():
            atomic_write_json(output / (name + ".json"), value, mode=0o644)
        _write_result(output, evidence)
    return evidence


def _write_result(output: Path, evidence: Mapping[str, Any]) -> None:
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob("*.json"))
              if p.name != "result.json"}
    key = Ed25519PrivateKey.generate()
    result = sign_record({"schema": "openline.wallet.provider_effect_experiment.v1",
        "experiment_id": EXPERIMENT, "base_commit": BASE_COMMIT, "verdict": evidence["verdict"],
        "transport": evidence["transport"], "evidence_sha256": hashes,
        "claim_boundary": evidence["claim_boundary"],
        "verification": "Disposable self-attestation, not an independent provider witness.",
        "completed_at": evidence["completed_at"]}, key)
    atomic_write_json(output / "result.json", result, mode=0o644)
    (output / "SHA256SUMS.txt").write_text("".join(
        hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
        for p in sorted(output.glob("*.json"))), encoding="ascii")


def recover(client: Any, state: Path, output: Path) -> dict[str, Any]:
    """Read/reconcile only. Never calls GitHubMergeReceiver.finish or client.merge."""
    config = strict_json_load(Path(state) / "config.json")
    target = MergeTarget(**{k: config["target"][k] for k in
        ("repository", "repository_id", "number", "head_sha", "base_ref", "base_sha")})
    wallet = Wallet.open(Path(state) / "wallet")
    result: dict[str, Any] = {"schema": "openline.wallet.github_recovery.v1", "target": target.to_record(),
                               "phases": [], "effect_authority": "NONE"}
    output = _empty(output)
    for phase in config["phases"]:
        name, mandate = phase["name"], phase["mandate_id"]
        now = utc_now()
        current = wallet.timeline().mandates[mandate]
        if current["status"] == "ACTIVE":
            wallet.revoke(mandate, now=now)
        bundle = wallet.export_bundle()
        gate = EffectGate("provider-effect-" + name,
                          gate_key=load_private_key(Path(state) / name / "gate.key"))
        gate.pin_principal(wallet.principal_id, wallet.root_public_key)
        gate.admit_bundle(bundle)
        receiver = GitHubMergeReceiver(gate, client, target, Path(state) / name / "journal.sqlite")
        try:
            observations = receiver.reconcile()
            item: dict[str, Any] = {"name": name, "observations": observations}
            try:
                item["closure"] = receiver.close(bundle, mandate)
                item["status"] = "EFFECT_CLOSED"
            except WalletError as exc:
                item["status"] = "INCONCLUSIVE"
                item["reason"] = exc.code
            result["phases"].append(item)
        finally:
            receiver.shutdown()
    atomic_write_json(output / "recovery.json", result, mode=0o644)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openline-wallet-github-effect")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="read an explicitly named disposable PR")
    inspect.add_argument("--repository", required=True)
    inspect.add_argument("--pr", required=True, type=int)
    inspect.add_argument("--output", required=True, type=Path)
    run = sub.add_parser("run", help="execute one explicitly confirmed disposable merge")
    run.add_argument("--target", required=True, type=Path)
    run.add_argument("--state", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--confirm-action", required=True)
    run.add_argument("--confirm-repository", required=True)
    run.add_argument("--confirm-pr", required=True, type=int)
    run.add_argument("--allow-merge", action="store_true")
    recovery = sub.add_parser("recover", help="read-only reconciliation; never retries merge")
    recovery.add_argument("--state", required=True, type=Path)
    recovery.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        token = os.environ.get("OPENLINE_GITHUB_TOKEN", "")
        client = GitHubClient(token)
        if args.command == "inspect":
            target, safety = discover_target(client, args.repository, args.pr)
            record = {"target": target.to_record(), "safety": safety,
                      "effect_authority": "NONE"}
            atomic_write_json(args.output, record)
            print(pretty_json(record))
            return 0
        if args.command == "run":
            record = strict_json_load(args.target)
            target = MergeTarget(**{k: record["target"][k] for k in
                ("repository", "repository_id", "number", "head_sha", "base_ref", "base_sha")})
            _require(args.allow_merge and args.confirm_action == target.action and
                     args.confirm_repository.lower() == target.repository and args.confirm_pr == target.number,
                     "GITHUB_EXPLICIT_MERGE_CONFIRMATION_REQUIRED")
            current, safety = discover_target(client, target.repository, target.number)
            _require(current == target, "GITHUB_TARGET_CHANGED_SINCE_INSPECTION")
            result = run_experiment(client, target, args.state, args.output, transport="github")
            print(pretty_json({"verdict": result["verdict"], "output": str(args.output)}))
            return 0 if result["verdict"] == "LIVE_GITHUB_MERGE_OBSERVED" else 2
        result = recover(client, args.state, args.output)
        print(pretty_json(result))
        return 0 if all(x["status"] == "EFFECT_CLOSED" for x in result["phases"]) else 2
    except WalletError as exc:
        print("STOPPED — " + exc.code, file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
