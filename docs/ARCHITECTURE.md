
# Architecture - Mailbox is core

```
timer ──┐
journal watcher ─┼─► [capture script] ──► agent (headless) ──► result.json ──► log + report
file watcher ──┤   evidence file        Muse / claude /       validated
mailbox watcher ─┘   body + attachment    codex / kimi          by harness
                     folder path raw
```

Mailbox watcher:

- Watches /srv/relay/mailboxes/*/pending/*.json via inotify (watchdog) + poll fallback
- pending/*.json + attachments/<msg_id>/ raw files, zero processing
- Atomically moves pending->processing, reads envelope, builds prompt, execs, moves to done/failed
- Recovers stale processing->pending after 300s (crash during exec)
- ACL optional: principals can_write_to prevents poison
- For trading: executor template uses persistent session /work/muse-session-id, muse exec --session-id
- For generic: default executor echo or configurable

PWA flow: multipart raw files -> relay stores raw -> pending/ -> wake-agent -> prompt with folder path -> agent reads naturally
X feed flow: separate machine RO, qualifies post, downloads chart.png raw -> webhook -> same path

Reboot-proof: quadlet Volume=%i-state:/work:Z + /srv/relay/mailboxes/%i:/mailbox:Z, linger, Restart=always, recover.sh, /work persists, TG queue replays, wg0 enabled
