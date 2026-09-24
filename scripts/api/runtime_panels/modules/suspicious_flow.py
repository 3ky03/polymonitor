from __future__ import annotations

from typing import Any, Dict

PANEL_ID = "suspicious-flow"
ROUTE = "/runtime/trades/suspicious"
DEFAULT_LIMIT = 12
MIN_LIMIT = 1
MAX_LIMIT = 40


def get_snapshot(ctx: Dict[str, Any], *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    return ctx["get_suspicious_trades_snapshot"](limit=limit)
