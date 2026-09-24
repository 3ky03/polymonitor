from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


CATALOG_VERSION = "telegram-delivery-2026-08-26.v1"


class DeliveryMode(str, Enum):
    SPECIALIZED = "specialized"
    GENERIC = "generic"
    AGGREGATE = "aggregate"
    MARKET_SCOPED = "market-scoped"
    BROWSER_ONLY = "browser-only"
    NON_PUSHABLE = "non-pushable"


@dataclass(frozen=True)
class DeliveryContract:
    panel_id: str
    mode: DeliveryMode
    topic: str = ""
    reason: str = ""
    aggregate_source: str = ""
    server_runtime: bool = False
    scheduled_default: bool = False

    @property
    def pushable(self) -> bool:
        return self.mode in {DeliveryMode.SPECIALIZED, DeliveryMode.GENERIC}


FRONTEND_PANEL_IDS = (
    "active-markets", "global-orderfilled", "oracle-feed", "market-summary",
    "featured-market", "price-implications", "price-chart", "sample-chain-trades",
    "oracle-timeline", "related-news", "breaking-event-radar", "market-tv-wire",
    "market-youtube-channels", "alpha-signal", "polybeats-feed", "whale-tracker",
    "suspicious-flow", "commodities-watch", "crypto-watch", "ai-model-race",
    "big-tech-market-cap", "consumer-app-pulse", "crypto-funding-watch",
    "defi-token-watch", "defi-yield-monitor", "defi-security-watch",
    "crypto-perp-funding", "tradfi-perp-radar", "ipo-news-watch",
    "broker-research-watch", "global-index-monitor", "commodity-equity-transmission",
    "crypto-fear-greed", "crypto-etf-flow", "stablecoin-monitor",
    "blockchain-policy-news", "trade-policy-radar", "geo-sanctions-shock",
    "global-transport-shipping", "cpi-release-command-center",
    "cpi-components-pressure-registry", "goods-tariff-supply-watch",
    "labor-services-inflation-monitor", "fed-reaction-growth-risk-board",
    "polymarket-macro-map", "cpi-release-calendar", "energy-gasoline-shock",
    "global-temperature-monitor", "weather-market-browser", "weather-city-snapshot",
    "weather-quote-detail", "weather-quote-table", "weather-trend-detail",
    "weather-trend-7d", "weather-news", "world-clock", "food-retail-basket-pressure",
    "supply-tariff-import-watch", "shelter-rent-oer-pressure",
    "labor-wage-services-pressure", "growth-demand-recession-tracker",
    "inflation-nowcast", "fed-rates-polymarket-gap", "nba-scoreboard", "nba-intel",
    "espn-matchup-predictor", "esports-intel", "sports-odds", "jin10-flash",
    "new-market-signals", "lob-depth", "f1-trackside",
)


_SPECIALIZED = {
    "latest-content": ("news", False, True),
    "related-news": ("intel", False, False),
    "alpha-signal": ("alpha", True, True),
    "new-market-signals": ("alpha", True, True),
    "polymarket-macro-map": ("macro", True, True),
    "cpi-release-command-center": ("macro", True, True),
    "nba-scoreboard": ("nba", True, True),
    "nba-intel": ("nba", True, True),
    "espn-matchup-predictor": ("nba", True, True),
    "worldcup-intel": ("worldcup", False, True),
    "global-weather-map": ("weather", True, True),
    "weather-news": ("weather", True, True),
}


_GENERIC_TOPIC_GROUPS = {
    "alpha": (
        "crypto-watch", "crypto-funding-watch", "defi-token-watch",
        "defi-yield-monitor", "defi-security-watch", "crypto-perp-funding",
        "tradfi-perp-radar", "crypto-fear-greed", "crypto-etf-flow",
        "stablecoin-monitor", "onchain-tradfi-perp-radar", "polybeats-feed",
        "whale-tracker", "suspicious-flow",
    ),
    "macro": (
        "commodities-watch", "global-index-monitor", "commodity-equity-transmission",
        "finance-market-atlas", "finance-liquidity-regime",
        "cpi-components-pressure-registry", "goods-tariff-supply-watch",
        "labor-services-inflation-monitor", "fed-reaction-growth-risk-board",
        "cpi-release-calendar", "inflation-nowcast", "energy-gasoline-shock",
        "food-retail-basket-pressure", "supply-tariff-import-watch",
        "shelter-rent-oer-pressure", "labor-wage-services-pressure",
        "growth-demand-recession-tracker", "fed-rates-polymarket-gap",
    ),
    "news": (
        "ai-model-race", "big-tech-market-cap", "consumer-app-pulse",
        "ipo-news-watch", "broker-research-watch", "blockchain-policy-news",
        "breaking-event-radar", "market-tv-wire", "market-youtube-channels",
        "jin10-flash",
    ),
    "intel": (
        "equity-event-command", "global-transport-shipping", "esports-intel",
        "sports-odds", "f1-trackside", "geo-sanctions-shock",
    ),
    "monitor": ("natural-hazards",),
}


_AGGREGATES = {
    "active-markets": "new-market-signals",
    "global-orderfilled": "alpha-signal/suspicious-flow/whale-tracker",
    "oracle-feed": "oracle lifecycle notifications",
    "global-temperature-monitor": "global-weather-map",
    "weather-market-browser": "global-weather-map",
    "weather-city-snapshot": "global-weather-map",
    "weather-quote-table": "global-weather-map",
    "weather-trend-detail": "global-weather-map",
    "weather-trend-7d": "global-weather-map",
    "world-cup-match-ops": "worldcup-intel",
}


_MARKET_SCOPED = {
    "market-summary": "selected market changes are represented by market-scoped alerts",
    "featured-market": "selection-dependent view",
    "price-implications": "selection-dependent derived view",
    "sample-chain-trades": "selection-dependent trade sample",
    "oracle-timeline": "selection-dependent lifecycle view",
    "lob-depth": "high-frequency selected-market order book",
}


_BROWSER_ONLY = {
    "trade-policy-radar": "static browser watchlist with local tab state",
    "weather-quote-detail": "selected-city browser CLOB hook",
    "world-clock": "browser Date/setInterval clock",
}


_NON_PUSHABLE = {
    "price-chart": "high-frequency selected-market visualization",
}


def _build_catalog() -> dict[str, DeliveryContract]:
    catalog: dict[str, DeliveryContract] = {}
    for panel_id, (topic, server_runtime, scheduled_default) in _SPECIALIZED.items():
        catalog[panel_id] = DeliveryContract(
            panel_id=panel_id,
            mode=DeliveryMode.SPECIALIZED,
            topic=topic,
            reason="dedicated formatter",
            server_runtime=server_runtime,
            scheduled_default=scheduled_default,
        )
    for topic, panel_ids in _GENERIC_TOPIC_GROUPS.items():
        for panel_id in panel_ids:
            catalog[panel_id] = DeliveryContract(
                panel_id=panel_id,
                mode=DeliveryMode.GENERIC,
                topic=topic,
                reason="semantic change summary",
                server_runtime=True,
            )
    for panel_id, source in _AGGREGATES.items():
        catalog[panel_id] = DeliveryContract(
            panel_id=panel_id,
            mode=DeliveryMode.AGGREGATE,
            reason="covered by an aggregate feed",
            aggregate_source=source,
            server_runtime=panel_id in {
                "global-temperature-monitor", "weather-market-browser", "world-cup-match-ops"
            },
        )
    for panel_id, reason in _MARKET_SCOPED.items():
        catalog[panel_id] = DeliveryContract(
            panel_id=panel_id,
            mode=DeliveryMode.MARKET_SCOPED,
            reason=reason,
        )
    for panel_id, reason in _BROWSER_ONLY.items():
        catalog[panel_id] = DeliveryContract(
            panel_id=panel_id,
            mode=DeliveryMode.BROWSER_ONLY,
            reason=reason,
        )
    for panel_id, reason in _NON_PUSHABLE.items():
        catalog[panel_id] = DeliveryContract(
            panel_id=panel_id,
            mode=DeliveryMode.NON_PUSHABLE,
            reason=reason,
        )
    return catalog


DELIVERY_CATALOG = _build_catalog()
SERVER_RUNTIME_PANEL_IDS = tuple(
    panel_id for panel_id, contract in DELIVERY_CATALOG.items() if contract.server_runtime
)


def delivery_contract(panel_id: str) -> DeliveryContract | None:
    return DELIVERY_CATALOG.get(str(panel_id or "").strip())


def coverage_report(
    *,
    frontend_panel_ids: Iterable[str] = FRONTEND_PANEL_IDS,
    runtime_panel_ids: Iterable[str] = SERVER_RUNTIME_PANEL_IDS,
) -> dict[str, object]:
    frontend = {str(value) for value in frontend_panel_ids}
    runtime = {str(value) for value in runtime_panel_ids}
    catalog_ids = set(DELIVERY_CATALOG)
    mode_counts = {
        mode.value: sum(contract.mode is mode for contract in DELIVERY_CATALOG.values())
        for mode in DeliveryMode
    }
    return {
        "catalogVersion": CATALOG_VERSION,
        "catalogCount": len(catalog_ids),
        "frontendCount": len(frontend),
        "runtimeCount": len(runtime),
        "missingFrontend": sorted(frontend - catalog_ids),
        "missingRuntime": sorted(runtime - catalog_ids),
        "modeCounts": mode_counts,
        "valid": not (frontend - catalog_ids or runtime - catalog_ids),
    }
