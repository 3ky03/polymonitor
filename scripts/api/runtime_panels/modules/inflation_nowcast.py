from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "inflation-nowcast"
ROUTE = "/runtime/macro/inflation-nowcast"
DEFAULT_LIMIT = None
MIN_LIMIT = None
MAX_LIMIT = None


def get_snapshot(ctx: RuntimePanelContext) -> PanelPayload:
    return ctx.macro.inflation_nowcast_snapshot()
