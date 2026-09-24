from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "tradfi-perp-radar"
ROUTE = "/runtime/finance/tradfi-perp-radar"
DEFAULT_LIMIT = 10
MIN_LIMIT = 3
MAX_LIMIT = 24


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.finance.watch_panel_snapshot(PANEL_ID, limit=limit)
