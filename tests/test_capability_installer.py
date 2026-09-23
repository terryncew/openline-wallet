"""Regression controls for the capinstall preview, run against the CLI entry
point in this repo's unittest style (CI: python -m unittest discover).

Ports the discriminating controls from CAPABILITY-EXCHANGE-001 plus the
preview's own binding controls:

  rejected package, unsigned / tampered-signature / substituted-manifest /
  seller-principal-mismatch package, substituted artifact, wrong version,
  wrong buyer, altered acceptance policy, unsigned / forged invocation
  receipt, settlement replay, revoked invocation.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from openline_wallet.capability_installer import installer as I
from openline_wallet.capability_installer.cli import main
from openline_wallet.crypto import (
    Ed25519PrivateKey,
    load_private_key,
    principal_id,
    public_key_hex,
    sha256_hex,
    sign_record,
    verify_record,
)

EXPECTED_ARTIFACT_SHA256 = (
    "80d6bb6ea5198cbbc4ec4fa300704fd1b1a7254062da461cd59b49ea43bfb44b"
)


class CapinstallCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "caphome"
        self._old_home = os.environ.get(I.HOME_ENV)
        os.environ[I.HOME_ENV] = str(self.home)
        code, _out, err = self.call("init")
        self.assertEqual(code, 0, err)

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop(I.HOME_ENV, None)
        else:
            os.environ[I.HOME_ENV] = self._old_home
        self.tmp.cleanup()

    # -- helpers -----------------------------------------------------------

    def call(self, *args: str) -> tuple[int, str, str]:
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(list(args))
        return code, output.getvalue(), error.getvalue()

    def pkgdir(self) -> Path:
        return self.home / "packages" / "example"

    def package_hash(self) -> str:
        return next(iter(json.loads((self.home / "decisions.json").read_text())))

    def seller_key(self):
        return load_private_key(self.home / "keys" / "demo_seller.key")

    def signed_package(self, name: str, artifact_src: str, **overrides) -> Path:
        """Build a seller-signed package in this home, like the example one."""
        seller_key = self.seller_key()
        artifact_bytes = artifact_src.encode()
        manifest = {
            "schema": "capability-installer.manifest.v1",
            "name": "symptom_summarizer",
            "version": "custom",
            "artifact": "symptom_summarizer.py",
            "artifact_sha256": sha256_hex(artifact_bytes),
            "interface": {"entry_point": "summarize"},
            "declared_scope": "test",
            "dependencies": [],
            "runtime": "python3, stdlib only",
            "requested_permissions": [],
            "seller": principal_id(public_key_hex(seller_key)),
            "asking_price": {"amount": 250, "currency": "SIM_USD"},
            "provenance": {"source": "test"},
            "evidence": {},
        }
        manifest.update(overrides)
        sig = sign_record({"manifest": dict(manifest)}, seller_key)
        manifest["signatures"] = [
            {"signer": public_key_hex(seller_key), "record": sig}
        ]
        pkgdir = self.home / "packages" / name
        pkgdir.mkdir(parents=True, exist_ok=True)
        (pkgdir / "symptom_summarizer.py").write_bytes(artifact_bytes)
        (pkgdir / "manifest.json").write_text(
            json.dumps(manifest, indent=1, sort_keys=True) + "\n"
        )
        return pkgdir

    # -- full flow ----------------------------------------------------------

    def test_full_flow(self) -> None:
        pkg = self.pkgdir()
        code, out, err = self.call("inspect", str(pkg))
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        info = json.loads("\n".join(lines[: lines.index("}") + 1]))
        self.assertEqual(info["artifact_sha256"], EXPECTED_ARTIFACT_SHA256)
        self.assertEqual(info["requested_permissions"], [])
        self.assertIn("signature valid: True", out)
        self.assertIn("does not provide a seller trust registry", out)

        code, out, err = self.call("accept", str(pkg))
        self.assertEqual(code, 0, err)
        self.assertIn("ACCEPTED", out)
        self.assertIn("buyer battery score: 10/12, buyer threshold: 10/12", out)
        ph = self.package_hash()

        code, out, err = self.call("import", str(pkg))
        self.assertEqual(code, 0, err)
        self.assertTrue((self.home / "lineage" / ph / "symptom_summarizer.py").exists())

        code, out, err = self.call("invoke", ph, "w01_mixed_eval_majority")
        self.assertEqual(code, 0, err)
        receipts = list((self.home / "receipts" / "invocations").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        body = json.loads(receipts[0].read_text())
        self.assertEqual(body["receipt"]["package_hash"], ph)
        # receipt is a signed record (verifiable by the receiver)
        from openline_wallet.crypto import verify_record

        ok, _ = verify_record(body["signature"])
        self.assertTrue(ok)

        code, out, err = self.call("settle", ph, "--demo")
        self.assertEqual(code, 0, err)
        self.assertIn("SIMULATED settlement", out)
        ledger = json.loads((self.home / "settlement.json").read_text())
        self.assertEqual(ledger["balances"], {"buyer": 9750, "seller": 250})
        self.assertEqual(len(ledger["transfers"]), 1)

        # replay cannot double-pay
        code, _out, err = self.call("settle", ph, "--demo")
        self.assertEqual(code, 2)
        self.assertIn("ALREADY_SETTLED", err)

        code, _out, err = self.call("revoke", ph)
        self.assertEqual(code, 0, err)

        code, _out, err = self.call("invoke", ph, "w02_all_ok")
        self.assertEqual(code, 2)
        self.assertIn("MANDATE_REVOKED", err)

    # -- rejected package: signed but fails the buyer battery ---------------

    def test_rejected_package(self) -> None:
        bad = self.signed_package(
            "bad",
            "VERSION='bad'\ndef summarize(a, b):\n    return {'version': VERSION}\n",
        )
        code, out, err = self.call("accept", str(bad))
        self.assertEqual(code, 2, err)
        self.assertIn("REJECTED", out)
        code, _out, err = self.call("import", str(bad))
        self.assertEqual(code, 2)
        self.assertIn("IMPORT_REFUSED", err)
        code, _out, err = self.call("settle", "no-such-hash", "--demo")
        self.assertEqual(code, 2)
        self.assertIn("SETTLEMENT_REFUSED", err)

    # -- unsigned package: no seller identity --------------------------------

    def test_accept_refuses_unsigned_package(self) -> None:
        pkg = self.pkgdir()
        man_path = pkg / "manifest.json"
        manifest = json.loads(man_path.read_text())
        manifest["signatures"] = []
        man_path.write_text(json.dumps(manifest))
        code, _out, err = self.call("inspect", str(pkg))
        self.assertEqual(code, 2)
        self.assertIn("SIGNATURE_MISSING", err)
        code, _out, err = self.call("accept", str(pkg))
        self.assertEqual(code, 2)
        self.assertIn("SIGNATURE_MISSING", err)

    # -- tampered signature ----------------------------------------------------

    def test_accept_refuses_tampered_signature(self) -> None:
        pkg = self.pkgdir()
        man_path = pkg / "manifest.json"
        manifest = json.loads(man_path.read_text())
        record = manifest["signatures"][0]["record"]
        record["signature"]["value"] = "ab" * 64
        man_path.write_text(json.dumps(manifest))
        code, _out, err = self.call("accept", str(pkg))
        self.assertEqual(code, 2)
        self.assertIn("SIGNATURE_INVALID", err)

    # -- manifest-field substitution under the old signature --------------------

    def test_refuses_manifest_substitution(self) -> None:
        pkg = self.pkgdir()
        man_path = pkg / "manifest.json"
        manifest = json.loads(man_path.read_text())
        manifest["version"] = "baseline-evil"
        manifest["requested_permissions"] = ["network"]
        manifest["provenance"] = {"source": "forged"}
        # old signed record left attached
        man_path.write_text(json.dumps(manifest))
        code, _out, err = self.call("inspect", str(pkg))
        self.assertEqual(code, 2)
        self.assertIn("MANIFEST_TAMPERED", err)
        code, _out, err = self.call("accept", str(pkg))
        self.assertEqual(code, 2)
        self.assertIn("MANIFEST_TAMPERED", err)

    # -- seller principal must correspond to the signing key ---------------------

    def test_refuses_seller_principal_mismatch(self) -> None:
        other = Ed25519PrivateKey.generate()
        pkg = self.signed_package(
            "mismatch",
            (self.pkgdir() / "symptom_summarizer.py").read_text(),
            seller=principal_id(public_key_hex(other)),
        )
        code, _out, err = self.call("accept", str(pkg))
        self.assertEqual(code, 2)
        self.assertIn("SELLER_PRINCIPAL_MISMATCH", err)

    # -- substituted artifact after acceptance ------------------------------------

    def test_substituted_artifact(self) -> None:
        pkg = self.pkgdir()
        code, _out, err = self.call("accept", str(pkg))
        self.assertEqual(code, 0, err)
        # seller swaps the artifact after acceptance
        (pkg / "symptom_summarizer.py").write_text(
            "VERSION='x'\ndef summarize(a, b):\n    return {'version': VERSION}\n"
        )
        code, _out, err = self.call("import", str(pkg))
        self.assertEqual(code, 2)
        self.assertIn("IMPORT_REFUSED", err)

    # -- wrong version after acceptance ---------------------------------------------

    def test_wrong_version(self) -> None:
        pkg = self.pkgdir()
        code, _out, err = self.call("accept", str(pkg))
        self.assertEqual(code, 0, err)
        man_path = pkg / "manifest.json"
        manifest = json.loads(man_path.read_text())
        manifest["version"] = "baseline-evil"
        man_path.write_text(json.dumps(manifest))
        code, _out, err = self.call("import", str(pkg))
        self.assertEqual(code, 2)
        self.assertIn("IMPORT_REFUSED", err)  # package hash no longer matches acceptance

    # -- wrong buyer: another buyer's home cannot use the mandate -------------------

    def test_wrong_buyer(self) -> None:
        pkg = self.pkgdir()
        code, _out, err = self.call("accept", str(pkg))
        self.assertEqual(code, 0, err)
        code, _out, err = self.call("import", str(pkg))
        self.assertEqual(code, 0, err)
        ph = self.package_hash()

        # a second buyer on the same host, with their own wallet and home
        with tempfile.TemporaryDirectory() as other:
            home_b = Path(other) / "buyer-b"
            os.environ[I.HOME_ENV] = str(home_b)
            try:
                code, _out, err = self.call("init")
                self.assertEqual(code, 0, err)
                code, _out, err = self.call("invoke", ph, "w01_mixed_eval_majority")
                self.assertEqual(code, 2)
                self.assertIn("NOT_IMPORTED", err)
            finally:
                os.environ[I.HOME_ENV] = str(self.home)

    # -- altered acceptance policy is detected ---------------------------------------

    def test_altered_acceptance_policy(self) -> None:
        pkg = self.pkgdir()
        # the acceptance battery no longer matches the pinned digest
        with mock.patch.object(I, "battery_digest", return_value="00" * 32):
            code, _out, err = self.call("accept", str(pkg))
        self.assertEqual(code, 2)
        self.assertIn("BATTERY_INTEGRITY", err)

    # -- settlement requires a verified signed invocation ------------------------------

    def test_settle_requires_verified_invocation(self) -> None:
        pkg = self.pkgdir()
        code, _out, err = self.call("accept", str(pkg))
        self.assertEqual(code, 0, err)
        code, _out, err = self.call("import", str(pkg))
        self.assertEqual(code, 0, err)
        ph = self.package_hash()

        # no invocation yet
        code, _out, err = self.call("settle", ph, "--demo")
        self.assertEqual(code, 2)
        self.assertIn("INVOCATION_REQUIRED", err)

        inv_dir = self.home / "receipts" / "invocations"
        inv_dir.mkdir(parents=True, exist_ok=True)
        dec = json.loads((self.home / "decisions.json").read_text())[ph]
        forged_body = {
            "receipt": {
                "schema": "capability-installer.invocation-receipt.v1",
                "package_hash": ph,
                "artifact_sha256": dec["artifact_sha256"],
                "fixture": "w01_mixed_eval_majority",
                "input_digest": "00",
                "output_digest": "00",
                "output": {},
                "mandate_id": "forged",
                "buyer": "openline:principal:forged",
            },
            "signature": {"unsigned": True},
        }
        (inv_dir / "forged.json").write_text(json.dumps(forged_body))

        # a locally forged unsigned receipt does not count as invocation
        code, _out, err = self.call("settle", ph, "--demo")
        self.assertEqual(code, 2)
        self.assertIn("INVOCATION_REQUIRED", err)

        # a receipt signed by a different key does not count either
        attacker = Ed25519PrivateKey.generate()
        signed_forged = sign_record(dict(forged_body["receipt"]), attacker)
        forged_body["signature"] = signed_forged
        (inv_dir / "forged_signed.json").write_text(json.dumps(forged_body))
        code, _out, err = self.call("settle", ph, "--demo")
        self.assertEqual(code, 2)
        self.assertIn("INVOCATION_REQUIRED", err)

        # the real receiver-produced signed receipt does count
        code, _out, err = self.call("invoke", ph, "w01_mixed_eval_majority")
        self.assertEqual(code, 0, err)
        code, _out, err = self.call("settle", ph, "--demo")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
