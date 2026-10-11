"""`wake doctor`: prove the wake chain is ready before you need it."""
from __future__ import annotations

import os
import time

from . import channels as chan
from . import runners
from .config import Config
from .mutes import expired
from .reporter import Reporter


def run(cfg: Config, smoke: bool = False, send_test: bool = False) -> int:
    bad = 0

    def ok(msg: str) -> None:
        print(f"  ok    {msg}")

    def warn(msg: str) -> None:
        print(f"  warn  {msg}")

    def fail(msg: str) -> None:
        nonlocal bad
        bad += 1
        print(f"  FAIL  {msg}")

    print(f"config: {cfg.path}")
    try:
        cfg.data.mkdir(parents=True, exist_ok=True)
        probe = cfg.data / ".write-test"
        probe.write_text("x")
        probe.unlink()
        ok(f"data dir writable: {cfg.data}")
    except OSError as e:
        fail(f"data dir not writable: {e}")

    if " " in str(cfg.path):
        warn("config path contains spaces; systemd EnvironmentFile= may not load secrets.env from it")
    print("jobs:")
    for j in cfg.jobs.values():
        for label, p in [("prompt", j.prompt), ("capture", j.capture), *[("include", i) for i in j.include]]:
            if p is None:
                continue
            (ok if p.exists() else fail)(f"{j.name}: {label} {p}" + ("" if p.exists() else " is missing"))
        if not j.workdir.exists():
            fail(f"{j.name}: workdir {j.workdir} does not exist")
        if j.mode == "fix":
            warn(f"{j.name}: mode=fix gives the agent write access; run it in a sandbox you trust")

    print("runners:")
    for rname in sorted({j.runner for j in cfg.jobs.values()} or set(cfg.runners)):
        try:
            r = runners.build(rname, cfg.runners[rname])
        except ValueError as e:
            fail(str(e))
            continue
        p = r.probe(smoke=smoke)
        if p.ok:
            ok(f"{rname}: {p.binary} {p.version}")
        for pr in p.problems:
            fail(f"{rname}: {pr}")
        for n in p.notes:
            warn(f"{rname}: {n}")

    print("channels:")
    for cname, ccfg in cfg.channels.items():
        try:
            problems = chan.build(cname, ccfg).check()
        except Exception as e:
            fail(f"{cname}: {e}")
            continue
        (ok if not problems else fail)(f"{cname} ({ccfg.get('type')})" + (": " + "; ".join(problems) if problems else ""))
    for sev in ("info", "alert", "urgent"):
        if not cfg.routing.get(sev):
            warn(f"no channels routed for severity {sev!r}")

    if cfg.mutes:
        print("mutes:")
        for m in expired(cfg.mutes):
            warn(f"mute {m.pattern.pattern!r} (owner {m.owner}) EXPIRED {m.raw_until}: remove it or renew it")
        for m in cfg.mutes:
            if m.until == float("inf"):
                warn(f"mute {m.pattern.pattern!r} never expires; prefer a date")
            elif m.until > time.time():
                ok(f"mute {m.pattern.pattern!r} until {m.raw_until} ({m.owner}): {m.reason}")

    if send_test:
        print("test notification:")
        rep = Reporter(cfg)
        for sev in ("info",):
            for d in rep.notify(sev, "wake doctor", "test notification from wake-agent"):
                (ok if d.ok else fail)(f"{d.channel}: {d.error or 'delivered'}")
    if not os.environ.get("XDG_RUNTIME_DIR"):
        warn("XDG_RUNTIME_DIR unset: systemd --user may not be reachable from this shell")
    print("RESULT:", "ready" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0
