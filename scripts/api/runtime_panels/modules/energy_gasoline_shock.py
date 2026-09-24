from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "energy-gasoline-shock"
ROUTE = "/runtime/macro/energy-gasoline-shock"
DEFAULT_LIMIT = 6
MIN_LIMIT = 3
MAX_LIMIT = 8


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.macro.energy_gasoline_shock_snapshot(limit=limit)
