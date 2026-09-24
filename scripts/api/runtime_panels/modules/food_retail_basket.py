from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "food-retail-basket-pressure"
ROUTE = "/runtime/macro/food-retail-basket"
DEFAULT_LIMIT = 8
MIN_LIMIT = 3
MAX_LIMIT = 10


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.macro.food_retail_basket_snapshot(limit=limit)
