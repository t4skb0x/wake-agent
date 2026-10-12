
# Lessons learned

Original:

- CLI that moved its own binary
- Agent that forgot to archive the queue
- Watchdog that could never fire

Fix: move every deterministic job out of agent into harness. Agent only does judgement.

New lesson: Even generic state needs an inbox.

- Generic wake-agent needs mailbox as core, not extension
- PWA doesn't process any files, sends raw files through message, like email/BBS, zero processing, just text and attachment sent raw
- Files never get processed by system only read by agent in natural state like email/discord
- X feed contains raw chart images, levels are on chart that trading agents read
- Wake-agent delivers path to message attachment folder
- Zero system processing: relay uses shutil.copy2 raw, no thumbnail, no OCR

This is deterministic job for harness: claim pending->processing atomically, store raw attachments, build prompt with folder path, exec, move to done/failed, recover stale on boot.

Trading adds: persistent session /work/muse-session-id, reboot-proof linger + Restart=always + /work volume + TG queue + WireGuard, least-privilege ACL prevents poison.
