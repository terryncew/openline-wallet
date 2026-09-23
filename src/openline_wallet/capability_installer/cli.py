"""capinstall: local receiver-controlled capability installer (preview)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openline_wallet.capability_installer import (
    InstallerError,
    accept_package,
    import_package,
    inspect_package,
    invoke,
    revoke,
    settle_demo,
)
from openline_wallet.capability_installer import installer as I
from openline_wallet.capability_installer.battery import battery_digest


def _out(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True))


def cmd_init(args) -> int:
    try:
        info = I.init_home()
    except InstallerError as e:
        print("refused [%s]: %s" % (e.code, e.message), file=sys.stderr)
        return 2
    print("initialized local installer home: %s" % info["home"])
    print("buyer principal: %s" % info["buyer_principal"])
    print("demo seller key: %s (local demo key, single host)" % info["demo_seller_public_key"][:16])
    print("example package: %s" % info["example_package"])
    print("pinned acceptance battery: %s" % battery_digest()[:16])
    return 0


def cmd_inspect(args) -> int:
    try:
        info = inspect_package(args.pkgdir)
    except InstallerError as e:
        print("refused [%s]: %s" % (e.code, e.message), file=sys.stderr)
        return 2
    sig = info.pop("signature")
    trust = info.pop("trust_note")
    _out(info)
    print("signature valid: %s (%s)" % (sig["valid"], sig["detail"]))
    print("signer: %s" % sig["signer"])
    print("note: %s" % trust)
    return 0


def cmd_accept(args) -> int:
    checks = [c.strip() for c in args.checks.split(",")] if args.checks else None
    try:
        dec = accept_package(args.pkgdir, checks=checks, threshold=args.threshold)
    except InstallerError as e:
        print("refused [%s]: %s" % (e.code, e.message), file=sys.stderr)
        return 2
    if dec["decision"] == "accepted":
        print("ACCEPTED  package %s" % dec["package_hash"][:16])
    else:
        print("REJECTED  package %s" % dec["package_hash"][:16])
    print("buyer battery score: %d/12, buyer threshold: %d/12" % (dec["score"], dec["threshold"]))
    for tid, t in dec["tests"].items():
        print("  %s: %s — %s" % (tid, "pass" if t["pass"] else "FAIL", t["detail"]))
    print("this score is the buyer's own acceptance result, not universal correctness")
    return 0 if dec["decision"] == "accepted" else 2


def cmd_import(args) -> int:
    try:
        info = import_package(args.pkgdir)
    except InstallerError as e:
        print("refused [%s]: %s" % (e.code, e.message), file=sys.stderr)
        return 2
    print("imported package %s" % info["package_hash"][:16])
    print("lineage: %s" % info["lineage"])
    print("invoke authority: %s" % info["invoke_authority"])
    return 0


def cmd_invoke(args) -> int:
    try:
        info = invoke(args.package_hash, args.fixture)
    except InstallerError as e:
        print("refused [%s]: %s" % (e.code, e.message), file=sys.stderr)
        return 2
    print("invoked through the receiver boundary")
    print("receipt: %s" % info["receipt_path"])
    print("output digest: %s" % info["output_digest"][:16])
    print("fault hint: %s" % info["fault_hint"])
    return 0


def cmd_revoke(args) -> int:
    try:
        info = revoke(args.package_hash)
    except InstallerError as e:
        print("refused [%s]: %s" % (e.code, e.message), file=sys.stderr)
        return 2
    print("revoked invoke mandate %s" % info["mandate_id"])
    print("note: %s" % info["note"])
    return 0


def cmd_settle(args) -> int:
    if not args.demo:
        print("refused: settlement is an optional simulated demo; pass --demo", file=sys.stderr)
        return 2
    try:
        info = settle_demo(args.package_hash)
    except InstallerError as e:
        print("refused [%s]: %s" % (e.code, e.message), file=sys.stderr)
        return 2
    print("SIMULATED settlement: %d %s" % (info["amount"], info["currency"]))
    print("buyer %d, seller %d" % (info["buyer_balance"], info["seller_balance"]))
    print("note: %s" % info["note"])
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="capinstall",
        description="local receiver-controlled capability installer (developer preview)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="one-time local setup: home, keys, example package")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("inspect", help="describe a package without executing it")
    p.add_argument("pkgdir")
    p.set_defaults(fn=cmd_inspect)

    p = sub.add_parser("accept", help="run buyer-selected acceptance checks")
    p.add_argument("pkgdir")
    p.add_argument("--checks", default="T1,T2,T3,T4,T5",
                   help="comma-separated buyer-selected checks")
    p.add_argument("--threshold", type=int, default=10,
                   help="buyer-declared accuracy threshold, of 12")
    p.set_defaults(fn=cmd_accept)

    p = sub.add_parser("import", help="import only the exact accepted package")
    p.add_argument("pkgdir")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("invoke", help="invoke through the receiver boundary")
    p.add_argument("package_hash")
    p.add_argument("fixture", help="fresh fixture id (w01..w04) or path")
    p.set_defaults(fn=cmd_invoke)

    p = sub.add_parser("revoke", help="revoke future invocation through the receiver")
    p.add_argument("package_hash")
    p.set_defaults(fn=cmd_revoke)

    p = sub.add_parser("settle", help="optional SIMULATED settlement demo")
    p.add_argument("package_hash")
    p.add_argument("--demo", action="store_true",
                   help="acknowledge this is a simulated demo")
    p.set_defaults(fn=cmd_settle)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except InstallerError as e:
        print("refused [%s]: %s" % (e.code, e.message), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - surface unexpected failures plainly
        print("error [%s]: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
