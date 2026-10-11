"""Small stdlib helpers: atomic writes, file locks, JSON stores, time."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterator


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically (tmp file in the same dir + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


@contextlib.contextmanager
def file_lock(path: Path, blocking: bool = True) -> Iterator[bool]:
    """Advisory flock on `path`. Yields True when held, False if non-blocking and busy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


class JsonStore:
    """A JSON dict on disk with locked, atomic read-modify-write."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def update(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        with file_lock(self.path.with_name(self.path.name + ".lock")):
            data = self.read()
            result = fn(data)
            atomic_write(self.path, json.dumps(data, indent=1, sort_keys=True))
            return result


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    # O_APPEND writes of a single short line are atomic enough for log use.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def parse_when(value: str) -> float:
    """Parse an ISO date/datetime into epoch seconds (naive = local time)."""
    value = value.strip()
    if value.lower() == "never":
        return float("inf")
    d = dt.datetime.fromisoformat(value)
    return d.timestamp()


def human_age(secs: float) -> str:
    if secs < 90:
        return f"{secs:.0f}s"
    if secs < 5400:
        return f"{secs / 60:.0f}m"
    return f"{secs / 3600:.1f}h"


_DIGITS = re.compile(r"\d+")
_HEX = re.compile(r"\b[0-9a-f]{8,}\b", re.I)


def normalize_line(line: str) -> str:
    """Normalize volatile bits (timestamps, pids, ids) so similar lines share a dedup key."""
    line = _HEX.sub("#", line)
    return _DIGITS.sub("#", line).strip()
