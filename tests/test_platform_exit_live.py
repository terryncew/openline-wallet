from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest

from mcp import Client

from openline_wallet.canonical import strict_json_load
from openline_wallet.gate_http import ReceiverRuntime, build_http_server
from openline_wallet.mcp_bridge import BridgeConfig, OpenLineMCPBridge, build_mcp_server
from openline_wallet.platform_exit_live import (
    CLAUDE_MANDATE,
    CLAUDE_SUBJECT,
    CODEX_MANDATE,
    CODEX_SUBJECT,
    VERDICT,
    prepare_workspace,
    switch_workspace,
    verify_workspace,
)


class PlatformExitLiveAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_provider_switch_keeps_authority_receiver_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "live"
            state = prepare_workspace(workspace)

            runtime = ReceiverRuntime.create(
                gate_id="test-receiver",
                principal_id=state["principal_id"],
                root_public_key=state["root_public_key"],
                ledger_path=workspace / "receiver" / "effects.json",
                receipts_dir=workspace / "receiver" / "receipts",
            )
            http = build_http_server(runtime=runtime, port=0)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            gate_url = f"http://127.0.0.1:{http.server_address[1]}"

            try:
                claude_bridge = OpenLineMCPBridge(
                    BridgeConfig(
                        bundle_path=workspace / "current.olw",
                        subject_key_path=workspace / "subjects" / "claude.key",
                        subject_id=CLAUDE_SUBJECT,
                        mandate_id=CLAUDE_MANDATE,
                        gate_url=gate_url,
                        provider_label="claude",
                    )
                )
                async with Client(build_mcp_server(claude_bridge)) as client:
                    before = await client.call_tool(
                        "deploy_staging", {"release": "release-claude-before"}
                    )
                self.assertIsNotNone(before.structured_content)
                self.assertEqual(before.structured_content["decision"], "ALLOWED")
                self.assertTrue(before.structured_content["effect_applied"])

                switch_workspace(workspace)

                async with Client(build_mcp_server(claude_bridge)) as client:
                    old = await client.call_tool(
                        "deploy_staging", {"release": "release-claude-after"}
                    )
                self.assertEqual(old.structured_content["decision"], "STOPPED")
                self.assertEqual(
                    old.structured_content["reason_codes"], ["MANDATE_REVOKED"]
                )
                self.assertFalse(old.structured_content["effect_applied"])

                codex_bridge = OpenLineMCPBridge(
                    BridgeConfig(
                        bundle_path=workspace / "current.olw",
                        subject_key_path=workspace / "subjects" / "codex.key",
                        subject_id=CODEX_SUBJECT,
                        mandate_id=CODEX_MANDATE,
                        gate_url=gate_url,
                        provider_label="codex",
                    )
                )
                async with Client(build_mcp_server(codex_bridge)) as client:
                    current = await client.call_tool(
                        "deploy_staging", {"release": "release-codex-after"}
                    )
                self.assertEqual(current.structured_content["decision"], "ALLOWED")
                self.assertTrue(current.structured_content["effect_applied"])

                result = verify_workspace(workspace)
                self.assertEqual(result["verdict"], VERDICT)
                self.assertTrue(all(result["checks"].values()))

                effects = strict_json_load(workspace / "receiver" / "effects.json")
                self.assertEqual(len(effects), 2)
                self.assertEqual(
                    [effect["subject_id"] for effect in effects],
                    [CLAUDE_SUBJECT, CODEX_SUBJECT],
                )

                # Provider-specific subject secrets are distinct and never exported.
                claude_secret = (workspace / "subjects" / "claude.key").read_text().strip()
                codex_secret = (workspace / "subjects" / "codex.key").read_text().strip()
                self.assertNotEqual(claude_secret, codex_secret)
                exported = (workspace / "current.olw").read_text()
                configs = (
                    (workspace / "claude-mcp.json").read_text()
                    + (workspace / "codex-mcp.toml").read_text()
                )
                self.assertNotIn(claude_secret, exported)
                self.assertNotIn(codex_secret, exported)
                self.assertNotIn(claude_secret, configs)
                self.assertNotIn(codex_secret, configs)

                # The real Codex host is non-interactive in CI. Its config must
                # allow only the intended MCP tool and pre-approve only that tool;
                # the receiver Gate still owns whether the effect is allowed.
                codex_config = (workspace / "codex-mcp.toml").read_text()
                self.assertIn('enabled_tools = ["deploy_staging"]', codex_config)
                self.assertIn(
                    "[mcp_servers.openline_wallet.tools.deploy_staging]",
                    codex_config,
                )
                self.assertIn('approval_mode = "approve"', codex_config)

                bundle = strict_json_load(workspace / "current.olw")
                self.assertTrue(
                    any(
                        receipt.get("subject_id") == CLAUDE_SUBJECT
                        and receipt.get("decision") == "ALLOWED"
                        for receipt in bundle["receipts"]
                    )
                )
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
