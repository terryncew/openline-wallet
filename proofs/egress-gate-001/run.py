"""EGRESS-GATE-001 — MCP consequence boundary falsifier.

The target tool keeps its own call witness. OpenLine receipts are evaluated
separately. A broad schema-valid but owner-disallowed call must not reach the
target, while the same call reaches it in the no-gate negative control.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.crypto import public_key_hex, record_hash, verify_record
from openline_wallet.effect_closure import EffectGate
from openline_wallet.mcp_egress import (
    HTTPJSONRPCDownstream,
    MCPConsequenceGate,
    MCP_CONTRACT_SCHEMA,
    MCP_2026_PROTOCOL,
    ReceiverContract,
    parse_mcp_tool_call,
)
from openline_wallet.receiver import create_presentation
from openline_wallet.storage import atomic_write_json
from openline_wallet.wallet import Wallet

WALLET_BASE = "48b4a9e8f6f8e6514963a3afb27beea717077c81"
VERDICT = "EGRESS_GATE_CONSEQUENCE_BOUNDARY_ENFORCED"
HERE = Path(__file__).resolve().parent


def headers(name: str = "account.set_limit") -> dict[str, str]:
    return {
        "Mcp-Protocol-Version": MCP_2026_PROTOCOL,
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
    }


def request(amount: int, request_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": "account.set_limit", "arguments": {"amount": amount}},
    }


def broad_schema_valid(body: dict) -> bool:
    try:
        amount = body["params"]["arguments"]["amount"]
    except (KeyError, TypeError):
        return False
    return isinstance(amount, int) and not isinstance(amount, bool) and 0 <= amount <= 1_000_000


def witness_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_target(witness: Path) -> tuple[subprocess.Popen, str]:
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "fake_tool.py"), "--port", str(port), "--witness", str(witness)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    health = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + 5
    while time.time() < deadline:
        if proc.poll() is not None:
            detail = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"fake target exited early: {detail}")
        try:
            with urlopen(health, timeout=0.2) as response:
                if response.status == 200:
                    return proc, f"http://127.0.0.1:{port}/mcp"
        except Exception:
            time.sleep(0.05)
    proc.terminate()
    raise RuntimeError("fake target did not become ready")


def make_contract() -> ReceiverContract:
    return ReceiverContract.from_mapping({
        "schema": MCP_CONTRACT_SCHEMA,
        "contract_id": "account-limit-v1",
        "wallet_scope": "mcp:account.set_limit",
        "tool_name": "account.set_limit",
        "rule": {
            "rule_id": "amount-0-5000",
            "parameter": "amount",
            "minimum": 0,
            "maximum": 5000,
        },
    })


def reproduce(output: Path) -> dict:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    receipts = output / "receipts"
    witness = output / "target-witness.jsonl"

    target, target_url = start_target(witness)
    try:
        wallet = Wallet.create(output / "wallet")
        subject_id = "worker"
        subject_key = Ed25519PrivateKey.generate()
        mandate_id = "mcp-account-limit-worker"
        scope = "mcp:account.set_limit"
        wallet.grant(
            subject_id=subject_id,
            subject_public_key=public_key_hex(subject_key),
            scopes=[scope],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            mandate_id=mandate_id,
        )

        gate = EffectGate("egress-gate-001")
        gate.pin_principal(wallet.principal_id, wallet.root_public_key)
        contract = make_contract()
        downstream = HTTPJSONRPCDownstream(target_url)
        runtime = MCPConsequenceGate(gate, contract, receipts, downstream)

        def presentation() -> dict:
            bundle = wallet.export_bundle()
            gate.admit_bundle(bundle)
            challenge = gate.issue_challenge(
                principal_id=wallet.principal_id,
                subject_id=subject_id,
                action=scope,
            )
            return create_presentation(
                bundle=bundle,
                mandate_id=mandate_id,
                subject_id=subject_id,
                subject_key=subject_key,
                action=scope,
                receiver_challenge=challenge,
            )

        safe_body = request(2000, 1)
        safe = runtime.execute(headers=headers(), body=safe_body, presentation=presentation())
        if safe["decision"] != "EXECUTED" or len(witness_rows(witness)) != 1:
            raise AssertionError("safe call did not execute exactly once")

        bad_body = request(50000, 2)
        if not broad_schema_valid(bad_body):
            raise AssertionError("negative candidate must pass the broad schema")
        bad = runtime.execute(headers=headers(), body=bad_body, presentation=presentation())
        if bad["decision"] != "REJECTED" or bad["reason_codes"] != ["CONTRACT_PARAMETER_ABOVE_MAXIMUM"]:
            raise AssertionError("out-of-contract call was not rejected by receiver contract")
        calls_after_bad = len(witness_rows(witness))
        if calls_after_bad != 1:
            raise AssertionError("rejected out-of-contract call leaked to target")

        wallet.revoke(subject_id)
        revoked = runtime.execute(headers=headers(), body=request(2000, 3), presentation=presentation())
        if revoked["decision"] != "REJECTED" or "MANDATE_REVOKED" not in revoked["reason_codes"]:
            raise AssertionError("revoked worker was not stopped")
        calls_after_revocation = len(witness_rows(witness))
        if calls_after_revocation != 1:
            raise AssertionError("revoked call leaked to target")

        # Negative control: bypass OpenLine. The exact schema-valid bad proposal
        # reaches the target and changes the target-owned witness.
        direct = downstream(parse_mcp_tool_call(headers(), bad_body), headers())
        if direct.get("result", {}).get("applied") != 50000:
            raise AssertionError("negative control did not reach target")
        calls_after_control = len(witness_rows(witness))
        if calls_after_control != 2:
            raise AssertionError("negative control witness did not advance exactly once")

        named = {"safe": safe, "out_of_contract": bad, "revoked": revoked}
        receipt_hashes = {}
        for name, result in named.items():
            valid, reason = verify_record(result["receipt"], expected_public_key=gate.public_key)
            if (valid, reason) != (True, None):
                raise AssertionError(f"{name} receipt signature invalid: {reason}")
            receipt_hashes[name] = result["receipt_hash"]
            atomic_write_json(output / f"{name}-receipt.json", result["receipt"], mode=0o644)

        result = {
            "schema": "openline.egress_gate_001.result.v1",
            "verdict": VERDICT,
            "wallet_base": WALLET_BASE,
            "gate_id": gate.gate_id,
            "gate_public_key": gate.public_key,
            "contract_id": contract.contract_id,
            "contract_hash": contract.contract_hash,
            "rule_id": contract.rule_id,
            "broad_schema_maximum": 1_000_000,
            "owner_contract_maximum": contract.maximum,
            "safe_amount": 2000,
            "out_of_contract_amount": 50000,
            "out_of_contract_schema_valid": True,
            "safe_effect_applied": safe["effect_applied"],
            "out_of_contract_effect_applied": bad["effect_applied"],
            "revoked_effect_applied": revoked["effect_applied"],
            "target_calls_after_safe": 1,
            "target_calls_after_out_of_contract": calls_after_bad,
            "target_calls_after_revocation": calls_after_revocation,
            "target_calls_after_negative_control": calls_after_control,
            "receipt_hashes": receipt_hashes,
            "target_witness_hash": record_hash({"rows": witness_rows(witness)}),
        }
        atomic_write_json(output / "result.json", result, mode=0o644)
        return result
    finally:
        target.terminate()
        try:
            target.wait(timeout=2)
        except subprocess.TimeoutExpired:
            target.kill()
            target.wait(timeout=2)


def verify(output: Path) -> dict:
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    if result.get("verdict") != VERDICT:
        raise AssertionError("verdict mismatch")
    if result.get("wallet_base") != WALLET_BASE:
        raise AssertionError("wallet base mismatch")
    if result.get("out_of_contract_schema_valid") is not True:
        raise AssertionError("negative candidate was not schema valid")
    if result.get("safe_effect_applied") is not True:
        raise AssertionError("safe effect missing")
    if result.get("out_of_contract_effect_applied") is not False:
        raise AssertionError("bad effect leaked")
    if result.get("revoked_effect_applied") is not False:
        raise AssertionError("revoked effect leaked")
    if result.get("target_calls_after_safe") != 1:
        raise AssertionError("safe target count mismatch")
    if result.get("target_calls_after_out_of_contract") != 1:
        raise AssertionError("bad call reached target")
    if result.get("target_calls_after_revocation") != 1:
        raise AssertionError("revoked call reached target")
    if result.get("target_calls_after_negative_control") != 2:
        raise AssertionError("negative control did not demonstrate leakage")

    rows = witness_rows(output / "target-witness.jsonl")
    if record_hash({"rows": rows}) != result.get("target_witness_hash"):
        raise AssertionError("target witness hash mismatch")
    if [row.get("amount") for row in rows] != [2000, 50000]:
        raise AssertionError("target witness contents mismatch")

    gate_public_key = result["gate_public_key"]
    for name, expected_hash in result["receipt_hashes"].items():
        receipt = json.loads((output / f"{name}-receipt.json").read_text(encoding="utf-8"))
        if record_hash(receipt) != expected_hash:
            raise AssertionError(f"{name} receipt hash mismatch")
        valid, reason = verify_record(receipt, expected_public_key=gate_public_key)
        if (valid, reason) != (True, None):
            raise AssertionError(f"{name} receipt invalid: {reason}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output")
    group.add_argument("--verify")
    args = parser.parse_args(argv)
    if args.output:
        result = reproduce(Path(args.output).resolve())
    else:
        result = verify(Path(args.verify).resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
