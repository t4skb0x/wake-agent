"""File-per-event spool with atomic claim.

pending/     events waiting for a job
processing/  <run_id>/ events claimed by a running job (claimed via rename, so races are safe)
archive/     <run_id>/ events that got a verdict
dead/        events that failed max_attempts times (needs a human)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .util import atomic_write, now_iso


class Spool:
    def __init__(self, root: Path):
        self.root = root
        self.pending = root / "pending"
        self.processing = root / "processing"
        self.archive = root / "archive"
        self.dead = root / "dead"
        self.tmp = root / "tmp"
        for d in (self.pending, self.processing, self.archive, self.dead, self.tmp):
            d.mkdir(parents=True, exist_ok=True)

    def emit(self, job: str, sig: str, line: str = "", data: dict[str, Any] | None = None,
             source: str = "manual") -> str:
        eid = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
        event = {"id": eid, "ts": now_iso(), "job": job, "sig": sig, "line": line[:2000],
                 "source": source, "data": data or {}, "attempts": 0}
        tmp = self.tmp / f"{eid}.json"
        tmp.write_text(json.dumps(event))
        os.replace(tmp, self.pending / f"{eid}.json")  # atomic publish
        return eid

    def _read(self, p: Path) -> dict[str, Any] | None:
        """Parsed event, or None if unreadable/corrupt. Raises FileNotFoundError if it vanished."""
        try:
            return json.loads(p.read_text())
        except FileNotFoundError:
            raise
        except (OSError, ValueError):
            return None

    def claim(self, job: str, run_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Atomically move this job's pending events into processing/<run_id>/."""
        dest = self.processing / run_id
        dest.mkdir(parents=True, exist_ok=True)
        claimed: list[dict[str, Any]] = []
        for p in sorted(self.pending.glob("*.json")):
            if limit and len(claimed) >= limit:
                break
            try:
                ev = self._read(p)
                if ev is None:
                    os.replace(p, self.dead / p.name)  # corrupt -> quarantine, never loop on it
                    continue
                if ev.get("job") != job:
                    continue
                os.rename(p, dest / p.name)  # atomic; losing a race raises FileNotFoundError
            except FileNotFoundError:
                continue  # another claimer got it first
            claimed.append(ev)
        return claimed

    def finish(self, run_id: str) -> None:
        src = self.processing / run_id
        if src.exists():
            os.replace(src, self.archive / run_id)

    def release(self, run_id: str, max_attempts: int) -> tuple[int, int]:
        """Return claimed events to pending (attempts+1) or dead-letter them. -> (requeued, dead)."""
        src = self.processing / run_id
        requeued = dead = 0
        if not src.exists():
            return 0, 0
        for p in sorted(src.glob("*.json")):
            try:
                ev = self._read(p) or {}
            except FileNotFoundError:
                continue
            ev["attempts"] = int(ev.get("attempts", 0)) + 1
            ev["last_failure"] = now_iso()
            if ev["attempts"] >= max_attempts:
                atomic_write(self.dead / p.name, json.dumps(ev))
                dead += 1
            else:
                atomic_write(self.pending / p.name, json.dumps(ev))
                requeued += 1
            p.unlink(missing_ok=True)
        try:
            src.rmdir()
        except OSError:
            pass
        return requeued, dead

    def pending_events(self, job: str | None = None) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.pending.glob("*.json")):
            try:
                ev = self._read(p)
                if ev and (job is None or ev.get("job") == job):
                    ev["_mtime"] = p.stat().st_mtime
                    out.append(ev)
            except FileNotFoundError:
                continue
        return out

    def dead_count(self) -> int:
        return len(list(self.dead.glob("*.json")))

    def orphaned_runs(self, older_than: float) -> list[Path]:
        """processing/<run_id> dirs left behind by a crashed run."""
        now = time.time()
        return [d for d in self.processing.iterdir() if d.is_dir() and now - d.stat().st_mtime > older_than]

    def recover_orphans(self, older_than: float, max_attempts: int) -> int:
        n = 0
        for d in self.orphaned_runs(older_than):
            r, dd = self.release(d.name, max_attempts)
            n += r + dd
        return n
