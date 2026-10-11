# wake-agent

Wake a local CLI coding agent on a timer or an event, let it do a job, **log what it did**, and **report
back** over Telegram, Slack, Discord, SMS or ntfy.

Supported agents: **Muse Code** (first-class), plus Kimi Code, Claude Code and Codex (experimental).
Pure Python standard library. One config file. Runs on any Linux box with systemd (or without it).

```
trigger ──► [capture script] ──► agent (headless) ──► result.json ──► log + report
 timer        evidence file        muse / claude /       validated       verdict log,
 watcher                           codex / kimi          by harness      Telegram/SMS/...
 manual / emit
```

## Why this exists

It started as an alert-triage system: a watcher follows the journal, and each alert wakes a headless agent
that diagnoses and fixes it. Running that for weeks produced a list of failures (a CLI that moved its own
binary, an agent that forgot to archive the queue, a watchdog that could never fire). This project keeps
the idea and moves every deterministic job out of the agent and into the harness. See
[docs/LESSONS.md](docs/LESSONS.md).

**The agent only does judgement.** The harness does locking, claiming events, retries, archiving, dedup,
paging, run records and watchdogs.

## Quickstart

```bash
pip install .                    # or: pipx install .
wake init ~/wake && cd ~/wake    # writes wake.toml, prompts/, scripts/
wake doctor --smoke              # checks runner, channels, paths (Muse: credential-free echo test)
wake run hello                   # one scheduled QA job: capture -> agent -> verdict -> report
```

Muse Code: `curl -fsSL https://dev.meta.ai/install.sh | bash`, then `muse login` (device code) or set
`META_API_KEY`.

### Two kinds of job

**Scheduled QA review.** A capture script prints evidence; the agent judges it against a checklist.

```toml
[jobs.hello]
runner = "muse"
mode = "read-only"
prompt = "prompts/hello.md"
capture = "scripts/capture-example.sh"
schedule = "*-*-* 09:00:00"
expect_every_hours = 26        # `wake check` pages if no good run in this window
notify_ok = "info"
```

**Event-driven triage.** A watcher turns log lines into events; the agent gives each a verdict.

```toml
[jobs.triage]
runner = "muse"
mode = "fix"
events = true
prompt = "prompts/triage.md"

[[watch]]
name = "unit-fail"
job = "triage"
source = "journal"
pattern = "Failed with result"
key_regex = "(?P<key>[\\w.@-]+\\.service)"
recheck_cmd = "! systemctl --user --failed --quiet | grep -q ."   # skip the agent if it already healed
recheck_delay = 600
```

You can also push events from anywhere: `echo "disk full on db1" | wake emit triage --sig disk --wake`
(use it behind an ssh forced command to relay alerts from other machines).

### Reporting

```toml
[channels.telegram]
type = "telegram"
token_env = "TELEGRAM_BOT_TOKEN"
chat_id_env = "TELEGRAM_CHAT_ID"

[channels.sms]
type = "twilio"
sid_env = "TWILIO_SID"
token_env = "TWILIO_TOKEN"
from_env = "TWILIO_FROM"
to_env = "TWILIO_TO"

[routing]                      # chat carries everything, SMS only for what must not be missed
info   = ["telegram"]
alert  = ["telegram"]
urgent = ["telegram", "sms"]
```

Secrets are environment variable **names** in config, never values. Outgoing messages are scrubbed of
any configured secret. `alert` and `urgent` are deduped per key (default 6h) and the suppressed count is
reported with the next page.

### Run it for real

```bash
wake systemd                   # writes user units: watcher, per-job timers, 5-minute watchdog
# then run the printed `systemctl --user enable --now ...` commands
```

## Commands

| Command | What it does |
|---|---|
| `wake init [dir]` | starter config, prompts, capture script |
| `wake doctor [--smoke] [--send-test]` | verify runners, channels, paths, mutes |
| `wake run JOB [--dry-run]` | run a job now (`--dry-run` sends no notifications) |
| `wake emit JOB [--wake]` | enqueue an event |
| `wake watch` | follow journald/files and enqueue matching lines |
| `wake check [--drill]` | watchdog: page if the wake chain is broken |
| `wake status` / `wake runs` | queue depth, last runs, run records |
| `wake systemd [--print]` | generate systemd units |
| `wake notify SEVERITY TITLE [TEXT]` | send through your channels |
| `wake prune [--days N] [--dry-run]` | delete old run transcripts and archived events |

## Status and honesty

- 88 tests, no network or credentials needed (a fake agent binary and a local HTTP server).
- The **Muse Code adapter is built from public documentation and community SDK captures** and is tested
  against a fake binary. It has not been verified against a real model run in this repo's CI; the nightly
  workflow runs `doctor --smoke` against the real CLI using its credential-free echo provider to catch
  flag or format drift. Verify `muse exec --help` on your version (it is a 0.x beta that auto-updates).
- Kimi, Claude Code and Codex adapters are **experimental**: argv follows public docs, unverified in CI.
- `read-only` is enforced by runner flags only where the CLI offers them (Muse, Claude Code, Codex). The
  post-run `actions` check is an audit, not enforcement. For real isolation, run the agent in a
  container or sandbox. See [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).
- Linux only for now (fcntl locks, systemd units).

## Docs

[Architecture](docs/ARCHITECTURE.md) · [Runners](docs/RUNNERS.md) · [Lessons learned](docs/LESSONS.md) ·
[Threat model](docs/THREAT-MODEL.md) · [Contributing](CONTRIBUTING.md)

MIT licensed.
