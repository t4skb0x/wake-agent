import json
import threading
import time
import unittest

from tests.helpers import make_env
from wakeagent.spool import Spool
from wakeagent.watch import Watcher

WATCH = '''
[[watch]]
name = "unit-fail"
job = "triage"
source = "file"
path = "app.log"
pattern = "Failed with result"
class = "unit-fail"
key_regex = "(?P<key>[\\\\w.-]+\\\\.service)"

[[watch]]
name = "errors"
job = "triage"
source = "file"
path = "app.log"
pattern = "ERROR"
class = "err"
'''


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.cfg = make_env(extra_toml=WATCH)
        self.w = Watcher(self.cfg)
        self.w.trigger = lambda job: self.w.triggered.append(job)          # never spawn real wakes
        self.s = Spool(self.cfg.data / "spool")
        self.unit, self.err = self.cfg.watches

    def test_no_match(self):
        self.assertEqual(self.w.handle_line(self.unit, "all fine"), "no-match")

    def test_emit_then_dedup_by_unit_key(self):
        self.assertEqual(self.w.handle_line(self.unit, "x foo.service: Failed with result 'exit-code'"), "emitted")
        self.assertEqual(self.w.handle_line(self.unit, "y foo.service: Failed with result 'timeout'"), "dedup")
        self.assertEqual(self.w.handle_line(self.unit, "z bar.service: Failed with result 'exit-code'"), "emitted")
        self.assertEqual(len(self.s.pending_events("triage")), 2)
        self.assertEqual(self.w.triggered, ["triage", "triage"])

    def test_content_dedup_ignores_volatile_numbers_but_not_different_errors(self):
        self.assertEqual(self.w.handle_line(self.err, "2026-10-11T10:00:01 ERROR db timeout after 30s pid=123"), "emitted")
        self.assertEqual(self.w.handle_line(self.err, "2026-10-11T10:05:09 ERROR db timeout after 31s pid=456"), "dedup")
        # a genuinely different error is NOT swallowed by the first one's dedup window
        self.assertEqual(self.w.handle_line(self.err, "2026-10-11T10:06:00 ERROR payment webhook rejected"), "emitted")

    def test_suppressed_count_is_carried_to_next_event(self):
        self.w.handle_line(self.err, "ERROR same thing 1")
        self.w.handle_line(self.err, "ERROR same thing 2")
        self.w.handle_line(self.err, "ERROR same thing 3")
        sig = self.w.signature(self.err, "ERROR same thing 1")
        self.w.dedup.update(lambda d: d[sig].update(last=d[sig]["last"] - 7 * 3600))
        self.assertEqual(self.w.handle_line(self.err, "ERROR same thing 4"), "emitted")
        self.assertEqual(self.s.pending_events()[-1]["data"]["suppressed_since_last"], 2)

    def test_mute_records_and_does_not_emit(self):
        self.cfg.mutes = load_mutes(self.tmp, "2999-01-01")
        self.assertEqual(self.w.handle_line(self.err, "ERROR flaky-module blew up"), "muted")
        self.assertEqual(self.s.pending_events(), [])
        rec = json.loads((self.cfg.data / "muted.jsonl").read_text().splitlines()[0])
        self.assertEqual((rec["owner"], rec["reason"]), ("alice", "under repair"))

    def test_expired_mute_stops_muting(self):
        self.cfg.mutes = load_mutes(self.tmp, "2000-01-01")
        self.assertEqual(self.w.handle_line(self.err, "ERROR flaky-module blew up"), "emitted")

    def test_recheck_auto_closes_when_problem_cleared(self):
        self.err.recheck_cmd, self.err.recheck_delay = "true", 0
        self.assertEqual(self.w.handle_line(self.err, "ERROR transient"), "recheck")
        time.sleep(0.5)
        self.assertEqual(self.s.pending_events(), [])
        acts = [json.loads(l)["action"] for l in (self.cfg.data / "logs" / "watch.jsonl").read_text().splitlines()]
        self.assertIn("auto-closed", acts)

    def test_recurrence_after_auto_close_pages_again(self):
        """QC bug B: an auto-closed problem used to stay deduped, hiding the recurrence for hours."""
        self.err.recheck_cmd, self.err.recheck_delay = "true", 0
        self.w.handle_line(self.err, "ERROR flapping")
        time.sleep(0.5)
        self.err.recheck_cmd = None
        self.assertEqual(self.w.handle_line(self.err, "ERROR flapping"), "emitted")

    def test_recheck_emits_when_still_broken(self):
        self.err.recheck_cmd, self.err.recheck_delay = "false", 0
        self.w.handle_line(self.err, "ERROR persistent")
        time.sleep(0.5)
        self.assertEqual(len(self.s.pending_events()), 1)

    def test_follow_file_end_to_end(self):
        log = self.tmp / "app.log"
        log.write_text("")
        t = threading.Thread(target=self.w.run, daemon=True)
        t.start()
        time.sleep(1.0)
        with open(log, "a") as f:
            f.write("2026 noise\n2026 ERROR disk exploded\n")
        deadline = time.time() + 8
        while time.time() < deadline and not self.s.pending_events():
            time.sleep(0.2)
        self.w.stop.set()
        self.assertEqual(len(self.s.pending_events()), 1)
        self.assertIn("disk exploded", self.s.pending_events()[0]["line"])
        self.assertTrue((self.cfg.data / "state" / "heartbeat.json").exists())


class ShutdownTests(unittest.TestCase):
    def test_sigterm_exits_cleanly_and_stops_followers(self):
        import signal
        import subprocess
        import sys
        tmp, cfg = make_env(extra_toml='''
[[watch]]
name = "e"
job = "triage"
source = "file"
path = "app.log"
pattern = "ERROR"
''')
        (tmp / "app.log").write_text("")
        p = subprocess.Popen([sys.executable, "-m", "wakeagent", "--config", str(cfg.path), "watch"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        hb = cfg.data / "state" / "heartbeat.json"
        deadline = time.time() + 8
        while time.time() < deadline and not hb.exists():
            time.sleep(0.1)
        time.sleep(0.8)
        p.send_signal(signal.SIGTERM)
        try:
            rc = p.wait(timeout=6)
        finally:
            if p.poll() is None:
                p.kill()
            p.stdout.close()
            p.stderr.close()
        self.assertEqual(rc, 0)


class JournalFollowTests(unittest.TestCase):
    def test_journal_follower_uses_user_journal_resumes_and_emits(self):
        import os
        import stat
        import sys
        tmp, cfg = make_env(extra_toml='''
[[watch]]
name = "uf"
job = "triage"
source = "journal"
pattern = "Failed with result"
class = "unit-fail"
key_regex = "(?P<key>[\\\\w.-]+\\\\.service)"
''')
        fakebin = tmp / "bin"
        fakebin.mkdir()
        j = fakebin / "journalctl"
        j.write_text(f"""#!{sys.executable}
import json, sys, time, os
open(os.environ["JCTL_ARGV"], "a").write(json.dumps(sys.argv[1:]) + "\\n")
print("2026-10-11T10:00:00+0000 host systemd[1]: noise", flush=True)
print("2026-10-11T10:00:01+0000 host systemd[1]: foo.service: Failed with result 'exit-code'.", flush=True)
time.sleep(30)
""")
        j.chmod(j.stat().st_mode | stat.S_IXUSR)
        argv_log = tmp / "jargv.txt"
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{fakebin}:{old_path}"
        os.environ["JCTL_ARGV"] = str(argv_log)
        w = Watcher(cfg)
        w.trigger = lambda job: None
        s = Spool(cfg.data / "spool")
        t = threading.Thread(target=w.run, daemon=True)
        try:
            t.start()
            deadline = time.time() + 8
            while time.time() < deadline and not s.pending_events():
                time.sleep(0.2)
        finally:
            w.stop.set()
            os.environ["PATH"] = old_path
            del os.environ["JCTL_ARGV"]
        evs = s.pending_events()
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["sig"], "unit-fail:foo.service")
        argv = json.loads(argv_log.read_text().splitlines()[0])
        self.assertIn("--user", argv)
        self.assertIn("-f", argv)
        self.assertIn("--since", argv)                                           # restart-safe: never starts blind


def load_mutes(tmp, until):
    from wakeagent.config import Mute
    from wakeagent.util import parse_when
    import re
    return [Mute(re.compile("flaky-module"), parse_when(until), "under repair", "alice", None, until)]


if __name__ == "__main__":
    unittest.main()
