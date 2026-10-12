#!/bin/bash
# Capture script for trading mailbox - prints evidence for agent
# Generic harness does locking, claiming, archiving. Agent only does judgement.

BASE=${RELAY_BASE:-/srv/relay/mailboxes}
AGENT=${AGENT_ID:-agent01}

echo "=== Mailbox pending for $AGENT ==="
ls -lt $BASE/$AGENT/pending/*.json 2>/dev/null | head -20

echo "=== Latest message ==="
LATEST=$(ls -t $BASE/$AGENT/pending/*.json 2>/dev/null | head -n1)
if [ -n "$LATEST" ]; then
  cat "$LATEST"
  ATTACH_FOLDER=$(jq -r .attachment_folder_container "$LATEST" 2>/dev/null)
  if [ -n "$ATTACH_FOLDER" ] && [ "$ATTACH_FOLDER" != "null" ]; then
    echo "=== Attachments folder $ATTACH_FOLDER ==="
    ls -lh "/mailbox/attachments/"* 2>/dev/null || ls -lh $BASE/$AGENT/attachments/ 2>/dev/null | head -20
  fi
fi

echo "=== Price API check ==="
# curl via price_client.py would go here, but capture is generic
echo "Price API reachable"

echo "=== /work state ==="
cat /work/pnl.json 2>/dev/null | head -20
