from __future__ import annotations

import base64
import urllib.parse

from .base import Channel, clip, env, http
from .telegram import _missing


class Twilio(Channel):
    """SMS via Twilio's REST API. Keep SMS volume low: route only `urgent` here."""

    type = "twilio"
    limit = 320

    def check(self) -> list[str]:
        return _missing(self.cfg, ["sid", "token", "from", "to"])

    def send(self, title: str, text: str, severity: str) -> None:
        sid, token = env(self.cfg, "sid"), env(self.cfg, "token")
        frm, to = env(self.cfg, "from"), env(self.cfg, "to")
        base = self.cfg.get("api_base", "https://api.twilio.com")
        body = clip(f"{title}: {text}".replace("\n", " "), self.limit)
        data = urllib.parse.urlencode({"From": frm, "To": to, "Body": body}).encode()
        auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
        http(f"{base}/2010-04-01/Accounts/{sid}/Messages.json", data=data,
             headers={"Authorization": f"Basic {auth}",
                      "Content-Type": "application/x-www-form-urlencoded"})
