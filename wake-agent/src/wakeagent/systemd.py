"""Generate systemd user units for the watcher, per-job wakes, timers and the check watchdog."""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

from .config import Config


def units(cfg: Config) -> dict[str, str]:
    py = sys.executable
    base = " ".join(shlex.quote(a).replace("%", "%%") for a in (py, "-m", "wakeagent", "--config", str(cfg.path)))
    env = f"EnvironmentFile=-{cfg.base / 'secrets.env'}"
    out: dict[str, str] = {}
    if cfg.watches:
        out["wake-watch.service"] = f"""[Unit]
Description=wake-agent event watcher

[Service]
ExecStart={base} watch
Restart=always
RestartSec=5
{env}

[Install]
WantedBy=default.target
"""
    out["wake@.service"] = f"""[Unit]
Description=wake-agent run %i

[Service]
Type=oneshot
ExecStart={base} run %i --trigger systemd
TimeoutStartSec=infinity
{env}
"""
    for job in cfg.jobs.values():
        if job.schedule:
            out[f"wake-{job.name}.timer"] = f"""[Unit]
Description=wake-agent schedule for {job.name}

[Timer]
OnCalendar={job.schedule}
Unit=wake@{job.name}.service

[Install]
WantedBy=timers.target
"""
    out["wake-check.service"] = f"""[Unit]
Description=wake-agent watchdog

[Service]
Type=oneshot
ExecStart={base} check
{env}
"""
    out["wake-check.timer"] = """[Unit]
Description=wake-agent watchdog every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Unit=wake-check.service

[Install]
WantedBy=timers.target
"""
    return out


def write(cfg: Config, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    names = []
    for name, text in units(cfg).items():
        (out_dir / name).write_text(text)
        names.append(name)
    return names


def enable_commands(cfg: Config) -> list[str]:
    cmds = ["systemctl --user daemon-reload"]
    if cfg.watches:
        cmds.append("systemctl --user enable --now wake-watch.service")
    for job in cfg.jobs.values():
        if job.schedule:
            cmds.append(f"systemctl --user enable --now wake-{job.name}.timer")
    cmds.append("systemctl --user enable --now wake-check.timer")
    cmds.append("loginctl enable-linger $USER   # keep user units running when logged out")
    return cmds
