from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "polymarket-macro-map"
ROUTE = "/runtime/macro/polymarket-map"
DEFAULT_LIMIT = 12
MIN_LIMIT = 4
MAX_LIMIT = 20


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.macro.polymarket_macro_map_snapshot(limit=limit)
