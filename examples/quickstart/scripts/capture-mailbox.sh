#!/bin/bash
BASE=${RELAY_BASE:-/srv/relay/mailboxes}
echo "=== Mailbox pending ==="
for mb in $BASE/*; do
  [ -d "$mb/pending" ] || continue
  echo "--- $mb ---"
  ls -lt "$mb/pending"/*.json 2>/dev/null | head -5
  LATEST=$(ls -t "$mb/pending"/*.json 2>/dev/null | head -n1)
  if [ -n "$LATEST" ]; then
    cat "$LATEST"
    ATTACH=$(jq -r .attachment_folder_container "$LATEST" 2>/dev/null)
    echo "Attachments folder: $ATTACH"
  fi
done
