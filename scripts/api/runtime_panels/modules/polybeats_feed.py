from __future__ import annotations

from typing import Any, Dict


PANEL_ID = "polybeats-feed"
ROUTE = "/runtime/signals/polybeats"
DEFAULT_LIMIT = 8
MIN_LIMIT = 1
MAX_LIMIT = 20


def get_snapshot(ctx: Dict[str, Any], *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    return ctx["get_polybeats_snapshot"](limit=limit)
