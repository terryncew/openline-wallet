"""Local receiver-controlled capability installer (developer preview).

Thin adapter over proven machinery:
  - openline_wallet.crypto / canonical  (hashes, signatures, package identity)
  - openline_wallet.wallet              (invoke mandates, revocation)
  - capability_installer.battery         (buyer-owned acceptance battery)

The receiver (buyer) decides: it inspects the package, runs its own
acceptance checks, imports only the exact package that passed, invokes it
through the mandate + hash-binding boundary, and can revoke future use.

Nothing here is a marketplace, a payment system, or a sandbox.
Capabilities run in-process with the host's full privileges; see the demo
README for the documented limits.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openline_wallet.canonical import canonical_json
from openline_wallet.crypto import (
    Ed25519PrivateKey,
    load_private_key,
    principal_id,
    private_key_hex,
    public_key_hex,
    save_private_key,
    sha256_hex,
    sign_record,
    verify_record,
)
from openline_wallet.wallet import Wallet, WalletError

from .battery import CHECK_IDS, battery_digest, load_module_from_bytes, run_battery

HOME_ENV = "CAPINSTALL_HOME"
INVOKE_SCOPE = "capability:invoke"
MANIFEST_SCHEMA = "capability-installer.manifest.v1"
EXAMPLE_NAME = "symptom_summarizer"
EXAMPLE_VERSION = "baseline"


class InstallerError(Exception):
    """Refusal or failure with a machine-readable code and a human message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def home() -> Path:
    return Path(os.environ.get(HOME_ENV, str(Path.home() / ".capinstall")))


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _freeze_floats(value):
    """Wallet canonical records forbid floats; freeze them as strings."""
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {k: _freeze_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_freeze_floats(v) for v in value]
    return value


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")


def package_hash(artifact_bytes: bytes, manifest: dict) -> str:
    """Frozen package identity: artifact bytes || 0x00 || canonical manifest
    (manifest without signatures, so signing the manifest can't move the hash)."""
    m = {k: v for k, v in manifest.items() if k != "signatures"}
    return sha256_hex(artifact_bytes + b"\x00" + canonical_json(m))


# -- setup -----------------------------------------------------------------


def init_home(h: Path | None = None) -> dict:
    """One-time local setup: dirs, buyer wallet, demo keys, example package."""
    h = h or home()
    if (h / "wallet").exists():
        raise InstallerError("ALREADY_INITIALIZED", "home already initialized: %s" % h)
    (h / "packages").mkdir(parents=True)
    (h / "lineage").mkdir(parents=True)
    (h / "receipts").mkdir(parents=True)
    (h / "keys").mkdir(parents=True)
    wallet = Wallet.create(h / "wallet", label="capinstall buyer wallet")
    seller_key = Ed25519PrivateKey.generate()
    save_private_key(h / "keys" / "demo_seller.key", seller_key)
    seller_pub = public_key_hex(seller_key)
    _write_json(h / "keys" / "demo_seller.pub.json", {"public_key": seller_pub})
    _write_json(h / "battery.pin", {"battery_digest": battery_digest()})
    _write_json(h / "decisions.json", {})
    _write_json(h / "mandates.json", {})
    _write_json(
        h / "settlement.json",
        {"balances": {"buyer": 10000, "seller": 0}, "transfers": {}},
    )
    pkgdir = build_example_package(h, seller_key)
    return {
        "home": str(h),
        "buyer_principal": wallet.principal_id,
        "demo_seller_public_key": seller_pub,
        "example_package": str(pkgdir),
    }


def build_example_package(h: Path, seller_key) -> Path:
    """Seller-side packaging of the shipped example artifact, signed locally."""
    artifact_bytes = (Path(__file__).parent / "example_artifact.py").read_bytes()
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "name": EXAMPLE_NAME,
        "version": EXAMPLE_VERSION,
        "artifact": "symptom_summarizer.py",
        "artifact_sha256": sha256_hex(artifact_bytes),
        "interface": {
            "entry_point": "summarize",
            "signature": "summarize(probe_results, absent_files) -> dict",
            "output_keys": ["version", "lines", "counts", "fault_hint", "per_task"],
        },
        "declared_scope": "summarize repair probe results into symptom summaries",
        "dependencies": [],
        "runtime": "python3, stdlib only",
        "requested_permissions": [],
        "seller": principal_id(public_key_hex(seller_key)),
        "asking_price": {"amount": 250, "currency": "SIM_USD"},
        "provenance": {
            "source": "CAPABILITY-EXCHANGE-001 traded artifact (frozen record)",
            "source_sha256": sha256_hex(artifact_bytes),
        },
        "evidence": {
            "note": "buyer battery score from the exchange study: 10/12 "
            "against the buyer's declared threshold of 10/12",
            "acceptance_battery": battery_digest(),
        },
    }
    sig = sign_record(
        {"manifest": {k: v for k, v in manifest.items()}}, seller_key
    )
    manifest["signatures"] = [
        {"signer": public_key_hex(seller_key), "record": sig}
    ]
    pkgdir = h / "packages" / "example"
    pkgdir.mkdir(parents=True, exist_ok=True)
    (pkgdir / "symptom_summarizer.py").write_bytes(artifact_bytes)
    (pkgdir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    )
    return pkgdir


# -- shared package validation -----------------------------------------------


def validate_package(pkgdir: str | Path) -> dict:
    """One shared validation step, used by inspect and accept.

    (a) the declared artifact hash matches the actual artifact bytes;
    (b) the embedded seller signature verifies against the signer's key;
    (c) the signed manifest canonical-matches the manifest being inspected
        (no field — version, permissions, provenance, seller metadata —
        may change under the old signature);
    (d) the manifest's seller principal corresponds to the signing key.
    """
    pkgdir = Path(pkgdir)
    man_path = pkgdir / "manifest.json"
    if not man_path.exists():
        raise InstallerError("NO_MANIFEST", "package has no manifest.json")
    manifest = json.loads(man_path.read_text())
    art_name = manifest.get("artifact", "symptom_summarizer.py")
    art_path = pkgdir / art_name
    if not art_path.exists():
        raise InstallerError("NO_ARTIFACT", "manifest names %s, not present" % art_name)
    artifact_bytes = art_path.read_bytes()
    if sha256_hex(artifact_bytes) != manifest.get("artifact_sha256"):
        raise InstallerError(
            "ARTIFACT_HASH_MISMATCH",
            "artifact bytes do not match the manifest's declared hash",
        )
    sigs = manifest.get("signatures", [])
    if not sigs:
        raise InstallerError(
            "SIGNATURE_MISSING",
            "package carries no seller signature; refusing without seller identity",
        )
    sig = sigs[0]
    signer = sig.get("signer")
    ok, reason = verify_record(sig.get("record", {}), expected_public_key=signer)
    if not ok:
        raise InstallerError(
            "SIGNATURE_INVALID", "seller signature does not verify: %s" % reason
        )
    signed_manifest = sig.get("record", {}).get("manifest")
    unsigned = {k: v for k, v in manifest.items() if k != "signatures"}
    if signed_manifest is None or canonical_json(signed_manifest) != canonical_json(
        unsigned
    ):
        raise InstallerError(
            "MANIFEST_TAMPERED",
            "the signed manifest does not match the manifest being inspected; "
            "version, permissions, provenance, or seller metadata may have "
            "been altered while the old signature was left attached",
        )
    if manifest.get("seller") != principal_id(signer):
        raise InstallerError(
            "SELLER_PRINCIPAL_MISMATCH",
            "manifest seller principal does not correspond to the signing key",
        )
    return {
        "artifact_bytes": artifact_bytes,
        "manifest": manifest,
        "signer": signer,
        "package_hash": package_hash(artifact_bytes, manifest),
    }


# -- inspect ---------------------------------------------------------------


def _interface_of(artifact_bytes: bytes) -> dict:
    """Interface extracted by parsing, never by executing the artifact."""
    try:
        tree = ast.parse(artifact_bytes.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise InstallerError("ARTIFACT_UNPARSEABLE", "artifact is not valid python: %r" % e)
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    entry = next((f for f in funcs if f.name == "summarize"), None)
    if entry is None:
        raise InstallerError(
            "NO_ENTRY_POINT",
            "no summarize() entry point found; functions present: %s"
            % [f.name for f in funcs],
        )
    args = [a.arg for a in entry.args.args]
    return {
        "entry_point": "summarize",
        "signature": "summarize(%s)" % ", ".join(args),
        "docstring": (ast.get_docstring(entry) or "").strip()[:300],
        "module_functions": [f.name for f in funcs],
    }


def inspect_package(pkgdir: str | Path) -> dict:
    """Describe a package without executing it. Hashes identify; they do not
    certify safety. Refuses packages that fail the shared validation step."""
    validated = validate_package(pkgdir)
    manifest = validated["manifest"]
    artifact_bytes = validated["artifact_bytes"]
    return {
        "name": manifest.get("name"),
        "version": manifest.get("version"),
        "package_hash": validated["package_hash"],
        "artifact_sha256": sha256_hex(artifact_bytes),
        "manifest_schema": manifest.get("schema"),
        "interface": _interface_of(artifact_bytes),
        "declared_scope": manifest.get("declared_scope"),
        "dependencies": manifest.get("dependencies"),
        "runtime": manifest.get("runtime"),
        "requested_permissions": manifest.get("requested_permissions"),
        "seller": manifest.get("seller"),
        "asking_price": manifest.get("asking_price"),
        "provenance": manifest.get("provenance"),
        "supplied_evidence": manifest.get("evidence"),
        "signature": {"valid": True, "detail": "valid", "signer": validated["signer"]},
        "trust_note": "The signature identifies the packaging key. This preview "
        "does not provide a seller trust registry; the buyer decides whether "
        "that signer is trusted. A valid signature proves who packaged it, "
        "not that it is safe to run.",
    }


# -- accept -----------------------------------------------------------------


def _check_battery_pin(h: Path) -> None:
    pin = _read_json(h / "battery.pin", None)
    if pin is None or pin.get("battery_digest") != battery_digest():
        raise InstallerError(
            "BATTERY_INTEGRITY",
            "acceptance policy does not match the pinned battery digest; "
            "refusing to evaluate",
        )


def accept_package(
    pkgdir: str | Path,
    h: Path | None = None,
    checks=CHECK_IDS,
    threshold: int = 10,
) -> dict:
    """Run the buyer-selected acceptance checks on the exact package bytes.

    The package must first pass the shared validation step; the buyer
    battery never runs on an unsigned, tampered, or misbound package."""
    h = h or home()
    pkgdir = Path(pkgdir)
    _check_battery_pin(h)
    validated = validate_package(pkgdir)
    manifest = validated["manifest"]
    artifact_bytes = validated["artifact_bytes"]
    ph = validated["package_hash"]
    result = run_battery(artifact_bytes, checks=checks)
    score = result["correct"]
    passed = bool(result["verdict"]) and score >= threshold
    decision = {
        "decision": "accepted" if passed else "rejected",
        "package_hash": ph,
        "artifact_sha256": sha256_hex(artifact_bytes),
        "manifest_version": manifest.get("version"),
        "signature": {"valid": True, "signer": validated["signer"]},
        "checks": result["checks"],
        "score": score,
        "threshold": threshold,
        "tests": {k: {"pass": v["pass"], "detail": v["detail"]} for k, v in result["tests"].items()},
        "battery_digest": battery_digest(),
    }
    decisions = _read_json(h / "decisions.json", {})
    decisions[ph] = decision
    _write_json(h / "decisions.json", decisions)
    _write_json(h / "receipts" / ("acceptance_%s.json" % ph[:16]), decision)
    return decision


# -- import -----------------------------------------------------------------


def import_package(pkgdir: str | Path, h: Path | None = None) -> dict:
    """Import only the exact package that passed acceptance."""
    h = h or home()
    pkgdir = Path(pkgdir)
    manifest = json.loads((pkgdir / "manifest.json").read_text())
    artifact_bytes = (pkgdir / manifest.get("artifact", "symptom_summarizer.py")).read_bytes()
    ph = package_hash(artifact_bytes, manifest)
    decisions = _read_json(h / "decisions.json", {})
    dec = decisions.get(ph)
    if dec is None or dec.get("decision") != "accepted":
        raise InstallerError(
            "IMPORT_REFUSED",
            "no acceptance decision for this exact package; "
            "import is only allowed after acceptance",
        )
    if manifest.get("version") != dec.get("manifest_version"):
        raise InstallerError(
            "VERSION_MISMATCH",
            "manifest version changed after acceptance; re-acceptance required",
        )
    mandates = _read_json(h / "mandates.json", {})
    if ph in mandates:
        raise InstallerError(
            "ALREADY_IMPORTED",
            "this exact package is already imported; revoke first to reinstall",
        )
    dest_dir = h / "lineage" / ph
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "symptom_summarizer.py").write_bytes(artifact_bytes)
    _write_json(
        dest_dir / "lineage.json",
        {
            "from": manifest.get("seller"),
            "package_hash": ph,
            "artifact_sha256": sha256_hex(artifact_bytes),
            "manifest": {k: v for k, v in manifest.items() if k != "signatures"},
            "acceptance": dec,
        },
    )
    wallet = Wallet.open(h / "wallet")
    subject_key = Ed25519PrivateKey.generate()
    save_private_key(h / "keys" / ("subject_%s.key" % ph[:16]), subject_key)
    subject_id = "capability:%s" % ph[:16]
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
    mandates[ph] = {
        "mandate_id": mid,
        "subject_id": subject_id,
        "scope": INVOKE_SCOPE,
    }
    _write_json(h / "mandates.json", mandates)
    return {
        "package_hash": ph,
        "lineage": str(dest_dir),
        "mandate_id": mid,
        "invoke_authority": "mandate %s, scope %s" % (mid, INVOKE_SCOPE),
    }


# -- invoke -----------------------------------------------------------------


def _active_mandate(wallet: Wallet, mandate_id: str) -> dict:
    now = datetime.now(timezone.utc)
    active = {m["mandate_id"]: m for m in wallet.timeline().current(now)}
    m = active.get(mandate_id)
    if m is None:
        raise InstallerError(
            "MANDATE_REVOKED",
            "no active invoke mandate for this capability; "
            "it was revoked or expired, and the receiver refuses invocation",
        )
    if INVOKE_SCOPE not in m.get("scopes", []):
        raise InstallerError(
            "ACTION_OUTSIDE_MANDATE",
            "mandate does not carry the %s scope" % INVOKE_SCOPE,
        )
    return m


def invoke(
    package_hash: str,
    fixture: str | Path,
    h: Path | None = None,
) -> dict:
    """Invoke through the receiver boundary: active mandate, hash-bound
    artifact, receiver-recomputed output. Saves a signed result receipt."""
    h = h or home()
    mandates = _read_json(h / "mandates.json", {})
    binding = mandates.get(package_hash)
    if binding is None:
        raise InstallerError("NOT_IMPORTED", "capability was never imported here")
    wallet = Wallet.open(h / "wallet")
    mandate = _active_mandate(wallet, binding["mandate_id"])
    decisions = _read_json(h / "decisions.json", {})
    dec = decisions.get(package_hash)
    if dec is None or dec.get("decision") != "accepted":
        raise InstallerError("INVOCATION_REFUSED", "no acceptance on record")
    art_path = h / "lineage" / package_hash / "symptom_summarizer.py"
    artifact_bytes = art_path.read_bytes()
    if sha256_hex(artifact_bytes) != dec["artifact_sha256"]:
        raise InstallerError(
            "INVOCATION_BINDING_MISMATCH",
            "lineage artifact no longer matches the accepted hash",
        )
    fx_path = Path(fixture)
    if not fx_path.exists():
        from importlib import resources

        fx_path = resources.files("openline_wallet.capability_installer")
        fx_path = fx_path / "fixtures" / (str(fixture) + ".json")
    fx = json.loads(fx_path.read_text())
    mod = load_module_from_bytes("invoked_capability", artifact_bytes)
    output = mod.summarize(fx["probe_results"], fx["absent_files"])
    # receiver recomputation: run again from the hash-bound artifact and compare
    mod2 = load_module_from_bytes("invoked_capability_check", artifact_bytes)
    recomputed = mod2.summarize(fx["probe_results"], fx["absent_files"])
    if recomputed != output:
        raise InstallerError("INVOCATION_NONDETERMINISTIC", "recomputation diverged")
    input_digest = sha256_hex(
        json.dumps(
            {"probe_results": fx["probe_results"], "absent_files": fx["absent_files"]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    frozen_output = _freeze_floats(output)
    output_digest = sha256_hex(
        json.dumps(frozen_output, sort_keys=True, separators=(",", ":")).encode()
    )
    receipt = {
        "schema": "capability-installer.invocation-receipt.v1",
        "package_hash": package_hash,
        "artifact_sha256": sha256_hex(artifact_bytes),
        "fixture": fx_path.name,
        "input_digest": input_digest,
        "output_digest": output_digest,
        "output": frozen_output,
        "mandate_id": mandate["mandate_id"],
        "buyer": wallet.principal_id,
    }
    signed = sign_record(receipt, wallet.epoch_key)
    rid = sha256_hex(canonical_json(signed))[:16]
    rpath = h / "receipts" / "invocations"
    rpath.mkdir(parents=True, exist_ok=True)
    _write_json(rpath / ("%s.json" % rid), {"receipt": receipt, "signature": signed})
    return {
        "receipt_id": rid,
        "receipt_path": str(rpath / ("%s.json" % rid)),
        "output_digest": output_digest,
        "fault_hint": output.get("fault_hint"),
    }


# -- revoke -----------------------------------------------------------------


def revoke(package_hash: str, h: Path | None = None) -> dict:
    """Revoke future invocation through the receiver. This blocks the
    receiver boundary only; it does not erase exported code and does not
    prevent anyone from running a copy elsewhere."""
    h = h or home()
    mandates = _read_json(h / "mandates.json", {})
    binding = mandates.get(package_hash)
    if binding is None:
        raise InstallerError("NOT_IMPORTED", "capability was never imported here")
    wallet = Wallet.open(h / "wallet")
    try:
        wallet.revoke(binding["mandate_id"], reason="USER_REVOKED")
    except WalletError as e:
        raise InstallerError("REVOKE_FAILED", str(e))
    return {
        "package_hash": package_hash,
        "mandate_id": binding["mandate_id"],
        "status": "revoked",
        "note": "future invocation through this receiver is blocked; "
        "exported copies outside the receiver are unaffected",
    }


# -- simulated settlement (optional demo) ------------------------------------


def settle_demo(package_hash: str, h: Path | None = None) -> dict:
    """Optional SIMULATED settlement demo. Pays the manifest asking price
    exactly once, and only after acceptance plus at least one verified
    fresh-work invocation. SIM_USD has no real-world value."""
    h = h or home()
    ledger = _read_json(h / "settlement.json", {"balances": {"buyer": 10000, "seller": 0}, "transfers": {}})
    if package_hash in ledger["transfers"]:
        raise InstallerError(
            "ALREADY_SETTLED",
            "settlement for this package already completed; "
            "replay cannot produce a second payment",
        )
    decisions = _read_json(h / "decisions.json", {})
    dec = decisions.get(package_hash)
    if dec is None or dec.get("decision") != "accepted":
        raise InstallerError(
            "SETTLEMENT_REFUSED",
            "no payment before buyer acceptance",
        )
    # verified invocation: a signed invocation record whose signature
    # verifies against this buyer's epoch key, whose signed body names
    # this buyer and the accepted package, and whose unsigned duplicate
    # matches the signed body. The signed record is the sole source of
    # truth: an unsigned or forged local file is not invocation, and a
    # valid signature attached to forged outer fields does not count.
    wallet = Wallet.open(h / "wallet")
    buyer_pub = public_key_hex(wallet.epoch_key)
    inv_dir = h / "receipts" / "invocations"
    invoked = False
    if inv_dir.exists():
        for p in sorted(inv_dir.glob("*.json")):
            try:
                body = json.loads(p.read_text())
                rec, sig = body["receipt"], body["signature"]
            except Exception:  # noqa: BLE001 - malformed files are not evidence
                continue
            if not isinstance(rec, dict) or not isinstance(sig, dict):
                continue
            ok, _reason = verify_record(sig, expected_public_key=buyer_pub)
            if not ok:
                continue
            signed_body = {
                k: v for k, v in sig.items() if k not in ("signature", "payload_hash")
            }
            if canonical_json(signed_body) != canonical_json(rec):
                continue
            if signed_body.get("package_hash") != package_hash:
                continue
            if signed_body.get("buyer") != wallet.principal_id:
                continue
            if signed_body.get("artifact_sha256") != dec["artifact_sha256"]:
                continue
            invoked = True
            break
    if not invoked:
        raise InstallerError(
            "INVOCATION_REQUIRED",
            "settlement requires a verified signed invocation first; "
            "unsigned or forged local receipts do not count, and a valid "
            "signature attached to forged outer fields does not count",
        )
    lineage = json.loads((h / "lineage" / package_hash / "lineage.json").read_text())
    price = lineage["manifest"]["asking_price"]
    assert price["currency"] == "SIM_USD", "simulated currency only"
    amount = price["amount"]
    if ledger["balances"]["buyer"] < amount:
        raise InstallerError("INSUFFICIENT_SIM_FUNDS", "buyer SIM_USD balance too low")
    ledger["balances"]["buyer"] -= amount
    ledger["balances"]["seller"] += amount
    ledger["transfers"][package_hash] = {"amount": amount, "currency": "SIM_USD"}
    _write_json(h / "settlement.json", ledger)
    return {
        "package_hash": package_hash,
        "amount": amount,
        "currency": "SIM_USD (simulated)",
        "buyer_balance": ledger["balances"]["buyer"],
        "seller_balance": ledger["balances"]["seller"],
        "note": "simulated payment; no real money moved",
    }
