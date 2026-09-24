from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "equity-event-command"
ROUTE = "/runtime/finance/equity-event-command"
DEFAULT_LIMIT = 12
MIN_LIMIT = 4
MAX_LIMIT = 24


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.finance.equity_event_command_snapshot(limit=limit)
