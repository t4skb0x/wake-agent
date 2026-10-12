# wake-agent

Wake a local CLI coding agent on a timer, an event, **or a mailbox message**, let it do a job, **log what it did**, and **report back** over Telegram, Slack, Discord, SMS or ntfy.

Supported agents: **Muse Code** (first-class), plus Kimi Code, Claude Code and Codex (experimental).

Pure Python standard library + watchdog. One config file. Runs on any Linux box with systemd (or without it).

```
trigger ──► [capture script] ──► agent (headless) ──► result.json ──► log + report

 timer        evidence file        Muse / claude /       validated       verdict log,
 watcher      body + attachment    codex / kimi          by harness      Telegram/SMS
 manual / emit folder path raw
 mailbox
```

## Why this exists

It started as an alert-triage system: a watcher follows the journal, and each alert wakes a headless agent that diagnoses and fixes it. Running that for weeks produced a list of failures (a CLI that moved its own binary, an agent that forgot to archive the queue, a watchdog that could never fire). This project keeps the idea and moves every deterministic job out of the agent and into the harness. See docs/LESSONS.md.

**The agent only does judgement.** The harness does locking, claiming events, retries, archiving, dedup, paging, run records and watchdogs.

**Mailbox is now core - even generic state needs an inbox.** Like email/BBS, zero system processing, just text + raw attachments. Files never processed by system only read by agent in natural state.

## Quickstart

```
pip install .                    # or: pipx install .
wake init ~/wake && cd ~/wake    # writes wake.toml, prompts/, scripts/
wake doctor --smoke              # checks runner, channels, paths
wake run hello                   # one scheduled QA job
wake watch                       # follows journal/files/mailbox and enqueues
```

### Three kinds of job

**1. Scheduled QA review.** Capture script prints evidence; agent judges it.
```
[jobs.hello]
runner = "Muse"
mode = "read-only"
prompt = "prompts/hello.md"
capture = "scripts/capture-example.sh"
schedule = "*-*-* 09:00:00"
expect_every_hours = 26
```

**2. Event-driven triage.** Watcher turns log lines into events.
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

**3. Mailbox inbox (core).** Generic filesystem inbox, raw attachments, zero processing.

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
pattern = "*/pending/*.json"
key_regex = "(?P<key>msg_[a-f0-9]+)"
```

Structure:
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
  "from": "human",
  "to": "agent01",
  "body": "Check this file",
  "attachment_folder": "/srv/relay/mailboxes/agent01/attachments/msg_7c82",
  "attachment_folder_container": "/mailbox/attachments/msg_7c82/",
  "attachments": [{"filename":"report.pdf","container_path":"/mailbox/attachments/msg_7c82/report.pdf","mime":"application/pdf","raw":true}]
}
```

Flow: any service writes JSON + raw files -> pending/ -> wake-agent claims pending->processing atomically -> builds prompt with body + attachment folder path -> exec -> done/failed

Zero processing: relay never opens, converts, thumbnails, OCRs. Only agent reads raw like email.

## Popular real-world example: Screenshot bug inbox

You have a support form or a personal hotkey that drops screenshots into an inbox. PWA/mobile app sends raw screenshots + notes without processing.

```
[jobs.bug-inbox]
runner = "Muse"
mode = "fix"
events = true
prompt = "prompts/bug-inbox.md"
capture = "scripts/capture-bug-inbox.sh"

[[watch]]
name = "screenshots"
job = "bug-inbox"
source = "mailbox"
base_dir = "/home/user/wake/mailboxes"
pattern = "bugs/pending/*.json"
```

User presses hotkey, screenshot.png + description "login button overlaps on mobile" -> /mailboxes/bugs/attachments/msg_123/screenshot.png raw -> wake-agent delivers path -> agent reads image with vision, reproduces issue, opens GitHub issue, replies.

See examples/screenshot-bug-inbox/

Other uses: receipts inbox (photos of receipts), family photo inbox, home lab logs, etc.

## Reporting

```
[channels.telegram]
type = "telegram"
token_env = "TELEGRAM_BOT_TOKEN"
chat_id_env = "TELEGRAM_CHAT_ID"

[routing]
info = ["telegram"]
alert = ["telegram"]
urgent = ["telegram", "sms"]
```

Secrets are env var names, never values. Outgoing scrubbed. alert/urgent deduped per key (default 6h).

## Commands

| Command | What it does |
| --- | --- |
| `wake init [dir]` | starter config |
| `wake doctor [--smoke]` | verify runners, channels |
| `wake run JOB [--dry-run]` | run a job now |
| `wake emit JOB [--wake]` | enqueue event |
| `wake watch` | follow journal/files/mailbox |
| `wake check [--drill]` | watchdog |
| `wake status` / `wake runs` | queue depth, last runs |
| `wake systemd [--print]` | generate systemd units |

MIT licensed.
