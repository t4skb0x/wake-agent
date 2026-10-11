"""Channel interface and a tiny stdlib HTTP helper."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

UA = "wake-agent/0.1 (+https://github.com/)"


class ChannelError(Exception):
    pass


def env(cfg: dict[str, Any], key: str, required: bool = True) -> str:
    """Resolve cfg['<key>_env'] to an environment variable value (secrets never live in config)."""
    var = cfg.get(f"{key}_env")
    if not var:
        if required:
            raise ChannelError(f"config needs {key}_env (name of the env var holding {key})")
        return ""
    val = os.environ.get(var, "")
    if required and not val:
        raise ChannelError(f"env var {var} is not set")
    return val


def http(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
         timeout: float = 15, retries: int = 2, method: str | None = None) -> tuple[int, str]:
    hdrs = {"User-Agent": UA}
    hdrs.update(headers or {})
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code < 500 and e.code != 429:
                raise ChannelError(f"HTTP {e.code}: {body}") from e
            last = ChannelError(f"HTTP {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = ChannelError(f"network error: {e}")
        time.sleep(min(2 ** attempt, 4))
    raise last or ChannelError("request failed")


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, str]:
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    return http(url, data=json.dumps(payload).encode(), headers=h)


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 15].rstrip() + "\n…[truncated]"


class Channel:
    """Subclass and implement send(). `cfg` is the [channels.<name>] table."""

    type = ""
    limit = 4000

    def __init__(self, name: str, cfg: dict[str, Any]):
        self.name = name
        self.cfg = cfg

    def check(self) -> list[str]:
        """Return a list of config problems (empty = ready). Must not hit the network."""
        return []

    def send(self, title: str, text: str, severity: str) -> None:
        raise NotImplementedError
