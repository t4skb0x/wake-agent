from __future__ import annotations

from .base import Channel, ChannelError, clip, env, http


class Ntfy(Channel):
    """ntfy.sh (or self-hosted) push notifications."""

    type = "ntfy"
    limit = 3500

    def check(self) -> list[str]:
        return [] if self.cfg.get("topic") else ["missing topic"]

    def send(self, title: str, text: str, severity: str) -> None:
        topic = self.cfg.get("topic")
        if not topic:
            raise ChannelError("ntfy needs topic")
        base = self.cfg.get("server", "https://ntfy.sh").rstrip("/")
        headers = {"Title": title.encode("ascii", "replace").decode(),
                   "Priority": {"urgent": "5", "alert": "4"}.get(severity, "3")}
        token = env(self.cfg, "token", required=False)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        http(f"{base}/{topic}", data=clip(text, self.limit).encode(), headers=headers)
