"""Codex CLI adapter (EXPERIMENTAL). Uses `codex exec --sandbox <read-only|workspace-write> "<prompt>"`.
Verify flags with `codex exec --help` on your installed version."""
from __future__ import annotations

from .base import RunRequest, Runner


class Codex(Runner):
    name = "codex"
    binary_names = ["codex"]
    extra_paths = ["~/.local/bin/codex"]
    experimental = True

    def build_argv(self, binary: str, req: RunRequest, prompt_arg: str) -> list[str]:
        o = {**self.cfg, **req.options}
        sandbox = "read-only" if req.mode == "read-only" else str(o.get("fix_sandbox", "workspace-write"))
        argv = [binary, "exec", "--sandbox", sandbox]
        if o.get("skip_git_repo_check", True):
            argv.append("--skip-git-repo-check")
        argv += [str(x) for x in o.get("extra_args", [])]
        argv.append(prompt_arg)
        return argv
