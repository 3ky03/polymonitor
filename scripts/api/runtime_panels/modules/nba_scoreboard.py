from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "nba-scoreboard"
ROUTE = "/runtime/sports/nba"
DEFAULT_LIMIT = 10
MIN_LIMIT = 1
MAX_LIMIT = 20


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.sports.nba_scoreboard_snapshot(limit=limit)
