# Architecture

## Data layout (`data_dir`, default `./data`)

```
spool/pending/      events waiting for a job (one JSON file per event)
spool/processing/   <run_id>/ events claimed by a running job
spool/archive/      <run_id>/ events that received a verdict
spool/dead/         events that failed max_attempts times; a human must look
runs/<run_id>/      prompt.md, evidence.txt, agent.stdout (event stream), agent.stderr, result.json
logs/<job>.md       human-readable verdict log, one entry per run
logs/verdicts.jsonl one line per event verdict
logs/runs.jsonl     one record per run (rc, duration, runner version, status, notifications)
logs/notify.jsonl   every notification attempt, including deduped ones
logs/watch.jsonl    watcher actions (emitted, auto-closed, reloads)
muted.jsonl         lines suppressed by mutes (audit trail)
state/              paged.json (report dedup), watch_dedup.json, last_run.json, heartbeat.json, rate.json
locks/              per-job flock files
```

## A run

1. `wake run JOB` takes the job's lock without blocking. If another run holds it, it exits `busy`; the
   events stay pending and the running instance picks them up in its drain loop (`max_drain`).
2. Orphans from a crashed earlier run are returned to `pending`.
3. Up to `max_events` (default 25) events are **claimed by atomic rename** into `processing/<run_id>/`, so
   concurrent claimers never overlap and nothing is read-then-lost. A flood is drained in batches, up to
   `max_drain` batches per wake; anything left stays pending for the next trigger.
4. The `capture` script (if any) writes `evidence.txt`. A timeout or failure becomes a `MISSING:` line in
   the evidence rather than aborting the run.
5. The prompt is assembled: shared `include` files, the job checklist, evidence, events (fenced as
   untrusted data) and the result contract.
6. The runner starts the agent in its own process group with a timeout. On timeout the whole group is
   killed.
7. The harness validates `result.json`: valid JSON, known status, a summary, and a verdict for every event.
   **Anything else is a failed run.**
   - Success: verdicts go to the log, events are archived, the notification is routed and deduped.
   - Failure: events return to `pending` with `attempts+1` (dead-lettered at `max_attempts`), an `alert`
     is sent (`urgent` if something was dead-lettered), and the run exits non-zero.
8. A run record is appended to `runs.jsonl`, including the agent CLI version.

## result.json

```json
{"status": "ok|fixed|problem|operator", "summary": "...",
 "verdicts": [{"event_id": "...", "verdict": "FIXED|OK|OPERATOR|IGNORED", "reason": "..."}],
 "notify": "none|info|alert|urgent", "actions": ["..."], "dedup_key": "optional"}
```

Default `notify` by status: ok=none, fixed=info, problem=alert, operator=alert. A job's `notify_ok` can
raise the floor for healthy runs (a daily "all clear").

## Watcher

One follower process per distinct source (a journalctl argument set, or a file). Each line is tested
against every rule on that source, then: muted? deduped? recheck? Otherwise it becomes an event and a wake
is started **without waiting** (`spawn`: detached process, or `systemd`: `systemctl --user start --no-block`).

- Reporting dedup (alert/urgent) is **per channel**: a page that reached nobody gives its window back, so a
  down SMS gateway retries on the next occurrence instead of being hidden behind a working chat channel.
- Dedup key = rule class + `key_regex` group, or a hash of the line with numbers and ids normalized. A
  different error is never swallowed by an earlier error's window, and suppressed counts travel with the
  next event.
- Followers restart with backoff and resume from the last seen time, so a restart does not go blind.
- A heartbeat is written every 30s; config changes are hot-reloaded.

## Retention

`wake prune --days 30` deletes old `runs/<run_id>/` transcripts and archived events (`--dry-run` to preview).
The `.jsonl` logs and `<job>.md` verdict logs are kept; rotate them with logrotate. `data_dir` is created
mode 0700 because agent transcripts can contain sensitive output.

## Watchdog (`wake check`)

Pages when it can prove a link is broken: watcher heartbeat stale, events pending longer than
`stale_minutes`, last run failed, a job past its `expect_every_hours`, anything dead-lettered, or crashed
runs holding claimed events. Run it from a timer (`wake systemd` generates one every 5 minutes). It is
deliberately independent of the agent: it checks harness state, so it still works when the agent is the
thing that broke.
