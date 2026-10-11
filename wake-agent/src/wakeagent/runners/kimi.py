"""Kimi Code adapter (EXPERIMENTAL). Derived from a production wake script that used
`kimi --prompt "<text>"` from the home directory. Mode cannot be enforced via flags here, so
`read-only` is advisory (prompt-level) only; run Kimi in a sandbox/container for real isolation."""
from __future__ import annotations

from .base import Probe, RunRequest, Runner


class Kimi(Runner):
    name = "kimi"
    binary_names = ["kimi"]
    extra_paths = ["~/.kimi-code/bin/kimi", "~/.local/bin/kimi"]  # the CLI relocated itself once
    experimental = True

    def build_argv(self, binary: str, req: RunRequest, prompt_arg: str) -> list[str]:
        argv = [binary, "--prompt", prompt_arg]
        argv += [str(x) for x in {**self.cfg, **req.options}.get("extra_args", [])]
        return argv

    def probe(self, smoke: bool = False) -> Probe:
        p = super().probe()
        p.notes.append("read-only mode is advisory for this runner (no enforcing flag known)")
        return p
