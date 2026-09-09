from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.crypto import public_key_hex, verify_record
from openline_wallet.effect_closure import EffectGate
from openline_wallet.errors import WalletError
from openline_wallet.mcp_egress import (
    MCPConsequenceGate,
    MCP_CONTRACT_SCHEMA,
    MCP_2026_PROTOCOL,
    ReceiverContract,
    parse_mcp_tool_call,
)
from openline_wallet.receiver import create_presentation
from openline_wallet.wallet import Wallet


def request(amount: int, *, name: str = "account.set_limit") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": {"amount": amount}},
    }


def headers(*, name: str = "account.set_limit") -> dict[str, str]:
    return {
        "Mcp-Protocol-Version": MCP_2026_PROTOCOL,
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
    }


def contract() -> ReceiverContract:
    return ReceiverContract.from_mapping({
        "schema": MCP_CONTRACT_SCHEMA,
        "contract_id": "account-limit-v1",
        "wallet_scope": "mcp:account.set_limit",
        "tool_name": "account.set_limit",
        "rule": {
            "rule_id": "amount-0-5000",
            "parameter": "amount",
            "minimum": 0,
            "maximum": 5000,
        },
    })


class MCPParsingTests(unittest.TestCase):
    def test_modern_headers_must_match_body(self):
        proposal = parse_mcp_tool_call(headers(), request(2000))
        self.assertEqual((proposal.method, proposal.name), ("tools/call", "account.set_limit"))
        self.assertEqual(proposal.arguments["amount"], 2000)

        with self.assertRaisesRegex(WalletError, "MCP_NAME_HEADER_MISMATCH"):
            parse_mcp_tool_call(headers(name="other.tool"), request(2000))

    def test_modern_protocol_requires_routing_headers(self):
        with self.assertRaisesRegex(WalletError, "MCP_2026_ROUTING_HEADERS_REQUIRED"):
            parse_mcp_tool_call({"Mcp-Protocol-Version": MCP_2026_PROTOCOL}, request(2000))

    def test_contract_is_stricter_than_broad_integer_schema(self):
        proposal = parse_mcp_tool_call(headers(), request(50000))
        broad_schema_valid = isinstance(proposal.arguments["amount"], int) and 0 <= proposal.arguments["amount"] <= 1_000_000
        self.assertTrue(broad_schema_valid)
        self.assertEqual(contract().reasons(proposal), ["CONTRACT_PARAMETER_ABOVE_MAXIMUM"])


class MCPConsequenceGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.wallet = Wallet.create(self.root / "wallet")
        self.subject_id = "worker"
        self.subject_key = Ed25519PrivateKey.generate()
        self.mandate_id = "mcp-account-limit-worker"
        self.scope = "mcp:account.set_limit"
        self.wallet.grant(
            subject_id=self.subject_id,
            subject_public_key=public_key_hex(self.subject_key),
            scopes=[self.scope],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            mandate_id=self.mandate_id,
        )
        self.gate = EffectGate("mcp-egress-test")
        self.gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        self.calls: list[int] = []

        def downstream(proposal, _headers):
            amount = proposal.arguments["amount"]
            self.calls.append(amount)
            return {"jsonrpc": "2.0", "id": proposal.request_id, "result": {"applied": amount}}

        self.runtime = MCPConsequenceGate(self.gate, contract(), self.root / "receipts", downstream)

    def tearDown(self):
        self.temp.cleanup()

    def presentation(self):
        bundle = self.wallet.export_bundle()
        self.gate.admit_bundle(bundle)
        challenge = self.gate.issue_challenge(
            principal_id=self.wallet.principal_id,
            subject_id=self.subject_id,
            action=self.scope,
        )
        return create_presentation(
            bundle=bundle,
            mandate_id=self.mandate_id,
            subject_id=self.subject_id,
            subject_key=self.subject_key,
            action=self.scope,
            receiver_challenge=challenge,
        )

    def test_allowed_call_reaches_downstream_once_and_receipt_is_signed(self):
        result = self.runtime.execute(headers=headers(), body=request(2000), presentation=self.presentation())
        self.assertEqual((result["decision"], result["effect_applied"], self.calls), ("EXECUTED", True, [2000]))
        valid, reason = verify_record(result["receipt"], expected_public_key=self.gate.public_key)
        self.assertEqual((valid, reason), (True, None))
        self.assertEqual(result["receipt"]["proposal_hash"], parse_mcp_tool_call(headers(), request(2000)).proposal_hash)

    def test_schema_valid_but_out_of_contract_call_never_reaches_downstream(self):
        result = self.runtime.execute(headers=headers(), body=request(50000), presentation=self.presentation())
        self.assertEqual(result["decision"], "REJECTED")
        self.assertEqual(result["reason_codes"], ["CONTRACT_PARAMETER_ABOVE_MAXIMUM"])
        self.assertEqual(self.calls, [])

    def test_revoked_subject_cannot_reach_downstream(self):
        self.wallet.revoke(self.subject_id)
        result = self.runtime.execute(headers=headers(), body=request(2000), presentation=self.presentation())
        self.assertEqual(result["decision"], "REJECTED")
        self.assertIn("MANDATE_REVOKED", result["reason_codes"])
        self.assertEqual(self.calls, [])

    def test_downstream_uncertainty_is_not_retried(self):
        calls = []

        def uncertain(proposal, _headers):
            calls.append(proposal.arguments["amount"])
            raise RuntimeError("ack lost")

        runtime = MCPConsequenceGate(self.gate, contract(), self.root / "uncertain-receipts", uncertain)
        result = runtime.execute(headers=headers(), body=request(2000), presentation=self.presentation())
        self.assertEqual((result["decision"], result["effect_applied"], calls), ("UNRESOLVED", None, [2000]))
        self.assertEqual(result["reason_codes"], ["DOWNSTREAM_OUTCOME_UNRESOLVED"])


if __name__ == "__main__":
    unittest.main()
