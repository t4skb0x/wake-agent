import os
import unittest

from tests.helpers import MemoryChannel, make_env
from wakeagent.reporter import Reporter


class ReporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.cfg = make_env()
        self.r = Reporter(self.cfg)

    def test_routing_by_severity(self):
        self.r.notify("info", "t", "x")
        self.r.notify("urgent", "t2", "y", dedup_key="k")
        names = [(n, s) for n, s, _, _ in MemoryChannel.sent]
        self.assertEqual(names, [("chat", "info"), ("chat", "urgent"), ("sms", "urgent")])

    def test_dedup_counts_suppressed_and_reports_them(self):
        self.r.notify("alert", "t", "first", dedup_key="k")
        self.assertEqual(self.r.notify("alert", "t", "again", dedup_key="k"), [])
        self.r.notify("alert", "t", "again2", dedup_key="k")
        self.assertEqual(len(MemoryChannel.sent), 1)
        # expire the window and fire again: the suppressed count is carried into the message
        def age(d):
            d["alert:k:chat"]["last"] -= 7 * 3600
        self.r.paged.update(age)
        self.r.notify("alert", "t", "later", dedup_key="k")
        self.assertIn("+2 similar suppressed", MemoryChannel.sent[-1][3])

    def test_failing_channel_does_not_block_others(self):
        self.cfg.channels["chat"]["fail"] = True
        out = self.r.notify("urgent", "t", "x")
        by = {d.channel: d for d in out}
        self.assertFalse(by["chat"].ok)
        self.assertTrue(by["sms"].ok)

    def test_secrets_are_redacted(self):
        os.environ["TG_TOKEN_TEST"] = "supersecret-token-123"
        self.cfg.channels["chat"]["token_env"] = "TG_TOKEN_TEST"
        self.r.notify("info", "t", "leak: supersecret-token-123 here")
        self.assertNotIn("supersecret-token-123", MemoryChannel.sent[-1][3])
        self.assertIn("[redacted]", MemoryChannel.sent[-1][3])
        del os.environ["TG_TOKEN_TEST"]

    def test_rate_limit_applies_to_info_not_urgent(self):
        self.cfg.max_per_hour = 2
        for _ in range(4):
            self.r.notify("info", "t", "x")
        self.assertEqual(len([m for m in MemoryChannel.sent if m[1] == "info"]), 2)
        self.r.notify("urgent", "t", "x")
        self.assertEqual(len([m for m in MemoryChannel.sent if m[1] == "urgent"]), 2)  # chat + sms

    def test_page_that_reached_nobody_does_not_consume_the_dedup_window(self):
        """QC bug A: all channels down once => the alert used to be hidden for the whole window."""
        for c in self.cfg.channels.values():
            c["fail"] = True
        self.assertFalse(any(d.ok for d in self.r.notify("urgent", "t", "x", dedup_key="k")))
        for c in self.cfg.channels.values():
            c["fail"] = False
        self.r.notify("urgent", "t", "x", dedup_key="k")
        self.assertEqual({m[0] for m in MemoryChannel.sent}, {"chat", "sms"})   # delivered on retry

    def test_dedup_is_per_channel_so_a_down_sms_retries_while_chat_stays_quiet(self):
        self.cfg.channels["sms"]["fail"] = True
        self.r.notify("urgent", "t", "x", dedup_key="k")                         # chat ok, sms down
        self.cfg.channels["sms"]["fail"] = False
        self.r.notify("urgent", "t", "x", dedup_key="k")
        self.assertEqual([m[0] for m in MemoryChannel.sent], ["chat", "sms"])    # no second chat page

    def test_none_sends_nothing(self):
        self.assertEqual(self.r.notify("none", "t", "x"), [])
        self.assertEqual(MemoryChannel.sent, [])


if __name__ == "__main__":
    unittest.main()
