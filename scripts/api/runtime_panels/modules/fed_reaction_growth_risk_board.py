from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "fed-reaction-growth-risk-board"
ROUTE = "/runtime/macro/fed-reaction-growth-risk-board"
DEFAULT_LIMIT = 36
MIN_LIMIT = 8
MAX_LIMIT = 60


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.macro.fed_reaction_growth_risk_board_snapshot(limit=limit)
