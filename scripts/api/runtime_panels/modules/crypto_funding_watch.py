from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "crypto-funding-watch"
ROUTE = "/runtime/crypto/funding-watch"
DEFAULT_LIMIT = 18
MIN_LIMIT = 4
MAX_LIMIT = 40


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.finance.crypto_funding_watch_snapshot(limit=limit)
