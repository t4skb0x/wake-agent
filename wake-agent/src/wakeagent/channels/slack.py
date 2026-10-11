from __future__ import annotations

from .base import Channel, clip, env, post_json
from .telegram import _missing


class Slack(Channel):
    """Slack incoming webhook."""

    type = "slack"
    limit = 3500

    def check(self) -> list[str]:
        return _missing(self.cfg, ["webhook"])

    def send(self, title: str, text: str, severity: str) -> None:
        url = env(self.cfg, "webhook")
        icon = {"urgent": ":rotating_light:", "alert": ":warning:", "info": ":information_source:"}.get(severity, "")
        post_json(url, {"text": clip(f"{icon} *{title}*\n{text}", self.limit)})
