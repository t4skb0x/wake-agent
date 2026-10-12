#!/usr/bin/env python3
import argparse, json, os, time, uuid, pathlib, sys, shutil, mimetypes

BASE = pathlib.Path(os.getenv("RELAY_BASE", "/srv/relay/mailboxes"))
ACL_PATH = pathlib.Path(os.getenv("RELAY_ACL", "/srv/relay/acl.json"))

def load_acl():
    data = json.loads(ACL_PATH.read_text())
    return data.get("principals", {})

def can_send(frm, to):
    principals = load_acl()
    p = principals.get(frm)
    if not p:
        return False
    return to in p.get("can_write_to", [])

def atomic_write(target, data):
    tmp = target.with_suffix(".tmp." + uuid.uuid4().hex[:6])
    tmp.write_text(json.dumps(data, indent=2))
    os.rename(tmp, target)

def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def store_attachments(to, msg_id, attachment_paths):
    if not attachment_paths:
        return []
    attach_dir = BASE / to / "attachments" / msg_id
    attach_dir.mkdir(parents=True, exist_ok=True)
    stored=[]
    for src_path in attachment_paths:
        src = pathlib.Path(src_path)
        if not src.exists():
            print(f"attachment not found: {src}", file=sys.stderr)
            continue
        dest = attach_dir / src.name
        shutil.copy2(src, dest)
        mime, _ = mimetypes.guess_type(str(dest))
        stored.append({
            "filename": src.name,
            "path": str(dest),
            "container_path": f"/mailbox/attachments/{msg_id}/{src.name}",
            "mime": mime or "application/octet-stream",
            "size": dest.stat().st_size,
            "raw": True
        })
        print(f"stored raw attachment {src.name} -> {dest} (no processing)")
    return stored

def send(args):
    frm = args.from_
    to = args.to
    if not can_send(frm, to):
        print(f"ACL DENIED: {frm} -> {to}", file=sys.stderr)
        sys.exit(1)
    msg_id = f"msg_{uuid.uuid4().hex[:8]}"
    thread_id = args.thread or f"thread_{uuid.uuid4().hex[:8]}"
    attachments_meta = store_attachments(to, msg_id, args.attachments or [])
    envelope = {
        "version": 1,
        "id": msg_id,
        "thread_id": thread_id,
        "in_reply_to": args.in_reply_to,
        "from": frm,
        "to": to,
        "created_at": now_iso(),
        "type": args.type,
        "body": args.body,
        "attachments": attachments_meta,
        "attachment_folder": str(BASE / to / "attachments" / msg_id),
        "attachment_folder_container": f"/mailbox/attachments/{msg_id}/",
        "cohort": load_acl().get(frm, {}).get("cohort"),
        "raw_files": True
    }
    inbox = BASE / to / "pending"
    inbox.mkdir(parents=True, exist_ok=True)
    atomic_write(inbox / f"{msg_id}.json", envelope)
    print(f"enqueued {msg_id} -> {to}/pending with {len(attachments_meta)} raw attachments")
    wake_cmd = os.getenv("WAKE_AGENT_CMD", "wake-agent")
    os.system(f"{wake_cmd} --agent {to} --message {msg_id} &")

def reply(args):
    orig = None
    for mailbox in BASE.iterdir():
        for status in ["pending","processing","done"]:
            p = mailbox / status / f"{args.message}.json"
            if p.exists():
                orig = json.loads(p.read_text())
                break
    if not orig:
        print(f"original {args.message} not found", file=sys.stderr)
        sys.exit(1)
    target = orig["from"] if args.to is None else args.to
    if not can_send(args.from_, target):
        print(f"ACL DENIED reply: {args.from_} -> {target}", file=sys.stderr)
        sys.exit(1)
    msg_id = f"msg_{uuid.uuid4().hex[:8]}"
    attachments_meta = store_attachments(target, msg_id, args.attachments or [])
    envelope = {
        "version": 1,
        "id": msg_id,
        "thread_id": orig["thread_id"],
        "in_reply_to": orig["id"],
        "from": args.from_,
        "to": target,
        "created_at": now_iso(),
        "type": "reply",
        "body": args.body,
        "attachments": attachments_meta,
        "attachment_folder": str(BASE / target / "attachments" / msg_id),
        "attachment_folder_container": f"/mailbox/attachments/{msg_id}/",
        "cohort": load_acl().get(args.from_, {}).get("cohort"),
        "raw_files": True
    }
    inbox = BASE / target / "pending"
    inbox.mkdir(parents=True, exist_ok=True)
    atomic_write(inbox / f"{msg_id}.json", envelope)
    print(f"enqueued reply {msg_id} -> {target}/pending with {len(attachments_meta)} raw")
    wake_cmd = os.getenv("WAKE_AGENT_CMD", "wake-agent")
    os.system(f"{wake_cmd} --agent {target} --message {msg_id} &")

def list_cmd(args):
    box = BASE / args.mailbox / args.status
    if not box.exists():
        print("empty")
        return
    for f in sorted(box.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[: args.limit]:
        data = json.loads(f.read_text())
        att_info = f" + {len(data.get('attachments',[]))} raw" if data.get('attachments') else ""
        print(f"{data['id']} | {data['from']} -> {data['to']} | {data['body'][:60]}{att_info}")

def main():
    p = argparse.ArgumentParser(prog="relay")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--from", dest="from_", default=os.getenv("RELAY_FROM", "human-ameet"))
    s.add_argument("--to", required=True)
    s.add_argument("--body", required=True)
    s.add_argument("--thread", default=None)
    s.add_argument("--in-reply-to", default=None)
    s.add_argument("--type", default="request")
    s.add_argument("--attachments", nargs="*", default=[])
    s.set_defaults(func=send)
    r = sub.add_parser("reply")
    r.add_argument("--message", required=True)
    r.add_argument("--from", dest="from_", default=os.getenv("RELAY_FROM", "human-ameet"))
    r.add_argument("--to", default=None)
    r.add_argument("--body", required=True)
    r.add_argument("--attachments", nargs="*", default=[])
    r.set_defaults(func=reply)
    l = sub.add_parser("list")
    l.add_argument("--mailbox", required=True)
    l.add_argument("--status", default="pending", choices=["pending","processing","done","failed"])
    l.add_argument("--limit", type=int, default=20)
    l.set_defaults(func=list_cmd)
    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
