from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "defi-token-watch"
ROUTE = "/runtime/finance/defi-token-watch"
DEFAULT_LIMIT = 10
MIN_LIMIT = 3
MAX_LIMIT = 24


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.finance.defi_token_watch_snapshot(limit=limit)
