#!/bin/bash
BASE=${RELAY_BASE:-/home/user/wake/mailboxes}
echo "=== Bug inbox pending ==="
ls -lt $BASE/bugs/pending/*.json 2>/dev/null | head -10
LATEST=$(ls -t $BASE/bugs/pending/*.json 2>/dev/null | head -n1)
if [ -n "$LATEST" ]; then
  echo "--- Latest ---"
  cat "$LATEST"
  ATTACH=$(jq -r .attachment_folder_container "$LATEST" 2>/dev/null)
  echo "Attachments folder: $ATTACH"
  ls -lh $BASE/bugs/attachments/ 2>/dev/null | head -20
fi
