"""Receiver-owned localhost Gate for PLATFORM-EXIT-LIVE-001.

This is deliberately a development-only receiver. It keeps authorization
decision state in memory, writes signed receipts to disk, and applies one safe
demo effect only after ReferenceGate returns ALLOWED.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
from typing import Any, Mapping

from .canonical import pretty_json
from .crypto import record_hash
from .errors import WalletError
from .receiver import ReferenceGate
from .storage import atomic_write_json


_MAX_BODY_BYTES = 2 * 1024 * 1024
_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="ascii"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WalletError("RECEIVER_STATE_READ_FAILED", str(exc)) from exc


@dataclass
class ReceiverRuntime:
    gate: ReferenceGate
    ledger_path: Path
    receipts_dir: Path

    @classmethod
    def create(
        cls,
        *,
        gate_id: str,
        principal_id: str,
        root_public_key: str,
        ledger_path: str | Path,
        receipts_dir: str | Path,
    ) -> "ReceiverRuntime":
        gate = ReferenceGate(gate_id)
        gate.pin_principal(principal_id, root_public_key)
        runtime = cls(gate, Path(ledger_path), Path(receipts_dir))
        runtime.receipts_dir.mkdir(parents=True, exist_ok=True)
        return runtime

    def admit(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        return self.gate.admit_bundle(bundle)

    def challenge(self, body: Mapping[str, Any]) -> dict[str, Any]:
        principal_id = str(body.get("principal_id", ""))
        subject_id = str(body.get("subject_id", ""))
        action = str(body.get("action", ""))
        token = self.gate.issue_challenge(
            principal_id=principal_id,
            subject_id=subject_id,
            action=action,
        )
        return {"challenge": token, "gate_id": self.gate.gate_id}

    def execute(self, body: Mapping[str, Any]) -> dict[str, Any]:
        presentation = body.get("presentation")
        action = str(body.get("action", ""))
        release = str(body.get("release", ""))
        if _RELEASE.fullmatch(release) is None:
            raise WalletError("RELEASE_ID_INVALID")

        receipt = self.gate.evaluate(
            presentation if isinstance(presentation, Mapping) else {},
            expected_action=action,
        )
        receipt_hash = record_hash(receipt)
        atomic_write_json(
            self.receipts_dir / f"{receipt_hash}.json",
            receipt,
            mode=0o644,
        )

        effect_applied = False
        if receipt["decision"] == "ALLOWED":
            if action != "deploy:staging":
                raise WalletError("RECEIVER_EFFECT_UNSUPPORTED", action)
            ledger = _read_json(self.ledger_path, [])
            if not isinstance(ledger, list):
                raise WalletError("RECEIVER_LEDGER_INVALID")
            effect = {
                "schema": "openline.platform_exit_live.effect.v1",
                "action": action,
                "release": release,
                "principal_id": receipt["principal_id"],
                "subject_id": receipt["subject_id"],
                "mandate_id": receipt["mandate_id"],
                "gate_id": receipt["gate_id"],
                "receipt_hash": receipt_hash,
                "decided_at": receipt["decided_at"],
            }
            ledger.append(effect)
            atomic_write_json(self.ledger_path, ledger, mode=0o644)
            effect_applied = True

        return {
            "decision": receipt["decision"],
            "reason_codes": list(receipt["reason_codes"]),
            "effect_applied": effect_applied,
            "release": release,
            "gate_id": receipt["gate_id"],
            "receipt_hash": receipt_hash,
            "receipt": receipt,
        }


class _Handler(BaseHTTPRequestHandler):
    server_version = "OpenLineGate/0.1"

    @property
    def runtime(self) -> ReceiverRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    def _send(self, status: int, body: Mapping[str, Any]) -> None:
        encoded = pretty_json(dict(body)).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
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

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path == "/health":
            self._send(200, {"status": "ok", "gate_id": self.runtime.gate.gate_id})
            return
        self._send(404, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        try:
            body = self._body()
            if self.path == "/admit":
                bundle = body.get("bundle")
                if not isinstance(bundle, Mapping):
                    raise WalletError("BUNDLE_REQUIRED")
                result = self.runtime.admit(bundle)
            elif self.path == "/challenge":
                result = self.runtime.challenge(body)
            elif self.path == "/execute":
                result = self.runtime.execute(body)
            else:
                self._send(404, {"error": "NOT_FOUND"})
                return
            self._send(200, result)
        except WalletError as exc:
            self._send(409, {"error": exc.code, "detail": str(exc)})
        except Exception as exc:  # development receiver: fail closed and expose only class
            self._send(500, {"error": "RECEIVER_INTERNAL_ERROR", "detail": type(exc).__name__})

    def log_message(self, fmt: str, *args: object) -> None:
        return


class GateHTTPServer(ThreadingHTTPServer):
    runtime: ReceiverRuntime


def build_http_server(
    *,
    runtime: ReceiverRuntime,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> GateHTTPServer:
    if host not in _LOOPBACK_HOSTS:
        raise WalletError("DEMO_GATE_MUST_BE_LOOPBACK", host)
    server = GateHTTPServer((host, port), _Handler)
    server.runtime = runtime
    return server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openline-wallet-gate",
        description="Development receiver Gate for PLATFORM-EXIT-LIVE-001.",
    )
    parser.add_argument("--principal-id", required=True)
    parser.add_argument("--root-public-key", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--gate-id", default="platform-exit-live-receiver")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime = ReceiverRuntime.create(
        gate_id=args.gate_id,
        principal_id=args.principal_id,
        root_public_key=args.root_public_key,
        ledger_path=args.ledger,
        receipts_dir=args.receipts,
    )
    server = build_http_server(runtime=runtime, host=args.host, port=args.port)
    address, port = server.server_address[:2]
    print(f"Receiver Gate  http://{address}:{port}")
    print("Effect         receiver-owned staging ledger")
    print("Boundary       development-only; Gate state is in memory")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
