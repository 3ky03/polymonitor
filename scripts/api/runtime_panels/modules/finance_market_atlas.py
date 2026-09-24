from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "finance-market-atlas"
ROUTE = "/runtime/finance/market-atlas"
DEFAULT_LIMIT = 16
MIN_LIMIT = 4
MAX_LIMIT = 40


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.finance.market_atlas_snapshot(limit=limit)
