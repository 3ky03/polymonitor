from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Protocol, cast
from urllib.parse import unquote

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
    resolve_service_value,
)
from api.services import clickhouse_orderfilled_service
from api.services import market_group_service
from market.market_identity import MarketIdentity, oracle_event_lookup_clause, oracle_event_lookup_terms

ACTIVE_MARKETS_SNAPSHOT_NAMESPACE = "snapshot:markets_active_v14"
DEFAULT_ACTIVE_MARKET_MAX_AGE_HOURS = int(os.environ.get("POLYDATA_ACTIVE_MARKET_MAX_AGE_HOURS", "336"))
DEFAULT_ACTIVE_MARKET_ACTIVITY_HOURS = int(os.environ.get("POLYDATA_ACTIVE_MARKET_ACTIVITY_HOURS", "72"))
DEFAULT_ACTIVE_MARKET_LOB_PREFETCH_LIMIT = int(os.environ.get("POLYDATA_ACTIVE_MARKET_LOB_PREFETCH_LIMIT", "0"))
DEFAULT_ACTIVE_MARKET_MIN_PRICE = Decimal(os.environ.get("POLYDATA_ACTIVE_MARKET_MIN_PRICE", "0.05"))
DEFAULT_ACTIVE_MARKET_MAX_PRICE = Decimal(os.environ.get("POLYDATA_ACTIVE_MARKET_MAX_PRICE", "0.95"))
DEFAULT_MARKET_SEARCH_ACTIVE_POOL_SIZE = 25000
DEFAULT_MARKET_SEARCH_RECENT_POOL_SIZE = 20000
DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL = """
    LOWER(COALESCE(CAST(m.tags AS TEXT), '')) NOT LIKE '%%hide-from-new%%'
    AND LOWER(COALESCE(CAST(m.tags AS TEXT), '')) NOT LIKE '%%recurring%%'
    AND LOWER(COALESCE(CAST(m.tags AS TEXT), '')) NOT LIKE '%%onchain-registry%%'
    AND LOWER(COALESCE(CAST(m.tags AS TEXT), '')) NOT LIKE '%%orderfilled-placeholder%%'
    AND LOWER(COALESCE(CAST(m.category AS TEXT), '')) NOT LIKE '%%orderfilled-placeholder%%'
    AND LOWER(COALESCE(CAST(m.slug AS TEXT), '')) NOT LIKE '%%trade-indexer-placeholder%%'
    AND LOWER(COALESCE(CAST(m.title AS TEXT), '')) NOT LIKE 'trade indexer placeholder market%%'
    AND LOWER(COALESCE(CAST(m.slug AS TEXT), '')) NOT LIKE '%%updown-5m%%'
    AND LOWER(COALESCE(CAST(m.slug AS TEXT), '')) NOT LIKE '%%updown-15m%%'
    AND LOWER(COALESCE(CAST(m.title AS TEXT), '')) NOT LIKE '%% up or down - %%'
"""


def _service_callable(
    context: Mapping[str, Any],
    name: str,
) -> Callable[..., Any]:
    return cast(Callable[..., Any], resolve_service_callable(context, name))


@dataclass(frozen=True)
class MarketLookupDependencies:
    query_one: Callable[..., Any]
    utc_now_iso: Callable[..., Any]
    build_market_status_case: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketLookupDependencies:
        return cls(
            query_one=_service_callable(context, "query_one"),
            utc_now_iso=_service_callable(context, "utc_now_iso"),
            build_market_status_case=_service_callable(
                context,
                "build_market_status_case",
            ),
        )


@dataclass(frozen=True)
class MarketOracleDependencies:
    lookup: MarketLookupDependencies
    query_all: Callable[..., Any]
    normalize_oracle_event: Callable[..., Any]
    get_backend: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketOracleDependencies:
        return cls(
            lookup=MarketLookupDependencies.from_context(context),
            query_all=_service_callable(context, "query_all"),
            normalize_oracle_event=_service_callable(
                context,
                "normalize_oracle_event",
            ),
            get_backend=resolve_optional_service_callable(
                context,
                "get_backend",
            ),
        )


@dataclass(frozen=True)
class RecentOracleDependencies:
    get_snapshot_payload: Callable[..., Any]
    get_recent_oracle_events: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> RecentOracleDependencies:
        return cls(
            get_snapshot_payload=_service_callable(
                context,
                "get_snapshot_payload",
            ),
            get_recent_oracle_events=_service_callable(
                context,
                "get_recent_oracle_events",
            ),
        )


@dataclass(frozen=True)
class MarketOraclePayloadDependencies:
    oracle: MarketOracleDependencies
    get_snapshot_payload: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketOraclePayloadDependencies:
        return cls(
            oracle=MarketOracleDependencies.from_context(context),
            get_snapshot_payload=_service_callable(
                context,
                "get_snapshot_payload",
            ),
        )


@dataclass(frozen=True)
class MarketTradeReadDependencies:
    source: Mapping[str, Any] = field(repr=False)
    get_existing_trade_read_source: Callable[..., Any]
    identifier_name: Callable[..., Any]
    trade_v2_core_table: Any
    query_all: Callable[..., Any]
    get_trade_market_projection_sql: Callable[..., Any]
    normalize_trade: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketTradeReadDependencies:
        return cls(
            source=context,
            get_existing_trade_read_source=_service_callable(
                context,
                "get_existing_trade_read_source",
            ),
            identifier_name=_service_callable(context, "_identifier_name"),
            trade_v2_core_table=resolve_service_value(
                context,
                "TRADE_V2_CORE_TABLE",
            ),
            query_all=_service_callable(context, "query_all"),
            get_trade_market_projection_sql=_service_callable(
                context,
                "get_trade_market_projection_sql",
            ),
            normalize_trade=_service_callable(context, "normalize_trade"),
        )


@dataclass(frozen=True)
class RecentTradeDependencies:
    get_snapshot_payload: Callable[..., Any]
    get_recent_trades: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> RecentTradeDependencies:
        return cls(
            get_snapshot_payload=_service_callable(
                context,
                "get_snapshot_payload",
            ),
            get_recent_trades=_service_callable(context, "get_recent_trades"),
        )


@dataclass(frozen=True)
class MarketSearchDependencies:
    source: Mapping[str, Any] = field(repr=False)
    utc_now_iso: Callable[..., Any]
    query_all: Callable[..., Any]
    parse_json_list: Callable[..., Any]
    format_trade_decimal: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketSearchDependencies:
        return cls(
            source=context,
            utc_now_iso=_service_callable(context, "utc_now_iso"),
            query_all=_service_callable(context, "query_all"),
            parse_json_list=_service_callable(context, "parse_json_list"),
            format_trade_decimal=_service_callable(
                context,
                "format_trade_decimal",
            ),
        )


class MarketListItemDependencies(Protocol):
    parse_json_list: Callable[..., Any]
    format_trade_decimal: Callable[..., Any]


@dataclass(frozen=True)
class MarketServingReadDependencies:
    table_exists: Callable[..., Any]
    query_one: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketServingReadDependencies:
        return cls(
            table_exists=_service_callable(context, "table_exists"),
            query_one=_service_callable(context, "query_one"),
        )


@dataclass(frozen=True)
class MarketPriceDependencies:
    lookup: MarketLookupDependencies
    serving: MarketServingReadDependencies
    get_snapshot_payload: Callable[..., Any]
    query_one: Callable[..., Any]
    get_market_clob_price_snapshot: Callable[..., Any]
    get_existing_trade_read_source: Callable[..., Any]
    identifier_name: Callable[..., Any]
    trade_v2_core_table: Any
    iso_days_before: Callable[..., Any]
    utc_date_days_ago: Callable[..., Any]
    format_trade_decimal: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketPriceDependencies:
        return cls(
            lookup=MarketLookupDependencies.from_context(context),
            serving=MarketServingReadDependencies.from_context(context),
            get_snapshot_payload=_service_callable(context, "get_snapshot_payload"),
            query_one=_service_callable(context, "query_one"),
            get_market_clob_price_snapshot=_service_callable(
                context,
                "get_market_clob_price_snapshot",
            ),
            get_existing_trade_read_source=_service_callable(
                context,
                "get_existing_trade_read_source",
            ),
            identifier_name=_service_callable(context, "_identifier_name"),
            trade_v2_core_table=resolve_service_value(
                context,
                "TRADE_V2_CORE_TABLE",
            ),
            iso_days_before=_service_callable(context, "iso_days_before"),
            utc_date_days_ago=_service_callable(
                context,
                "utc_date_days_ago",
            ),
            format_trade_decimal=_service_callable(
                context,
                "format_trade_decimal",
            ),
        )


@dataclass(frozen=True)
class MarketChartDependencies:
    source: Mapping[str, Any] = field(repr=False)
    lookup: MarketLookupDependencies
    serving: MarketServingReadDependencies
    price: MarketPriceDependencies
    get_snapshot_payload: Callable[..., Any]
    get_yahoo_market_snapshot: Callable[..., Any]
    get_trade_derived_market_price_series: Callable[..., Any]
    get_market_clob_price_series: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketChartDependencies:
        return cls(
            source=context,
            lookup=MarketLookupDependencies.from_context(context),
            serving=MarketServingReadDependencies.from_context(context),
            price=MarketPriceDependencies.from_context(context),
            get_snapshot_payload=_service_callable(
                context,
                "get_snapshot_payload",
            ),
            get_yahoo_market_snapshot=_service_callable(
                context,
                "get_yahoo_market_snapshot",
            ),
            get_trade_derived_market_price_series=_service_callable(
                context,
                "get_trade_derived_market_price_series",
            ),
            get_market_clob_price_series=_service_callable(
                context,
                "get_market_clob_price_series",
            ),
        )


@dataclass(frozen=True)
class MarketWorkspaceDependencies:
    source: Mapping[str, Any] = field(repr=False)
    application: Any
    lookup: MarketLookupDependencies
    serving: MarketServingReadDependencies
    price: MarketPriceDependencies
    chart: MarketChartDependencies
    oracle: MarketOraclePayloadDependencies
    trades: MarketTradeReadDependencies
    get_snapshot_payload: Callable[..., Any]
    normalize_market: Callable[..., Any]
    utc_now_iso: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketWorkspaceDependencies:
        return cls(
            source=context,
            application=resolve_service_value(context, "app"),
            lookup=MarketLookupDependencies.from_context(context),
            serving=MarketServingReadDependencies.from_context(context),
            price=MarketPriceDependencies.from_context(context),
            chart=MarketChartDependencies.from_context(context),
            oracle=MarketOraclePayloadDependencies.from_context(context),
            trades=MarketTradeReadDependencies.from_context(context),
            get_snapshot_payload=_service_callable(
                context,
                "get_snapshot_payload",
            ),
            normalize_market=_service_callable(context, "normalize_market"),
            utc_now_iso=_service_callable(context, "utc_now_iso"),
        )


@dataclass(frozen=True)
class MarketListDependencies:
    source: Mapping[str, Any] = field(repr=False)
    application: Any
    snapshot_store: Any
    lob_runtime_manager: Any
    utc_now_iso: Callable[..., Any]
    parse_iso_datetime: Callable[..., Any]
    get_market_clob_price_snapshot: Callable[..., Any]
    get_existing_trade_read_source: Callable[..., Any]
    utc_date_days_ago: Callable[..., Any]
    identifier_name: Callable[..., Any]
    trade_v2_core_table: Any
    query_all: Callable[..., Any]
    query_one: Callable[..., Any]
    parse_json_list: Callable[..., Any]
    format_trade_decimal: Callable[..., Any]
    get_markets_payload_cached: Callable[..., Any]
    set_cached_json: Callable[..., Any]
    get_snapshot_payload: Callable[..., Any]
    get_backend: Callable[..., Any] | None
    table_exists: Callable[..., Any] | None
    get_gamma_active_market_filter: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketListDependencies:
        return cls(
            source=context,
            application=resolve_service_value(context, "app"),
            snapshot_store=resolve_service_value(context, "SNAPSHOT_STORE"),
            lob_runtime_manager=resolve_optional_service_value(
                context,
                "LOB_RUNTIME_MANAGER",
            ),
            utc_now_iso=_service_callable(context, "utc_now_iso"),
            parse_iso_datetime=_service_callable(context, "parse_iso_datetime"),
            get_market_clob_price_snapshot=_service_callable(
                context,
                "get_market_clob_price_snapshot",
            ),
            get_existing_trade_read_source=_service_callable(
                context,
                "get_existing_trade_read_source",
            ),
            utc_date_days_ago=_service_callable(
                context,
                "utc_date_days_ago",
            ),
            identifier_name=_service_callable(context, "_identifier_name"),
            trade_v2_core_table=resolve_service_value(
                context,
                "TRADE_V2_CORE_TABLE",
            ),
            query_all=_service_callable(context, "query_all"),
            query_one=_service_callable(context, "query_one"),
            parse_json_list=_service_callable(context, "parse_json_list"),
            format_trade_decimal=_service_callable(
                context,
                "format_trade_decimal",
            ),
            get_markets_payload_cached=_service_callable(
                context,
                "get_markets_payload_cached",
            ),
            set_cached_json=_service_callable(context, "set_cached_json"),
            get_snapshot_payload=_service_callable(
                context,
                "get_snapshot_payload",
            ),
            get_backend=resolve_optional_service_callable(
                context,
                "get_backend",
            ),
            table_exists=resolve_optional_service_callable(
                context,
                "table_exists",
            ),
            get_gamma_active_market_filter=resolve_optional_service_callable(
                context,
                "get_gamma_active_market_filter",
            ),
        )


def _default_active_market_activity_sql(stats_alias: str) -> str:
    return f"""
    (
        COALESCE({stats_alias}.trade_count_24h, 0) > 0
        OR COALESCE({stats_alias}.volume_24h, 0) > 0
        OR {stats_alias}.last_trade_at IS NOT NULL
        OR {stats_alias}.latest_trade_at IS NOT NULL
    )
    """

def _default_active_market_price_sql(stats_alias: str) -> str:
    return f"""
    (
        {stats_alias}.latest_price IS NULL
        OR (CAST({stats_alias}.latest_price AS DECIMAL(18, 10)) >= {DEFAULT_ACTIVE_MARKET_MIN_PRICE} AND CAST({stats_alias}.latest_price AS DECIMAL(18, 10)) <= {DEFAULT_ACTIVE_MARKET_MAX_PRICE})
    )
    """

def _default_active_market_recent_trade_sql(stats_alias: str) -> str:
    return f"COALESCE({stats_alias}.last_trade_at, {stats_alias}.latest_trade_at) >= ?"


def _default_active_market_created_recent_sql() -> str:
    return "m.created_at IS NULL OR m.created_at >= ?"


def _iso_hours_before(now_iso: str, hours: int) -> str:
    text = str(now_iso or "").replace("Z", "+00:00")
    try:
        now = datetime.fromisoformat(text)
    except ValueError:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

PRICE_TARGET_RE = re.compile(r"\b(?:hit|reach)\s+\$+\s*([0-9][0-9,]*(?:\.\d+)?)\s*([kmb])?\b", re.IGNORECASE)
PAIR_RE = re.compile(r"\b([A-Z0-9]{2,12}/[A-Z0-9]{2,12})\b")
YAHOO_QUOTE_RE = re.compile(r"finance\.yahoo\.com/quote/([^/?\"' )]+)", re.IGNORECASE)

NAME_TO_YAHOO_SYMBOL = {
    "bitcoin": "BTC-USD",
    "btc": "BTC-USD",
    "ethereum": "ETH-USD",
    "eth": "ETH-USD",
    "solana": "SOL-USD",
    "sol": "SOL-USD",
    "xrp": "XRP-USD",
    "dogecoin": "DOGE-USD",
    "doge": "DOGE-USD",
    "s&p 500": "^GSPC",
    "spx": "^GSPC",
    "nasdaq 100": "^NDX",
    "ndx": "^NDX",
    "gold": "GC=F",
    "silver": "SI=F",
    "oil": "CL=F",
}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def markets_runtime_prices_enabled() -> bool:
    return _env_flag("POLYDATA_MARKETS_RUNTIME_PRICES", False)


def markets_latest_snapshot_fallback_enabled() -> bool:
    return _env_flag("POLYDATA_MARKETS_LATEST_SNAPSHOT_FALLBACK", True)


def active_market_clickhouse_primary_enabled() -> bool:
    return _env_flag("POLYDATA_ACTIVE_MARKET_CLICKHOUSE_PRIMARY", False)


def _trim_active_markets_payload(
    payload: Any,
    page_size: int,
) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return None
    filtered_items = [
        item
        for item in items
        if isinstance(item, dict)
        and _is_tradeable_probability(item.get("latestPrice") or item.get("latest_price"))
        and int(item.get("tradeCount24h") or item.get("trade_count_24h") or 0) > 0
    ]
    if not filtered_items:
        return None
    source_page_size = len(items)
    pagination = payload.get("pagination")
    if isinstance(pagination, dict):
        try:
            source_page_size = int(pagination.get("pageSize") or source_page_size)
        except (TypeError, ValueError):
            source_page_size = len(items)
    if len(items) < page_size and source_page_size < page_size:
        return None
    trimmed_items = filtered_items[:page_size]
    return {
        **payload,
        "items": trimmed_items,
        "pagination": {
            "page": 1,
            "pageSize": page_size,
            "total": len(trimmed_items),
            "totalPages": 1,
            "hasMore": len(filtered_items) > page_size,
        },
    }


def _normalized_gamma_active_keys(
    dependencies: MarketListDependencies,
) -> tuple[set[str], set[str]]:
    if dependencies.get_gamma_active_market_filter is None:
        return set(), set()
    payload = dependencies.get_gamma_active_market_filter() or {}
    condition_ids = {
        str(value or "").strip().lower()
        for value in (payload.get("conditionIds") or [])
        if str(value or "").strip()
    }
    slugs = {
        str(value or "").strip().lower()
        for value in (payload.get("slugs") or [])
        if str(value or "").strip()
    }
    return condition_ids, slugs


def _filter_candidate_rows_to_gamma_active(
    dependencies: MarketListDependencies,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    condition_ids, slugs = _normalized_gamma_active_keys(dependencies)
    if not condition_ids and not slugs:
        return rows
    filtered: List[Dict[str, Any]] = []
    for row in rows:
        condition_id = str(row.get("condition_id") or "").strip().lower()
        slug = str(row.get("slug") or "").strip().lower()
        if condition_id and condition_id in condition_ids:
            filtered.append(row)
            continue
        if slug and slug in slugs:
            filtered.append(row)
    return filtered


def _prefer_gamma_active_candidate_rows(
    dependencies: MarketListDependencies,
    rows: List[Dict[str, Any]],
    target_count: int,
) -> List[Dict[str, Any]]:
    """Prefer Gamma-confirmed markets, then fill from DB-active rows like PolyWorld."""
    gamma_rows = _filter_candidate_rows_to_gamma_active(dependencies, rows)
    if len(gamma_rows) >= target_count:
        return gamma_rows
    if not gamma_rows:
        return rows

    seen_ids = {int(row["id"]) for row in gamma_rows if row.get("id") is not None}
    fallback_rows = [row for row in rows if row.get("id") is None or int(row["id"]) not in seen_ids]
    return [*gamma_rows, *fallback_rows]


def _blend_recent_candidate_rows(volume_rows: List[Dict[str, Any]], recent_rows: List[Dict[str, Any]], target_count: int) -> List[Dict[str, Any]]:
    if not recent_rows:
        return volume_rows
    target_count = max(1, int(target_count))
    recent_count = max(target_count, min(len(recent_rows), target_count * 2))

    blended: List[Dict[str, Any]] = []
    seen_ids: set[int] = set()

    def append_rows(rows: List[Dict[str, Any]], limit: Optional[int] = None) -> None:
        added = 0
        for row in rows:
            market_id = row.get("id")
            if market_id is not None:
                numeric_id = int(market_id)
                if numeric_id in seen_ids:
                    continue
                seen_ids.add(numeric_id)
            blended.append(row)
            added += 1
            if limit is not None and added >= limit:
                break

    append_rows(recent_rows, recent_count)
    append_rows(volume_rows)
    append_rows(recent_rows)
    return blended


def _decimal_from_any(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _price_value_from_point(point: Dict[str, Any]) -> Any:
    if not isinstance(point, dict):
        return None
    return point.get("yesPrice") if point.get("yesPrice") not in (None, "") else point.get("price")


def _chart_point_stats(points: List[Dict[str, Any]]) -> tuple[int, int]:
    values: set[str] = set()
    for point in points or []:
        value = _price_value_from_point(point)
        if value not in (None, ""):
            values.add(str(value))
    return len(points or []), len(values)


def _chart_history_status(range_name: str, interval: str, points: List[Dict[str, Any]]) -> str:
    if not points:
        return "missing"
    if range_name == "snapshot" or interval == "snapshot" or len(points) <= 2:
        return "snapshot"
    _, distinct_count = _chart_point_stats(points)
    if distinct_count <= 1:
        return "flat"
    if len(points) < 8:
        return "short"
    return "ok"


def _workspace_identity(market_id: int, market: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "localMarketId": market_id,
        "marketId": market_id,
        "gammaMarketId": market.get("gamma_market_id"),
        "eventId": market.get("event_id"),
        "eventSlug": market.get("event_slug"),
        "slug": market.get("slug"),
        "conditionId": market.get("condition_id"),
        "questionId": market.get("question_id"),
        "oracle": market.get("oracle"),
        "yesTokenId": market.get("yes_token_id"),
        "noTokenId": market.get("no_token_id"),
    }


def _workspace_selected_outcome(
    group: Optional[Dict[str, Any]],
    market_id: int,
    market: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(group, dict):
        return None
    market = market or {}
    gamma_market_id = str(market.get("gamma_market_id") or "").strip()
    condition_id = str(market.get("condition_id") or "").strip().lower()
    yes_token_id = str(market.get("yes_token_id") or "").strip()
    outcomes = list(group.get("outcomes") or []) + list(group.get("topOutcomes") or [])
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            continue
        for key in ("marketId", "localMarketId"):
            if outcome.get(key) is None:
                continue
            try:
                if int(outcome.get(key) or 0) == int(market_id):
                    return outcome
            except (TypeError, ValueError):
                continue
        if gamma_market_id and str(outcome.get("gammaMarketId") or "").strip() == gamma_market_id:
            return outcome
        if condition_id and str(outcome.get("conditionId") or "").strip().lower() == condition_id:
            return outcome
        if yes_token_id and str(outcome.get("yesTokenId") or "").strip() == yes_token_id:
            return outcome
    return None


def _workspace_health(
    *,
    market_id: int,
    identity: Dict[str, Any],
    price: Optional[Dict[str, Any]],
    chart: Optional[Dict[str, Any]],
    oracle_payload: Optional[Dict[str, Any]],
    diagnostics: Optional[Dict[str, Any]],
    group: Optional[Dict[str, Any]],
    selected_outcome: Optional[Dict[str, Any]],
    serving_source: Optional[str],
) -> Dict[str, Any]:
    chart_status = str((chart or {}).get("historyStatus") or (diagnostics or {}).get("chartStatus") or "missing")
    timeline = (oracle_payload or {}).get("timeline") or []
    if not isinstance(timeline, list):
        timeline = []
    has_identity = bool(identity.get("conditionId") or identity.get("questionId") or identity.get("oracle"))
    oracle_market_id = (oracle_payload or {}).get("localMarketId") or (oracle_payload or {}).get("marketId")
    try:
        oracle_matches = oracle_market_id in (None, "") or int(oracle_market_id or 0) == int(market_id)
    except (TypeError, ValueError):
        oracle_matches = str(oracle_market_id or "") == str(market_id)
    if not oracle_matches:
        oracle_status = "mismatch"
    elif timeline:
        oracle_status = "bound"
    elif has_identity:
        oracle_status = "open-no-events"
    else:
        oracle_status = "unbound"
    price_status = "ok" if price and (price.get("latestYesPrice") not in (None, "") or price.get("latestPrice") not in (None, "")) else "missing"
    if chart_status == "missing" and price_status == "ok":
        chart_status = "snapshot" if (chart or {}).get("points") else "missing-local-history"
    if group and selected_outcome is None:
        group_status = "outcome-missing"
    elif group:
        group_status = "ok"
    else:
        group_status = "single-market"
    serving_status = "ok" if serving_source == "postgres" else "fallback"
    issues = list((diagnostics or {}).get("issues") or [])
    if oracle_status == "mismatch":
        issues.append("oracle-market-id-mismatch")
    if group_status == "outcome-missing":
        issues.append("group-selected-outcome-missing")
    return {
        "marketId": market_id,
        "priceStatus": price_status,
        "chartStatus": chart_status,
        "oracleStatus": oracle_status,
        "lobStatus": "not-loaded",
        "servingStatus": serving_status,
        "groupStatus": group_status,
        "issues": issues,
        "level": "critical" if any("mismatch" in issue for issue in issues) else ("warn" if issues else "ok"),
    }


def _workspace_evidence(
    *,
    market_id: int,
    identity: Dict[str, Any],
    price: Optional[Dict[str, Any]],
    chart: Optional[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    oracle_payload: Optional[Dict[str, Any]],
    group: Optional[Dict[str, Any]],
    health: Dict[str, Any],
    serving_source: Optional[str],
    serving_updated_at: Optional[str],
    generated_at: str,
) -> Dict[str, Any]:
    points = (chart or {}).get("points") or []
    if not isinstance(points, list):
        points = []
    timeline = (oracle_payload or {}).get("timeline") or []
    if not isinstance(timeline, list):
        timeline = []
    outcomes = (group or {}).get("outcomes") or []
    if not isinstance(outcomes, list):
        outcomes = []

    latest_chart_at = max(
        (
            str(point.get("timestamp"))
            for point in points
            if isinstance(point, dict) and point.get("timestamp")
        ),
        default=None,
    )
    latest_trade_at = max(
        (
            str(trade.get("timestamp"))
            for trade in trades
            if isinstance(trade, dict) and trade.get("timestamp")
        ),
        default=None,
    )
    latest_oracle_at = max(
        (
            str(event.get("eventTime"))
            for event in timeline
            if isinstance(event, dict) and event.get("eventTime")
        ),
        default=None,
    )
    identifiers = {
        key: value
        for key, value in {
            "localMarketId": identity.get("localMarketId") or market_id,
            "gammaMarketId": identity.get("gammaMarketId"),
            "conditionId": identity.get("conditionId"),
            "questionId": identity.get("questionId"),
        }.items()
        if value not in (None, "")
    }
    has_price = bool(
        price
        and (
            price.get("latestYesPrice") not in (None, "")
            or price.get("latestPrice") not in (None, "")
        )
    )
    claims = [
        {
            "id": "identity",
            "label": "Market identity",
            "status": "ok" if identity.get("conditionId") else "partial",
            "source": serving_source or "market-registry",
            "observedAt": serving_updated_at or generated_at,
            "recordCount": 1,
            "detail": "Local, Gamma, condition and question identifiers",
            "identifiers": identifiers,
        },
        {
            "id": "price",
            "label": "Current probability",
            "status": str(health.get("priceStatus") or ("ok" if has_price else "missing")),
            "source": str((price or {}).get("priceSource") or serving_source or "market-serving"),
            "observedAt": (price or {}).get("updatedAt") or serving_updated_at,
            "recordCount": 1 if has_price else 0,
            "detail": "Latest YES and NO probability observation",
        },
        {
            "id": "history",
            "label": "Probability history",
            "status": str(health.get("chartStatus") or "missing"),
            "source": str((chart or {}).get("priceSource") or "market-chart"),
            "observedAt": latest_chart_at or (chart or {}).get("servingUpdatedAt"),
            "recordCount": len(points),
            "detail": f"{(chart or {}).get('range') or 'unknown'} range · {(chart or {}).get('interval') or 'unknown'} interval",
        },
        {
            "id": "trades",
            "label": "OrderFilled evidence",
            "status": "ok" if trades else "missing",
            "source": "polygon-orderfilled",
            "observedAt": latest_trade_at,
            "recordCount": len(trades),
            "detail": "Canonical transaction hash and log index rows",
        },
        {
            "id": "oracle",
            "label": "Oracle lifecycle",
            "status": str(health.get("oracleStatus") or "unbound"),
            "source": str(
                next(
                    (
                        event.get("sourceAdapter") or event.get("sourceOracle")
                        for event in timeline
                        if isinstance(event, dict)
                        and (event.get("sourceAdapter") or event.get("sourceOracle"))
                    ),
                    identity.get("oracle") or "uma-oracle",
                )
            ),
            "observedAt": latest_oracle_at,
            "recordCount": len(timeline),
            "detail": "Proposal, dispute and settlement observations",
        },
        {
            "id": "group",
            "label": "Event and outcomes",
            "status": str(health.get("groupStatus") or "single-market"),
            "source": "gamma-market-group",
            "observedAt": (group or {}).get("generatedAt") or generated_at,
            "recordCount": len(outcomes) if group else 1,
            "detail": "Event membership and outcome-token mapping",
        },
    ]
    return {
        "contractVersion": "market-workspace-evidence.v1",
        "generatedAt": generated_at,
        "claims": claims,
        "issues": list(health.get("issues") or []),
    }


def _workspace_diagnostics(
    market_id: int,
    market: Dict[str, Any],
    price: Dict[str, Any],
    chart: Dict[str, Any],
    oracle_payload: Dict[str, Any],
    trades: List[Dict[str, Any]],
) -> Dict[str, Any]:
    points = chart.get("points") if isinstance(chart, dict) else []
    if not isinstance(points, list):
        points = []
    chart_status = str(chart.get("historyStatus") or _chart_history_status(str(chart.get("range") or ""), str(chart.get("interval") or ""), points))
    oracle_timeline = oracle_payload.get("timeline") if isinstance(oracle_payload, dict) else []
    if not isinstance(oracle_timeline, list):
        oracle_timeline = []
    token_ids = [value for value in (market.get("yes_token_id"), market.get("no_token_id")) if value]
    issues: List[str] = []
    if not market.get("gamma_market_id"):
        issues.append("missing-gamma-market-id")
    if not market.get("condition_id"):
        issues.append("missing-condition-id")
    if not token_ids:
        issues.append("missing-clob-token-ids")
    if chart_status in {"missing", "snapshot", "flat"}:
        issues.append(f"chart-{chart_status}")
    if not price or price.get("latestPrice") in (None, ""):
        issues.append("missing-latest-price")
    completion_status = str(oracle_payload.get("completionStatus") or "UNKNOWN")
    if completion_status not in {"OPEN", "UNKNOWN"} and not oracle_timeline:
        issues.append("missing-oracle-timeline")
    volume = _decimal_from_any(price.get("volume24h") if isinstance(price, dict) else None)
    trade_count = int((price or {}).get("tradeCount24h") or 0)
    if not trades and ((volume is not None and volume > 0) or trade_count > 0):
        issues.append("serving-volume-without-local-trades")

    critical_issues = {"missing-condition-id", "missing-clob-token-ids"}
    if any(issue in critical_issues for issue in issues):
        level = "critical"
    elif issues:
        level = "warn"
    else:
        level = "ok"
    return {
        "marketId": market_id,
        "identityStatus": "ok" if market.get("condition_id") and token_ids else "partial",
        "chartStatus": chart_status,
        "oracleStatus": completion_status,
        "oracleEventCount": len(oracle_timeline),
        "tradeCount": len(trades),
        "hasPrice": bool(price and price.get("latestPrice") not in (None, "")),
        "hasLobTokens": bool(token_ids),
        "issues": issues,
        "level": level,
    }


def _is_tradeable_probability(value: Any) -> bool:
    price = _decimal_from_any(value)
    if price is None:
        return True
    return DEFAULT_ACTIVE_MARKET_MIN_PRICE <= price <= DEFAULT_ACTIVE_MARKET_MAX_PRICE


def _has_recent_trade_window(row: Dict[str, Any]) -> bool:
    trade_count = int(row.get("trade_count_24h") or 0)
    volume_24h = _decimal_from_any(row.get("volume_24h"))
    return trade_count > 0 or (volume_24h is not None and volume_24h > 0)


def _filter_tradeable_market_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for row in rows:
        if not _has_recent_trade_window(row):
            continue
        if not _is_tradeable_probability(row.get("latest_price")):
            continue
        filtered.append(row)
    return filtered


def _prefer_tradeable_market_rows(rows: List[Dict[str, Any]], target_count: int) -> List[Dict[str, Any]]:
    """Prefer actively traded, non-terminal markets for the primary active feed."""
    tradeable_rows = _filter_tradeable_market_rows(rows)
    return tradeable_rows[:target_count]


def _balanced_probability_score(value: Any) -> float:
    price = _decimal_from_any(value)
    if price is None:
        return 0.0
    distance = abs(float(price) - 0.5)
    return max(0.0, 1.0 - distance / 0.5)


def _market_family_key(row: Dict[str, Any]) -> str:
    question_id = str(row.get("question_id") or "").strip().lower()
    if question_id:
        return f"question:{question_id}"
    title = re.sub(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b\d+(?:\.\d+)?\b", " ", str(row.get("title") or "").lower())
    words = re.findall(r"[a-z][a-z0-9]+", title)
    prefix = " ".join(words[:5]) if words else str(row.get("slug") or row.get("condition_id") or row.get("id"))
    category = str(row.get("category") or "").strip().lower()
    return f"{category}:{prefix}"


def _market_category_bucket(row: Dict[str, Any]) -> str:
    category = str(row.get("category") or "").strip().lower()
    tags = str(row.get("tags") or "").strip().lower()
    title = str(row.get("title") or "").strip().lower()
    slug = str(row.get("slug") or "").strip().lower()
    text = " ".join((category, tags, title, slug))
    if "orderfilled-placeholder" in text or title.startswith("trade indexer placeholder market"):
        return "placeholder"
    if any(token in text for token in ("politic", "election", "trump", "biden", "congress", "iran", "ceasefire", "war", "president")):
        return "politics"
    if any(token in text for token in ("crypto", "bitcoin", "ethereum", "solana", "xrp", "token", "btc", "eth")):
        return "crypto"
    if any(token in text for token in ("finance", "business", "econom", "fed", "rate", "inflation", "ipo", "valuation", "stock", "macro")):
        return "macro"
    if any(token in text for token in ("tech", "ai", "openai", "spacex", "tesla", "apple", "google", "nvidia")):
        return "tech"
    if any(token in text for token in ("weather", "temperature", "hurricane", "rain", "snow")):
        return "weather"
    if any(token in text for token in ("sports", "tennis", "soccer", "nba", "nfl", "mlb", "nhl", "fifa", "formula1", "ufc", "valorant")):
        return "sports"
    if any(token in text for token in ("esports", "games", "gaming", "counter-strike", "league of legends")):
        return "games"
    if any(token in text for token in ("pop-culture", "awards", "movie", "music", "celebrity", "oscars", "grammy")):
        return "culture"
    return category or "market"


def _interleave_market_category_rows(rows: List[Dict[str, Any]], target_count: int) -> List[Dict[str, Any]]:
    if len(rows) <= 2:
        return rows
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for row in rows:
        bucket = _market_category_bucket(row)
        if bucket == "placeholder":
            continue
        if bucket not in buckets:
            buckets[bucket] = []
            order.append(bucket)
        buckets[bucket].append(row)
    if len(order) <= 1:
        return [row for row in rows if _market_category_bucket(row) != "placeholder"]

    selected: List[Dict[str, Any]] = []
    seen_ids: set[int] = set()
    limit = max(1, min(len(rows), int(target_count)))
    while len(selected) < limit:
        added = False
        for bucket in order:
            bucket_rows = buckets.get(bucket) or []
            while bucket_rows:
                row = bucket_rows.pop(0)
                market_id = row.get("id")
                if market_id is not None and int(market_id) in seen_ids:
                    continue
                selected.append(row)
                if market_id is not None:
                    seen_ids.add(int(market_id))
                added = True
                break
            if len(selected) >= limit:
                break
        if not added:
            break

    for row in rows:
        market_id = row.get("id")
        if _market_category_bucket(row) == "placeholder":
            continue
        if market_id is not None and int(market_id) in seen_ids:
            continue
        selected.append(row)
        if market_id is not None:
            seen_ids.add(int(market_id))
    return selected


def _rank_default_market_rows(rows: List[Dict[str, Any]], now_value: Any = None) -> List[Dict[str, Any]]:
    def parse_time(value: Any) -> Optional[datetime]:
        if not value:
            return None
        text = str(value).replace(" ", "T")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    now = parse_time(now_value) or datetime.now(timezone.utc)

    def score(row: Dict[str, Any]) -> float:
        created = parse_time(row.get("created_at"))
        last_trade = parse_time(row.get("last_trade_at") or row.get("latest_trade_at"))
        age_hours = (now - created).total_seconds() / 3600 if created else 9999
        trade_age_hours = (now - last_trade).total_seconds() / 3600 if last_trade else 9999
        recency = max(0.0, 1.0 - min(age_hours, 24 * 14) / (24 * 14))
        trade_recency = max(0.0, 1.0 - min(trade_age_hours, 24 * 3) / (24 * 3))
        volume = min(1.0, float(_decimal_from_any(row.get("volume_24h")) or 0) / 50000.0)
        trades = min(1.0, int(row.get("trade_count_24h") or 0) / 250.0)
        balance = _balanced_probability_score(row.get("latest_price"))
        active_rank = min(1.0, max(0.0, float(row.get("active_rank") or 0) / 160.0))
        return active_rank * 45 + volume * 25 + trade_recency * 15 + balance * 10 + trades * 5 + recency * 4

    return sorted(rows, key=score, reverse=True)


def _diversify_market_rows(rows: List[Dict[str, Any]], page_size: int, now_value: Any = None) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    seen_families: set[str] = set()
    for row in rows:
        family = _market_family_key(row)
        if family in seen_families:
            continue
        selected.append(row)
        seen_families.add(family)
        if len(selected) >= page_size:
            return selected
    seen_ids = {int(row["id"]) for row in selected if row.get("id") is not None}
    ranked = _rank_default_market_rows(rows, now_value)
    for row in ranked:
        market_id = row.get("id")
        if market_id is not None and int(market_id) in seen_ids:
            continue
        selected.append(row)
        if len(selected) >= page_size:
            break
    return selected


def _coalesce_native_market_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    passthrough: List[Dict[str, Any]] = []
    for row in rows:
        question_id = str(row.get("question_id") or "").strip().lower()
        if not question_id:
            passthrough.append(row)
            continue
        grouped.setdefault(question_id, []).append(row)

    coalesced: List[Dict[str, Any]] = []
    for group_rows in grouped.values():
        if len(group_rows) == 1:
            coalesced.append(group_rows[0])
            continue
        ranked = _rank_default_market_rows(group_rows)
        representative = dict(ranked[0])
        representative["native_outcome_count"] = len(group_rows)
        representative["volume_24h"] = sum((_decimal_from_any(row.get("volume_24h")) or Decimal("0")) for row in group_rows)
        representative["trade_count_24h"] = sum(int(row.get("trade_count_24h") or 0) for row in group_rows)
        coalesced.append(representative)
    return [*coalesced, *passthrough]


def _book_side_has_levels(side: Any) -> bool:
    return isinstance(side, dict) and bool(side.get("bids") or side.get("asks"))


def _lob_payload_has_levels(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return _book_side_has_levels(payload.get("yes")) or _book_side_has_levels(payload.get("no"))


def _prefer_lob_ready_market_rows(
    dependencies: MarketListDependencies,
    rows: List[Dict[str, Any]],
    target_count: int,
) -> List[Dict[str, Any]]:
    if not _env_flag("POLYDATA_ACTIVE_MARKET_PREFER_LOB_READY", True):
        return rows
    manager = dependencies.lob_runtime_manager
    if manager is None or not hasattr(manager, "get_market_snapshot"):
        return rows
    max_checks = min(len(rows), max(0, DEFAULT_ACTIVE_MARKET_LOB_PREFETCH_LIMIT), max(target_count * 3, target_count))
    if max_checks <= 0:
        return rows

    ready: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    for row in rows[:max_checks]:
        market_id = row.get("id")
        yes_token_id = str(row.get("yes_token_id") or "").strip()
        no_token_id = str(row.get("no_token_id") or "").strip()
        if market_id is None or not yes_token_id or not no_token_id:
            deferred.append(row)
            continue
        try:
            payload = manager.get_market_snapshot(
                market_id=int(market_id),
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                market_title=str(row.get("title") or ""),
            )
        except Exception:
            deferred.append(row)
            continue
        if _lob_payload_has_levels(payload):
            ready.append(row)
        else:
            deferred.append(row)
    if not ready:
        return rows
    return [*ready, *deferred, *rows[max_checks:]]


def _parse_numeric_target(value: str, suffix: str | None = None) -> Optional[float]:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        return None
    normalized_suffix = str(suffix or "").strip().lower()
    if normalized_suffix == "k":
        numeric *= 1_000
    elif normalized_suffix == "m":
        numeric *= 1_000_000
    elif normalized_suffix == "b":
        numeric *= 1_000_000_000
    return numeric


def _resolve_yahoo_symbol(title: str, description: str) -> Optional[str]:
    yahoo_match = YAHOO_QUOTE_RE.search(description)
    if yahoo_match:
        return unquote(yahoo_match.group(1))

    pair_match = PAIR_RE.search(description)
    if pair_match:
        base, quote = pair_match.group(1).split("/", 1)
        base = base.strip().upper()
        quote = quote.strip().upper()
        if quote in {"USDT", "USD"}:
            return f"{base}-USD"

    haystack = f"{title} {description}".lower()
    for label, symbol in NAME_TO_YAHOO_SYMBOL.items():
        if label in haystack:
            return symbol
    return None


def _extract_market_chart_context(
    get_yahoo_market_snapshot: Callable[..., Any],
    market: Optional[Dict[str, Any]],
    range_name: str,
) -> Optional[Dict[str, Any]]:
    if not market:
        return None

    title = str(market.get("title") or "").strip()
    description = str(market.get("description") or "").strip()
    if not title and not description:
        return None

    title_match = PRICE_TARGET_RE.search(title)
    desc_match = PRICE_TARGET_RE.search(description)
    target_match = title_match or desc_match
    target_price = None
    if target_match:
        target_price = _parse_numeric_target(target_match.group(1), target_match.group(2))

    pair_match = PAIR_RE.search(description)
    pair_label = pair_match.group(1) if pair_match else None
    yahoo_symbol = _resolve_yahoo_symbol(title, description)
    is_up_down = "up or down" in title.lower() or "close price is greater than or equal to the open price" in description.lower()
    is_price_target = target_price is not None and (
        "price specified in the title" in description.lower()
        or "hit" in title.lower()
        or "reach" in title.lower()
    )

    if not yahoo_symbol or not (is_up_down or is_price_target):
        return None

    source_label = "Yahoo Finance" if "finance.yahoo.com/quote/" in description.lower() else "Underlying"
    if "binance" in description.lower():
        source_label = "Underlying proxy"

    yahoo_interval = "5m" if range_name in {"1h", "1d"} else "30m"
    yahoo_range = "1d" if range_name == "1h" else "5d"
    snapshot = get_yahoo_market_snapshot(
        yahoo_symbol,
        interval=yahoo_interval,
        range_name=yahoo_range,
    )
    if not snapshot or not snapshot.get("points"):
        return None

    return {
        "kind": "underlying-price",
        "sourceSymbol": yahoo_symbol,
        "sourceLabel": source_label,
        "pairLabel": pair_label,
        "targetPrice": target_price,
        "targetLabel": "Target" if is_price_target else "Price to beat",
        "referenceRule": "close >= open" if is_up_down else "hit threshold",
        "currentUnderlyingPrice": snapshot.get("price"),
        "underlyingChangePercent": snapshot.get("changePercent"),
        "points": snapshot.get("points") or [],
    }


def search_markets(
    ctx: Mapping[str, Any],
    query: str,
    limit: int = 10,
) -> Dict[str, Any]:
    return _search_markets(
        MarketSearchDependencies.from_context(ctx),
        query,
        limit=limit,
    )


def _search_markets(
    dependencies: MarketSearchDependencies,
    query: str,
    limit: int = 10,
) -> Dict[str, Any]:
    cleaned = str(query or "").strip()
    if not cleaned:
        return {"items": []}
    limit = min(50, max(1, int(limit or 10)))
    now_iso = dependencies.utc_now_iso()
    tokens = re.findall(r"[a-zA-Z0-9]+", cleaned.lower())[:6]
    if not tokens:
        return {"items": []}
    ts_query = " & ".join(f"{token}:*" for token in tokens)
    prefix_pattern = f"{cleaned.lower()}%"
    contains_pattern = f"%{cleaned.lower()}%"
    candidate_limit = max(limit * 2, 100)
    # Keep interactive searches bounded to active/recent markets. A zero-result
    # query still falls back to the full GIN-indexed history, preserving lookup
    # for old resolved markets without making common broad terms sort millions
    # of registry rows.
    rows = dependencies.query_all(
        f"""
        WITH candidate_ids AS MATERIALIZED (
            SELECT market_id AS id
            FROM (
                SELECT mls.market_id
                FROM market_list_serving mls
                JOIN market_status_snapshot mss ON mss.market_id = mls.market_id
                WHERE mss.is_trading_closed = FALSE
                  AND mss.has_settle = FALSE
                  AND mss.has_propose = FALSE
                  AND mss.settlement_code = 0
                  AND (
                      mls.latest_price IS NOT NULL
                      OR mls.volume_24h > 0
                      OR mls.trade_count_24h > 0
                      OR mls.last_trade_at IS NOT NULL
                      OR mls.latest_trade_at IS NOT NULL
                  )
                ORDER BY
                    mls.volume_24h DESC,
                    mls.trade_count_24h DESC,
                    mls.last_trade_at DESC NULLS LAST
                LIMIT {DEFAULT_MARKET_SEARCH_ACTIVE_POOL_SIZE}
            ) active_candidates
            UNION
            SELECT id
            FROM (
                SELECT m.id
                FROM markets m
                WHERE {DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL}
                ORDER BY m.created_at DESC NULLS LAST, m.id DESC
                LIMIT {DEFAULT_MARKET_SEARCH_RECENT_POOL_SIZE}
            ) recent_candidates
        ),
        priority_matched AS MATERIALIZED (
            SELECT
                m.id,
                ts_rank_cd(
                    to_tsvector(
                        'simple',
                        (((COALESCE(m.title, '') || ' ') || COALESCE(m.slug, '')) || ' ') || COALESCE(m.category, '')
                    ),
                    to_tsquery('simple', ?)
                ) AS search_rank
            FROM candidate_ids candidate
            JOIN markets m ON m.id = candidate.id
            WHERE to_tsvector(
                'simple',
                (((COALESCE(m.title, '') || ' ') || COALESCE(m.slug, '')) || ' ') || COALESCE(m.category, '')
            ) @@ to_tsquery('simple', ?)
              AND {DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL}
            ORDER BY search_rank DESC, m.created_at DESC
            LIMIT 5000
        ),
        fallback_matched AS MATERIALIZED (
            SELECT
                m.id,
                ts_rank_cd(
                    to_tsvector(
                        'simple',
                        (((COALESCE(m.title, '') || ' ') || COALESCE(m.slug, '')) || ' ') || COALESCE(m.category, '')
                    ),
                    to_tsquery('simple', ?)
                ) AS search_rank
            FROM markets m
            WHERE NOT EXISTS (SELECT 1 FROM priority_matched)
              AND to_tsvector(
                  'simple',
                  (((COALESCE(m.title, '') || ' ') || COALESCE(m.slug, '')) || ' ') || COALESCE(m.category, '')
              ) @@ to_tsquery('simple', ?)
              AND {DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL}
            ORDER BY search_rank DESC, m.created_at DESC
            LIMIT 5000
        ),
        matched AS MATERIALIZED (
            SELECT id, search_rank FROM priority_matched
            UNION ALL
            SELECT id, search_rank FROM fallback_matched
        )
        SELECT
            m.id,
            m.gamma_market_id,
            m.slug,
            m.title,
            m.condition_id,
            m.question_id,
            m.end_date,
            m.created_at,
            m.category,
            m.tags,
            m.yes_token_id,
            m.no_token_id,
            m.clob_token_ids,
            COALESCE(mss.completion_status, 'OPEN') AS completion_status,
            mss.completion_source,
            mss.completion_time,
            COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
            COALESCE(mss.is_resolved, FALSE) AS is_resolved,
            COALESCE(mss.is_final, FALSE) AS is_final,
            COALESCE(mss.has_settle, FALSE) AS has_settle,
            COALESCE(mss.has_propose, FALSE) AS has_propose,
            COALESCE(mss.settlement_code, 0) AS settlement_code,
            COALESCE(mss.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
            mss.settlement_source,
            mss.settlement_event_id,
            mss.settlement_event_time,
            mss.settlement_transaction,
            COALESCE(mss.gamma_closed, FALSE) AS gamma_closed,
            mss.gamma_closed_time,
            COALESCE(mls.latest_price, mlp.latest_yes_price) AS latest_price,
            mls.price_24h_ago,
            COALESCE(mls.volume_24h, 0) AS volume_24h,
            COALESCE(mls.trade_count_24h, 0) AS trade_count_24h,
            mls.last_trade_at,
            mls.latest_trade_at,
            matched.search_rank,
            CASE
                WHEN COALESCE(mss.is_trading_closed, FALSE) = FALSE
                 AND COALESCE(mss.has_settle, FALSE) = FALSE
                 AND COALESCE(mss.has_propose, FALSE) = FALSE
                 AND COALESCE(mss.settlement_code, 0) = 0
                 AND (m.end_date IS NULL OR m.end_date >= ?)
                 AND (
                    mls.latest_price IS NOT NULL
                    OR mlp.latest_yes_price IS NOT NULL
                    OR COALESCE(mls.volume_24h, 0) > 0
                    OR COALESCE(mls.trade_count_24h, 0) > 0
                    OR mls.last_trade_at IS NOT NULL
                    OR mls.latest_trade_at IS NOT NULL
                 )
                 AND (
                    COALESCE(mls.latest_price, mlp.latest_yes_price) IS NULL
                    OR (
                        CAST(COALESCE(mls.latest_price, mlp.latest_yes_price) AS DECIMAL(18, 10)) >= 0.05
                        AND CAST(COALESCE(mls.latest_price, mlp.latest_yes_price) AS DECIMAL(18, 10)) <= 0.95
                    )
                 )
                THEN 'active'
                WHEN COALESCE(mss.is_trading_closed, FALSE) = FALSE
                 AND COALESCE(mss.has_settle, FALSE) = FALSE
                 AND COALESCE(mss.has_propose, FALSE) = FALSE
                 AND COALESCE(mss.settlement_code, 0) = 0
                 AND (m.end_date IS NULL OR m.end_date >= ?)
                 AND (
                    mls.latest_price IS NOT NULL
                    OR mlp.latest_yes_price IS NOT NULL
                    OR COALESCE(mls.volume_24h, 0) > 0
                    OR COALESCE(mls.trade_count_24h, 0) > 0
                    OR mls.last_trade_at IS NOT NULL
                    OR mls.latest_trade_at IS NOT NULL
                 )
                THEN 'open_terminal'
                WHEN COALESCE(mss.is_trading_closed, FALSE) = FALSE
                 AND COALESCE(mss.has_settle, FALSE) = FALSE
                 AND COALESCE(mss.has_propose, FALSE) = FALSE
                 AND COALESCE(mss.settlement_code, 0) = 0
                 AND (m.end_date IS NULL OR m.end_date >= ?)
                THEN 'open_no_data'
                ELSE LOWER(COALESCE(mss.completion_status, 'closed'))
            END AS status
        FROM matched
        JOIN markets m ON m.id = matched.id
        LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
        LEFT JOIN market_list_serving mls ON mls.market_id = m.id
        LEFT JOIN market_latest_prices mlp ON mlp.market_id = m.id
        ORDER BY
            CASE
                WHEN COALESCE(mss.is_trading_closed, FALSE) = FALSE
                 AND COALESCE(mss.has_settle, FALSE) = FALSE
                 AND COALESCE(mss.has_propose, FALSE) = FALSE
                 AND COALESCE(mss.settlement_code, 0) = 0
                 AND (m.end_date IS NULL OR m.end_date >= ?)
                 AND (
                    mls.latest_price IS NOT NULL
                    OR mlp.latest_yes_price IS NOT NULL
                    OR COALESCE(mls.volume_24h, 0) > 0
                    OR COALESCE(mls.trade_count_24h, 0) > 0
                    OR mls.last_trade_at IS NOT NULL
                    OR mls.latest_trade_at IS NOT NULL
                 )
                 AND (
                    COALESCE(mls.latest_price, mlp.latest_yes_price) IS NULL
                    OR (
                        CAST(COALESCE(mls.latest_price, mlp.latest_yes_price) AS DECIMAL(18, 10)) >= 0.05
                        AND CAST(COALESCE(mls.latest_price, mlp.latest_yes_price) AS DECIMAL(18, 10)) <= 0.95
                    )
                 )
                THEN 0 ELSE 1
            END ASC,
            CASE
                WHEN COALESCE(mss.is_trading_closed, FALSE) = FALSE
                 AND COALESCE(mss.has_settle, FALSE) = FALSE
                 AND COALESCE(mss.has_propose, FALSE) = FALSE
                 AND COALESCE(mss.settlement_code, 0) = 0
                 AND (m.end_date IS NULL OR m.end_date >= ?)
                THEN 0 ELSE 1
            END ASC,
            CASE
                WHEN LOWER(COALESCE(m.title, '')) LIKE ? THEN 0
                WHEN LOWER(COALESCE(m.slug, '')) LIKE ? THEN 1
                WHEN LOWER(COALESCE(m.title, '')) LIKE ? THEN 2
                WHEN LOWER(COALESCE(m.slug, '')) LIKE ? THEN 3
                WHEN LOWER(COALESCE(m.category, '')) LIKE ? THEN 4
                ELSE 5
            END ASC,
            CASE
                WHEN COALESCE(mls.trade_count_24h, 0) > 0
                  OR COALESCE(mls.volume_24h, 0) > 0
                THEN 0 ELSE 1
            END ASC,
            CASE
                WHEN mls.latest_price IS NOT NULL OR mlp.latest_yes_price IS NOT NULL
                THEN 0 ELSE 1
            END ASC,
            CASE
                WHEN COALESCE(mls.trade_count_24h, 0) > 0
                  OR COALESCE(mls.volume_24h, 0) > 0
                  OR mls.latest_price IS NOT NULL
                  OR mlp.latest_yes_price IS NOT NULL
                THEN 0 ELSE 1
            END ASC,
            COALESCE(mls.last_trade_at, mls.latest_trade_at) DESC NULLS LAST,
            matched.search_rank DESC,
            m.created_at DESC,
            COALESCE(mls.trade_count_24h, 0) DESC,
            COALESCE(mls.volume_24h, 0) DESC
        LIMIT ?
        """,
        (
            ts_query,
            ts_query,
            ts_query,
            ts_query,
            now_iso,
            now_iso,
            now_iso,
            now_iso,
            now_iso,
            prefix_pattern,
            prefix_pattern,
            contains_pattern,
            contains_pattern,
            prefix_pattern,
            candidate_limit,
        ),
    )
    # Search is an interactive serving path and already reads the materialized
    # market_list_serving projection. Keep ClickHouse enrichment on market
    # detail/list endpoints, but do not make typeahead wait for the remote
    # OrderFilled transport or its timeout fallback.

    def has_serving_data(row: Dict[str, Any]) -> bool:
        return (
            row.get("latest_price") not in (None, "")
            or (_decimal_from_any(row.get("volume_24h")) or Decimal("0")) > 0
            or _int_value(row.get("trade_count_24h"), 0) > 0
            or row.get("last_trade_at") not in (None, "")
            or row.get("latest_trade_at") not in (None, "")
        )

    def has_tradeable_price(row: Dict[str, Any]) -> bool:
        price = _decimal_from_any(row.get("latest_price"))
        if price is None:
            return True
        return DEFAULT_ACTIVE_MARKET_MIN_PRICE <= price <= DEFAULT_ACTIVE_MARKET_MAX_PRICE

    for row in rows:
        status = str(row.get("status") or "").lower()
        if status in {"active", "open_no_data", "open_terminal"}:
            if not has_serving_data(row):
                row["status"] = "open_no_data"
            elif has_tradeable_price(row):
                row["status"] = "active"
            else:
                row["status"] = "open_terminal"

    query_lower = cleaned.lower()

    def text_rank(row: Dict[str, Any]) -> int:
        title = str(row.get("title") or "").lower()
        slug = str(row.get("slug") or "").lower()
        category = str(row.get("category") or "").lower()
        if title.startswith(query_lower):
            return 0
        if slug.startswith(query_lower):
            return 1
        if query_lower in title:
            return 2
        if query_lower in slug:
            return 3
        if category.startswith(query_lower):
            return 4
        return 5

    def timestamp_sort_value(value: Any) -> float:
        if value in (None, ""):
            return 0.0
        if hasattr(value, "timestamp"):
            try:
                return float(value.timestamp())
            except (OverflowError, OSError, TypeError, ValueError):
                return 0.0
        try:
            return float(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
        except (OverflowError, OSError, TypeError, ValueError):
            return 0.0

    def numeric_sort_value(value: Any) -> float:
        parsed = _decimal_from_any(value)
        return float(parsed or Decimal("0"))

    def row_sort_key(row: Dict[str, Any]) -> tuple:
        status = str(row.get("status") or "").lower()
        status_rank = 0 if status == "active" else 1 if status == "open_terminal" else 2 if status == "open_no_data" else 3
        recent_trade = timestamp_sort_value(row.get("last_trade_at") or row.get("latest_trade_at"))
        created = timestamp_sort_value(row.get("created_at"))
        trade_count = _int_value(row.get("trade_count_24h"), 0)
        volume = numeric_sort_value(row.get("volume_24h"))
        rank = numeric_sort_value(row.get("search_rank"))
        return (
            status_rank,
            text_rank(row),
            0 if (trade_count > 0 or volume > 0) else 1,
            0 if row.get("latest_price") not in (None, "") else 1,
            -recent_trade,
            -rank,
            -created,
            -trade_count,
            -volume,
        )

    rows.sort(key=row_sort_key)
    return {
        "items": [
            _market_list_item(dependencies, row)
            for row in rows[:limit]
        ]
    }


def get_market_by_slug(
    ctx: Mapping[str, Any],
    slug: str,
) -> Optional[dict]:
    return _get_market_by_slug(MarketLookupDependencies.from_context(ctx), slug)


def _get_market_by_slug(
    dependencies: MarketLookupDependencies,
    slug: str,
) -> Optional[dict]:
    now_iso = dependencies.utc_now_iso()
    status_case = dependencies.build_market_status_case(now_iso)
    market = dependencies.query_one(
        f"""
        SELECT
            m.*,
            {status_case} AS status,
            COALESCE(mss_detail.settlement_code, 0) AS settlement_code,
            COALESCE(mss_detail.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
            mss_detail.settlement_source,
            mss_detail.settlement_raw,
            mss_detail.settlement_event_id,
            mss_detail.settlement_event_time,
            mss_detail.settlement_transaction,
            COALESCE(mss_detail.is_trading_closed, FALSE) AS is_trading_closed,
            COALESCE(mss_detail.is_resolved, FALSE) AS is_resolved,
            COALESCE(mss_detail.is_final, FALSE) AS is_final,
            COALESCE(mss_detail.completion_status, 'OPEN') AS completion_status,
            mss_detail.completion_source,
            mss_detail.completion_time,
            COALESCE(mss_detail.gamma_closed, FALSE) AS gamma_closed,
            mss_detail.gamma_closed_time,
            mlp.latest_yes_price,
            mlp.latest_no_price,
            mlp.latest_price
        FROM markets m
        LEFT JOIN market_status_snapshot mss_detail ON mss_detail.market_id = m.id
        LEFT JOIN market_latest_prices mlp ON mlp.market_id = m.id
        WHERE m.slug = ? COLLATE NOCASE
        LIMIT 1
        """,
        (now_iso, slug),
    )
    return market or None


def get_market_by_id(
    ctx: Mapping[str, Any],
    market_id: int,
) -> Optional[dict]:
    return _get_market_by_id(MarketLookupDependencies.from_context(ctx), market_id)


def _get_market_by_id(
    dependencies: MarketLookupDependencies,
    market_id: int,
) -> Optional[dict]:
    now_iso = dependencies.utc_now_iso()
    status_case = dependencies.build_market_status_case(now_iso)
    market = dependencies.query_one(
        f"""
        SELECT
            m.*,
            {status_case} AS status,
            COALESCE(mss_detail.settlement_code, 0) AS settlement_code,
            COALESCE(mss_detail.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
            mss_detail.settlement_source,
            mss_detail.settlement_raw,
            mss_detail.settlement_event_id,
            mss_detail.settlement_event_time,
            mss_detail.settlement_transaction,
            COALESCE(mss_detail.is_trading_closed, FALSE) AS is_trading_closed,
            COALESCE(mss_detail.is_resolved, FALSE) AS is_resolved,
            COALESCE(mss_detail.is_final, FALSE) AS is_final,
            COALESCE(mss_detail.completion_status, 'OPEN') AS completion_status,
            mss_detail.completion_source,
            mss_detail.completion_time,
            COALESCE(mss_detail.gamma_closed, FALSE) AS gamma_closed,
            mss_detail.gamma_closed_time,
            mlp.latest_yes_price,
            mlp.latest_no_price,
            mlp.latest_price
        FROM markets m
        LEFT JOIN market_status_snapshot mss_detail ON mss_detail.market_id = m.id
        LEFT JOIN market_latest_prices mlp ON mlp.market_id = m.id
        WHERE m.id = ?
        LIMIT 1
        """,
        (now_iso, market_id),
    )
    return market or None


def get_trades_by_market_id(
    ctx: Mapping[str, Any],
    market_id: int,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    return _get_trades_by_market_id(
        MarketTradeReadDependencies.from_context(ctx),
        market_id,
        limit=limit,
        offset=offset,
    )


def _get_trades_by_market_id(
    dependencies: MarketTradeReadDependencies,
    market_id: int,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    clickhouse_rows = clickhouse_orderfilled_service.get_market_trades(
        dependencies.source,
        market_id,
        limit=limit,
        offset=offset,
    )
    if clickhouse_rows is not None:
        return clickhouse_rows
    if clickhouse_orderfilled_service.clickhouse_orderfilled_enabled():
        return []
    trade_source = dependencies.get_existing_trade_read_source()
    if trade_source is None:
        return []
    if dependencies.identifier_name(trade_source) == dependencies.trade_v2_core_table:
        rows = dependencies.query_all(
            f"""
            SELECT
                {dependencies.get_trade_market_projection_sql('t')}
            FROM {trade_source} t
            WHERE t.market_id = ?
            ORDER BY t.block_time DESC, t.block_number DESC, t.log_index DESC
            LIMIT ? OFFSET ?
            """,
            (market_id, limit, offset),
        )
    else:
        rows = dependencies.query_all(
            f"""
            SELECT
                tx_hash, log_index, market_id, maker, taker, price, size, side, outcome,
                token_id, timestamp, block_number, order_hash, maker_asset_id, taker_asset_id,
                maker_amount, taker_amount, fee, contract
            FROM {trade_source}
            WHERE market_id = ?
            ORDER BY timestamp DESC, block_number DESC, log_index DESC
            LIMIT ? OFFSET ?
            """,
            (market_id, limit, offset),
        )
    return [dependencies.normalize_trade(row) for row in rows]


def get_recent_trades_snapshot(
    ctx: Mapping[str, Any],
    limit: int = 24,
) -> List[Dict[str, Any]]:
    return _get_recent_trades_snapshot(
        RecentTradeDependencies.from_context(ctx),
        limit=limit,
    )


def _get_recent_trades_snapshot(
    dependencies: RecentTradeDependencies,
    limit: int = 24,
) -> List[Dict[str, Any]]:
    cache_key = json.dumps({"limit": limit}, sort_keys=True, ensure_ascii=True)
    return dependencies.get_snapshot_payload(
        "snapshot:trades_recent",
        cache_key,
        lambda: dependencies.get_recent_trades(limit=limit),
        ttl_seconds=15,
    )


def get_oracle_events_by_market_id(
    ctx: Mapping[str, Any],
    market_id: int,
    market: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    return _get_oracle_events_by_market_id(
        MarketOracleDependencies.from_context(ctx),
        market_id,
        market=market,
    )


def _get_oracle_events_by_market_id(
    dependencies: MarketOracleDependencies,
    market_id: int,
    market: Optional[dict] = None,
) -> List[Dict[str, Any]]:
    market = (
        market
        if market is not None
        else _get_market_by_id(dependencies.lookup, market_id)
    )
    identity = MarketIdentity.from_row(market) if market else None
    backend = str(
        dependencies.get_backend() if dependencies.get_backend is not None else ""
    ).strip().lower()
    if backend in {"postgres", "postgresql"} and identity:
        terms = oracle_event_lookup_terms(identity)
        union_sql = "\nUNION ALL\n".join(
            f"SELECT oe.* FROM oracle_events oe WHERE oe.{column_name} = ?"
            for column_name, _value in terms
        )
        rows = dependencies.query_all(
            f"""
            WITH matched_events AS (
                {union_sql}
            ),
            dedup_events AS (
                SELECT DISTINCT ON (id) *
                FROM matched_events
                ORDER BY id
            )
            SELECT
                oe.id, oe.tx_hash, oe.log_index, oe.block_number, oe.event_time, oe.event_status, oe.external_market_id,
                COALESCE(oe.market_id, m.id) AS market_id, COALESCE(m.title, oe.market_title) AS market_title,
                oe.matched_by, COALESCE(NULLIF(oe.question_id, ''), m.question_id) AS question_id,
                COALESCE(NULLIF(oe.condition_id, ''), m.condition_id) AS condition_id,
                oe.proposed_price, oe.settled_price, oe.payout, oe.requester, oe.proposer, oe.disputer,
                oe.proposal_transaction, oe.settlement_transaction, oe.source_adapter, oe.source_oracle,
                m.slug AS market_slug, m.category AS market_category,
                COALESCE(mss.completion_status, 'OPEN') AS completion_status,
                COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
                COALESCE(mss.is_resolved, FALSE) AS is_resolved,
                COALESCE(mss.is_final, FALSE) AS is_final,
                COALESCE(mss.settlement_code, 0) AS snapshot_settlement_code,
                COALESCE(mss.settlement_outcome, 'UNKNOWN') AS snapshot_settlement_outcome,
                mss.settlement_source AS snapshot_settlement_source
            FROM dedup_events oe
            LEFT JOIN markets m ON m.id = COALESCE(oe.market_id, ?)
            LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
            ORDER BY oe.block_number ASC NULLS LAST, oe.id ASC
            """,
            (*[value for _column_name, value in terms], market_id),
        )
        return [dependencies.normalize_oracle_event(row) for row in rows]

    if market:
        where_sql, where_params = oracle_event_lookup_clause(identity or MarketIdentity.from_row(market), "oe")
    else:
        where_sql, where_params = "oe.market_id = ?", (market_id,)
    rows = dependencies.query_all(
        f"""
        SELECT
            oe.id, oe.tx_hash, oe.log_index, oe.block_number, oe.event_time, oe.event_status, oe.external_market_id,
            COALESCE(oe.market_id, m.id) AS market_id, COALESCE(m.title, oe.market_title) AS market_title,
            oe.matched_by, COALESCE(NULLIF(oe.question_id, ''), m.question_id) AS question_id,
            COALESCE(NULLIF(oe.condition_id, ''), m.condition_id) AS condition_id,
            oe.proposed_price, oe.settled_price, oe.payout, oe.requester, oe.proposer, oe.disputer,
            oe.proposal_transaction, oe.settlement_transaction, oe.source_adapter, oe.source_oracle,
            m.slug AS market_slug, m.category AS market_category,
            COALESCE(mss.completion_status, 'OPEN') AS completion_status,
            COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
            COALESCE(mss.is_resolved, FALSE) AS is_resolved,
            COALESCE(mss.is_final, FALSE) AS is_final,
            COALESCE(mss.settlement_code, 0) AS snapshot_settlement_code,
            COALESCE(mss.settlement_outcome, 'UNKNOWN') AS snapshot_settlement_outcome,
            mss.settlement_source AS snapshot_settlement_source
        FROM oracle_events oe
        LEFT JOIN markets m ON m.id = COALESCE(oe.market_id, ?)
        LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
        WHERE {where_sql}
        ORDER BY oe.block_number ASC, oe.id ASC
        """,
        (market_id, *where_params),
    )
    return [dependencies.normalize_oracle_event(row) for row in rows]


def get_recent_oracle_snapshot(
    ctx: Mapping[str, Any],
    limit: int = 24,
) -> List[Dict[str, Any]]:
    dependencies = RecentOracleDependencies.from_context(ctx)
    cache_key = json.dumps({"limit": limit, "v": 2}, sort_keys=True, ensure_ascii=True)
    return dependencies.get_snapshot_payload(
        "snapshot:oracle_recent",
        cache_key,
        lambda: dependencies.get_recent_oracle_events(limit=limit),
        ttl_seconds=30,
    )


def _json_payload(value: Any, expected_type: type) -> Optional[Any]:
    if isinstance(value, expected_type):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        return parsed if isinstance(parsed, expected_type) else None
    return None


def _get_market_workspace_serving_row(
    ctx: Mapping[str, Any],
    market_id: int,
) -> Optional[Dict[str, Any]]:
    return _read_market_workspace_serving_row(
        MarketServingReadDependencies.from_context(ctx),
        market_id,
    )


def _read_market_workspace_serving_row(
    dependencies: MarketServingReadDependencies,
    market_id: int,
) -> Optional[Dict[str, Any]]:
    if not dependencies.table_exists("market_workspace_serving"):
        return None
    return dependencies.query_one(
        """
        SELECT market_id, detail_payload, price_payload, oracle_summary, content_summary, updated_at
        FROM market_workspace_serving
        WHERE market_id = ?
        LIMIT 1
        """,
        (market_id,),
    )


def _get_market_workspace_detail_payload(
    ctx: Mapping[str, Any],
    market_id: int,
) -> Optional[Dict[str, Any]]:
    return _read_market_workspace_detail_payload(
        MarketServingReadDependencies.from_context(ctx),
        market_id,
    )


def _read_market_workspace_detail_payload(
    dependencies: MarketServingReadDependencies,
    market_id: int,
) -> Optional[Dict[str, Any]]:
    row = _read_market_workspace_serving_row(dependencies, market_id)
    if not row:
        return None
    payload = _json_payload(row.get("detail_payload"), dict)
    if not payload:
        return None
    payload.setdefault("servingSource", "postgres")
    payload.setdefault("servingUpdatedAt", row.get("updated_at"))
    return payload


def _get_market_workspace_price_payload(
    ctx: Mapping[str, Any],
    market_id: int,
) -> Optional[Dict[str, Any]]:
    return _read_market_workspace_price_payload(
        MarketServingReadDependencies.from_context(ctx),
        market_id,
    )


def _read_market_workspace_price_payload(
    dependencies: MarketServingReadDependencies,
    market_id: int,
) -> Optional[Dict[str, Any]]:
    row = _read_market_workspace_serving_row(dependencies, market_id)
    if not row:
        return None
    payload = _json_payload(row.get("price_payload"), dict)
    if not payload:
        return None
    payload.setdefault("marketId", market_id)
    payload.setdefault("localMarketId", market_id)
    payload.setdefault("servingSource", "postgres")
    payload.setdefault("servingUpdatedAt", row.get("updated_at"))
    return payload


def _get_market_chart_serving_payload(
    ctx: Mapping[str, Any],
    market_id: int,
    range_name: str,
    interval: str,
) -> Optional[Dict[str, Any]]:
    return _read_market_chart_serving_payload(
        MarketServingReadDependencies.from_context(ctx),
        market_id,
        range_name,
        interval,
    )


def _read_market_chart_serving_payload(
    dependencies: MarketServingReadDependencies,
    market_id: int,
    range_name: str,
    interval: str,
) -> Optional[Dict[str, Any]]:
    if not dependencies.table_exists("market_chart_serving"):
        return None
    normalized_range = str(range_name or "1d").strip().lower()
    normalized_interval = str(interval or "5m").strip().lower()
    row = dependencies.query_one(
        """
        SELECT market_id, range_name, interval_name, kind, history_status, point_count, points, updated_at
        FROM market_chart_serving
        WHERE market_id = ? AND range_name = ?
        ORDER BY CASE WHEN interval_name = ? THEN 0 ELSE 1 END, updated_at DESC
        LIMIT 1
        """,
        (market_id, normalized_range, normalized_interval),
    )
    if not row:
        return None
    points = _json_payload(row.get("points"), list) or []
    history_status = str(row.get("history_status") or ("ok" if points else "missing"))
    return {
        "marketId": market_id,
        "localMarketId": market_id,
        "range": row.get("range_name") or normalized_range,
        "interval": row.get("interval_name") or normalized_interval,
        "kind": row.get("kind") or "probability",
        "historyStatus": history_status,
        "points": points,
        "priceSource": "serving-history" if history_status == "ok" else "serving-snapshot",
        "servingSource": "postgres",
        "servingUpdatedAt": row.get("updated_at"),
    }


def _is_usable_market_chart_serving(payload: Optional[Dict[str, Any]]) -> bool:
    if not payload:
        return False
    status = str(payload.get("historyStatus") or "").strip().lower()
    if status == "ok":
        return True
    points = payload.get("points")
    if not isinstance(points, list) or len(points) <= 2:
        return False
    _, distinct_count = _chart_point_stats(points)
    return distinct_count > 1 and status not in {"missing", "snapshot", "flat"}


def _last_chart_price(chart: Optional[Dict[str, Any]]) -> tuple[Optional[Any], Optional[Any], Optional[Any]]:
    if not isinstance(chart, dict):
        return None, None, None
    points = chart.get("points")
    if not isinstance(points, list) or not points:
        return None, None, None
    last = points[-1]
    if not isinstance(last, dict):
        return None, None, None
    return last.get("yesPrice"), last.get("noPrice"), last.get("timestamp")


def _merge_chart_latest_price(price: Dict[str, Any], chart: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    latest_yes, latest_no, updated_at = _last_chart_price(chart)
    if latest_yes in (None, ""):
        return price
    merged = dict(price or {})
    merged["latestPrice"] = latest_yes
    merged["latestYesPrice"] = latest_yes
    if latest_no not in (None, ""):
        merged["latestNoPrice"] = latest_no
    if updated_at:
        merged["updatedAt"] = updated_at
    merged["priceSource"] = "chart-history"
    return merged


def get_market_price_summary(
    ctx: Mapping[str, Any],
    market_id: int,
    market: Optional[dict] = None,
    *,
    include_runtime_price: bool = False,
    include_recent_stats: bool = False,
) -> Dict[str, Any]:
    return _get_market_price_summary(
        MarketPriceDependencies.from_context(ctx),
        market_id,
        market=market,
        include_runtime_price=include_runtime_price,
        include_recent_stats=include_recent_stats,
    )


def _get_market_price_summary(
    dependencies: MarketPriceDependencies,
    market_id: int,
    market: Optional[dict] = None,
    *,
    include_runtime_price: bool = False,
    include_recent_stats: bool = False,
) -> Dict[str, Any]:
    if not include_runtime_price and not include_recent_stats:
        serving_payload = _read_market_workspace_price_payload(
            dependencies.serving,
            market_id,
        )
        if serving_payload is not None:
            return serving_payload
    if market is None and not include_runtime_price and not include_recent_stats:
        cache_key = json.dumps({"marketId": int(market_id), "v": 3}, sort_keys=True, ensure_ascii=True)
        return dependencies.get_snapshot_payload(
            "snapshot:market_price_summary",
            cache_key,
            lambda: _get_market_price_summary(
                dependencies,
                market_id,
                market=_get_market_by_id(dependencies.lookup, market_id),
                include_runtime_price=False,
                include_recent_stats=False,
            ),
            ttl_seconds=90,
        )
    market = (
        market
        if market is not None
        else _get_market_by_id(dependencies.lookup, market_id)
    )
    summary_row = dependencies.query_one(
        """
        SELECT
            COALESCE(mlp.market_id, mls.market_id) AS market_id,
            COALESCE(mlp.latest_price, mls.latest_price) AS latest_price,
            COALESCE(mlp.latest_yes_price, mls.latest_price) AS latest_yes_price,
            mlp.latest_no_price,
            COALESCE(mlp.latest_trade_at, mls.latest_trade_at, mls.last_trade_at) AS latest_trade_at,
            mls.price_24h_ago AS serving_price_24h_ago,
            mls.trade_count_24h AS serving_trade_count_24h,
            mls.volume_24h AS serving_volume_24h
        FROM (SELECT ? AS market_id) requested
        LEFT JOIN market_latest_prices mlp ON mlp.market_id = requested.market_id
        LEFT JOIN market_list_serving mls ON mls.market_id = requested.market_id
        LIMIT 1
        """,
        (market_id,),
    ) or {}
    latest_price = summary_row.get("latest_yes_price") or summary_row.get("latest_price")
    latest_yes_price = summary_row.get("latest_yes_price")
    latest_no_price = summary_row.get("latest_no_price")
    updated_at = summary_row.get("latest_trade_at")
    clob_snapshot = (
        dependencies.get_market_clob_price_snapshot(market)
        if include_runtime_price
        else None
    )
    if clob_snapshot:
        latest_price = clob_snapshot.get("latestYesPrice") or clob_snapshot.get("latestPrice") or latest_price
        latest_yes_price = clob_snapshot.get("latestYesPrice") or latest_yes_price
        latest_no_price = clob_snapshot.get("latestNoPrice") or latest_no_price
        updated_at = clob_snapshot.get("updatedAt") or updated_at

    recent_stats = {
        "price_24h_ago": summary_row.get("serving_price_24h_ago"),
        "price_1h_ago": None,
        "trade_count_24h": summary_row.get("serving_trade_count_24h") or 0,
        "volume_24h": summary_row.get("serving_volume_24h") or 0,
    }
    trade_source = (
        dependencies.get_existing_trade_read_source()
        if include_recent_stats
        else None
    )
    if trade_source is None:
        pass
    elif dependencies.identifier_name(trade_source) == dependencies.trade_v2_core_table:
        recent_stats = dependencies.query_one(
            f"""
            SELECT
                MAX(CASE WHEN block_time >= ? THEN price END) AS price_24h_ago,
                MAX(CASE WHEN block_time >= ? THEN price END) AS price_1h_ago,
                SUM(CASE WHEN block_time >= ? THEN 1 ELSE 0 END) AS trade_count_24h,
                COALESCE(SUM(CASE WHEN block_time >= ? THEN size * price END), 0) AS volume_24h
            FROM {trade_source}
            WHERE market_id = ?
            """,
            (
                dependencies.iso_days_before(updated_at, 1)
                if updated_at
                else dependencies.utc_date_days_ago(1),
                (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                dependencies.iso_days_before(updated_at, 1)
                if updated_at
                else dependencies.utc_date_days_ago(1),
                dependencies.iso_days_before(updated_at, 1)
                if updated_at
                else dependencies.utc_date_days_ago(1),
                market_id,
            ),
        )
    else:
        recent_stats = dependencies.query_one(
            f"""
            SELECT
                MAX(CASE WHEN timestamp >= ? THEN price END) AS price_24h_ago,
                MAX(CASE WHEN timestamp >= ? THEN price END) AS price_1h_ago,
                COUNT(*) AS trade_count_24h,
                COALESCE(SUM(CASE WHEN timestamp >= ? THEN size * price END), 0) AS volume_24h
            FROM {trade_source}
            WHERE market_id = ?
            """,
            (
                dependencies.iso_days_before(updated_at, 1)
                if updated_at
                else dependencies.utc_date_days_ago(1),
                (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
                dependencies.iso_days_before(updated_at, 1)
                if updated_at
                else dependencies.utc_date_days_ago(1),
                market_id,
            ),
        )

    def _change(current: Any, past: Any) -> Optional[str]:
        if current in (None, "") or past in (None, ""):
            return None
        try:
            delta = Decimal(str(current)) - Decimal(str(past))
        except (InvalidOperation, ValueError, TypeError):
            return None
        return format(delta, "f")

    return {
        "marketId": market_id,
        "localMarketId": market_id,
        "latestPrice": dependencies.format_trade_decimal(
            latest_yes_price or latest_price
        ),
        "latestYesPrice": dependencies.format_trade_decimal(latest_yes_price),
        "latestNoPrice": dependencies.format_trade_decimal(latest_no_price),
        "change1h": clob_snapshot.get("change1h") if clob_snapshot else _change(latest_price, recent_stats.get("price_1h_ago")),
        "change24h": clob_snapshot.get("change24h") if clob_snapshot else _change(latest_price, recent_stats.get("price_24h_ago")),
        "volume24h": dependencies.format_trade_decimal(
            recent_stats.get("volume_24h")
        ),
        "tradeCount24h": int(recent_stats.get("trade_count_24h") or 0),
        "updatedAt": updated_at,
    }


def get_market_chart_payload(
    ctx: Mapping[str, Any],
    market_id: int,
    range_name: str = "1d",
    interval: str = "5m",
    market: Optional[dict] = None,
    price: Optional[Dict[str, Any]] = None,
    include_runtime_series: bool = True,
) -> Dict[str, Any]:
    return _get_market_chart_payload(
        MarketChartDependencies.from_context(ctx),
        market_id,
        range_name=range_name,
        interval=interval,
        market=market,
        price=price,
        include_runtime_series=include_runtime_series,
    )


def _get_market_chart_payload(
    dependencies: MarketChartDependencies,
    market_id: int,
    range_name: str = "1d",
    interval: str = "5m",
    market: Optional[dict] = None,
    price: Optional[Dict[str, Any]] = None,
    include_runtime_series: bool = True,
) -> Dict[str, Any]:
    serving_payload = _read_market_chart_serving_payload(
        dependencies.serving,
        market_id,
        range_name,
        interval,
    )
    if _is_usable_market_chart_serving(serving_payload):
        return serving_payload
    if market is None and price is None:
        cache_key = json.dumps(
            {
                "marketId": int(market_id),
                "range": str(range_name or "1d").strip().lower(),
                "interval": str(interval or "5m").strip().lower(),
                "includeRuntimeSeries": bool(include_runtime_series),
                "v": 11,
            },
            sort_keys=True,
            ensure_ascii=True,
        )
        return dependencies.get_snapshot_payload(
            "snapshot:market_chart",
            cache_key,
            lambda: _get_market_chart_payload(
                dependencies,
                market_id,
                range_name=range_name,
                interval=interval,
                market=_get_market_by_id(dependencies.lookup, market_id),
                price=None,
                include_runtime_series=include_runtime_series,
            ),
            ttl_seconds=180,
        )
    market = (
        market
        if market is not None
        else _get_market_by_id(dependencies.lookup, market_id)
    )
    chart_context = (
        _extract_market_chart_context(
            dependencies.get_yahoo_market_snapshot,
            market,
            range_name,
        )
        if include_runtime_series
        else None
    )
    if chart_context:
        return {
            "marketId": market_id,
            "localMarketId": market_id,
            "range": range_name,
            "interval": interval,
            "kind": chart_context.get("kind"),
            "sourceSymbol": chart_context.get("sourceSymbol"),
            "sourceLabel": chart_context.get("sourceLabel"),
            "pairLabel": chart_context.get("pairLabel"),
            "currentUnderlyingPrice": chart_context.get("currentUnderlyingPrice"),
            "underlyingChangePercent": chart_context.get("underlyingChangePercent"),
            "targetPrice": chart_context.get("targetPrice"),
            "targetLabel": chart_context.get("targetLabel"),
            "referenceRule": chart_context.get("referenceRule"),
            "points": chart_context.get("points"),
        }
    price = (
        price
        if price is not None
        else _get_market_price_summary(
            dependencies.price,
            market_id,
            market=market,
        )
    )
    latest = price.get("latestYesPrice") or price.get("latestPrice")
    latest_decimal = _decimal_from_any(latest)
    recent_volume = _decimal_from_any(price.get("volume24h")) or Decimal("0")
    recent_trades = int(price.get("tradeCount24h") or 0)
    points: List[Dict[str, Any]] = []
    price_source = "missing"
    if recent_volume > 0 or recent_trades > 0:
        limit = 400
        if range_name == "7d":
            limit = 700
        clickhouse_points = clickhouse_orderfilled_service.get_price_series(
            dependencies.source,
            market_id,
            limit=limit,
        )
        points = (
            clickhouse_points
            if clickhouse_points is not None
            else dependencies.get_trade_derived_market_price_series(
                market_id,
                limit=limit,
            )
        )
        if points:
            price_source = "orderfilled-history" if clickhouse_points is not None else "trade-history"
    if not points:
        limit = 700 if range_name == "7d" else 400
        clickhouse_points = clickhouse_orderfilled_service.get_price_series(
            dependencies.source,
            market_id,
            limit=limit,
        )
        if clickhouse_points:
            points = clickhouse_points
            price_source = "orderfilled-history"
    if include_runtime_series:
        point_count, distinct_count = _chart_point_stats(points)
        needs_clob_series = (
            not points
            or point_count <= 2
            or distinct_count <= 1
        )
        if needs_clob_series:
            clob_points = dependencies.get_market_clob_price_series(
                market,
                range_name=range_name,
                interval=interval,
            )
            clob_count, clob_distinct_count = _chart_point_stats(clob_points)
            if clob_count > point_count and (clob_distinct_count > distinct_count or distinct_count <= 1):
                points = clob_points
                price_source = "clob-history"
    effective_range = range_name
    effective_interval = interval
    if not points and latest not in (None, ""):
        timestamp = price.get("updatedAt") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        points = [
            {"timestamp": timestamp, "yesPrice": latest, "noPrice": price.get("latestNoPrice")},
            {"timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "yesPrice": latest, "noPrice": price.get("latestNoPrice")},
        ]
        effective_range = "snapshot"
        effective_interval = "snapshot"
        price_source = "snapshot"
    history_status = _chart_history_status(effective_range, effective_interval, points)
    return {
        "marketId": market_id,
        "localMarketId": market_id,
        "range": effective_range,
        "interval": effective_interval,
        "kind": "probability",
        "historyStatus": history_status,
        "priceSource": price_source,
        "points": points,
    }


def get_market_oracle_payload(
    ctx: Mapping[str, Any],
    market_id: int,
    market: Optional[dict] = None,
) -> Dict[str, Any]:
    return _get_market_oracle_payload(
        MarketOraclePayloadDependencies.from_context(ctx),
        market_id,
        market=market,
    )


def _build_market_oracle_payload(
    dependencies: MarketOraclePayloadDependencies,
    market_id: int,
    market: dict,
) -> Dict[str, Any]:
    return {
        "marketId": market_id,
        "localMarketId": market_id,
        "gammaMarketId": market.get("gamma_market_id"),
        "questionId": market.get("question_id"),
        "conditionId": market.get("condition_id"),
        "oracle": market.get("oracle"),
        "currentStatus": market.get("status"),
        "completionStatus": market.get("completion_status"),
        "isTradingClosed": _truthy_flag(market.get("is_trading_closed")),
        "isResolved": _truthy_flag(market.get("is_resolved")),
        "isFinal": _truthy_flag(market.get("is_final")),
        "settlementOutcome": market.get("settlement_outcome"),
        "settlementSource": market.get("settlement_source"),
        "timeline": _get_oracle_events_by_market_id(
            dependencies.oracle,
            market_id,
            market=market,
        ),
    }


def _get_market_oracle_payload(
    dependencies: MarketOraclePayloadDependencies,
    market_id: int,
    market: Optional[dict] = None,
) -> Dict[str, Any]:
    market = (
        market
        if market is not None
        else _get_market_by_id(dependencies.oracle.lookup, market_id)
    )
    if not market:
        return {"error": "Market not found", "marketId": market_id, "_status": 404}
    cache_key = json.dumps({"marketId": int(market_id), "v": 4}, sort_keys=True, ensure_ascii=True)

    return dependencies.get_snapshot_payload(
        "snapshot:market_oracle_payload",
        cache_key,
        lambda: _build_market_oracle_payload(dependencies, market_id, market),
        ttl_seconds=60,
    )


def enrich_market_rows_with_runtime_prices(
    ctx: Mapping[str, Any],
    rows: List[Dict[str, Any]],
    *,
    max_updates: int = 18,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    return _enrich_market_rows_with_runtime_prices(
        MarketListDependencies.from_context(ctx),
        rows,
        max_updates=max_updates,
        force_refresh=force_refresh,
    )


def _enrich_market_rows_with_runtime_prices(
    dependencies: MarketListDependencies,
    rows: List[Dict[str, Any]],
    *,
    max_updates: int = 18,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    enriched_rows: List[Dict[str, Any]] = [dict(row) for row in rows]
    candidates: List[tuple[int, Dict[str, Any]]] = []
    for index, normalized in enumerate(enriched_rows):
        latest_trade_at = dependencies.parse_iso_datetime(
            normalized.get("last_trade_at") or normalized.get("latest_trade_at")
        )
        is_stale = latest_trade_at is None or (now - latest_trade_at) > timedelta(hours=6)
        needs_runtime_price = force_refresh or normalized.get("latest_price") in (None, "") or is_stale
        if needs_runtime_price and len(candidates) < max_updates:
            candidates.append((index, normalized))
    if not candidates:
        return enriched_rows
    max_workers = min(6, len(candidates))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                dependencies.get_market_clob_price_snapshot,
                candidate,
            ): index
            for index, candidate in candidates
        }
        for future in as_completed(future_map):
            index = future_map[future]
            try:
                snapshot = future.result()
            except Exception:
                dependencies.application.logger.exception(
                    "runtime market price enrichment failed index=%s",
                    index,
                )
                continue
            runtime_price = snapshot.get("latestPrice") if snapshot else None
            if runtime_price not in (None, ""):
                enriched_rows[index]["latest_price"] = runtime_price
    return enriched_rows


def enrich_market_rows_with_24h_change(
    ctx: Mapping[str, Any],
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return _enrich_market_rows_with_24h_change(
        MarketListDependencies.from_context(ctx),
        rows,
    )


def _enrich_market_rows_with_24h_change(
    dependencies: MarketListDependencies,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    market_ids = [int(row["id"]) for row in rows if row.get("id") is not None]
    if not market_ids:
        return rows
    trade_source = dependencies.get_existing_trade_read_source()
    if trade_source is None:
        return rows

    placeholders = ", ".join("?" for _ in market_ids)
    threshold = dependencies.utc_date_days_ago(1)
    if dependencies.identifier_name(trade_source) == dependencies.trade_v2_core_table:
        time_column = "block_time"
        order_columns = "block_time DESC, block_number DESC, log_index DESC"
        yes_price_expr = "CASE WHEN outcome_code = 2 THEN 1 - price ELSE price END"
    else:
        time_column = "timestamp"
        order_columns = "timestamp DESC, block_number DESC, log_index DESC"
        yes_price_expr = "CASE WHEN UPPER(COALESCE(outcome, '')) = 'NO' THEN 1 - price ELSE price END"

    price_rows = dependencies.query_all(
        f"""
        SELECT market_id, price
        FROM (
            SELECT
                market_id,
                {yes_price_expr} AS price,
                ROW_NUMBER() OVER (
                    PARTITION BY market_id
                    ORDER BY {order_columns}
                ) AS row_num
            FROM {trade_source}
            WHERE market_id IN ({placeholders}) AND {time_column} <= ?
        ) ranked_prices
        WHERE row_num = 1
        """,
        (*market_ids, threshold),
    )
    price_map = {int(row["market_id"]): row.get("price") for row in price_rows if row.get("market_id") is not None}
    enriched_rows: List[Dict[str, Any]] = []
    for row in rows:
        normalized = dict(row)
        market_id = normalized.get("id")
        if market_id is not None:
            normalized["price_24h_ago"] = price_map.get(int(market_id))
        enriched_rows.append(normalized)
    return enriched_rows


def _market_outcome_count(
    dependencies: MarketListItemDependencies,
    row: Dict[str, Any],
) -> int:
    native_count = int(row.get("native_outcome_count") or 0)
    if native_count > 1:
        return native_count
    token_ids = dependencies.parse_json_list(row.get("clob_token_ids"))
    if token_ids:
        return len(token_ids)
    yes_token = row.get("yes_token_id")
    no_token = row.get("no_token_id")
    return int(bool(yes_token)) + int(bool(no_token))


def _market_change(
    dependencies: MarketListItemDependencies,
    current: Any,
    past: Any,
) -> Any:
    if current in (None, "") or past in (None, ""):
        return None
    try:
        delta = Decimal(str(current)) - Decimal(str(past))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return dependencies.format_trade_decimal(delta)


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"", "0", "false", "none", "null"}


def _settlement_code(row: Dict[str, Any]) -> int:
    return _int_value(row.get("settlement_code"), 0)


def _settlement_payload(row: Dict[str, Any], *, include_raw: bool = False) -> Dict[str, Any]:
    payload = {
        "settlementCode": _settlement_code(row),
        "settlementOutcome": row.get("settlement_outcome") or "UNKNOWN",
        "settlementSource": row.get("settlement_source"),
        "settlementEventId": row.get("settlement_event_id"),
        "settlementEventTime": row.get("settlement_event_time"),
        "settlementTransaction": row.get("settlement_transaction"),
        "completionStatus": row.get("completion_status") or "OPEN",
        "completionSource": row.get("completion_source"),
        "completionTime": row.get("completion_time"),
        "isTradingClosed": _truthy_flag(row.get("is_trading_closed")),
        "isResolved": _truthy_flag(row.get("is_resolved")),
        "isFinal": _truthy_flag(row.get("is_final")),
        "gammaClosed": _truthy_flag(row.get("gamma_closed")),
        "gammaClosedTime": row.get("gamma_closed_time"),
    }
    if include_raw:
        payload["settlementRaw"] = row.get("settlement_raw")
    return payload


def _market_status_from_snapshot(row: Dict[str, Any], now_iso: str) -> str:
    completion_status = str(row.get("completion_status") or "").strip().upper()
    if completion_status in {"SETTLED", "CANCELLED", "UNKNOWN"} and _truthy_flag(row.get("is_final")):
        return "Settled"
    if completion_status == "DISPUTED":
        return "Disputed"
    if completion_status == "PROPOSED":
        return "Proposed"
    if _truthy_flag(row.get("is_trading_closed")):
        return "Closed"
    if _settlement_code(row) in {1, 2, 3} or _truthy_flag(row.get("has_settle")):
        return "Settled"
    if _truthy_flag(row.get("has_propose")):
        return "Proposed"
    end_date = row.get("end_date")
    if end_date not in (None, "") and str(end_date) < now_iso:
        return "Closed"
    return "Active"


def _is_postgres_dependencies(dependencies: MarketListDependencies) -> bool:
    backend = str(
        dependencies.get_backend() if dependencies.get_backend is not None else ""
    ).strip().lower()
    return backend in {"postgres", "postgresql"}


def _market_trade_count_24h(row: Dict[str, Any]) -> Optional[int]:
    trade_count = _int_value(row.get("trade_count_24h"), 0)
    if trade_count > 0:
        return trade_count
    return None


def _market_volume_24h(
    dependencies: MarketListItemDependencies,
    row: Dict[str, Any],
) -> Optional[str]:
    volume = _decimal_from_any(row.get("volume_24h")) or Decimal("0")
    if volume > 0:
        return dependencies.format_trade_decimal(volume)
    if row.get("last_trade_at") not in (None, "") or row.get("latest_trade_at") not in (None, ""):
        return None
    return dependencies.format_trade_decimal(volume)


def _market_list_item(
    dependencies: MarketListItemDependencies,
    row: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "localMarketId": row.get("id"),
        "gammaMarketId": row.get("gamma_market_id"),
        "slug": row.get("slug"),
        "title": row.get("title"),
        "conditionId": row.get("condition_id"),
        "questionId": row.get("question_id"),
        "yesTokenId": row.get("yes_token_id"),
        "noTokenId": row.get("no_token_id"),
        "endDate": row.get("end_date"),
        "createdAt": row.get("created_at"),
        "latestPrice": row.get("latest_price"),
        "price24hAgo": row.get("price_24h_ago"),
        "status": row.get("status"),
        "category": row.get("category") or "Uncategorized",
        "tags": dependencies.parse_json_list(row.get("tags")),
        "outcomeCount": _market_outcome_count(dependencies, row),
        "volume24h": _market_volume_24h(dependencies, row),
        "tradeCount24h": _market_trade_count_24h(row),
        "change24h": row.get("change_24h")
        or _market_change(
            dependencies,
            row.get("latest_price"),
            row.get("price_24h_ago"),
        ),
        "lastTradeAt": row.get("last_trade_at") or row.get("latest_trade_at"),
        **_settlement_payload(row),
    }


def _merge_clickhouse_stats(
    dependencies: MarketListDependencies,
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    market_ids = [int(row["id"]) for row in rows if row.get("id") is not None]
    stats = clickhouse_orderfilled_service.get_market_stats(
        dependencies.source,
        market_ids,
        hours=24,
    )
    if not stats:
        return rows
    merged_rows: List[Dict[str, Any]] = []
    for row in rows:
        market_id = row.get("id")
        if market_id is None or int(market_id) not in stats:
            merged_rows.append(row)
            continue
        stat = stats[int(market_id)]
        merged = dict(row)
        for key in ("trade_count_24h", "volume_24h", "latest_price", "last_trade_at", "latest_trade_at"):
            value = stat.get(key)
            if value not in (None, ""):
                merged[key] = value
        merged_rows.append(merged)
    return merged_rows


def _clickhouse_active_market_candidate_rows(
    dependencies: MarketListDependencies,
    now_iso: str,
    limit: int,
) -> List[Dict[str, Any]]:
    activity_rows = clickhouse_orderfilled_service.get_recent_market_activity(
        dependencies.source,
        limit=max(int(limit) * 6, 200),
        hours=DEFAULT_ACTIVE_MARKET_ACTIVITY_HOURS,
    )
    if not activity_rows:
        return []
    stats_by_market_id = {
        int(row["market_id"]): row
        for row in activity_rows
        if row.get("market_id") is not None
    }
    market_ids = list(stats_by_market_id.keys())
    if not market_ids:
        return []
    placeholders = ", ".join("?" for _ in market_ids)
    created_cutoff = _iso_hours_before(now_iso, DEFAULT_ACTIVE_MARKET_MAX_AGE_HOURS)
    detail_rows = dependencies.query_all(
        f"""
        SELECT
            m.id,
            m.slug,
            m.condition_id,
            m.end_date,
            m.created_at,
            CASE WHEN COALESCE(mss.has_settle, FALSE) THEN 1 ELSE 0 END AS has_settle,
            CASE WHEN COALESCE(mss.has_propose, FALSE) THEN 1 ELSE 0 END AS has_propose,
            COALESCE(mss.settlement_code, 0) AS settlement_code,
            COALESCE(mss.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
            mss.settlement_source,
            mss.settlement_event_id,
            mss.settlement_event_time,
            mss.settlement_transaction,
            COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
            COALESCE(mss.is_resolved, FALSE) AS is_resolved,
            COALESCE(mss.is_final, FALSE) AS is_final,
            COALESCE(mss.completion_status, 'OPEN') AS completion_status,
            mss.completion_source,
            mss.completion_time,
            COALESCE(mss.gamma_closed, FALSE) AS gamma_closed,
            mss.gamma_closed_time,
            0 AS trade_count_24h,
            0 AS volume_24h,
            NULL AS latest_price,
            NULL AS last_trade_at,
            NULL AS latest_trade_at,
            NULL AS price_24h_ago
        FROM markets m
        LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
        WHERE m.id IN ({placeholders})
          AND COALESCE(mss.has_settle, FALSE) = FALSE
          AND COALESCE(mss.has_propose, FALSE) = FALSE
          AND COALESCE(mss.is_trading_closed, FALSE) = FALSE
          AND COALESCE(mss.settlement_code, 0) = 0
          AND (m.end_date IS NULL OR m.end_date >= ?)
          AND ({_default_active_market_created_recent_sql()})
          AND {DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL}
        """,
        (*market_ids, now_iso, created_cutoff),
    )
    detail_by_id = {int(row["id"]): row for row in detail_rows if row.get("id") is not None}
    candidates: List[Dict[str, Any]] = []
    for market_id in market_ids:
        row = detail_by_id.get(market_id)
        if not row:
            continue
        merged = dict(row)
        stats = stats_by_market_id.get(market_id, {})
        if not _is_tradeable_probability(stats.get("latest_price")):
            continue
        merged.update(
            {
                "trade_count_24h": stats.get("trade_count_24h") or 0,
                "volume_24h": stats.get("volume_24h") or 0,
                "latest_price": stats.get("latest_price"),
                "last_trade_at": stats.get("last_trade_at"),
                "latest_trade_at": stats.get("latest_trade_at"),
            }
        )
        candidates.append(merged)
        if len(candidates) >= limit:
            break
    return candidates


def _active_market_candidate_select_sql(stats_alias: str) -> str:
    return f"""
            SELECT
                m.id,
                m.slug,
                m.condition_id,
                m.end_date,
                m.created_at,
                CASE WHEN COALESCE(mss.has_settle, FALSE) THEN 1 ELSE 0 END AS has_settle,
                CASE WHEN COALESCE(mss.has_propose, FALSE) THEN 1 ELSE 0 END AS has_propose,
                COALESCE(mss.settlement_code, 0) AS settlement_code,
                COALESCE(mss.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
                mss.settlement_source,
                mss.settlement_event_id,
                mss.settlement_event_time,
                mss.settlement_transaction,
                COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
                COALESCE(mss.is_resolved, FALSE) AS is_resolved,
                COALESCE(mss.is_final, FALSE) AS is_final,
                COALESCE(mss.completion_status, 'OPEN') AS completion_status,
                mss.completion_source,
                mss.completion_time,
                COALESCE(mss.gamma_closed, FALSE) AS gamma_closed,
                mss.gamma_closed_time,
                {stats_alias}.trade_count_24h,
                {stats_alias}.volume_24h,
                {stats_alias}.latest_price,
                {stats_alias}.last_trade_at,
                {stats_alias}.latest_trade_at,
                {stats_alias}.price_24h_ago
            FROM markets m
            LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
            LEFT JOIN market_list_serving {stats_alias} ON {stats_alias}.market_id = m.id
            WHERE COALESCE(mss.has_settle, FALSE) = FALSE
              AND COALESCE(mss.has_propose, FALSE) = FALSE
              AND COALESCE(mss.is_trading_closed, FALSE) = FALSE
              AND COALESCE(mss.settlement_code, 0) = 0
              AND (m.end_date IS NULL OR m.end_date >= ?)
              AND ({_default_active_market_created_recent_sql()})
              AND {DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL}
              AND {_default_active_market_activity_sql(stats_alias)}
              AND {_default_active_market_price_sql(stats_alias)}
              AND {_default_active_market_recent_trade_sql(stats_alias)}
        """


def _event_serving_active_market_candidate_rows(
    dependencies: MarketListDependencies,
    now_iso: str,
    limit: int,
) -> List[Dict[str, Any]]:
    if (
        dependencies.table_exists is not None
        and not dependencies.table_exists("event_market_serving")
    ):
        return []
    try:
        rows = dependencies.query_all(
            f"""
            WITH ranked_events AS (
                SELECT
                    ems.default_market_id AS market_id,
                    ems.active_rank,
                    ems.volume_24h AS event_volume_24h,
                    ems.trade_count_24h AS event_trade_count_24h,
                    ems.last_activity_at AS event_last_activity_at,
                    row_number() OVER (
                        PARTITION BY COALESCE(NULLIF(LOWER(ems.category), ''), 'market')
                        ORDER BY ems.active_rank DESC, ems.volume_24h DESC, ems.last_activity_at DESC NULLS LAST
                    ) AS category_rank
                FROM event_market_serving ems
                WHERE ems.default_market_id IS NOT NULL
                  AND ems.outcome_count > 0
                  AND ems.is_trading_closed = FALSE
                  AND ems.completion_status NOT IN ('SETTLED', 'CANCELLED', 'CLOSED_UNRESOLVED')
                  AND (ems.end_date IS NULL OR ems.end_date >= ?)
                  AND LOWER(COALESCE(ems.category, '')) NOT LIKE '%%orderfilled-placeholder%%'
                  AND LOWER(COALESCE(ems.event_slug, '')) NOT LIKE '%%trade-indexer-placeholder%%'
                  AND LOWER(COALESCE(ems.title, '')) NOT LIKE 'trade indexer placeholder market%%'
                  AND LOWER(COALESCE(CAST(ems.tags AS TEXT), '')) NOT LIKE '%%orderfilled-placeholder%%'
            )
            SELECT
                m.id,
                m.slug,
                m.condition_id,
                m.end_date,
                m.created_at,
                CASE WHEN COALESCE(mss.has_settle, FALSE) THEN 1 ELSE 0 END AS has_settle,
                CASE WHEN COALESCE(mss.has_propose, FALSE) THEN 1 ELSE 0 END AS has_propose,
                COALESCE(mss.settlement_code, 0) AS settlement_code,
                COALESCE(mss.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
                mss.settlement_source,
                mss.settlement_event_id,
                mss.settlement_event_time,
                mss.settlement_transaction,
                COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
                COALESCE(mss.is_resolved, FALSE) AS is_resolved,
                COALESCE(mss.is_final, FALSE) AS is_final,
                COALESCE(mss.completion_status, 'OPEN') AS completion_status,
                mss.completion_source,
                mss.completion_time,
                COALESCE(mss.gamma_closed, FALSE) AS gamma_closed,
                mss.gamma_closed_time,
                COALESCE(mls.trade_count_24h, re.event_trade_count_24h) AS trade_count_24h,
                COALESCE(mls.volume_24h, re.event_volume_24h) AS volume_24h,
                mls.latest_price,
                COALESCE(mls.last_trade_at, re.event_last_activity_at) AS last_trade_at,
                mls.latest_trade_at,
                mls.price_24h_ago,
                re.active_rank,
                re.category_rank
            FROM ranked_events re
            JOIN markets m ON m.id = re.market_id
            LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
            LEFT JOIN market_list_serving mls ON mls.market_id = m.id
            WHERE COALESCE(mss.has_settle, FALSE) = FALSE
              AND COALESCE(mss.has_propose, FALSE) = FALSE
              AND COALESCE(mss.is_trading_closed, FALSE) = FALSE
              AND COALESCE(mss.settlement_code, 0) = 0
              AND (m.end_date IS NULL OR m.end_date >= ?)
              AND {DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL}
              AND {_default_active_market_price_sql("mls")}
            ORDER BY
                CASE
                    WHEN LOWER(COALESCE(m.category, '')) IN ('sports', 'esports') AND re.category_rank > 4 THEN 1
                    ELSE 0
                END ASC,
                re.active_rank DESC,
                COALESCE(re.event_volume_24h, mls.volume_24h, 0) DESC,
                COALESCE(re.event_last_activity_at, mls.last_trade_at, mls.latest_trade_at, m.created_at) DESC NULLS LAST
            LIMIT ?
            """,
            (now_iso, now_iso, int(limit)),
        )
    except Exception:
        logger = getattr(dependencies.application, "logger", None)
        if logger:
            logger.exception("event-serving active market candidate query failed")
        return []
    return rows


def _market_list_serving_has_rows(
    dependencies: MarketListDependencies,
    min_rows: int = 1,
) -> bool:
    if dependencies.table_exists is None:
        return True
    if not dependencies.table_exists("market_list_serving"):
        return False
    min_rows = max(1, int(min_rows))
    row = dependencies.query_one(
        "SELECT COUNT(*) AS c FROM market_list_serving WHERE volume_24h > 0 OR latest_price IS NOT NULL"
    )
    return bool(row and int(row.get("c") or 0) >= min_rows)


def _fallback_active_market_candidate_rows(
    dependencies: MarketListDependencies,
    now_iso: str,
    limit: int,
) -> List[Dict[str, Any]]:
    created_cutoff = _iso_hours_before(now_iso, DEFAULT_ACTIVE_MARKET_MAX_AGE_HOURS)
    if _is_postgres_dependencies(dependencies):
        prelimit = max(int(limit) * 30, 5000)
        return dependencies.query_all(
            f"""
            WITH recent_markets AS MATERIALIZED (
                SELECT
                    m.id,
                    m.slug,
                    m.condition_id,
                    m.end_date,
                    m.created_at
                FROM markets m
                WHERE (m.end_date IS NULL OR m.end_date >= ?)
                  AND (m.created_at IS NULL OR m.created_at >= ?)
                  AND {DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL}
                ORDER BY m.created_at DESC NULLS LAST, m.id DESC
                LIMIT ?
            )
            SELECT
                m.id,
                m.slug,
                m.condition_id,
                m.end_date,
                m.created_at,
                0 AS has_settle,
                0 AS has_propose,
                COALESCE(mss.settlement_code, 0) AS settlement_code,
                COALESCE(mss.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
                mss.settlement_source,
                mss.settlement_event_id,
                mss.settlement_event_time,
                mss.settlement_transaction,
                COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
                COALESCE(mss.is_resolved, FALSE) AS is_resolved,
                COALESCE(mss.is_final, FALSE) AS is_final,
                COALESCE(mss.completion_status, 'OPEN') AS completion_status,
                mss.completion_source,
                mss.completion_time,
                COALESCE(mss.gamma_closed, FALSE) AS gamma_closed,
                mss.gamma_closed_time,
                0 AS trade_count_24h,
                0 AS volume_24h,
                NULL AS latest_price,
                NULL AS last_trade_at,
                NULL AS latest_trade_at,
                NULL AS price_24h_ago
            FROM recent_markets m
            JOIN market_status_snapshot mss ON mss.market_id = m.id
            WHERE mss.has_settle = FALSE
              AND mss.has_propose = FALSE
              AND mss.is_trading_closed = FALSE
              AND mss.settlement_code = 0
            ORDER BY m.created_at DESC NULLS LAST, m.id DESC
            LIMIT ?
            """,
            (now_iso, created_cutoff, prelimit, limit),
        )
    return dependencies.query_all(
        f"""
            SELECT
                m.id,
                m.slug,
                m.condition_id,
                m.end_date,
                m.created_at,
                CASE WHEN COALESCE(mss.has_settle, FALSE) THEN 1 ELSE 0 END AS has_settle,
                CASE WHEN COALESCE(mss.has_propose, FALSE) THEN 1 ELSE 0 END AS has_propose,
                COALESCE(mss.settlement_code, 0) AS settlement_code,
                COALESCE(mss.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
                mss.settlement_source,
                mss.settlement_event_id,
                mss.settlement_event_time,
                mss.settlement_transaction,
                COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
                COALESCE(mss.is_resolved, FALSE) AS is_resolved,
                COALESCE(mss.is_final, FALSE) AS is_final,
                COALESCE(mss.completion_status, 'OPEN') AS completion_status,
                mss.completion_source,
                mss.completion_time,
                COALESCE(mss.gamma_closed, FALSE) AS gamma_closed,
                mss.gamma_closed_time,
                0 AS trade_count_24h,
                0 AS volume_24h,
                NULL AS latest_price,
                NULL AS last_trade_at,
                NULL AS latest_trade_at,
                NULL AS price_24h_ago
            FROM markets m
            LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
            WHERE COALESCE(mss.has_settle, FALSE) = FALSE
              AND COALESCE(mss.has_propose, FALSE) = FALSE
              AND COALESCE(mss.is_trading_closed, FALSE) = FALSE
              AND COALESCE(mss.settlement_code, 0) = 0
              AND (m.end_date IS NULL OR m.end_date >= ?)
              AND (m.created_at IS NULL OR m.created_at >= ?)
              AND {DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL}
            ORDER BY m.created_at DESC NULLS LAST, m.id DESC
            LIMIT ?
        """,
        (now_iso, created_cutoff, limit),
    )


def _get_market_detail_rows_by_ids(
    ctx: Mapping[str, Any],
    market_ids: List[int],
) -> Dict[int, Dict[str, Any]]:
    return _read_market_detail_rows_by_ids(
        MarketListDependencies.from_context(ctx),
        market_ids,
    )


def _read_market_detail_rows_by_ids(
    dependencies: MarketListDependencies,
    market_ids: List[int],
) -> Dict[int, Dict[str, Any]]:
    if not market_ids:
        return {}
    placeholders = ", ".join("?" for _ in market_ids)
    rows = dependencies.query_all(
        f"""
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
            m.clob_token_ids,
            m.end_date,
            m.created_at,
            COALESCE(mss.settlement_code, 0) AS settlement_code,
            COALESCE(mss.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
            mss.settlement_source,
            mss.settlement_event_id,
            mss.settlement_event_time,
            mss.settlement_transaction,
            mss.settlement_raw,
            COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
            COALESCE(mss.is_resolved, FALSE) AS is_resolved,
            COALESCE(mss.is_final, FALSE) AS is_final,
            COALESCE(mss.completion_status, 'OPEN') AS completion_status,
            mss.completion_source,
            mss.completion_time,
            COALESCE(mss.gamma_closed, FALSE) AS gamma_closed,
            mss.gamma_closed_time,
            COALESCE(mlp.latest_yes_price, mls.latest_price) AS latest_price,
            COALESCE(mls.latest_trade_at, mlp.latest_trade_at) AS latest_trade_at,
            mls.price_24h_ago
        FROM markets m
        LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
        LEFT JOIN market_list_serving mls ON mls.market_id = m.id
        LEFT JOIN market_latest_prices mlp ON mlp.market_id = m.id
        WHERE m.id IN ({placeholders})
        """,
        market_ids,
    )
    return {
        int(row["id"]): row
        for row in rows
        if row.get("id") is not None
    }


def get_markets_payload(
    ctx: Mapping[str, Any],
    *,
    status: str = "active",
    query: str = "",
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    return _get_markets_payload(
        MarketListDependencies.from_context(ctx),
        status=status,
        query=query,
        page=page,
        page_size=page_size,
    )


def _get_markets_payload(
    dependencies: MarketListDependencies,
    *,
    status: str = "active",
    query: str = "",
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    now_iso = dependencies.utc_now_iso()
    status = str(status or "active").strip().lower()
    query = str(query or "").strip()
    page = max(1, int(page))
    page_size = min(500, max(1, int(page_size)))
    offset = (page - 1) * page_size

    filters: List[str] = []
    params: List[Any] = []
    recent_trade_cutoff = _iso_hours_before(now_iso, 24 * 7)
    created_cutoff = _iso_hours_before(now_iso, DEFAULT_ACTIVE_MARKET_MAX_AGE_HOURS)
    serving_has_rows = _market_list_serving_has_rows(
        dependencies,
        min_rows=max(page_size * 10, 1000),
    )
    if status == "active":
        filters.append("(COALESCE(mss.is_trading_closed, FALSE) = FALSE AND COALESCE(mss.has_settle, FALSE) = FALSE AND COALESCE(mss.has_propose, FALSE) = FALSE AND COALESCE(mss.settlement_code, 0) = 0 AND (m.end_date IS NULL OR m.end_date >= ?))")
        params.append(now_iso)
        if not query:
            filters.append(f"({_default_active_market_created_recent_sql()})")
            params.append(created_cutoff)
        if not query and serving_has_rows:
            filters.append(f"({DEFAULT_ACTIVE_MARKET_EXCLUSION_SQL})")
            filters.append(_default_active_market_activity_sql("mls"))
            filters.append(_default_active_market_price_sql("mls"))
            filters.append(_default_active_market_recent_trade_sql("mls"))
            params.append(recent_trade_cutoff)
    elif status == "closed":
        filters.append("(COALESCE(mss.is_trading_closed, FALSE) = TRUE OR COALESCE(mss.has_settle, FALSE) = TRUE OR COALESCE(mss.settlement_code, 0) IN (1, 2, 3) OR (COALESCE(mss.has_settle, FALSE) = FALSE AND COALESCE(mss.has_propose, FALSE) = FALSE AND COALESCE(mss.settlement_code, 0) = 0 AND m.end_date IS NOT NULL AND m.end_date < ?))")
        params.append(now_iso)
    if query:
        pattern = f"%{query}%"
        filters.append("(m.title LIKE ? OR m.slug LIKE ? OR m.condition_id LIKE ? OR m.question_id LIKE ?)")
        params.extend([pattern, pattern, pattern, pattern])

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    cache_key = json.dumps({"status": status, "query": query, "page": page, "pageSize": page_size, "v": 8}, sort_keys=True, ensure_ascii=True)

    if status == "active" and not query and page == 1:
        return get_active_markets_snapshot(
            dependencies.source,
            page_size=page_size,
            include_runtime_prices=markets_runtime_prices_enabled(),
        )

    def build_payload() -> Dict[str, Any]:
        recent_14d_iso = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat().replace("+00:00", "Z")
        recent_30d_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        raw_limit = min(5000, max((offset + page_size + 1) * 6, 180))
        clickhouse_candidate_rows = (
            _clickhouse_active_market_candidate_rows(
                dependencies,
                now_iso,
                raw_limit,
            )
            if status == "active" and not query and active_market_clickhouse_primary_enabled()
            else []
        )
        if clickhouse_candidate_rows:
            candidate_rows = clickhouse_candidate_rows
        elif status == "active" and not query and not serving_has_rows:
            candidate_rows = _fallback_active_market_candidate_rows(
                dependencies,
                now_iso,
                raw_limit,
            )
        else:
            candidate_rows = dependencies.query_all(
                f"""
                SELECT
                    m.id,
                    m.slug,
                    m.condition_id,
                    m.end_date,
                    m.created_at,
                    CASE WHEN COALESCE(mss.has_settle, FALSE) THEN 1 ELSE 0 END AS has_settle,
                    CASE WHEN COALESCE(mss.has_propose, FALSE) THEN 1 ELSE 0 END AS has_propose,
                    COALESCE(mss.settlement_code, 0) AS settlement_code,
                    COALESCE(mss.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
                    mss.settlement_source,
                    mss.settlement_event_id,
                    mss.settlement_event_time,
                    mss.settlement_transaction,
                    COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
                    COALESCE(mss.is_resolved, FALSE) AS is_resolved,
                    COALESCE(mss.is_final, FALSE) AS is_final,
                    COALESCE(mss.completion_status, 'OPEN') AS completion_status,
                    mss.completion_source,
                    mss.completion_time,
                    COALESCE(mss.gamma_closed, FALSE) AS gamma_closed,
                    mss.gamma_closed_time,
                    mls.trade_count_24h,
                    mls.volume_24h,
                    mls.latest_price,
                    mls.last_trade_at,
                    mls.latest_trade_at,
                    mls.price_24h_ago
                FROM markets m
                LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
                LEFT JOIN market_list_serving mls ON mls.market_id = m.id
                {where_clause}
                ORDER BY
                    CASE
                        WHEN m.created_at >= ? THEN 0
                        WHEN m.created_at >= ? THEN 1
                        ELSE 2
                    END ASC,
                    m.created_at DESC,
                    COALESCE(mls.trade_count_24h, 0) DESC,
                    COALESCE(mls.volume_24h, 0) DESC,
                    mls.last_trade_at DESC
                LIMIT ?
                """,
                [*params, recent_14d_iso, recent_30d_iso, raw_limit],
            )
        if status == "active" and serving_has_rows and not clickhouse_candidate_rows:
            candidate_rows = _prefer_gamma_active_candidate_rows(
                dependencies,
                candidate_rows,
                offset + page_size + 1,
            )
        working_candidates = candidate_rows[offset: offset + max(page_size * 3, page_size + 1)]
        if not working_candidates and candidate_rows:
            working_candidates = candidate_rows[: max(page_size * 3, page_size + 1)]
        visible_market_ids = [int(row["id"]) for row in working_candidates if row.get("id") is not None]
        detail_rows = _get_market_detail_rows_by_ids(
            dependencies.source,
            visible_market_ids,
        )
        visible_rows: List[Dict[str, Any]] = []
        for candidate in working_candidates:
            market_id = candidate.get("id")
            if market_id is None:
                continue
            detail_row = detail_rows.get(int(market_id))
            if not detail_row:
                continue
            normalized = dict(detail_row)
            normalized.update(
                {
                    "trade_count_24h": candidate.get("trade_count_24h"),
                    "volume_24h": candidate.get("volume_24h"),
                    "last_trade_at": candidate.get("last_trade_at") or candidate.get("latest_trade_at"),
                    "price_24h_ago": candidate.get("price_24h_ago"),
                    "has_settle": candidate.get("has_settle"),
                    "has_propose": candidate.get("has_propose"),
                    "settlement_code": candidate.get("settlement_code"),
                    "settlement_outcome": candidate.get("settlement_outcome"),
                    "settlement_source": candidate.get("settlement_source"),
                    "settlement_event_id": candidate.get("settlement_event_id"),
                    "settlement_event_time": candidate.get("settlement_event_time"),
                    "settlement_transaction": candidate.get("settlement_transaction"),
                    "is_trading_closed": candidate.get("is_trading_closed"),
                    "is_resolved": candidate.get("is_resolved"),
                    "is_final": candidate.get("is_final"),
                    "completion_status": candidate.get("completion_status"),
                    "completion_source": candidate.get("completion_source"),
                    "completion_time": candidate.get("completion_time"),
                    "gamma_closed": candidate.get("gamma_closed"),
                    "gamma_closed_time": candidate.get("gamma_closed_time"),
                }
            )
            normalized["status"] = _market_status_from_snapshot(normalized, now_iso)
            visible_rows.append(normalized)
        max_runtime_updates = min(page_size, 40 if page_size >= 80 else (24 if page_size >= 40 else 16))
        visible_rows = enrich_market_rows_with_runtime_prices(
            dependencies.source,
            visible_rows,
            max_updates=max_runtime_updates,
        )
        if status == "active":
            visible_rows = _merge_clickhouse_stats(dependencies, visible_rows)
        if status == "active":
            visible_rows = _prefer_tradeable_market_rows(visible_rows, page_size + 1)
            if not query:
                visible_rows = _rank_default_market_rows(visible_rows, now_iso)
                visible_rows = _interleave_market_category_rows(visible_rows, page_size + 1)
                visible_rows = _coalesce_native_market_rows(visible_rows)
                visible_rows = _diversify_market_rows(visible_rows, page_size + 1, now_iso)
        has_more = len(visible_rows) > page_size
        visible_rows = visible_rows[:page_size]
        return {
            "items": [
                _market_list_item(dependencies, row)
                for row in visible_rows
            ],
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "total": offset + len(visible_rows) + (1 if has_more else 0),
                "totalPages": page + (1 if has_more else 0),
                "hasMore": has_more,
            },
        }

    return dependencies.get_markets_payload_cached(cache_key, build_payload)


def build_active_markets_payload(
    ctx: Mapping[str, Any],
    page_size: int = 40,
    *,
    include_runtime_prices: bool = False,
    include_change_24h: bool = False,
) -> Dict[str, Any]:
    return _build_active_markets_payload(
        MarketListDependencies.from_context(ctx),
        page_size=page_size,
        include_runtime_prices=include_runtime_prices,
        include_change_24h=include_change_24h,
    )


def _build_active_markets_payload(
    dependencies: MarketListDependencies,
    page_size: int = 40,
    *,
    include_runtime_prices: bool = False,
    include_change_24h: bool = False,
) -> Dict[str, Any]:
    now_iso = dependencies.utc_now_iso()
    created_cutoff = _iso_hours_before(now_iso, DEFAULT_ACTIVE_MARKET_MAX_AGE_HOURS)
    raw_limit = min(2500, max(page_size * 20, 300))
    clickhouse_candidate_rows = (
        _clickhouse_active_market_candidate_rows(
            dependencies,
            now_iso,
            raw_limit,
        )
        if active_market_clickhouse_primary_enabled()
        else []
    )
    event_candidate_rows = _event_serving_active_market_candidate_rows(
        dependencies,
        now_iso,
        raw_limit,
    )
    if clickhouse_candidate_rows:
        candidate_rows = clickhouse_candidate_rows
    elif event_candidate_rows:
        candidate_rows = event_candidate_rows
    elif _market_list_serving_has_rows(
        dependencies,
        min_rows=max(page_size * 10, 1000),
    ):
        volume_candidate_rows = dependencies.query_all(
            f"""
            {_active_market_candidate_select_sql("stats_24h")}
            ORDER BY COALESCE(stats_24h.volume_24h, 0) DESC, COALESCE(stats_24h.trade_count_24h, 0) DESC, stats_24h.last_trade_at DESC, m.created_at DESC
            LIMIT ?
            """,
            (now_iso, created_cutoff, _iso_hours_before(now_iso, 24 * 7), raw_limit),
        )
        recent_candidate_rows = dependencies.query_all(
            f"""
            {_active_market_candidate_select_sql("stats_24h")}
            ORDER BY m.created_at DESC, COALESCE(stats_24h.volume_24h, 0) DESC, COALESCE(stats_24h.trade_count_24h, 0) DESC
            LIMIT ?
            """,
            (now_iso, created_cutoff, _iso_hours_before(now_iso, 24 * 7), min(raw_limit, max(page_size * 2, 80))),
        )
        candidate_rows = _blend_recent_candidate_rows(volume_candidate_rows, recent_candidate_rows, page_size)
    else:
        candidate_rows = _fallback_active_market_candidate_rows(
            dependencies,
            now_iso,
            raw_limit,
        )
    candidate_stats_map = {
        int(row["id"]): {
            "trade_count_24h": row.get("trade_count_24h"),
            "volume_24h": row.get("volume_24h"),
            "latest_price": row.get("latest_price"),
            "last_trade_at": row.get("last_trade_at") or row.get("latest_trade_at"),
            "price_24h_ago": row.get("price_24h_ago"),
            "has_settle": row.get("has_settle"),
            "has_propose": row.get("has_propose"),
            "settlement_code": row.get("settlement_code"),
            "settlement_outcome": row.get("settlement_outcome"),
            "settlement_source": row.get("settlement_source"),
            "settlement_event_id": row.get("settlement_event_id"),
            "settlement_event_time": row.get("settlement_event_time"),
            "settlement_transaction": row.get("settlement_transaction"),
            "is_trading_closed": row.get("is_trading_closed"),
            "is_resolved": row.get("is_resolved"),
            "is_final": row.get("is_final"),
            "completion_status": row.get("completion_status"),
            "completion_source": row.get("completion_source"),
            "completion_time": row.get("completion_time"),
            "gamma_closed": row.get("gamma_closed"),
            "gamma_closed_time": row.get("gamma_closed_time"),
            "active_rank": row.get("active_rank"),
            "category_rank": row.get("category_rank"),
        }
        for row in candidate_rows
        if row.get("id") is not None
    }
    ordered_market_ids: List[int] = []
    for row in candidate_rows:
        market_id = row.get("id")
        if market_id is None:
            continue
        ordered_market_ids.append(int(market_id))
        if len(ordered_market_ids) >= max(page_size * 3, page_size):
            break
    detail_rows = _get_market_detail_rows_by_ids(
        dependencies.source,
        ordered_market_ids,
    )
    rows: List[Dict[str, Any]] = []
    for market_id in ordered_market_ids:
        detail_row = detail_rows.get(market_id)
        if not detail_row:
            continue
        normalized = dict(detail_row)
        normalized.update(candidate_stats_map.get(market_id, {}))
        normalized["status"] = _market_status_from_snapshot(normalized, now_iso)
        rows.append(normalized)
    if include_runtime_prices:
        rows = enrich_market_rows_with_runtime_prices(
            dependencies.source,
            rows,
            max_updates=min(page_size, 24),
            force_refresh=False,
        )
    if include_change_24h:
        rows = enrich_market_rows_with_24h_change(dependencies.source, rows)
    rows = _merge_clickhouse_stats(dependencies, rows)
    rows = _prefer_tradeable_market_rows(rows, max(page_size * 3, page_size))
    rows = _rank_default_market_rows(rows, now_iso)
    rows = _interleave_market_category_rows(rows, max(page_size * 3, page_size))
    rows = _coalesce_native_market_rows(rows)
    rows = _prefer_lob_ready_market_rows(dependencies, rows, page_size)
    rows = _diversify_market_rows(rows, page_size, now_iso)
    rows = rows[:page_size]
    return {
        "items": [_market_list_item(dependencies, row) for row in rows],
        "pagination": {"page": 1, "pageSize": page_size, "total": len(rows), "totalPages": 1, "hasMore": False},
    }


def _active_markets_payload_has_price_history_schema(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return False
    return any(
        isinstance(item, dict)
        and (
            item.get("price24hAgo") not in (None, "")
            or item.get("change24h") not in (None, "")
        )
        for item in items[:20]
    )


def _active_markets_payload_has_token_schema(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return False
    return all(
        isinstance(item, dict)
        and "yesTokenId" in item
        and "noTokenId" in item
        for item in items[:20]
    )


def get_active_markets_snapshot(
    ctx: Mapping[str, Any],
    page_size: int = 40,
    *,
    include_runtime_prices: bool = False,
    include_change_24h: bool = False,
) -> Dict[str, Any]:
    return _get_active_markets_snapshot(
        MarketListDependencies.from_context(ctx),
        page_size=page_size,
        include_runtime_prices=include_runtime_prices,
        include_change_24h=include_change_24h,
    )


def _get_active_markets_snapshot(
    dependencies: MarketListDependencies,
    page_size: int = 40,
    *,
    include_runtime_prices: bool = False,
    include_change_24h: bool = False,
) -> Dict[str, Any]:
    should_include_change_24h = bool(include_change_24h or include_runtime_prices)
    cache_key = json.dumps(
        {
            "page": 1,
            "pageSize": page_size,
            "status": "active",
            "includeRuntimePrices": include_runtime_prices,
            "includeChange24h": should_include_change_24h,
            "maxAgeHours": DEFAULT_ACTIVE_MARKET_MAX_AGE_HOURS,
            "v": 25,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    exact_payload = dependencies.snapshot_store.get(
        ACTIVE_MARKETS_SNAPSHOT_NAMESPACE,
        cache_key,
    )
    exact_payload_was_empty = False
    if exact_payload is not None:
        exact_items = exact_payload.get("items") if isinstance(exact_payload, dict) else None
        if (
            isinstance(exact_items, list)
            and exact_items
            and _active_markets_payload_has_token_schema(exact_payload)
        ):
            dependencies.set_cached_json(
                ACTIVE_MARKETS_SNAPSHOT_NAMESPACE,
                cache_key,
                exact_payload,
                60,
            )
            return exact_payload
        exact_payload_was_empty = True
        dependencies.application.logger.warning(
            "markets-active exact snapshot ignored because it is empty or incompatible page_size=%s include_runtime_prices=%s",
            page_size,
            include_runtime_prices,
        )

    if markets_latest_snapshot_fallback_enabled() and not include_runtime_prices:
        latest_payload = dependencies.snapshot_store.get_latest_stale(
            ACTIVE_MARKETS_SNAPSHOT_NAMESPACE,
            exclude_cache_key=cache_key,
        )
        fallback_payload = _trim_active_markets_payload(
            latest_payload,
            page_size,
        )
        if (
            fallback_payload is not None
            and _active_markets_payload_has_token_schema(fallback_payload)
            and (
                not should_include_change_24h or _active_markets_payload_has_price_history_schema(fallback_payload)
            )
        ):
            dependencies.application.logger.info(
                "markets-active latest-snapshot-fallback page_size=%s include_runtime_prices=%s",
                page_size,
                include_runtime_prices,
            )
            dependencies.snapshot_store.set(
                ACTIVE_MARKETS_SNAPSHOT_NAMESPACE,
                cache_key,
                fallback_payload,
                60,
            )
            dependencies.set_cached_json(
                ACTIVE_MARKETS_SNAPSHOT_NAMESPACE,
                cache_key,
                fallback_payload,
                60,
            )
            return fallback_payload
        if fallback_payload is not None:
            dependencies.application.logger.info(
                "markets-active latest-snapshot-fallback skipped because cached schema lacks price24hAgo page_size=%s",
                page_size,
            )

    if exact_payload_was_empty:
        rebuilt_payload = _build_active_markets_payload(
            dependencies,
            page_size=page_size,
            include_runtime_prices=include_runtime_prices,
            include_change_24h=should_include_change_24h,
        )
        rebuilt_items = rebuilt_payload.get("items") if isinstance(rebuilt_payload, dict) else None
        if isinstance(rebuilt_items, list) and rebuilt_items:
            dependencies.snapshot_store.set(
                ACTIVE_MARKETS_SNAPSHOT_NAMESPACE,
                cache_key,
                rebuilt_payload,
                60,
            )
            dependencies.set_cached_json(
                ACTIVE_MARKETS_SNAPSHOT_NAMESPACE,
                cache_key,
                rebuilt_payload,
                60,
            )
        return rebuilt_payload

    return dependencies.get_snapshot_payload(
        ACTIVE_MARKETS_SNAPSHOT_NAMESPACE,
        cache_key,
        lambda: _build_active_markets_payload(
            dependencies,
            page_size=page_size,
            include_runtime_prices=include_runtime_prices,
            include_change_24h=should_include_change_24h,
        ),
        ttl_seconds=60,
    )


def get_market_detail_payload(
    ctx: Mapping[str, Any],
    market_id: int,
) -> Dict[str, Any]:
    return _get_market_detail_payload(
        MarketWorkspaceDependencies.from_context(ctx),
        market_id,
    )


def _get_market_detail_payload(
    dependencies: MarketWorkspaceDependencies,
    market_id: int,
) -> Dict[str, Any]:
    serving_payload = _read_market_workspace_detail_payload(
        dependencies.serving,
        market_id,
    )
    if serving_payload is not None:
        # The pre-generated serving row intentionally contains only a compact
        # Oracle summary. Hydrate the selected market from the canonical
        # oracle_events table so a stale serving row cannot hide newly indexed
        # request/propose/dispute/settle events from the Oracle panel.
        market = _get_market_by_id(dependencies.lookup, market_id)
        if market:
            oracle_payload = _build_market_oracle_payload(
                dependencies.oracle,
                market_id,
                market,
            )
            if not oracle_payload.get("error"):
                timeline = oracle_payload.get("timeline") or []
                serving_payload = dict(serving_payload)
                serving_payload["oracle"] = oracle_payload
                serving_payload["oracleEvents"] = timeline
                diagnostics = dict(serving_payload.get("diagnostics") or {})
                diagnostics["oracleStatus"] = (
                    oracle_payload.get("completionStatus") or "OPEN"
                )
                diagnostics["oracleEventCount"] = len(timeline) if isinstance(timeline, list) else 0
                serving_payload["diagnostics"] = diagnostics
        return serving_payload
    market = _get_market_by_id(dependencies.lookup, market_id)
    if not market:
        return {"error": "Market not found", "marketId": market_id, "_status": 404}
    cache_key = json.dumps({"marketId": int(market_id), "v": 11}, sort_keys=True, ensure_ascii=True)

    def build_payload() -> Dict[str, Any]:
        price = _get_market_price_summary(
            dependencies.price,
            market_id,
            market=market,
            include_runtime_price=False,
            include_recent_stats=False,
        )
        latest = price.get("latestYesPrice") or price.get("latestPrice")
        snapshot_time = price.get("updatedAt") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        chart_points = (
            [
                {"timestamp": snapshot_time, "yesPrice": latest, "noPrice": price.get("latestNoPrice")},
                {"timestamp": snapshot_time, "yesPrice": latest, "noPrice": price.get("latestNoPrice")},
            ]
            if latest not in (None, "")
            else []
        )
        chart = {
            "marketId": market_id,
            "localMarketId": market_id,
            "range": "snapshot" if chart_points else "missing",
            "interval": "snapshot" if chart_points else "missing",
            "kind": "probability",
            "historyStatus": "snapshot" if chart_points else "missing",
            "points": chart_points,
        }
        oracle_payload = _get_market_oracle_payload(
            dependencies.oracle,
            market_id,
            market=market,
        )
        oracle_events = oracle_payload.get("timeline", [])
        trades = _get_trades_by_market_id(
            dependencies.trades,
            market_id,
            limit=24,
            offset=0,
        )
        normalized_market = dependencies.normalize_market(market)
        identity = _workspace_identity(market_id, market)
        diagnostics = _workspace_diagnostics(
            market_id,
            market,
            price,
            chart,
            oracle_payload,
            trades,
        )
        return {
            "market": normalized_market,
            "localMarketId": market_id,
            "gammaMarketId": market.get("gamma_market_id"),
            "identity": identity,
            "diagnostics": diagnostics,
            "price": price,
            "chart": chart,
            "priceSeries": chart.get("points", []),
            "trades": trades,
            "oracle": oracle_payload,
            "oracleEvents": oracle_events,
            "content": None,
        }

    return dependencies.get_snapshot_payload(
        "snapshot:market_detail_bundle",
        cache_key,
        build_payload,
        ttl_seconds=90,
    )


def get_market_workspace_payload(
    ctx: Mapping[str, Any],
    market_id: int,
) -> Dict[str, Any]:
    return _get_market_workspace_payload(
        MarketWorkspaceDependencies.from_context(ctx),
        market_id,
    )


def _get_market_workspace_payload(
    dependencies: MarketWorkspaceDependencies,
    market_id: int,
) -> Dict[str, Any]:
    market = _get_market_by_id(dependencies.lookup, market_id)
    if not market:
        return {"error": "Market not found", "marketId": market_id, "_status": 404}

    detail_payload = _get_market_detail_payload(dependencies, market_id)
    if detail_payload.get("_status") == 404:
        return detail_payload

    identity = dict(detail_payload.get("identity") or _workspace_identity(market_id, market))
    identity.setdefault("eventId", market.get("event_id"))
    identity.setdefault("eventSlug", market.get("event_slug"))

    group = None
    event_id = str(market.get("event_id") or identity.get("eventId") or "").strip()
    if event_id:
        try:
            group = market_group_service.get_market_group_detail_payload(
                dependencies.source,
                event_id,
            )
        except Exception:
            dependencies.application.logger.exception(
                "market workspace group load failed market_id=%s event_id=%s",
                market_id,
                event_id,
            )
            group = None
    selected_outcome = _workspace_selected_outcome(group, market_id, market)
    if selected_outcome and selected_outcome.get("outcomeKey"):
        identity["selectedOutcomeKey"] = selected_outcome.get("outcomeKey")

    price = detail_payload.get("price") or _get_market_price_summary(
        dependencies.price,
        market_id,
        market=market,
    )
    if not (price or {}).get("latestYesPrice") and not (price or {}).get("latestPrice"):
        price = _get_market_price_summary(
            dependencies.price,
            market_id,
            market=market,
        )

    detail_chart = detail_payload.get("chart") if isinstance(detail_payload.get("chart"), dict) else None
    detail_chart_points = detail_chart.get("points") if isinstance(detail_chart, dict) else []
    chart = detail_chart if isinstance(detail_chart_points, list) and _is_usable_market_chart_serving(detail_chart) else None
    if chart is None:
        chart = _get_market_chart_payload(
            dependencies.chart,
            market_id,
            range_name="1d",
            interval="5m",
            market=market,
            price=price,
            include_runtime_series=True,
        )
    price = _merge_chart_latest_price(price, chart)
    oracle_payload = detail_payload.get("oracle") or _get_market_oracle_payload(
        dependencies.oracle,
        market_id,
        market=market,
    )
    trades = detail_payload.get("trades") if isinstance(detail_payload.get("trades"), list) else []
    if not trades:
        trades = _get_trades_by_market_id(
            dependencies.trades,
            market_id,
            limit=24,
            offset=0,
        )
    diagnostics = dict(detail_payload.get("diagnostics") or {})
    if diagnostics:
        diagnostics["workspaceContract"] = "v1"
    health = _workspace_health(
        market_id=market_id,
        identity=identity,
        price=price,
        chart=chart,
        oracle_payload=oracle_payload,
        diagnostics=diagnostics,
        group=group,
        selected_outcome=selected_outcome,
        serving_source=detail_payload.get("servingSource"),
    )
    generated_at = dependencies.utc_now_iso()
    evidence = _workspace_evidence(
        market_id=market_id,
        identity=identity,
        price=price,
        chart=chart,
        trades=trades,
        oracle_payload=oracle_payload,
        group=group,
        health=health,
        serving_source=detail_payload.get("servingSource"),
        serving_updated_at=detail_payload.get("servingUpdatedAt"),
        generated_at=generated_at,
    )
    return {
        "market": detail_payload.get("market") or dependencies.normalize_market(market),
        "identity": identity,
        "diagnostics": diagnostics,
        "health": health,
        "evidence": evidence,
        "group": group,
        "selectedOutcome": selected_outcome,
        "price": price,
        "chart": chart,
        "trades": trades,
        "oracle": oracle_payload,
        "content": detail_payload.get("content"),
        "lob": None,
        "servingSource": detail_payload.get("servingSource") or "fallback",
        "servingUpdatedAt": detail_payload.get("servingUpdatedAt"),
        "generatedAt": generated_at,
    }
