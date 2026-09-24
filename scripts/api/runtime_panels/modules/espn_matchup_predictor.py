from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "espn-matchup-predictor"
ROUTE = "/runtime/sports/nba-matchup-predictor"
DEFAULT_LIMIT = 8
MIN_LIMIT = 1
MAX_LIMIT = 16


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.sports.nba_matchup_predictor_snapshot(limit=limit)
