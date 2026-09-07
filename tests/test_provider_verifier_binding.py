"""Independent-context and valid-signature substitution regressions.

No live provider calls. Fresh private keys are confined to temporary fixtures.
The original signed packet and historical source archive are never modified.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.canonical import pretty_json
from openline_wallet.crypto import load_private_key, public_key_hex, record_hash, sign_record
from openline_wallet.github_effect_live import run_experiment
from openline_wallet.errors import WalletError

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / "proofs/provider-effect-001"
SPEC = importlib.util.spec_from_file_location("provider_context_verifier", PROOF / "verify.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

from github_http_fixture import Fixture
from test_github_effect import TARGET


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(pretty_json(value), encoding="ascii")


def resign(record, key):
    body = {k: v for k, v in record.items() if k not in ("payload_hash", "signature")}
    return sign_record(body, key)


def refresh_packet(path):
    """Re-sign disposable wrappers and recompute all hashes after a mutation."""
    repaired = path / "repaired"
    nested = read(repaired / "result.json")
    nested["evidence_sha256"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(repaired.glob("*.json")) if p.name != "result.json"}
    save(repaired / "result.json", resign(nested, Ed25519PrivateKey.generate()))
    for directory in (repaired, path):
        if directory == path:
            result = read(path / "result.json")
            result["source_sha256"] = {
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in verifier.HISTORICAL_SOURCE_SHA256}
            result["evidence_sha256"] = {
                str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(path.rglob("*.json")) if p != path / "result.json"}
            save(path / "result.json", resign(result, Ed25519PrivateKey.generate()))
        sums = "".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " +
                       str(p.relative_to(directory)) + "\n"
                       for p in sorted(directory.rglob("*.json")))
        (directory / "SHA256SUMS.txt").write_text(sums, encoding="ascii")


class HistoricalBindingTests(unittest.TestCase):
    def test_original_packet_passes_with_pinned_context(self):
        result = verifier.run(PROOF)
        self.assertEqual(result["context_trust"], "PINNED_HISTORICAL_CONTEXT")
        self.assertEqual(result["source_status"], "RECORDED_HISTORICAL_SOURCE")
        self.assertEqual(result["verdict"], "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED")

    def test_fresh_packet_requires_explicit_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "packet"
            shutil.copytree(PROOF, path, ignore=shutil.ignore_patterns("historical-source-packet.zip"))
            refresh_packet(path)
            with self.assertRaisesRegex(RuntimeError, "EXPECTED_CONTEXT_REQUIRED"):
                verifier.run(path)

    def test_legacy_accepts_foreign_closure_but_repair_rejects(self):
        """The exact historical verifier accepts a valid foreign closure signer."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline"
            baseline.mkdir()
            with zipfile.ZipFile(PROOF / "historical-source-packet.zip") as z:
                for name in z.namelist():
                    target = baseline / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(name))
            spec = importlib.util.spec_from_file_location(
                "historical_provider_verifier", baseline / "proofs/provider-effect-001/verify.py")
            legacy = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(legacy)
            legacy.ROOT = baseline
            packet = root / "packet"
            shutil.copytree(PROOF, packet, ignore=shutil.ignore_patterns("historical-source-packet.zip"))
            attacker = Ed25519PrivateKey.generate()
            path = packet / "repaired/closure_b.json"
            closure = read(path)
            closure["gate_public_key"] = public_key_hex(attacker)
            save(path, resign(closure, attacker))
            refresh_packet(packet)
            # Recreate the original source map for the old verifier.
            top = read(packet / "result.json")
            top["source_sha256"] = verifier.HISTORICAL_SOURCE_SHA256
            save(packet / "result.json", resign(top, Ed25519PrivateKey.generate()))
            sums = "".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " +
                           str(p.relative_to(packet)) + "\n"
                           for p in sorted(packet.rglob("*.json")))
            (packet / "SHA256SUMS.txt").write_text(sums, encoding="ascii")
            self.assertEqual(legacy.run(packet)["verdict"], "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED")
            # The repaired verifier uses the same packet and trusted historical context.
            refresh_packet(packet)
            with self.assertRaisesRegex(RuntimeError, "signed evidence invalid"):
                verifier.run(packet, context=verifier.FROZEN_CONTEXT)

    def test_historical_source_archive_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = PROOF / "historical-source-packet.zip"
            shadow = root / "historical-source-packet.zip"
            shutil.copyfile(source, shadow)
            with patch.object(verifier, "ROOT", root):
                (root / "proofs/provider-effect-001").mkdir(parents=True)
                shutil.copyfile(shadow, root / "proofs/provider-effect-001/historical-source-packet.zip")
                (root / "proofs/provider-effect-001/historical-source-packet.zip").write_bytes(b"tampered")
                with self.assertRaisesRegex(RuntimeError, "historical source archive"):
                    verifier._source_closure(read(PROOF / "result.json"), historical=True)


class SignedSubstitutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.original = cls.root / "clean"
        cls.state = cls.root / "private"
        cls.original.mkdir()
        fixture = Fixture()
        try:
            result = run_experiment(fixture_client(fixture), TARGET, cls.state,
                                    cls.original / "repaired", transport="fixture")
            assert result["verdict"] == "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED"
        finally:
            fixture.close()
        shutil.copyfile(PROOF / "unsafe-control.json", cls.original / "unsafe-control.json")
        top = read(PROOF / "result.json")
        save(cls.original / "result.json", top)
        refresh_packet(cls.original)
        cls.context = verifier.fixture_context(cls.original)
        cls.keys = {
            phase: load_private_key(cls.state / phase / "gate.key")
            for phase in ("a", "b")}
        assert verifier.run(cls.original, context=cls.context)["verdict"] == "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED"

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packet = Path(self.tmp.name) / "packet"
        shutil.copytree(self.original, self.packet)
        self.context = copy.deepcopy(self.__class__.context)

    def mutate(self, name, change, phase="b", key=None):
        path = self.packet / "repaired" / (name + ".json")
        value = read(path)
        change(value)
        save(path, resign(value, key or self.keys[phase]))

    def check_rejected(self, reason):
        refresh_packet(self.packet)
        with self.assertRaisesRegex((RuntimeError, WalletError), reason):
            verifier.run(self.packet, context=self.context)

    def test_legitimate_fresh_fixture_accepts(self):
        self.assertEqual(verifier.run(self.packet, context=self.context)["context_trust"],
                         "CALLER_SUPPLIED_CONTEXT")

    def test_other_receiver_signature_is_rejected(self):
        def change(record):
            record["gate_public_key"] = public_key_hex(self.keys["a"])
        self.mutate("closure_b", change, key=self.keys["a"])
        self.check_rejected("signed evidence invalid")

    def test_unrelated_valid_signer_is_rejected(self):
        attacker = Ed25519PrivateKey.generate()
        self.mutate("closure_b", lambda r: r.update(gate_public_key=public_key_hex(attacker)), key=attacker)
        self.check_rejected("signed evidence invalid")

    def test_wrong_principal_with_valid_receiver_signature(self):
        self.mutate("closure_b", lambda r: r.update(principal_id="openline:principal:" + "0"*64))
        self.check_rejected("principal_id mismatch")

    def test_wrong_mandate_with_valid_receiver_signature(self):
        self.mutate("closure_b", lambda r: r.update(mandate_id="grant-a"))
        self.check_rejected("mandate_id mismatch")

    def test_wrong_gate_identity_with_valid_receiver_signature(self):
        self.mutate("closure_b", lambda r: r.update(gate_id="another-receiver"))
        self.check_rejected("gate_id mismatch")

    def test_wrong_target_with_valid_receiver_signature(self):
        self.mutate("closure_b", lambda r: r["target"].update(number=999))
        self.check_rejected("closure target mismatch")

    def test_wrong_revocation_head_with_valid_receiver_signature(self):
        self.mutate("closure_b", lambda r: r.update(head_sequence=2))
        self.check_rejected("closure sequence mismatch")

    def test_wrong_scope_with_valid_receiver_signature(self):
        self.mutate("closure_b", lambda r: r.update(scope="UNIVERSAL_PROVIDER_CLOSURE"))
        self.check_rejected("closure scope mismatch")

    def test_wrong_subject_with_valid_receiver_signature(self):
        path = self.packet / "repaired/effect_b.json"
        value = read(path)
        value["effect_receipt"]["subject_id"] = "agent-a"
        value["effect_receipt"] = resign(value["effect_receipt"], self.keys["b"])
        save(path, value)
        self.check_rejected("subject_id mismatch")

    def test_wrong_effect_target_with_valid_receiver_signature(self):
        path = self.packet / "repaired/effect_b.json"
        value = read(path)
        value["effect_receipt"]["target"]["head_sha"] = "d"*40
        value["effect_receipt"] = resign(value["effect_receipt"], self.keys["b"])
        # Keep the closure hash consistent so the target check is the falsifier.
        save(path, value)
        self.mutate("closure_b", lambda r: r.update(
            confirmed_effect_hashes=[record_hash(value["effect_receipt"])]))
        self.check_rejected("effect target mismatch")

    def test_foreign_admission_is_not_accepted_as_effect_context(self):
        path = self.packet / "repaired/effect_b.json"
        value = read(path)
        value["admission_receipt"] = read(self.packet / "repaired/admission_a.json")
        save(path, value)
        self.check_rejected("effect admission mismatch")

    def test_wrong_wallet_root_is_rejected(self):
        path = self.packet / "repaired/revoked_b.json"
        value = read(path)
        value["principal"]["root_public_key"] = "0"*64
        save(path, value)
        self.check_rejected("PRINCIPAL_ID_MISMATCH|principal root|invalid")

    def test_wrong_history_head_is_rejected(self):
        path = self.packet / "repaired/revoked_b.json"
        value = read(path)
        value["head"]["sequence"] = 2
        save(path, value)
        self.check_rejected("MISMATCH|INVALID|invalid|mismatch")

    def test_validly_resigned_wrapper_does_not_override_pinned_history(self):
        path = self.packet / "repaired/closure_b.json"
        value = read(path)
        value["head_hash"] = "f"*64
        save(path, resign(value, self.keys["b"]))
        self.check_rejected("closure head mismatch")

    def test_source_omission_is_rejected(self):
        refresh_packet(self.packet)
        path = self.packet / "result.json"
        result = read(path)
        result["source_sha256"].pop("proofs/provider-effect-001/verify.py")
        save(path, resign(result, Ed25519PrivateKey.generate()))
        sums = "".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " +
                       str(p.relative_to(self.packet)) + "\n"
                       for p in sorted(self.packet.rglob("*.json")))
        (self.packet / "SHA256SUMS.txt").write_text(sums, encoding="ascii")
        with self.assertRaisesRegex(RuntimeError, "source set invalid"):
            verifier.run(self.packet, context=self.context)


def fixture_client(fixture):
    from openline_wallet.github_effect import GitHubClient
    client = GitHubClient("disposable-fixture-token")
    client._opener = fixture.opener()
    return client


if __name__ == "__main__":
    unittest.main()
