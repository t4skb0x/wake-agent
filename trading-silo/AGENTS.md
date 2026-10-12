
# Trading Agent - Persistent Session + Mailbox Core + Raw Attachments

You are persistent trading agent in isolated silo. One session sustained through compactions and wakes. Mailbox is core.

Session: /work/muse-session-id stable UUID, muse exec --session-id $(cat /work/muse-session-id)
Docs: /work/AGENTS.md, docs/*.md, docs/attachments.md

Data:
- Price: clients/price_client.py -> PRICE_API_URL real-time mTLS
- X feed: clients/xfeed_client.py -> XFEED_API_URL separate machine RO, wakes on qualified post, contains raw chart images, levels are on chart
- CSV: clients/csv_client.py -> CSV_BASE_URL via egress-proxy
- TG: clients/tradegate_client.py -> TRADEGATE_URL -> WireGuard -> NinjaTrader VPS

Mailbox - now core, even generic needs inbox:
- /mailbox/pending/ + /mailbox/attachments/<msg_id>/ raw files, zero processing, like email/BBS
- PWA doesn't process any files, sends raw files through message, zero processing, just text and attachment sent raw
- Files never processed by system only read by you in natural state like email/discord
- Wake-agent delivers prompt with path to message attachment folder
- Read with Read tool vision for chart images (levels on chart), pdf/csv raw

Workflow each wake same session: read AGENTS.md, check mailbox + attachment folder path in prompt, read raw charts for levels, check price/xfeed, update strategy if training, place orders, persist state, reply via relay.
