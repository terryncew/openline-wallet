"""STOP-BARRIER-COLD-001 — externally derived stop-barrier probes.

Ported from the operator barrier clauses in:
  Stop Means Stop: Measuring and Repairing the Enforcement Gap in
  Agent-Framework Control Primitives (arXiv:2607.14166)

This proof does not import agent frameworks or SOUNDGATE. It maps only clauses
for which current OpenLine has an equivalent primitive:

* held effect ticket: EffectClosure.prepare
* receiver-observed stop: authentic revocation + EffectClosure.close returning
  signed EFFECT_CLOSED
* replay: repeated finish of the exact same effect ticket
* ambiguous egress outcome: MCPConsequenceGate must not retry automatically

OpenLine has no run-wide approval-pause primitive and no receiver timeout
primitive here. Those axes are explicitly not promoted into claims.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.canonical import strict_json_load
from openline_wallet.crypto import (
    public_key_hex,
    record_hash,
    sign_record,
    verify_record,
)
from openline_wallet.effect_closure import EffectClosure, EffectGate
from openline_wallet.mcp_egress import (
    MCPConsequenceGate,
    MCP_CONTRACT_SCHEMA,
    MCP_2026_PROTOCOL,
    ReceiverContract,
)
from openline_wallet.receiver import create_presentation
from openline_wallet.storage import atomic_write_json
from openline_wallet.wallet import Wallet

BASE_COMMIT = "6cbaf646cf8953cbad474dd1b609966c19689e9d"
EXTERNAL_ARTIFACT_COMMIT = "656840348dcf9660f8db7add9f3ca9f794a55926"
ARXIV_ID = "2607.14166"
VERDICT = "EXTERNAL_STOP_BARRIER_PROBE_SURVIVED_WITHIN_EXISTING_LOCAL_SCOPE"

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
BASE = datetime(2026, 9, 10, 16, 0, tzinfo=timezone.utc)
ACTION = "deploy:staging"


def at(seconds: float):
    return BASE + timedelta(seconds=seconds)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    value = strict_json_load(path)
    if not isinstance(value, list):
        raise AssertionError("ledger must be a list")
    return value


class LocalFixture:
    def __init__(self, root: Path, *, gate_id: str):
        self.root = root
        self.wallet = Wallet.create(root / "wallet", now=at(0))
        self.subject_key = Ed25519PrivateKey.generate()
        self.subject_id = "agent-a"
        self.mandate_id = "grant-a"
        self.wallet.grant(
            subject_id=self.subject_id,
            subject_public_key=public_key_hex(self.subject_key),
            scopes=[ACTION],
            expires_at=at(3600),
            now=at(1),
            mandate_id=self.mandate_id,
        )
        self.bundle = self.wallet.export_bundle(now=at(2))
        self.gate = EffectGate(gate_id)
        self.gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        self.gate.admit_bundle(self.bundle, now=at(2))
        self.ledger_path = root / "effects.json"
        self.effects = EffectClosure(self.gate, self.ledger_path, root / "receipts")

    def presentation(self, when):
        challenge = self.gate.issue_challenge(
            principal_id=self.wallet.principal_id,
            subject_id=self.subject_id,
            action=ACTION,
            now=when,
        )
        return create_presentation(
            bundle=self.bundle,
            mandate_id=self.mandate_id,
            subject_id=self.subject_id,
            subject_key=self.subject_key,
            action=ACTION,
            receiver_challenge=challenge,
            now=when,
        )

    def prepare(self, release: str, when):
        return self.effects.prepare(
            self.presentation(when),
            action=ACTION,
            release=release,
            now=when,
        )

    def revoke_bundle(self, revoke_when, export_when):
        self.wallet.revoke(self.mandate_id, now=revoke_when)
        return self.wallet.export_bundle(now=export_when)

    def shutdown(self):
        self.effects.shutdown()


def case_hold_then_reject() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        f = LocalFixture(Path(td), gate_id="stop-barrier-hold")
        try:
            prepared = f.prepare("held", at(3))
            if prepared["decision"] != "PREPARED":
                raise AssertionError("effect was not prepared")
            if ledger_rows(f.ledger_path):
                raise AssertionError("held effect leaked before decision")

            revoked = f.revoke_bundle(at(4), at(5))
            closure = f.effects.close(revoked, f.mandate_id, now=at(6))
            stopped = f.effects.finish(
                prepared["ticket"], action=ACTION, release="held", now=at(7)
            )
            rows = ledger_rows(f.ledger_path)

            if closure["status"] != "EFFECT_CLOSED":
                raise AssertionError("receiver did not close revoked effect set")
            if closure["pending_fenced"] != 1:
                raise AssertionError("held ticket was not counted as fenced")
            if stopped["decision"] != "STOPPED":
                raise AssertionError("held effect executed after rejection/closure")
            if stopped["reason_codes"] != ["MANDATE_REVOKED"]:
                raise AssertionError("unexpected stop reason")
            if stopped["effect_applied"] is not False or rows:
                raise AssertionError("rejected held effect reached ledger")

            return {
                "case_id": "hold_then_reject",
                "mapped_clause": "B1/B2_EFFECT_TICKET_SCOPE",
                "gate_public_key": f.gate.public_key,
                "closure": closure,
                "stopped_effect_receipt": stopped["effect_receipt"],
                "ledger_count_after_close_and_release": len(rows),
                "verdict": "PASS",
            }
        finally:
            f.shutdown()


def case_exact_ticket_replay() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        f = LocalFixture(Path(td), gate_id="stop-barrier-replay")
        try:
            prepared = f.prepare("replay", at(3))
            first = f.effects.finish(
                prepared["ticket"], action=ACTION, release="replay", now=at(4)
            )
            second = f.effects.finish(
                prepared["ticket"], action=ACTION, release="replay", now=at(5)
            )
            rows = ledger_rows(f.ledger_path)

            if first != second:
                raise AssertionError("exact ticket replay did not return identical result")
            if first["effect_applied"] is not True:
                raise AssertionError("first effect did not execute")
            if len(rows) != 1:
                raise AssertionError("exact ticket replay executed logical effect twice")

            return {
                "case_id": "exact_ticket_replay",
                "mapped_clause": "B3_EXACT_TICKET_SCOPE",
                "gate_public_key": f.gate.public_key,
                "first_effect_receipt": first["effect_receipt"],
                "first_effect_receipt_hash": first["effect_receipt_hash"],
                "second_effect_receipt_hash": second["effect_receipt_hash"],
                "ledger_count_after_replay": len(rows),
                "verdict": "PASS",
            }
        finally:
            f.shutdown()


def case_close_fences_late_work() -> dict[str, Any]:
    """Make one effect sit inside the frontier while revocation is issued.

    The critical assertion is not that revocation issuance rewinds the in-flight
    effect. It is that EFFECT_CLOSED cannot be observed until that frontier has
    drained, and no pending mediated effect can land after closure returns.
    """
    with tempfile.TemporaryDirectory() as td:
        f = LocalFixture(Path(td), gate_id="stop-barrier-fence")
        try:
            first = f.prepare("first", at(3))
            second = f.prepare("second", at(3.1))

            entered = threading.Event()
            release = threading.Event()
            closed = threading.Event()
            event_lock = threading.Lock()
            events: list[str] = []
            results: dict[str, Any] = {}
            original_write = atomic_write_json

            def log(tag: str):
                with event_lock:
                    events.append(tag)

            def held_write(path, value, **kwargs):
                if Path(path).resolve() == f.ledger_path.resolve():
                    log("FIRST_EFFECT_ENTERED_FRONTIER")
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError("frontier hold timed out")
                    original_write(path, value, **kwargs)
                    log("FIRST_EFFECT_COMMITTED")
                    return None
                return original_write(path, value, **kwargs)

            def run_first():
                try:
                    results["first"] = f.effects.finish(
                        first["ticket"], action=ACTION, release="first", now=at(4)
                    )
                except Exception as exc:  # pragma: no cover - diagnostic path
                    results["first_error"] = repr(exc)

            def run_close(bundle):
                log("CLOSE_CALLED")
                try:
                    results["closure"] = f.effects.close(
                        bundle, f.mandate_id, now=at(7)
                    )
                    log("EFFECT_CLOSED_RETURNED")
                except Exception as exc:  # pragma: no cover - diagnostic path
                    results["close_error"] = repr(exc)
                finally:
                    closed.set()

            with patch("openline_wallet.effect_closure.atomic_write_json", side_effect=held_write):
                worker = threading.Thread(target=run_first, name="stop-barrier-first")
                worker.start()
                if not entered.wait(5):
                    raise AssertionError("first effect never entered frontier")

                revoked = f.revoke_bundle(at(5), at(6))
                log("REVOCATION_ISSUED")

                closer = threading.Thread(
                    target=run_close, args=(revoked,), name="stop-barrier-close"
                )
                closer.start()

                # Closure must not be observable while a capable effect is still
                # inside the serialized frontier.
                if closed.wait(0.1):
                    raise AssertionError("EFFECT_CLOSED returned before active frontier drained")

                log("ALLOW_FIRST_FRONTIER_TO_DRAIN")
                release.set()
                worker.join(5)
                closer.join(5)

            if worker.is_alive() or closer.is_alive():
                raise AssertionError("probe thread failed to terminate")
            if "first_error" in results or "close_error" in results:
                raise AssertionError(str(results))

            closure = results["closure"]
            if closure["status"] != "EFFECT_CLOSED":
                raise AssertionError("closure receipt missing")
            if closure["active_frontiers"] != 0:
                raise AssertionError("closure reported active frontier")
            if closure["pending_fenced"] != 1:
                raise AssertionError("pending sibling was not fenced")

            rows_at_close = ledger_rows(f.ledger_path)
            count_at_close = len(rows_at_close)
            if count_at_close != 1:
                raise AssertionError("in-flight effect accounting mismatch at closure")

            after = f.effects.finish(
                second["ticket"], action=ACTION, release="second", now=at(8)
            )
            # Give a would-be orphan a chance to appear. All actual capable work
            # is joined before this point; the sleep is only a post-condition guard.
            time.sleep(0.05)
            final_rows = ledger_rows(f.ledger_path)

            if after["decision"] != "STOPPED":
                raise AssertionError("pending effect crossed barrier after EFFECT_CLOSED")
            if after["reason_codes"] != ["MANDATE_REVOKED"]:
                raise AssertionError("pending effect stopped for unexpected reason")
            if len(final_rows) != count_at_close:
                raise AssertionError("mediated effect landed after EFFECT_CLOSED")

            order = {tag: events.index(tag) for tag in (
                "REVOCATION_ISSUED",
                "FIRST_EFFECT_COMMITTED",
                "EFFECT_CLOSED_RETURNED",
            )}
            if not (
                order["REVOCATION_ISSUED"]
                < order["FIRST_EFFECT_COMMITTED"]
                < order["EFFECT_CLOSED_RETURNED"]
            ):
                raise AssertionError("probe did not exercise issuance-before-effect-before-closure")

            return {
                "case_id": "close_fences_late_work",
                "mapped_clause": "B4_RECEIVER_EFFECT_CLOSED_SCOPE",
                "gate_public_key": f.gate.public_key,
                "events": events,
                "closure": closure,
                "first_effect_receipt": results["first"]["effect_receipt"],
                "post_close_stop_receipt": after["effect_receipt"],
                "ledger_count_at_effect_closed": count_at_close,
                "ledger_count_after_pending_release": len(final_rows),
                "revocation_issuance_preceded_inflight_effect": True,
                "effect_closed_followed_inflight_effect": True,
                "effect_after_effect_closed": False,
                "verdict": "PASS",
            }
        finally:
            f.shutdown()


def mcp_headers() -> dict[str, str]:
    return {
        "Mcp-Protocol-Version": MCP_2026_PROTOCOL,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "account.set_limit",
    }


def mcp_request(amount: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "account.set_limit", "arguments": {"amount": amount}},
    }


def mcp_contract() -> ReceiverContract:
    return ReceiverContract.from_mapping({
        "schema": MCP_CONTRACT_SCHEMA,
        "contract_id": "stop-barrier-account-limit",
        "wallet_scope": "mcp:account.set_limit",
        "tool_name": "account.set_limit",
        "rule": {
            "rule_id": "amount-0-5000",
            "parameter": "amount",
            "minimum": 0,
            "maximum": 5000,
        },
    })


def case_ambiguous_egress_no_auto_retry() -> dict[str, Any]:
    """Exercise the existing EGRESS-GATE-001 ambiguity rule.

    The downstream records one call and then loses its acknowledgement. The gate
    must return UNRESOLVED and must not make a second downstream attempt.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # MCPConsequenceGate.execute() intentionally revalidates against the
        # receiver's real current clock. Keep this case on that same clock so
        # the probe cannot expire its own authority before reaching the
        # acknowledgement-loss path.
        current = datetime.now(timezone.utc)
        wallet = Wallet.create(root / "wallet", now=current)
        subject_key = Ed25519PrivateKey.generate()
        subject_id = "worker"
        mandate_id = "mcp-worker"
        scope = "mcp:account.set_limit"
        wallet.grant(
            subject_id=subject_id,
            subject_public_key=public_key_hex(subject_key),
            scopes=[scope],
            expires_at=current + timedelta(hours=1),
            now=current,
            mandate_id=mandate_id,
        )
        bundle = wallet.export_bundle(now=current + timedelta(seconds=1))
        gate = EffectGate("stop-barrier-egress")
        gate.pin_principal(wallet.principal_id, wallet.root_public_key)
        gate.admit_bundle(bundle, now=current + timedelta(seconds=1))

        calls: list[int] = []

        def downstream(proposal, _headers):
            calls.append(proposal.arguments["amount"])
            raise RuntimeError("simulated acknowledgement loss")

        runtime = MCPConsequenceGate(
            gate, mcp_contract(), root / "consequence-receipts", downstream
        )
        challenge_time = current + timedelta(seconds=2)
        challenge = gate.issue_challenge(
            principal_id=wallet.principal_id,
            subject_id=subject_id,
            action=scope,
            now=challenge_time,
        )
        presentation = create_presentation(
            bundle=bundle,
            mandate_id=mandate_id,
            subject_id=subject_id,
            subject_key=subject_key,
            action=scope,
            receiver_challenge=challenge,
            now=challenge_time,
        )
        result = runtime.execute(
            headers=mcp_headers(),
            body=mcp_request(2000),
            presentation=presentation,
        )
        if result["decision"] != "UNRESOLVED":
            raise AssertionError("ambiguous downstream outcome was not preserved")
        if result["effect_applied"] is not None:
            raise AssertionError("ambiguous effect was falsely classified")
        if calls != [2000]:
            raise AssertionError("ambiguous downstream call was automatically retried")

        return {
            "case_id": "ambiguous_egress_no_auto_retry",
            "mapped_clause": "B3_ADJACENT_NO_AUTOMATIC_RETRY",
            "gate_public_key": gate.public_key,
            "consequence_receipt": result["receipt"],
            "downstream_call_count": len(calls),
            "decision": result["decision"],
            "effect_applied": result["effect_applied"],
            "verdict": "PASS",
        }


def source_hashes() -> dict[str, str]:
    paths = [
        ".github/workflows/stop-barrier-cold-001.yml",
        "STOP_BARRIER_COLD_001.md",
        "proofs/stop-barrier-cold-001/run.py",
    ]
    return {path: sha256_file(REPO_ROOT / path) for path in paths}


def reproduce(output: Path) -> dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    case_functions = [
        ("hold_then_reject", case_hold_then_reject),
        ("exact_ticket_replay", case_exact_ticket_replay),
        ("close_fences_late_work", case_close_fences_late_work),
        ("ambiguous_egress_no_auto_retry", case_ambiguous_egress_no_auto_retry),
    ]
    cases: list[dict[str, Any]] = []
    case_hashes: dict[str, str] = {}

    # Persist each completed case immediately. If a later probe fails, CI's
    # always-upload diagnostics step still has the surviving evidence plus an
    # explicit failure record instead of an empty artifact directory.
    for expected_case_id, case_function in case_functions:
        try:
            case = case_function()
        except Exception as exc:
            atomic_write_json(
                output / "failure.json",
                {
                    "schema": "openline.wallet.stop_barrier_cold_001.failure.v1",
                    "experiment_id": "STOP-BARRIER-COLD-001",
                    "base_commit": BASE_COMMIT,
                    "failed_case": expected_case_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                mode=0o644,
            )
            raise
        if case.get("case_id") != expected_case_id:
            raise AssertionError(f"case id mismatch: {expected_case_id}")
        path = output / f"{case['case_id']}.json"
        atomic_write_json(path, case, mode=0o644)
        cases.append(case)
        case_hashes[case["case_id"]] = record_hash(case)

    experiment_key = Ed25519PrivateKey.generate()
    result = sign_record({
        "schema": "openline.wallet.stop_barrier_cold_001.result.v1",
        "experiment_id": "STOP-BARRIER-COLD-001",
        "experiment_public_key": public_key_hex(experiment_key),
        "base_commit": BASE_COMMIT,
        "external_source": {
            "paper": "Stop Means Stop: Measuring and Repairing the Enforcement Gap in Agent-Framework Control Primitives",
            "arxiv_id": ARXIV_ID,
            "artifact_commit": EXTERNAL_ARTIFACT_COMMIT,
            "ported_operator_clauses": ["B1", "B2", "B3", "B4"],
        },
        "source_sha256": source_hashes(),
        "case_hashes": case_hashes,
        "cases": {case["case_id"]: case["verdict"] for case in cases},
        "not_equivalent": {
            "run_wide_sibling_approval_pause": "NO_EQUIVALENT_OPENLINE_PRIMITIVE_IN_THIS_PATH",
            "receiver_timeout_primitive": "NO_EQUIVALENT_OPENLINE_PRIMITIVE_IN_THIS_PATH",
            "arbitrary_new_ticket_logical_dedup": "NOT_CLAIMED_EXACT_TICKET_REPLAY_ONLY",
            "kernel_or_network_complete_mediation": "NOT_ESTABLISHED",
            "external_provider_queue_fencing": "NOT_ESTABLISHED",
        },
        "earned_if_verified": (
            "Within the existing receiver-local serialized effect path, held work "
            "did not escape after EFFECT_CLOSED, exact ticket replay did not duplicate "
            "the effect, and ambiguous egress was not automatically retried."
        ),
        "verdict": VERDICT,
        "limitations": [
            "receiver-local in-process serialization and one receiver-owned staging ledger",
            "cooperating mediated adapter only",
            "EFFECT_CLOSED is the stop observation boundary; revocation issuance alone is not",
            "no run-wide sibling approval pause primitive tested",
            "no receiver timeout primitive tested",
            "exact ticket replay only; no general cross-ticket or distributed exactly-once claim",
            "no kernel/network complete-mediation claim",
            "no external provider queue-fencing claim",
        ],
        "self_attested": True,
    }, experiment_key)
    atomic_write_json(output / "result.json", result, mode=0o644)

    files = sorted(p for p in output.glob("*.json") if p.is_file())
    sums = "".join(f"{sha256_file(p)}  {p.name}\n" for p in files)
    (output / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    return result


def verify_case_signatures(case: dict[str, Any]) -> None:
    key = case["gate_public_key"]
    for field in (
        "closure",
        "stopped_effect_receipt",
        "first_effect_receipt",
        "post_close_stop_receipt",
        "consequence_receipt",
    ):
        record = case.get(field)
        if record is None:
            continue
        valid, reason = verify_record(record, expected_public_key=key)
        if (valid, reason) != (True, None):
            raise AssertionError(f"{case['case_id']} {field} signature invalid: {reason}")


def verify(output: Path) -> dict[str, Any]:
    expected_sums = {}
    for line in (output / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        expected_sums[name] = digest
    for name, digest in expected_sums.items():
        if sha256_file(output / name) != digest:
            raise AssertionError(f"artifact checksum mismatch: {name}")

    result = strict_json_load(output / "result.json")
    if not isinstance(result, dict):
        raise AssertionError("result must be object")
    valid, reason = verify_record(
        result, expected_public_key=result.get("experiment_public_key")
    )
    if (valid, reason) != (True, None):
        raise AssertionError(f"aggregate result signature invalid: {reason}")
    if result.get("experiment_id") != "STOP-BARRIER-COLD-001":
        raise AssertionError("experiment id mismatch")
    if result.get("base_commit") != BASE_COMMIT:
        raise AssertionError("base commit mismatch")
    if result.get("verdict") != VERDICT:
        raise AssertionError("verdict mismatch")
    if result.get("external_source", {}).get("artifact_commit") != EXTERNAL_ARTIFACT_COMMIT:
        raise AssertionError("external artifact pin mismatch")

    if result.get("source_sha256") != source_hashes():
        raise AssertionError("source hash mismatch")

    expected_case_ids = {
        "hold_then_reject",
        "exact_ticket_replay",
        "close_fences_late_work",
        "ambiguous_egress_no_auto_retry",
    }
    if set(result.get("cases", {})) != expected_case_ids:
        raise AssertionError("case set mismatch")
    if any(value != "PASS" for value in result["cases"].values()):
        raise AssertionError("one or more cases did not pass")

    loaded = {}
    for case_id in expected_case_ids:
        case = strict_json_load(output / f"{case_id}.json")
        if not isinstance(case, dict):
            raise AssertionError(f"{case_id} case invalid")
        if record_hash(case) != result["case_hashes"].get(case_id):
            raise AssertionError(f"{case_id} aggregate binding mismatch")
        if case.get("verdict") != "PASS":
            raise AssertionError(f"{case_id} verdict mismatch")
        verify_case_signatures(case)
        loaded[case_id] = case

    hold = loaded["hold_then_reject"]
    if hold["ledger_count_after_close_and_release"] != 0:
        raise AssertionError("held/rejected effect leaked")
    if hold["closure"]["status"] != "EFFECT_CLOSED":
        raise AssertionError("hold case closure missing")

    replay = loaded["exact_ticket_replay"]
    if replay["ledger_count_after_replay"] != 1:
        raise AssertionError("exact ticket replay duplicated effect")
    if replay["first_effect_receipt_hash"] != replay["second_effect_receipt_hash"]:
        raise AssertionError("exact ticket replay changed effect receipt")

    fence = loaded["close_fences_late_work"]
    if fence["effect_after_effect_closed"] is not False:
        raise AssertionError("effect landed after EFFECT_CLOSED")
    if fence["ledger_count_at_effect_closed"] != fence["ledger_count_after_pending_release"]:
        raise AssertionError("ledger advanced after EFFECT_CLOSED")
    if fence["closure"]["pending_fenced"] != 1 or fence["closure"]["active_frontiers"] != 0:
        raise AssertionError("closure did not establish expected local frontier")
    events = fence["events"]
    if not (
        events.index("REVOCATION_ISSUED")
        < events.index("FIRST_EFFECT_COMMITTED")
        < events.index("EFFECT_CLOSED_RETURNED")
    ):
        raise AssertionError("issuance/commit/closure distinction lost")

    egress = loaded["ambiguous_egress_no_auto_retry"]
    if egress["decision"] != "UNRESOLVED" or egress["effect_applied"] is not None:
        raise AssertionError("ambiguous egress was falsely resolved")
    if egress["downstream_call_count"] != 1:
        raise AssertionError("ambiguous egress was automatically retried")

    not_equivalent = result["not_equivalent"]
    if not_equivalent["receiver_timeout_primitive"] != "NO_EQUIVALENT_OPENLINE_PRIMITIVE_IN_THIS_PATH":
        raise AssertionError("timeout scope was widened")
    if not_equivalent["external_provider_queue_fencing"] != "NOT_ESTABLISHED":
        raise AssertionError("external queue scope was widened")

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output")
    group.add_argument("--verify")
    args = parser.parse_args(argv)

    if args.output:
        result = reproduce(Path(args.output).resolve())
        print(json.dumps({
            "experiment_id": result["experiment_id"],
            "verdict": result["verdict"],
            "cases": result["cases"],
        }, sort_keys=True))
    else:
        result = verify(Path(args.verify).resolve())
        print(
            "STOP-BARRIER-COLD-001: PASS "
            f"({len(result['cases'])}/{len(result['cases'])} equivalent probes verified)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
