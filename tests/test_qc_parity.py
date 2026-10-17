"""QC + parity tests for wake-agent (unittest, stdlib only).

Run:  python3 -m unittest discover -s tests -v   (from wake-agent/)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from wakeagent import __version__  # noqa: E402
from wakeagent import mailbox  # noqa: E402
from wakeagent import worker  # noqa: E402


ALLOW = {"qa-user": {"can_write_to": ["bugs"]}}


def make_mailbox(tmp):
    base = Path(tmp) / "mailboxes" / "bugs"
    for d in ("pending", "processing", "done", "failed"):
        (base / d).mkdir(parents=True)
    return base


def put_message(base, name="msg_1.json", frm="qa-user", to="bugs",
                body="hello", **extra):
    env = {"id": name.replace(".json", ""), "thread_id": "t1",
           "from": frm, "to": to, "body": body}
    env.update(extra)
    p = base / "pending" / name
    p.write_text(json.dumps(env))
    return p


class TestQCFixes(unittest.TestCase):
    def test_version_is_valid_pep440(self):
        text = (ROOT / "pyproject.toml").read_text()
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(m)
        self.assertRegex(m.group(1), r"^\d+\.\d+\.\d+$")
        self.assertNotIn("-", m.group(1))
        self.assertEqual(__version__, m.group(1))

    def test_requires_python_allows_tomllib(self):
        text = (ROOT / "pyproject.toml").read_text()
        m = re.search(r'requires-python\s*=\s*">=?([\d.]+)"', text)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(tuple(map(int, m.group(1).split("."))),
                                (3, 11))

    def test_entry_point_is_cli_main(self):
        text = (ROOT / "pyproject.toml").read_text()
        m = re.search(r'^wake-agent\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "wakeagent.cli:main")
        from wakeagent import cli
        self.assertTrue(callable(cli.main))

    def test_acl_fail_closed_empty(self):
        self.assertFalse(mailbox.can_send({}, "qa-user", "bugs"))
        self.assertFalse(mailbox.can_send(None, "qa-user", "bugs"))

    def test_acl_fail_closed_unknown_and_wrong_target(self):
        self.assertFalse(mailbox.can_send(ALLOW, "stranger", "bugs"))
        self.assertFalse(mailbox.can_send(ALLOW, "qa-user", "other"))
        self.assertTrue(mailbox.can_send(ALLOW, "qa-user", "bugs"))

    def test_acl_missing_file_denies(self):
        acl = mailbox.load_acl("/nonexistent/acl.json")
        self.assertFalse(mailbox.can_send(acl, "qa-user", "bugs"))

    def test_process_one_denies_without_acl(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = make_mailbox(tmp)
            p = put_message(base)
            ok = mailbox.process_one(p, {}, {}, "echo '{prompt}'")
            self.assertFalse(ok)
            self.assertTrue((base / "failed" / p.name).exists())


class TestShellSafety(unittest.TestCase):
    def test_prompt_is_single_argv_element(self):
        dumper = Path(tempfile.mkdtemp()) / "dump.py"
        out = dumper.with_suffix(".out")
        dumper.write_text(
            "import json,sys; open(sys.argv[1],'w').write(json.dumps(sys.argv[2:]))")
        tmpl = f"{sys.executable} {dumper} '{{prompt}}' {out}"
        argv = mailbox.build_argv(tmpl, {"prompt": "a  b\tc'd\"e"})
        self.assertEqual(len(argv), 4)
        self.assertEqual(argv[2], "a  b\tc'd\"e")

    def test_injection_body_never_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "PWNED"
            evil = (f"hi'); $(touch {marker}) `touch {marker}.2`; "
                    f"touch {marker}.3 | tee {marker}.4 && echo '")
            base = make_mailbox(tmp)
            p = put_message(base, body=evil)
            tmpl = (f"{sys.executable} -c "
                    f"'import sys; print(sys.argv[1])' '{{prompt}}'")
            ok = mailbox.process_one(p, {}, ALLOW, tmpl)
            self.assertTrue(ok)
            self.assertTrue((base / "done" / p.name).exists())
            self.assertFalse(marker.exists())
            self.assertFalse(Path(str(marker) + ".2").exists())
            self.assertFalse(Path(str(marker) + ".3").exists())
            self.assertFalse(Path(str(marker) + ".4").exists())

    def test_no_shell_used(self):
        seen = {}

        real_run = subprocess.run

        def spy(*a, **k):
            seen.update(k)
            return real_run(*a, **k)

        subprocess.run = spy
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = make_mailbox(tmp)
                p = put_message(base)
                mailbox.process_one(p, {}, ALLOW, "echo '{prompt}'")
        finally:
            subprocess.run = real_run
        self.assertIn("shell", seen)
        self.assertFalse(seen["shell"])


class TestParityWorker(unittest.TestCase):
    def test_worker_identity(self):
        self.assertTrue(worker.WORKER_ID)
        self.assertEqual(worker.resolve_worker("w-1"), "w-1")
        self.assertEqual(worker.resolve_worker(None), worker.WORKER_ID)
        self.assertEqual(worker.resolve_worker("  "), worker.WORKER_ID)

    def test_backoff_sequence_and_cap(self):
        self.assertEqual(worker.compute_backoff(0, 5, 300), 5)
        self.assertEqual(worker.compute_backoff(1, 5, 300), 10)
        self.assertEqual(worker.compute_backoff(2, 5, 300), 20)
        self.assertEqual(worker.compute_backoff(99, 5, 30), 30)

    def test_retry_succeeds_after_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            counter = Path(tmp) / "n"
            counter.write_text("0")
            script = Path(tmp) / "flaky.py"
            script.write_text(
                "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                "n=int(p.read_text()); p.write_text(str(n+1)); "
                "sys.exit(0 if n >= 2 else 1)")
            res = worker.run_with_retries(
                [sys.executable, str(script), str(counter)],
                timeout=30, max_retries=3,
                backoff_base=0.01, backoff_cap=0.05)
            self.assertEqual(res["returncode"], 0)
            self.assertEqual(res["attempts"], 3)
            self.assertFalse(res["timed_out"])

    def test_retry_exhausted_reports_attempts(self):
        res = worker.run_with_retries(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            timeout=30, max_retries=2,
            backoff_base=0.01, backoff_cap=0.02)
        self.assertEqual(res["returncode"], 3)
        self.assertEqual(res["attempts"], 3)
        self.assertFalse(res["timed_out"])

    def test_task_timeout_reports_124(self):
        res = worker.run_with_retries(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.3, max_retries=1,
            backoff_base=0.01, backoff_cap=0.02)
        self.assertEqual(res["returncode"], worker.EXIT_TIMEOUT)
        self.assertEqual(res["attempts"], 2)
        self.assertTrue(res["timed_out"])

    def test_resolve_timeout_prefers_message_then_config(self):
        self.assertEqual(worker.resolve_timeout({"timeout": 5}, {}, 600), 5)
        self.assertEqual(
            worker.resolve_timeout({}, {"task_timeout": 42}, 600), 42)
        self.assertEqual(worker.resolve_timeout({}, {}, 600), 600)
        self.assertEqual(
            worker.resolve_timeout({"timeout": -1}, {}, 600), 600)

    def test_progress_appends_and_refreshes_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            claimed = Path(tmp) / "msg_1.json"
            claimed.write_text("{}")
            r1, e1 = worker.report_progress(claimed, "halfway", "msg_1")
            r2, e2 = worker.report_progress(claimed, "done soon", "msg_1")
            self.assertIsNone(e1)
            self.assertIsNone(e2)
            lines = worker.progress_file(claimed).read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["message"], "halfway")
            self.assertIsNotNone(worker.heartbeat_ts(claimed))
            _, _ = r1, r2

    def test_progress_rejects_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            claimed = Path(tmp) / "msg_1.json"
            claimed.write_text("{}")
            rec, err = worker.report_progress(claimed, "   ", "msg_1")
            self.assertIsNone(rec)
            self.assertEqual(err, "empty message")

    def test_heartbeat_post_every_interval(self):
        posts = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                ln = int(self.headers.get("Content-Length", "0"))
                posts.append(json.loads(self.rfile.read(ln)))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}/hb"
            with tempfile.TemporaryDirectory() as tmp:
                claimed = Path(tmp) / "msg_9.json"
                claimed.write_text("{}")
                stop, hb = worker.heartbeat_thread(
                    claimed, "msg_9", worker="w-test",
                    url=url, interval=0.05)
                time.sleep(0.3)
                stop.set()
                hb.join(timeout=5)
                self.assertGreaterEqual(len(posts), 2)
                self.assertTrue(all(p["msg_id"] == "msg_9" for p in posts))
                self.assertTrue(all(p["worker"] == "w-test" for p in posts))
                self.assertIsNotNone(worker.heartbeat_ts(claimed))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_is_alive_fresh_and_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            claimed = Path(tmp) / "msg_1.json"
            claimed.write_text("{}")
            worker.write_heartbeat(claimed, "msg_1", "w")
            self.assertTrue(worker.is_alive(claimed))
            old = Path(tmp) / "old.json"
            old.write_text("{}")
            ancient = time.time() - 9999
            os.utime(old, (ancient, ancient))
            self.assertFalse(worker.is_alive(old))

    def test_recover_stale_skips_live_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "mb" / "agent1"
            proc = base / "processing"
            pend = base / "pending"
            proc.mkdir(parents=True)
            pend.mkdir(parents=True)
            live = proc / "live.json"
            live.write_text("{}")
            worker.write_heartbeat(live, "live", "w")
            dead = proc / "dead.json"
            dead.write_text("{}")
            ancient = time.time() - 9999
            os.utime(dead, (ancient, ancient))
            mailbox.recover_stale(str(Path(tmp) / "mb"), stale_after=300)
            self.assertTrue(live.exists())
            self.assertTrue((pend / "dead.json").exists())

    def test_process_one_records_worker_and_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = make_mailbox(tmp)
            p = put_message(base)
            ok = mailbox.process_one(
                p, {"worker_id": "w-7"}, ALLOW, "echo '{prompt}'")
            self.assertTrue(ok)
            self.assertTrue((base / "done" / p.name).exists())
            wrec = json.loads(
                (base / "processing" / "msg_1.worker.json").read_text())
            self.assertEqual(wrec["worker"], "w-7")
            self.assertEqual(wrec["msg_id"], "msg_1")
            prog = (base / "processing" / "msg_1.progress.jsonl")
            self.assertTrue(prog.exists())

    def test_process_one_task_timeout_moves_to_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = make_mailbox(tmp)
            p = put_message(base, name="slow.json")
            tmpl = (f"{sys.executable} -c "
                    f"'import time; time.sleep(30)' '{{prompt}}'")
            ok = mailbox.process_one(
                p, {"task_timeout": 0.3}, ALLOW, tmpl)
            self.assertFalse(ok)
            self.assertTrue((base / "failed" / "slow.json").exists())

    def test_process_one_dry_run_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = make_mailbox(tmp)
            p = put_message(base)
            ok = mailbox.process_one(p, {}, ALLOW, "echo '{prompt}'",
                                     dry_run=True)
            self.assertTrue(ok)
            self.assertTrue((base / "pending" / p.name).exists())


class TestSendBugScript(unittest.TestCase):
    SCRIPT = (ROOT / "examples" / "screenshot-bug-inbox" / "send-bug.sh")

    def test_evil_body_stays_valid_json(self):
        body = "it's \"broken\"\nline2 $(touch /tmp/x) `id` \\ end"
        with tempfile.TemporaryDirectory() as tmp:
            shot = Path(tmp) / "shot'quote.png"
            shot.write_bytes(b"\x89PNG-RAW-BYTES")
            env = dict(os.environ, WAKE_MAILBOX_BASE=str(Path(tmp) / "mb"))
            r = subprocess.run(
                ["bash", str(self.SCRIPT), body, str(shot)],
                capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            pend = Path(tmp) / "mb" / "bugs" / "pending"
            files = list(pend.glob("msg_*.json"))
            self.assertEqual(len(files), 1)
            env_data = json.loads(files[0].read_text())
            self.assertEqual(env_data["body"], body)
            self.assertEqual(env_data["from"], "qa-user")
            self.assertEqual(env_data["to"], "bugs")
            raw = Path(env_data["attachment_folder"]) / shot.name
            self.assertEqual(raw.read_bytes(), b"\x89PNG-RAW-BYTES")
            leftovers = list(pend.glob(".tmp-*.json"))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
