"""Operator workflow for the MCP provider-switch proof.

The deterministic CI test exercises the same MCP tool and localhost receiver.
A public "Claude -> Codex" claim is earned only after those real hosts perform
the calls and `verify` sees their signed receiver receipts.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
import shlex
import sys
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import pretty_json, strict_json_load
from .clock import utc_now
from .crypto import load_private_key, public_key_hex, record_hash, save_private_key
from .errors import WalletError
from .storage import atomic_write_json
from .wallet import Wallet


STATE_SCHEMA = "openline.platform_exit_live.workspace.v1"
VERDICT = "PLATFORM_EXIT_LIVE_CONTINUITY_ENFORCED"

CLAUDE_SUBJECT = "provider-claude"
CODEX_SUBJECT = "provider-codex"
CLAUDE_MANDATE = "mandate-claude"
CODEX_MANDATE = "mandate-codex"
ACTION = "deploy:staging"


def _workspace(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _state_path(workspace: Path) -> Path:
    return workspace / "platform-exit-live.json"


def _load_state(workspace: Path) -> dict[str, Any]:
    state = strict_json_load(_state_path(workspace))
    if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA:
        raise WalletError("PLATFORM_EXIT_LIVE_STATE_INVALID")
    return state


def _save_state(workspace: Path, state: dict[str, Any]) -> None:
    atomic_write_json(_state_path(workspace), state)


def _receiver_receipts(workspace: Path) -> list[dict[str, Any]]:
    directory = workspace / "receiver" / "receipts"
    if not directory.exists():
        return []
    receipts = []
    for path in sorted(directory.glob("*.json")):
        value = strict_json_load(path)
        if isinstance(value, dict):
            receipts.append(value)
    return receipts


def _sync_receipts(wallet: Wallet, workspace: Path) -> int:
    existing = {record_hash(item) for item in wallet.state["receipts"]}
    added = 0
    for receipt in _receiver_receipts(workspace):
        digest = record_hash(receipt)
        if digest in existing:
            continue
        wallet.add_receipt(receipt)
        existing.add(digest)
        added += 1
    return added


def _export_current(wallet: Wallet, workspace: Path) -> None:
    atomic_write_json(
        workspace / "current.olw",
        wallet.export_bundle(),
        mode=0o644,
    )


def _bridge_args(
    workspace: Path,
    *,
    subject_key: Path,
    subject_id: str,
    mandate_id: str,
    provider_label: str,
    gate_url: str,
) -> list[str]:
    return [
        "-m",
        "openline_wallet.mcp_bridge",
        "--bundle",
        str(workspace / "current.olw"),
        "--subject-key",
        str(subject_key),
        "--subject-id",
        subject_id,
        "--mandate-id",
        mandate_id,
        "--gate-url",
        gate_url,
        "--provider-label",
        provider_label,
    ]


def _write_host_configs(workspace: Path, state: dict[str, Any]) -> None:
    python = sys.executable
    gate_url = state["gate_url"]

    claude_args = _bridge_args(
        workspace,
        subject_key=workspace / "subjects" / "claude.key",
        subject_id=CLAUDE_SUBJECT,
        mandate_id=CLAUDE_MANDATE,
        provider_label="claude",
        gate_url=gate_url,
    )
    codex_args = _bridge_args(
        workspace,
        subject_key=workspace / "subjects" / "codex.key",
        subject_id=CODEX_SUBJECT,
        mandate_id=CODEX_MANDATE,
        provider_label="codex",
        gate_url=gate_url,
    )

    claude = {
        "mcpServers": {
            "openline_wallet": {
                "type": "stdio",
                "command": python,
                "args": claude_args,
            }
        }
    }
    atomic_write_json(workspace / "claude-mcp.json", claude, mode=0o644)

    quoted_args = ", ".join(json.dumps(item) for item in codex_args)
    codex_toml = (
        "[mcp_servers.openline_wallet]\n"
        f"command = {json.dumps(python)}\n"
        f"args = [{quoted_args}]\n"
    )
    (workspace / "codex-mcp.toml").write_text(codex_toml, encoding="utf-8")

    gate_command = [
        sys.executable,
        "-m",
        "openline_wallet.gate_http",
        "--principal-id",
        state["principal_id"],
        "--root-public-key",
        state["root_public_key"],
        "--ledger",
        str(workspace / "receiver" / "effects.json"),
        "--receipts",
        str(workspace / "receiver" / "receipts"),
        "--port",
        str(state["gate_port"]),
    ]
    (workspace / "start-gate.txt").write_text(
        " ".join(shlex.quote(item) for item in gate_command) + "\n",
        encoding="utf-8",
    )


def prepare_workspace(path: str | Path, *, gate_port: int = 8765) -> dict[str, Any]:
    workspace = _workspace(path)
    if workspace.exists() and any(workspace.iterdir()):
        raise WalletError("PLATFORM_EXIT_LIVE_WORKSPACE_NOT_EMPTY", str(workspace))
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "subjects").mkdir()
    (workspace / "receiver" / "receipts").mkdir(parents=True)

    now = utc_now()
    wallet = Wallet.create(workspace / "wallet", label="Platform Exit Live", now=now)

    claude_key = Ed25519PrivateKey.generate()
    codex_key = Ed25519PrivateKey.generate()
    save_private_key(workspace / "subjects" / "claude.key", claude_key)
    save_private_key(workspace / "subjects" / "codex.key", codex_key)

    wallet.grant(
        subject_id=CLAUDE_SUBJECT,
        subject_public_key=public_key_hex(claude_key),
        scopes=[ACTION],
        expires_at=now + timedelta(hours=2),
        now=now,
        mandate_id=CLAUDE_MANDATE,
    )
    _export_current(wallet, workspace)

    state = {
        "schema": STATE_SCHEMA,
        "phase": "CLAUDE_CURRENT",
        "principal_id": wallet.principal_id,
        "root_public_key": wallet.root_public_key,
        "gate_port": gate_port,
        "gate_url": f"http://127.0.0.1:{gate_port}",
        "wallet_dir": str(workspace / "wallet"),
        "bundle": str(workspace / "current.olw"),
        "claude": {
            "subject_id": CLAUDE_SUBJECT,
            "mandate_id": CLAUDE_MANDATE,
            "subject_key": str(workspace / "subjects" / "claude.key"),
        },
        "codex": {
            "subject_id": CODEX_SUBJECT,
            "mandate_id": CODEX_MANDATE,
            "subject_key": str(workspace / "subjects" / "codex.key"),
        },
        "authority": {
            "wallet_policy_authority": "NONE",
            "decision_authority": "RECEIVER_GATE",
        },
    }
    _save_state(workspace, state)
    _write_host_configs(workspace, state)
    return state


def switch_workspace(path: str | Path) -> dict[str, Any]:
    workspace = _workspace(path)
    state = _load_state(workspace)
    if state["phase"] != "CLAUDE_CURRENT":
        raise WalletError("PLATFORM_EXIT_LIVE_PHASE_INVALID", state["phase"])

    wallet = Wallet.open(workspace / "wallet")
    _sync_receipts(wallet, workspace)

    # The first provider must have actually earned one receiver effect before exit.
    baseline = [
        receipt
        for receipt in wallet.state["receipts"]
        if receipt.get("subject_id") == CLAUDE_SUBJECT
        and receipt.get("decision") == "ALLOWED"
        and receipt.get("action") == ACTION
    ]
    if not baseline:
        raise WalletError("CLAUDE_BASELINE_RECEIPT_REQUIRED")

    now = utc_now()
    wallet.revoke(CLAUDE_SUBJECT, reason="PROVIDER_EXIT", now=now)
    codex_key = load_private_key(workspace / "subjects" / "codex.key")
    wallet.grant(
        subject_id=CODEX_SUBJECT,
        subject_public_key=public_key_hex(codex_key),
        scopes=[ACTION],
        expires_at=now + timedelta(hours=2),
        now=now,
        mandate_id=CODEX_MANDATE,
    )
    _export_current(wallet, workspace)

    state["phase"] = "CODEX_CURRENT"
    state["baseline_receipt_hash"] = record_hash(baseline[0])
    _save_state(workspace, state)
    return state


def verify_workspace(path: str | Path) -> dict[str, Any]:
    workspace = _workspace(path)
    state = _load_state(workspace)
    wallet = Wallet.open(workspace / "wallet")
    _sync_receipts(wallet, workspace)
    _export_current(wallet, workspace)

    receipts = list(wallet.state["receipts"])
    effects_path = workspace / "receiver" / "effects.json"
    effects = strict_json_load(effects_path) if effects_path.exists() else []
    if not isinstance(effects, list):
        raise WalletError("RECEIVER_LEDGER_INVALID")

    claude_allowed = [
        r for r in receipts
        if r.get("subject_id") == CLAUDE_SUBJECT and r.get("decision") == "ALLOWED"
    ]
    claude_stopped = [
        r for r in receipts
        if r.get("subject_id") == CLAUDE_SUBJECT
        and r.get("decision") == "STOPPED"
        and r.get("reason_codes") == ["MANDATE_REVOKED"]
    ]
    codex_allowed = [
        r for r in receipts
        if r.get("subject_id") == CODEX_SUBJECT and r.get("decision") == "ALLOWED"
    ]
    effect_subjects = [item.get("subject_id") for item in effects if isinstance(item, dict)]
    effect_receipts = {
        item.get("receipt_hash") for item in effects if isinstance(item, dict)
    }
    summary = wallet.summary()
    final_bundle = strict_json_load(workspace / "current.olw")

    mandates = {item["mandate_id"]: item for item in summary["mandates"]}
    stopped_hashes = {record_hash(receipt) for receipt in claude_stopped}
    final_receipt_hashes = {
        record_hash(receipt) for receipt in final_bundle["receipts"]
    }
    checks = {
        "claude_pre_switch_allowed": bool(claude_allowed),
        "claude_post_switch_stopped": bool(claude_stopped),
        "codex_post_switch_allowed": bool(codex_allowed),
        "claude_effect_recorded": CLAUDE_SUBJECT in effect_subjects,
        "codex_effect_recorded": CODEX_SUBJECT in effect_subjects,
        "denied_attempt_produced_no_effect": bool(stopped_hashes)
        and stopped_hashes.isdisjoint(effect_receipts),
        "old_mandate_is_revoked": mandates.get(CLAUDE_MANDATE, {}).get("status") == "REVOKED",
        "successor_mandate_is_active": mandates.get(CODEX_MANDATE, {}).get("status") == "ACTIVE",
        "provider_a_receipt_preserved_in_wallet": state.get("baseline_receipt_hash")
        in final_receipt_hashes,
        "wallet_has_no_policy_authority": summary["wallet_policy_authority"] == "NONE",
        "receiver_gate_owns_decision": summary["decision_authority"] == "RECEIVER_GATE",
    }
    verdict = VERDICT if all(checks.values()) else "PLATFORM_EXIT_LIVE_INCONCLUSIVE"
    result = {
        "schema": "openline.platform_exit_live.result.v1",
        "experiment_id": "PLATFORM-EXIT-LIVE-001",
        "verdict": verdict,
        "checks": checks,
        "observed": {
            "effects": len(effects),
            "receipts": len(receipts),
            "claude_allowed": len(claude_allowed),
            "claude_stopped_revoked": len(claude_stopped),
            "codex_allowed": len(codex_allowed),
            "wallet_head_sequence": summary["head_sequence"],
        },
        "authority": state["authority"],
        "claim_boundary": {
            "earned_if_real_hosts_were_used": (
                "Provider replacement preserved user-owned authority history while "
                "the superseded provider lost execution standing."
            ),
            "ci_only": (
                "CI proves the MCP transport, subject-key separation, receiver effect gate, "
                "and provider-switch state machine without claiming Claude or Codex host compatibility."
            ),
            "unearned": [
                "provider credential portability",
                "production key custody",
                "durable Gate restart recovery",
                "cross-machine revocation propagation",
                "production deployment safety",
            ],
        },
    }
    atomic_write_json(workspace / "result.json", result, mode=0o644)
    return result


def render_prepare(state: dict[str, Any], workspace: Path) -> str:
    return "\n".join(
        [
            f"Workspace     {workspace}",
            f"Principal     {state['principal_id']}",
            "Current       Claude / deploy:staging",
            f"Gate command  {workspace / 'start-gate.txt'}",
            f"Claude MCP    {workspace / 'claude-mcp.json'}",
            f"Codex MCP     {workspace / 'codex-mcp.toml'}",
            "Boundary      provider credentials are not stored or transferred",
        ]
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openline-wallet-platform-exit",
        description="PLATFORM-EXIT-LIVE-001 operator workflow.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("workspace")
    prepare.add_argument("--gate-port", type=int, default=8765)

    switch = sub.add_parser("switch")
    switch.add_argument("workspace")

    verify = sub.add_parser("verify")
    verify.add_argument("workspace")
    verify.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            workspace = _workspace(args.workspace)
            state = prepare_workspace(workspace, gate_port=args.gate_port)
            print(render_prepare(state, workspace))
            return 0
        if args.command == "switch":
            state = switch_workspace(args.workspace)
            print("Switched      Claude -> Codex")
            print("Continuity    current.olw preserved; prior receiver receipt retained")
            print("Old standing  revoked")
            print(f"Current       {state['codex']['subject_id']} / {ACTION}")
            return 0
        if args.command == "verify":
            result = verify_workspace(args.workspace)
            if args.json:
                print(pretty_json(result), end="")
            else:
                print(f"Claude before  {'ALLOWED' if result['checks']['claude_pre_switch_allowed'] else 'MISSING'}")
                print(f"Claude after   {'STOPPED / MANDATE_REVOKED' if result['checks']['claude_post_switch_stopped'] else 'MISSING'}")
                print(f"Codex after    {'ALLOWED' if result['checks']['codex_post_switch_allowed'] else 'MISSING'}")
                print(f"Verdict        {result['verdict']}")
                print("Boundary       Wallet owns continuity. Gate owns consequences.")
            return 0 if result["verdict"] == VERDICT else 1
    except WalletError as exc:
        print(f"STOPPED — {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
