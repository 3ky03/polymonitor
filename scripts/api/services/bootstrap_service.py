from __future__ import annotations

import threading
import time
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, cast

from api.context import resolve_service_callable, resolve_service_value
from api.runtime_panels import get_default_panel_ids


BOOTSTRAP_SNAPSHOT_NAMESPACE = "snapshot:bootstrap"
BOOTSTRAP_CACHE_KEY = "workspace-default-v12"
DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL = """
    LOWER(COALESCE(CAST(m.tags AS TEXT), '')) NOT LIKE '%%hide-from-new%%'
    AND LOWER(COALESCE(CAST(m.tags AS TEXT), '')) NOT LIKE '%%recurring%%'
    AND LOWER(COALESCE(CAST(m.tags AS TEXT), '')) NOT LIKE '%%onchain-registry%%'
    AND LOWER(COALESCE(CAST(m.slug AS TEXT), '')) NOT LIKE '%%updown-5m%%'
    AND LOWER(COALESCE(CAST(m.slug AS TEXT), '')) NOT LIKE '%%updown-15m%%'
    AND LOWER(COALESCE(CAST(m.title AS TEXT), '')) NOT LIKE '%% up or down - %%'
"""
DEFAULT_ACTIVE_MARKET_ACTIVITY_SQL = """
    (
        COALESCE(stats_24h.trade_count_24h, 0) > 0
        OR COALESCE(stats_24h.volume_24h, 0) > 0
        OR stats_24h.last_trade_at IS NOT NULL
        OR mlp.latest_trade_at IS NOT NULL
    )
"""
DEFAULT_ACTIVE_MARKET_PRICE_SQL = """
    (
        mlp.latest_yes_price IS NULL
        OR (CAST(mlp.latest_yes_price AS DECIMAL(18, 10)) >= 0.10 AND CAST(mlp.latest_yes_price AS DECIMAL(18, 10)) <= 0.90)
    )
"""
DEFAULT_ACTIVE_MARKET_RECENT_TRADE_SQL = "COALESCE(stats_24h.last_trade_at, mlp.latest_trade_at) >= ?"
SNAPSHOT_PREWARM_INTERVAL_SECONDS = 15
_PREWARM_LAST_RUN_LOCK = threading.Lock()
_PREWARM_LAST_RUN: Dict[str, float] = {}
_DASHBOARD_REFRESH_LOCK = threading.Lock()
_DASHBOARD_REFRESHING = False


def _service_callable(
    context: Mapping[str, Any],
    name: str,
) -> Callable[..., Any]:
    return cast(Callable[..., Any], resolve_service_callable(context, name))


@dataclass(frozen=True)
class DashboardBuildDependencies:
    fetch_market_status: Callable[..., Any]
    fetch_trade_volume: Callable[..., Any]
    fetch_recent_markets: Callable[..., Any]
    fetch_trade_window_bounds: Callable[..., Any]
    fetch_trade_count_estimate: Callable[..., Any]
    query_one: Callable[..., Any]
    iso_days_before: Callable[..., Any]
    utc_now_iso: Callable[..., Any]
    recent_trade_window: int
    cache_ttl_seconds: int

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> DashboardBuildDependencies:
        return cls(
            fetch_market_status=_service_callable(
                context,
                "fetch_dashboard_market_status",
            ),
            fetch_trade_volume=_service_callable(
                context,
                "fetch_dashboard_trade_volume",
            ),
            fetch_recent_markets=_service_callable(
                context,
                "fetch_dashboard_recent_markets",
            ),
            fetch_trade_window_bounds=_service_callable(
                context,
                "fetch_recent_trade_window_bounds",
            ),
            fetch_trade_count_estimate=_service_callable(
                context,
                "fetch_trade_count_estimate",
            ),
            query_one=_service_callable(context, "query_one"),
            iso_days_before=_service_callable(context, "iso_days_before"),
            utc_now_iso=_service_callable(context, "utc_now_iso"),
            recent_trade_window=int(
                resolve_service_value(context, "RECENT_TRADE_WINDOW", 0)
            ),
            cache_ttl_seconds=int(
                resolve_service_value(context, "DASHBOARD_CACHE_TTL_SECONDS", 0)
            ),
        )


@dataclass(frozen=True)
class DashboardCacheDependencies:
    source: Mapping[str, Any] = field(repr=False)
    application: Any
    snapshot_store: Any
    cache: dict[str, Any]
    cache_lock: Any
    threading_module: Any
    get_cached_json: Callable[..., Any]
    set_cached_json: Callable[..., Any]
    utc_now_iso: Callable[..., Any]
    recent_trade_window: int
    cache_ttl_seconds: int

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> DashboardCacheDependencies:
        return cls(
            source=context,
            application=resolve_service_value(context, "app"),
            snapshot_store=resolve_service_value(context, "SNAPSHOT_STORE"),
            cache=cast(
                dict[str, Any],
                resolve_service_value(context, "_dashboard_cache", {}),
            ),
            cache_lock=resolve_service_value(context, "_dashboard_cache_lock"),
            threading_module=resolve_service_value(context, "threading"),
            get_cached_json=_service_callable(context, "get_cached_json"),
            set_cached_json=_service_callable(context, "set_cached_json"),
            utc_now_iso=_service_callable(context, "utc_now_iso"),
            recent_trade_window=int(
                resolve_service_value(context, "RECENT_TRADE_WINDOW", 0)
            ),
            cache_ttl_seconds=int(
                resolve_service_value(context, "DASHBOARD_CACHE_TTL_SECONDS", 0)
            ),
        )


@dataclass(frozen=True)
class BootstrapCoreDependencies:
    application: Any
    commodity_symbols: Sequence[Any]
    finance_runtime_ttl_seconds: int
    query_all: Callable[..., Any]
    query_one: Callable[..., Any]
    utc_now_iso: Callable[..., Any]
    utc_date_days_ago: Callable[..., Any]
    parse_json_list: Callable[..., Any]
    get_gamma_active_market_filter: Callable[..., Any]
    enrich_market_rows_with_runtime_prices: Callable[..., Any]
    get_bootstrap_component_cached: Callable[..., Any]
    get_market_groups_payload: Callable[..., Any]
    get_market_by_id: Callable[..., Any]
    normalize_market: Callable[..., Any]
    get_trades_by_market_id: Callable[..., Any]
    get_oracle_events_by_market_id: Callable[..., Any]
    table_exists: Callable[..., Any]
    get_related_content_by_market_id: Callable[..., Any]
    get_recent_trades_snapshot: Callable[..., Any]
    get_recent_oracle_snapshot: Callable[..., Any]
    get_latest_content_snapshot: Callable[..., Any]
    get_market_group_snapshot: Callable[..., Any]
    build_system_health_payload: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> BootstrapCoreDependencies:
        return cls(
            application=resolve_service_value(context, "app"),
            commodity_symbols=cast(
                Sequence[Any],
                resolve_service_value(context, "COMMODITY_SYMBOLS", ()),
            ),
            finance_runtime_ttl_seconds=int(
                resolve_service_value(context, "FINANCE_RUNTIME_TTL_SECONDS", 0)
            ),
            query_all=_service_callable(context, "query_all"),
            query_one=_service_callable(context, "query_one"),
            utc_now_iso=_service_callable(context, "utc_now_iso"),
            utc_date_days_ago=_service_callable(context, "utc_date_days_ago"),
            parse_json_list=_service_callable(context, "parse_json_list"),
            get_gamma_active_market_filter=_service_callable(
                context,
                "get_gamma_active_market_filter",
            ),
            enrich_market_rows_with_runtime_prices=_service_callable(
                context,
                "enrich_market_rows_with_runtime_prices",
            ),
            get_bootstrap_component_cached=_service_callable(
                context,
                "get_bootstrap_component_cached",
            ),
            get_market_groups_payload=_service_callable(
                context,
                "get_market_groups_payload",
            ),
            get_market_by_id=_service_callable(context, "get_market_by_id"),
            normalize_market=_service_callable(context, "normalize_market"),
            get_trades_by_market_id=_service_callable(
                context,
                "get_trades_by_market_id",
            ),
            get_oracle_events_by_market_id=_service_callable(
                context,
                "get_oracle_events_by_market_id",
            ),
            table_exists=_service_callable(context, "table_exists"),
            get_related_content_by_market_id=_service_callable(
                context,
                "get_related_content_by_market_id",
            ),
            get_recent_trades_snapshot=_service_callable(
                context,
                "get_recent_trades_snapshot",
            ),
            get_recent_oracle_snapshot=_service_callable(
                context,
                "get_recent_oracle_snapshot",
            ),
            get_latest_content_snapshot=_service_callable(
                context,
                "get_latest_content_snapshot",
            ),
            get_market_group_snapshot=_service_callable(
                context,
                "get_market_group_snapshot",
            ),
            build_system_health_payload=_service_callable(
                context,
                "build_system_health_payload",
            ),
        )


@dataclass(frozen=True)
class BootstrapCacheDependencies:
    source: Mapping[str, Any] = field(repr=False)
    application: Any
    snapshot_store: Any
    cache: dict[str, Any]
    cache_lock: Any
    threading_module: Any
    get_cached_json: Callable[..., Any]
    set_cached_json: Callable[..., Any]
    cache_ttl_seconds: int
    component_ttl_seconds: int

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> BootstrapCacheDependencies:
        return cls(
            source=context,
            application=resolve_service_value(context, "app"),
            snapshot_store=resolve_service_value(context, "SNAPSHOT_STORE"),
            cache=cast(
                dict[str, Any],
                resolve_service_value(context, "_bootstrap_cache", {}),
            ),
            cache_lock=resolve_service_value(context, "_bootstrap_cache_lock"),
            threading_module=resolve_service_value(context, "threading"),
            get_cached_json=_service_callable(context, "get_cached_json"),
            set_cached_json=_service_callable(context, "set_cached_json"),
            cache_ttl_seconds=int(
                resolve_service_value(context, "BOOTSTRAP_CACHE_TTL_SECONDS", 0)
            ),
            component_ttl_seconds=int(
                resolve_service_value(context, "BOOTSTRAP_COMPONENT_TTL_SECONDS", 0)
            ),
        )


@dataclass(frozen=True)
class BootstrapPrewarmDependencies:
    bootstrap: BootstrapCoreDependencies
    application: Any
    threading_module: Any
    commodity_symbols: Sequence[Any]
    finance_runtime_ttl_seconds: int
    signal_runtime_ttl_seconds: int
    snapshot_prewarm_enabled: bool
    get_market_groups_payload: Callable[..., Any]
    get_market_group_chart_payload: Callable[..., Any]
    get_market_focus_tile_payload: Callable[..., Any]
    get_market_group_snapshot: Callable[..., Any]
    get_bootstrap_component_cached: Callable[..., Any]
    get_active_markets_snapshot: Callable[..., Any]
    get_recent_oracle_snapshot: Callable[..., Any]
    get_recent_trades_snapshot: Callable[..., Any]
    get_finance_market_atlas_snapshot: Callable[..., Any]
    get_equity_event_command_snapshot: Callable[..., Any]
    get_onchain_tradfi_perp_radar_snapshot: Callable[..., Any]
    get_finance_liquidity_regime_snapshot: Callable[..., Any]
    get_whale_trades_snapshot: Callable[..., Any]
    get_suspicious_trades_snapshot: Callable[..., Any]
    get_alpha_signal_snapshot: Callable[..., Any]
    get_jin10_panel_snapshot: Callable[..., Any]
    get_bootstrap_payload_cached: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> BootstrapPrewarmDependencies:
        return cls(
            bootstrap=BootstrapCoreDependencies.from_context(context),
            application=resolve_service_value(context, "app"),
            threading_module=resolve_service_value(context, "threading"),
            commodity_symbols=cast(
                Sequence[Any],
                resolve_service_value(context, "COMMODITY_SYMBOLS", ()),
            ),
            finance_runtime_ttl_seconds=int(
                resolve_service_value(
                    context,
                    "FINANCE_RUNTIME_TTL_SECONDS",
                    0,
                )
            ),
            signal_runtime_ttl_seconds=int(
                resolve_service_value(
                    context,
                    "SIGNAL_RUNTIME_TTL_SECONDS",
                    0,
                )
            ),
            snapshot_prewarm_enabled=bool(
                resolve_service_value(
                    context,
                    "SNAPSHOT_PREWARM_ENABLED",
                    False,
                )
            ),
            get_market_groups_payload=_service_callable(
                context,
                "get_market_groups_payload",
            ),
            get_market_group_chart_payload=_service_callable(
                context,
                "get_market_group_chart_payload",
            ),
            get_market_focus_tile_payload=_service_callable(
                context,
                "get_market_focus_tile_payload",
            ),
            get_market_group_snapshot=_service_callable(
                context,
                "get_market_group_snapshot",
            ),
            get_bootstrap_component_cached=_service_callable(
                context,
                "get_bootstrap_component_cached",
            ),
            get_active_markets_snapshot=_service_callable(
                context,
                "get_active_markets_snapshot",
            ),
            get_recent_oracle_snapshot=_service_callable(
                context,
                "get_recent_oracle_snapshot",
            ),
            get_recent_trades_snapshot=_service_callable(
                context,
                "get_recent_trades_snapshot",
            ),
            get_finance_market_atlas_snapshot=_service_callable(
                context,
                "get_finance_market_atlas_snapshot",
            ),
            get_equity_event_command_snapshot=_service_callable(
                context,
                "get_equity_event_command_snapshot",
            ),
            get_onchain_tradfi_perp_radar_snapshot=_service_callable(
                context,
                "get_onchain_tradfi_perp_radar_snapshot",
            ),
            get_finance_liquidity_regime_snapshot=_service_callable(
                context,
                "get_finance_liquidity_regime_snapshot",
            ),
            get_whale_trades_snapshot=_service_callable(
                context,
                "get_whale_trades_snapshot",
            ),
            get_suspicious_trades_snapshot=_service_callable(
                context,
                "get_suspicious_trades_snapshot",
            ),
            get_alpha_signal_snapshot=_service_callable(
                context,
                "get_alpha_signal_snapshot",
            ),
            get_jin10_panel_snapshot=_service_callable(
                context,
                "get_jin10_panel_snapshot",
            ),
            get_bootstrap_payload_cached=_service_callable(
                context,
                "get_bootstrap_payload_cached",
            ),
        )


def build_dashboard_payload(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    return _build_dashboard_payload(DashboardBuildDependencies.from_context(ctx))


def _build_dashboard_payload(
    dependencies: DashboardBuildDependencies,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    last_24h = (now - timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    status_rows = dependencies.fetch_market_status(now_iso)
    active_markets = sum(int(row.get("value") or 0) for row in status_rows if row.get("name") in {"Active", "Proposed"})
    settlements_row = dependencies.query_one(
        """
        SELECT COUNT(*) AS settlements_24h
        FROM oracle_events
        WHERE event_status = 'settle' AND event_time >= ?
        """,
        (last_24h,),
    )
    trade_volume_rows = dependencies.fetch_trade_volume(
        dependencies.recent_trade_window
    )
    recent_rows = dependencies.fetch_recent_markets(
        now_iso,
        dependencies.recent_trade_window,
    )
    trade_window = dependencies.fetch_trade_window_bounds(
        dependencies.recent_trade_window
    )
    trade_count_estimate = dependencies.fetch_trade_count_estimate()
    latest_trade_ts = trade_window.get("latest_timestamp")
    earliest_trade_ts = trade_window.get("earliest_timestamp")
    coverage_7d_start = dependencies.iso_days_before(latest_trade_ts, 7)
    coverage_30d_start = dependencies.iso_days_before(latest_trade_ts, 30)
    return {
        "metrics": {
            "activeMarkets": active_markets,
            "totalTrades": int(trade_count_estimate.get("table_rows") or 0),
            "settlements24h": int(settlements_row.get("settlements_24h") or 0),
        },
        "volume7d": [{"day": str(row.get("day")) if row.get("day") is not None else None, "trade_count": int(row.get("trade_count") or 0)} for row in trade_volume_rows[-7:]],
        "volume30d": [{"day": str(row.get("day")) if row.get("day") is not None else None, "trade_count": int(row.get("trade_count") or 0)} for row in trade_volume_rows[-30:]],
        "statusShare": status_rows,
        "recentActiveMarkets": [
            {
                "id": row.get("id"),
                "localMarketId": row.get("id"),
                "gammaMarketId": row.get("gamma_market_id"),
                "slug": row.get("slug"),
                "title": row.get("title"),
                "tradeCount": int(row.get("trade_count") or 0),
                "lastTradeAt": row.get("last_trade_at"),
                "status": row.get("status"),
                "endDate": row.get("end_date"),
                "latestPrice": row.get("latest_price"),
            }
            for row in recent_rows
        ],
        "metadata": {
            "generatedAt": now_iso,
            "cacheTtlSeconds": dependencies.cache_ttl_seconds,
            "tradeWindowSize": dependencies.recent_trade_window,
            "tradeWindowEarliestTimestamp": earliest_trade_ts,
            "tradeWindowLatestTimestamp": latest_trade_ts,
            "tradeWindowSource": trade_window.get("source"),
            "tradeWindowCovers7d": bool(coverage_7d_start and earliest_trade_ts and earliest_trade_ts <= coverage_7d_start),
            "tradeWindowCovers30d": bool(coverage_30d_start and earliest_trade_ts and earliest_trade_ts <= coverage_30d_start),
            "totalTradesSource": "information_schema.table_rows",
            "totalTradesAutoIncrement": int(trade_count_estimate.get("auto_increment") or 0),
        },
    }


def _build_dashboard_fallback_payload(
    dependencies: DashboardCacheDependencies,
) -> Dict[str, Any]:
    now_iso = dependencies.utc_now_iso()
    return {
        "metrics": {"activeMarkets": 0, "totalTrades": 0, "settlements24h": 0},
        "volume7d": [],
        "volume30d": [],
        "statusShare": [],
        "recentActiveMarkets": [],
        "metadata": {
            "generatedAt": now_iso,
            "cacheTtlSeconds": dependencies.cache_ttl_seconds,
            "tradeWindowSize": dependencies.recent_trade_window,
            "tradeWindowEarliestTimestamp": None,
            "tradeWindowLatestTimestamp": None,
            "tradeWindowSource": "warming",
            "tradeWindowCovers7d": False,
            "tradeWindowCovers30d": False,
            "totalTradesSource": "warming",
            "totalTradesAutoIncrement": 0,
            "sourceMode": "fast-fallback",
            "status": "warming",
        },
    }


def _schedule_dashboard_refresh(
    dependencies: DashboardCacheDependencies,
    reason: str,
) -> None:
    global _DASHBOARD_REFRESHING
    with _DASHBOARD_REFRESH_LOCK:
        if _DASHBOARD_REFRESHING:
            return
        _DASHBOARD_REFRESHING = True

    def refresh() -> None:
        global _DASHBOARD_REFRESHING
        started_at = time.perf_counter()
        try:
            dependencies.application.logger.info(
                "dashboard-cache refresh-start reason=%s",
                reason,
            )
            payload = build_dashboard_payload(dependencies.source)
            dependencies.snapshot_store.set(
                "snapshot:dashboard",
                "dashboard",
                payload,
                dependencies.cache_ttl_seconds,
            )
            dependencies.cache["value"] = payload
            dependencies.cache["expires_at"] = (
                time.monotonic() + dependencies.cache_ttl_seconds
            )
            dependencies.set_cached_json(
                "dashboard",
                "dashboard",
                payload,
                dependencies.cache_ttl_seconds,
            )
            dependencies.application.logger.info(
                "dashboard-cache refresh-done reason=%s duration_ms=%.2f",
                reason,
                (time.perf_counter() - started_at) * 1000,
            )
        except Exception:
            dependencies.application.logger.exception(
                "dashboard-cache refresh-failed reason=%s",
                reason,
            )
        finally:
            with _DASHBOARD_REFRESH_LOCK:
                _DASHBOARD_REFRESHING = False

    dependencies.threading_module.Thread(
        target=refresh,
        name="dashboard-refresh",
        daemon=True,
    ).start()


def get_dashboard_payload_cached(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    dependencies = DashboardCacheDependencies.from_context(ctx)
    redis_cache_key = "dashboard"
    redis_payload = dependencies.get_cached_json("dashboard", redis_cache_key)
    if redis_payload is not None:
        dependencies.application.logger.info("dashboard-cache redis-hit")
        return redis_payload
    now_monotonic = time.monotonic()
    cached = dependencies.cache.get("value")
    if (
        cached is not None
        and dependencies.cache.get("expires_at", 0.0) > now_monotonic
    ):
        dependencies.application.logger.info(
            "dashboard-cache hit ttl_remaining_ms=%.2f",
            (dependencies.cache.get("expires_at", 0.0) - now_monotonic) * 1000,
        )
        return cached
    with dependencies.cache_lock:
        cached = dependencies.cache.get("value")
        if (
            cached is not None
            and dependencies.cache.get("expires_at", 0.0) > time.monotonic()
        ):
            dependencies.application.logger.info("dashboard-cache hit-after-lock")
            return cached
        dependencies.application.logger.info(
            "dashboard-cache rebuild window_size=%s ttl_seconds=%s",
            dependencies.recent_trade_window,
            dependencies.cache_ttl_seconds,
        )
        snapshot_payload = dependencies.snapshot_store.get(
            "snapshot:dashboard",
            redis_cache_key,
        )
        if snapshot_payload is not None:
            payload = snapshot_payload
        else:
            stale_payload = dependencies.snapshot_store.get_stale(
                "snapshot:dashboard",
                redis_cache_key,
            )
            if stale_payload is not None:
                dependencies.application.logger.info(
                    "dashboard-cache stale-hit scheduling_refresh=true"
                )
                _schedule_dashboard_refresh(dependencies, "stale-hit")
                payload = stale_payload
            else:
                dependencies.application.logger.info(
                    "dashboard-cache cold-miss returning_fallback=true "
                    "scheduling_refresh=true"
                )
                _schedule_dashboard_refresh(dependencies, "cold-miss")
                payload = _build_dashboard_fallback_payload(dependencies)
        dependencies.cache["value"] = payload
        dependencies.cache["expires_at"] = (
            time.monotonic() + dependencies.cache_ttl_seconds
        )
        dependencies.set_cached_json(
            "dashboard",
            redis_cache_key,
            payload,
            dependencies.cache_ttl_seconds,
        )
        return payload


def _status_priority(status: Any) -> int:
    normalized = str(status or "").strip().lower()
    if normalized == "active":
        return 2
    if normalized == "proposed":
        return 1
    return 0


def _safe_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_tradeable_bootstrap_row(row: Dict[str, Any]) -> bool:
    trade_count = _safe_int(row.get("trade_count_24h"))
    try:
        volume_24h = float(row.get("volume_24h") or 0)
    except (TypeError, ValueError):
        volume_24h = 0.0
    if trade_count <= 0 and volume_24h <= 0:
        return False
    try:
        latest_price = float(row.get("latest_price")) if row.get("latest_price") not in (None, "") else None
    except (TypeError, ValueError):
        latest_price = None
    if latest_price is None:
        return True
    return 0.01 < latest_price < 0.99


def _normalize_bootstrap_market_item(
    dependencies: BootstrapCoreDependencies,
    row: Dict[str, Any],
) -> Dict[str, Any]:
    yes_token_id = str(row.get("yes_token_id") or "").strip()
    no_token_id = str(row.get("no_token_id") or "").strip()
    return {
        "id": row.get("id"),
        "localMarketId": row.get("id"),
        "gammaMarketId": row.get("gamma_market_id"),
        "slug": row.get("slug"),
        "title": row.get("title"),
        "conditionId": row.get("condition_id"),
        "questionId": row.get("question_id"),
        "endDate": row.get("end_date"),
        "createdAt": row.get("created_at"),
        "latestPrice": row.get("latest_price"),
        "status": row.get("status"),
        "category": row.get("category") or "Uncategorized",
        "tags": dependencies.parse_json_list(row.get("tags")),
        "outcomeCount": int(bool(yes_token_id)) + int(bool(no_token_id)),
        "volume24h": row.get("volume_24h"),
        "tradeCount24h": _safe_int(row.get("trade_count_24h")),
        "change24h": row.get("change_24h"),
        "lastTradeAt": row.get("last_trade_at") or row.get("latest_trade_at"),
    }


def _build_bootstrap_active_markets_payload(
    dependencies: BootstrapCoreDependencies,
    page_size: int = 20,
) -> Dict[str, Any]:
    now_iso = dependencies.utc_now_iso()
    recent_trade_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    recent_14d_iso = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat().replace("+00:00", "Z")
    recent_30d_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    raw_limit = max(page_size * 6, 120)
    candidate_rows = dependencies.query_all(
        """
        SELECT
            m.id,
            m.gamma_market_id,
            m.slug,
            m.title,
            m.condition_id,
            m.question_id,
            m.yes_token_id,
            m.no_token_id,
            m.category,
            m.tags,
            m.end_date,
            m.created_at,
            mlp.latest_yes_price AS latest_price,
            mlp.latest_trade_at,
            stats_24h.trade_count_24h,
            stats_24h.volume_24h,
            stats_24h.last_trade_at
        FROM markets m
        LEFT JOIN market_latest_prices mlp ON mlp.market_id = m.id
        LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
        LEFT JOIN (
            SELECT
                market_id,
                SUM(trade_count) AS trade_count_24h,
                SUM(volume_notional) AS volume_24h,
                MAX(last_trade_at) AS last_trade_at
            FROM market_trade_daily_stats
            WHERE trade_date >= ?
            GROUP BY market_id
        ) stats_24h ON stats_24h.market_id = m.id
        WHERE (m.end_date IS NULL OR m.end_date >= ?)
          AND COALESCE(mss.has_settle, FALSE) = FALSE
          AND COALESCE(mss.has_propose, FALSE) = FALSE
          AND COALESCE(mss.settlement_code, 0) = 0
          AND """ + DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL + """
          AND """ + DEFAULT_ACTIVE_MARKET_ACTIVITY_SQL + """
          AND """ + DEFAULT_ACTIVE_MARKET_PRICE_SQL + """
          AND """ + DEFAULT_ACTIVE_MARKET_RECENT_TRADE_SQL + """
        ORDER BY
            CASE
                WHEN m.created_at >= ? THEN 0
                WHEN m.created_at >= ? THEN 1
                ELSE 2
            END ASC,
            m.created_at DESC,
            COALESCE(stats_24h.trade_count_24h, 0) DESC,
            COALESCE(stats_24h.volume_24h, 0) DESC,
            stats_24h.last_trade_at DESC,
            mlp.latest_trade_at DESC
        LIMIT ?
        """,
        (
            dependencies.utc_date_days_ago(1),
            now_iso,
            recent_trade_cutoff,
            recent_14d_iso,
            recent_30d_iso,
            raw_limit,
        ),
    )
    gamma_active_payload = dependencies.get_gamma_active_market_filter() or {}
    gamma_condition_ids = {
        str(value or "").strip().lower()
        for value in (gamma_active_payload.get("conditionIds") or [])
        if str(value or "").strip()
    }
    gamma_slugs = {
        str(value or "").strip().lower()
        for value in (gamma_active_payload.get("slugs") or [])
        if str(value or "").strip()
    }
    if gamma_condition_ids or gamma_slugs:
        filtered_candidates: List[Dict[str, Any]] = []
        for row in candidate_rows:
            condition_id = str(row.get("condition_id") or "").strip().lower()
            slug = str(row.get("slug") or "").strip().lower()
            if condition_id and condition_id in gamma_condition_ids:
                filtered_candidates.append(row)
                continue
            if slug and slug in gamma_slugs:
                filtered_candidates.append(row)
        candidate_rows = filtered_candidates
    candidate_market_ids = [int(row["id"]) for row in candidate_rows if row.get("id") is not None]
    status_map: Dict[int, Dict[str, bool]] = {}
    if candidate_market_ids:
        placeholders = ", ".join("?" for _ in candidate_market_ids)
        status_rows = dependencies.query_all(
            f"""
            SELECT
                market_id,
                MAX(CASE WHEN event_status = 'settle' THEN 1 ELSE 0 END) AS has_settle,
                MAX(CASE WHEN event_status = 'propose' THEN 1 ELSE 0 END) AS has_propose
            FROM oracle_events
            WHERE market_id IN ({placeholders})
            GROUP BY market_id
            """,
            candidate_market_ids,
        )
        status_map = {
            int(row["market_id"]): {"has_settle": bool(row.get("has_settle")), "has_propose": bool(row.get("has_propose"))}
            for row in status_rows
            if row.get("market_id") is not None
        }
    rows: List[Dict[str, Any]] = []
    for row in candidate_rows:
        market_id = row.get("id")
        if market_id is None:
            continue
        flags = status_map.get(int(market_id), {})
        if flags.get("has_settle") or flags.get("has_propose"):
            continue
        normalized = dict(row)
        normalized["status"] = "Active"
        rows.append(normalized)
        if len(rows) >= max(page_size * 3, page_size):
            break
    rows = dependencies.enrich_market_rows_with_runtime_prices(
        rows,
        max_updates=len(rows),
        force_refresh=True,
    )
    rows = [row for row in rows if _is_tradeable_bootstrap_row(row)]
    rows = rows[:page_size]
    return {
        "rows": rows,
        "items": [
            _normalize_bootstrap_market_item(dependencies, row) for row in rows
        ],
    }


def _select_featured_market_id(preview_rows: List[Dict[str, Any]]) -> Optional[int]:
    best_market_id: Optional[int] = None
    best_score: Optional[tuple] = None
    for row in preview_rows:
        market_id = row.get("id")
        if market_id is None:
            continue
        try:
            numeric_market_id = int(market_id)
        except (TypeError, ValueError):
            continue
        score = (
            _status_priority(row.get("status")),
            int(bool(str(row.get("yes_token_id") or "").strip()) and bool(str(row.get("no_token_id") or "").strip())),
            int(bool(row.get("last_trade_at") or row.get("latest_trade_at"))),
            _safe_float(row.get("volume_24h")),
            _safe_int(row.get("trade_count_24h")),
            str(row.get("last_trade_at") or row.get("latest_trade_at") or ""),
            str(row.get("end_date") or ""),
            str(row.get("created_at") or ""),
        )
        if best_score is None or score > best_score:
            best_score = score
            best_market_id = numeric_market_id
    return best_market_id


def _get_fallback_featured_market_id(
    dependencies: BootstrapCoreDependencies,
) -> Optional[int]:
    now_iso = dependencies.utc_now_iso()
    recent_trade_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat().replace("+00:00", "Z")
    row = dependencies.query_one(
        """
        WITH settled_markets AS (
            SELECT DISTINCT market_id
            FROM oracle_events
            WHERE event_status = 'settle' AND market_id IS NOT NULL
        ),
        proposed_markets AS (
            SELECT DISTINCT market_id
            FROM oracle_events
            WHERE event_status = 'propose' AND market_id IS NOT NULL
        ),
        stats_24h AS (
            SELECT
                market_id,
                SUM(trade_count) AS trade_count_24h,
                SUM(volume_notional) AS volume_24h,
                MAX(last_trade_at) AS last_trade_at
            FROM market_trade_daily_stats
            WHERE trade_date >= ?
            GROUP BY market_id
        )
        SELECT m.id
        FROM markets m
        LEFT JOIN settled_markets settled ON settled.market_id = m.id
        LEFT JOIN proposed_markets proposed ON proposed.market_id = m.id
        LEFT JOIN stats_24h ON stats_24h.market_id = m.id
        LEFT JOIN market_latest_prices mlp ON mlp.market_id = m.id
        LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
        WHERE settled.market_id IS NULL
          AND COALESCE(mss.has_settle, FALSE) = FALSE
          AND (m.end_date IS NULL OR m.end_date >= ?)
          AND """ + DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL + """
          AND """ + DEFAULT_ACTIVE_MARKET_ACTIVITY_SQL + """
          AND """ + DEFAULT_ACTIVE_MARKET_PRICE_SQL + """
          AND """ + DEFAULT_ACTIVE_MARKET_RECENT_TRADE_SQL + """
        ORDER BY
            CASE WHEN proposed.market_id IS NULL THEN 1 ELSE 0 END DESC,
            CASE
                WHEN COALESCE(NULLIF(TRIM(m.yes_token_id), ''), '') <> ''
                 AND COALESCE(NULLIF(TRIM(m.no_token_id), ''), '') <> '' THEN 1
                ELSE 0
            END DESC,
            COALESCE(stats_24h.volume_24h, 0) DESC,
            COALESCE(stats_24h.trade_count_24h, 0) DESC,
            COALESCE(stats_24h.last_trade_at, mlp.latest_trade_at, m.created_at) DESC
        LIMIT 1
        """,
        (dependencies.utc_date_days_ago(1), now_iso, recent_trade_cutoff),
    )
    market_id = row.get("id") if row else None
    try:
        return int(market_id) if market_id is not None else None
    except (TypeError, ValueError):
        return None


def _build_bootstrap_price_preview(
    dependencies: BootstrapCoreDependencies,
    market_id: int,
) -> Dict[str, Any]:
    summary_row = dependencies.query_one(
        """
        SELECT
            market_id,
            latest_price,
            latest_yes_price,
            latest_no_price,
            latest_trade_at
        FROM market_latest_prices
        WHERE market_id = ?
        LIMIT 1
        """,
        (market_id,),
    )
    stats_row = dependencies.query_one(
        """
        SELECT
            SUM(trade_count) AS trade_count_24h,
            SUM(volume_notional) AS volume_24h,
            MAX(last_trade_at) AS updated_at
        FROM market_trade_daily_stats
        WHERE market_id = ? AND trade_date >= ?
        """,
        (market_id, dependencies.utc_date_days_ago(1)),
    )
    return {
        "marketId": market_id,
        "localMarketId": market_id,
        "latestPrice": summary_row.get("latest_price") if summary_row else None,
        "latestYesPrice": summary_row.get("latest_yes_price") if summary_row else None,
        "latestNoPrice": summary_row.get("latest_no_price") if summary_row else None,
        "change1h": None,
        "change24h": None,
        "volume24h": stats_row.get("volume_24h") if stats_row else None,
        "tradeCount24h": _safe_int(stats_row.get("trade_count_24h") if stats_row else 0),
        "updatedAt": (summary_row or {}).get("latest_trade_at") or (stats_row or {}).get("updated_at"),
    }


def _get_bootstrap_latest_content_preview(
    dependencies: BootstrapCoreDependencies,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    if not dependencies.table_exists("content_items"):
        return []
    return dependencies.get_latest_content_snapshot(limit=limit).get("items", [])


def _store_bootstrap_payload(
    dependencies: BootstrapCacheDependencies,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    expires_at = time.monotonic() + dependencies.cache_ttl_seconds
    with dependencies.cache_lock:
        dependencies.cache["value"] = payload
        dependencies.cache["expires_at"] = expires_at
        dependencies.cache["refresh_in_progress"] = False
    dependencies.snapshot_store.set(
        BOOTSTRAP_SNAPSHOT_NAMESPACE,
        BOOTSTRAP_CACHE_KEY,
        payload,
        dependencies.cache_ttl_seconds,
    )
    dependencies.set_cached_json(
        "bootstrap",
        BOOTSTRAP_CACHE_KEY,
        payload,
        dependencies.cache_ttl_seconds,
    )
    return payload


def _refresh_bootstrap_payload(
    dependencies: BootstrapCacheDependencies,
    reason: str,
) -> Optional[Dict[str, Any]]:
    started_at = time.perf_counter()
    dependencies.application.logger.info(
        "bootstrap-cache refresh-start reason=%s",
        reason,
    )
    try:
        payload = build_bootstrap_payload(dependencies.source)
    except Exception:
        with dependencies.cache_lock:
            dependencies.cache["refresh_in_progress"] = False
        dependencies.application.logger.exception(
            "bootstrap-cache refresh-failed reason=%s",
            reason,
        )
        return None
    _store_bootstrap_payload(dependencies, payload)
    dependencies.application.logger.info(
        "bootstrap-cache refresh-done reason=%s duration_ms=%.2f",
        reason,
        (time.perf_counter() - started_at) * 1000,
    )
    return payload


def _schedule_bootstrap_refresh(
    dependencies: BootstrapCacheDependencies,
    reason: str,
) -> None:
    thread = dependencies.threading_module.Thread(
        target=lambda: _refresh_bootstrap_payload(dependencies, reason),
        name="polydata-bootstrap-refresh",
        daemon=True,
    )
    thread.start()


def _claim_prewarm_slot(task_name: str, interval_seconds: int) -> bool:
    now = time.monotonic()
    with _PREWARM_LAST_RUN_LOCK:
        last_run = _PREWARM_LAST_RUN.get(task_name, 0.0)
        if now - last_run < interval_seconds:
            return False
        _PREWARM_LAST_RUN[task_name] = now
    return True


def build_bootstrap_payload(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    return _build_bootstrap_payload(BootstrapCoreDependencies.from_context(ctx))


def _build_bootstrap_payload(
    dependencies: BootstrapCoreDependencies,
) -> Dict[str, Any]:
    preview_payload = dependencies.get_bootstrap_component_cached(
        "active-markets-preview-v11",
        lambda: _build_bootstrap_active_markets_payload(
            dependencies,
            page_size=20,
        ),
        ttl_seconds=15,
    )
    preview_rows = preview_payload.get("rows", [])
    active_markets_preview = preview_payload.get("items", [])
    try:
        active_market_groups_preview = dependencies.get_market_groups_payload(
            query="",
            page=1,
            page_size=20,
            sort="active",
        ).get("items", [])
    except Exception:
        dependencies.application.logger.exception(
            "bootstrap active market groups preview failed"
        )
        active_market_groups_preview = []
    featured_market_id = _select_featured_market_id(preview_rows)
    if featured_market_id is None:
        featured_market_id = _get_fallback_featured_market_id(dependencies)
    if featured_market_id is None:
        now_iso = dependencies.utc_now_iso()
        row = dependencies.query_one(
            """
            SELECT m.id
            FROM markets m
            LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
            WHERE (m.end_date IS NULL OR m.end_date >= ?)
              AND COALESCE(mss.has_settle, FALSE) = FALSE
              AND """ + DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL + """
            ORDER BY m.created_at DESC NULLS LAST, m.id DESC
            LIMIT 1
            """,
            (now_iso,),
        )
        featured_market_id = row.get("id") if row else None

    featured_market = (
        dependencies.get_bootstrap_component_cached(
            f"featured-market:{int(featured_market_id)}",
            lambda: dependencies.normalize_market(
                dependencies.get_market_by_id(int(featured_market_id))
            ),
        )
        if featured_market_id is not None
        else None
    )
    recent_trade_preview = (
        dependencies.get_bootstrap_component_cached(
            f"recent-trades:{int(featured_market_id)}",
            lambda: dependencies.get_trades_by_market_id(
                int(featured_market_id),
                limit=12,
                offset=0,
            ),
            ttl_seconds=30,
        )
        if featured_market_id is not None
        else []
    )
    oracle_preview = (
        dependencies.get_bootstrap_component_cached(
            f"oracle-preview:{int(featured_market_id)}",
            lambda: dependencies.get_oracle_events_by_market_id(
                int(featured_market_id)
            )[:8],
            ttl_seconds=60,
        )
        if featured_market_id is not None
        else []
    )
    if (
        featured_market_id is not None
        and dependencies.table_exists("content_items")
        and dependencies.table_exists("content_links")
    ):
        content_preview = dependencies.get_bootstrap_component_cached(
            f"content-preview:{int(featured_market_id)}",
            lambda: dependencies.get_related_content_by_market_id(
                int(featured_market_id),
                limit=6,
            ).get("items", []),
            ttl_seconds=300,
        )
    else:
        content_preview = []
    global_trades_preview = dependencies.get_recent_trades_snapshot(limit=18)
    global_oracle_preview = dependencies.get_recent_oracle_snapshot(limit=12)
    latest_content_preview = dependencies.get_bootstrap_component_cached(
        "latest-content-preview-v1",
        lambda: {
            "items": _get_bootstrap_latest_content_preview(
                dependencies,
                limit=8,
            )
        },
        ttl_seconds=300,
    ).get("items", [])
    commodities_preview = dependencies.get_bootstrap_component_cached(
        "commodities-preview-v1",
        lambda: dependencies.get_market_group_snapshot(
            dependencies.commodity_symbols,
            kind="commodities",
        ),
        ttl_seconds=dependencies.finance_runtime_ttl_seconds,
    )
    system_health = dependencies.get_bootstrap_component_cached(
        "system-health",
        dependencies.build_system_health_payload,
        ttl_seconds=15,
    )
    return {
        "generatedAt": dependencies.utc_now_iso(),
        "defaultWorkspace": {
            "name": "Hackathon Demo",
            "panels": get_default_panel_ids(),
        },
        "featuredMarket": featured_market,
        "activeMarketsPreview": active_markets_preview,
        "activeMarketGroupsPreview": active_market_groups_preview,
        "globalTradesPreview": global_trades_preview,
        "globalOraclePreview": global_oracle_preview,
        "latestContentPreview": latest_content_preview,
        "commoditiesPreview": commodities_preview,
        "recentTradesPreview": recent_trade_preview,
        "oraclePreview": oracle_preview,
        "contentPreview": content_preview,
        "pricePreview": (
            _build_bootstrap_price_preview(
                dependencies,
                int(featured_market_id),
            )
            if featured_market_id is not None
            else None
        ),
        "systemHealth": system_health,
    }


def get_bootstrap_payload_cached(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    dependencies = BootstrapCacheDependencies.from_context(ctx)
    redis_payload = dependencies.get_cached_json("bootstrap", BOOTSTRAP_CACHE_KEY)
    if redis_payload is not None:
        dependencies.application.logger.info("bootstrap-cache redis-hit")
        # Redis is already the fast, seeded source for this request. Mirroring
        # every hit back into SQLite turned a read-only endpoint into a writer
        # and allowed cache contention to consume the browser timeout budget.
        with dependencies.cache_lock:
            dependencies.cache["value"] = redis_payload
            dependencies.cache["expires_at"] = (
                time.monotonic() + dependencies.cache_ttl_seconds
            )
        return redis_payload
    now_monotonic = time.monotonic()
    cached = dependencies.cache.get("value")
    if (
        cached is not None
        and dependencies.cache.get("expires_at", 0.0) > now_monotonic
    ):
        dependencies.application.logger.info(
            "bootstrap-cache hit ttl_remaining_ms=%.2f",
            (dependencies.cache.get("expires_at", 0.0) - now_monotonic) * 1000,
        )
        return cached
    with dependencies.cache_lock:
        cached = dependencies.cache.get("value")
        if (
            cached is not None
            and dependencies.cache.get("expires_at", 0.0) > time.monotonic()
        ):
            dependencies.application.logger.info("bootstrap-cache hit-after-lock")
            return cached
        stale_payload = cached or dependencies.snapshot_store.get_stale(
            BOOTSTRAP_SNAPSHOT_NAMESPACE,
            BOOTSTRAP_CACHE_KEY,
        )
        if stale_payload is not None:
            dependencies.application.logger.info(
                "bootstrap-cache stale-hit scheduling_refresh=true"
            )
            if cached is None:
                dependencies.cache["value"] = stale_payload
                dependencies.cache["expires_at"] = now_monotonic
            schedule_refresh = not dependencies.cache.get("refresh_in_progress")
        else:
            schedule_refresh = False
        if stale_payload is not None:
            if schedule_refresh:
                dependencies.cache["refresh_in_progress"] = True
            else:
                dependencies.application.logger.info(
                    "bootstrap-cache stale-hit refresh_already_in_progress=true"
                )
        else:
            dependencies.application.logger.info(
                "bootstrap-cache cold-rebuild ttl_seconds=%s component_ttl_seconds=%s",
                dependencies.cache_ttl_seconds,
                dependencies.component_ttl_seconds,
            )
    if stale_payload is not None:
        if schedule_refresh:
            _schedule_bootstrap_refresh(dependencies, "stale-hit")
        return stale_payload
    payload = _refresh_bootstrap_payload(dependencies, "cold-miss")
    if payload is not None:
        return payload
    cached = dependencies.cache.get("value")
    if cached is not None:
        return cached
    raise RuntimeError("bootstrap payload refresh failed")


def prewarm_snapshot_payloads(ctx: Mapping[str, Any]) -> None:
    _prewarm_snapshot_payloads(
        BootstrapPrewarmDependencies.from_context(ctx),
    )


def _prewarm_active_market_focus_tiles(
    dependencies: BootstrapPrewarmDependencies,
) -> None:
    payload = dependencies.get_active_markets_snapshot(page_size=80)
    items = payload.get("items") if isinstance(payload, dict) else []
    limit = max(1, min(int(os.environ.get("POLYDATA_PREWARM_MARKET_FOCUS_LIMIT", "16")), 40))
    for market in (items or [])[:limit]:
        market_id = market.get("id") if isinstance(market, dict) else None
        if market_id is not None:
            dependencies.get_market_focus_tile_payload(int(market_id))


def _prewarm_snapshot_payloads(
    dependencies: BootstrapPrewarmDependencies,
) -> None:
    def _prewarm_active_market_group_charts() -> None:
        enabled = str(
            os.environ.get("POLYDATA_PREWARM_MARKET_GROUP_CHARTS") or ""
        ).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not enabled:
            return
        payload = dependencies.get_market_groups_payload(
            query="",
            page=1,
            page_size=80,
            sort="active",
        )
        for group in (payload.get("items") or [])[:8]:
            event_id = group.get("eventId")
            if event_id is not None:
                dependencies.get_market_group_chart_payload(
                    str(event_id),
                    "1d",
                )

    tasks = [
        (
            "commodities",
            dependencies.finance_runtime_ttl_seconds,
            lambda: dependencies.get_market_group_snapshot(
                dependencies.commodity_symbols,
                kind="commodities",
            ),
        ),
        (
            "bootstrap:active-markets-preview",
            15,
            lambda: dependencies.get_bootstrap_component_cached(
                "active-markets-preview-v11",
                lambda: _build_bootstrap_active_markets_payload(
                    dependencies.bootstrap,
                    page_size=20,
                ),
                ttl_seconds=15,
            ),
        ),
        (
            "markets:80",
            15,
            lambda: dependencies.get_active_markets_snapshot(page_size=80),
        ),
        (
            "markets:focus-tiles",
            30,
            lambda: _prewarm_active_market_focus_tiles(dependencies),
        ),
        (
            "oracle:12",
            15,
            lambda: dependencies.get_recent_oracle_snapshot(limit=12),
        ),
        (
            "oracle:16",
            15,
            lambda: dependencies.get_recent_oracle_snapshot(limit=16),
        ),
        (
            "trades:18",
            15,
            lambda: dependencies.get_recent_trades_snapshot(limit=18),
        ),
        (
            "trades:24",
            15,
            lambda: dependencies.get_recent_trades_snapshot(limit=24),
        ),
        (
            "content:8",
            300,
            lambda: dependencies.get_bootstrap_component_cached(
                "latest-content-preview-v1",
                lambda: {
                    "items": _get_bootstrap_latest_content_preview(
                        dependencies.bootstrap,
                        limit=8,
                    )
                },
                ttl_seconds=300,
            ),
        ),
        (
            "content:12",
            300,
            lambda: dependencies.get_bootstrap_component_cached(
                "latest-content-preview-v2",
                lambda: {
                    "items": _get_bootstrap_latest_content_preview(
                        dependencies.bootstrap,
                        limit=12,
                    )
                },
                ttl_seconds=300,
            ),
        ),
        (
            "bootstrap:commodities-preview",
            dependencies.finance_runtime_ttl_seconds,
            lambda: dependencies.get_bootstrap_component_cached(
                "commodities-preview-v1",
                lambda: dependencies.get_market_group_snapshot(
                    dependencies.commodity_symbols,
                    kind="commodities",
                ),
                ttl_seconds=dependencies.finance_runtime_ttl_seconds,
            ),
        ),
        (
            "market-groups:active:80",
            15,
            lambda: dependencies.get_market_groups_payload(
                query="",
                page=1,
                page_size=80,
                sort="active",
            ),
        ),
        (
            "finance:market-atlas",
            60,
            lambda: dependencies.get_finance_market_atlas_snapshot(
                limit=16,
            ),
        ),
        (
            "finance:equity-event-command",
            60,
            lambda: dependencies.get_equity_event_command_snapshot(
                limit=12,
            ),
        ),
        (
            "finance:onchain-tradfi-perp-radar",
            45,
            lambda: dependencies.get_onchain_tradfi_perp_radar_snapshot(
                limit=12,
            ),
        ),
        (
            "finance:liquidity-regime",
            45,
            lambda: dependencies.get_finance_liquidity_regime_snapshot(
                limit=12,
            ),
        ),
        (
            "market-groups:active-charts:1d",
            300,
            _prewarm_active_market_group_charts,
        ),
        (
            "whales",
            30,
            lambda: dependencies.get_whale_trades_snapshot(limit=14),
        ),
        (
            "suspicious",
            30,
            lambda: dependencies.get_suspicious_trades_snapshot(limit=12),
        ),
        (
            "alpha",
            45,
            lambda: dependencies.get_alpha_signal_snapshot(limit=8),
        ),
        (
            "jin10",
            max(15, dependencies.signal_runtime_ttl_seconds),
            lambda: dependencies.get_jin10_panel_snapshot(limit=24),
        ),
        (
            "bootstrap",
            15,
            dependencies.get_bootstrap_payload_cached,
        ),
    ]
    for name, interval_seconds, builder in tasks:
        if not _claim_prewarm_slot(name, interval_seconds):
            continue
        started_at = time.perf_counter()
        try:
            builder()
            dependencies.application.logger.info(
                "snapshot-prewarm done task=%s duration_ms=%.2f",
                name,
                (time.perf_counter() - started_at) * 1000,
            )
        except Exception:
            dependencies.application.logger.exception(
                "snapshot-prewarm failed task=%s",
                name,
            )


def prewarm_critical_payloads(ctx: Mapping[str, Any]) -> None:
    _prewarm_critical_payloads(
        BootstrapPrewarmDependencies.from_context(ctx),
    )


def _prewarm_critical_payloads(
    dependencies: BootstrapPrewarmDependencies,
) -> None:
    tasks = [
        (
            "markets:focus-tiles",
            lambda: _prewarm_active_market_focus_tiles(dependencies),
        ),
        (
            "commodities",
            lambda: dependencies.get_market_group_snapshot(
                dependencies.commodity_symbols,
                kind="commodities",
            ),
        ),
        (
            "bootstrap:commodities-preview",
            lambda: dependencies.get_bootstrap_component_cached(
                "commodities-preview-v1",
                lambda: dependencies.get_market_group_snapshot(
                    dependencies.commodity_symbols,
                    kind="commodities",
                ),
                ttl_seconds=dependencies.finance_runtime_ttl_seconds,
            ),
        ),
        (
            "market-groups:active:80",
            lambda: dependencies.get_market_groups_payload(
                query="",
                page=1,
                page_size=80,
                sort="active",
            ),
        ),
        (
            "finance:market-atlas",
            lambda: dependencies.get_finance_market_atlas_snapshot(
                limit=16,
            ),
        ),
        (
            "finance:equity-event-command",
            lambda: dependencies.get_equity_event_command_snapshot(
                limit=12,
            ),
        ),
        (
            "finance:onchain-tradfi-perp-radar",
            lambda: dependencies.get_onchain_tradfi_perp_radar_snapshot(
                limit=12,
            ),
        ),
        (
            "finance:liquidity-regime",
            lambda: dependencies.get_finance_liquidity_regime_snapshot(
                limit=12,
            ),
        ),
        (
            "bootstrap",
            dependencies.get_bootstrap_payload_cached,
        ),
    ]
    for name, builder in tasks:
        started_at = time.perf_counter()
        try:
            builder()
            dependencies.application.logger.info(
                "startup-prewarm done task=%s duration_ms=%.2f",
                name,
                (time.perf_counter() - started_at) * 1000,
            )
        except Exception:
            dependencies.application.logger.exception(
                "startup-prewarm failed task=%s",
                name,
            )


def start_snapshot_prewarm_thread(ctx: Mapping[str, Any]) -> None:
    dependencies = BootstrapPrewarmDependencies.from_context(ctx)
    if not dependencies.snapshot_prewarm_enabled:
        dependencies.application.logger.info("snapshot-prewarm disabled")
        return

    def _runner() -> None:
        while True:
            _prewarm_snapshot_payloads(dependencies)
            time.sleep(SNAPSHOT_PREWARM_INTERVAL_SECONDS)

    thread = dependencies.threading_module.Thread(
        target=_runner,
        name="polydata-snapshot-prewarm",
        daemon=True,
    )
    thread.start()
