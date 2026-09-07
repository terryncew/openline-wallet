"""Real loopback HTTP and JSON transport, with a disposable fake GitHub state."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
import os

from openline_wallet.github_effect import GitHubClient, GitHubHTTPError, _preflight
from openline_wallet.github_effect_live import discover_target, run_experiment, recover, main
from github_http_fixture import Fixture
from test_github_effect import TARGET, MERGE_SHA


class GitHubHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.client = GitHubClient("disposable-fixture-token")
        self.client._opener = self.fixture.opener()

    def test_exact_rest_contract_and_read_only_discovery(self):
        target, safety = discover_target(self.client, TARGET.repository, TARGET.number)
        self.assertEqual(target, TARGET)
        self.assertFalse(safety["base_protected"])
        self.assertEqual(self.fixture.provider.mutation_count, 0)
        self.assertEqual(_preflight(self.client.pr(target), target)["head_sha"], TARGET.head_sha)
        self.assertEqual(self.client.merge(target)["sha"], MERGE_SHA)
        self.assertEqual(self.client.commit(target, MERGE_SHA)["parents"][1]["sha"], TARGET.head_sha)
        self.assertEqual(self.fixture.provider.mutation_count, 1)

    def test_all_other_methods_and_paths_fail_before_transport(self):
        count = len(self.fixture.requests)
        for method, path, body in [
            ("DELETE", "/repos/a/b/pulls/1", None),
            ("PUT", "/repos/a/b/pulls/1", {}),
            ("POST", "/repos/a/b/pulls/1/merge", {}),
            ("GET", "/repos/a/b/pulls/1/merge", None),
            ("PUT", "/repos/a/b/pulls/1/merge", {"sha": "a"*40, "merge_method": "squash"}),
            ("GET", "/repos/a/b/actions/secrets", None),
            ("GET", "/repos/a/b/branches/..%2Fsecret", None),
        ]:
            with self.subTest(method=method, path=path), self.assertRaises(Exception):
                self.client.request(method, path, body)
        self.assertEqual(len(self.fixture.requests), count)

    def test_transport_error_never_retries_a_successful_remote_effect(self):
        self.fixture.drop_after_merge = True
        with self.assertRaises(Exception):
            self.client.merge(TARGET)
        self.assertEqual(self.fixture.provider.mutation_count, 1)
        self.assertEqual(len([x for x in self.fixture.requests if x[0] == "PUT"]), 1)
        self.assertEqual(self.client.pr(TARGET)["merge_commit_sha"], MERGE_SHA)

    def test_discovery_rejects_default_and_protected_bases(self):
        self.fixture.default_branch = TARGET.base_ref
        with self.assertRaisesRegex(Exception, "GITHUB_DISPOSABLE_BASE_REQUIRED"):
            discover_target(self.client, TARGET.repository, TARGET.number)
        self.fixture.default_branch = "main"
        self.fixture.protected = True
        with self.assertRaisesRegex(Exception, "GITHUB_DISPOSABLE_BASE_REQUIRED"):
            discover_target(self.client, TARGET.repository, TARGET.number)
        self.assertEqual(self.fixture.provider.mutation_count, 0)

    def test_live_cli_refuses_wrong_confirmation_without_a_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target.json"
            target.write_text(json.dumps({"target": TARGET.to_record()}))
            with patch("openline_wallet.github_effect_live.GitHubClient", return_value=self.client), \
                 patch.dict(os.environ, {"OPENLINE_GITHUB_TOKEN": "disposable-fixture-token"}):
                status = main(["run", "--target", str(target), "--state", str(root/"state"),
                    "--output", str(root/"evidence"), "--confirm-repository", TARGET.repository,
                    "--confirm-pr", str(TARGET.number), "--confirm-action", "wrong", "--allow-merge"])
            self.assertEqual(status, 2)
            self.assertEqual(self.fixture.provider.mutation_count, 0)
            self.assertFalse((root/"state").exists())

    def test_full_experiment_and_read_only_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = run_experiment(self.client, TARGET, root/"state", root/"evidence")
            self.assertEqual(result["verdict"], "CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED")
            self.assertTrue(result["closure_blocked_during_ack"])
            self.assertEqual(result["merge_requests"], 1)
            self.assertEqual(len([x for x in self.fixture.requests if x[0] == "PUT"]), 1)
            self.assertEqual(self.fixture.provider.mutation_count, 1)
            recovery = recover(self.client, root/"state", root/"recovery")
            self.assertTrue(all(x["status"] == "EFFECT_CLOSED" for x in recovery["phases"]))
            self.assertEqual(len([x for x in self.fixture.requests if x[0] == "PUT"]), 1)


if __name__ == "__main__":
    unittest.main()
