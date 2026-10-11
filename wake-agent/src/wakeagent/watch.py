"""Event watcher: follow journald or log files, match signatures, mute, dedup, debounce, emit, wake.

Fixes vs. the system this was extracted from:
  * waking is non-blocking (the loop never waits for an agent turn)
  * dedup keys are content-based, and suppressed counts are kept (nothing vanishes silently)
  * optional recheck: wait N seconds, run a command, and auto-close if the problem already cleared
  * on restart, journal followers resume from the last seen time instead of going blind
"""
from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict
from typing import Any

from . import mutes as mutes_mod
from .config import Config, Watch, load
from .spool import Spool
from .util import JsonStore, append_jsonl, normalize_line, now_iso


class Watcher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spool = Spool(cfg.data / "spool")
        self.dedup = JsonStore(cfg.data / "state" / "watch_dedup.json")
        self.heartbeat = JsonStore(cfg.data / "state" / "heartbeat.json")
        self.log = cfg.data / "logs" / "watch.jsonl"
        self.stop = threading.Event()
        self._children: list[subprocess.Popen] = []
        self._followers: list[subprocess.Popen] = []
        self._cfg_mtime = cfg.path.stat().st_mtime
        self.triggered: list[str] = []  # test hook

    # ------------------------------------------------------------ core (testable)
    def signature(self, w: Watch, line: str) -> str:
        if w.key_regex:
            m = w.key_regex.search(line)
            if m:
                key = m.groupdict().get("key") or (m.group(1) if m.groups() else m.group(0))
                return f"{w.cls}:{key}"
        h = hashlib.sha1(normalize_line(line).encode()).hexdigest()[:10]
        return f"{w.cls}:{h}"

    def _dedup(self, sig: str, hours: float) -> tuple[bool, int]:
        now = time.time()
        window = hours * 3600

        def fn(d: dict[str, Any]) -> tuple[bool, int]:
            e = d.get(sig, {"last": 0, "suppressed": 0})
            if now - e["last"] >= window:
                carried = int(e.get("suppressed", 0))
                d[sig] = {"last": now, "suppressed": 0}
                return True, carried
            e["suppressed"] = int(e.get("suppressed", 0)) + 1
            d[sig] = e
            return False, e["suppressed"]

        return self.dedup.update(fn)

    def handle_line(self, w: Watch, line: str) -> str:
        """Process one log line against one rule. Returns what happened (for tests and logs)."""
        line = line.rstrip("\n")
        if not w.pattern.search(line):
            return "no-match"
        sig = self.signature(w, line)
        mute = mutes_mod.match(self.cfg.mutes, line, w.job)
        if mute:
            mutes_mod.record(self.cfg.data, mute, line, sig)
            return "muted"
        fire, n = self._dedup(sig, w.dedup_hours)
        if not fire:
            return "dedup"
        if w.recheck_cmd:
            t = threading.Timer(w.recheck_delay, self._recheck, args=(w, sig, line, n))
            t.daemon = True
            t.start()
            return "recheck"
        self._emit(w, sig, line, n)
        return "emitted"

    def _emit(self, w: Watch, sig: str, line: str, suppressed: int) -> None:
        self.spool.emit(w.job, sig, line, {"rule": w.name, "suppressed_since_last": suppressed}, source="watch")
        append_jsonl(self.log, {"ts": now_iso(), "action": "emitted", "sig": sig, "job": w.job})
        self.trigger(w.job)

    def _recheck(self, w: Watch, sig: str, line: str, suppressed: int) -> None:
        try:
            r = subprocess.run(["bash", "-c", w.recheck_cmd or "false"], capture_output=True, timeout=120,
                               stdin=subprocess.DEVNULL)
            cleared = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            cleared = False
        if cleared:
            self.dedup.update(lambda d: d.pop(sig, None))  # a recurrence must page again, not be deduped
            append_jsonl(self.log, {"ts": now_iso(), "action": "auto-closed", "sig": sig, "job": w.job})
        else:
            self._emit(w, sig, line, suppressed)

    def trigger(self, job: str) -> None:
        """Start a wake without waiting for it. Overlapping triggers coalesce via the job lock."""
        self.triggered.append(job)
        if self.cfg.trigger_mode == "systemd":
            subprocess.Popen(["systemctl", "--user", "start", "--no-block", f"wake@{job}.service"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            return
        logf = open(self.cfg.data / "logs" / "trigger.log", "a")
        p = subprocess.Popen([sys.executable, "-m", "wakeagent", "--config", str(self.cfg.path), "run", job,
                              "--trigger", "event"], stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                             start_new_session=True)
        logf.close()
        self._children.append(p)

    # --------------------------------------------------------------- plumbing
    def _reap(self) -> None:
        self._children = [c for c in self._children if c.poll() is None]

    def _beat_loop(self) -> None:
        while not self.stop.wait(30):
            self._beat()

    def _beat(self) -> None:
        self.heartbeat.update(lambda d: d.update({"ts": time.time(), "pid": os.getpid(), "iso": now_iso()}))
        self._reap()
        try:
            m = self.cfg.path.stat().st_mtime
            if m != self._cfg_mtime:
                new = load(self.cfg.path)
                self.cfg.watches, self.cfg.mutes = new.watches, new.mutes
                self._cfg_mtime = m
                append_jsonl(self.log, {"ts": now_iso(), "action": "config-reloaded"})
        except Exception as e:  # keep running on the old config
            append_jsonl(self.log, {"ts": now_iso(), "action": "config-reload-failed", "error": str(e)[:200]})

    def groups(self) -> dict[tuple, list[Watch]]:
        g: dict[tuple, list[Watch]] = defaultdict(list)
        for w in self.cfg.watches:
            g[self._key(w)].append(w)
        return g

    @staticmethod
    def _key(w: Watch) -> tuple:
        return ("journal", tuple(w.args or ["--user"])) if w.source == "journal" else ("file", str(w.path))

    def _follow(self, key: tuple) -> None:
        backoff = 2
        last_seen = time.time()
        while not self.stop.is_set():
            if key[0] == "journal":
                since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_seen - 5))
                argv = ["journalctl", *key[1], "-f", "--since", since, "-o", "short-iso"]
            else:
                argv = ["tail", "-n", "0", "-F", key[1]]
            try:
                proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                        errors="replace")
            except OSError as e:
                append_jsonl(self.log, {"ts": now_iso(), "action": "follow-failed", "error": str(e)})
                if self.stop.wait(min(backoff, 60)):
                    return
                backoff *= 2
                continue
            self._followers.append(proc)
            started = time.time()
            assert proc.stdout is not None
            for line in proc.stdout:
                last_seen = time.time()
                for w in [w for w in self.cfg.watches if self._key(w) == key]:  # live: honors hot reload
                    try:
                        self.handle_line(w, line)
                    except Exception as e:  # the watcher never dies on a bad line
                        append_jsonl(self.log, {"ts": now_iso(), "action": "line-error", "error": str(e)[:200]})
                if self.stop.is_set():
                    break
            proc.terminate()
            try:
                proc.wait(timeout=5)  # reap: no zombies, no leaked pipe
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            if proc in self._followers:
                self._followers.remove(proc)
            if self.stop.is_set():
                return
            backoff = 2 if time.time() - started > 60 else min(backoff * 2, 60)
            append_jsonl(self.log, {"ts": now_iso(), "action": "follower-exited", "key": str(key)})
            if self.stop.wait(backoff):
                return

    def run(self) -> None:
        if not self.cfg.watches:
            raise SystemExit("no [[watch]] rules in config; nothing to do")
        self._beat()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, lambda *_: self.stop.set())  # systemctl stop => clean exit
            except ValueError:
                pass  # not the main thread (tests)
        threads = [threading.Thread(target=self._beat_loop, daemon=True)]
        threads += [threading.Thread(target=self._follow, args=(k,), daemon=True) for k in self.groups()]
        for t in threads:
            t.start()
        try:
            while not self.stop.wait(1):
                pass
        except KeyboardInterrupt:
            self.stop.set()
        finally:
            for f in self._followers:
                if f.poll() is None:
                    f.terminate()
