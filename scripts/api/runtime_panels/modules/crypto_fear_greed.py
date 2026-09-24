from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "crypto-fear-greed"
ROUTE = "/runtime/finance/crypto-fear-greed"
DEFAULT_LIMIT = 6
MIN_LIMIT = 3
MAX_LIMIT = 12


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.finance.watch_panel_snapshot(PANEL_ID, limit=limit)
