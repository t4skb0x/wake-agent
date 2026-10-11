# Threat model

wake-agent gives an AI agent real permissions on a machine and feeds it text it did not write. Treat it
like any automation with shell access.

1. **Prompt injection through events and evidence.** Log lines, alerts and captured output can contain
   instructions. The prompt fences them as untrusted data (and escapes any text that tries to close the
   fence early) and tells the agent never to follow them, but that is a mitigation, not a guarantee. Do not give a `fix` job access you would not give to whoever can
   write to those logs.
2. **`mode = "fix"` is real write access.** Run it in a container, a dedicated user, or a VM, with only
   the credentials the job needs. Prefer many narrow jobs over one broad one.
3. **`read-only` depends on the runner.** It is enforced by CLI flags where the CLI supports them (see
   `docs/RUNNERS.md`) and advisory otherwise. The post-run check of reported `actions` is an audit, not
   a control.
4. **Secrets.** Channel credentials (any variable named by a `*_env` key in `[channels.*]`) are **withheld
   from the agent's environment**; only the harness reports, so the agent never needs them. A job can opt a
   variable back in with `options.pass_env`, but think twice: whatever the agent can read it can leak.
   Other variables, such as the agent's own API key, are passed through. They live in environment variables (for systemd: a `secrets.env` with mode 600, which
   `wake init` does not create for you). Config holds only variable names. Outgoing notifications are
   scrubbed of configured secret values. Agent transcripts in `runs/` may still contain whatever the agent
   printed, so protect `data_dir`.
5. **`wake emit` and ssh relays.** Anything that can call `wake emit` can wake the agent with arbitrary
   text. Restrict it (ssh forced command, dedicated key) and rely on dedup and `max_attempts` to bound
   cost.
6. **Cost and loops.** Dedup, `max_attempts`, `max_drain`, the per-hour channel rate limit and the job lock
   bound runaway behavior. Alerts that your own notifications could re-trigger (for example a watcher on
   a log your agent writes to) need a mute or a different log.
