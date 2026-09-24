from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext


PANEL_ID = "esports-intel"
ROUTE = "/runtime/esports/grid-intel"
DEFAULT_LIMIT = 10
MIN_LIMIT = 2
MAX_LIMIT = 20


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.sports.grid_esports_snapshot(limit=limit)
