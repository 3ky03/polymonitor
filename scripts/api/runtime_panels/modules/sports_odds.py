from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext


PANEL_ID = "sports-odds"
ROUTE = "/runtime/sports/odds-monitor"
DEFAULT_LIMIT = 8
MIN_LIMIT = 2
MAX_LIMIT = 20


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.sports.sports_odds_snapshot(limit=limit)
