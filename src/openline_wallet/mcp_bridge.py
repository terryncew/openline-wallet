"""MCP sidecar that presents user-owned OpenLine authority to a receiver Gate.

MCP is transport only. The model host gets one tool. The subject private key
stays in this user-controlled process; the receiver sees only signed holder
presentations and the public Wallet bundle.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .canonical import strict_json_load
from .crypto import load_private_key
from .receiver import create_presentation


@dataclass(frozen=True)
class BridgeConfig:
    bundle_path: Path
    subject_key_path: Path
    subject_id: str
    mandate_id: str
    gate_url: str
    provider_label: str


class ReceiverHTTPClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=encoded,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except Exception:
                detail = {"error": "HTTP_ERROR", "status": exc.code}
            raise RuntimeError(f"receiver refused request: {detail}") from exc
        except (URLError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"receiver unavailable: {type(exc).__name__}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("receiver returned non-object JSON")
        return value


class OpenLineMCPBridge:
    ACTION = "deploy:staging"

    def __init__(
        self,
        config: BridgeConfig,
        *,
        receiver: ReceiverHTTPClient | None = None,
    ) -> None:
        self.config = config
        self.receiver = receiver or ReceiverHTTPClient(config.gate_url)

    def deploy_staging(self, release: str) -> dict[str, Any]:
        """Request one staging deployment under the user's current Wallet history."""
        bundle = strict_json_load(self.config.bundle_path)
        subject_key = load_private_key(self.config.subject_key_path)

        self.receiver.post("/admit", {"bundle": bundle})
        challenge = self.receiver.post(
            "/challenge",
            {
                "principal_id": bundle["principal"]["principal_id"],
                "subject_id": self.config.subject_id,
                "action": self.ACTION,
            },
        )
        presentation = create_presentation(
            bundle=bundle,
            mandate_id=self.config.mandate_id,
            subject_id=self.config.subject_id,
            subject_key=subject_key,
            action=self.ACTION,
            receiver_challenge=str(challenge["challenge"]),
        )
        result = self.receiver.post(
            "/execute",
            {
                "presentation": presentation,
                "action": self.ACTION,
                "release": release,
            },
        )
        return {
            "provider": self.config.provider_label,
            "decision": result["decision"],
            "reason_codes": result["reason_codes"],
            "effect_applied": result["effect_applied"],
            "release": result["release"],
            "gate_id": result["gate_id"],
            "receipt_hash": result["receipt_hash"],
        }


def build_mcp_server(bridge: OpenLineMCPBridge):
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise RuntimeError(
            'MCP support is optional. Install with: pip install -e ".[mcp]"'
        ) from exc

    mcp = MCPServer("OpenLine Wallet")

    @mcp.tool()
    def deploy_staging(release: str) -> dict[str, Any]:
        """Deploy a named release to staging only if the receiver Gate allows it."""
        return bridge.deploy_staging(release)

    return mcp


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openline-wallet-mcp",
        description="Expose one Wallet-bound receiver action over MCP stdio.",
    )
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--subject-key", required=True)
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--mandate-id", required=True)
    parser.add_argument("--gate-url", required=True)
    parser.add_argument("--provider-label", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = BridgeConfig(
        bundle_path=Path(args.bundle).resolve(),
        subject_key_path=Path(args.subject_key).resolve(),
        subject_id=args.subject_id,
        mandate_id=args.mandate_id,
        gate_url=args.gate_url,
        provider_label=args.provider_label,
    )
    build_mcp_server(OpenLineMCPBridge(config)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
