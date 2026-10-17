
"""
Mailbox watcher - core inbox, generic
Zero processing of attachments: raw files stored via shutil.copy2
Envelope contains attachment_folder + container path + attachments[]
Files never processed by system only read by agent naturally like email/discord
"""
import pathlib, json, os, time, shutil, shlex, sys

from .worker import (
    heartbeat_thread,
    is_alive,
    report_progress,
    resolve_retries,
    resolve_timeout,
    resolve_worker,
    run_with_retries,
    worker_file,
    write_heartbeat,
    atomic_write_text,
)

def load_acl(acl_path):
    if not pathlib.Path(acl_path).exists():
        return {}
    try:
        data = json.loads(pathlib.Path(acl_path).read_text())
        principals = data.get("principals", {})
        return principals if isinstance(principals, dict) else {}
    except Exception:
        return {}

def can_send(acl, frm, to):
    # Fail-closed: a missing, unreadable, or empty ACL denies everything.
    if not acl:
        return False
    p = acl.get(frm)
    if not isinstance(p, dict):
        return False
    return to in (p.get("can_write_to", []) or [])

def build_attachments_list(envelope):
    atts = envelope.get("attachments", [])
    if not atts:
        return "No attachments - text only"
    lines=[]
    for a in atts:
        lines.append(f"- {a.get('filename')} ({a.get('mime')}, {a.get('size')} bytes) raw: {a.get('container_path')} - zero system processing, read naturally")
    return "\n".join(lines)

def build_prompt(envelope, template=None):
    if template is None:
        template = """You have new mailbox message from {from} in thread {thread_id}:
{body}

Attachments folder: {attachment_folder_container}
{attachments_list}

Read docs if present. If attachments, read raw files in natural state like email/discord.
Reply using: relay reply --message {msg_id} --from {agent} --body 'reply'
"""
    attachments_list = build_attachments_list(envelope)
    fmt_kwargs = {
        "attachments_list": attachments_list,
        "attachment_folder": envelope.get("attachment_folder", ""),
        "attachment_folder_container": envelope.get("attachment_folder_container", ""),
        "agent": envelope.get("to", ""),
        "msg_id": envelope.get("id", ""),
        "body": envelope.get("body", ""),
        "thread_id": envelope.get("thread_id", ""),
        "from": envelope.get("from", ""),
        "from_field": envelope.get("from", ""),
        "from_": envelope.get("from", ""),
    }
    for k, v in envelope.items():
        fmt_kwargs.setdefault(k, v)
    return template.format(**fmt_kwargs)

def build_argv(executor_template, fmt_kwargs):
    """Build argv from an executor template WITHOUT a shell.

    The template is tokenized with shlex BEFORE substitution, then each
    token is formatted, so envelope values (body, prompt, paths) always
    land inside a single argv element. Shell metacharacters in message
    content ($( ), backticks, ;, |, quotes) are data, never syntax.
    """
    tokens = shlex.split(executor_template, posix=True)
    if not tokens:
        raise ValueError("empty executor template")
    return [t.format(**fmt_kwargs) for t in tokens]


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
    if not can_send(acl, frm, to):
        print(f"[mailbox] ACL DENIED {frm} -> {to}", file=sys.stderr)
        failed_dir = msg_path.parent.parent / "failed"
        failed_dir.mkdir(exist_ok=True)
        shutil.move(str(msg_path), str(failed_dir / msg_path.name))
        return False

    claimed = claim_message(msg_path)
    if not claimed:
        return False

    worker = resolve_worker(config.get("worker_id"))
    print(f"[mailbox] claimed {envelope.get('id')} {frm}->{to} worker={worker}")

    prompt = build_prompt(envelope, config.get("prompt_template"))
    fmt_kwargs = {
        "agent": to,
        "msg_id": envelope.get("id"),
        "body": envelope.get("body", ""),
        "thread_id": envelope.get("thread_id", ""),
        "attachment_folder": envelope.get("attachment_folder", ""),
        "attachment_folder_container": envelope.get("attachment_folder_container", ""),
        "from": frm,
        "from_field": frm,
        "from_": frm,
        "prompt": prompt,
    }
    for k, v in envelope.items():
        fmt_kwargs.setdefault(k, v)
    try:
        argv = build_argv(executor_template, fmt_kwargs)
    except (KeyError, ValueError) as e:
        print(f"[mailbox] bad executor template: {e}", file=sys.stderr)
        failed_dir = claimed.parent.parent / "failed"
        failed_dir.mkdir(exist_ok=True)
        claimed.rename(failed_dir / claimed.name)
        return False

    atomic_write_text(worker_file(claimed), json.dumps({
        "msg_id": envelope.get("id"),
        "worker": worker,
        "pid": os.getpid(),
        "cmd": " ".join(argv[:6]) + (" ..." if len(argv) > 6 else ""),
        "started_at": int(time.time()),
    }) + "\n")
    write_heartbeat(claimed, envelope.get("id"), worker)

    if dry_run:
        print(f"[DRY-RUN] would exec for {to} worker={worker}: {argv[:6]}")
        pending_dir = claimed.parent.parent / "pending"
        claimed.rename(pending_dir / claimed.name)
        return True

    timeout = resolve_timeout(envelope, config)
    max_retries, backoff_base, backoff_cap = resolve_retries(config)
    report_progress(claimed, f"worker {worker} started (timeout={timeout:g}s)",
                    envelope.get("id"))

    stop, hb = heartbeat_thread(
        claimed, envelope.get("id"), worker,
        url=config.get("heartbeat_url"),
        interval=float(config.get("heartbeat_interval", 60)),
    )
    try:
        res = run_with_retries(argv, timeout=timeout,
                               max_retries=max_retries,
                               backoff_base=backoff_base,
                               backoff_cap=backoff_cap)
    finally:
        stop.set()
        hb.join(timeout=5)

    print(res["output"][-3000:])
    if res["returncode"] != 0:
        reason = "timeout" if res["timed_out"] else f"rc={res['returncode']}"
        print(f"[mailbox] failed {reason} after {res['attempts']} attempt(s) "
              f"{res['output'][-1000:]}", file=sys.stderr)
        report_progress(claimed,
                        f"finished {reason} after {res['attempts']} attempt(s)",
                        envelope.get("id"))
        failed_dir = claimed.parent.parent / "failed"
        failed_dir.mkdir(exist_ok=True)
        claimed.rename(failed_dir / claimed.name)
        return False
    report_progress(claimed,
                    f"finished ok after {res['attempts']} attempt(s)",
                    envelope.get("id"))
    done_dir = claimed.parent.parent / "done"
    done_dir.mkdir(exist_ok=True)
    claimed.rename(done_dir / claimed.name)
    print(f"[mailbox] {envelope.get('id')} -> done worker={worker}")
    return True

def recover_stale(base_dir, stale_after=300):
    for mb in pathlib.Path(base_dir).iterdir():
        if not mb.is_dir():
            continue
        proc_dir = mb / "processing"
        if not proc_dir.exists():
            continue
        for f in proc_dir.glob("*.json"):
            if f.name.endswith((".heartbeat.json", ".worker.json")):
                continue
            if is_alive(f, stale_after=stale_after):
                continue  # fresh worker heartbeat: alive, skip in sweep
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
    executors = config.get("executors", {})

    recover_stale(base_dir, config.get("recover_stale_after", 300))

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
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
        pass

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
