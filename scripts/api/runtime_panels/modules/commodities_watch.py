from __future__ import annotations

from typing import Any, Dict

from api.runtime_panels.types import RuntimePanelContext

PANEL_ID = "commodities-watch"
ROUTE = "/runtime/markets/commodities"
DEFAULT_LIMIT = None
MIN_LIMIT = None
MAX_LIMIT = None


def get_snapshot(ctx: RuntimePanelContext) -> Dict[str, Any]:
    return ctx.get_market_group_snapshot(ctx.commodity_symbols, kind="commodities")
