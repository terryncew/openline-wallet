"""Regression tests for acknowledged GitHub late settlement."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from openline_wallet.crypto import record_hash, verify_record
from openline_wallet.errors import WalletError
from openline_wallet.github_ack_reconcile import settle_acknowledged_merges
from openline_wallet.github_effect import GitHubMergeReceiver

import test_github_effect as fixture


class AcknowledgedGitHubSettlementTests(unittest.TestCase):
    setUp = fixture.GitHubEffectTests.setUp
    at = fixture.GitHubEffectTests.at
    presentation = fixture.GitHubEffectTests.presentation
    prepared = fixture.GitHubEffectTests.prepared
    revoke = fixture.GitHubEffectTests.revoke

    def _force_immediate_settlement_timeout(self):
        ticket = self.prepared()
        with patch(
            "openline_wallet.github_effect._settled_merge",
            side_effect=WalletError("GITHUB_MERGE_NOT_RECONCILED"),
        ):
            with self.assertRaisesRegex(
                WalletError, "GITHUB_MERGE_NOT_RECONCILED"
            ):
                self.receiver.finish(ticket, now=self.at(3))
        self.assertEqual(self.client.mutation_count, 1)
        return ticket

    def test_acknowledged_merge_can_confirm_late_without_retry(self):
        self._force_immediate_settlement_timeout()

        settled = settle_acknowledged_merges(
            self.receiver, now=self.at(5), wait_seconds=0
        )
        self.assertEqual(len(settled), 1)
        self.assertEqual(settled[0]["status"], "CONFIRMED")
        effect = settled[0]["effect_receipt"]
        self.assertEqual(effect["status"], "MERGE_CONFIRMED")
        self.assertEqual(effect["merge_commit_sha"], fixture.MERGE_SHA)
        self.assertTrue(
            verify_record(effect, expected_public_key=self.gate.public_key)[0]
        )
        self.assertEqual(self.client.mutation_count, 1)

        self.wallet.add_receipt(settled[0]["result"]["receipt"])
        closure = self.receiver.close(
            self.revoke(at=6), "grant-a", now=self.at(7)
        )
        self.assertEqual(closure["status"], "EFFECT_CLOSED")
        self.assertEqual(closure["active_frontiers"], 0)
        self.assertEqual(
            closure["confirmed_effect_hashes"], [record_hash(effect)]
        )
        self.assertEqual(closure["unattributed_merge_observations"], [])
        self.assertEqual(self.client.mutation_count, 1)

    def test_acknowledged_merge_can_confirm_after_receiver_restart(self):
        self._force_immediate_settlement_timeout()
        self.receiver.shutdown()

        recovered = GitHubMergeReceiver(
            self.gate,
            self.client,
            fixture.TARGET,
            self.root / "journal.sqlite",
        )
        self.receiver = recovered

        settled = settle_acknowledged_merges(
            recovered, now=self.at(5), wait_seconds=0
        )
        self.assertEqual(settled[0]["status"], "CONFIRMED")
        self.assertEqual(self.client.mutation_count, 1)

        self.wallet.add_receipt(settled[0]["result"]["receipt"])
        closure = recovered.close(
            self.revoke(at=6), "grant-a", now=self.at(7)
        )
        self.assertEqual(closure["status"], "EFFECT_CLOSED")
        self.assertEqual(len(closure["confirmed_effect_hashes"]), 1)
        self.assertEqual(self.client.mutation_count, 1)

    def test_transport_ambiguous_merge_is_never_promoted(self):
        ticket = self.prepared()
        self.client.fail_after_effect = True
        with self.assertRaisesRegex(
            WalletError, "GITHUB_TRANSPORT_UNCERTAIN"
        ):
            self.receiver.finish(ticket, now=self.at(3))

        self.assertEqual(self.client.mutation_count, 1)
        settled = settle_acknowledged_merges(
            self.receiver, now=self.at(5), wait_seconds=0
        )
        self.assertEqual(settled[0]["status"], "UNCERTAIN")
        self.assertEqual(
            settled[0]["reason"], "GITHUB_MERGE_NOT_ACKNOWLEDGED"
        )

        # Existing conservative reconciliation may observe that a merge
        # happened, but it remains unattributed because no success response was
        # durably recorded.
        observed = self.receiver.reconcile(now=self.at(6))
        self.assertEqual(observed[0]["status"], "OBSERVED_MERGED_UNATTRIBUTED")
        closure = self.receiver.close(
            self.revoke(at=7), "grant-a", now=self.at(8)
        )
        self.assertEqual(closure["confirmed_effect_hashes"], [])
        self.assertEqual(len(closure["unattributed_merge_observations"]), 1)
        self.assertEqual(self.client.mutation_count, 1)

    def test_wrong_late_merge_sha_remains_uncertain(self):
        self._force_immediate_settlement_timeout()
        self.client.merge_sha = "d" * 40

        settled = settle_acknowledged_merges(
            self.receiver, now=self.at(5), wait_seconds=0
        )
        self.assertEqual(settled[0]["status"], "UNCERTAIN")
        self.assertEqual(
            settled[0]["reason"], "GITHUB_MERGE_NOT_RECONCILED"
        )
        with self.assertRaisesRegex(
            WalletError, "GITHUB_EFFECT_OUTCOME_UNRESOLVED"
        ):
            self.receiver.close(
                self.revoke(at=6), "grant-a", now=self.at(7)
            )
        self.assertEqual(self.client.mutation_count, 1)

    def test_invalid_settlement_window_fails_closed(self):
        for value in (-1, 121, True, "60"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    WalletError, "GITHUB_SETTLEMENT_WAIT_INVALID"
                ):
                    settle_acknowledged_merges(
                        self.receiver, wait_seconds=value
                    )


if __name__ == "__main__":
    unittest.main()
