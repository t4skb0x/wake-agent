
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form
import pathlib, json, os, subprocess, shutil
from typing import List

BASE = pathlib.Path(os.getenv("RELAY_BASE", "/srv/relay/mailboxes"))
ACL_PATH = pathlib.Path(os.getenv("RELAY_ACL", "/srv/relay/acl.json"))
app = FastAPI(title="Relay Mailbox API - Raw Attachments")

def load_principals():
    return json.loads(ACL_PATH.read_text()).get("principals", {})

def can_send(frm, to):
    p = load_principals().get(frm, {})
    return to in p.get("can_write_to", [])

@app.post("/messages")
async def post_message(
    to: str = Form(...),
    body: str = Form(...),
    from_: str = Form("human-ameet"),
    thread_id: str = Form(None),
    in_reply_to: str = Form(None),
    files: List[UploadFile] = File(None),
    x_relay_from: str = Header(None)
):
    frm = x_relay_from or from_
    if not can_send(frm, to):
        raise HTTPException(403, f"ACL denied: {frm} -> {to}")
    tmp_dir = pathlib.Path(f"/tmp/relay_upload_{to}")
    tmp_dir.mkdir(exist_ok=True)
    attachment_paths=[]
    if files:
        for uf in files:
            dest = tmp_dir / uf.filename
            with open(dest, "wb") as out:
                shutil.copyfileobj(uf.file, out)
            attachment_paths.append(str(dest))
    cmd = ["python3", "/srv/relay/relay.py", "send", "--from", frm, "--to", to, "--body", body]
    if thread_id:
        cmd += ["--thread", thread_id]
    if in_reply_to:
        cmd += ["--in-reply-to", in_reply_to]
    if attachment_paths:
        cmd += ["--attachments"] + attachment_paths
    env = os.environ.copy()
    env["RELAY_FROM"]=frm
    subprocess.check_call(cmd, env=env)
    for p in attachment_paths:
        try:
            os.unlink(p)
        except:
            pass
    return {"status": "enqueued", "from": frm, "to": to, "raw_attachments": len(attachment_paths), "note": "Files stored raw, zero processing"}

@app.get("/messages")
def list_messages(mailbox: str, status: str = "pending", limit: int = 20, x_relay_from: str = Header(None)):
    requester = x_relay_from or mailbox
    principals = load_principals()
    p = principals.get(requester, {})
    if mailbox not in p.get("can_read_mailboxes", []) and requester != mailbox:
        out=[]
        for mb in BASE.iterdir():
            for st in [status]:
                for f in (mb / st).glob("*.json"):
                    try:
                        d=json.loads(f.read_text())
                        if d.get("from")==requester or d.get("to")==requester:
                            out.append(d)
                    except:
                        pass
        out.sort(key=lambda x: x.get("created_at",""), reverse=True)
        return out[:limit]
    d = BASE / mailbox / status
    if not d.exists():
        return []
    out=[]
    for f in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        out.append(json.loads(f.read_text()))
    return out

@app.post("/xfeed/webhook")
async def xfeed_webhook(
    analyst: str = Form(...),
    post_id: str = Form(...),
    text: str = Form(""),
    to_agents: str = Form(...),
    files: List[UploadFile] = File(None)
):
    agents = [a.strip() for a in to_agents.split(",") if a.strip()]
    tmp_dir = pathlib.Path(f"/tmp/xfeed_{post_id}")
    tmp_dir.mkdir(exist_ok=True)
    attachment_paths=[]
    if files:
        for uf in files:
            dest = tmp_dir / uf.filename
            with open(dest, "wb") as out:
                shutil.copyfileobj(uf.file, out)
            attachment_paths.append(str(dest))
    for agent in agents:
        cmd = ["python3", "/srv/relay/relay.py", "send", "--from", "xfeed", "--to", agent, "--body", f"[{analyst}] {text} (post {post_id}) - chart has levels"]
        if attachment_paths:
            cmd += ["--attachments"] + attachment_paths
        env = os.environ.copy()
        env["RELAY_FROM"]="xfeed"
        subprocess.call(cmd, env=env)
    for p in attachment_paths:
        try:
            os.unlink(p)
        except:
            pass
    try:
        tmp_dir.rmdir()
    except:
        pass
    return {"status": "enqueued", "agents": agents, "raw_charts": len(attachment_paths)}
