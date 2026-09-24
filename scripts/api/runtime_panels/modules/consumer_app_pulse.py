from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "consumer-app-pulse"
ROUTE = "/runtime/tech/consumer-app-pulse"
DEFAULT_LIMIT = 40
MIN_LIMIT = 3
MAX_LIMIT = 40


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.technology.panel_snapshot(PANEL_ID, limit=limit)
