import threading
import unittest

from tests.helpers import make_env
from wakeagent.spool import Spool


class SpoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.cfg = make_env()
        self.s = Spool(self.cfg.data / "spool")

    def test_emit_claim_finish(self):
        a = self.s.emit("triage", "a", "line a")
        self.s.emit("other", "b", "line b")
        got = self.s.claim("triage", "r1")
        self.assertEqual([e["id"] for e in got], [a])
        self.assertEqual(len(self.s.pending_events("other")), 1)  # other job's event untouched
        self.s.finish("r1")
        self.assertTrue((self.s.archive / "r1").exists())
        self.assertFalse((self.s.processing / "r1").exists())

    def test_concurrent_claims_never_duplicate(self):
        for i in range(60):
            self.s.emit("triage", f"s{i}", "x")
        results = []

        def worker(n):
            results.append([e["id"] for e in self.s.claim("triage", f"run{n}")])

        ts = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        flat = [i for r in results for i in r]
        self.assertEqual(len(flat), 60)
        self.assertEqual(len(set(flat)), 60)  # each event claimed exactly once

    def test_release_requeues_then_dead_letters(self):
        self.s.emit("triage", "a", "x")
        self.s.claim("triage", "r1")
        self.assertEqual(self.s.release("r1", 2), (1, 0))   # attempt 1 -> requeued
        self.s.claim("triage", "r2")
        self.assertEqual(self.s.release("r2", 2), (0, 1))   # attempt 2 -> dead
        self.assertEqual(self.s.dead_count(), 1)
        self.assertEqual(self.s.pending_events(), [])

    def test_unreadable_event_is_quarantined(self):
        (self.s.pending / "bad.json").write_text("{nope")
        self.assertEqual(self.s.claim("triage", "r1"), [])
        self.assertEqual(self.s.dead_count(), 1)

    def test_orphan_recovery(self):
        self.s.emit("triage", "a", "x")
        self.s.claim("triage", "crashed")
        self.assertEqual(self.s.recover_orphans(older_than=-1, max_attempts=3), 1)
        self.assertEqual(len(self.s.pending_events()), 1)


if __name__ == "__main__":
    unittest.main()
