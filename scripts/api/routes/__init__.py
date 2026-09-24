"""Route blueprints for the polyData API service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from flask import Flask

from .agent import create_agent_blueprint
from .agent_snapshots import create_agent_snapshot_blueprint
from .analytics import create_analytics_blueprint
from .auth import create_auth_blueprint
from .bootstrap import create_bootstrap_blueprint
from .content import create_content_blueprint
from .data_quality import create_data_quality_blueprint
from .lob import create_lob_blueprint
from .market_groups import create_market_groups_blueprint
from .markets import create_markets_blueprint
from .mcp import create_mcp_blueprint
from .product import create_product_blueprint
from .quant import create_quant_blueprint
from .runtime_panels import create_runtime_panels_blueprint
from .runtime_sports import create_runtime_sports_blueprint
from .schema import create_schema_blueprint
from .system import create_system_blueprint
from .workspace_product import create_workspace_product_blueprint


BlueprintFactory = Callable[[Mapping[str, Any]], Any]
BLUEPRINT_FACTORIES: Final[tuple[BlueprintFactory, ...]] = (
    create_auth_blueprint,
    create_product_blueprint,
    create_workspace_product_blueprint,
    create_agent_blueprint,
    create_agent_snapshot_blueprint,
    create_bootstrap_blueprint,
    create_market_groups_blueprint,
    create_markets_blueprint,
    create_mcp_blueprint,
    create_quant_blueprint,
    create_runtime_panels_blueprint,
    create_runtime_sports_blueprint,
    create_schema_blueprint,
    create_content_blueprint,
    create_data_quality_blueprint,
    create_analytics_blueprint,
    create_system_blueprint,
    create_lob_blueprint,
)


def register_blueprints(app: Flask, context: Mapping[str, Any]) -> None:
    if app.config.get("POLYDATA_BLUEPRINTS_REGISTERED"):
        return
    for factory in BLUEPRINT_FACTORIES:
        app.register_blueprint(factory(context))
    app.config["POLYDATA_BLUEPRINTS_REGISTERED"] = True
