from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "nba-intel"
ROUTE = "/runtime/sports/nba-intel"
DEFAULT_LIMIT = 12
MIN_LIMIT = 1
MAX_LIMIT = 24


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.sports.nba_intel_snapshot(limit=limit)
