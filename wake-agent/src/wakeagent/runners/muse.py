"""Muse Code (Meta) adapter. Headless mode: `muse exec --json [OPTIONS] [PROMPT]`.

Facts this adapter relies on (from public docs and the community muse-codes SDK captures):
  * `muse exec` is the non-interactive entry point; `--json` emits a JSONL event stream on stdout.
  * `--approval-mode <untrusted|on-request|never>` (default on-request).
  * `--disable-write` / `--disable-shell` exist.
  * `--provider echo` is a credential-free mode that exercises the event stream without model calls.
  * Auth: `muse login` (device code), META_API_KEY, or ~/.config/muse/auth.json.
  * The CLI auto-updates hourly and publishes no event schema. We therefore record the version on
    every run and treat result.json (written by the agent) as the contract, not the event stream.
Verify flags with `muse exec --help` on your installed version; this is a 0.x beta.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .base import Probe, RunRequest, Runner


class Muse(Runner):
    name = "muse"
    binary_names = ["muse"]
    extra_paths = ["~/.local/bin/muse"]

    def build_argv(self, binary: str, req: RunRequest, prompt_arg: str) -> list[str]:
        o = {**self.cfg, **req.options}
        argv = [binary, "exec", "--json"]
        if o.get("provider"):
            argv += ["--provider", str(o["provider"])]
        if req.mode == "read-only":
            argv += ["--approval-mode", "untrusted", "--disable-write", "--disable-shell"]
        else:
            argv += ["--approval-mode", str(o.get("fix_approval_mode", "on-request"))]
        argv += [str(x) for x in o.get("extra_args", [])]
        argv.append(prompt_arg)
        return argv

    def probe(self, smoke: bool = False) -> Probe:
        p = super().probe()
        if not p.ok:
            return p
        has_cred = bool(os.environ.get("META_API_KEY")) or Path("~/.config/muse/auth.json").expanduser().exists()
        if not has_cred and self.cfg.get("provider") != "echo":
            p.notes.append("no META_API_KEY or ~/.config/muse/auth.json found; run `muse login` (heuristic check)")
        if smoke:
            try:
                r = subprocess.run([p.binary, "exec", "--json", "--provider", "echo", "ping"],
                                   capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
                lines = [l for l in r.stdout.splitlines() if l.strip().startswith("{")]
                if r.returncode != 0 or not lines:
                    p.ok = False
                    p.problems.append(f"echo smoke test failed rc={r.returncode}, json_lines={len(lines)}: "
                                      f"{r.stderr.strip()[:200]}")
                else:
                    p.notes.append(f"echo smoke test ok ({len(lines)} events)")
            except (OSError, subprocess.TimeoutExpired) as e:
                p.ok = False
                p.problems.append(f"echo smoke test error: {e}")
        return p
