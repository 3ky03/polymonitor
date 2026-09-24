from __future__ import annotations

from api.runtime_panels.types import PanelPayload, RuntimePanelContext

PANEL_ID = "cpi-components-pressure-registry"
ROUTE = "/runtime/macro/cpi-components-pressure-registry"
DEFAULT_LIMIT = 48
MIN_LIMIT = 12
MAX_LIMIT = 60


def get_snapshot(
    ctx: RuntimePanelContext,
    *,
    limit: int = DEFAULT_LIMIT,
) -> PanelPayload:
    return ctx.macro.cpi_components_pressure_registry_snapshot(limit=limit)
