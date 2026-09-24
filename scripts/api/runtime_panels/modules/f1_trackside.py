from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "f1-trackside"
ROUTE = "/runtime/sports/f1"
DEFAULT_LIMIT = 10
MIN_LIMIT = 1
MAX_LIMIT = 16


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.sports.f1_panel_snapshot(limit=limit)
