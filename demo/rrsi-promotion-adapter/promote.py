#!/usr/bin/env python3
"""Thin RRSI-to-OpenLine promotion adapter (developer preview).

An optimizer (here: the RRSI search process, external to this repo) may
propose a harness change. Only the operator-owned receiver may install that
exact change into the protected runtime.

Reuses the merged capability-installer machinery for the hard parts:
  - openline_wallet.canonical / crypto   (hashes, signatures, package identity)
  - capability_installer.validate_package (one shared package-validation step:
      artifact hash, signature, signed-manifest canonical match, seller
      principal <-> signing key binding)
  - openline_wallet.wallet              (invoke mandates, revocation)

The adapter adds the promotion-specific pieces the installer does not have:
  - export of a git candidate with exact base commit + artifact hash
  - operator-owned protected checks (base pinning, component allowlist,
    interface/dependency gate, clean-env smoke check)
  - a guarded deployment dir: only the operator identity may write it
  - invocation of the installed component in a subprocess with a clean
    environment (no wallet, no receiver credentials on the path)

Scope: one small harness component, local demo, single host. See README.md
and SUPPORTED.md for what this does and does not establish.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openline_wallet.canonical import canonical_json
from openline_wallet.crypto import (
    Ed25519PrivateKey,
    load_private_key,
    principal_id,
    public_key_hex,
    save_private_key,
    sha256_hex,
    sign_record,
)
from openline_wallet.wallet import Wallet, WalletError

from openline_wallet.capability_installer.installer import (  # noqa: E402
    InstallerError,
    package_hash,
    validate_package,
)

HOME_ENV = "RRSI_ADAPTER_HOME"
INVOKE_SCOPE = "harness:invoke"
MANIFEST_SCHEMA = "rrsi-promotion.manifest.v1"
COMPONENT_ALLOWLIST = {
    "third_party/harbor_terminus2/terminus_json_plain_parser.py",
}
ARTIFACT_FILENAME = "terminus_json_plain_parser.py"
# stdlib modules the promoted component may import; anything else fails C3.
STDLIB_ALLOW = {
    "json", "re", "dataclasses", "typing", "abc", "collections", "copy",
    "enum", "functools", "itertools", "math", "string", "textwrap", "io",
    "os", "sys", "time", "datetime", "hashlib", "pathlib", "inspect",
    "numbers", "decimal", "fractions", "statistics", "operator",
}
UPSTREAM_REPO = "https://github.com/google-research/rrsi"
UPSTREAM_PIN = "e4d1a7a0388e02b388bc40eb0a125fcfc7123f8d"


# -- small helpers ------------------------------------------------------------


def _home(h: Path | None) -> Path:
    return h or Path(os.environ.get(HOME_ENV, ".rrsi-adapter-home"))


def _read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=rrsi-adapter@localhost",
         "-c", "user.name=rrsi-adapter", *args],
        cwd=str(repo), capture_output=True, text=True,
    )


def _git_out(repo: Path, *args: str) -> str:
    r = _git(repo, *args)
    if r.returncode != 0:
        raise InstallerError("GIT_FAILED", "git %s: %s" % (" ".join(args), r.stderr.strip()[-300:]))
    return r.stdout.strip()


def _clean_env() -> dict:
    """Minimal environment for executing the untrusted component: no wallet,
    no receiver credentials, no home directory that could leak authority."""
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return env


_RUNNER = r'''
import importlib.util, json, sys
comp_path, input_path = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("promoted_component", comp_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
parser = mod.TerminusJSONPlainParser()
response = open(input_path).read()
res = parser.parse_response(response)
out = {
    "n_commands": len(res.commands),
    "keystrokes": [c.keystrokes for c in res.commands],
    "is_task_complete": res.is_task_complete,
    "error": res.error,
    "warning": res.warning,
    "analysis": res.analysis,
    "plan": res.plan,
}
print(json.dumps(out, sort_keys=True))
'''


def _run_component(component_path: Path, input_path: Path) -> dict:
    """Run the component in a subprocess with a clean environment. The
    component never sees the operator's wallet, keys, or home."""
    env = _clean_env()
    for leaked in ("CAPINSTALL_HOME", HOME_ENV, "HOME", "XDG_CONFIG_HOME"):
        env.pop(leaked, None)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(_RUNNER)
        runner = f.name
    try:
        r = subprocess.run(
            [sys.executable, runner, str(component_path), str(input_path)],
            capture_output=True, text=True, env=env, timeout=60,
        )
    finally:
        os.unlink(runner)
    if r.returncode != 0:
        raise InstallerError(
            "COMPONENT_RUN_FAILED",
            "component exited %d: %s" % (r.returncode, r.stderr.strip()[-500:]),
        )
    return json.loads(r.stdout)


def _operator_key(h: Path):
    return load_private_key(h / "keys" / "operator.key")


def _operator_pub(h: Path) -> str:
    return json.loads((h / "keys" / "operator.pub.json").read_text())["public_key"]


# -- init ---------------------------------------------------------------------


def cmd_init(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    if (h / "wallet").exists():
        raise InstallerError("ALREADY_INITIALIZED", "home exists: %s" % h)
    for d in ("wallet", "keys", "deployment/components", "deployment/lineage", "receipts"):
        (h / d).mkdir(parents=True)
    wallet = Wallet.create(h / "wallet", label="rrsi promotion operator wallet")
    operator_key = Ed25519PrivateKey.generate()
    save_private_key(h / "keys" / "operator.key", operator_key)
    _write_json(h / "keys" / "operator.pub.json",
                {"public_key": public_key_hex(operator_key),
                 "principal": principal_id(public_key_hex(operator_key)),
                 "role": "operator"})
    # The optimizer is a separate identity. In this single-host demo the
    # operator generates its key to stand in for the external optimizer;
    # the optimizer identity is what signs candidate packages, and it has
    # no write authority over the protected deployment dir.
    optimizer_key = Ed25519PrivateKey.generate()
    save_private_key(h / "keys" / "optimizer.key", optimizer_key)
    _write_json(h / "keys" / "optimizer.pub.json",
                {"public_key": public_key_hex(optimizer_key),
                 "principal": principal_id(public_key_hex(optimizer_key)),
                 "role": "optimizer"})
    _write_json(h / "deployment" / "deployment.json",
                {"base_commit": None, "components": {}})
    _write_json(h / "decisions.json", {})
    _write_json(h / "mandates.json", {})
    print("initialized adapter home: %s" % h)
    print("operator principal: %s" % wallet.principal_id)
    print("optimizer principal: %s" % principal_id(public_key_hex(optimizer_key)))
    print("next: set the deployment base with `set-base --commit <base>`")
    return 0


def cmd_set_base(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    dep_path = h / "deployment" / "deployment.json"
    dep = _read_json(dep_path, None)
    if dep is None:
        raise InstallerError("NOT_INITIALIZED", "run init first")
    dep["base_commit"] = args.commit
    _write_json(dep_path, dep)
    print("deployment base pinned to %s" % args.commit)
    return 0


# -- export -------------------------------------------------------------------


def _interface_of(artifact_bytes: bytes) -> dict:
    """Interface extracted by parsing, never by executing the artifact."""
    tree = ast.parse(artifact_bytes.decode("utf-8"))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    entry = next((c for c in classes if c.name == "TerminusJSONPlainParser"), None)
    if entry is None:
        raise InstallerError(
            "NO_ENTRY_POINT",
            "no TerminusJSONPlainParser class found; classes present: %s"
            % [c.name for c in classes],
        )
    methods = [n.name for n in entry.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return {
        "entry_point": "TerminusJSONPlainParser.parse_response",
        "signature": "parse_response(response: str) -> ParseResult",
        "methods": methods,
        "imports": sorted(imports),
    }


def cmd_export(args) -> int:
    """Export an optimizer candidate as a signed package.

    The candidate is a git commit; the base is the incumbent commit the
    candidate claims to descend from. Both are pinned by hash.
    """
    repo = Path(args.repo)
    base, candidate, component = args.base, args.candidate, args.component
    if component not in COMPONENT_ALLOWLIST:
        raise InstallerError("COMPONENT_NOT_SUPPORTED",
                             "adapter promotes only allowlisted components")
    if _git(repo, "merge-base", "--is-ancestor", base, candidate).returncode != 0:
        raise InstallerError("NOT_A_DESCENDANT",
                             "candidate %s does not descend from base %s" % (candidate, base))
    artifact_bytes = _git_out(repo, "show", "%s:%s" % (candidate, component)).encode()
    # byte-verify against the pinned upstream clone when the component is the
    pkgdir = Path(args.out)
    if pkgdir.exists():
        shutil.rmtree(pkgdir)
    pkgdir.mkdir(parents=True)
    (pkgdir / ARTIFACT_FILENAME).write_bytes(artifact_bytes)
    optimizer_pub = json.loads(
        (_home(Path(args.home) if args.home else None) / "keys" / "optimizer.pub.json").read_text()
    )["public_key"]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "name": "rrsi-harness-component/terminus-json-plain-parser",
        "version": args.version,
        "component": component,
        "base_commit": _git_out(repo, "rev-parse", base),
        "candidate_commit": _git_out(repo, "rev-parse", candidate),
        "artifact": ARTIFACT_FILENAME,
        "artifact_sha256": sha256_hex(artifact_bytes),
        "interface": _interface_of(artifact_bytes),
        "declared_scope": "parse terminus JSON plain agent responses into commands",
        "dependencies": [],
        "runtime": "python3, stdlib only",
        "requested_permissions": [],
        "seller": principal_id(optimizer_pub),
        "seller_role": "optimizer",
        "provenance": {
            "upstream_repo": UPSTREAM_REPO,
            "upstream_commit": UPSTREAM_PIN,
            "candidate_note": args.candidate_note,
        },
    }
    optimizer_key = load_private_key(
        _home(Path(args.home) if args.home else None) / "keys" / "optimizer.key")
    sig = sign_record({"manifest": {k: v for k, v in manifest.items()}}, optimizer_key)
    manifest["signatures"] = [{"signer": optimizer_pub, "record": sig}]
    (pkgdir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print("exported candidate package: %s" % pkgdir)
    print("base_commit:      %s" % manifest["base_commit"])
    print("candidate_commit: %s" % manifest["candidate_commit"])
    print("artifact_sha256:  %s" % manifest["artifact_sha256"])
    print("signed by optimizer principal %s" % manifest["seller"])
    return 0


# -- accept -------------------------------------------------------------------


def _smoke_input() -> str:
    return json.dumps({
        "analysis": "list the working directory",
        "plan": "run ls, then finish",
        "commands": [
            {"keystrokes": "ls -la\n", "duration": 1.2},
            {"keystrokes": "echo done\n", "duration": 0.4},
        ],
        "task_complete": False,
    })


def _operator_checks(h: Path, validated: dict) -> dict:
    """Operator-owned protected checks. They live in the adapter home, outside
    the candidate's writable surface; the candidate cannot alter them."""
    manifest = validated["manifest"]
    artifact_bytes = validated["artifact_bytes"]
    dep = _read_json(h / "deployment" / "deployment.json", {})
    results = {}

    # C1: base pinning. The candidate must descend from the exact incumbent
    # the operator deployed. A changed base is refused.
    base = dep.get("base_commit")
    if not base:
        results["C1_base_pinned"] = (False, "operator has not pinned a deployment base")
    elif manifest.get("base_commit") != base:
        results["C1_base_pinned"] = (
            False,
            "candidate base %s != deployed base %s" % (manifest.get("base_commit"), base),
        )
    else:
        results["C1_base_pinned"] = (True, "candidate built on deployed base %s" % base)

    # C2: component allowlist.
    results["C2_component_allowlisted"] = (
        manifest.get("component") in COMPONENT_ALLOWLIST,
        "component %s" % manifest.get("component"),
    )

    # C3: interface and dependency gate. Entry point present, imports within
    # the stdlib allowlist.
    iface = _interface_of(artifact_bytes)
    bad_imports = [i for i in iface["imports"] if i not in STDLIB_ALLOW]
    if "parse_response" not in iface["methods"]:
        results["C3_interface"] = (False, "parse_response entry point missing")
    elif bad_imports:
        results["C3_interface"] = (False, "non-allowlisted imports: %s" % bad_imports)
    else:
        results["C3_interface"] = (True, "entry point present; imports %s" % iface["imports"])

    # C4: clean-env smoke check. The candidate runs in a subprocess with no
    # wallet, no keys, no receiver credentials in the environment.
    if all(v[0] for v in results.values()):
        with tempfile.TemporaryDirectory() as td:
            comp = Path(td) / ARTIFACT_FILENAME
            comp.write_bytes(artifact_bytes)
            inp = Path(td) / "smoke.json"
            inp.write_text(_smoke_input())
            try:
                out = _run_component(comp, inp)
            except InstallerError as e:
                results["C4_smoke"] = (False, "smoke run failed: %s" % e.message)
            else:
                ok = (out["n_commands"] == 2 and out["error"] == ""
                      and out["is_task_complete"] is False
                      and out["keystrokes"] == ["ls -la\n", "echo done\n"])
                results["C4_smoke"] = (ok, "parsed 2 commands, no error" if ok else "unexpected: %s" % out)
    else:
        results["C4_smoke"] = (False, "skipped: earlier check failed")
    return results


def cmd_accept(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    validated = validate_package(args.pkgdir)
    ph = validated["package_hash"]
    checks = _operator_checks(h, validated)
    passed = all(v[0] for v in checks.values())
    decision = {
        "decision": "accepted" if passed else "rejected",
        "package_hash": ph,
        "artifact_sha256": validated["manifest"]["artifact_sha256"],
        "base_commit": validated["manifest"]["base_commit"],
        "candidate_commit": validated["manifest"].get("candidate_commit"),
        "component": validated["manifest"].get("component"),
        "signer": validated["signer"],
        "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in checks.items()},
    }
    decisions = _read_json(h / "decisions.json", {})
    decisions[ph] = decision
    _write_json(h / "decisions.json", decisions)
    _write_json(h / "receipts" / ("acceptance_%s.json" % ph[:16]), decision)
    print(("ACCEPTED" if passed else "REJECTED"), " package %s" % ph[:16])
    for cid, c in decision["checks"].items():
        print("  %s: %s — %s" % (cid, "pass" if c["pass"] else "FAIL", c["detail"]))
    print("these are the operator's own checks, not a claim about the candidate")
    return 0 if passed else 2


# -- install (operator only) ---------------------------------------------------


def _guarded_deployment_write(h: Path, caller: str, rel: str, data: bytes) -> Path:
    """The only write path into the protected deployment dir. The caller must
    be the operator identity; the optimizer has no write authority here."""
    if caller != "operator":
        raise InstallerError(
            "OPTIMIZER_WRITE_DENIED",
            "principal '%s' has no write authority over the protected deployment; "
            "only the operator may install" % caller,
        )
    dest = h / "deployment" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def cmd_install(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    pkgdir = Path(args.pkgdir)
    manifest = json.loads((pkgdir / "manifest.json").read_text())
    artifact_bytes = (pkgdir / manifest["artifact"]).read_bytes()
    ph = package_hash(artifact_bytes, manifest)
    decisions = _read_json(h / "decisions.json", {})
    dec = decisions.get(ph)
    if dec is None or dec.get("decision") != "accepted":
        raise InstallerError(
            "IMPORT_REFUSED",
            "no acceptance decision for this exact package; install is only "
            "allowed after the operator's checks accept it",
        )
    dep = _read_json(h / "deployment" / "deployment.json", {})
    if manifest.get("base_commit") != dep.get("base_commit"):
        raise InstallerError(
            "BASE_CHANGED",
            "deployment base moved since acceptance; re-export and re-accept",
        )
    _guarded_deployment_write(h, "operator", "components/" + ARTIFACT_FILENAME, artifact_bytes)
    _write_json(h / "deployment" / "lineage" / (ph[:16] + ".json"), {
        "package_hash": ph,
        "artifact_sha256": sha256_hex(artifact_bytes),
        "base_commit": manifest.get("base_commit"),
        "candidate_commit": manifest.get("candidate_commit"),
        "component": manifest.get("component"),
        "from": manifest.get("seller"),
        "acceptance": dec,
    })
    dep["components"][manifest["component"]] = {
        "package_hash": ph,
        "artifact_sha256": sha256_hex(artifact_bytes),
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(h / "deployment" / "deployment.json", dep)
    wallet = Wallet.open(h / "wallet")
    subject_key = Ed25519PrivateKey.generate()
    subject_key_path = h / "keys" / ("subject_%s.key" % ph[:16])
    if subject_key_path.exists():
        # a prior install of this exact package was revoked; its subject key
        # is dead, so rotate it rather than failing the reinstall
        subject_key_path.unlink()
    save_private_key(subject_key_path, subject_key)
    subject_id = "harness:%s" % ph[:16]
    try:
        mandate = wallet.grant(
            subject_id=subject_id,
            subject_public_key=public_key_hex(subject_key),
            scopes=[INVOKE_SCOPE],
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    except WalletError as e:
        raise InstallerError("MANDATE_GRANT_FAILED", str(e))
    mandates = _read_json(h / "mandates.json", {})
    mid = mandate["data"]["mandate_id"]
    mandates[ph] = {"mandate_id": mid, "subject_id": subject_id, "scope": INVOKE_SCOPE}
    _write_json(h / "mandates.json", mandates)
    print("installed package %s into the protected runtime" % ph[:16])
    print("invoke authority: mandate %s, scope %s" % (mid, INVOKE_SCOPE))
    print("only this exact artifact hash may run under that mandate")
    return 0


# -- invoke -------------------------------------------------------------------


def _active_mandate(h: Path, mandate_id: str) -> dict:
    wallet = Wallet.open(h / "wallet")
    now = datetime.now(timezone.utc)
    active = {m["mandate_id"]: m for m in wallet.timeline().current(now)}
    m = active.get(mandate_id)
    if m is None:
        raise InstallerError(
            "MANDATE_REVOKED",
            "no active invoke mandate; it was revoked or expired, and the "
            "receiver refuses invocation",
        )
    if INVOKE_SCOPE not in m.get("scopes", []):
        raise InstallerError("ACTION_OUTSIDE_MANDATE", "mandate lacks %s" % INVOKE_SCOPE)
    return m


def cmd_invoke(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    mandates = _read_json(h / "mandates.json", {})
    binding = mandates.get(args.package_hash)
    if binding is None:
        raise InstallerError("NOT_IMPORTED", "component was never installed here")
    _active_mandate(h, binding["mandate_id"])
    decisions = _read_json(h / "decisions.json", {})
    dec = decisions.get(args.package_hash)
    if dec is None or dec.get("decision") != "accepted":
        raise InstallerError("INVOCATION_REFUSED", "no acceptance on record")
    deployed = h / "deployment" / "components" / ARTIFACT_FILENAME
    deployed_bytes = deployed.read_bytes()
    # substitution check: the bytes on disk must still be the accepted bytes.
    if sha256_hex(deployed_bytes) != dec["artifact_sha256"]:
        raise InstallerError(
            "INVOCATION_BINDING_MISMATCH",
            "deployed artifact no longer matches the accepted hash; refusing",
        )
    inp = Path(args.input)
    if not inp.exists():
        raise InstallerError("NO_INPUT", "fresh work input not found: %s" % inp)
    out = _run_component(deployed, inp)
    loaded_digest = sha256_hex(deployed_bytes)
    receipt = {
        "package_hash": args.package_hash,
        "component": dec["component"],
        "input_sha256": sha256_hex(inp.read_bytes()),
        "output_sha256": sha256_hex(canonical_json(out)),
        "loaded_digest": loaded_digest,
        "operator": principal_id(_operator_pub(h)),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    sig = sign_record(receipt, _operator_key(h))
    _write_json(h / "receipts" / ("invocation_%s.json" % args.package_hash[:16]),
                {"receipt": receipt, "signature": sig})
    print("invoked through the receiver boundary")
    print("loaded digest: %s" % loaded_digest)
    print("commands parsed: %d, error: %r" % (out["n_commands"], out["error"]))
    print("signed invocation receipt recorded")
    return 0


def cmd_revoke(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    mandates = _read_json(h / "mandates.json", {})
    binding = mandates.get(args.package_hash)
    if binding is None:
        raise InstallerError("NOT_IMPORTED", "component was never installed here")
    wallet = Wallet.open(h / "wallet")
    try:
        wallet.revoke(binding["subject_id"], reason="OPERATOR_REVOKED")
    except WalletError as e:
        raise InstallerError("REVOKE_FAILED", str(e))
    print("revoked invoke mandate for package %s" % args.package_hash[:16])
    print("the next gated invocation will be refused")
    return 0


# -- bypass demonstrations ----------------------------------------------------


def cmd_bypass(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    if args.kind == "optimizer-write":
        # The optimizer identity attempts a direct write into the protected
        # deployment dir, bypassing the operator's install path.
        _guarded_deployment_write(h, "optimizer", "components/" + ARTIFACT_FILENAME,
                                  b"# optimizer direct write\n")
        print("unexpected: write allowed")
        return 1
    if args.kind == "substitute":
        # Tamper with the deployed bytes outside the install path, then try
        # to invoke. The receiver detects the hash mismatch.
        deployed = h / "deployment" / "components" / ARTIFACT_FILENAME
        deployed.write_bytes(b"# substituted artifact\n")
        print("deployed bytes substituted outside the install path")
        return 0
    raise InstallerError("UNKNOWN_BYPASS", args.kind)


# -- cli ----------------------------------------------------------------------


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="promote",
                                description="thin RRSI-to-OpenLine promotion adapter")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="one-time operator setup")
    s.add_argument("--home", default=None)
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("set-base", help="pin the deployment base commit")
    s.add_argument("--home", default=None)
    s.add_argument("--commit", required=True)
    s.set_defaults(fn=cmd_set_base)

    s = sub.add_parser("export", help="export an optimizer candidate as a package")
    s.add_argument("--home", default=None)
    s.add_argument("--repo", required=True)
    s.add_argument("--base", required=True)
    s.add_argument("--candidate", required=True)
    s.add_argument("--component", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--version", default="candidate")
    s.add_argument("--candidate-note", required=True,
                   help="honest label for what this candidate is")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("accept", help="run the operator's protected checks")
    s.add_argument("--home", default=None)
    s.add_argument("pkgdir")
    s.set_defaults(fn=cmd_accept)

    s = sub.add_parser("install", help="install only the exact accepted artifact")
    s.add_argument("--home", default=None)
    s.add_argument("pkgdir")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("invoke", help="invoke through the receiver boundary")
    s.add_argument("--home", default=None)
    s.add_argument("package_hash")
    s.add_argument("--input", required=True, help="fresh local work input (JSON)")
    s.set_defaults(fn=cmd_invoke)

    s = sub.add_parser("revoke", help="revoke future invocation")
    s.add_argument("--home", default=None)
    s.add_argument("package_hash")
    s.set_defaults(fn=cmd_revoke)

    s = sub.add_parser("bypass", help="demonstrate a blocked bypass")
    s.add_argument("--home", default=None)
    s.add_argument("kind", choices=["optimizer-write", "substitute"])
    s.set_defaults(fn=cmd_bypass)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except InstallerError as e:
        print("refused [%s]: %s" % (e.code, e.message), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print("error [%s]: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
