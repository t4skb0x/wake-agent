#!/bin/bash
# Example sender: hotkey captures screenshot raw, sends to mailbox without processing
# Usage: ./send-bug.sh "login button overlaps on mobile" ~/Pictures/screenshot.png

BODY="$1"
FILE="$2"
MSG_ID="msg_$(cat /proc/sys/kernel/random/uuid | cut -c1-8)"
BASE="/home/user/wake/mailboxes/bugs"
ATTACH_DIR="$BASE/attachments/$MSG_ID"
PENDING_DIR="$BASE/pending"

mkdir -p "$ATTACH_DIR" "$PENDING_DIR"

# Zero processing: raw copy only
cp "$FILE" "$ATTACH_DIR/"

cat > "$PENDING_DIR/$MSG_ID.json" <<EOF
{
  "id": "$MSG_ID",
  "thread_id": "bug_thread",
  "from": "qa-user",
  "to": "bugs",
  "body": "$BODY",
  "attachment_folder": "$ATTACH_DIR",
  "attachment_folder_container": "/mailbox/attachments/$MSG_ID/",
  "attachments": [{"filename": "$(basename $FILE)", "container_path": "/mailbox/attachments/$MSG_ID/$(basename $FILE)", "mime": "image/png", "raw": true}]
}
EOF

echo "Sent $MSG_ID with raw $FILE - wake-agent will wake"
