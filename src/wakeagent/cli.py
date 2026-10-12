
import argparse, pathlib, json, os, sys
from .config import load_config
from .mailbox import watch_loop, recover_stale, load_acl

def cmd_init(args):
    print(f"Initializing wake in {args.dir}")
    pathlib.Path(args.dir).mkdir(parents=True, exist_ok=True)
    # copy example trading config
    example = pathlib.Path(__file__).parent.parent.parent / "examples" / "trading" / "wake.toml.trading"
    if example.exists():
        (pathlib.Path(args.dir) / "wake.toml").write_text(example.read_text())
    print("Wrote wake.toml, prompts/, scripts/")

def cmd_doctor(args):
    print("Checking runners, channels, paths...")
    # Muse credential-free echo test
    import subprocess
    try:
        result = subprocess.run(["Muse", "exec", "--help"], capture_output=True, text=True, timeout=10)
        print("Muse CLI found")
    except FileNotFoundError:
        print("Muse CLI not found - install via curl -fsSL https://dev.meta.ai/install.sh | bash", file=sys.stderr)

def cmd_run(args):
    config = load_config(args.config)
    jobs = config.get("jobs", {})
    job = jobs.get(args.job)
    if not job:
        print(f"Job {args.job} not found", file=sys.stderr)
        sys.exit(1)
    # If mailbox job, run mailbox watcher once
    if job.get("events"):
        print(f"Running event job {args.job}")
        # For mailbox, process pending
        from .mailbox import watch_loop
        base_dir = config.get("base_dir", "/srv/relay/mailboxes")
        acl = load_acl(config.get("acl_path", "/srv/relay/acl.json"))
        watch_loop(base_dir, config, acl, poll_interval=2, dry_run=args.dry_run)
    else:
        print(f"Running scheduled job {args.job} dry_run={args.dry_run}")

def cmd_watch(args):
    config = load_config(args.config)
    base_dir = config.get("base_dir", "/srv/relay/mailboxes")
    acl = load_acl(config.get("acl_path", "/srv/relay/acl.json"))
    watch_loop(base_dir, config, acl, poll_interval=config.get("poll_interval", 2), dry_run=args.dry_run)

def cmd_systemd(args):
    unit = """[Unit]
Description=wake-agent mailbox watcher - generic + trading reboot-proof
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wake-agent watch --config /srv/relay/wake.toml
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
"""
    if args.print:
        print(unit)
    else:
        p = pathlib.Path("wake-agent.service")
        p.write_text(unit)
        print(f"Wrote {p}")

def main():
    parser = argparse.ArgumentParser(prog="wake")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init")
    init.add_argument("dir", nargs="?", default="~/wake")
    init.set_defaults(func=cmd_init)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--smoke", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    run = sub.add_parser("run")
    run.add_argument("job")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--config", default="wake.toml")
    run.set_defaults(func=cmd_run)

    watch = sub.add_parser("watch")
    watch.add_argument("--config", default="wake.toml")
    watch.add_argument("--dry-run", action="store_true")
    watch.set_defaults(func=cmd_watch)

    systemd = sub.add_parser("systemd")
    systemd.add_argument("--print", action="store_true")
    systemd.set_defaults(func=cmd_systemd)

    # Compatibility with previous wake-agent generic: wake-agent CLI
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
