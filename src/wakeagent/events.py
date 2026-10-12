
import pathlib, re, json

def match_journal(line, watch_config):
    pattern = watch_config.get("pattern")
    if not pattern:
        return False
    return re.search(pattern, line) is not None

def mailbox_events(base_dir):
    base_dir = pathlib.Path(base_dir)
    events=[]
    for mb in base_dir.iterdir():
        if not mb.is_dir():
            continue
        pending = mb / "pending"
        if not pending.exists():
            continue
        for f in pending.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                events.append({
                    "source": "mailbox",
                    "agent": mb.name,
                    "msg_id": data.get("id"),
                    "from": data.get("from"),
                    "body": data.get("body"),
                    "attachment_folder": data.get("attachment_folder_container"),
                    "attachments": data.get("attachments", []),
                    "file": str(f),
                    "raw_files": True
                })
            except:
                pass
    return events
