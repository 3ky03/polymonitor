from __future__ import annotations

from types import ModuleType
from typing import Dict, Iterable, List, Optional

from .modules import (
    ai_model_race,
    alpha_signal,
    breaking_event_radar,
    big_tech_market_cap,
    crypto_funding_watch,
    crypto_etf_flow,
    crypto_fear_greed,
    crypto_perp_funding,
    commodities_watch,
    consumer_app_pulse,
    blockchain_policy_news,
    broker_research_watch,
    commodity_equity_transmission,
    cpi_components_pressure_registry,
    cpi_release_command_center,
    cpi_release_calendar,
    crypto_watch,
    defi_yield_monitor,
    defi_security_watch,
    defi_token_watch,
    energy_gasoline_shock,
    equity_event_command,
    esports_intel,
    espn_matchup_predictor,
    f1_trackside,
    fed_reaction_growth_risk_board,
    fed_rates_polymarket_gap,
    finance_liquidity_regime,
    finance_market_atlas,
    food_retail_basket,
    global_temperature_monitor,
    global_index_monitor,
    global_transport_shipping,
    global_weather_map,
    weather_market_browser,
    geo_sanctions_shock,
    natural_hazards,
    goods_tariff_supply_watch,
    growth_demand_recession_tracker,
    inflation_nowcast,
    ipo_news_watch,
    jin10_flash,
    labor_wage_services_pressure,
    labor_services_inflation_monitor,
    market_tv_wire,
    market_youtube_channels,
    new_market_signals,
    nba_intel,
    nba_scoreboard,
    onchain_tradfi_perp_radar,
    polybeats_feed,
    polymarket_macro_map,
    shelter_rent_oer_pressure,
    supply_tariff_import_watch,
    suspicious_flow,
    sports_odds,
    stablecoin_monitor,
    tradfi_perp_radar,
    weather_news,
    world_cup_match_ops,
    whale_tracker,
)
from .types import RuntimePanelModule


_MODULES: List[ModuleType] = [
    commodities_watch,
    crypto_watch,
    ai_model_race,
    big_tech_market_cap,
    consumer_app_pulse,
    crypto_funding_watch,
    defi_token_watch,
    defi_yield_monitor,
    defi_security_watch,
    crypto_perp_funding,
    tradfi_perp_radar,
    ipo_news_watch,
    broker_research_watch,
    global_index_monitor,
    commodity_equity_transmission,
    crypto_fear_greed,
    crypto_etf_flow,
    stablecoin_monitor,
    blockchain_policy_news,
    finance_market_atlas,
    equity_event_command,
    onchain_tradfi_perp_radar,
    finance_liquidity_regime,
    global_transport_shipping,
    global_temperature_monitor,
    global_weather_map,
    weather_market_browser,
    weather_news,
    breaking_event_radar,
    market_tv_wire,
    market_youtube_channels,
    esports_intel,
    sports_odds,
    world_cup_match_ops,
    f1_trackside,
    jin10_flash,
    new_market_signals,
    nba_scoreboard,
    nba_intel,
    espn_matchup_predictor,
    geo_sanctions_shock,
    natural_hazards,
    cpi_release_command_center,
    cpi_components_pressure_registry,
    goods_tariff_supply_watch,
    labor_services_inflation_monitor,
    fed_reaction_growth_risk_board,
    cpi_release_calendar,
    polymarket_macro_map,
    inflation_nowcast,
    energy_gasoline_shock,
    food_retail_basket,
    supply_tariff_import_watch,
    shelter_rent_oer_pressure,
    labor_wage_services_pressure,
    growth_demand_recession_tracker,
    fed_rates_polymarket_gap,
    polybeats_feed,
    alpha_signal,
    whale_tracker,
    suspicious_flow,
]


def _coerce_module(module: ModuleType) -> RuntimePanelModule:
    return RuntimePanelModule(
        panel_id=module.PANEL_ID,
        route=module.ROUTE,
        default_limit=module.DEFAULT_LIMIT,
        min_limit=module.MIN_LIMIT,
        max_limit=module.MAX_LIMIT,
        get_snapshot=module.get_snapshot,
    )


RUNTIME_PANEL_MODULES: List[RuntimePanelModule] = [_coerce_module(module) for module in _MODULES]

DEFAULT_WORKSPACE_PANEL_IDS: List[str] = [
    "active-markets",
    "global-orderfilled",
    "oracle-feed",
    "market-summary",
    "featured-market",
    "world-brief",
    "geo-sanctions-shock",
    "cpi-release-command-center",
    "cpi-components-pressure-registry",
    "goods-tariff-supply-watch",
    "labor-services-inflation-monitor",
    "fed-reaction-growth-risk-board",
    "price-implications",
    "price-chart",
    "sample-chain-trades",
    "oracle-timeline",
    "related-news",
    "market-tv-wire",
    "market-youtube-channels",
    "breaking-event-radar",
    "alpha-signal",
    "polybeats-feed",
    "whale-tracker",
    "suspicious-flow",
    "commodities-watch",
    "commodity-equity-transmission",
    "crypto-watch",
    "ai-model-race",
    "big-tech-market-cap",
    "consumer-app-pulse",
    "crypto-funding-watch",
    "defi-token-watch",
    "defi-yield-monitor",
    "defi-security-watch",
    "crypto-perp-funding",
    "tradfi-perp-radar",
    "ipo-news-watch",
    "broker-research-watch",
    "global-index-monitor",
    "crypto-fear-greed",
    "crypto-etf-flow",
    "stablecoin-monitor",
    "blockchain-policy-news",
    "finance-market-atlas",
    "equity-event-command",
    "onchain-tradfi-perp-radar",
    "finance-liquidity-regime",
    "global-transport-shipping",
    "global-temperature-monitor",
    "weather-market-browser",
    "weather-news",
    "nba-scoreboard",
    "nba-intel",
    "espn-matchup-predictor",
    "esports-intel",
    "sports-odds",
    "jin10-flash",
    "new-market-signals",
    "lob-depth",
    "live-api-status",
    "system-health",
    "f1-trackside",
]


def _assert_unique(panels: Iterable[RuntimePanelModule]) -> None:
    panel_ids = set()
    routes = set()
    for panel in panels:
        if panel.panel_id in panel_ids:
            raise ValueError(f"duplicate runtime panel id: {panel.panel_id}")
        if panel.route in routes:
            raise ValueError(f"duplicate runtime panel route: {panel.route}")
        panel_ids.add(panel.panel_id)
        routes.add(panel.route)


_assert_unique(RUNTIME_PANEL_MODULES)

_PANELS_BY_ROUTE: Dict[str, RuntimePanelModule] = {panel.route: panel for panel in RUNTIME_PANEL_MODULES}
_PANELS_BY_ID: Dict[str, RuntimePanelModule] = {panel.panel_id: panel for panel in RUNTIME_PANEL_MODULES}


def get_panel_by_route(route: str) -> Optional[RuntimePanelModule]:
    return _PANELS_BY_ROUTE.get(route)


def get_panel_by_id(panel_id: str) -> Optional[RuntimePanelModule]:
    return _PANELS_BY_ID.get(panel_id)


def get_panel_routes() -> List[str]:
    return [panel.route for panel in RUNTIME_PANEL_MODULES]


def get_panel_ids() -> List[str]:
    return [panel.panel_id for panel in RUNTIME_PANEL_MODULES]


def get_default_panel_ids() -> List[str]:
    return list(DEFAULT_WORKSPACE_PANEL_IDS)
