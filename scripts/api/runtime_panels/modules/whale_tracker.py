from __future__ import annotations

from typing import Any, Dict

PANEL_ID = "whale-tracker"
ROUTE = "/runtime/trades/whales"
DEFAULT_LIMIT = 14
MIN_LIMIT = 1
MAX_LIMIT = 40


def get_snapshot(ctx: Dict[str, Any], *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    return ctx["get_whale_trades_snapshot"](limit=limit)
