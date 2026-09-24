from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "stablecoin-monitor"
ROUTE = "/runtime/finance/stablecoin-monitor"
DEFAULT_LIMIT = 8
MIN_LIMIT = 3
MAX_LIMIT = 16


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.finance.watch_panel_snapshot(PANEL_ID, limit=limit)
