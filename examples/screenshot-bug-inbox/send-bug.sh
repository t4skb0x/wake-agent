#!/bin/bash
# Example sender: hotkey captures screenshot raw, sends to mailbox without processing
# Usage: ./send-bug.sh "login button overlaps on mobile" ~/Pictures/screenshot.png
#
# Safety: JSON is built with python3 (bodies with quotes/newlines can't
# break it) and the envelope is written atomically (temp file + mv), so
# the watcher never reads a partial file. Override the mailbox root with
# WAKE_MAILBOX_BASE (default /home/user/wake/mailboxes).
set -u

BODY="${1:?usage: send-bug.sh \"description\" screenshot.png}"
FILE="${2:?usage: send-bug.sh \"description\" screenshot.png}"
BASE="${WAKE_MAILBOX_BASE:-/home/user/wake/mailboxes}/bugs"
ATTACH_ROOT="$BASE/attachments"
PENDING_DIR="$BASE/pending"

if [ ! -f "$FILE" ]; then
  echo "error: file not found: $FILE" >&2
  exit 1
fi

MSG_ID="msg_$(cat /proc/sys/kernel/random/uuid | cut -c1-8)"
ATTACH_DIR="$ATTACH_ROOT/$MSG_ID"

mkdir -p "$ATTACH_DIR" "$PENDING_DIR"

# Zero processing: raw copy only
cp -- "$FILE" "$ATTACH_DIR/"

FILENAME="$(basename -- "$FILE")"
export MSG_ID BODY ATTACH_DIR PENDING_DIR FILENAME

# Build the envelope with python3 so arbitrary bodies/filenames stay valid
# JSON, then move it into pending/ atomically.
TMP_ENV="$(mktemp "$PENDING_DIR/.tmp-$MSG_ID.XXXXXX.json")"
trap 'rm -f "$TMP_ENV"' EXIT
python3 - "$TMP_ENV" <<'PY'
import json, os, sys
tmp = sys.argv[1]
msg_id = os.environ["MSG_ID"]
envelope = {
    "id": msg_id,
    "thread_id": "bug_thread",
    "from": "qa-user",
    "to": "bugs",
    "body": os.environ["BODY"],
    "attachment_folder": os.environ["ATTACH_DIR"],
    "attachment_folder_container": f"/mailbox/attachments/{msg_id}/",
    "attachments": [{
        "filename": os.environ["FILENAME"],
        "container_path": f"/mailbox/attachments/{msg_id}/{os.environ['FILENAME']}",
        "mime": "image/png",
        "raw": True,
    }],
}
with open(tmp, "w") as fh:
    json.dump(envelope, fh)
    fh.write("\n")
PY

mv -- "$TMP_ENV" "$PENDING_DIR/$MSG_ID.json"
trap - EXIT

echo "Sent $MSG_ID with raw $FILE - wake-agent will wake"
