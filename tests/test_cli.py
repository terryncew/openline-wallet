from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from openline_wallet.cli import main


class CliTests(unittest.TestCase):
    def call(self, *args: str) -> tuple[int, str, str]:
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(list(args))
        return code, output.getvalue(), error.getvalue()

    def test_cli_authorize_export_verify_revoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wallet = str(Path(directory) / "wallet")
            subject = str(Path(directory) / "agent-a.key")
            bundle = str(Path(directory) / "wallet.olw")
            code, output, _error = self.call("--wallet", wallet, "init")
            self.assertEqual(code, 0)
            self.assertIn("receiver Gate decides", output)
            code, _output, _error = self.call(
                "--wallet", wallet, "keygen-subject", "agent-a", "--output", subject
            )
            self.assertEqual(code, 0)
            code, output, _error = self.call(
                "--wallet",
                wallet,
                "grant",
                "agent-a",
                "deploy:staging",
                "--subject-key",
                subject + ".pub",
                "--expires",
                "2h",
            )
            self.assertEqual(code, 0)
            self.assertIn("eligible for receiver verification", output)
            code, _output, _error = self.call(
                "--wallet", wallet, "export", "--output", bundle
            )
            self.assertEqual(code, 0)
            code, output, _error = self.call("verify", bundle)
            self.assertEqual(code, 0)
            self.assertIn("EVIDENCE_VALID", output)
            code, output, _error = self.call("--wallet", wallet, "revoke", "agent-a")
            self.assertEqual(code, 0)
            self.assertIn("signature history preserved", output)


if __name__ == "__main__":
    unittest.main()
