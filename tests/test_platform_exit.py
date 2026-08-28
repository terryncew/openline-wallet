from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from openline_wallet.canonical import strict_json_load
from openline_wallet.demo import VERDICT, run_platform_exit
from openline_wallet.wallet import verify_bundle


class PlatformExitAcceptanceTests(unittest.TestCase):
    def test_platform_exit_001(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_platform_exit(directory)
            self.assertEqual(result["verdict"], VERDICT)
            self.assertTrue(all(result["checks"].values()))
            self.assertEqual(result["authority"]["wallet_policy_authority"], "NONE")
            self.assertEqual(result["authority"]["decision_authority"], "RECEIVER_GATE")
            after = strict_json_load(Path(directory) / "wallet-after.olw")
            _verified, timeline = verify_bundle(after, require_fresh=True)
            self.assertEqual(timeline.mandates["mandate-platform-a"]["status"], "REVOKED")
            self.assertEqual(timeline.mandates["mandate-platform-b"]["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
