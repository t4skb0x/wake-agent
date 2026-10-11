"""Severity routing, dedup, rate limiting, secret redaction. One place decides who gets told."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from . import channels as chan
from .config import Config
from .util import JsonStore, append_jsonl, now_iso

RANK = {"none": 0, "info": 1, "alert": 2, "urgent": 3}


@dataclass
class Delivery:
    channel: str
    ok: bool
    error: str = ""


class Reporter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.paged = JsonStore(cfg.data / "state" / "paged.json")
        self.rate = JsonStore(cfg.data / "state" / "rate.json")
        self.log = cfg.data / "logs" / "notify.jsonl"

    # -- secrets -------------------------------------------------------------
    def _secret_values(self) -> list[str]:
        vals: list[str] = []
        for c in self.cfg.channels.values():
            for k, v in c.items():
                if k.endswith("_env") and k != "to_env" and os.environ.get(v):
                    vals.append(os.environ[v])
        return [v for v in vals if len(v) >= 6]

    def redact(self, text: str) -> str:
        for v in self._secret_values():
            text = text.replace(v, "[redacted]")
        return text

    # -- dedup / rate --------------------------------------------------------
    def _dedup(self, key: str) -> tuple[bool, int]:
        window = self.cfg.dedup_hours * 3600
        now = time.time()

        def fn(d: dict[str, Any]) -> tuple[bool, int]:
            e = d.get(key, {"last": 0, "suppressed": 0})
            if now - e["last"] >= window:
                carried = int(e.get("suppressed", 0))
                d[key] = {"last": now, "suppressed": 0}
                return True, carried
            e["suppressed"] = int(e.get("suppressed", 0)) + 1
            d[key] = e
            return False, e["suppressed"]

        return self.paged.update(fn)

    def _rate_ok(self, channel: str) -> bool:
        now = time.time()
        limit = self.cfg.max_per_hour

        def fn(d: dict[str, Any]) -> bool:
            stamps = [t for t in d.get(channel, []) if now - t < 3600]
            if len(stamps) >= limit:
                d[channel] = stamps
                return False
            stamps.append(now)
            d[channel] = stamps
            return True

        return self.rate.update(fn)

    # -- public --------------------------------------------------------------
    def notify(self, severity: str, title: str, text: str, dedup_key: str | None = None,
               dry_run: bool = False) -> list[Delivery]:
        """Send to every channel routed for `severity`. alert/urgent are deduped per key."""
        if severity == "none":
            return []
        if severity not in ("info", "alert", "urgent"):
            raise ValueError(f"bad severity {severity!r}")
        names = self.cfg.routing.get(severity, [])
        title = self.redact(title)
        out: list[Delivery] = []
        deduped: list[str] = []
        if not names:
            out.append(Delivery("-", False, f"no channels routed for {severity}"))
        for name in names:
            # Dedup is per channel: if SMS is down while Telegram works, SMS must retry next time
            # instead of being hidden behind Telegram's successful page.
            stamp, carried, note = None, 0, ""
            if dedup_key and severity in ("alert", "urgent") and not dry_run:
                stamp = f"{severity}:{dedup_key}:{name}"
                fire, n = self._dedup(stamp)
                if not fire:
                    deduped.append(f"{name}({n})")
                    continue
                carried = n
                if n:
                    note = f"\n(+{n} similar suppressed since last page)"
            if dry_run:
                out.append(Delivery(name, True, "dry-run"))
                continue
            if severity != "urgent" and not self._rate_ok(name):
                out.append(Delivery(name, False, "rate limited"))
                d = out[-1]
            else:
                try:
                    chan.build(name, self.cfg.channels[name]).send(title, self.redact(text) + note, severity)
                    out.append(Delivery(name, True))
                    continue
                except Exception as e:  # a failing channel must never break the others
                    out.append(Delivery(name, False, self.redact(str(e))))
                    d = out[-1]
            if stamp and not d.ok:
                # Nobody got this page on this channel: give the window back so it retries.
                self.paged.update(lambda dd, k=stamp, c=carried: dd.__setitem__(k, {"last": 0, "suppressed": c}))
        if deduped:
            self._record(severity, title, [], "deduped on " + ", ".join(deduped), dedup_key)
        if out:
            self._record(severity, title, out, "", dedup_key)
        return out

    def _record(self, severity: str, title: str, out: list[Delivery], note: str, key: str | None) -> None:
        append_jsonl(self.log, {
            "ts": now_iso(), "severity": severity, "title": title[:200], "key": key, "note": note,
            "deliveries": [{"channel": d.channel, "ok": d.ok, "error": d.error} for d in out],
        })
