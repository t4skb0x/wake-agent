# Runners

A runner knows how to find a CLI agent, probe it, and build its headless command line. The harness does
the rest (timeouts, process groups, result validation).

| Runner | Status | Command | Mode mapping |
|---|---|---|---|
| `muse` | first-class, see caveats | `muse exec --json [--provider P] --approval-mode M [...] PROMPT` | read-only: `untrusted` + `--disable-write --disable-shell`; fix: `fix_approval_mode` (default `on-request`) |
| `claude` | experimental | `claude -p PROMPT [--allowedTools ...] [--permission-mode ...]` | read-only: `Read,Grep,Glob`; fix: `acceptEdits` + `allowed_tools` |
| `codex` | experimental | `codex exec --sandbox S [--skip-git-repo-check] PROMPT` | read-only: `read-only`; fix: `fix_sandbox` (default `workspace-write`) |
| `kimi` | experimental | `kimi --prompt PROMPT` | advisory only; sandbox it yourself |

## Muse Code notes

- Built from public docs and the community `muse-codes` SDK captures. The event stream has no published
  schema, so wake-agent only counts events and uses `result.json` as the contract.
- The CLI **auto-updates hourly**. Every run record stores the version; `wake doctor` shows it. Pin or
  disable updates if you need stability, and keep the nightly `doctor --smoke` CI job.
- Check what `--approval-mode on-request` does without a TTY on your version. If your fix jobs stall, set
  `fix_approval_mode` explicitly and rely on a sandbox.
- Auth: `muse login`, `META_API_KEY`, or `~/.config/muse/auth.json`. A stale environment key can mask a
  browser login.
- Binary lookup order: `$WAKE_MUSE_BIN`, `runners.muse.bin`, `~/.local/bin/muse`, then `PATH`. systemd
  user units have a minimal `PATH`, so set `bin` explicitly there.

## Adding a runner

Subclass `Runner`, set `name`, `binary_names`, `extra_paths`, implement `build_argv`, optionally override
`probe` and `parse_stream`, register it in `runners/__init__.py`, and add argv tests. Keep
`experimental = True` until verified against the real binary.

Options set in `[runners.NAME]` apply to every job; `[jobs.X.options]` overrides per job.
