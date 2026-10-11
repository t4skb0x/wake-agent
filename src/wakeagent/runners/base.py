"""Runner interface: how a particular CLI agent is found, probed and invoked headlessly."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_ARG_BYTES = 100_000  # Linux caps a single argv string at ~128 KiB


@dataclass
class RunRequest:
    prompt: str
    workdir: Path
    timeout: int
    mode: str                 # read-only | fix
    run_dir: Path
    env: dict[str, str] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    drop_env: list[str] = field(default_factory=list)  # env vars withheld from the agent


@dataclass
class RunResult:
    rc: int
    timed_out: bool
    duration: float
    version: str
    argv_redacted: list[str]
    stdout_path: Path
    stderr_path: Path
    events: int = 0
    last_event: str = ""
    note: str = ""


@dataclass
class Probe:
    ok: bool
    binary: str = ""
    version: str = ""
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Runner:
    name = ""
    binary_names: list[str] = []
    extra_paths: list[str] = []
    version_args: list[str] = ["--version"]
    experimental = False

    def __init__(self, cfg: dict[str, Any] | None = None):
        self.cfg = cfg or {}

    # -- discovery (AT-4 lesson: CLIs self-update, relocate, and rename flags) ----
    def resolve_binary(self) -> str | None:
        env_name = f"WAKE_{self.name.upper()}_BIN"
        for cand in (os.environ.get(env_name), self.cfg.get("bin")):
            if cand and os.access(os.path.expanduser(cand), os.X_OK):
                return os.path.expanduser(cand)
        for p in self.extra_paths:
            p = os.path.expanduser(p)
            if os.access(p, os.X_OK):
                return p
        for n in self.binary_names:
            found = shutil.which(n)
            if found:
                return found
        return None

    def version(self, binary: str) -> str:
        try:
            r = subprocess.run([binary, *self.version_args], capture_output=True, text=True, timeout=20,
                               stdin=subprocess.DEVNULL)
            return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def probe(self, smoke: bool = False) -> Probe:
        b = self.resolve_binary()
        if not b:
            return Probe(False, problems=[
                f"binary not found (set WAKE_{self.name.upper()}_BIN, runners.{self.name}.bin, or put "
                f"{'/'.join(self.binary_names)} on PATH; note systemd user units have a minimal PATH)"])
        v = self.version(b)
        p = Probe(True, binary=b, version=v)
        if not v:
            p.notes.append("could not read version")
        if self.experimental:
            p.notes.append("adapter is experimental: argv built from public docs, not verified in CI")
        return p

    # -- invocation -----------------------------------------------------------
    def build_argv(self, binary: str, req: RunRequest, prompt_arg: str) -> list[str]:
        raise NotImplementedError

    def parse_stream(self, stdout_path: Path) -> tuple[int, str]:
        """Best-effort (event_count, last_event_type). Default: count JSON lines."""
        n, last = 0, ""
        try:
            for line in stdout_path.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                n += 1
                if isinstance(obj, dict):
                    last = str(obj.get("type") or obj.get("payload_type") or obj.get("event") or last)
        except OSError:
            pass
        return n, last

    def prompt_arg(self, req: RunRequest) -> str:
        """Pass the prompt inline, or a bootstrap line when it would exceed argv limits."""
        if len(req.prompt.encode()) <= MAX_ARG_BYTES:
            return req.prompt
        pf = req.run_dir / "prompt.md"
        return f"Read the file {pf} in full and follow every instruction in it exactly."

    def run(self, req: RunRequest) -> RunResult:
        binary = self.resolve_binary()
        stdout_p, stderr_p = req.run_dir / "agent.stdout", req.run_dir / "agent.stderr"
        if not binary:
            stderr_p.write_text(f"runner {self.name}: binary not found\n")
            stdout_p.write_text("")
            return RunResult(127, False, 0.0, "", [], stdout_p, stderr_p, note="binary not found")
        version = self.version(binary)
        argv = self.build_argv(binary, req, self.prompt_arg(req))
        shown = [a if len(a) < 200 else a[:200] + "…" for a in argv]
        env = {k: v for k, v in os.environ.items() if k not in req.drop_env}
        env.update(req.env)
        t0 = time.time()
        timed_out = False
        with open(stdout_p, "w") as so, open(stderr_p, "w") as se:
            try:
                proc = subprocess.Popen(argv, cwd=req.workdir, env=env, stdin=subprocess.DEVNULL,
                                        stdout=so, stderr=se, start_new_session=True)
            except OSError as e:  # bad workdir, not executable, ...: a failed run, never a crash
                se.write(f"failed to start {argv[0]}: {e}\n")
                return RunResult(126, False, time.time() - t0, version, shown, stdout_p, stderr_p,
                                 note=f"spawn failed: {e}")
            try:
                rc = proc.wait(timeout=req.timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                try:  # the leader may be gone while children linger; clean the whole group either way
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                rc = proc.returncode if proc.returncode is not None else -9
        n, last = self.parse_stream(stdout_p)
        return RunResult(rc, timed_out, time.time() - t0, version, shown, stdout_p, stderr_p, n, last)
