from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "jin10-flash"
ROUTE = "/runtime/macro/jin10"
DEFAULT_LIMIT = 24
MIN_LIMIT = 4
MAX_LIMIT = 24


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.macro.jin10_panel_snapshot(limit=limit)
