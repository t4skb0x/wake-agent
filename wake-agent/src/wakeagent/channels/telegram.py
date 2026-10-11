from __future__ import annotations

from .base import Channel, ChannelError, clip, env, post_json


class Telegram(Channel):
    type = "telegram"
    limit = 4000

    def check(self) -> list[str]:
        return _missing(self.cfg, ["token", "chat_id"])

    def send(self, title: str, text: str, severity: str) -> None:
        token, chat = env(self.cfg, "token"), env(self.cfg, "chat_id")
        base = self.cfg.get("api_base", "https://api.telegram.org")
        icon = {"urgent": "🚨", "alert": "⚠️", "info": "ℹ️"}.get(severity, "")
        body = clip(f"{icon} {title}\n\n{text}".strip(), self.limit)
        status, resp = post_json(f"{base}/bot{token}/sendMessage", {"chat_id": chat, "text": body,
                                                                     "disable_web_page_preview": True})
        if '"ok":true' not in resp.replace(" ", ""):
            raise ChannelError(f"telegram rejected message: {resp[:200]}")


def _missing(cfg, keys):
    import os
    out = []
    for k in keys:
        var = cfg.get(f"{k}_env")
        if not var:
            out.append(f"missing {k}_env")
        elif not os.environ.get(var):
            out.append(f"env var {var} not set")
    return out
