"""The result.json contract: the agent writes it, the harness validates it."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUSES = ("ok", "fixed", "problem", "operator")
NOTIFY = ("none", "info", "alert", "urgent")
DEFAULT_NOTIFY = {"ok": "none", "fixed": "info", "problem": "alert", "operator": "alert"}

CONTRACT = """\
RESULT CONTRACT (mandatory). Before you finish, write a JSON file to the path in $WAKE_RESULT_PATH
(also given below) with exactly this shape:

{
  "status": "ok" | "fixed" | "problem" | "operator",
  "summary": "one or two plain sentences: what you found and did",
  "verdicts": [ {"event_id": "<id>", "verdict": "FIXED|OK|OPERATOR|IGNORED", "reason": "one line"} ],
  "notify": "none" | "info" | "alert" | "urgent",
  "actions": ["each state-changing command or edit you performed"],
  "dedup_key": "optional stable key for the problem, so repeats are not re-paged"
}

Meaning: ok = nothing wrong; fixed = you found and repaired a problem; problem = still broken, a human
should know; operator = needs a human decision or access you do not have. Use notify "urgent" only for
things a person must not miss. Every event listed above needs a verdict entry. If you do not write a valid
result file, the run is treated as FAILED and the events are retried. Do not page or message anyone
yourself; the harness reports for you.
"""


class ResultError(Exception):
    pass


@dataclass
class Result:
    status: str
    summary: str
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    notify: str = "none"
    actions: list[str] = field(default_factory=list)
    dedup_key: str | None = None


def load(path: Path, event_ids: list[str] | None = None) -> Result:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise ResultError("agent did not write result.json") from None
    except ValueError as e:
        raise ResultError(f"result.json is not valid JSON: {e}") from None
    if not isinstance(raw, dict):
        raise ResultError("result.json must be a JSON object")
    status = raw.get("status")
    if status not in STATUSES:
        raise ResultError(f"status must be one of {STATUSES}, got {status!r}")
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ResultError("summary must be a non-empty string")
    notify = raw.get("notify", DEFAULT_NOTIFY[status])
    if notify not in NOTIFY:
        raise ResultError(f"notify must be one of {NOTIFY}, got {notify!r}")
    verdicts = raw.get("verdicts", [])
    if not isinstance(verdicts, list) or not all(isinstance(v, dict) for v in verdicts):
        raise ResultError("verdicts must be a list of objects")
    actions = raw.get("actions", [])
    if not isinstance(actions, list):
        raise ResultError("actions must be a list")
    if event_ids:
        seen = {str(v.get("event_id")) for v in verdicts}
        missing = [e for e in event_ids if e not in seen]
        if missing:
            raise ResultError(f"no verdict for event(s): {', '.join(missing[:5])}")
    return Result(status, summary.strip(), verdicts, notify, [str(a) for a in actions],
                  raw.get("dedup_key"))
