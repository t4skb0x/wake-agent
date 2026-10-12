#!/bin/bash
BASE=${RELAY_BASE:-/srv/relay/mailboxes}
for mb in $BASE/*; do [ -d "$mb/pending" ] || continue; ls -lt "$mb/pending"/*.json 2>/dev/null | head -5; done
