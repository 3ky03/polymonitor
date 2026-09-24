from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "natural-hazards"
ROUTE = "/runtime/world/natural-hazards"
DEFAULT_LIMIT = 1200
MIN_LIMIT = 1
MAX_LIMIT = 1200


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.world.natural_hazards_snapshot(limit=limit)
