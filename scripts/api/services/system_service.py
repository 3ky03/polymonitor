from __future__ import annotations

import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, cast

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
    resolve_service_value,
)


SYSTEM_HEALTH_CACHE_NAMESPACE = "system:health"
SYSTEM_HEALTH_CACHE_KEY = "payload-v1"
SYSTEM_HEALTH_CACHE_TTL_SECONDS = 15
_SYSTEM_HEALTH_CACHE_LOCK = threading.Lock()
_SYSTEM_HEALTH_REFRESH_LOCK = threading.Lock()
_SYSTEM_HEALTH_CACHE: Dict[str, Any] = {}
_SYSTEM_HEALTH_REFRESHING = False


@dataclass(frozen=True)
class SystemHealthDependencies:
    application: Any
    describe_db_target: Callable[..., Any]
    get_redis_client: Callable[..., Any]
    table_exists: Callable[..., Any]
    query_all: Callable[..., Any]
    query_one: Callable[..., Any]
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    get_lob_runtime_status: Callable[..., Any] | None
    get_lob_storage_status: Callable[..., Any] | None

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> SystemHealthDependencies:
        return cls(
            application=resolve_service_value(context, "app"),
            describe_db_target=cast(
                Callable[..., Any],
                resolve_service_callable(context, "describe_db_target"),
            ),
            get_redis_client=cast(
                Callable[..., Any],
                resolve_service_callable(context, "get_redis_client"),
            ),
            table_exists=cast(
                Callable[..., Any],
                resolve_service_callable(context, "table_exists"),
            ),
            query_all=cast(
                Callable[..., Any],
                resolve_service_callable(context, "query_all"),
            ),
            query_one=cast(
                Callable[..., Any],
                resolve_service_callable(context, "query_one"),
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
            get_lob_runtime_status=resolve_optional_service_callable(
                context,
                "get_lob_runtime_status",
            ),
            get_lob_storage_status=resolve_optional_service_callable(
                context,
                "get_lob_storage_status",
            ),
        )


@dataclass(frozen=True)
class SeedHealthDependencies:
    snapshot_store: Any | None
    get_cached_json: Callable[..., Any] | None
    utc_now_iso: Callable[..., Any]

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> SeedHealthDependencies:
        return cls(
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            utc_now_iso=cast(
                Callable[..., Any],
                resolve_service_callable(context, "utc_now_iso"),
            ),
        )


SEED_META_SPECS = [
    {
        "panelId": "geo-sanctions-shock",
        "namespace": "seed-meta:world",
        "cacheKey": "geo-sanctions-shock",
        "serviceName": "polydata-geo-sanctions-shock.service",
        "intervalEnv": "POLYDATA_GEO_SHOCK_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 300,
    },
    {
        "panelId": "new-market-signals",
        "namespace": "seed-meta:markets",
        "cacheKey": "new-market-signals",
        "serviceName": "polydata-new-market-signal.service",
        "intervalEnv": "POLYDATA_NEW_MARKET_SIGNAL_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 20,
    },
    {
        "panelId": "jin10-flash",
        "namespace": "seed-meta:macro",
        "cacheKey": "jin10-flash",
        "serviceName": "polydata-jin10-seed.service",
        "intervalEnv": "POLYDATA_JIN10_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 180,
    },
    {
        "panelId": "f1-trackside",
        "namespace": "seed-meta:sports",
        "cacheKey": "f1-trackside",
        "serviceName": "polydata-f1-seed.service",
        "intervalEnv": "POLYDATA_F1_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 180,
    },
    {
        "panelId": "nba-scoreboard",
        "namespace": "seed-meta:sports",
        "cacheKey": "nba-scoreboard",
        "serviceName": "polydata-nba-seed.service",
        "intervalEnv": "POLYDATA_NBA_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 60,
    },
    {
        "panelId": "nba-intel",
        "namespace": "seed-meta:sports",
        "cacheKey": "nba-intel",
        "serviceName": "polydata-nba-seed.service",
        "intervalEnv": "POLYDATA_NBA_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 60,
    },
    {
        "panelId": "espn-matchup-predictor",
        "namespace": "seed-meta:sports",
        "cacheKey": "espn-matchup-predictor",
        "serviceName": "polydata-nba-seed.service",
        "intervalEnv": "POLYDATA_NBA_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 60,
    },
    {
        "panelId": "commodities-watch",
        "namespace": "seed-meta:markets",
        "cacheKey": "commodities-watch",
        "serviceName": "polydata-market-group-seed.service",
        "intervalEnv": "POLYDATA_MARKET_GROUP_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 60,
    },
    {
        "panelId": "crypto-watch",
        "namespace": "seed-meta:markets",
        "cacheKey": "crypto-watch",
        "serviceName": "polydata-market-group-seed.service",
        "intervalEnv": "POLYDATA_MARKET_GROUP_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 60,
    },
    {
        "panelId": "crypto-funding-watch",
        "namespace": "seed-meta:crypto",
        "cacheKey": "funding-watch",
        "serviceName": "polydata-crypto-funding-seed.service",
        "intervalEnv": "POLYDATA_CRYPTO_FUNDING_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 30,
    },
    {
        "panelId": "defi-token-watch",
        "namespace": "seed-meta:finance",
        "cacheKey": "defi-token-watch",
        "serviceName": "polydata-defi-token-watch-seed.service",
        "intervalEnv": "POLYDATA_DEFI_TOKEN_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 120,
    },
    *[
        {
            "panelId": panel_id,
            "namespace": "seed-meta:finance",
            "cacheKey": panel_id,
            "serviceName": "polydata-finance-watch-panels-seed.service",
            "intervalEnv": "POLYDATA_FINANCE_WATCH_PANELS_INTERVAL_SECONDS",
            "defaultIntervalSeconds": 600,
        }
        for panel_id in (
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
        )
    ],
    *[
        {
            "panelId": panel_id,
            "namespace": "seed-meta:tech",
            "cacheKey": panel_id,
            "serviceName": "polydata-tech-panels-seed.service",
            "intervalEnv": "POLYDATA_TECH_PANELS_INTERVAL_SECONDS",
            "defaultIntervalSeconds": 600,
        }
        for panel_id in (
            "ai-model-race",
            "big-tech-market-cap",
            "consumer-app-pulse",
        )
    ],
    {
        "panelId": "finance-external-sources",
        "namespace": "seed-meta:finance",
        "cacheKey": "external-sources",
        "serviceName": "polydata-finance-external-sources-seed.service",
        "intervalEnv": "POLYDATA_FINANCE_EXTERNAL_SOURCES_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 900,
    },
    {
        "panelId": "esports-intel",
        "namespace": "seed-meta:esports",
        "cacheKey": "esports-intel",
        "serviceName": "polydata-grid-esports-seed.service",
        "intervalEnv": "POLYDATA_GRID_ESPORTS_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 120,
    },
    {
        "panelId": "sports-odds",
        "namespace": "seed-meta:sports",
        "cacheKey": "sports-odds",
        "serviceName": "polydata-sports-odds-seed.service",
        "intervalEnv": "POLYDATA_SPORTS_ODDS_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 180,
    },
    {
        "panelId": "inflation-nowcast",
        "namespace": "seed-meta:macro",
        "cacheKey": "inflation-nowcast",
        "serviceName": "polydata-inflation-nowcast-seed.service",
        "intervalEnv": "POLYDATA_INFLATION_NOWCAST_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 1800,
    },
    {
        "panelId": "polymarket-macro-map",
        "namespace": "seed-meta:macro",
        "cacheKey": "polymarket-macro-map",
        "serviceName": "polydata-polymarket-macro-map-seed.service",
        "intervalEnv": "POLYDATA_MACRO_MARKET_MAP_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 180,
    },
    {
        "panelId": "cpi-release-calendar",
        "namespace": "seed-meta:macro",
        "cacheKey": "cpi-release-calendar",
        "serviceName": "polydata-cpi-release-calendar-seed.service",
        "intervalEnv": "POLYDATA_CPI_CALENDAR_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 3600,
    },
    {
        "panelId": "energy-gasoline-shock",
        "namespace": "seed-meta:macro",
        "cacheKey": "energy-gasoline-shock",
        "serviceName": "polydata-energy-gasoline-shock-seed.service",
        "intervalEnv": "POLYDATA_ENERGY_SHOCK_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 21600,
    },
    {
        "panelId": "global-weather-map",
        "namespace": "seed-meta:weather",
        "cacheKey": "global-weather-map",
        "serviceName": "polydata-global-weather-map-seed.service",
        "intervalEnv": "POLYDATA_GLOBAL_WEATHER_MAP_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 60,
    },
    {
        "panelId": "weather-news",
        "namespace": "seed-meta:weather",
        "cacheKey": "weather-news",
        "serviceName": "polydata-weather-news-seed.service",
        "intervalEnv": "POLYDATA_WEATHER_NEWS_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 300,
    },
    {
        "panelId": "breaking-event-radar",
        "namespace": "seed-meta:evidence",
        "cacheKey": "breaking-event-radar",
        "serviceName": "polydata-breaking-event-radar-seed.service",
        "intervalEnv": "POLYDATA_BREAKING_EVENT_RADAR_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 300,
    },
    {
        "panelId": "world-cup-match-ops",
        "namespace": "seed-meta:sports",
        "cacheKey": "world-cup-match-ops",
        "serviceName": "polydata-world-cup-match-ops-seed.service",
        "intervalEnv": "POLYDATA_WORLD_CUP_MATCH_OPS_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 300,
    },
    {
        "panelId": "global-transport-shipping",
        "namespace": "seed-meta:transport",
        "cacheKey": "global-transport-shipping",
        "serviceName": "polydata-global-transport-shipping-seed.service",
        "intervalEnv": "POLYDATA_GLOBAL_TRANSPORT_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 3600,
    },
    {
        "panelId": "market-tv-wire",
        "namespace": "seed-meta:content",
        "cacheKey": "market-tv-wire",
        "serviceName": "polydata-market-tv-wire-seed.service",
        "intervalEnv": "POLYDATA_MARKET_TV_WIRE_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 900,
    },
    {
        "panelId": "food-retail-basket-pressure",
        "namespace": "seed-meta:macro",
        "cacheKey": "food-retail-basket-pressure",
        "serviceName": "polydata-food-retail-basket-seed.service",
        "intervalEnv": "POLYDATA_FOOD_BASKET_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 21600,
    },
    {
        "panelId": "supply-tariff-import-watch",
        "namespace": "seed-meta:macro",
        "cacheKey": "supply-tariff-import-watch",
        "serviceName": "polydata-macro-cpi-panels-seed.service",
        "intervalEnv": "POLYDATA_MACRO_CPI_PANELS_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 21600,
    },
    {
        "panelId": "shelter-rent-oer-pressure",
        "namespace": "seed-meta:macro",
        "cacheKey": "shelter-rent-oer-pressure",
        "serviceName": "polydata-macro-cpi-panels-seed.service",
        "intervalEnv": "POLYDATA_MACRO_CPI_PANELS_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 21600,
    },
    {
        "panelId": "labor-wage-services-pressure",
        "namespace": "seed-meta:macro",
        "cacheKey": "labor-wage-services-pressure",
        "serviceName": "polydata-macro-cpi-panels-seed.service",
        "intervalEnv": "POLYDATA_MACRO_CPI_PANELS_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 21600,
    },
    {
        "panelId": "growth-demand-recession-tracker",
        "namespace": "seed-meta:macro",
        "cacheKey": "growth-demand-recession-tracker",
        "serviceName": "polydata-macro-cpi-panels-seed.service",
        "intervalEnv": "POLYDATA_MACRO_CPI_PANELS_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 21600,
    },
    {
        "panelId": "fed-rates-polymarket-gap",
        "namespace": "seed-meta:macro",
        "cacheKey": "fed-rates-polymarket-gap",
        "serviceName": "polydata-macro-cpi-panels-seed.service",
        "intervalEnv": "POLYDATA_MACRO_CPI_PANELS_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 21600,
    },
    {
        "panelId": "cpi-release-command-center",
        "namespace": "seed-meta:macro",
        "cacheKey": "cpi-release-command-center",
        "serviceName": "polydata-macro-cpi-registry-seed.service",
        "intervalEnv": "POLYDATA_MACRO_CPI_REGISTRY_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 1800,
    },
    {
        "panelId": "cpi-components-pressure-registry",
        "namespace": "seed-meta:macro",
        "cacheKey": "cpi-components-pressure-registry",
        "serviceName": "polydata-macro-cpi-registry-seed.service",
        "intervalEnv": "POLYDATA_MACRO_CPI_REGISTRY_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 1800,
    },
    {
        "panelId": "goods-tariff-supply-watch",
        "namespace": "seed-meta:macro",
        "cacheKey": "goods-tariff-supply-watch",
        "serviceName": "polydata-macro-cpi-registry-seed.service",
        "intervalEnv": "POLYDATA_MACRO_CPI_REGISTRY_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 1800,
    },
    {
        "panelId": "labor-services-inflation-monitor",
        "namespace": "seed-meta:macro",
        "cacheKey": "labor-services-inflation-monitor",
        "serviceName": "polydata-macro-cpi-registry-seed.service",
        "intervalEnv": "POLYDATA_MACRO_CPI_REGISTRY_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 1800,
    },
    {
        "panelId": "fed-reaction-growth-risk-board",
        "namespace": "seed-meta:macro",
        "cacheKey": "fed-reaction-growth-risk-board",
        "serviceName": "polydata-macro-cpi-registry-seed.service",
        "intervalEnv": "POLYDATA_MACRO_CPI_REGISTRY_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 1800,
    },
    {
        "panelId": "alpha-signal",
        "namespace": "seed-meta:signals",
        "cacheKey": "alpha-signal",
        "serviceName": "polydata-alpha-signal-seed.service",
        "intervalEnv": "POLYDATA_SIGNAL_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 45,
    },
    {
        "panelId": "polybeats-feed",
        "namespace": "seed-meta:signals",
        "cacheKey": "polybeats-feed",
        "serviceName": "polydata-polybeats-feed-seed.service",
        "intervalEnv": "POLYDATA_SIGNAL_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 45,
    },
    {
        "panelId": "whale-trades",
        "namespace": "seed-meta:signals",
        "cacheKey": "whale-trades",
        "serviceName": "polydata-whale-trades-seed.service",
        "intervalEnv": "POLYDATA_SIGNAL_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 45,
    },
    {
        "panelId": "suspicious-trades",
        "namespace": "seed-meta:signals",
        "cacheKey": "suspicious-trades",
        "serviceName": "polydata-suspicious-trades-seed.service",
        "intervalEnv": "POLYDATA_SIGNAL_WATCH_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 45,
    },
    {
        "panelId": "bootstrap",
        "namespace": "seed-meta:bootstrap",
        "cacheKey": "bootstrap",
        "serviceName": "polydata-bootstrap-seed.service",
        "intervalEnv": "POLYDATA_BOOTSTRAP_SEED_INTERVAL_SECONDS",
        "defaultIntervalSeconds": 60,
    },
]


def _read_seed_meta(
    dependencies: SeedHealthDependencies,
    *,
    namespace: str,
    cache_key: str,
) -> Dict[str, Any] | None:
    if dependencies.get_cached_json is not None:
        payload = dependencies.get_cached_json(namespace, cache_key)
        if isinstance(payload, dict):
            return payload
    if dependencies.snapshot_store is not None:
        payload = dependencies.snapshot_store.get_stale(namespace, cache_key)
        if isinstance(payload, dict):
            return payload
    return None


def _age_seconds_from_iso(raw: Any) -> int | None:
    if not raw:
        return None
    try:
        iso = str(raw).replace("Z", "+00:00")
        return max(0, int(time.time() - datetime.fromisoformat(iso).timestamp()))
    except Exception:
        return None


def _freshness_label(age_seconds: int | None, expected_interval_seconds: int) -> str:
    if age_seconds is None:
        return "unknown"
    if age_seconds <= max(30, expected_interval_seconds * 2):
        return "fresh"
    if age_seconds <= max(60, expected_interval_seconds * 6):
        return "aging"
    return "stale"


def _system_health_cache_ttl_seconds() -> int:
    try:
        return max(1, int(os.environ.get("POLYDATA_SYSTEM_HEALTH_CACHE_TTL_SECONDS", SYSTEM_HEALTH_CACHE_TTL_SECONDS)))
    except (TypeError, ValueError):
        return SYSTEM_HEALTH_CACHE_TTL_SECONDS


def _build_system_health_payload_uncached(
    dependencies: SystemHealthDependencies,
) -> Dict[str, Any]:
    lob_runtime: Dict[str, Any] = {"status": "ready", "mode": "local-orderbook"}
    if dependencies.get_lob_runtime_status is not None:
        try:
            runtime_payload = dependencies.get_lob_runtime_status()
            if isinstance(runtime_payload, dict):
                lob_runtime.update(runtime_payload)
        except Exception as exc:
            lob_runtime.update({"status": "unavailable", "detail": str(exc)[:240]})
    if dependencies.get_lob_storage_status is not None:
        try:
            storage_payload = dependencies.get_lob_storage_status()
            if isinstance(storage_payload, dict):
                lob_runtime["storage"] = storage_payload
                if storage_payload.get("rollupWatermark") is not None:
                    lob_runtime["rollupWatermark"] = storage_payload.get("rollupWatermark")
                if storage_payload.get("deadLetters1h") is not None:
                    lob_runtime["deadLetters1h"] = storage_payload.get("deadLetters1h")
        except Exception as exc:
            lob_runtime["storage"] = {"status": "unavailable", "detail": str(exc)[:240]}
    payload: Dict[str, Any] = {
        "database": dependencies.describe_db_target(),
        "redis": bool(dependencies.get_redis_client()),
        "apiStatus": "ok",
        "lobRuntime": lob_runtime,
        "contentSync": {
            "status": "database-runtime-intel"
            if dependencies.table_exists("content_items")
            and dependencies.table_exists("content_links")
            else "runtime-intel"
        },
    }
    if not dependencies.table_exists("sync_state"):
        payload["syncState"] = {}
        return payload

    sync_rows = dependencies.query_all(
        """
        SELECT `key`, value, last_block, updated_at
        FROM sync_state
        WHERE `key` IN (?, ?, ?, ?, ?, ?)
        ORDER BY updated_at DESC
        """,
        (
            "market_sync",
            "trade_sync",
            "oracle_sync",
            "market_sync_live",
            "trade_sync_live",
            "oracle_sync_live",
        ),
    )
    sync_state = {}
    for row in sync_rows:
        sync_state[row.get("key")] = {
            "value": row.get("value"),
            "lastBlock": row.get("last_block"),
            "updatedAt": row.get("updated_at"),
        }
    payload["syncState"] = sync_state
    market_sync = sync_state.get("market_sync_live") or sync_state.get("market_sync")
    if market_sync is None and dependencies.table_exists("market_tokens"):
        market_token_state = dependencies.query_one(
            "SELECT MAX(updated_at) AS updated_at FROM market_tokens"
        )
        if market_token_state.get("updated_at") is not None:
            market_sync = {
                "status": "derived-from-market-tokens",
                "updatedAt": market_token_state.get("updated_at"),
            }
    payload["marketSync"] = market_sync
    payload["tradeSync"] = sync_state.get("trade_sync_live") or sync_state.get("trade_sync")
    payload["oracleSync"] = sync_state.get("oracle_sync_live") or sync_state.get("oracle_sync")
    payload["priceSync"] = {
        "status": "derived-from-trades",
        "updatedAt": dependencies.query_one(
            "SELECT MAX(latest_trade_at) AS updated_at FROM market_latest_prices"
        ).get("updated_at")
        if dependencies.table_exists("market_latest_prices")
        else None,
    }
    return payload


def _build_system_health_warming_payload(
    dependencies: SystemHealthDependencies,
) -> Dict[str, Any]:
    """Return a dependency-free placeholder while the full health probe runs."""

    return {
        "database": dependencies.describe_db_target(),
        "apiStatus": "warming",
        "lobRuntime": {"status": "warming", "mode": "local-orderbook"},
        "contentSync": {"status": "warming"},
        "syncState": {},
        "marketSync": None,
        "tradeSync": None,
        "oracleSync": None,
        "priceSync": {"status": "warming", "updatedAt": None},
    }


def _store_local_system_health_payload(payload: Dict[str, Any], ttl_seconds: int) -> None:
    with _SYSTEM_HEALTH_CACHE_LOCK:
        _SYSTEM_HEALTH_CACHE["value"] = payload
        _SYSTEM_HEALTH_CACHE["expires_at"] = time.monotonic() + ttl_seconds


def _store_system_health_payload(
    dependencies: SystemHealthDependencies,
    payload: Dict[str, Any],
    ttl_seconds: int,
) -> None:
    if dependencies.set_cached_json is not None:
        dependencies.set_cached_json(
            SYSTEM_HEALTH_CACHE_NAMESPACE,
            SYSTEM_HEALTH_CACHE_KEY,
            payload,
            ttl_seconds,
        )
    _store_local_system_health_payload(payload, ttl_seconds)


def _schedule_system_health_refresh(
    dependencies: SystemHealthDependencies,
    ttl_seconds: int,
) -> None:
    global _SYSTEM_HEALTH_REFRESHING
    with _SYSTEM_HEALTH_REFRESH_LOCK:
        if _SYSTEM_HEALTH_REFRESHING:
            return
        _SYSTEM_HEALTH_REFRESHING = True

    def refresh() -> None:
        global _SYSTEM_HEALTH_REFRESHING
        try:
            payload = _build_system_health_payload_uncached(dependencies)
            _store_system_health_payload(dependencies, payload, ttl_seconds)
        except Exception:
            dependencies.application.logger.exception("system-health refresh failed")
        finally:
            with _SYSTEM_HEALTH_REFRESH_LOCK:
                _SYSTEM_HEALTH_REFRESHING = False

    thread = threading.Thread(target=refresh, name="system-health-refresh", daemon=True)
    thread.start()


def build_system_health_payload(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    dependencies = SystemHealthDependencies.from_context(ctx)
    ttl_seconds = _system_health_cache_ttl_seconds()
    now = time.monotonic()
    stale_payload = None
    with _SYSTEM_HEALTH_CACHE_LOCK:
        cached = _SYSTEM_HEALTH_CACHE.get("value")
        if isinstance(cached, dict) and float(_SYSTEM_HEALTH_CACHE.get("expires_at") or 0.0) > now:
            return cached
        if isinstance(cached, dict):
            stale_payload = cached

    if dependencies.get_cached_json is not None:
        redis_payload = dependencies.get_cached_json(
            SYSTEM_HEALTH_CACHE_NAMESPACE,
            SYSTEM_HEALTH_CACHE_KEY,
        )
        if isinstance(redis_payload, dict):
            with _SYSTEM_HEALTH_CACHE_LOCK:
                _SYSTEM_HEALTH_CACHE["value"] = redis_payload
                _SYSTEM_HEALTH_CACHE["expires_at"] = time.monotonic() + ttl_seconds
            return redis_payload

    if stale_payload is not None:
        _schedule_system_health_refresh(dependencies, ttl_seconds)
        return stale_payload

    warming_payload = _build_system_health_warming_payload(dependencies)
    _store_local_system_health_payload(warming_payload, ttl_seconds)
    _schedule_system_health_refresh(dependencies, ttl_seconds)
    return warming_payload


def prewarm_system_health_payload(ctx: Mapping[str, Any]) -> None:
    """Start the full health probe without extending API startup latency."""

    dependencies = SystemHealthDependencies.from_context(ctx)
    ttl_seconds = _system_health_cache_ttl_seconds()
    with _SYSTEM_HEALTH_CACHE_LOCK:
        cached = _SYSTEM_HEALTH_CACHE.get("value")
        expires_at = float(_SYSTEM_HEALTH_CACHE.get("expires_at") or 0.0)
    if isinstance(cached, dict) and cached.get("apiStatus") != "warming" and expires_at > time.monotonic():
        return
    if not isinstance(cached, dict):
        _store_local_system_health_payload(
            _build_system_health_warming_payload(dependencies),
            ttl_seconds,
        )
    _schedule_system_health_refresh(dependencies, ttl_seconds)


def build_seed_health_payload(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    dependencies = SeedHealthDependencies.from_context(ctx)
    items = []
    for spec in SEED_META_SPECS:
        payload = (
            _read_seed_meta(
                dependencies,
                namespace=spec["namespace"],
                cache_key=spec["cacheKey"],
            )
            or {}
        )
        expected_interval_seconds = max(
            1,
            int(os.environ.get(spec["intervalEnv"], payload.get("expectedIntervalSeconds") or spec["defaultIntervalSeconds"])),
        )
        last_attempt_at = payload.get("lastAttemptAt")
        last_success_at = payload.get("lastSuccessAt")
        attempt_age_seconds = _age_seconds_from_iso(last_attempt_at)
        success_age_seconds = _age_seconds_from_iso(last_success_at)
        freshness = _freshness_label(success_age_seconds, expected_interval_seconds)
        status = str(payload.get("status") or ("missing" if not payload else "unknown")).strip().lower()
        if status == "ok" and freshness != "fresh":
            status = "degraded"
        if status == "scan":
            status = "ok"
        if status == "bootstrap":
            status = "ok"
        if status == "preserved":
            status = "degraded"
        if status == "empty":
            status = "degraded"
        item = {
            "panelId": spec["panelId"],
            "serviceName": spec["serviceName"],
            "status": status,
            "freshness": freshness,
            "expectedIntervalSeconds": expected_interval_seconds,
            "lastAttemptAt": last_attempt_at,
            "lastSuccessAt": last_success_at,
            "attemptAgeSeconds": attempt_age_seconds,
            "successAgeSeconds": success_age_seconds,
            "recordCount": int(payload.get("recordCount") or 0),
            "sourceStates": payload.get("sourceStates") if isinstance(payload.get("sourceStates"), dict) else {},
            "errorSummary": payload.get("errorSummary"),
            "cacheMode": payload.get("cacheMode"),
            "payloadStatus": payload.get("payloadStatus"),
            "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        }
        items.append(item)

    ok_count = sum(1 for item in items if item["status"] == "ok")
    degraded_count = sum(1 for item in items if item["status"] == "degraded")
    error_count = sum(1 for item in items if item["status"] in {"error", "missing"})
    overall_status = "ok"
    if error_count:
        overall_status = "error"
    elif degraded_count:
        overall_status = "degraded"
    return {
        "generatedAt": dependencies.utc_now_iso(),
        "status": overall_status,
        "summary": {
            "watcherCount": len(items),
            "okCount": ok_count,
            "degradedCount": degraded_count,
            "errorCount": error_count,
        },
        "items": items,
    }
