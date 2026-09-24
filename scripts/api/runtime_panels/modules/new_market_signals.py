from __future__ import annotations

from typing import Any, Dict

PANEL_ID = "new-market-signals"
ROUTE = "/runtime/markets/new-signals"
DEFAULT_LIMIT = 12
MIN_LIMIT = 1
MAX_LIMIT = 50


def get_snapshot(ctx: Dict[str, Any], *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    return ctx["get_new_market_signals_snapshot"](limit=limit)
