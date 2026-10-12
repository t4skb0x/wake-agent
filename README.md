# wake-agent

Wake a local CLI coding agent on a timer, an event, **or a mailbox message**, let it do a job, **log what it did**, and **report back** over Telegram, Slack, Discord, SMS or ntfy.

Supported agents: **Muse Code** (first-class), plus Kimi Code, Claude Code and Codex (experimental).

Pure Python standard library + watchdog. One config file. Runs on any Linux box with systemd (or without it).

```
trigger ──► [capture script] ──► agent (headless) ──► result.json ──► log + report

 timer        evidence file        Muse / claude /       validated       verdict log,
 watcher      body + attachment    codex / kimi          by harness      Telegram/SMS/...
              folder path raw                                        + relay reply
 manual / emit
 mailbox (NEW)
```

## Why this exists

It started as an alert-triage system: a watcher follows the journal, and each alert wakes a headless agent that diagnoses and fixes it. Running that for weeks produced a list of failures (a CLI that moved its own binary, an agent that forgot to archive the queue, a watchdog that could never fire). This project keeps the idea and moves every deterministic job out of the agent and into the harness. See docs/LESSONS.md.

**The agent only does judgement.** The harness does locking, claiming events, retries, archiving, dedup, paging, run records and watchdogs.

**NEW: Mailbox as core - even generic state needs an inbox.** Like email/BBS, zero processing, just text + raw attachments. Files never processed by system only read by agent in natural state like email/discord.

## Quickstart

```
pip install .                    # or: pipx install .
wake init ~/wake && cd ~/wake    # writes wake.toml, prompts/, scripts/
wake doctor --smoke              # checks runner, channels, paths
wake run hello                   # one scheduled QA job: capture -> agent -> verdict -> report
wake watch                       # follows journal/files/mailbox and enqueues
```

Muse Code: `curl -fsSL https://dev.meta.ai/install.sh | bash`, then `muse login` (device code) or set `META_API_KEY`.

### Three kinds of job

**1. Scheduled QA review.** A capture script prints evidence; the agent judges it against a checklist.
```
[jobs.hello]
runner = "Muse"
mode = "read-only"
prompt = "prompts/hello.md"
capture = "scripts/capture-example.sh"
schedule = "*-*-* 09:00:00"
expect_every_hours = 26
notify_ok = "info"
```

**2. Event-driven triage.** A watcher turns log lines into events; the agent gives each a verdict.
```
[jobs.triage]
runner = "Muse"
mode = "fix"
events = true
prompt = "prompts/triage.md"

[[watch]]
name = "unit-fail"
job = "triage"
source = "journal"
pattern = "Failed with result"
key_regex = "(?P<key>[\w.@-]+\.service)"
```

**3. Mailbox inbox (NEW - core).** Generic filesystem inbox, raw attachments, zero system processing. Even generic needs an inbox for human messages, PWA, X feed charts, etc.

```
[jobs.inbox]
runner = "Muse"
mode = "fix"
events = true
prompt = "prompts/mailbox.md"
capture = "scripts/capture-mailbox.sh"

[[watch]]
name = "mailbox-main"
job = "inbox"
source = "mailbox"
base_dir = "/srv/relay/mailboxes"
pattern = "agent01/pending/*.json"
key_regex = "(?P<key>msg_[a-f0-9]+)"
```

Mailbox structure (generic):
```
/srv/relay/mailboxes/
  agent01/
    pending/    <- new messages as JSON
    processing/ <- claimed
    done/       <- succeeded
    failed/     <- failed
    attachments/<msg_id>/ <- raw files, zero processing
```

Envelope:
```json
{
  "id": "msg_7c82",
  "thread_id": "thread_a941",
  "from": "human-ameet",
  "to": "agent01",
  "body": "Check chart levels",
  "attachment_folder": "/srv/relay/mailboxes/agent01/attachments/msg_7c82",
  "attachment_folder_container": "/mailbox/attachments/msg_7c82/",
  "attachments": [{"filename":"chart.png","container_path":"/mailbox/attachments/msg_7c82/chart.png","mime":"image/png","raw":true}]
}
```

Flow: PWA or service writes JSON + raw files -> pending/ -> wake-agent claims pending->processing atomically -> builds prompt with body + attachment folder path -> exec -> done/failed

Zero processing: relay never opens, converts, thumbnails, OCRs. Only agent reads raw like email/discord. X feed chart images contain levels on chart that trading agents read. PWA is email/BBS style, zero processing.

You can push events from anywhere: `echo "disk full" | wake emit triage --sig disk --wake` or via mailbox: `relay send --to agent01 --body "check levels" --attachments chart.png`

### Reporting

```
[channels.telegram]
type = "telegram"
token_env = "TELEGRAM_BOT_TOKEN"
chat_id_env = "TELEGRAM_CHAT_ID"

[routing]
info   = ["telegram"]
alert  = ["telegram"]
urgent = ["telegram", "sms"]
```

Secrets are env var names, never values. Outgoing scrubbed. alert/urgent deduped per key (default 6h).

### Run it for real

```
wake systemd                   # writes user units: watcher, per-job timers, 5-minute watchdog
# then run the printed `systemctl --user enable --now ...` commands
```

## Trading specialization (uses generic mailbox)

For Muse Spark 1.3 trading silos:

- Persistent session: /work/muse-session-id stable UUID, muse exec --session-id $(cat /work/muse-session-id)
- Price API: PRICE_API_URL real-time mTLS, XFEED_API_URL separate machine RO raw chart images, levels on chart
- CSV: CSV_BASE_URL via egress-proxy, TG: tradegate -> WireGuard -> NinjaTrader VPS
- Least-privilege: trainer can_write_to [agent01, agent02] only prevents poison
- Reboot-proof: linger + Restart=always + recover moves stale processing->pending + /work volume + /srv/tradegate/queue.db + wg0
- Raw attachments: X feed charts + PWA pdf/csv, PWA email/BBS zero processing

See examples/trading/wake.toml.trading

## Commands

| Command | What it does |
| --- | --- |
| `wake init [dir]` | starter config, prompts, capture script |
| `wake doctor [--smoke] [--send-test]` | verify runners, channels, paths, mutes |
| `wake run JOB [--dry-run]` | run a job now |
| `wake emit JOB [--wake]` | enqueue an event |
| `wake watch` | follow journald/files/mailbox and enqueue |
| `wake check [--drill]` | watchdog: page if wake chain broken |
| `wake status` / `wake runs` | queue depth, last runs, run records |
| `wake systemd [--print]` | generate systemd units |
| `wake notify SEVERITY TITLE [TEXT]` | send through channels |
| `wake prune [--days N] [--dry-run]` | delete old run transcripts |

## Docs

Architecture · Runners · Lessons learned · Threat model · Contributing

MIT licensed.
