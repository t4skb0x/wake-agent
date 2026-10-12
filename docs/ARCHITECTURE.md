# Architecture

timer + journal watcher + file watcher + mailbox watcher -> [capture script body + attachment folder path raw] -> agent -> result.json -> log + report

Mailbox watcher core: pending/*.json + attachments/<msg_id>/ raw, atomic claim, zero processing, recover stale
