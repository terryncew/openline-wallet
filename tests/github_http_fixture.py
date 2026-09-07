"""Loopback HTTP fixture for the exact GitHub REST request contract.

The opener routes the official host to localhost using only a disposable test
credential. Production GitHubClient never accepts a configurable provider host.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener
import json

from openline_wallet.github_effect import _NoRedirect
from test_github_effect import FakeGitHub, TARGET, MERGE_SHA


class Fixture:
    def __init__(self):
        self.provider = FakeGitHub()
        self.requests = []
        self.drop_after_merge = False
        self.default_branch = "main"
        self.protected = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, value, status=200):
                raw = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def handle_request(self, method):
                fixture.requests.append((method, self.path))
                root = "/repos/" + TARGET.repository
                pr = root + "/pulls/" + str(TARGET.number)
                if method == "GET" and self.path == root:
                    return self.reply({"full_name": TARGET.repository, "id": TARGET.repository_id,
                                       "archived": False, "default_branch": fixture.default_branch})
                if method == "GET" and self.path == root + "/branches/" + TARGET.base_ref:
                    return self.reply({"name": TARGET.base_ref, "protected": fixture.protected,
                                       "commit": {"sha": fixture.provider.base}})
                if method == "GET" and self.path == pr:
                    value = fixture.provider.pr(TARGET)
                    value.update({"draft": False, "mergeable": True, "mergeable_state": "clean"})
                    value["base"]["repo"]["default_branch"] = fixture.default_branch
                    return self.reply(value)
                if method == "GET" and self.path == root + "/git/commits/" + MERGE_SHA:
                    return self.reply(fixture.provider.commit(TARGET, MERGE_SHA))
                if method == "PUT" and self.path == pr + "/merge":
                    try:
                        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                        if body != {"sha": TARGET.head_sha, "merge_method": "merge"}:
                            return self.reply({"message": "wrong binding"}, 409)
                        value = fixture.provider.merge(TARGET)
                        if fixture.drop_after_merge:
                            self.connection.shutdown(2)
                            self.connection.close()
                            return
                        return self.reply(value)
                    except Exception:
                        return self.reply({"message": "merge rejected"}, 409)
                if self.path == root + "/pulls/" + str(TARGET.number) + "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "https://example.invalid/secret")
                    self.end_headers()
                    return
                return self.reply({"message": "not found"}, 404)

            def do_GET(self):
                self.handle_request("GET")

            def do_PUT(self):
                self.handle_request("PUT")

            def do_POST(self):
                self.handle_request("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def opener(self):
        address = "http://127.0.0.1:" + str(self.server.server_port)
        delegate = build_opener(ProxyHandler({}), _NoRedirect)

        class LocalOpener:
            def open(self, request, timeout=None):
                url = urlsplit(request.full_url)
                forwarded = Request(address + url.path, data=request.data, method=request.get_method(),
                                    headers=dict(request.header_items()))
                return delegate.open(forwarded, timeout=timeout)

        return LocalOpener()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
