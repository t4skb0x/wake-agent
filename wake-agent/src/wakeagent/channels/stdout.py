from __future__ import annotations

import sys

from .base import Channel


class Stdout(Channel):
    """Prints to stderr. Useful for first runs and tests."""

    type = "stdout"

    def send(self, title: str, text: str, severity: str) -> None:
        print(f"[{severity}] {title}\n{text}", file=sys.stderr)
