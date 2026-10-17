"""Worker parity primitives (PWA worker.py parity for wake-agent).

Filesystem-mailbox equivalents of the PWA worker behaviors:
  - worker identity (WORKER_ID, recorded on claim like claim_task does)
  - heartbeat every HEARTBEAT_INTERVAL (local sidecar + optional HTTP POST,
    mirroring worker.py heartbeat_forever -> /api/tasks/heartbeat)
  - progress reporting API (mirrors /api/tasks/progress: appends a note
    and refreshes the heartbeat)
  - retry with exponential backoff
  - task-level timeouts (mirrors EXEC_TIMEOUT / timed_out exit 124)

Standard library only. No shell is ever used here.
"""
import json
import os
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

# Worker identity, reported on claim/heartbeat so logs show which
# machine runs each message. Override with WORKER_ID when several
# workers share a hostname.
WORKER_ID = os.environ.get("WORKER_ID", "").strip() or socket.gethostname()

# Seconds between heartbeats while a message runs (also refreshable via
# WAKE_HEARTBEAT_INTERVAL). recover_stale treats a message with a fresh
# heartbeat as alive and skips it in the sweep, like the PWA timeout
# sweep skips tasks with a fresh worker heartbeat.
HEARTBEAT_INTERVAL = int(os.environ.get("WAKE_HEARTBEAT_INTERVAL", "60"))

# A heartbeat newer than this means the worker is alive.
HEARTBEAT_STALE = 300

# Default max seconds per executor run (also settable per config /
# per message). Mirrors PWA EXEC_TIMEOUT.
TASK_TIMEOUT_DEFAULT = 600

# Exit code reported when a task-level timeout fires (mirrors PWA timed_out).
EXIT_TIMEOUT = 124


def resolve_worker(explicit=None) -> str:
    """Worker identity: explicit config value, else WORKER_ID."""
    return str(explicit or "").strip() or WORKER_ID


def heartbeat_file(claimed_path) -> Path:
    """Sidecar path holding this run's heartbeat record."""
    p = Path(claimed_path)
    return p.parent / (p.stem + ".heartbeat.json")


def progress_file(claimed_path) -> Path:
    """Sidecar path holding this run's progress notes (JSON lines)."""
    p = Path(claimed_path)
    return p.parent / (p.stem + ".progress.jsonl")


def worker_file(claimed_path) -> Path:
    """Sidecar path holding this run's claim record (identity + cmd)."""
    p = Path(claimed_path)
    return p.parent / (p.stem + ".worker.json")


def atomic_write_text(path, text: str) -> Path:
    """Write text atomically (temp file + os.replace, same directory)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / (p.name + f".tmp-{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, p)
    return p


def write_heartbeat(claimed_path, msg_id, worker=None) -> Path:
    """Record a heartbeat (also the progress API's refresh)."""
    record = {
        "msg_id": msg_id,
        "worker": resolve_worker(worker),
        "pid": os.getpid(),
        "ts": int(time.time()),
    }
    return atomic_write_text(heartbeat_file(claimed_path),
                             json.dumps(record) + "\n")


def heartbeat_ts(claimed_path, now=None):
    """Epoch seconds of the last heartbeat, or None if never."""
    try:
        data = json.loads(heartbeat_file(claimed_path).read_text())
        return int(data.get("ts"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def is_alive(claimed_path, stale_after=HEARTBEAT_STALE, now=None) -> bool:
    """True when the heartbeat is fresh (worker alive, skip in sweep).

    Falls back to the claimed file's mtime when no heartbeat was ever
    written, so old messages without sidecars keep the previous behavior.
    """
    now = int(now if now is not None else time.time())
    ts = heartbeat_ts(claimed_path)
    if ts is None:
        try:
            ts = int(Path(claimed_path).stat().st_mtime)
        except OSError:
            return False
    return now - ts < stale_after


def post_heartbeat(url, payload, timeout=10) -> bool:
    """POST a heartbeat record. Best effort: never raises.

    Bypasses proxy env vars: heartbeats go to a local coordinator and
    must not depend on (or leak through) an egress proxy.
    """
    try:
        req = urllib.request.Request(
            url,
            method="POST",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def heartbeat_forever(stop, claimed_path, msg_id, worker=None, url=None,
                      interval=HEARTBEAT_INTERVAL):
    """POST/write a heartbeat every interval until stop is set."""
    worker = resolve_worker(worker)
    while not stop.wait(interval):
        write_heartbeat(claimed_path, msg_id, worker)
        if url:
            post_heartbeat(url, {"msg_id": msg_id, "worker": worker,
                                 "pid": os.getpid()})


def heartbeat_thread(claimed_path, msg_id, worker=None, url=None,
                     interval=HEARTBEAT_INTERVAL):
    """Start the heartbeat loop; returns (stop_event, thread)."""
    stop = threading.Event()
    t = threading.Thread(
        target=heartbeat_forever,
        args=(stop, claimed_path, msg_id, worker, url, interval),
        daemon=True,
    )
    t.start()
    return stop, t


def report_progress(claimed_path, message, msg_id=""):
    """Append a progress note (also refreshes the heartbeat).

    Returns (record_or_None, error_or_None), mirroring the PWA
    add_progress contract: empty message and missing message are
    rejected, notes on finished work are append-only here.
    """
    msg = str(message or "").strip()
    if not msg:
        return None, "empty message"
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "message": msg[:500]}
    p = progress_file(claimed_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fh.write(json.dumps(record) + "\n")
    write_heartbeat(claimed_path, msg_id or Path(claimed_path).stem)
    return record, None


def compute_backoff(attempt, base=5.0, cap=300.0) -> float:
    """Exponential backoff delay for retry attempt (0-based)."""
    try:
        base = float(base)
    except (TypeError, ValueError):
        base = 5.0
    try:
        cap = float(cap)
    except (TypeError, ValueError):
        cap = 300.0
    return min(cap, base * (2 ** max(0, int(attempt))))


def run_with_retries(argv, timeout=TASK_TIMEOUT_DEFAULT, max_retries=0,
                     backoff_base=5.0, backoff_cap=300.0,
                     sleep=time.sleep, cwd=None) -> dict:
    """Run argv (no shell) with a task-level timeout and retry/backoff.

    Retries on non-zero exit AND on timeout. Returns
    {returncode, output, attempts, timed_out}: returncode is EXIT_TIMEOUT
    (124) when the final attempt timed out, output keeps the last
    attempt's combined output (truncated), attempts counts every try.
    """
    try:
        max_retries = max(0, int(max_retries))
    except (TypeError, ValueError):
        max_retries = 0
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = float(TASK_TIMEOUT_DEFAULT)
    if timeout <= 0:
        timeout = float(TASK_TIMEOUT_DEFAULT)

    attempts = 0
    output = ""
    timed_out = False
    returncode = 1
    for n in range(max_retries + 1):
        attempts += 1
        timed_out = False
        try:
            r = subprocess.run(argv, shell=False, timeout=timeout,
                               capture_output=True, text=True, cwd=cwd)
            output = (r.stdout or "") + (r.stderr or "")
            returncode = r.returncode
            if returncode == 0:
                return {"returncode": 0, "output": output[-8000:],
                        "attempts": attempts, "timed_out": False}
        except subprocess.TimeoutExpired as e:
            parts = []
            for chunk in (getattr(e, "stdout", None),
                          getattr(e, "stderr", None)):
                if isinstance(chunk, bytes):
                    chunk = chunk.decode(errors="replace")
                parts.append(chunk or "")
            output = "".join(parts)
            returncode = EXIT_TIMEOUT
            timed_out = True
        if n < max_retries:
            sleep(compute_backoff(n, backoff_base, backoff_cap))
    return {"returncode": returncode, "output": output[-8000:],
            "attempts": attempts, "timed_out": timed_out}


def resolve_timeout(envelope, config, default=TASK_TIMEOUT_DEFAULT):
    """Task-level timeout: per-message override, else config, else default."""
    for source in (envelope or {}, config or {}):
        raw = source.get("timeout", source.get("task_timeout"))
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return default


def resolve_retries(config):
    """Retry knobs from config: (max_retries, backoff_base, backoff_cap)."""
    config = config or {}
    try:
        max_retries = max(0, int(config.get("max_retries", 0)))
    except (TypeError, ValueError):
        max_retries = 0
    try:
        base = float(config.get("retry_backoff_base", 5.0))
    except (TypeError, ValueError):
        base = 5.0
    try:
        cap = float(config.get("retry_backoff_cap", 300.0))
    except (TypeError, ValueError):
        cap = 300.0
    return max_retries, base, cap
