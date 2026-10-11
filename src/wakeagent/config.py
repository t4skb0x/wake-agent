"""Configuration: a single wake.toml. Secrets are never stored here, only env var names."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SEVERITIES = ("info", "alert", "urgent")
MODES = ("read-only", "fix")


class ConfigError(Exception):
    pass


@dataclass
class Job:
    name: str
    runner: str
    prompt: Path
    mode: str = "read-only"
    timeout: int = 900
    workdir: Path = field(default_factory=Path.home)
    capture: Path | None = None
    capture_timeout: int = 240
    include: list[Path] = field(default_factory=list)
    events: bool = False          # consumes events from the spool
    max_attempts: int = 3         # event retries before dead-lettering
    max_drain: int = 10           # re-run loops while events keep arriving
    max_events: int = 25          # events per agent run (0 = unlimited); a flood is drained in batches
    max_actions: int | None = None  # post-hoc audit of result.actions
    schedule: str | None = None   # systemd OnCalendar string (for `wake systemd`)
    expect_every_hours: float | None = None  # `wake check` pages if no good run in this window
    notify_ok: str = "none"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Watch:
    name: str
    job: str
    source: str            # journal | file
    pattern: re.Pattern
    cls: str
    args: list[str] = field(default_factory=list)   # journalctl args
    path: Path | None = None                         # file source
    key_regex: re.Pattern | None = None              # named group "key"
    dedup_hours: float = 6.0
    recheck_cmd: str | None = None
    recheck_delay: int = 600


@dataclass
class Mute:
    pattern: re.Pattern
    until: float
    reason: str
    owner: str
    job: str | None = None
    raw_until: str = ""


@dataclass
class Config:
    path: Path
    base: Path
    data: Path
    raw: dict[str, Any]
    jobs: dict[str, Job]
    watches: list[Watch]
    mutes: list[Mute]
    channels: dict[str, dict[str, Any]]
    routing: dict[str, list[str]]
    runners: dict[str, dict[str, Any]]
    dedup_hours: float
    max_per_hour: int
    trigger_mode: str
    stale_minutes: int


def find_config(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("WAKE_CONFIG"):
        candidates.append(Path(os.environ["WAKE_CONFIG"]))
    if os.environ.get("WAKE_HOME"):
        candidates.append(Path(os.environ["WAKE_HOME"]) / "wake.toml")
    candidates += [Path.cwd() / "wake.toml", Path.home() / ".wake" / "wake.toml"]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    raise ConfigError(
        "no wake.toml found (tried --config, $WAKE_CONFIG, $WAKE_HOME, ./wake.toml, ~/.wake/wake.toml). "
        "Run `wake init` to create one."
    )


def _p(base: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    return (base / os.path.expanduser(value)).resolve() if not os.path.isabs(os.path.expanduser(value)) \
        else Path(os.path.expanduser(value))


def load(path: str | Path | None = None) -> Config:
    from .util import parse_when

    cfg_path = find_config(str(path) if path else None)
    try:
        raw = tomllib.loads(cfg_path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{cfg_path}: invalid TOML: {e}") from e
    base = cfg_path.parent
    w = raw.get("wake", {})
    data = _p(base, w.get("data_dir", "data")) or base / "data"

    runners = raw.get("runners", {})
    channels = raw.get("channels", {})
    routing = raw.get("routing", {})
    for sev, chans in routing.items():
        if sev not in SEVERITIES:
            raise ConfigError(f"routing.{sev}: severity must be one of {SEVERITIES}")
        for c in chans:
            if c not in channels:
                raise ConfigError(f"routing.{sev}: unknown channel {c!r}")

    jobs: dict[str, Job] = {}
    for name, j in raw.get("jobs", {}).items():
        if not NAME_RE.match(name):
            raise ConfigError(f"jobs.{name}: invalid name")
        for req in ("runner", "prompt"):
            if req not in j:
                raise ConfigError(f"jobs.{name}: missing required key {req!r}")
        if j["runner"] not in runners:
            raise ConfigError(f"jobs.{name}: unknown runner {j['runner']!r} (define [runners.{j['runner']}])")
        mode = j.get("mode", "read-only")
        if mode not in MODES:
            raise ConfigError(f"jobs.{name}: mode must be one of {MODES}")
        if j.get("notify_ok", "none") not in ("none",) + SEVERITIES:
            raise ConfigError(f"jobs.{name}: notify_ok must be none/info/alert/urgent")
        jobs[name] = Job(
            name=name,
            runner=j["runner"],
            prompt=_p(base, j["prompt"]),  # type: ignore[arg-type]
            mode=mode,
            timeout=int(j.get("timeout", 900)),
            workdir=_p(base, j.get("workdir", "~")) or Path.home(),
            capture=_p(base, j.get("capture")),
            capture_timeout=int(j.get("capture_timeout", 240)),
            include=[_p(base, i) for i in j.get("include", [])],  # type: ignore[misc]
            events=bool(j.get("events", False)),
            max_attempts=int(j.get("max_attempts", 3)),
            max_drain=int(j.get("max_drain", 10)),
            max_events=int(j.get("max_events", 25)),
            max_actions=j.get("max_actions"),
            schedule=j.get("schedule"),
            expect_every_hours=j.get("expect_every_hours"),
            notify_ok=j.get("notify_ok", "none"),
            options=dict(j.get("options", {})),
        )

    watches: list[Watch] = []
    for i, wr in enumerate(raw.get("watch", [])):
        nm = wr.get("name", f"watch{i}")
        for req in ("job", "source", "pattern"):
            if req not in wr:
                raise ConfigError(f"watch[{nm}]: missing {req!r}")
        if wr["job"] not in jobs:
            raise ConfigError(f"watch[{nm}]: unknown job {wr['job']!r}")
        if wr["source"] not in ("journal", "file"):
            raise ConfigError(f"watch[{nm}]: source must be journal or file")
        if wr["source"] == "file" and "path" not in wr:
            raise ConfigError(f"watch[{nm}]: file source needs path")
        try:
            pat = re.compile(wr["pattern"])
            kr = re.compile(wr["key_regex"]) if wr.get("key_regex") else None
        except re.error as e:
            raise ConfigError(f"watch[{nm}]: bad regex: {e}") from e
        watches.append(Watch(
            name=nm, job=wr["job"], source=wr["source"], pattern=pat, cls=wr.get("class", nm),
            args=list(wr.get("args", [])), path=_p(base, wr.get("path")), key_regex=kr,
            dedup_hours=float(wr.get("dedup_hours", 6)), recheck_cmd=wr.get("recheck_cmd"),
            recheck_delay=int(wr.get("recheck_delay", 600)),
        ))

    mutes: list[Mute] = []
    for m in raw.get("mute", []):
        for req in ("pattern", "until", "reason", "owner"):
            if req not in m:
                raise ConfigError(f"mute: missing {req!r} (mutes need pattern, until, reason, owner)")
        try:
            mutes.append(Mute(pattern=re.compile(m["pattern"]), until=parse_when(str(m["until"])),
                              reason=m["reason"], owner=m["owner"], job=m.get("job"),
                              raw_until=str(m["until"])))
        except (re.error, ValueError) as e:
            raise ConfigError(f"mute {m.get('pattern')!r}: {e}") from e

    if not data.exists():
        data.mkdir(parents=True, mode=0o700)  # transcripts and logs may contain sensitive output
        data.chmod(0o700)
    return Config(
        path=cfg_path, base=base, data=data, raw=raw, jobs=jobs, watches=watches, mutes=mutes,
        channels=channels, routing={k: list(v) for k, v in routing.items()}, runners=runners,
        dedup_hours=float(raw.get("reporting", {}).get("dedup_hours", 6)),
        max_per_hour=int(raw.get("reporting", {}).get("max_per_hour", 30)),
        trigger_mode=w.get("trigger_mode", "spawn"),
        stale_minutes=int(w.get("stale_minutes", 15)),
    )
