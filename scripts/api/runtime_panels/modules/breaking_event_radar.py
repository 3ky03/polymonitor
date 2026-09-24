from __future__ import annotations

from typing import Any, Dict

from api.runtime_panels.types import RuntimePanelContext


PANEL_ID = "breaking-event-radar"
ROUTE = "/runtime/evidence/breaking-event-radar"
DEFAULT_LIMIT = 12
MIN_LIMIT = 1
MAX_LIMIT = 80


def get_snapshot(ctx: RuntimePanelContext, *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    return ctx.get_breaking_event_radar_snapshot(limit=limit)
