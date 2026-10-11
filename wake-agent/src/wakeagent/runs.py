"""Run records (runs.jsonl) and per-job last-run state."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .util import JsonStore, append_jsonl, read_jsonl


class Runs:
    def __init__(self, data: Path):
        self.file = data / "logs" / "runs.jsonl"
        self.state = JsonStore(data / "state" / "last_run.json")

    def add(self, rec: dict[str, Any]) -> None:
        append_jsonl(self.file, rec)
        job = rec["job"]

        def fn(d: dict[str, Any]) -> None:
            e = d.get(job, {})
            e["last"] = {"run_id": rec["run_id"], "status": rec["status"], "ended_epoch": time.time(),
                         "ts": rec["ended"]}
            if rec["status"] in ("ok", "fixed"):
                e["last_good_epoch"] = time.time()
            d[job] = e

        self.state.update(fn)

    def tail(self, n: int = 20, job: str | None = None) -> list[dict[str, Any]]:
        recs = [r for r in read_jsonl(self.file) if job is None or r.get("job") == job]
        return recs[-n:]

    def last(self, job: str) -> dict[str, Any]:
        return self.state.read().get(job, {})
