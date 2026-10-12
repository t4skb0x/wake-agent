
import argparse, pathlib, json, sys
from .config import load_config
from .mailbox import watch_loop, recover_stale, load_acl

def cmd_init(args):
    print(f"Initializing wake in {args.dir}")
    pathlib.Path(args.dir).mkdir(parents=True, exist_ok=True)

def cmd_doctor(args):
    print("Checking runners, channels, paths...")
    import subprocess
    try:
        subprocess.run(["Muse", "exec", "--help"], capture_output=True, text=True, timeout=10)
        print("Muse CLI found")
    except FileNotFoundError:
        print("Muse CLI not found")

def cmd_run(args):
    config = load_config(args.config)
    jobs = config.get("jobs", {})
    job = jobs.get(args.job)
    if not job:
        print(f"Job {args.job} not found", file=sys.stderr)
        sys.exit(1)
    if job.get("events"):
        base_dir = config.get("base_dir", "/srv/relay/mailboxes")
        acl = load_acl(config.get("acl_path", "/srv/relay/acl.json"))
        watch_loop(base_dir, config, acl, poll_interval=2, dry_run=args.dry_run)
    else:
        print(f"Running scheduled job {args.job}")

def cmd_watch(args):
    config = load_config(args.config)
    base_dir = config.get("base_dir", "/srv/relay/mailboxes")
    acl = load_acl(config.get("acl_path", "/srv/relay/acl.json"))
    watch_loop(base_dir, config, acl, poll_interval=config.get("poll_interval", 2), dry_run=args.dry_run)

def cmd_systemd(args):
    unit = "[Unit]\nDescription=wake-agent with mailbox core\nAfter=network-online.target\n\n[Service]\nType=simple\nExecStart=/usr/local/bin/wake-agent watch --config /srv/relay/wake.toml\nRestart=always\n\n[Install]\nWantedBy=default.target\n"
    if args.print:
        print(unit)
    else:
        pathlib.Path("wake-agent.service").write_text(unit)

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
    args = parser.parse_args()
    args.func(args)
