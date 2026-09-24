from __future__ import annotations

import json
import os
import hashlib
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, cast

from api.context import resolve_service_callable, resolve_service_value
from api.services import clickhouse_orderfilled_service


def _service_callable(
    context: Mapping[str, Any],
    name: str,
) -> Callable[..., Any]:
    return cast(Callable[..., Any], resolve_service_callable(context, name))


@dataclass(frozen=True)
class DashboardStatusDependencies:
    query_all: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> DashboardStatusDependencies:
        return cls(
            query_all=_service_callable(context, "query_all"),
        )


@dataclass(frozen=True)
class RecentTradeWindowDependencies:
    query_one: Callable[..., Any]
    table_exists: Callable[..., Any]
    get_existing_trade_read_source: Callable[..., Any]
    identifier_name: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> RecentTradeWindowDependencies:
        return cls(
            query_one=_service_callable(context, "query_one"),
            table_exists=_service_callable(context, "table_exists"),
            get_existing_trade_read_source=_service_callable(
                context,
                "get_existing_trade_read_source",
            ),
            identifier_name=_service_callable(context, "_identifier_name"),
        )


@dataclass(frozen=True)
class DashboardTradeVolumeDependencies:
    query_all: Callable[..., Any]
    get_existing_trade_read_source: Callable[..., Any]
    utc_date_days_ago: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> DashboardTradeVolumeDependencies:
        return cls(
            query_all=_service_callable(context, "query_all"),
            get_existing_trade_read_source=_service_callable(
                context,
                "get_existing_trade_read_source",
            ),
            utc_date_days_ago=_service_callable(
                context,
                "utc_date_days_ago",
            ),
        )


@dataclass(frozen=True)
class DashboardRecentMarketsDependencies:
    query_all: Callable[..., Any]
    get_existing_trade_read_source: Callable[..., Any]
    utc_date_days_ago: Callable[..., Any]
    build_market_status_case: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> DashboardRecentMarketsDependencies:
        return cls(
            query_all=_service_callable(context, "query_all"),
            get_existing_trade_read_source=_service_callable(
                context,
                "get_existing_trade_read_source",
            ),
            utc_date_days_ago=_service_callable(
                context,
                "utc_date_days_ago",
            ),
            build_market_status_case=_service_callable(
                context,
                "build_market_status_case",
            ),
        )


@dataclass(frozen=True)
class TradeCountEstimateDependencies:
    query_one: Callable[..., Any]
    get_existing_trade_read_source: Callable[..., Any]
    identifier_name: Callable[..., Any]
    get_backend: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> TradeCountEstimateDependencies:
        return cls(
            query_one=_service_callable(context, "query_one"),
            get_existing_trade_read_source=_service_callable(
                context,
                "get_existing_trade_read_source",
            ),
            identifier_name=_service_callable(context, "_identifier_name"),
            get_backend=_service_callable(context, "get_backend"),
        )


@dataclass(frozen=True)
class RecentTradeDependencies:
    query_all: Callable[..., Any]
    get_existing_trade_read_source: Callable[..., Any]
    identifier_name: Callable[..., Any]
    get_trade_market_projection_sql: Callable[..., Any]
    normalize_trade: Callable[..., Any]
    trade_v2_core_table: str

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> RecentTradeDependencies:
        return cls(
            query_all=_service_callable(context, "query_all"),
            get_existing_trade_read_source=_service_callable(
                context,
                "get_existing_trade_read_source",
            ),
            identifier_name=_service_callable(context, "_identifier_name"),
            get_trade_market_projection_sql=_service_callable(
                context,
                "get_trade_market_projection_sql",
            ),
            normalize_trade=_service_callable(context, "normalize_trade"),
            trade_v2_core_table=cast(
                str,
                resolve_service_value(context, "TRADE_V2_CORE_TABLE"),
            ),
        )


@dataclass(frozen=True)
class RecentOracleDependencies:
    query_all: Callable[..., Any]
    normalize_oracle_event: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> RecentOracleDependencies:
        return cls(
            query_all=_service_callable(context, "query_all"),
            normalize_oracle_event=_service_callable(
                context,
                "normalize_oracle_event",
            ),
        )


@dataclass(frozen=True)
class ContentStorageDependencies:
    application: Any
    database_path: str
    get_backend: Callable[..., Any]
    table_exists: Callable[..., Any]
    get_connection: Callable[..., Any]
    query_all: Callable[..., Any]
    query_one: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> ContentStorageDependencies:
        return cls(
            application=resolve_service_value(context, "app"),
            database_path=cast(
                str,
                resolve_service_value(context, "DB_PATH"),
            ),
            get_backend=_service_callable(context, "get_backend"),
            table_exists=_service_callable(context, "table_exists"),
            get_connection=_service_callable(context, "get_connection"),
            query_all=_service_callable(context, "query_all"),
            query_one=_service_callable(context, "query_one"),
        )


@dataclass(frozen=True)
class ContentRefreshDependencies:
    storage: ContentStorageDependencies
    content_runtime_provider: Any

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> ContentRefreshDependencies:
        return cls(
            storage=ContentStorageDependencies.from_context(context),
            content_runtime_provider=resolve_service_value(
                context,
                "CONTENT_RUNTIME_PROVIDER",
            ),
        )


@dataclass(frozen=True)
class RelatedContentQueryDependencies:
    storage: ContentStorageDependencies
    content_runtime_provider: Any
    get_market_by_id: Callable[..., Any]
    parse_json_list: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> RelatedContentQueryDependencies:
        return cls(
            storage=ContentStorageDependencies.from_context(context),
            content_runtime_provider=resolve_service_value(
                context,
                "CONTENT_RUNTIME_PROVIDER",
            ),
            get_market_by_id=_service_callable(
                context,
                "get_market_by_id",
            ),
            parse_json_list=_service_callable(context, "parse_json_list"),
        )


@dataclass(frozen=True)
class LatestContentQueryDependencies:
    storage: ContentStorageDependencies
    content_runtime_provider: Any
    get_snapshot_payload: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> LatestContentQueryDependencies:
        return cls(
            storage=ContentStorageDependencies.from_context(context),
            content_runtime_provider=resolve_service_value(
                context,
                "CONTENT_RUNTIME_PROVIDER",
            ),
            get_snapshot_payload=_service_callable(
                context,
                "get_snapshot_payload",
            ),
        )


def fetch_dashboard_market_status(
    ctx: Mapping[str, Any],
    now_iso: str,
) -> List[Dict[str, Any]]:
    dependencies = DashboardStatusDependencies.from_context(ctx)
    return dependencies.query_all(
        """
        SELECT status AS name, COUNT(*) AS value
        FROM (
            SELECT
                CASE
                    WHEN COALESCE(mss.has_settle, FALSE) = TRUE OR COALESCE(mss.settlement_code, 0) IN (1, 2, 3) THEN 'Settled'
                    WHEN COALESCE(mss.has_propose, FALSE) = TRUE THEN 'Proposed'
                    WHEN m.end_date IS NOT NULL AND m.end_date < ? THEN 'Closed'
                    ELSE 'Active'
                END AS status
            FROM markets m
            LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
        ) status_rows
        GROUP BY status
        ORDER BY value DESC
        """,
        (now_iso,),
    )


def fetch_recent_trade_window_bounds(
    ctx: Mapping[str, Any],
    window_size: int,
) -> Dict[str, Any]:
    dependencies = RecentTradeWindowDependencies.from_context(ctx)
    if dependencies.table_exists("market_trade_daily_stats"):
        summary_days = 30 if window_size >= 50000 else 7
        summary_row = dependencies.query_one(
            """
            SELECT
                COALESCE(SUM(day_rows.trade_count), 0) AS trade_count,
                MIN(CONCAT(day_rows.trade_date, 'T00:00:00Z')) AS earliest_timestamp,
                MAX(day_rows.last_trade_at) AS latest_timestamp
            FROM (
                SELECT
                    trade_date,
                    SUM(trade_count) AS trade_count,
                    MAX(last_trade_at) AS last_trade_at
                FROM market_trade_daily_stats
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT ?
            ) day_rows
            """,
            (summary_days,),
        )
        if summary_row and (summary_row.get("latest_timestamp") is not None or int(summary_row.get("trade_count") or 0) > 0):
            summary_row["source"] = f"market_trade_daily_stats:{summary_days}d"
            return summary_row

    trade_source = dependencies.get_existing_trade_read_source()
    if trade_source is None:
        return {"trade_count": 0, "earliest_timestamp": None, "latest_timestamp": None, "source": "none"}
    payload = dependencies.query_one(
        f"""
        SELECT
            COUNT(*) AS trade_count,
            MIN(timestamp) AS earliest_timestamp,
            MAX(timestamp) AS latest_timestamp
        FROM (
            SELECT timestamp
            FROM {trade_source}
            ORDER BY timestamp DESC
            LIMIT ?
        ) recent_trades
        """,
        (window_size,),
    )
    payload["source"] = dependencies.identifier_name(trade_source)
    return payload


def fetch_dashboard_trade_volume(
    ctx: Mapping[str, Any],
    window_size: int,
) -> List[Dict[str, Any]]:
    dependencies = DashboardTradeVolumeDependencies.from_context(ctx)
    summary_threshold = dependencies.utc_date_days_ago(30)
    summary_rows = dependencies.query_all(
        """
        SELECT trade_date AS day, SUM(trade_count) AS trade_count
        FROM market_trade_daily_stats
        WHERE trade_date >= ?
        GROUP BY trade_date
        ORDER BY trade_date ASC
        """,
        (summary_threshold,),
    )
    if summary_rows:
        return summary_rows
    trade_source = dependencies.get_existing_trade_read_source()
    if trade_source is None:
        return []
    return dependencies.query_all(
        f"""
        SELECT day, COUNT(*) AS trade_count
        FROM (
            SELECT substr(timestamp, 1, 10) AS day
            FROM {trade_source}
            ORDER BY timestamp DESC
            LIMIT ?
        ) recent_trades
        GROUP BY day
        ORDER BY day ASC
        """,
        (window_size,),
    )


def fetch_dashboard_recent_markets(
    ctx: Mapping[str, Any],
    now_iso: str,
    window_size: int,
) -> List[Dict[str, Any]]:
    dependencies = DashboardRecentMarketsDependencies.from_context(ctx)
    status_case = dependencies.build_market_status_case(now_iso)
    summary_threshold = dependencies.utc_date_days_ago(30)
    summary_rows = dependencies.query_all(
        f"""
        WITH activity AS (
            SELECT
                market_id,
                SUM(trade_count) AS trade_count,
                MAX(last_trade_at) AS last_trade_at
            FROM market_trade_daily_stats
            WHERE trade_date >= ?
            GROUP BY market_id
            ORDER BY trade_count DESC, last_trade_at DESC
            LIMIT 5
        )
        SELECT
            m.id,
            m.gamma_market_id,
            m.slug,
            m.title,
            m.end_date,
            {status_case} AS status,
            activity.trade_count,
            activity.last_trade_at,
            mlp.latest_price AS latest_price
        FROM activity
        JOIN markets m ON m.id = activity.market_id
        LEFT JOIN market_latest_prices mlp ON mlp.market_id = activity.market_id
        ORDER BY activity.trade_count DESC, activity.last_trade_at DESC
        """,
        (summary_threshold, now_iso),
    )
    if summary_rows:
        return summary_rows

    trade_source = dependencies.get_existing_trade_read_source()
    if trade_source is None:
        return []
    return dependencies.query_all(
        f"""
        WITH recent_trades AS (
            SELECT market_id, timestamp, price, block_number, log_index
            FROM {trade_source}
            WHERE market_id IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
        ),
        activity AS (
            SELECT market_id, COUNT(*) AS trade_count, MAX(timestamp) AS last_trade_at
            FROM recent_trades
            GROUP BY market_id
            ORDER BY trade_count DESC, last_trade_at DESC
            LIMIT 5
        ),
        latest_price AS (
            SELECT market_id, price
            FROM (
                SELECT
                    market_id,
                    price,
                    ROW_NUMBER() OVER (
                        PARTITION BY market_id
                        ORDER BY timestamp DESC, block_number DESC, log_index DESC
                    ) AS row_num
                FROM recent_trades
            ) ranked_prices
            WHERE row_num = 1
        )
        SELECT
            m.id,
            m.gamma_market_id,
            m.slug,
            m.title,
            m.end_date,
            {status_case} AS status,
            activity.trade_count,
            activity.last_trade_at,
            latest_price.price AS latest_price
        FROM activity
        JOIN markets m ON m.id = activity.market_id
        LEFT JOIN latest_price ON latest_price.market_id = activity.market_id
        ORDER BY activity.trade_count DESC, activity.last_trade_at DESC
        """,
        (window_size, now_iso),
    )


def fetch_trade_count_estimate(
    ctx: Mapping[str, Any],
) -> Dict[str, Any]:
    dependencies = TradeCountEstimateDependencies.from_context(ctx)
    trade_source = dependencies.get_existing_trade_read_source()
    if trade_source is None:
        return {"table_rows": 0, "auto_increment": 0}
    if dependencies.get_backend() == "sqlite":
        return dependencies.query_one(
            f"""
            SELECT COUNT(*) AS table_rows, COALESCE(MAX(id), 0) AS auto_increment
            FROM {trade_source}
            """
        )
    return dependencies.query_one(
        f"""
        SELECT
            COALESCE(table_rows, 0) AS table_rows,
            COALESCE(auto_increment, 0) AS auto_increment
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = '{dependencies.identifier_name(trade_source)}'
        """
    )


def get_recent_trades(
    ctx: Mapping[str, Any],
    limit: int = 24,
) -> List[Dict[str, Any]]:
    clickhouse_rows = clickhouse_orderfilled_service.get_recent_trades(ctx, limit=limit)
    if clickhouse_rows is not None:
        return clickhouse_rows
    if clickhouse_orderfilled_service.clickhouse_orderfilled_enabled():
        fallback_enabled = str(os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_FALLBACK_ON_UNAVAILABLE", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not fallback_enabled:
            raise RuntimeError("ClickHouse OrderFilled read is enabled but unavailable")
    dependencies = RecentTradeDependencies.from_context(ctx)
    trade_source = dependencies.get_existing_trade_read_source()
    if trade_source is None:
        return []
    if dependencies.identifier_name(trade_source) == dependencies.trade_v2_core_table:
        rows = dependencies.query_all(
            f"""
            SELECT
                {dependencies.get_trade_market_projection_sql('t')},
                m.title AS market_title
            FROM {trade_source} t
            LEFT JOIN markets m ON m.id = t.market_id
            WHERE t.market_id IS NOT NULL
            ORDER BY t.block_number DESC, t.log_index DESC
            LIMIT ?
            """,
            (limit,),
        )
    else:
        rows = dependencies.query_all(
            f"""
            SELECT
                tx_hash, log_index, market_id, maker, taker, price, size, side, outcome,
                token_id, timestamp, block_number, order_hash, maker_asset_id, taker_asset_id,
                maker_amount, taker_amount, fee, contract,
                NULL AS market_title
            FROM {trade_source}
            WHERE market_id IS NOT NULL
            ORDER BY timestamp DESC, block_number DESC, log_index DESC
            LIMIT ?
            """,
            (limit,),
        )
    return [dependencies.normalize_trade(row) for row in rows]


def get_recent_oracle_events(
    ctx: Mapping[str, Any],
    limit: int = 24,
) -> List[Dict[str, Any]]:
    dependencies = RecentOracleDependencies.from_context(ctx)
    rows = dependencies.query_all(
        """
        SELECT
            oe.id, oe.tx_hash, oe.log_index, oe.block_number, oe.event_time, oe.event_status, oe.external_market_id,
            oe.market_id, COALESCE(m.title, oe.market_title) AS market_title, oe.matched_by,
            oe.question_id, oe.condition_id, oe.proposed_price, oe.settled_price, oe.payout,
            oe.requester, oe.proposer, oe.disputer, oe.proposal_transaction, oe.settlement_transaction,
            oe.source_adapter, oe.source_oracle, m.slug AS market_slug, m.category AS market_category,
            COALESCE(mss.completion_status, 'OPEN') AS completion_status,
            COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
            COALESCE(mss.is_resolved, FALSE) AS is_resolved,
            COALESCE(mss.is_final, FALSE) AS is_final,
            COALESCE(mss.settlement_code, 0) AS snapshot_settlement_code,
            COALESCE(mss.settlement_outcome, 'UNKNOWN') AS snapshot_settlement_outcome,
            mss.settlement_source AS snapshot_settlement_source
        FROM oracle_events oe
        LEFT JOIN markets m ON m.id = oe.market_id
        LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
        ORDER BY oe.block_number DESC, oe.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dependencies.normalize_oracle_event(row) for row in rows]


def _api_readonly() -> bool:
    return str(os.environ.get("POLYDATA_API_READONLY", "")).strip().lower() in {"1", "true", "yes", "on"}


def _content_api_refresh_enabled() -> bool:
    return str(os.environ.get("POLYDATA_CONTENT_API_REFRESH_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _content_item_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "contentType": row.get("content_type"),
        "source": row.get("source"),
        "category": row.get("category"),
        "topicId": row.get("topic_id"),
        "title": row.get("title"),
        "url": row.get("url"),
        "publishedAt": row.get("published_at"),
        "summary": row.get("summary"),
        "provider": row.get("provider"),
        "sourceCount": row.get("source_count"),
        "relevanceScore": row.get("link_score") if row.get("link_score") is not None else row.get("relevance_score"),
    }


def _content_id_for_url(url: str) -> str:
    normalized = str(url or "").strip()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"content:{digest}"


def _content_id_for_topic_url(topic_id: str, url: str) -> str:
    normalized = f"{str(topic_id or '').strip()}|{str(url or '').strip()}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"topic-content:{digest}"


_CONTENT_TABLE_EXISTS_CACHE: Dict[tuple[str, str], bool] = {}
_CONTENT_TABLES_ENSURED_CACHE: set[str] = set()
_CONTENT_TABLE_EXISTS_LOCK = threading.Lock()


def _content_table_exists(
    dependencies: ContentStorageDependencies,
    table_name: str,
) -> bool:
    backend = str(dependencies.get_backend() or "").lower()
    key = (backend, table_name)
    with _CONTENT_TABLE_EXISTS_LOCK:
        cached = _CONTENT_TABLE_EXISTS_CACHE.get(key)
    if cached is not None:
        return cached
    exists = bool(dependencies.table_exists(table_name))
    with _CONTENT_TABLE_EXISTS_LOCK:
        _CONTENT_TABLE_EXISTS_CACHE[key] = exists
    return exists


def _ensure_content_tables(
    dependencies: ContentStorageDependencies,
) -> None:
    if _api_readonly():
        return
    backend = str(dependencies.get_backend() or "").lower()
    with _CONTENT_TABLE_EXISTS_LOCK:
        if backend in _CONTENT_TABLES_ENSURED_CACHE:
            return
    conn = dependencies.get_connection(dependencies.database_path)
    try:
        if backend == "sqlite":
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS content_items (
                    id TEXT PRIMARY KEY,
                    content_type TEXT,
                    provider TEXT,
                    source TEXT,
                    category TEXT,
                    topic_id TEXT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT,
                    summary TEXT,
                    source_count INTEGER DEFAULT 1,
                    relevance_score INTEGER,
                    raw_payload TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS content_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    event_slug TEXT,
                    category TEXT,
                    topic_id TEXT,
                    link_score INTEGER,
                    link_reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(content_id, market_id)
                )
                """
            )
            for table, column_sql in (
                ("content_items", "provider TEXT"),
                ("content_items", "category TEXT"),
                ("content_items", "topic_id TEXT"),
                ("content_items", "source_count INTEGER DEFAULT 1"),
                ("content_items", "relevance_score INTEGER"),
                ("content_items", "raw_payload TEXT"),
                ("content_links", "link_score INTEGER"),
                ("content_links", "link_reason TEXT"),
                ("content_links", "topic_id TEXT"),
            ):
                column = column_sql.split()[0]
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_sql}")
                except Exception:
                    pass
                _ = column
        else:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('polydata_content_schema'))")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS content_items (
                    id TEXT PRIMARY KEY,
                    content_type TEXT,
                    provider TEXT,
                    source TEXT,
                    category TEXT,
                    topic_id TEXT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT,
                    summary TEXT,
                    source_count INTEGER DEFAULT 1,
                    relevance_score INTEGER,
                    raw_payload TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS content_links (
                    id BIGSERIAL PRIMARY KEY,
                    content_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
                    market_id BIGINT NOT NULL,
                    event_slug TEXT,
                    category TEXT,
                    topic_id TEXT,
                    link_score INTEGER,
                    link_reason TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(content_id, market_id)
                )
                """
            )
            for table, column_sql in (
                ("content_items", "provider TEXT"),
                ("content_items", "category TEXT"),
                ("content_items", "topic_id TEXT"),
                ("content_items", "source_count INTEGER DEFAULT 1"),
                ("content_items", "relevance_score INTEGER"),
                ("content_items", "raw_payload TEXT"),
                ("content_links", "link_score INTEGER"),
                ("content_links", "link_reason TEXT"),
                ("content_links", "topic_id TEXT"),
            ):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column_sql}")
            conn.execute(
                """
                DO $$
                DECLARE constraint_name text;
                BEGIN
                    SELECT conname INTO constraint_name
                    FROM pg_constraint
                    WHERE conrelid = 'content_items'::regclass
                      AND contype = 'u'
                      AND pg_get_constraintdef(oid) = 'UNIQUE (url)';
                    IF constraint_name IS NOT NULL THEN
                        EXECUTE format('ALTER TABLE content_items DROP CONSTRAINT %I', constraint_name);
                    END IF;
                END $$;
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_content_items_published_at ON content_items (published_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_content_items_topic_time ON content_items (topic_id, published_at DESC)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_content_items_topic_url ON content_items (COALESCE(topic_id, ''), url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_content_links_market_score ON content_links (market_id, link_score DESC, created_at DESC)")
        conn.commit()
        backend_key = str(dependencies.get_backend() or "").lower()
        with _CONTENT_TABLE_EXISTS_LOCK:
            _CONTENT_TABLE_EXISTS_CACHE[(backend_key, "content_items")] = True
            _CONTENT_TABLE_EXISTS_CACHE[(backend_key, "content_links")] = True
            _CONTENT_TABLES_ENSURED_CACHE.add(backend_key)
    except Exception:
        conn.rollback()
        dependencies.application.logger.exception(
            "content table ensure failed",
        )
    finally:
        conn.close()


def _persist_related_content(
    dependencies: ContentStorageDependencies,
    *,
    market_id: int,
    market: Dict[str, Any],
    items: List[Dict[str, Any]],
) -> None:
    if _api_readonly() or not items:
        return
    _ensure_content_tables(dependencies)
    conn = dependencies.get_connection(dependencies.database_path)
    backend = str(dependencies.get_backend() or "").lower()
    try:
        for item in items:
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url or not title:
                continue
            content_id = _content_id_for_url(url)
            relevance_score = int(item.get("relevanceScore") or 0)
            source_count = int(item.get("sourceCount") or 1)
            item_params = (
                content_id,
                item.get("contentType") or "news",
                item.get("provider") or "rss",
                item.get("source") or "intel",
                item.get("category") or market.get("category"),
                item.get("topicId") or "",
                title,
                url,
                item.get("publishedAt"),
                item.get("summary"),
                source_count,
                relevance_score,
                json.dumps(item, ensure_ascii=True, sort_keys=True),
            )
            link_params = (
                content_id,
                market_id,
                market.get("slug"),
                market.get("category"),
                item.get("topicId") or "",
                relevance_score,
                item.get("provider") or "runtime-intel",
            )
            if backend == "sqlite":
                conn.execute(
                    """
                    INSERT INTO content_items (
                        id, content_type, provider, source, category, topic_id, title, url, published_at,
                        summary, source_count, relevance_score, raw_payload, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO UPDATE SET
                        content_type = excluded.content_type,
                        provider = excluded.provider,
                        source = excluded.source,
                        category = excluded.category,
                        topic_id = COALESCE(NULLIF(excluded.topic_id, ''), content_items.topic_id),
                        title = excluded.title,
                        url = excluded.url,
                        published_at = COALESCE(excluded.published_at, content_items.published_at),
                        summary = excluded.summary,
                        source_count = max(COALESCE(content_items.source_count, 1), COALESCE(excluded.source_count, 1)),
                        relevance_score = max(COALESCE(content_items.relevance_score, 0), COALESCE(excluded.relevance_score, 0)),
                        raw_payload = excluded.raw_payload,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    item_params,
                )
                conn.execute(
                    """
                    INSERT INTO content_links (
                        content_id, market_id, event_slug, category, topic_id, link_score, link_reason
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (content_id, market_id) DO UPDATE SET
                        category = excluded.category,
                        topic_id = COALESCE(NULLIF(excluded.topic_id, ''), content_links.topic_id),
                        link_score = max(COALESCE(content_links.link_score, 0), COALESCE(excluded.link_score, 0)),
                        link_reason = excluded.link_reason
                    """,
                    link_params,
                )
            else:
                conn.execute(
                    """
                    INSERT INTO content_items (
                        id, content_type, provider, source, category, topic_id, title, url, published_at,
                        summary, source_count, relevance_score, raw_payload, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO UPDATE SET
                        content_type = EXCLUDED.content_type,
                        provider = EXCLUDED.provider,
                        source = EXCLUDED.source,
                        category = EXCLUDED.category,
                        topic_id = COALESCE(NULLIF(EXCLUDED.topic_id, ''), content_items.topic_id),
                        title = EXCLUDED.title,
                        url = EXCLUDED.url,
                        published_at = COALESCE(EXCLUDED.published_at, content_items.published_at),
                        summary = EXCLUDED.summary,
                        source_count = GREATEST(COALESCE(content_items.source_count, 1), COALESCE(EXCLUDED.source_count, 1)),
                        relevance_score = GREATEST(COALESCE(content_items.relevance_score, 0), COALESCE(EXCLUDED.relevance_score, 0)),
                        raw_payload = EXCLUDED.raw_payload,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    item_params,
                )
                conn.execute(
                    """
                    INSERT INTO content_links (
                        content_id, market_id, event_slug, category, topic_id, link_score, link_reason
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (content_id, market_id) DO UPDATE SET
                        category = EXCLUDED.category,
                        topic_id = COALESCE(NULLIF(EXCLUDED.topic_id, ''), content_links.topic_id),
                        link_score = GREATEST(COALESCE(content_links.link_score, 0), COALESCE(EXCLUDED.link_score, 0)),
                        link_reason = EXCLUDED.link_reason
                    """,
                    link_params,
                )
        conn.commit()
    except Exception:
        conn.rollback()
        dependencies.application.logger.exception(
            "content persistence failed market_id=%s",
            market_id,
        )
    finally:
        conn.close()


def _persist_topic_content(
    dependencies: ContentStorageDependencies,
    *,
    topic_id: str,
    items: List[Dict[str, Any]],
) -> int:
    if _api_readonly() or not items:
        return 0
    _ensure_content_tables(dependencies)
    conn = dependencies.get_connection(dependencies.database_path)
    backend = str(dependencies.get_backend() or "").lower()
    stored = 0
    try:
        for item in items:
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url or not title:
                continue
            item_topic_id = str(item.get("topicId") or topic_id or "").strip()
            content_id = _content_id_for_url(url) if backend == "sqlite" else _content_id_for_topic_url(item_topic_id, url)
            params = (
                content_id,
                item.get("contentType") or "news",
                item.get("provider") or "rss",
                item.get("source") or "intel",
                item.get("category") or item_topic_id,
                item_topic_id,
                title,
                url,
                item.get("publishedAt"),
                item.get("summary"),
                int(item.get("sourceCount") or 1),
                int(item.get("relevanceScore") or 0),
                json.dumps(item, ensure_ascii=True, sort_keys=True),
            )
            if backend == "sqlite":
                conn.execute(
                    """
                    INSERT INTO content_items (
                        id, content_type, provider, source, category, topic_id, title, url, published_at,
                        summary, source_count, relevance_score, raw_payload, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO UPDATE SET
                        content_type = excluded.content_type,
                        provider = excluded.provider,
                        source = excluded.source,
                        category = excluded.category,
                        topic_id = COALESCE(NULLIF(excluded.topic_id, ''), content_items.topic_id),
                        title = excluded.title,
                        url = excluded.url,
                        published_at = COALESCE(excluded.published_at, content_items.published_at),
                        summary = excluded.summary,
                        source_count = max(COALESCE(content_items.source_count, 1), COALESCE(excluded.source_count, 1)),
                        relevance_score = max(COALESCE(content_items.relevance_score, 0), COALESCE(excluded.relevance_score, 0)),
                        raw_payload = excluded.raw_payload,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    params,
                )
            else:
                conn.execute(
                    """
                    INSERT INTO content_items (
                        id, content_type, provider, source, category, topic_id, title, url, published_at,
                        summary, source_count, relevance_score, raw_payload, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO UPDATE SET
                        content_type = EXCLUDED.content_type,
                        provider = EXCLUDED.provider,
                        source = EXCLUDED.source,
                        category = EXCLUDED.category,
                        topic_id = COALESCE(NULLIF(EXCLUDED.topic_id, ''), content_items.topic_id),
                        title = EXCLUDED.title,
                        url = EXCLUDED.url,
                        published_at = COALESCE(EXCLUDED.published_at, content_items.published_at),
                        summary = EXCLUDED.summary,
                        source_count = GREATEST(COALESCE(content_items.source_count, 1), COALESCE(EXCLUDED.source_count, 1)),
                        relevance_score = GREATEST(COALESCE(content_items.relevance_score, 0), COALESCE(EXCLUDED.relevance_score, 0)),
                        raw_payload = EXCLUDED.raw_payload,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    params,
                )
            stored += 1
        conn.commit()
    except Exception:
        conn.rollback()
        dependencies.application.logger.exception(
            "topic content persistence failed topic_id=%s",
            topic_id,
        )
    finally:
        conn.close()
    return stored


def _delete_topic_content(
    dependencies: ContentStorageDependencies,
    *,
    topic_id: str,
) -> None:
    if _api_readonly() or not topic_id:
        return
    _ensure_content_tables(dependencies)
    conn = dependencies.get_connection(dependencies.database_path)
    try:
        conn.execute("DELETE FROM content_links WHERE topic_id = ?", (topic_id,))
        conn.execute("DELETE FROM content_items WHERE topic_id = ?", (topic_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        dependencies.application.logger.exception(
            "topic content delete failed topic_id=%s",
            topic_id,
        )
    finally:
        conn.close()


def _delete_market_content_links(
    dependencies: ContentStorageDependencies,
    *,
    market_id: int,
) -> None:
    if _api_readonly() or not market_id:
        return
    _ensure_content_tables(dependencies)
    conn = dependencies.get_connection(dependencies.database_path)
    try:
        conn.execute("DELETE FROM content_links WHERE market_id = ?", (market_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        dependencies.application.logger.exception(
            "market content links delete failed market_id=%s",
            market_id,
        )
    finally:
        conn.close()


def _fetch_persisted_related_content(
    dependencies: ContentStorageDependencies,
    market_id: int,
    limit: int,
) -> List[Dict[str, Any]]:
    if not (
        _content_table_exists(dependencies, "content_items")
        and _content_table_exists(dependencies, "content_links")
    ):
        return []
    return dependencies.query_all(
        """
        SELECT
            ci.id,
            ci.content_type,
            ci.provider,
            ci.source,
            ci.category,
            COALESCE(cl.topic_id, ci.topic_id) AS topic_id,
            ci.title,
            ci.url,
            ci.published_at,
            ci.summary,
            ci.source_count,
            ci.relevance_score,
            cl.link_score
        FROM content_links cl
        JOIN content_items ci ON ci.id = cl.content_id
        WHERE cl.market_id = ?
        ORDER BY COALESCE(cl.link_score, ci.relevance_score, 0) DESC, ci.published_at DESC
        LIMIT ?
        """,
        (market_id, limit),
    )


def _fetch_topic_content_candidates(
    dependencies: ContentStorageDependencies,
    topic_ids: List[str],
    limit: int,
) -> List[Dict[str, Any]]:
    if not topic_ids or not _content_table_exists(
        dependencies,
        "content_items",
    ):
        return []
    placeholders = ",".join(["?"] * len(topic_ids))
    return dependencies.query_all(
        f"""
        SELECT
            id,
            content_type,
            provider,
            source,
            category,
            topic_id,
            title,
            url,
            published_at,
            summary,
            source_count,
            relevance_score,
            relevance_score AS link_score
        FROM content_items
        WHERE topic_id IN ({placeholders})
        ORDER BY CASE WHEN published_at IS NULL THEN 1 ELSE 0 END, published_at DESC, relevance_score DESC
        LIMIT ?
        """,
        (*topic_ids, max(limit * 8, 32)),
    )


def _score_content_for_market(row: Dict[str, Any], market: Dict[str, Any], tags: List[str]) -> int:
    title = str(row.get("title") or "")
    quality_text = " ".join(str(row.get(key) or "") for key in ("source", "title", "summary", "url")).lower()
    if (
        any(token in quality_text for token in ("coupon", "promo code", "mshale"))
        or re.search(r"\([A-Za-z0-9]{8,}\)", title)
        or ("price prediction" in quality_text and re.search(r"#(?:btc|eth|crypto)|crash news|important analysis", quality_text))
    ):
        return 0
    text = " ".join(
        str(value or "")
        for value in (
            row.get("source"),
            row.get("category"),
            row.get("topic_id"),
            row.get("title"),
            row.get("summary"),
        )
    ).lower()
    raw_terms = [market.get("title"), market.get("category"), *tags]
    stopwords = {
        "will",
        "market",
        "markets",
        "yes",
        "above",
        "below",
        "over",
        "under",
        "close",
        "closes",
        "reach",
        "winner",
        "with",
        "from",
        "than",
        "this",
        "that",
        "game",
        "games",
        "sports",
        "esports",
        "match",
        "map",
        "round",
        "rounds",
        "handicap",
        "team",
        "teams",
        "versus",
        "vs",
    }
    terms: List[str] = []
    for raw in raw_terms:
        for piece in str(raw or "").lower().replace("?", " ").replace(",", " ").split():
            cleaned = "".join(ch for ch in piece if ch.isalnum())
            if len(cleaned) >= 3 and cleaned not in stopwords and cleaned not in terms:
                terms.append(cleaned)
    compact_text = re.sub(r"[^a-z0-9]+", "", text)
    hits = sum(1 for term in terms[:18] if term in text or term in compact_text)
    topic_bonus = 16 if str(row.get("topic_id") or "").lower() in text else 0
    existing_score = int(row.get("relevance_score") or row.get("link_score") or 0)
    published_penalty = 24 if not row.get("published_at") else 0
    return hits * 12 + topic_bonus + min(24, existing_score // 3) - published_penalty


def _rank_topic_candidates_for_market(rows: List[Dict[str, Any]], market: Dict[str, Any], tags: List[str], limit: int) -> List[Dict[str, Any]]:
    ranked: List[tuple[int, Dict[str, Any]]] = []
    for row in rows:
        score = _score_content_for_market(row, market, tags)
        if score < 20:
            continue
        payload = _content_item_payload(row)
        payload["relevanceScore"] = score
        ranked.append((score, payload))
    ranked.sort(key=lambda entry: (entry[0], entry[1].get("publishedAt") or ""), reverse=True)
    return [payload for _, payload in ranked[:limit]]


def _topic_rows_to_payloads(rows: List[Dict[str, Any]], *, limit: int, default_score: int = 12) -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        payload = _content_item_payload(row)
        key = str(payload.get("url") or payload.get("id") or "")
        if key in seen:
            continue
        seen.add(key)
        if not payload.get("relevanceScore"):
            payload["relevanceScore"] = default_score
        payloads.append(payload)
        if len(payloads) >= limit:
            break
    return payloads


def _merge_content_payloads(primary: List[Dict[str, Any]], fallback: List[Dict[str, Any]], *, limit: int) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*primary, *fallback]:
        key = str(item.get("url") or item.get("id") or "")
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


_RELATED_TAB_CONTENT_TYPES = ("news", "video", "report", "research")
_RELATED_TAB_TARGET_COUNT = 6


def _fetch_content_type_candidates(
    dependencies: ContentStorageDependencies,
    topic_ids: List[str],
    content_type: str,
    limit: int,
) -> List[Dict[str, Any]]:
    topic_ids = [str(topic_id or "").strip() for topic_id in topic_ids if str(topic_id or "").strip()]
    content_type = str(content_type or "").strip().lower()
    if not topic_ids or not content_type or not _content_table_exists(
        dependencies,
        "content_items",
    ):
        return []
    placeholders = ",".join(["?"] * len(topic_ids))
    return dependencies.query_all(
        f"""
        SELECT
            id,
            content_type,
            provider,
            source,
            category,
            topic_id,
            title,
            url,
            published_at,
            summary,
            source_count,
            relevance_score,
            relevance_score AS link_score
        FROM content_items
        WHERE topic_id IN ({placeholders})
          AND content_type = ?
        ORDER BY CASE WHEN published_at IS NULL THEN 1 ELSE 0 END, published_at DESC, relevance_score DESC
        LIMIT ?
        """,
        (*topic_ids, content_type, max(1, limit)),
    )


def _content_payload_as_row(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id"),
        "content_type": item.get("contentType"),
        "provider": item.get("provider"),
        "source": item.get("source"),
        "category": item.get("category"),
        "topic_id": item.get("topicId"),
        "title": item.get("title"),
        "url": item.get("url"),
        "published_at": item.get("publishedAt"),
        "summary": item.get("summary"),
        "source_count": item.get("sourceCount"),
        "relevance_score": item.get("relevanceScore"),
        "link_score": item.get("relevanceScore"),
    }


def _augment_related_content_tabs(
    dependencies: ContentStorageDependencies,
    *,
    market_id: int,
    market: Dict[str, Any],
    tags: List[str],
    topic_ids: List[str],
    rows: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    primary_topic = str(topic_ids[0] if topic_ids else "").strip()
    if primary_topic:
        for content_type in _RELATED_TAB_CONTENT_TYPES:
            primary_rows = _fetch_content_type_candidates(
                dependencies,
                [primary_topic],
                content_type,
                1,
            )
            if primary_rows:
                rows = [
                    row for row in rows
                    if not (
                        str(row.get("content_type") or "").strip().lower() == content_type
                        and str(row.get("topic_id") or "").strip() != primary_topic
                    )
                ]
    existing_by_type = {
        content_type: sum(1 for row in rows if str(row.get("content_type") or "").strip().lower() == content_type)
        for content_type in _RELATED_TAB_CONTENT_TYPES
    }
    target_count = min(_RELATED_TAB_TARGET_COUNT, max(2, limit // max(1, len(_RELATED_TAB_CONTENT_TYPES))))
    needed_by_type = {
        content_type: max(0, target_count - existing_count)
        for content_type, existing_count in existing_by_type.items()
    }
    if not any(needed_by_type.values()):
        return rows
    seen_urls = {str(row.get("url") or "").strip() for row in rows if str(row.get("url") or "").strip()}
    supplemental_items: List[Dict[str, Any]] = []
    for content_type, needed_count in needed_by_type.items():
        if needed_count <= 0:
            continue
        candidate_rows: List[Dict[str, Any]] = []
        for topic_id in topic_ids:
            topic_rows = [
                row
                for row in _fetch_content_type_candidates(
                    dependencies,
                    [topic_id],
                    content_type,
                    max(12, needed_count * 4),
                )
                if str(row.get("url") or "").strip() not in seen_urls
            ]
            if topic_rows:
                candidate_rows.extend(topic_rows)
                seen_urls.update(str(row.get("url") or "").strip() for row in topic_rows if str(row.get("url") or "").strip())
            if len(candidate_rows) >= needed_count * 4:
                break
        if not candidate_rows:
            continue
        items = _merge_content_payloads(
            _rank_topic_candidates_for_market(candidate_rows, market, tags, needed_count),
            _topic_rows_to_payloads(candidate_rows, limit=needed_count, default_score=18),
            limit=needed_count,
        )
        for item in items:
            item["relevanceScore"] = max(64, int(item.get("relevanceScore") or 0))
        supplemental_items.extend(items)
        seen_urls.update(str(item.get("url") or "").strip() for item in items if str(item.get("url") or "").strip())
    if not supplemental_items:
        return rows
    if _api_readonly():
        return [*rows, *(_content_payload_as_row(item) for item in supplemental_items)]
    _persist_related_content(
        dependencies,
        market_id=market_id,
        market=market,
        items=supplemental_items,
    )
    return _fetch_persisted_related_content(
        dependencies,
        market_id,
        max(limit + len(supplemental_items) + 4, limit),
    )


def refresh_topic_content(
    ctx: Mapping[str, Any],
    *,
    topic_ids: List[str] | None = None,
    limit_per_topic: int = 24,
) -> Dict[str, Any]:
    return _refresh_topic_content(
        ContentRefreshDependencies.from_context(ctx),
        topic_ids=topic_ids,
        limit_per_topic=limit_per_topic,
    )


def _refresh_topic_content(
    dependencies: ContentRefreshDependencies,
    *,
    topic_ids: List[str] | None = None,
    limit_per_topic: int = 24,
) -> Dict[str, Any]:
    runtime_payload = dependencies.content_runtime_provider.refresh_topics(
        topic_ids=topic_ids,
        limit_per_topic=limit_per_topic,
    )
    stored_by_topic: Dict[str, int] = {}
    skipped_by_topic: Dict[str, Dict[str, int]] = {}
    for topic_id, items in runtime_payload.items():
        existing_count = _count_topic_content(
            dependencies.storage,
            topic_id=topic_id,
        )
        fresh_count = len(items)
        if existing_count > 0 and fresh_count < max(4, existing_count // 2):
            stored_by_topic[topic_id] = existing_count
            skipped_by_topic[topic_id] = {"existing": existing_count, "fresh": fresh_count}
            continue
        _delete_topic_content(dependencies.storage, topic_id=topic_id)
        stored_by_topic[topic_id] = _persist_topic_content(
            dependencies.storage,
            topic_id=topic_id,
            items=items,
        )
    return {
        "sourceMode": "topic-registry",
        "topicCount": len(runtime_payload),
        "itemCount": sum(len(items) for items in runtime_payload.values()),
        "storedCount": sum(stored_by_topic.values()),
        "topics": stored_by_topic,
        "skippedTopics": skipped_by_topic,
    }


def _count_topic_content(
    dependencies: ContentStorageDependencies,
    *,
    topic_id: str,
) -> int:
    topic_id = str(topic_id or "").strip()
    if not topic_id or not _content_table_exists(
        dependencies,
        "content_items",
    ):
        return 0
    row = dependencies.query_one(
        "SELECT COUNT(*) AS count FROM content_items WHERE topic_id = ?",
        (topic_id,),
    ) or {}
    return int(row.get("count") or 0)


def get_related_content_by_market_id(
    ctx: Mapping[str, Any],
    market_id: int,
    limit: int = 8,
) -> Dict[str, Any]:
    return _get_related_content_by_market_id(
        RelatedContentQueryDependencies.from_context(ctx),
        market_id,
        limit=limit,
    )


def _get_related_content_by_market_id(
    dependencies: RelatedContentQueryDependencies,
    market_id: int,
    limit: int = 8,
) -> Dict[str, Any]:
    market = dependencies.get_market_by_id(market_id)
    if not market:
        return {"marketId": market_id, "localMarketId": market_id, "items": []}
    _ensure_content_tables(dependencies.storage)
    tags = dependencies.parse_json_list(market.get("tags"))
    topic_ids = dependencies.content_runtime_provider.infer_market_topics(
        market_title=str(market.get("title") or ""),
        category=str(market.get("category") or ""),
        tags=tags,
    )
    rows = _fetch_persisted_related_content(
        dependencies.storage,
        market_id,
        max(limit, 24),
    )
    if rows:
        row_topics = {str(row.get("topic_id") or "").strip() for row in rows if str(row.get("topic_id") or "").strip()}
        primary_topic = str(topic_ids[0] if topic_ids else "").strip()
        if row_topics and primary_topic and primary_topic in row_topics:
            rows = _augment_related_content_tabs(
                dependencies.storage,
                market_id=market_id,
                market=market,
                tags=tags,
                topic_ids=topic_ids,
                rows=rows,
                limit=limit,
            )
            return {
                "marketId": market_id,
                "localMarketId": market_id,
                "items": [_content_item_payload(row) for row in rows],
                "sourceMode": "database",
                "topicIds": topic_ids,
            }
        _delete_market_content_links(
            dependencies.storage,
            market_id=market_id,
        )
        rows = []
    if rows:
        return {
            "marketId": market_id,
            "localMarketId": market_id,
            "items": [_content_item_payload(row) for row in rows],
            "sourceMode": "database",
        }
    primary_topic_id = str(topic_ids[0] if topic_ids else "").strip()
    primary_rows = (
        _fetch_topic_content_candidates(
            dependencies.storage,
            [primary_topic_id],
            limit,
        )
        if primary_topic_id
        else []
    )
    primary_items = _merge_content_payloads(
        _rank_topic_candidates_for_market(primary_rows, market, tags, limit),
        _topic_rows_to_payloads(primary_rows, limit=limit, default_score=12),
        limit=limit,
    )
    if primary_items:
        _persist_related_content(
            dependencies.storage,
            market_id=market_id,
            market=market,
            items=primary_items,
        )
        persisted_rows = _fetch_persisted_related_content(
            dependencies.storage,
            market_id,
            max(limit, 24),
        )
        persisted_rows = _augment_related_content_tabs(
            dependencies.storage,
            market_id=market_id,
            market=market,
            tags=tags,
            topic_ids=topic_ids,
            rows=persisted_rows,
            limit=limit,
        )
        return {
            "marketId": market_id,
            "localMarketId": market_id,
            "items": [_content_item_payload(row) for row in persisted_rows] if persisted_rows else primary_items,
            "sourceMode": "database:topic-pool",
            "topicIds": topic_ids,
        }
    topic_rows = _fetch_topic_content_candidates(
        dependencies.storage,
        topic_ids,
        limit,
    )
    topic_items = _rank_topic_candidates_for_market(topic_rows, market, tags, limit)
    if topic_items:
        _persist_related_content(
            dependencies.storage,
            market_id=market_id,
            market=market,
            items=topic_items,
        )
        persisted_rows = _fetch_persisted_related_content(
            dependencies.storage,
            market_id,
            max(limit, 24),
        )
        persisted_rows = _augment_related_content_tabs(
            dependencies.storage,
            market_id=market_id,
            market=market,
            tags=tags,
            topic_ids=topic_ids,
            rows=persisted_rows,
            limit=limit,
        )
        return {
            "marketId": market_id,
            "localMarketId": market_id,
            "items": [_content_item_payload(row) for row in persisted_rows] if persisted_rows else topic_items,
            "sourceMode": "database:topic-pool",
            "topicIds": topic_ids,
        }
    if not _content_api_refresh_enabled():
        return {
            "marketId": market_id,
            "localMarketId": market_id,
            "items": [],
            "sourceMode": "database:topic-pool-miss",
            "topicIds": topic_ids,
        }
    _refresh_topic_content(
        ContentRefreshDependencies(
            storage=dependencies.storage,
            content_runtime_provider=dependencies.content_runtime_provider,
        ),
        topic_ids=topic_ids[:3],
        limit_per_topic=max(12, limit * 2),
    )
    topic_rows = _fetch_topic_content_candidates(
        dependencies.storage,
        topic_ids,
        limit,
    )
    topic_items = _rank_topic_candidates_for_market(topic_rows, market, tags, limit)
    if topic_items:
        _persist_related_content(
            dependencies.storage,
            market_id=market_id,
            market=market,
            items=topic_items,
        )
        persisted_rows = _fetch_persisted_related_content(
            dependencies.storage,
            market_id,
            limit,
        )
        return {
            "marketId": market_id,
            "localMarketId": market_id,
            "items": [_content_item_payload(row) for row in persisted_rows] if persisted_rows else topic_items,
            "sourceMode": "database:topic-refresh",
            "topicIds": topic_ids,
        }
    runtime_items = dependencies.content_runtime_provider.get_related_news(
        market_title=str(market.get("title") or ""),
        category=str(market.get("category") or ""),
        tags=tags,
        limit=limit,
    )
    _persist_related_content(
        dependencies.storage,
        market_id=market_id,
        market=market,
        items=runtime_items,
    )
    persisted_rows = _fetch_persisted_related_content(
        dependencies.storage,
        market_id,
        limit,
    )
    if persisted_rows:
        return {
            "marketId": market_id,
            "localMarketId": market_id,
            "items": [_content_item_payload(row) for row in persisted_rows],
            "sourceMode": "database:runtime-intel",
        }
    return {
        "marketId": market_id,
        "localMarketId": market_id,
        "items": runtime_items,
        "sourceMode": "runtime-intel",
    }


def get_latest_content_snapshot(
    ctx: Mapping[str, Any],
    limit: int = 8,
) -> Dict[str, Any]:
    return _get_latest_content_snapshot(
        LatestContentQueryDependencies.from_context(ctx),
        limit=limit,
    )


def _get_latest_content_snapshot(
    dependencies: LatestContentQueryDependencies,
    limit: int = 8,
) -> Dict[str, Any]:
    _ensure_content_tables(dependencies.storage)
    cache_key = json.dumps(
        {
            "limit": limit,
            "v": 2,
        },
        sort_keys=True,
        ensure_ascii=True,
    )

    def _builder() -> Dict[str, Any]:
        if _content_table_exists(dependencies.storage, "content_items"):
            rows = dependencies.storage.query_all(
                """
                SELECT id, content_type, provider, source, category, topic_id, title, url, published_at, summary, source_count, relevance_score
                FROM content_items
                WHERE COALESCE(topic_id, '') <> ''
                ORDER BY CASE WHEN published_at IS NULL THEN 1 ELSE 0 END, published_at DESC, relevance_score DESC
                LIMIT ?
                """,
                (limit,),
            )
            return {
                "items": [_content_item_payload(row) for row in rows],
                "sourceMode": "database",
            }
        if _content_api_refresh_enabled():
            return {
                "items": dependencies.content_runtime_provider.get_latest_items(
                    limit=limit,
                ),
                "sourceMode": "runtime-rss-expanded",
            }
        return {
            "items": [],
            "sourceMode": "database-empty",
        }

    return dependencies.get_snapshot_payload(
        "snapshot:content:latest",
        cache_key,
        _builder,
        ttl_seconds=300,
    )
