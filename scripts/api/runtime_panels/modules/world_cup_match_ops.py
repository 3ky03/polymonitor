from __future__ import annotations

from typing import Any, Dict


PANEL_ID = "world-cup-match-ops"
ROUTE = "/runtime/sports/world-cup-match-ops"
DEFAULT_LIMIT = 12
MIN_LIMIT = 1
MAX_LIMIT = 80


def get_snapshot(ctx: Dict[str, Any], *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    return ctx["get_world_cup_match_ops_snapshot"](limit=limit)
