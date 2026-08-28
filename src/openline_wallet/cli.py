"""Command-line interface for OpenLine Wallet v0.1."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import pretty_json, strict_json_load
from .clock import parse_duration, utc_now
from .crypto import load_private_key, public_key_hex, read_public_key, save_private_key
from .demo import render_result, run_platform_exit
from .errors import WalletError
from .storage import atomic_write_json
from .wallet import Wallet, verify_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openline-wallet",
        description="User-owned authority continuity for AI agents.",
    )
    parser.add_argument(
        "--wallet",
        default=os.environ.get("OPENLINE_WALLET", ".openline-wallet"),
        help="local wallet directory (default: .openline-wallet)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create or import a principal identity")
    init.add_argument("--label", default="My OpenLine Wallet")
    init.add_argument("--root-key", help="existing 32-byte Ed25519 root key file")
    init.add_argument("--epoch-key", help="existing 32-byte Ed25519 epoch key file")

    keygen = sub.add_parser("keygen-subject", help="create a demo/integration subject key")
    keygen.add_argument("subject_id")
    keygen.add_argument("--output", required=True)

    grant = sub.add_parser("grant", help="issue one scoped expiring mandate")
    grant.add_argument("subject_id")
    grant.add_argument("scopes", nargs="+")
    grant.add_argument("--subject-key", required=True, help="subject public key or .pub file")
    grant.add_argument("--expires", default="2h")

    narrow = sub.add_parser("narrow", help="replace a current mandate with a strict subset")
    narrow.add_argument("target", help="current mandate ID or subject ID")
    narrow.add_argument("scopes", nargs="+")
    narrow.add_argument("--expires", help="optional shorter lifetime, e.g. 30m")

    revoke = sub.add_parser("revoke", help="revoke a current mandate")
    revoke.add_argument("target", help="current mandate ID or subject ID")
    revoke.add_argument("--reason", default="USER_REVOKED")

    show = sub.add_parser("show", help="show current mandates and history count")
    show.add_argument("--json", action="store_true")
    history = sub.add_parser("history", help="show the signed authority timeline")
    history.add_argument("--json", action="store_true")

    export = sub.add_parser("export", help="export receiver-verifiable evidence; never private keys")
    export.add_argument("--output", "-o")
    export.add_argument("--ttl", default="10m", help="freshness ceiling, at most 10m")

    import_command = sub.add_parser("import", help="restore local history using separately held keys")
    import_command.add_argument("bundle")
    import_command.add_argument("--root-key", required=True)
    import_command.add_argument("--epoch-key", required=True)
    import_command.add_argument("--label", default="Imported OpenLine Wallet")

    verify = sub.add_parser("verify", help="verify bundle evidence without authorizing an action")
    verify.add_argument("bundle")
    verify.add_argument("--allow-expired", action="store_true")
    verify.add_argument("--json", action="store_true")

    record = sub.add_parser("record", help="store a signed receiver decision receipt")
    record.add_argument("receipt")

    demo = sub.add_parser("demo-platform-exit", help="run PLATFORM-EXIT-001")
    demo.add_argument("--output", help="directory for demo receipts and bundles")
    demo.add_argument("--json", action="store_true")
    return parser


def _write_public_key(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise WalletError("PUBLIC_KEY_EXISTS", str(path))
    path.write_text(value + "\n", encoding="ascii")
    if os.name != "nt":
        os.chmod(path, 0o644)


def _human_summary(summary: dict) -> str:
    lines = [
        f"Principal   {summary['principal_id']}",
        f"History     {summary['head_sequence']} events / {summary['receipt_count']} receipts",
    ]
    current = [item for item in summary["mandates"] if item["status"] == "ACTIVE"]
    if not current:
        lines.append("Mandates    none current")
    for item in current:
        lines.append(
            f"Mandate     {item['subject_id']} — {', '.join(item['scopes'])} — expires {item['expires_at']}"
        )
    lines.append("Authority   receiver Gate")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    wallet_dir = Path(args.wallet)
    now = utc_now()
    try:
        if args.command == "init":
            root = load_private_key(args.root_key) if args.root_key else None
            epoch = load_private_key(args.epoch_key) if args.epoch_key else None
            wallet = Wallet.create(wallet_dir, label=args.label, root_key=root, epoch_key=epoch, now=now)
            print(f"Created      {wallet.principal_id}")
            print("Authority    wallet evidence only; receiver Gate decides effects")
            return 0

        if args.command == "keygen-subject":
            private_path = Path(args.output)
            key = Ed25519PrivateKey.generate()
            save_private_key(private_path, key)
            public_path = Path(str(private_path) + ".pub")
            _write_public_key(public_path, public_key_hex(key))
            print(f"Private key  {private_path}")
            print(f"Public key   {public_path}")
            return 0

        if args.command == "import":
            bundle = strict_json_load(args.bundle)
            wallet = Wallet.import_bundle(
                wallet_dir,
                bundle,
                root_key=load_private_key(args.root_key),
                epoch_key=load_private_key(args.epoch_key),
                label=args.label,
                now=now,
            )
            print(f"Imported     {wallet.principal_id}")
            print(f"History      {wallet.summary(now=now)['head_sequence']} events preserved")
            return 0

        if args.command == "verify":
            bundle = strict_json_load(args.bundle)
            verified, timeline = verify_bundle(
                bundle,
                now=now,
                require_fresh=not args.allow_expired,
            )
            result = {
                "status": "EVIDENCE_VALID",
                "principal_id": verified["principal"]["principal_id"],
                "head_sequence": timeline.head_sequence,
                "head_hash": timeline.head_hash,
                "current_mandates": len(timeline.current(now)),
                "wallet_policy_authority": "NONE",
                "decision_authority": "RECEIVER_GATE",
            }
            if args.json:
                sys.stdout.write(pretty_json(result))
            else:
                print("EVIDENCE_VALID")
                print(f"Principal    {result['principal_id']}")
                print(f"History      {result['head_sequence']} signed events")
                print("Effect       requires receiver Gate admission")
            return 0

        if args.command == "demo-platform-exit":
            result = run_platform_exit(args.output)
            sys.stdout.write(pretty_json(result) if args.json else render_result(result) + "\n")
            return 0 if result["verdict"] == "PLATFORM_EXIT_CONTINUITY_ENFORCED" else 1

        wallet = Wallet.open(wallet_dir)
        if args.command == "grant":
            event = wallet.grant(
                subject_id=args.subject_id,
                subject_public_key=read_public_key(args.subject_key),
                scopes=args.scopes,
                expires_at=now + parse_duration(args.expires),
                now=now,
            )
            data = event["data"]
            print(f"Granted      {data['mandate_id']}")
            print(f"Subject      {data['subject_id']}")
            print(f"Scopes       {', '.join(data['scopes'])}")
            print("Effect       eligible for receiver verification")
            return 0
        if args.command == "narrow":
            expires = now + parse_duration(args.expires) if args.expires else None
            event = wallet.narrow(args.target, scopes=args.scopes, expires_at=expires, now=now)
            print(f"Narrowed     {event['data']['predecessor_mandate_id']}")
            print(f"Successor    {event['data']['mandate_id']}")
            print(f"Scopes       {', '.join(event['data']['scopes'])}")
            return 0
        if args.command == "revoke":
            event = wallet.revoke(args.target, reason=args.reason, now=now)
            print(f"Revoked      {event['data']['mandate_id']}")
            print("Standing     lost; signature history preserved")
            return 0
        if args.command in {"show", "history"}:
            summary = wallet.summary(now=now)
            sys.stdout.write(pretty_json(summary) if args.json else _human_summary(summary) + "\n")
            return 0
        if args.command == "export":
            ttl = int(parse_duration(args.ttl).total_seconds())
            bundle = wallet.export_bundle(now=now, ttl_seconds=ttl)
            if args.output:
                atomic_write_json(args.output, bundle, mode=0o644)
                print(f"Exported     {args.output}")
                print(f"Fresh until  {bundle['expires_at']}")
                print("Private keys  excluded")
            else:
                sys.stdout.write(pretty_json(bundle))
            return 0
        if args.command == "record":
            wallet.add_receipt(strict_json_load(args.receipt))
            print(f"Recorded     {args.receipt}")
            return 0
        raise WalletError("COMMAND_UNHANDLED", args.command)
    except WalletError as exc:
        print(f"STOPPED — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
