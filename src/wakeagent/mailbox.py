
"""
Mailbox watcher - new source type for trading silos, generic enough for public repo

Watches /srv/relay/mailboxes/*/pending/*.json
- Zero processing of attachments: raw files stored via shutil.copy2, no thumbnail, no OCR
- Envelope contains attachment_folder + attachment_folder_container + attachments[]
- Files never processed by system only read by agent in natural state like email/discord
- X feed contains raw chart images, levels are on chart that trading agents read
- PWA doesn't process any files, sends raw files through message like email/BBS

Generic flow:
  pending/*.json + attachments/<msg_id>/ raw -> claim -> processing -> done/failed

For trading silos:
  - Persistent session: /work/muse-session-id reused
  - Wake-agent delivers prompt with path to attachment folder
  - Agent reads raw chart.png via Read tool vision
  - Least-privilege ACL: trainer only sees trainees prevents poison
"""
import pathlib, json, time, shutil, os, re, sys, subprocess, uuid

BASE_DEFAULT = pathlib.Path("/srv/relay/mailboxes")
ACL_DEFAULT = pathlib.Path("/srv/relay/acl.json")

def load_acl(acl_path):
    if not pathlib.Path(acl_path).exists():
        return {}
    try:
        data = json.loads(pathlib.Path(acl_path).read_text())
        return data.get("principals", {})
    except:
        return {}

def can_send(acl, frm, to):
    if not acl:
        return True
    p = acl.get(frm)
    if not p:
        return False
    return to in p.get("can_write_to", [])

def build_attachments_list(envelope):
    atts = envelope.get("attachments", [])
    if not atts:
        return "No attachments - text only"
    lines=[]
    for a in atts:
        lines.append(f"- {a.get('filename')} ({a.get('mime')}, {a.get('size')} bytes) raw: {a.get('container_path')} - zero system processing, read naturally like email/discord")
    return "\n".join(lines)

def build_prompt(envelope, template=None):
    if template is None:
        template = """You have new mailbox message from {from} in thread {thread_id}:
{body}

Attachments folder: {attachment_folder_container}
{attachments_list}

Read /work/AGENTS.md and /work/docs/*.md
If attachments present, read raw files in natural state:
- Chart images: use Read tool with image path, levels are on chart image that trading agents read (analyst writes line or two but levels are on chart)
- PDFs/CSVs: read raw file, do not rely on system processing
Files never get processed by system only read by you in natural state like email/discord.
PWA is email/BBS style, zero processing, sends raw files through message.
Do work per docs, update /work/strategy.py if training, log to /work/training.log
Reply using: relay reply --message {msg_id} --from {agent} --body 'reply' [--attachments raw_files...]
Session ID is persistent and must be preserved.
"""
    attachments_list = build_attachments_list(envelope)
    return template.format(
        **envelope,
        attachments_list=attachments_list,
        attachment_folder=envelope.get("attachment_folder",""),
        attachment_folder_container=envelope.get("attachment_folder_container",""),
        agent=envelope.get("to",""),
        msg_id=envelope.get("id",""),
        body=envelope.get("body",""),
        thread_id=envelope.get("thread_id",""),
        from_field=envelope.get("from",""),
        from_=envelope.get("from","")
    )

def claim_message(msg_path):
    processing_dir = msg_path.parent.parent / "processing"
    processing_dir.mkdir(parents=True, exist_ok=True)
    processing_path = processing_dir / msg_path.name
    try:
        msg_path.rename(processing_path)
        return processing_path
    except FileNotFoundError:
        return None

def process_one(msg_path, config, acl, executor_template, dry_run=False):
    try:
        envelope = json.loads(msg_path.read_text())
    except Exception as e:
        print(f"[mailbox] failed to parse {msg_path}: {e}", file=sys.stderr)
        return False

    frm = envelope.get("from")
    to = envelope.get("to")
    if acl and not can_send(acl, frm, to):
        print(f"[mailbox] ACL DENIED {frm} -> {to}", file=sys.stderr)
        failed_dir = msg_path.parent.parent / "failed"
        failed_dir.mkdir(exist_ok=True)
        shutil.move(str(msg_path), str(failed_dir / msg_path.name))
        return False

    claimed = claim_message(msg_path)
    if not claimed:
        return False

    print(f"[mailbox] claimed {envelope.get('id')} {frm}->{to}")

    prompt = build_prompt(envelope, config.get("prompt_template"))

    # Escape for bash
    safe_prompt = prompt.replace("'", "'"'"'")
    cmd = executor_template.format(
        agent=to,
        from_=frm,
        msg_id=envelope.get("id"),
        body=envelope.get("body",""),
        thread_id=envelope.get("thread_id",""),
        attachment_folder=envelope.get("attachment_folder",""),
        attachment_folder_container=envelope.get("attachment_folder_container",""),
        prompt=safe_prompt
    )

    if dry_run:
        print(f"[DRY-RUN] would exec for {to}: {cmd[:500]}")
        pending_dir = claimed.parent.parent / "pending"
        claimed.rename(pending_dir / claimed.name)
        return True

    try:
        result = subprocess.run(cmd, shell=True, timeout=600, capture_output=True, text=True)
        print(result.stdout[-3000:])
        if result.returncode != 0:
            print(f"[mailbox] executor failed rc={result.returncode} {result.stderr[-1000:]}", file=sys.stderr)
            failed_dir = claimed.parent.parent / "failed"
            failed_dir.mkdir(exist_ok=True)
            claimed.rename(failed_dir / claimed.name)
            return False
        else:
            done_dir = claimed.parent.parent / "done"
            done_dir.mkdir(exist_ok=True)
            claimed.rename(done_dir / claimed.name)
            print(f"[mailbox] {envelope.get('id')} -> done")
            return True
    except subprocess.TimeoutExpired:
        print(f"[mailbox] timeout {envelope.get('id')} -> failed", file=sys.stderr)
        failed_dir = claimed.parent.parent / "failed"
        failed_dir.mkdir(exist_ok=True)
        claimed.rename(failed_dir / claimed.name)
        return False

def recover_stale(base_dir, stale_after=300):
    print(f"[mailbox] recover stale > {stale_after}s")
    for mb in pathlib.Path(base_dir).iterdir():
        if not mb.is_dir():
            continue
        proc_dir = mb / "processing"
        if not proc_dir.exists():
            continue
        for f in proc_dir.glob("*.json"):
            age = time.time() - f.stat().st_mtime
            if age > stale_after:
                print(f"[recover] stale {f} age {age:.0f}s -> pending")
                pending_dir = mb / "pending"
                pending_dir.mkdir(exist_ok=True)
                try:
                    f.rename(pending_dir / f.name)
                except Exception as e:
                    print(f"[recover] failed {e}", file=sys.stderr)

def watch_loop(base_dir, config, acl, poll_interval=2, dry_run=False):
    base_dir = pathlib.Path(base_dir)
    executor_template = config.get("executors", {}).get("default") or "echo '{prompt}'"
    # trading override: per-agent executors
    executors = config.get("executors", {})

    recover_stale(base_dir, config.get("recover_stale_after", 300))

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        print("[mailbox] using watchdog inotify")
        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                if not event.src_path.endswith(".json"):
                    return
                if "/pending/" not in event.src_path:
                    return
                p = pathlib.Path(event.src_path)
                time.sleep(0.2)
                if p.exists():
                    # pick executor for this agent
                    try:
                        env = json.loads(p.read_text())
                        to = env.get("to")
                        tmpl = executors.get(to) or executor_template
                        process_one(p, config, acl, tmpl, dry_run)
                    except:
                        process_one(p, config, acl, executor_template, dry_run)

        observer = Observer()
        for mb in base_dir.iterdir():
            if mb.is_dir():
                pending = mb / "pending"
                pending.mkdir(exist_ok=True)
                observer.schedule(Handler(), str(pending), recursive=False)
        observer.start()
        try:
            while True:
                time.sleep(poll_interval)
                for mb in base_dir.iterdir():
                    if not mb.is_dir():
                        continue
                    pending = mb / "pending"
                    if not pending.exists():
                        continue
                    for f in pending.glob("*.json"):
                        try:
                            env = json.loads(f.read_text())
                            to = env.get("to")
                            tmpl = executors.get(to) or executor_template
                            process_one(f, config, acl, tmpl, dry_run)
                        except:
                            process_one(f, config, acl, executor_template, dry_run)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
        return
    except ImportError:
        print("[mailbox] watchdog not available, poll loop")

    while True:
        for mb in base_dir.iterdir():
            if not mb.is_dir():
                continue
            pending = mb / "pending"
            if not pending.exists():
                continue
            for f in pending.glob("*.json"):
                try:
                    env = json.loads(f.read_text())
                    to = env.get("to")
                    tmpl = executors.get(to) or executor_template
                    process_one(f, config, acl, tmpl, dry_run)
                except:
                    process_one(f, config, acl, executor_template, dry_run)
        time.sleep(poll_interval)
