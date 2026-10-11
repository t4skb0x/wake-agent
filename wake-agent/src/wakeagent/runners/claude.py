"""Claude Code adapter (EXPERIMENTAL). Uses print mode: `claude -p "<prompt>"`.
read-only -> allow only Read/Grep/Glob. fix -> acceptEdits permission mode plus any
`allowed_tools` you list (e.g. ["Bash(git status)", "Edit"]). Verify flags with `claude --help`."""
from __future__ import annotations

from .base import RunRequest, Runner


class Claude(Runner):
    name = "claude"
    binary_names = ["claude"]
    extra_paths = ["~/.claude/local/claude", "~/.local/bin/claude"]
    experimental = True

    def build_argv(self, binary: str, req: RunRequest, prompt_arg: str) -> list[str]:
        o = {**self.cfg, **req.options}
        argv = [binary, "-p", prompt_arg]
        if req.mode == "read-only":
            argv += ["--allowedTools", "Read,Grep,Glob"]
        else:
            argv += ["--permission-mode", str(o.get("permission_mode", "acceptEdits"))]
            tools = o.get("allowed_tools")
            if tools:
                argv += ["--allowedTools", ",".join(tools)]
        argv += [str(x) for x in o.get("extra_args", [])]
        return argv
