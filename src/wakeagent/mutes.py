"""Mutes always have an owner, a reason and an expiry, and muted lines are still recorded."""
from __future__ import annotations

import time
from pathlib import Path

from .config import Mute
from .util import append_jsonl, now_iso


def active(mutes: list[Mute], now: float | None = None) -> list[Mute]:
    now = now or time.time()
    return [m for m in mutes if m.until > now]


def expired(mutes: list[Mute], now: float | None = None) -> list[Mute]:
    now = now or time.time()
    return [m for m in mutes if m.until <= now]


def match(mutes: list[Mute], line: str, job: str | None = None, now: float | None = None) -> Mute | None:
    for m in active(mutes, now):
        if m.job and job and m.job != job:
            continue
        if m.pattern.search(line):
            return m
    return None


def record(data_dir: Path, mute: Mute, line: str, sig: str) -> None:
    append_jsonl(data_dir / "muted.jsonl", {
        "ts": now_iso(), "sig": sig, "line": line[:400], "pattern": mute.pattern.pattern,
        "owner": mute.owner, "reason": mute.reason, "until": mute.raw_until,
    })
