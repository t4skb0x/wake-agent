import os
import time
import unittest

from tests.helpers import MemoryChannel, make_env
from wakeagent import check
from wakeagent.runs import Runs
from wakeagent.spool import Spool


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.cfg = make_env()
        self.s = Spool(self.cfg.data / "spool")

    def keys(self):
        return {k for k, _, _ in check.problems(self.cfg)}

    def test_stale_pending_events(self):
        self.s.emit("triage", "a", "x")
        p = self.s.pending / next(self.s.pending.iterdir()).name
        old = time.time() - 3600
        os.utime(p, (old, old))
        self.assertIn("check:stale:triage", self.keys())

    def test_dead_letter_is_urgent(self):
        (self.s.dead / "x.json").write_text("{}")
        probs = {k: sev for k, sev, _ in check.problems(self.cfg)}
        self.assertEqual(probs["check:dead-letter"], "urgent")

    def test_overdue_job(self):
        self.assertIn("check:overdue:qa", self.keys())                      # never ran, expect_every_hours=1

    def test_last_run_failed(self):
        Runs(self.cfg.data).add({"run_id": "r1", "job": "triage", "status": "failed", "ended": "now"})
        self.assertIn("check:failed:triage", self.keys())

    def test_watcher_heartbeat_only_checked_when_watches_configured(self):
        self.assertNotIn("check:watcher-down", self.keys())
        self.cfg.watches = [object()]
        self.assertIn("check:watcher-down", self.keys())

    def test_run_pages_and_drill_does_not(self):
        (self.s.dead / "x.json").write_text("{}")
        self.assertEqual(check.run(self.cfg, drill=True), 1)
        self.assertEqual(MemoryChannel.sent, [])
        self.assertEqual(check.run(self.cfg), 1)
        self.assertTrue(MemoryChannel.sent)

    def test_healthy_system_is_quiet(self):
        Runs(self.cfg.data).add({"run_id": "r1", "job": "qa", "status": "ok", "ended": "now"})
        self.assertEqual(check.run(self.cfg), 0)


if __name__ == "__main__":
    unittest.main()
