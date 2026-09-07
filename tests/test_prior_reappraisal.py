"""The historical evidence gate must reject altered records or source."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "provider_prior", ROOT / "proofs/provider-effect-001/verify_prior.py")
prior = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prior)

class HistoricalReappraisalTests(unittest.TestCase):
    def test_recorded_source_and_receipts_verify(self):
        result = prior.run()
        self.assertEqual(result["status"], "FROZEN_EVIDENCE_CONSISTENT")
        self.assertEqual(result["git_tree_source_identity"], "UNRESOLVED")
        self.assertEqual(result["source_files_verified"], 14)

    def test_modified_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.zip"
            path.write_bytes(prior.ARCHIVE.read_bytes() + b"tampered")
            with patch.object(prior, "ARCHIVE", path):
                with self.assertRaisesRegex(RuntimeError, "SOURCE_ARCHIVE_CHANGED"):
                    prior.run()

    def test_rehashed_archive_cannot_change_a_recorded_source(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.zip"
            with zipfile.ZipFile(prior.ARCHIVE) as original:
                entries = {n: original.read(n) for n in original.namelist()}
            name = "src/openline_wallet/canonical.py"
            entries[name] += b"\\n# tampered\\n"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(entries.items()):
                    archive.writestr(name, data)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(prior, "ARCHIVE", path), patch.object(prior, "ARCHIVE_SHA256", digest):
                with self.assertRaisesRegex(RuntimeError, "FROZEN_SOURCE_MISMATCH"):
                    prior.run()

    def test_changed_frozen_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "proofs/wallet-closure-set-001/result.json"
            target.parent.mkdir(parents=True)
            target.write_bytes((prior.ROOT / "proofs/wallet-closure-set-001/result.json").read_bytes() + b" ")
            with patch.object(prior, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "FROZEN_RESULT_CHANGED"):
                    prior.run()

if __name__ == "__main__":
    unittest.main()
