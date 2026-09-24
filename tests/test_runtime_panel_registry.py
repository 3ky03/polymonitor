from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.routes.runtime_panels import create_runtime_panels_blueprint
from api.runtime_panels import RUNTIME_PANEL_MODULES, get_default_panel_ids


def test_runtime_panel_modules_have_unique_ids_and_routes():
    panel_ids = [panel.panel_id for panel in RUNTIME_PANEL_MODULES]
    routes = [panel.route for panel in RUNTIME_PANEL_MODULES]

    assert len(panel_ids) == len(set(panel_ids))
    assert len(routes) == len(set(routes))
    assert all(route.startswith("/runtime/") for route in routes)


def test_runtime_panel_blueprint_registers_all_routes():
    app = Flask(__name__)
    helpers = {
        "COMMODITY_SYMBOLS": [],
        "CRYPTO_SYMBOLS": [],
        "get_market_group_snapshot": lambda symbols, kind: {"kind": kind, "items": symbols},
        "get_polymarket_macro_map_snapshot": lambda limit=12: {"limit": limit},
        "get_f1_panel_snapshot": lambda limit=10: {"limit": limit},
        "get_geo_sanctions_shock_snapshot": lambda limit=6: {"limit": limit},
        "get_global_transport_shipping_snapshot": lambda limit=14: {"limit": limit},
        "get_global_weather_map_snapshot": lambda limit=34: {"limit": limit},
        "get_grid_esports_snapshot": lambda limit=10: {"limit": limit},
        "get_sports_odds_snapshot": lambda limit=8: {"limit": limit},
        "get_jin10_panel_snapshot": lambda limit=24: {"limit": limit},
        "get_nba_scoreboard_snapshot": lambda limit=10: {"limit": limit},
        "get_nba_intel_snapshot": lambda limit=12: {"limit": limit},
        "get_nba_matchup_predictor_snapshot": lambda limit=8: {"limit": limit},
        "get_inflation_nowcast_snapshot": lambda: {"items": []},
        "get_alpha_signal_snapshot": lambda limit=8: {"limit": limit},
        "get_crypto_funding_watch_snapshot": lambda limit=16: {"limit": limit},
        "get_cpi_release_calendar_snapshot": lambda limit=8: {"limit": limit},
        "get_cpi_release_command_center_snapshot": lambda limit=36: {"limit": limit},
        "get_cpi_components_pressure_registry_snapshot": lambda limit=48: {"limit": limit},
        "get_energy_gasoline_shock_snapshot": lambda limit=6: {"limit": limit},
        "get_fed_reaction_growth_risk_board_snapshot": lambda limit=36: {"limit": limit},
        "get_food_retail_basket_snapshot": lambda limit=8: {"limit": limit},
        "get_goods_tariff_supply_watch_snapshot": lambda limit=36: {"limit": limit},
        "get_supply_tariff_import_watch_snapshot": lambda limit=8: {"limit": limit},
        "get_shelter_rent_oer_pressure_snapshot": lambda limit=8: {"limit": limit},
        "get_labor_wage_services_pressure_snapshot": lambda limit=8: {"limit": limit},
        "get_labor_services_inflation_monitor_snapshot": lambda limit=36: {"limit": limit},
        "get_growth_demand_recession_tracker_snapshot": lambda limit=8: {"limit": limit},
        "get_fed_rates_polymarket_gap_snapshot": lambda limit=8: {"limit": limit},
        "get_new_market_signals_snapshot": lambda limit=12: {"limit": limit},
        "get_whale_trades_snapshot": lambda limit=14: {"limit": limit},
        "get_suspicious_trades_snapshot": lambda limit=12: {"limit": limit},
        "get_weather_news_snapshot": lambda limit=24: {"limit": limit},
    }

    app.register_blueprint(create_runtime_panels_blueprint(helpers))
    registered_routes = {rule.rule for rule in app.url_map.iter_rules()}

    for panel in RUNTIME_PANEL_MODULES:
        assert panel.route in registered_routes


def test_default_workspace_panel_ids_include_runtime_and_static_panels():
    panel_ids = get_default_panel_ids()

    assert "active-markets" in panel_ids
    assert "espn-matchup-predictor" in panel_ids
    assert "esports-intel" in panel_ids
    assert "sports-odds" in panel_ids
    assert "crypto-funding-watch" in panel_ids
    assert "global-transport-shipping" in panel_ids
    assert "cpi-release-command-center" in panel_ids
    assert "cpi-components-pressure-registry" in panel_ids
    assert "goods-tariff-supply-watch" in panel_ids
    assert "labor-services-inflation-monitor" in panel_ids
    assert "fed-reaction-growth-risk-board" in panel_ids
    assert "f1-trackside" in panel_ids
    assert len(panel_ids) == len(set(panel_ids))
