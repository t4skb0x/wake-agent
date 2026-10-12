
# Screenshot Bug Inbox - popular real-world example using generic mailbox core

Problem: Users / QA press a hotkey to report bugs. They send a screenshot + short note. You want an agent to triage it.

Solution: Generic mailbox core.

Flow:
1. Hotkey script or mobile PWA captures screenshot.png raw + description
2. Writes to /home/user/wake/mailboxes/bugs/pending/msg_xxx.json + attachments/msg_xxx/screenshot.png raw, zero processing
3. wake-agent watches, claims pending->processing, builds prompt with body + attachment folder path
4. Agent reads screenshot with vision, reproduces issue, opens GitHub issue, replies

Zero system processing: screenshot is stored raw via shutil.copy2, only agent reads it naturally like email/discord.

No trading, just generic bug reporting.

See wake.toml.bug-inbox
