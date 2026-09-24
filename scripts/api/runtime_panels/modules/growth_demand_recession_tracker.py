from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "growth-demand-recession-tracker"
ROUTE = "/runtime/macro/growth-demand-recession-tracker"
DEFAULT_LIMIT = 8
MIN_LIMIT = 3
MAX_LIMIT = 12


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.macro.growth_demand_recession_tracker_snapshot(limit=limit)
