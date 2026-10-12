
# Threat model

- read-only enforced by runner flags only where CLI offers them (Muse, Claude Code, Codex). Post-run actions check is audit, not enforcement. For real isolation, run agent in container or sandbox.
- Mailbox: ACL prevents poison - trainer only sees trainees, cannot write to all silos
- Secrets are env var names, never values, outgoing scrubbed
- Raw attachments: zero system processing, no thumbnail, no OCR, no conversion, only agent reads. Prevents system-side parsing exploits.
- Locking via fcntl, atomic rename pending->processing
