from __future__ import annotations

from typing import Any

from .base import Probe, RunRequest, RunResult, Runner
from .claude import Claude
from .codex import Codex
from .kimi import Kimi
from .muse import Muse

REGISTRY: dict[str, type[Runner]] = {r.name: r for r in (Muse, Kimi, Claude, Codex)}


def build(name: str, cfg: dict[str, Any]) -> Runner:
    t = cfg.get("type", name)
    if t not in REGISTRY:
        raise ValueError(f"runners.{name}: unknown type {t!r} (known: {', '.join(sorted(REGISTRY))})")
    return REGISTRY[t](cfg)


__all__ = ["Probe", "RunRequest", "RunResult", "Runner", "REGISTRY", "build"]
