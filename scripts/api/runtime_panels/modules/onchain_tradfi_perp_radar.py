from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "onchain-tradfi-perp-radar"
ROUTE = "/runtime/finance/onchain-tradfi-perp-radar"
DEFAULT_LIMIT = 12
MIN_LIMIT = 4
MAX_LIMIT = 24


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.finance.onchain_tradfi_perp_radar_snapshot(limit=limit)
