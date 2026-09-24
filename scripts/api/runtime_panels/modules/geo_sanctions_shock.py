from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "geo-sanctions-shock"
ROUTE = "/runtime/world/geo-sanctions-shock"
DEFAULT_LIMIT = 2000
MIN_LIMIT = 1
MAX_LIMIT = 2000


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.world.geo_sanctions_shock_snapshot(limit=limit)
