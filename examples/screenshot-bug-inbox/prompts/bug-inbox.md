
You have new bug report with screenshot.

- Read capture output for body + attachment folder path
- Read screenshot.png raw via Read tool vision - it contains the bug visually
- Describe issue, severity, component
- Write to /tmp/bugs.log with title + description
- If prompt says to open GitHub issue, do it via gh cli
- Reply via relay: relay reply --message {msg_id} --from {agent} --body 'triaged'
