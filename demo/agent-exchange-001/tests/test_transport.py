"""Tests for LocalMailboxTransport. Plain unittest, stdlib only."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchange.transport import LocalMailboxTransport  # noqa: E402


class TransportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.maildir = Path(self.tmp.name) / "mail"
        self.t = LocalMailboxTransport(self.maildir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_send_receive_round_trip(self):
        body = {"need_id": "need-abc"}
        msg_id = self.t.send("buyer", "matcher", "MATCH_REQUEST", body)
        self.assertTrue(msg_id.startswith("msg-"))
        got = self.t.receive("matcher")
        self.assertEqual(len(got), 1)
        env = got[0]
        self.assertEqual(env["msg_id"], msg_id)
        self.assertEqual(env["sender"], "buyer")
        self.assertEqual(env["recipient"], "matcher")
        self.assertEqual(env["kind"], "MATCH_REQUEST")
        self.assertEqual(env["body"], body)
        self.assertIn("at", env)

    def test_receive_clears_mailbox(self):
        self.t.send("a", "b", "NEED_POSTED", {})
        self.assertEqual(len(self.t.receive("b")), 1)
        self.assertEqual(self.t.receive("b"), [])
        self.assertEqual(self.t.peek("b"), [])

    def test_peek_does_not_clear(self):
        self.t.send("a", "b", "NEED_POSTED", {"x": 1})
        self.assertEqual(len(self.t.peek("b")), 1)
        self.assertEqual(len(self.t.peek("b")), 1)
        self.assertEqual(len(self.t.receive("b")), 1)

    def test_messages_are_ordered(self):
        ids = [self.t.send("a", "b", "NEED_POSTED", {"n": i}) for i in range(5)]
        got = self.t.receive("b")
        self.assertEqual([m["msg_id"] for m in got], ids)
        self.assertEqual([m["body"]["n"] for m in got], list(range(5)))

    def test_mailboxes_are_per_recipient(self):
        self.t.send("a", "b", "NEED_POSTED", {})
        self.t.send("a", "c", "NEED_POSTED", {})
        self.assertEqual(len(self.t.receive("b")), 1)
        self.assertEqual(len(self.t.receive("c")), 1)
        self.assertEqual(self.t.receive("b"), [])

    def test_unknown_recipient_reads_empty(self):
        self.assertEqual(self.t.peek("nobody"), [])
        self.assertEqual(self.t.receive("nobody"), [])

    def test_ids_stable_across_transport_instances(self):
        # Same sequence of sends over two separate maildirs -> same ids.
        other = LocalMailboxTransport(Path(self.tmp.name) / "mail2")
        id1 = self.t.send("a", "b", "NEED_POSTED", {"n": 1})
        id2 = other.send("a", "b", "NEED_POSTED", {"n": 1})
        self.assertEqual(id1, id2)

    def test_two_instances_share_sequence(self):
        # Two instances on the same maildir increment one counter: no id reuse.
        second = LocalMailboxTransport(self.maildir)
        id1 = self.t.send("a", "b", "NEED_POSTED", {"n": 1})
        id2 = second.send("a", "b", "NEED_POSTED", {"n": 1})
        self.assertNotEqual(id1, id2)
        got = [m["msg_id"] for m in self.t.receive("b")]
        self.assertEqual(got, [id1, id2])

    def test_seq_counter_survives_recreate(self):
        id1 = self.t.send("a", "b", "NEED_POSTED", {})
        fresh = LocalMailboxTransport(self.maildir)
        id2 = fresh.send("a", "b", "NEED_POSTED", {})
        self.assertNotEqual(id1, id2)

    def test_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            self.t.send("a", "b", "NOT_A_KIND", {})

    def test_rejects_non_dict_body(self):
        with self.assertRaises(TypeError):
            self.t.send("a", "b", "NEED_POSTED", "body")


if __name__ == "__main__":
    unittest.main()
