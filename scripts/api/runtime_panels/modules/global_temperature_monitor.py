from __future__ import annotations

from typing import Any, Dict

from api.runtime_panels.types import RuntimePanelContext

PANEL_ID = "global-temperature-monitor"
ROUTE = "/runtime/weather/temperature-monitor"
DEFAULT_LIMIT = 60
MIN_LIMIT = 8
MAX_LIMIT = 100


def get_snapshot(ctx: RuntimePanelContext, *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    return ctx.get_global_weather_map_snapshot(limit=limit)
