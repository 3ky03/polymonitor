from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "labor-services-inflation-monitor"
ROUTE = "/runtime/macro/labor-services-inflation-monitor"
DEFAULT_LIMIT = 36
MIN_LIMIT = 8
MAX_LIMIT = 60


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.macro.labor_services_inflation_monitor_snapshot(limit=limit)
