from __future__ import annotations

from typing import Any, Dict

from api.services.commodity_equity_transmission_service import get_commodity_equity_transmission_snapshot


PANEL_ID = "commodity-equity-transmission"
ROUTE = "/runtime/finance/commodity-equity-transmission"
DEFAULT_LIMIT = 8
MIN_LIMIT = 1
MAX_LIMIT = 12


def get_snapshot(ctx: Dict[str, Any], *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    return get_commodity_equity_transmission_snapshot(ctx, limit=limit)
