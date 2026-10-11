from __future__ import annotations

from .base import Channel, clip, env, post_json
from .telegram import _missing


class Discord(Channel):
    """Discord webhook (2000 char message limit)."""

    type = "discord"
    limit = 1900

    def check(self) -> list[str]:
        return _missing(self.cfg, ["webhook"])

    def send(self, title: str, text: str, severity: str) -> None:
        url = env(self.cfg, "webhook")
        icon = {"urgent": "🚨", "alert": "⚠️", "info": "ℹ️"}.get(severity, "")
        post_json(url, {"content": clip(f"{icon} **{title}**\n{text}", self.limit)})
