from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext


PANEL_ID = "global-transport-shipping"
ROUTE = "/runtime/transport/global-shipping"
DEFAULT_LIMIT = 14
MIN_LIMIT = 4
MAX_LIMIT = 40


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.world.global_transport_shipping_snapshot(limit=limit)
