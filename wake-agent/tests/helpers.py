from __future__ import annotations

import os
import stat
import sys
import tempfile
import textwrap
from pathlib import Path

from wakeagent import channels as chan
from wakeagent.channels.base import Channel
from wakeagent.config import load

FAKE_MUSE = r'''#!{py}
import json, os, re, sys, time
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("Muse Code 0.1.0 (0.1.0-R708.1)"); sys.exit(0)
if os.environ.get("FAKE_MUSE_ARGV_FILE"):
    open(os.environ["FAKE_MUSE_ARGV_FILE"], "w").write(json.dumps(argv))
if "--provider" in argv and "echo" in argv:
    print(json.dumps({{"type": "run.started"}})); print(json.dumps({{"type": "run.finished"}})); sys.exit(0)
mode = os.environ.get("FAKE_MUSE_MODE", "ok")
if os.environ.get("FAKE_MUSE_ENV_DUMP"):
    open(os.environ["FAKE_MUSE_ENV_DUMP"], "w").write(json.dumps(sorted(os.environ)))
prompt = argv[-1]
print(json.dumps({{"type": "run.started"}}))
rp = os.environ.get("WAKE_RESULT_PATH")
ids = re.findall(r'"id": "([^"]+)"', prompt)
if mode == "spawnchild":
    import subprocess
    c = subprocess.Popen(["sleep", "300"])
    open(os.environ["CHILD_PID_FILE"], "w").write(str(c.pid))
    time.sleep(300)
if mode == "hang":
    time.sleep(120)
if mode == "fail":
    print("boom: upstream exploded", file=sys.stderr); sys.exit(3)
if os.environ.get("FAKE_MUSE_EMIT_PENDING"):
    d = os.environ["FAKE_MUSE_EMIT_PENDING"]
    open(os.path.join(d, "9999999999999999999-late.json"), "w").write(json.dumps(
        {{"id": "late-event", "ts": "now", "job": os.environ["WAKE_JOB"], "sig": "late", "line": "arrived mid-run",
          "source": "t", "data": {{}}, "attempts": 0}}))
res = {{"status": "ok", "summary": "all good", "verdicts": [{{"event_id": i, "verdict": "OK", "reason": "fine"}} for i in ids]}}
if mode == "problem":
    res.update(status="problem", summary="disk full", notify="alert", dedup_key="disk")
if mode == "urgent":
    res.update(status="operator", summary="needs human", notify="urgent", dedup_key="human")
if mode == "actions":
    res["actions"] = ["systemctl restart foo"]
if mode == "partial":
    res["verdicts"] = res["verdicts"][:-1]
if mode == "noresult":
    sys.exit(0)
if mode == "badjson":
    open(rp, "w").write("{{not json"); sys.exit(0)
open(rp, "w").write(json.dumps(res))
print(json.dumps({{"type": "run.finished"}}))
'''


class MemoryChannel(Channel):
    """Test channel that records messages (and can be told to fail)."""

    type = "memory"
    sent: list[tuple[str, str, str, str]] = []

    def send(self, title, text, severity):
        if self.cfg.get("fail"):
            raise RuntimeError("channel down")
        MemoryChannel.sent.append((self.name, severity, title, text))


chan.REGISTRY["memory"] = MemoryChannel


def make_fake_muse(dirpath: Path) -> Path:
    p = dirpath / "muse"
    p.write_text(FAKE_MUSE.format(py=sys.executable))
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def make_env(extra_toml: str = "", jobs_toml: str | None = None):
    """Create a temp wake home with a fake muse. Returns (tmpdir, Config)."""
    tmp = Path(tempfile.mkdtemp(prefix="wake-test-"))
    fake = make_fake_muse(tmp)
    (tmp / "prompts").mkdir()
    (tmp / "prompts" / "job.md").write_text("Do the job.")
    (tmp / "prompts" / "law.md").write_text("LAW BLOCK")
    (tmp / "cap.sh").write_text("echo capture-ok\n")
    if jobs_toml is None:
        jobs_toml = textwrap.dedent("""
            [jobs.triage]
            runner = "muse"
            mode = "fix"
            events = true
            prompt = "prompts/job.md"
            include = ["prompts/law.md"]
            timeout = 30
            max_attempts = 2
            max_drain = 3
            workdir = "."

            [jobs.qa]
            runner = "muse"
            mode = "read-only"
            prompt = "prompts/job.md"
            capture = "cap.sh"
            timeout = 30
            workdir = "."
            expect_every_hours = 1
            notify_ok = "info"
        """)
    (tmp / "wake.toml").write_text(textwrap.dedent(f"""
        [wake]
        data_dir = "data"
        stale_minutes = 15

        [runners.muse]
        type = "muse"
        bin = "{fake}"

        [channels.chat]
        type = "memory"
        [channels.sms]
        type = "memory"

        [routing]
        info = ["chat"]
        alert = ["chat"]
        urgent = ["chat", "sms"]

        [reporting]
        dedup_hours = 6
        max_per_hour = 30
    """) + jobs_toml + extra_toml)
    MemoryChannel.sent = []
    for k in ("FAKE_MUSE_MODE", "FAKE_MUSE_ARGV_FILE", "FAKE_MUSE_EMIT_PENDING"):
        os.environ.pop(k, None)
    return tmp, load(tmp / "wake.toml")
