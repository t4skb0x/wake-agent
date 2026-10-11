from __future__ import annotations

from typing import Any

from .base import Channel, ChannelError
from .discord import Discord
from .ntfy import Ntfy
from .slack import Slack
from .stdout import Stdout
from .telegram import Telegram
from .twilio import Twilio

REGISTRY: dict[str, type[Channel]] = {
    c.type: c for c in (Telegram, Slack, Discord, Twilio, Ntfy, Stdout)
}


def build(name: str, cfg: dict[str, Any]) -> Channel:
    t = cfg.get("type")
    if t not in REGISTRY:
        raise ChannelError(f"channels.{name}: unknown type {t!r} (known: {', '.join(sorted(REGISTRY))})")
    return REGISTRY[t](name, cfg)


__all__ = ["Channel", "ChannelError", "REGISTRY", "build"]
