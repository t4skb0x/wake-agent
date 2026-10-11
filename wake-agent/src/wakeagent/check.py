"""`wake check`: the dead-man's switch. Pages when any link of the wake chain is provably broken."""
from __future__ import annotations

import time

from .config import Config
from .reporter import Reporter
from .runs import Runs
from .spool import Spool
from .util import JsonStore, human_age


def problems(cfg: Config) -> list[tuple[str, str, str]]:
    """Return (dedup_key, severity, message) for everything currently wrong."""
    out: list[tuple[str, str, str]] = []
    now = time.time()
    spool = Spool(cfg.data / "spool")
    runs = Runs(cfg.data)

    if cfg.watches:
        hb = JsonStore(cfg.data / "state" / "heartbeat.json").read().get("ts", 0)
        if now - hb > 120:
            age = "never" if not hb else human_age(now - hb)
            out.append(("check:watcher-down", "urgent",
                        f"watcher heartbeat is stale (last: {age}); detection is blind. "
                        "Restart: systemctl --user restart wake-watch"))

    for job in cfg.jobs.values():
        pend = spool.pending_events(job.name)
        if pend:
            oldest = max(now - e["_mtime"] for e in pend)
            if oldest > cfg.stale_minutes * 60:
                out.append((f"check:stale:{job.name}", "alert",
                            f"{len(pend)} event(s) pending for job {job.name}, oldest {human_age(oldest)}: "
                            "the wake is not draining the queue"))
        last = runs.last(job.name)
        if last.get("last", {}).get("status") == "failed":
            out.append((f"check:failed:{job.name}", "alert",
                        f"last run of {job.name} FAILED ({last['last']['run_id']})"))
        if job.expect_every_hours:
            good = last.get("last_good_epoch", 0)
            if now - good > job.expect_every_hours * 3600:
                age = "never" if not good else human_age(now - good)
                out.append((f"check:overdue:{job.name}", "alert",
                            f"job {job.name} has no successful run in {job.expect_every_hours:g}h (last good: {age})"))

    dead = spool.dead_count()
    if dead:
        out.append(("check:dead-letter", "urgent", f"{dead} event(s) in the dead-letter queue need a human"))
    orphans = spool.orphaned_runs(3600)
    if orphans:
        out.append(("check:orphans", "alert", f"{len(orphans)} crashed run(s) left claimed events in processing/"))
    return out


def run(cfg: Config, drill: bool = False) -> int:
    probs = problems(cfg)
    if not probs:
        print("wake check: ok")
        return 0
    rep = Reporter(cfg)
    for key, sev, msg in probs:
        if drill:
            print(f"[drill] {sev} {key}: {msg}")
            continue
        deliveries = rep.notify(sev, "wake check", msg, dedup_key=key)
        sent = ", ".join(f"{d.channel}:{'ok' if d.ok else 'FAIL'}" for d in deliveries) or "deduped"
        print(f"{sev} {key}: {msg} [{sent}]")
    return 1
