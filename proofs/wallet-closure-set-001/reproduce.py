"""WALLET-CLOSURE-SET-001: isolated receiver closure under an untrusted relay.

Run from the repository root. All keys are disposable. The three workers use
separate processes, private directories, and private Gate keys; the relay sees
only public bundles, holder presentations, and signed evidence.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import secrets
import shutil
import traceback
import sys
import tempfile
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.canonical import strict_json_load
from openline_wallet.clock import isoformat, parse_time, utc_now
from openline_wallet.closure_set import (
    create_membership, create_closure_request, evaluate_closure_set,
    sign_set_report, verify_set_report,
)
from openline_wallet.crypto import public_key_hex, record_hash, sign_record
from openline_wallet.errors import WalletError
from openline_wallet.receiver import create_presentation
from openline_wallet.storage import atomic_write_json
from openline_wallet.wallet import Wallet, verify_bundle
from worker import worker_main

BASE_COMMIT = "a8633858a04d33c0e2930214d7c93105f67a3398"
EXPERIMENT = "WALLET-CLOSURE-SET-001"
VERDICT = "FIXED_SET_LOCAL_CLOSURE_ENFORCED"
SOURCES = (
    ".github/workflows/ci.yml",
    "src/openline_wallet/canonical.py",
    "src/openline_wallet/clock.py",
    "src/openline_wallet/crypto.py",
    "src/openline_wallet/effect_closure.py",
    "src/openline_wallet/gate_http.py",
    "src/openline_wallet/receiver.py",
    "src/openline_wallet/storage.py",
    "src/openline_wallet/wallet.py",
    "src/openline_wallet/closure_set.py",
    "proofs/wallet-closure-set-001/reproduce.py",
    "proofs/wallet-closure-set-001/worker.py",
    "proofs/wallet-closure-set-001/verify.py",
    "tests/test_closure_set.py",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("bounded receiver operation timed out")
    return remaining


class Relay:
    """Untrusted message carrier; it has no signing keys and cannot infer closure."""
    def __init__(self, ctx, directory: Path, gate_id: str, principal: str, root_public: str):
        parent, child = ctx.Pipe()
        self.conn = parent
        self.process = ctx.Process(target=worker_main, args=(child, str(directory), gate_id, principal, root_public))
        self.process.start()
        child.close()
        self.pending: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.counter = 0
        ready = self.event("ready", timeout=10)
        self.gate_id = ready["gate_id"]
        self.gate_public_key = ready["gate_public_key"]

    def begin(self, op: str, **kwargs) -> str:
        self.counter += 1
        request_id = f"request-{self.counter}"
        self.conn.send({"id": request_id, "op": op, **kwargs})
        return request_id

    def _receive(self, deadline: float) -> dict[str, Any]:
        if not self.conn.poll(_timeout(deadline)):
            raise TimeoutError(f"{self.gate_id}: receiver did not respond")
        value = self.conn.recv()
        if "event" in value:
            self.events.append(value)
        else:
            self.pending[value["id"]] = value
        return value

    def wait(self, request_id: str, timeout: float = 10) -> Any:
        deadline = time.monotonic() + timeout
        while request_id not in self.pending:
            self._receive(deadline)
        value = self.pending.pop(request_id)
        if not value["ok"]:
            raise WalletError(value["error"], value.get("traceback"))
        return value["result"]

    def call(self, op: str, timeout: float = 10, **kwargs) -> Any:
        return self.wait(self.begin(op, **kwargs), timeout)

    def event(self, name: str, timeout: float = 10) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for index, value in enumerate(self.events):
                if value["event"] == name:
                    return self.events.pop(index)
            self._receive(deadline)

    def has_result(self, request_id: str, interval: float = 0.2) -> bool:
        if request_id in self.pending:
            return True
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline:
            if not self.conn.poll(max(0, deadline - time.monotonic())):
                break
            self._receive(deadline)
        return request_id in self.pending

    def shutdown(self) -> None:
        try:
            if self.process.is_alive():
                self.call("release_write", timeout=2)
                self.call("shutdown", timeout=3)
                self.process.join(timeout=3)
                if self.process.is_alive():
                    self.process.terminate()
                    self.process.join(timeout=3)
        except (EOFError, BrokenPipeError, TimeoutError, WalletError):
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=3)
        finally:
            self.conn.close()


def _source_hashes(repo: Path) -> dict[str, str]:
    return {name: _sha((repo / name).read_bytes()) for name in SOURCES}


def run(output: Path, *, workspace: Path, repo: Path) -> dict[str, Any]:
    wallet = Wallet.create(workspace / "wallet", label="Closure set proof")
    subject = Ed25519PrivateKey.generate()
    now = utc_now()
    wallet.grant(subject_id="agent-a", subject_public_key=public_key_hex(subject),
                 scopes=["deploy:staging"], expires_at=now + timedelta(hours=1),
                 now=now, mandate_id="grant-a")
    grant = wallet.export_bundle()
    ctx = mp.get_context("spawn")
    workers: list[Relay] = []
    trace: list[dict[str, Any]] = []
    final_receipts: list[dict[str, Any]] = []

    def mark(event: str, **data) -> None:
        trace.append({"sequence": len(trace) + 1, "event": event,
                      "observed_at": isoformat(utc_now()), **data})
        atomic_write_json(output / "trace-live.json", trace, mode=0o644)

    def prepare(worker: Relay, release: str, bundle=grant) -> dict[str, Any]:
        challenge = worker.call("challenge")
        presentation = create_presentation(bundle=bundle, mandate_id="grant-a",
            subject_id="agent-a", subject_key=subject, action="deploy:staging",
            receiver_challenge=challenge)
        return worker.call("prepare", presentation=presentation, release=release)

    def finish(worker: Relay, prepared: dict[str, Any], release: str) -> dict[str, Any]:
        result = worker.call("finish", ticket=prepared["ticket"], release=release)
        final_receipts.append(result["receipt"])
        return result

    try:
        for i in range(3):
            workers.append(Relay(ctx, workspace / f"receiver-{i}", f"receiver-{i}",
                                 wallet.principal_id, wallet.root_public_key))
        manifest = create_membership(wallet.root_key, "grant-a", [
            {"gate_id": w.gate_id, "gate_public_key": w.gate_public_key} for w in workers])
        for worker in workers:
            worker.call("admit", bundle=grant)
        mark("grant_admitted", receivers=3)

        # An ordinary effect is already complete; revocation cannot undo history.
        prior = prepare(workers[1], "prior-effect")
        prior_result = finish(workers[1], prior, "prior-effect")
        assert prior_result["effect_applied"]
        mark("prior_effect_committed", gate_id=workers[1].gate_id)

        pending0 = prepare(workers[0], "held-after-close")
        pending1 = prepare(workers[1], "held-after-close")
        pending2 = prepare(workers[2], "inflight")
        workers[2].call("hold_write", release="inflight")
        running = workers[2].begin("finish", ticket=pending2["ticket"], release="inflight")
        workers[2].event("frontier_entered")
        mark("inflight_effect_held_at_ledger", gate_id=workers[2].gate_id)

        # Revocation has been signed while one actual effect is already in flight.
        wallet.revoke("grant-a", now=utc_now())
        revoked = wallet.export_bundle()
        request = create_closure_request(wallet.root_key, manifest, revoked)
        mark("revocation_signed", head_hash=request["head_hash"])
        for worker in workers[:2]:
            worker.call("admit", bundle=revoked)
        mark("two_receivers_admitted_revocation", closure_status="AUTHORIZATION_ADMITTED_ONLY")

        witnesses = [workers[i].call("close", manifest=manifest, request=request, bundle=revoked)
                     for i in (0, 1)]
        partial = evaluate_closure_set(manifest, request, revoked, witnesses)
        assert partial["status"] == "CLOSURE_INCOMPLETE" and partial["missing"] == ["receiver-2"]
        mark("two_of_three_incomplete", verified_receivers=2)

        waiting_close = workers[2].begin("close", manifest=manifest, request=request, bundle=revoked)
        assert not workers[2].has_result(waiting_close, 0.2)
        mark("closure_waits_for_actual_frontier")
        workers[2].call("release_write")
        inflight_result = workers[2].wait(running)
        final_receipts.append(inflight_result["receipt"])
        assert inflight_result["effect_applied"]
        mark("inflight_effect_completed_before_closure", receipt_hash=inflight_result["receipt_hash"])
        witness2 = workers[2].wait(waiting_close)
        witnesses.append(witness2)
        mark("third_receiver_closed", gate_id=workers[2].gate_id)

        # The closure clock is sampled after drain, not when the request queued.
        assert parse_time(witness2["local_closure"]["closed_at"]) >= parse_time(inflight_result["effect_receipt"]["completed_at"])
        closed = evaluate_closure_set(manifest, request, revoked, witnesses)
        assert closed["status"] == "EFFECT_CLOSED"
        auditor = Ed25519PrivateKey.generate()
        report = sign_set_report(closed, auditor)
        verify_set_report(report, public_key_hex(auditor), manifest, request, revoked, witnesses)
        mark("set_closure_verified", required_receivers=3)

        stopped = [finish(workers[0], pending0, "held-after-close"),
                   finish(workers[1], pending1, "held-after-close")]
        assert all(r["decision"] == "STOPPED" and r["reason_codes"] == ["MANDATE_REVOKED"]
                   and not r["effect_applied"] for r in stopped)
        for worker in workers:
            fresh = prepare(worker, "new-after-close", bundle=revoked)
            assert fresh["decision"] == "STOPPED" and fresh["reason_codes"] == ["MANDATE_REVOKED"]
            final_receipts.append(fresh["receipt"])
        mark("all_post_closure_actions_stopped")

        ledgers = {w.gate_id: w.call("ledger") for w in workers}
        assert [len(ledgers[w.gate_id]) for w in workers] == [0, 1, 1]
        assert all(e["release"] in {"prior-effect", "inflight"} for rows in ledgers.values() for e in rows)
        for receipt in final_receipts:
            wallet.add_receipt(receipt)
        wallet_final = wallet.export_bundle()
        verify_bundle(wallet_final)

        # An untrusted carrier may drop, duplicate, reorder, or counterfeit evidence.
        missing = evaluate_closure_set(manifest, request, revoked, witnesses[:2])
        forged = json.loads(json.dumps(witnesses[2]))
        forged["signature"]["value"] = "0" * 128
        fake = evaluate_closure_set(manifest, request, revoked, witnesses[:2] + [forged])
        duplicate = evaluate_closure_set(manifest, request, revoked, witnesses + [witnesses[0]])
        fresh_request = create_closure_request(wallet.root_key, manifest, revoked, nonce="fresh-" + secrets.token_hex(8))
        replay = evaluate_closure_set(manifest, fresh_request, revoked, witnesses)
        assert all(r["status"] == "CLOSURE_INCOMPLETE" for r in (missing, fake, duplicate, replay))
        mark("relay_attacks_rejected", attacks=4)
        # An unreachable member cannot be replaced by an auditor or a 2/3 vote.
        unreachable = evaluate_closure_set(manifest, request, revoked, witnesses[:2])
        assert unreachable["status"] == "CLOSURE_INCOMPLETE"
        mark("unreachable_member_remains_incomplete")

        checks = {
            "three_independent_processes": len({w.process.pid for w in workers}) == 3,
            "distinct_receiver_keys": len({w.gate_public_key for w in workers}) == 3,
            "two_of_three_never_closes": partial["status"] == "CLOSURE_INCOMPLETE",
            "inflight_frontier_drained_before_closure": parse_time(witness2["local_closure"]["closed_at"]) >= parse_time(inflight_result["effect_receipt"]["completed_at"]),
            "all_signed_closures_required": closed["verified_receivers"] == 3 and closed["status"] == "EFFECT_CLOSED",
            "zero_post_closure_effects": all(not r["effect_applied"] for r in stopped),
            "prior_effect_preserved": len(ledgers["receiver-1"]) == 1,
            "rejected_actions_preserve_wallet_receipts": len(wallet_final["receipts"]) == len(final_receipts),
            "missing_forged_duplicate_replay_rejected": all(r["status"] == "CLOSURE_INCOMPLETE" for r in (missing, fake, duplicate, replay)),
            "unreachable_member_fails_closed": unreachable["status"] == "CLOSURE_INCOMPLETE",
        }
        assert all(checks.values()), checks
        records = {
            "membership.json": manifest, "request.json": request, "grant-bundle.json": grant,
            "revoked-bundle.json": revoked, "witnesses.json": witnesses, "partial.json": partial,
            "report.json": report, "ledgers.json": ledgers, "prior-result.json": prior_result,
            "inflight-result.json": inflight_result, "post-closure.json": stopped,
            "wallet-final.json": wallet_final, "fresh-request.json": fresh_request,
            "forged-witness.json": forged,
            "negative-controls.json": {"missing": missing, "forged": fake, "duplicate": duplicate,
                                       "replay": replay, "unreachable": unreachable},
            "trace.json": trace,
        }
        for name, value in records.items():
            atomic_write_json(output / name, value, mode=0o644)
        result = sign_record({
            "schema": "openline.wallet.experiment_result.v1", "experiment_id": EXPERIMENT,
            "verdict": VERDICT, "base_commit": BASE_COMMIT, "source_sha256": _source_hashes(repo),
            "checks": checks, "observed": {"receivers": 3, "effects_before_or_during_closure": 2,
              "effects_after_closure": 0, "missing_member_status": missing["status"],
              "verified_receivers": closed["verified_receivers"], "wallet_receipts": len(final_receipts)},
            "evidence_sha256": {name: _sha((output / name).read_bytes()) for name in sorted(records)},
            "auditor_public_key": public_key_hex(auditor),
            "claim_boundary": "Three isolated local staging receivers with pinned keys, one fixed signed membership, and a cooperating final ledger frontier. No arbitrary provider, durable restart recovery, or open-world closure claim.",
        }, auditor)
        atomic_write_json(output / "result.json", result, mode=0o644)
        names = sorted([*records, "result.json"])
        (output / "SHA256SUMS.txt").write_text("".join(f"{_sha((output / name).read_bytes())}  {name}\n" for name in names), encoding="ascii")
        (output / "trace-live.json").unlink(missing_ok=True)
        return result
    except Exception:
        # Keep the failed path, including unresolved intents, before shutdown
        # releases test holds. No failure path may issue a closure verdict.
        diagnostics = output / "diagnostics"
        for worker in workers:
            source = workspace / worker.gate_id
            destination = diagnostics / worker.gate_id
            for path in source.rglob("*.json"):
                target = destination / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        raise
    finally:
        for worker in reversed(workers):
            worker.shutdown()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise WalletError("PROOF_OUTPUT_NOT_EMPTY")
    output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[2]
    try:
        with tempfile.TemporaryDirectory(prefix="wallet-closure-set-") as directory:
            result = run(output, workspace=Path(directory), repo=repo)
    except Exception as exc:
        atomic_write_json(output / "failure.json", {
            "schema": "openline.wallet.experiment_failure.v1", "experiment_id": EXPERIMENT,
            "verdict": "INCONCLUSIVE", "error_class": type(exc).__name__,
            "error_code": exc.code if isinstance(exc, WalletError) else "UNRESOLVED",
            "traceback": traceback.format_exc(), "observed_at": isoformat(utc_now()),
            "source_sha256": _source_hashes(repo),
            "note": "Diagnostic snapshot only. Shutdown may release test-held work. No closure is claimed."
        }, mode=0o644)
        raise
    print(json.dumps({"verdict": result["verdict"], "checks": result["checks"],
                      "result_hash": record_hash(result)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
