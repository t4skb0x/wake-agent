# Lessons learned

- CLI moved its own binary, agent forgot to archive queue, watchdog never fired
- Fix: move deterministic jobs to harness, agent only does judgement
- Mailbox is now core: even generic needs inbox, zero processing, raw files, like email/BBS
