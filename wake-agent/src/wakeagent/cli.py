"""wake: command line entry point."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, load
from .util import human_age


def _cfg(args: argparse.Namespace) -> Config:
    return load(args.config)


def cmd_init(args: argparse.Namespace) -> int:
    from .templates import FILES

    root = Path(args.dir).expanduser()
    for rel, text in FILES.items():
        p = root / rel
        if p.exists() and not args.force:
            print(f"skip   {p} (exists; use --force)")
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        if rel.endswith(".sh"):
            p.chmod(0o755)
        print(f"wrote  {p}")
    print(f"\nNext: cd {root} && wake doctor && wake run hello")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .engine import Engine

    out = Engine(_cfg(args)).run_job(args.job, trigger=args.trigger, dry_run=args.dry_run)
    print(f"{args.job}: {out.status}" + (f" ({out.error})" if out.error else "") +
          (f" - {out.summary}" if out.summary and out.status != "failed" else ""))
    return out.exit_code


def cmd_emit(args: argparse.Namespace) -> int:
    from .spool import Spool

    cfg = _cfg(args)
    if args.job not in cfg.jobs:
        print(f"unknown job {args.job!r}", file=sys.stderr)
        return 2
    line = args.line if args.line is not None else sys.stdin.read()
    data = json.loads(args.data) if args.data else {}
    eid = Spool(cfg.data / "spool").emit(args.job, args.sig, line.strip(), data, source="emit")
    print(eid)
    if args.wake:
        from .engine import Engine

        return Engine(cfg).run_job(args.job, trigger="event").exit_code
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from .watch import Watcher

    Watcher(_cfg(args)).run()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from . import check

    return check.run(_cfg(args), drill=args.drill)


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import doctor

    return doctor.run(_cfg(args), smoke=args.smoke, send_test=args.send_test)


def cmd_status(args: argparse.Namespace) -> int:
    from .runs import Runs
    from .spool import Spool

    cfg = _cfg(args)
    spool, runs = Spool(cfg.data / "spool"), Runs(cfg.data)
    now = time.time()
    for j in cfg.jobs.values():
        last = runs.last(j.name).get("last", {})
        age = human_age(now - last["ended_epoch"]) + " ago" if last else "never run"
        pend = len(spool.pending_events(j.name)) if j.events else "-"
        print(f"{j.name:20} last: {last.get('status', '-'):8} {age:12} pending: {pend}")
    print(f"dead-letter: {spool.dead_count()}")
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    from .runs import Runs

    for r in Runs(_cfg(args).data).tail(args.n, args.job):
        print(f"{r['started']}  {r['job']:16} {r['status']:9} {r['duration']:>6}s  "
              f"events={r['events']}  {r.get('runner_version', '')}  {r['summary'][:80]}")
    return 0


def cmd_systemd(args: argparse.Namespace) -> int:
    from . import systemd

    cfg = _cfg(args)
    out = Path(args.out).expanduser() if args.out else Path("~/.config/systemd/user").expanduser()
    if args.print:
        for name, text in systemd.units(cfg).items():
            print(f"# ---- {name}\n{text}")
        return 0
    names = systemd.write(cfg, out)
    print(f"wrote {len(names)} unit(s) to {out}:\n  " + "\n  ".join(names))
    print("\nTo enable:\n  " + "\n  ".join(systemd.enable_commands(cfg)))
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    import shutil

    cfg = _cfg(args)
    cutoff = time.time() - args.days * 86400
    removed = 0
    for parent in (cfg.data / "runs", cfg.data / "spool" / "archive"):
        if not parent.is_dir():
            continue
        for d in parent.iterdir():
            if d.stat().st_mtime < cutoff:
                print(("would remove " if args.dry_run else "removing ") + str(d))
                if not args.dry_run:
                    shutil.rmtree(d, ignore_errors=True)
                removed += 1
    print(f"{'would prune' if args.dry_run else 'pruned'} {removed} item(s) older than {args.days} days "
          "(logs/*.jsonl and logs/<job>.md are kept; rotate them with logrotate)")
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    from .reporter import Reporter

    ds = Reporter(_cfg(args)).notify(args.severity, args.title, args.text, dedup_key=args.key)
    for d in ds:
        print(f"{d.channel}: {'ok' if d.ok else 'FAIL ' + d.error}")
    return 0 if all(d.ok for d in ds) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wake", description="Wake a local CLI agent, log, and report.")
    p.add_argument("--config", help="path to wake.toml (default: $WAKE_CONFIG, ./wake.toml, ~/.wake/wake.toml)")
    p.add_argument("--version", action="version", version=f"wake-agent {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write a starter config, prompts and capture script")
    s.add_argument("dir", nargs="?", default=".")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("run", help="run a job now")
    s.add_argument("job")
    s.add_argument("--trigger", default="manual")
    s.add_argument("--dry-run", action="store_true", help="run the agent but send no notifications")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("emit", help="enqueue an event for a job (e.g. from an ssh forced command)")
    s.add_argument("job")
    s.add_argument("--sig", default="manual")
    s.add_argument("--line", help="event text (default: read stdin)")
    s.add_argument("--data", help="JSON object with extra fields")
    s.add_argument("--wake", action="store_true", help="run the job right after enqueueing")
    s.set_defaults(fn=cmd_emit)

    s = sub.add_parser("watch", help="follow journald/log files and enqueue matching events")
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("check", help="watchdog: page if the wake chain is broken")
    s.add_argument("--drill", action="store_true", help="print problems, never page")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("doctor", help="verify runners, channels, paths and mutes")
    s.add_argument("--smoke", action="store_true", help="also run runner smoke tests (Muse: --provider echo)")
    s.add_argument("--send-test", action="store_true", help="send a test notification")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("status", help="last run and queue depth per job")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("runs", help="recent run records")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--job")
    s.set_defaults(fn=cmd_runs)

    s = sub.add_parser("systemd", help="generate systemd user units")
    s.add_argument("--out", help="output dir (default ~/.config/systemd/user)")
    s.add_argument("--print", action="store_true", help="print units instead of writing files")
    s.set_defaults(fn=cmd_systemd)

    s = sub.add_parser("prune", help="delete old run transcripts and archived events")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_prune)

    s = sub.add_parser("notify", help="send a message through the configured channels")
    s.add_argument("severity", choices=["info", "alert", "urgent"])
    s.add_argument("title")
    s.add_argument("text", nargs="?", default="")
    s.add_argument("--key", help="dedup key (alert/urgent only)")
    s.set_defaults(fn=cmd_notify)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"error: {e.args[0]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
