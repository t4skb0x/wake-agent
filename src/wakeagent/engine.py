"""The wake engine: lock -> claim events -> capture evidence -> run agent -> validate -> log -> report.

The harness owns everything deterministic (locking, claiming, archiving, retries, run records,
dedup, paging). The agent only does judgement and writes result.json.
"""
from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import prompt as prompt_mod
from . import result as result_mod
from . import runners
from .config import Config, Job
from .reporter import RANK, Reporter
from .runs import Runs
from .spool import Spool
from .util import append_jsonl, file_lock, now_iso, stamp


@dataclass
class Outcome:
    status: str          # ok|fixed|problem|operator|failed|idle|busy
    run_id: str = ""
    error: str = ""
    summary: str = ""

    @property
    def exit_code(self) -> int:
        return 1 if self.status == "failed" else 0


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.spool = Spool(cfg.data / "spool")
        self.runs = Runs(cfg.data)
        self.reporter = Reporter(cfg)

    # ------------------------------------------------------------------ public
    def run_job(self, name: str, trigger: str = "manual", dry_run: bool = False) -> Outcome:
        job = self.cfg.jobs.get(name)
        if job is None:
            raise KeyError(f"unknown job {name!r} (known: {', '.join(sorted(self.cfg.jobs)) or 'none'})")
        lock = self.cfg.data / "locks" / f"{name}.lock"
        with file_lock(lock, blocking=False) as held:
            if not held:
                # Another run owns the job. Events stay pending; its drain loop picks them up.
                return Outcome("busy")
            self.spool.recover_orphans(job.timeout + job.capture_timeout + 300, job.max_attempts)
            last = Outcome("idle")
            loops = max(1, job.max_drain) if job.events else 1
            for i in range(loops):
                run_id = f"{stamp()}-{name}-{uuid.uuid4().hex[:6]}"
                events = self.spool.claim(name, run_id, job.max_events or None) if job.events else []
                if job.events and not events:
                    break
                try:
                    last = self._run_once(job, run_id, trigger, events, dry_run)
                except Exception as e:  # last-resort net: never strand claimed events or fail silently
                    last = self._fail(job, run_id, trigger, events, now_iso(), time.time(),
                                      f"internal error: {type(e).__name__}: {e}", None, dry_run)
                if last.status == "failed":
                    break  # never hot-loop on a failing agent
            return last

    # ----------------------------------------------------------------- internals
    def _withheld_env(self, job: Job) -> list[str]:
        """Channel credentials never reach the agent: only the harness reports. A job can opt a
        variable back in with options.pass_env = ["NAME", ...] if its task genuinely needs it."""
        keep = set(job.options.get("pass_env", []))
        names = {v for c in self.cfg.channels.values() for k, v in c.items() if k.endswith("_env")}
        return sorted(names - keep)

    def _capture(self, job: Job, run_dir: Path) -> tuple[Path | None, str]:
        if not job.capture:
            return None, ""
        ev = run_dir / "evidence.txt"
        cmd = [str(job.capture)] if job.capture.exists() and job.capture.stat().st_mode & 0o111 \
            else ["bash", str(job.capture)]
        try:
            with open(ev, "w") as f:
                r = subprocess.run(cmd, cwd=job.workdir, stdout=f, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, timeout=job.capture_timeout)
            return ev, f"capture rc={r.returncode}"
        except subprocess.TimeoutExpired:
            with open(ev, "a") as f:
                f.write(f"\nMISSING: capture timed out after {job.capture_timeout}s\n")
            return ev, "capture TIMED OUT (evidence may be partial)"
        except OSError as e:
            ev.write_text(f"MISSING: capture failed to start: {e}\n")
            return ev, "capture FAILED to start"

    def _run_once(self, job: Job, run_id: str, trigger: str, events: list[dict[str, Any]],
                  dry_run: bool) -> Outcome:
        t0 = time.time()
        started = now_iso()
        run_dir = self.cfg.data / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        result_path = run_dir / "result.json"
        evidence, cap_note = self._capture(job, run_dir)

        try:
            text = prompt_mod.build(job, run_dir, result_path, events, evidence, cap_note)
        except OSError as e:
            return self._fail(job, run_id, trigger, events, started, t0, f"cannot build prompt: {e}",
                              None, dry_run)
        (run_dir / "prompt.md").write_text(text)

        runner = runners.build(job.runner, self.cfg.runners[job.runner])
        req = runners.RunRequest(
            prompt=text, workdir=job.workdir, timeout=job.timeout, mode=job.mode, run_dir=run_dir,
            env={"WAKE_RESULT_PATH": str(result_path), "WAKE_RUN_DIR": str(run_dir), "WAKE_JOB": job.name,
                 "WAKE_EVIDENCE": str(evidence or "")},
            options=job.options, drop_env=self._withheld_env(job))
        rr = runner.run(req)

        error = ""
        if rr.timed_out:
            error = f"agent timed out after {job.timeout}s"
        elif rr.rc == 127:
            error = f"agent binary for runner '{job.runner}' not found; run `wake doctor`"
        elif rr.rc != 0:
            error = f"agent exited rc={rr.rc}" + (f" ({rr.note})" if rr.note else "")
        res: result_mod.Result | None = None
        if not error:
            try:
                res = result_mod.load(result_path, [str(e["id"]) for e in events] or None)
            except result_mod.ResultError as e:
                error = str(e)
        if error or res is None:
            return self._fail(job, run_id, trigger, events, started, t0, error or "no result", rr, dry_run)

        # Post-hoc audit. This is NOT enforcement; real isolation is the runner's sandbox/mode.
        audit = ""
        if job.mode == "read-only" and res.actions:
            audit = "audit: agent reported state-changing actions in a read-only job"
        elif job.max_actions is not None and len(res.actions) > job.max_actions:
            audit = f"audit: {len(res.actions)} actions exceed max_actions={job.max_actions}"
        if audit:
            res.status = "problem"
            res.summary += f" [{audit}]"
            res.notify = max(res.notify, "alert", key=lambda s: RANK[s])

        self._write_log(job, run_id, res, rr)
        self.spool.finish(run_id)

        notify = res.notify
        if res.status == "ok" and RANK[job.notify_ok] > RANK[notify]:
            notify = job.notify_ok
        deliveries = self.reporter.notify(
            notify, f"{job.name}: {res.status}", self._message(res, events, run_dir),
            dedup_key=f"{job.name}:{res.dedup_key or res.status}", dry_run=dry_run) if notify != "none" else []
        self.runs.add(self._record(job, run_id, trigger, events, started, t0, res.status, "", rr,
                                   res.summary, deliveries))
        return Outcome(res.status, run_id, "", res.summary)

    def _fail(self, job: Job, run_id: str, trigger: str, events: list[dict[str, Any]], started: str,
              t0: float, error: str, rr: Any, dry_run: bool) -> Outcome:
        requeued = dead = 0
        if events:
            requeued, dead = self.spool.release(run_id, job.max_attempts)
        tail = ""
        if rr is not None:
            try:
                tail = rr.stderr_path.read_text(errors="replace").strip()[-300:]
            except OSError:
                pass
        msg = f"{error}. events requeued={requeued} dead-lettered={dead}. run dir: {self.cfg.data / 'runs' / run_id}"
        if tail:
            msg += f"\nstderr tail: {tail}"
        sev = "urgent" if dead else "alert"
        key = f"dead-letter:{job.name}" if dead else f"run-failed:{job.name}"
        deliveries = self.reporter.notify(sev, f"{job.name}: wake FAILED", msg, dedup_key=key, dry_run=dry_run)
        self.runs.add(self._record(job, run_id, trigger, events, started, t0, "failed", error, rr, error,
                                   deliveries))
        return Outcome("failed", run_id, error, error)

    def _message(self, res: result_mod.Result, events: list[dict[str, Any]], run_dir: Path) -> str:
        lines = [res.summary]
        for v in res.verdicts[:8]:
            lines.append(f"- {v.get('verdict', '?')}: {str(v.get('reason', ''))[:160]}")
        if len(res.verdicts) > 8:
            lines.append(f"…and {len(res.verdicts) - 8} more verdicts")
        if res.actions:
            lines.append(f"actions: {len(res.actions)}")
        lines.append(f"run: {run_dir.name}")
        return "\n".join(lines)

    def _write_log(self, job: Job, run_id: str, res: result_mod.Result, rr: Any) -> None:
        logs = self.cfg.data / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / f"{job.name}.md", "a") as f:
            f.write(f"- {now_iso()} [{run_id}] {res.status.upper()} ({rr.version or 'unknown version'}, "
                    f"{rr.duration:.0f}s): {res.summary}\n")
            for v in res.verdicts:
                f.write(f"  - {v.get('event_id', '-')} {v.get('verdict', '?')}: {v.get('reason', '')}\n")
            for a in res.actions:
                f.write(f"  - action: {a}\n")
        for v in res.verdicts:
            append_jsonl(logs / "verdicts.jsonl", {"ts": now_iso(), "job": job.name, "run_id": run_id, **v})

    def _record(self, job: Job, run_id: str, trigger: str, events: list[dict[str, Any]], started: str,
                t0: float, status: str, error: str, rr: Any, summary: str, deliveries: list[Any]) -> dict[str, Any]:
        return {
            "run_id": run_id, "job": job.name, "trigger": trigger, "started": started, "ended": now_iso(),
            "duration": round(time.time() - t0, 1), "runner": job.runner, "mode": job.mode,
            "runner_version": getattr(rr, "version", ""), "rc": getattr(rr, "rc", None),
            "timed_out": getattr(rr, "timed_out", False), "stream_events": getattr(rr, "events", 0),
            "events": len(events), "status": status, "error": error, "summary": summary[:500],
            "notified": [{"channel": d.channel, "ok": d.ok} for d in deliveries],
        }
