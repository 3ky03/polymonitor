from __future__ import annotations

from typing import Any, Dict

from api.runtime_panels.types import RuntimePanelContext


PANEL_ID = "market-youtube-channels"
ROUTE = "/runtime/content/market-youtube-channels"
DEFAULT_LIMIT = 24
MIN_LIMIT = 1
MAX_LIMIT = 80


def get_snapshot(ctx: RuntimePanelContext, *, limit: int = DEFAULT_LIMIT, category: str | None = None) -> Dict[str, Any]:
    return ctx.get_market_youtube_channels_snapshot(limit=limit, category=category)
