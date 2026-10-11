# Lessons from production

Each design choice here fixes something that actually broke.

| Failure | Root cause | What wake-agent does instead |
|---|---|---|
| Wake chain dark for 44 hours | The agent CLI self-updated: moved its binary out of the service `PATH`, retired a flag, dropped another | Runner discovery at run time (env, config, known paths, `PATH`), `wake doctor`, version recorded on every run, nightly drift smoke test |
| Verdicts done but alert stayed red forever | The agent was asked to archive the queue and did not | The harness archives; the agent never touches the queue |
| "Last wake failed" watchdog could never fire | The wrapper script's exit status was its final `echo`, always 0 | The harness owns the exit code; no result means failed; failed runs are recorded and paged |
| Events lost between read and archive | Appends during a run were archived with the old batch | Atomic claim-by-rename; events arriving mid-run stay pending; the drain loop handles them |
| Watcher stalled during agent turns | `systemctl start` on a oneshot blocks until it finishes | Non-blocking wake; the job lock coalesces overlapping triggers |
| Distinct failures hidden for 6 hours | Dedup keyed on process name (`python3`, `systemd`) | Content-normalized or `key_regex` keys; suppressed counts reported |
| Same self-healing transient triaged ~60 times | Every blip woke a full agent turn | `recheck_cmd` + `recheck_delay`: wait, test, auto-close without an agent |
| Backgrounded jobs died with the agent turn | Child processes were killed with the turn's cgroup | Triggers are detached processes or systemd units, never children of the agent |
| A disabled timer meant silence | Failure watches never see a timer that is simply off | `expect_every_hours` + `wake check` pages on absence of success, not just on failure |
| Mutes forgotten for weeks | Hardcoded content mutes with no owner | Mutes require owner, reason and expiry; muted lines are still recorded; `doctor` warns on expired mutes |
| A fixed-date guard silently never worked | A hardcoded date survived a "fix" | No dates in scripts; schedules live in systemd timers |
| Prompt text contradicted the rules | Five prompts each repeated the law block and drifted | One shared `include` file for standing rules |
| Page storms and SMS fatigue | Agents paged directly, with per-script dedup state | Only the harness reports; one dedup store; channel routing by severity (chat for all, SMS for urgent) |
