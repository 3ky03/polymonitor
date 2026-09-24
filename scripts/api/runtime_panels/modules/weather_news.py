from __future__ import annotations

from typing import Any, Dict

from api.runtime_panels.types import RuntimePanelContext

PANEL_ID = "weather-news"
ROUTE = "/runtime/weather/news"
DEFAULT_LIMIT = 24
MIN_LIMIT = 6
MAX_LIMIT = 80


def get_snapshot(ctx: RuntimePanelContext, *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    return ctx.get_weather_news_snapshot(limit=limit)
