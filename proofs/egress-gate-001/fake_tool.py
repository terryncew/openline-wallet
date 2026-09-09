"""Independent fake MCP target for EGRESS-GATE-001.

The witness file belongs to the target, not to OpenLine's receipt trail. If a
rejected call reaches this process, the counter changes and the proof fails.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    server_version = "EgressGateFakeTool/1"

    @property
    def witness(self) -> Path:
        return self.server.witness  # type: ignore[attr-defined]

    def _send(self, status: int, value: dict) -> None:
        body = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "ok"})
            return
        self._send(404, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/mcp":
            self._send(404, {"error": "NOT_FOUND"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        params = body.get("params", {})
        arguments = params.get("arguments", {})
        record = {
            "id": body.get("id"),
            "method": body.get("method"),
            "name": params.get("name"),
            "amount": arguments.get("amount"),
        }
        self.witness.parent.mkdir(parents=True, exist_ok=True)
        with self.witness.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
        self._send(200, {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "result": {"applied": arguments.get("amount")},
        })

    def log_message(self, fmt: str, *args: object) -> None:
        return


class Server(ThreadingHTTPServer):
    witness: Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--witness", required=True)
    args = parser.parse_args()
    server = Server((args.host, args.port), Handler)
    server.witness = Path(args.witness).resolve()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
