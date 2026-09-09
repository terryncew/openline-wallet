"""Receiver-owned MCP egress gate for consequential tool calls.

This module is intentionally small. MCP remains the transport. Wallet supplies
portable subject authority. The receiver-owned contract constrains one tool
call's arguments, and the receiver signs the exact proposal and outcome.

The reference implementation is a local/development shim, not a hosted policy
service or a general policy language.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import canonical_json, strict_json_load
from .clock import isoformat, utc_now
from .crypto import (
    load_private_key,
    public_key_hex,
    record_hash,
    save_private_key,
    sha256_hex,
    sign_record,
)
from .effect_closure import EffectGate
from .errors import WalletError
from .receiver import create_presentation
from .storage import atomic_write_json

MCP_CONTRACT_SCHEMA = "openline.receiver.mcp_contract.v1"
CONSEQUENCE_RECEIPT_SCHEMA = "openline.receiver.consequence_receipt.v1"
MCP_TOOL_CALL_METHOD = "tools/call"
MCP_2026_PROTOCOL = "2026-07-28"
_MAX_BODY_BYTES = 2 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@dataclass(frozen=True)
class MCPProposal:
    request_id: Any
    method: str
    name: str
    arguments: dict[str, Any]
    proposal_hash: str
    body: dict[str, Any]


@dataclass(frozen=True)
class ReceiverContract:
    """One owner-defined tool boundary with one integer range rule.

    This is deliberately not a general policy engine. EGRESS-GATE-001 needs one
    parameter rule strong enough to distinguish schema validity from receiver
    acceptability.
    """

    contract_id: str
    wallet_scope: str
    tool_name: str
    rule_id: str
    parameter: str
    minimum: int
    maximum: int
    contract_hash: str
    raw: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReceiverContract":
        expected = {"schema", "contract_id", "wallet_scope", "tool_name", "rule"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise WalletError("MCP_CONTRACT_SHAPE_INVALID")
        if value.get("schema") != MCP_CONTRACT_SCHEMA:
            raise WalletError("MCP_CONTRACT_SCHEMA_INVALID")
        contract_id = str(value.get("contract_id", ""))
        wallet_scope = str(value.get("wallet_scope", ""))
        tool_name = str(value.get("tool_name", ""))
        if _ID.fullmatch(contract_id) is None or _ID.fullmatch(wallet_scope) is None:
            raise WalletError("MCP_CONTRACT_ID_OR_SCOPE_INVALID")
        if not tool_name or len(tool_name) > 255:
            raise WalletError("MCP_CONTRACT_TOOL_INVALID")
        rule = value.get("rule")
        if not isinstance(rule, Mapping) or set(rule) != {"rule_id", "parameter", "minimum", "maximum"}:
            raise WalletError("MCP_CONTRACT_RULE_INVALID")
        rule_id = str(rule.get("rule_id", ""))
        parameter = str(rule.get("parameter", ""))
        minimum = rule.get("minimum")
        maximum = rule.get("maximum")
        if _ID.fullmatch(rule_id) is None or not parameter or len(parameter) > 128:
            raise WalletError("MCP_CONTRACT_RULE_INVALID")
        if isinstance(minimum, bool) or isinstance(maximum, bool) or not isinstance(minimum, int) or not isinstance(maximum, int):
            raise WalletError("MCP_CONTRACT_RANGE_INVALID")
        if minimum > maximum:
            raise WalletError("MCP_CONTRACT_RANGE_INVALID")
        raw = dict(value)
        return cls(
            contract_id=contract_id,
            wallet_scope=wallet_scope,
            tool_name=tool_name,
            rule_id=rule_id,
            parameter=parameter,
            minimum=minimum,
            maximum=maximum,
            contract_hash=record_hash(raw),
            raw=raw,
        )

    @classmethod
    def load(cls, path: str | Path) -> "ReceiverContract":
        value = strict_json_load(path)
        if not isinstance(value, Mapping):
            raise WalletError("MCP_CONTRACT_OBJECT_REQUIRED")
        return cls.from_mapping(value)

    def reasons(self, proposal: MCPProposal) -> list[str]:
        if proposal.name != self.tool_name:
            return ["TOOL_OUTSIDE_CONTRACT"]
        if self.parameter not in proposal.arguments:
            return ["CONTRACT_PARAMETER_MISSING"]
        value = proposal.arguments[self.parameter]
        if isinstance(value, bool) or not isinstance(value, int):
            return ["CONTRACT_PARAMETER_NOT_INTEGER"]
        if value < self.minimum:
            return ["CONTRACT_PARAMETER_BELOW_MINIMUM"]
        if value > self.maximum:
            return ["CONTRACT_PARAMETER_ABOVE_MAXIMUM"]
        return []


def _normalized_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


def parse_mcp_tool_call(headers: Mapping[str, Any] | None, body: Mapping[str, Any]) -> MCPProposal:
    """Parse one MCP tools/call request and validate modern routing headers.

    For protocol version 2026-07-28 the standard method/name headers are
    required. On older/unspecified transports, present headers must still agree
    with the body.
    """
    if not isinstance(body, Mapping):
        raise WalletError("MCP_JSON_OBJECT_REQUIRED")
    if body.get("jsonrpc") != "2.0":
        raise WalletError("MCP_JSONRPC_VERSION_INVALID")
    method = body.get("method")
    if method != MCP_TOOL_CALL_METHOD:
        raise WalletError("MCP_METHOD_UNSUPPORTED", str(method))
    params = body.get("params")
    if not isinstance(params, Mapping):
        raise WalletError("MCP_PARAMS_OBJECT_REQUIRED")
    name = params.get("name")
    if not isinstance(name, str) or not name or len(name) > 255:
        raise WalletError("MCP_TOOL_NAME_INVALID")
    arguments = params.get("arguments", {})
    if not isinstance(arguments, Mapping):
        raise WalletError("MCP_ARGUMENTS_OBJECT_REQUIRED")

    normalized = _normalized_headers(headers)
    protocol = normalized.get("mcp-protocol-version")
    method_header = normalized.get("mcp-method")
    name_header = normalized.get("mcp-name")
    if protocol == MCP_2026_PROTOCOL and (method_header is None or name_header is None):
        raise WalletError("MCP_2026_ROUTING_HEADERS_REQUIRED")
    if method_header is not None and method_header != method:
        raise WalletError("MCP_METHOD_HEADER_MISMATCH")
    if name_header is not None and name_header != name:
        raise WalletError("MCP_NAME_HEADER_MISMATCH")

    proposal_body = {
        "method": method,
        "name": name,
        "arguments": dict(arguments),
    }
    return MCPProposal(
        request_id=body.get("id"),
        method=method,
        name=name,
        arguments=dict(arguments),
        proposal_hash=record_hash(proposal_body),
        body=dict(body),
    )


class HTTPJSONRPCDownstream:
    """Forward one JSON-RPC request to the target MCP tool endpoint."""

    def __init__(self, url: str, *, timeout_seconds: float = 10.0) -> None:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise WalletError("MCP_UPSTREAM_URL_INVALID")
        self.url = url
        self.timeout_seconds = timeout_seconds

    def __call__(self, proposal: MCPProposal, headers: Mapping[str, Any] | None) -> dict[str, Any]:
        incoming = _normalized_headers(headers)
        forwarded = {"Content-Type": "application/json", "Accept": "application/json"}
        for key, value in incoming.items():
            if key in {"mcp-protocol-version", "mcp-method", "mcp-name"} or key.startswith("mcp-param-"):
                forwarded[key] = value
        request = Request(
            self.url,
            data=json.dumps(proposal.body, separators=(",", ":")).encode("utf-8"),
            headers=forwarded,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                value = json.loads(raw)
        except HTTPError as exc:
            raise RuntimeError(f"DOWNSTREAM_HTTP_{exc.code}_OUTCOME_UNRESOLVED") from exc
        except (URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"DOWNSTREAM_{type(exc).__name__}_OUTCOME_UNRESOLVED") from exc
        if not isinstance(value, dict):
            raise RuntimeError("DOWNSTREAM_NON_OBJECT_OUTCOME_UNRESOLVED")
        return value


class MCPConsequenceGate:
    """Receiver-side contract + Wallet authority + signed consequence receipt."""

    def __init__(
        self,
        gate: EffectGate,
        contract: ReceiverContract,
        receipt_dir: str | Path,
        downstream: Callable[[MCPProposal, Mapping[str, Any] | None], dict[str, Any]],
    ) -> None:
        if not isinstance(gate, EffectGate):
            raise WalletError("EFFECT_GATE_REQUIRED")
        self.gate = gate
        self.contract = contract
        self.receipt_dir = Path(receipt_dir).resolve()
        self.receipt_dir.mkdir(parents=True, exist_ok=True)
        self.downstream = downstream

    def _receipt(
        self,
        proposal: MCPProposal,
        *,
        decision: str,
        reasons: list[str],
        effect_applied: bool | None,
        authority_receipt_hash: str | None,
        frontier_receipt_hash: str | None,
        downstream_result_hash: str | None,
    ) -> dict[str, Any]:
        return sign_record(
            {
                "schema": CONSEQUENCE_RECEIPT_SCHEMA,
                "gate_id": self.gate.gate_id,
                "gate_public_key": self.gate.public_key,
                "contract_id": self.contract.contract_id,
                "contract_hash": self.contract.contract_hash,
                "rule_id": self.contract.rule_id,
                "wallet_scope": self.contract.wallet_scope,
                "mcp_method": proposal.method,
                "mcp_name": proposal.name,
                "proposal_hash": proposal.proposal_hash,
                "decision": decision,
                "reason_codes": list(reasons),
                "effect_applied": effect_applied,
                "authority_receipt_hash": authority_receipt_hash,
                "frontier_receipt_hash": frontier_receipt_hash,
                "downstream_result_hash": downstream_result_hash,
                "decided_at": isoformat(utc_now()),
                "decision_authority": "RECEIVER_GATE",
            },
            self.gate.gate_key,
        )

    def _persist(self, receipt: Mapping[str, Any]) -> str:
        digest = record_hash(receipt)
        atomic_write_json(self.receipt_dir / f"{digest}.json", dict(receipt), mode=0o644)
        return digest

    def _result(self, receipt: dict[str, Any], downstream_result: dict[str, Any] | None = None) -> dict[str, Any]:
        digest = self._persist(receipt)
        return {
            "decision": receipt["decision"],
            "reason_codes": receipt["reason_codes"],
            "effect_applied": receipt["effect_applied"],
            "receipt_hash": digest,
            "receipt": receipt,
            "downstream": downstream_result,
        }

    def execute(
        self,
        *,
        headers: Mapping[str, Any] | None,
        body: Mapping[str, Any],
        presentation: Mapping[str, Any],
    ) -> dict[str, Any]:
        proposal = parse_mcp_tool_call(headers, body)
        contract_reasons = self.contract.reasons(proposal)
        if contract_reasons:
            return self._result(self._receipt(
                proposal,
                decision="REJECTED",
                reasons=contract_reasons,
                effect_applied=False,
                authority_receipt_hash=None,
                frontier_receipt_hash=None,
                downstream_result_hash=None,
            ))

        admission = self.gate.evaluate(presentation, expected_action=self.contract.wallet_scope)
        admission_hash = record_hash(admission)
        if admission["decision"] != "ALLOWED":
            return self._result(self._receipt(
                proposal,
                decision="REJECTED",
                reasons=list(admission["reason_codes"]),
                effect_applied=False,
                authority_receipt_hash=admission_hash,
                frontier_receipt_hash=None,
                downstream_result_hash=None,
            ))

        downstream_box: dict[str, dict[str, Any]] = {}

        def apply(_frontier_receipt: dict[str, Any]) -> None:
            downstream_box["result"] = self.downstream(proposal, headers)

        try:
            frontier, applied = self.gate.effect_frontier(admission, apply)
        except Exception:
            # The target may have committed before the acknowledgement failed.
            # Do not retry. Preserve uncertainty explicitly.
            return self._result(self._receipt(
                proposal,
                decision="UNRESOLVED",
                reasons=["DOWNSTREAM_OUTCOME_UNRESOLVED"],
                effect_applied=None,
                authority_receipt_hash=admission_hash,
                frontier_receipt_hash=None,
                downstream_result_hash=None,
            ))

        frontier_hash = record_hash(frontier)
        if not applied:
            return self._result(self._receipt(
                proposal,
                decision="REJECTED",
                reasons=list(frontier["reason_codes"]),
                effect_applied=False,
                authority_receipt_hash=admission_hash,
                frontier_receipt_hash=frontier_hash,
                downstream_result_hash=None,
            ))

        downstream_result = downstream_box.get("result")
        if downstream_result is None:
            return self._result(self._receipt(
                proposal,
                decision="UNRESOLVED",
                reasons=["DOWNSTREAM_RESULT_MISSING"],
                effect_applied=None,
                authority_receipt_hash=admission_hash,
                frontier_receipt_hash=frontier_hash,
                downstream_result_hash=None,
            ))
        result_hash = sha256_hex(canonical_json(downstream_result))
        return self._result(
            self._receipt(
                proposal,
                decision="EXECUTED",
                reasons=[],
                effect_applied=True,
                authority_receipt_hash=admission_hash,
                frontier_receipt_hash=frontier_hash,
                downstream_result_hash=result_hash,
            ),
            downstream_result=downstream_result,
        )


@dataclass
class LocalWalletEgressRuntime:
    """Development shim: local Wallet subject credentials + receiver boundary."""

    gate: EffectGate
    consequence: MCPConsequenceGate
    bundle_path: Path
    subject_key_path: Path
    subject_id: str
    mandate_id: str

    def handle(self, headers: Mapping[str, Any] | None, body: Mapping[str, Any]) -> dict[str, Any]:
        bundle = strict_json_load(self.bundle_path)
        if not isinstance(bundle, Mapping):
            raise WalletError("BUNDLE_REQUIRED")
        self.gate.admit_bundle(bundle)
        challenge = self.gate.issue_challenge(
            principal_id=str(bundle["principal"]["principal_id"]),
            subject_id=self.subject_id,
            action=self.consequence.contract.wallet_scope,
        )
        presentation = create_presentation(
            bundle=bundle,
            mandate_id=self.mandate_id,
            subject_id=self.subject_id,
            subject_key=load_private_key(self.subject_key_path),
            action=self.consequence.contract.wallet_scope,
            receiver_challenge=challenge,
        )
        return self.consequence.execute(headers=headers, body=body, presentation=presentation)


class _ProxyHandler(BaseHTTPRequestHandler):
    server_version = "OpenLineReceiverGate/0.1"

    @property
    def runtime(self) -> LocalWalletEgressRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    def _send(self, status: int, body: Mapping[str, Any], *, receipt_hash: str | None = None,
              decision: str | None = None) -> None:
        encoded = json.dumps(dict(body), separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        if receipt_hash is not None:
            self.send_header("OpenLine-Receipt-Hash", receipt_hash)
        if decision is not None:
            self.send_header("OpenLine-Decision", decision)
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise WalletError("HTTP_CONTENT_LENGTH_INVALID") from exc
        if length <= 0 or length > _MAX_BODY_BYTES:
            raise WalletError("HTTP_BODY_SIZE_INVALID")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WalletError("HTTP_JSON_INVALID") from exc
        if not isinstance(value, dict):
            raise WalletError("HTTP_JSON_OBJECT_REQUIRED")
        return value

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {
                "status": "ok",
                "gate_id": self.runtime.gate.gate_id,
                "contract_id": self.runtime.consequence.contract.contract_id,
            })
            return
        self._send(404, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/mcp":
            self._send(404, {"error": "NOT_FOUND"})
            return
        body: dict[str, Any] | None = None
        try:
            body = self._body()
            result = self.runtime.handle(self.headers, body)
            decision = result["decision"]
            receipt_hash = result["receipt_hash"]
            if decision == "EXECUTED":
                self._send(200, result["downstream"], receipt_hash=receipt_hash, decision=decision)
                return
            code = -32041 if decision == "REJECTED" else -32042
            message = "OpenLine receiver rejected tool call" if decision == "REJECTED" else "OpenLine downstream outcome unresolved"
            error = {
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {
                    "code": code,
                    "message": message,
                    "data": {"reason_codes": result["reason_codes"], "receipt_hash": receipt_hash},
                },
            }
            self._send(200 if decision == "REJECTED" else 502, error,
                       receipt_hash=receipt_hash, decision=decision)
        except WalletError as exc:
            self._send(400, {
                "jsonrpc": "2.0",
                "id": body.get("id") if isinstance(body, dict) else None,
                "error": {"code": -32600, "message": exc.code},
            })
        except Exception as exc:
            self._send(500, {
                "jsonrpc": "2.0",
                "id": body.get("id") if isinstance(body, dict) else None,
                "error": {"code": -32603, "message": "RECEIVER_INTERNAL_ERROR", "data": type(exc).__name__},
            })

    def log_message(self, fmt: str, *args: object) -> None:
        return


class EgressHTTPServer(ThreadingHTTPServer):
    runtime: LocalWalletEgressRuntime


def build_http_server(*, runtime: LocalWalletEgressRuntime, host: str = "127.0.0.1",
                      port: int = 8770) -> EgressHTTPServer:
    if host not in _LOOPBACK_HOSTS:
        raise WalletError("DEMO_GATE_MUST_BE_LOOPBACK", host)
    server = EgressHTTPServer((host, port), _ProxyHandler)
    server.runtime = runtime
    return server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openline-wallet-egress",
        description="Development MCP Receiver Gate for consequential tools/call requests.",
    )
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--subject-key", required=True)
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--mandate-id", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--upstream-url", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--receiver-key", required=True)
    parser.add_argument("--gate-id", default="openline-mcp-receiver")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    bundle_path = Path(args.bundle).resolve()
    bundle = strict_json_load(bundle_path)
    if not isinstance(bundle, Mapping):
        raise WalletError("BUNDLE_REQUIRED")
    receiver_key_path = Path(args.receiver_key).resolve()
    if receiver_key_path.exists():
        receiver_key = load_private_key(receiver_key_path)
    else:
        receiver_key = Ed25519PrivateKey.generate()
        save_private_key(receiver_key_path, receiver_key)
    gate = EffectGate(args.gate_id, gate_key=receiver_key)
    gate.pin_principal(
        str(bundle["principal"]["principal_id"]),
        str(bundle["principal"]["root_public_key"]),
    )
    contract = ReceiverContract.load(args.contract)
    consequence = MCPConsequenceGate(
        gate,
        contract,
        args.receipts,
        HTTPJSONRPCDownstream(args.upstream_url),
    )
    runtime = LocalWalletEgressRuntime(
        gate=gate,
        consequence=consequence,
        bundle_path=bundle_path,
        subject_key_path=Path(args.subject_key).resolve(),
        subject_id=args.subject_id,
        mandate_id=args.mandate_id,
    )
    server = build_http_server(runtime=runtime, host=args.host, port=args.port)
    address, actual_port = server.server_address[:2]
    print(f"Receiver Gate  http://{address}:{actual_port}/mcp")
    print(f"Contract       {contract.contract_id}  {contract.contract_hash}")
    print(f"Receiver key   {public_key_hex(receiver_key)}")
    print("Boundary       local development shim; tools/call only")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
