# Changelog

## 0.1.0

Initial release, extracted from a production alert-triage system and generalized.

- Job engine: lock, claim, capture, run, validate, log, report, archive
- Atomic file-per-event spool with retries and a dead-letter queue
- Runner adapters: Muse Code (first-class), Kimi Code, Claude Code, Codex (experimental)
- Channels: Telegram, Slack, Discord, Twilio SMS, ntfy, stdout
- Watcher with content-based dedup, mutes with expiry, optional recheck-before-wake
- `wake check` watchdog, `wake doctor`, `wake prune`, systemd unit generator
- Hardening: channel secrets withheld from the agent, untrusted text cannot close its prompt fence,
  per-channel report dedup that retries undelivered pages, bounded events per run, private data dir
