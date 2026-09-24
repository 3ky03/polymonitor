#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 `trades` 增量同步到面向分析和查询加速的派生表。

目标：
1. 为地址分析生成 `trade_addresses`、`address_trade_daily_stats`、`address_trade_totals`。
2. 为网站查询生成 `market_trade_daily_stats`、`market_latest_prices`。
3. 不直接对超大 `trades` 主表做高风险重 DDL，而是通过增量派生表提速。

如果只需要 market panel 的基础数据，可使用 --market-only，仅写入
`market_trade_daily_stats`、`market_latest_prices` 和 `sync_state`。
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import (  # type: ignore
    DEFAULT_DB_PATH,
    add_db_cli_args,
    configure_db_from_args,
    describe_db_target,
    get_connection,
    init_schema,
    table_exists,
)
from db.trade_v2 import TRADE_V2_CORE_TABLE, get_trade_stats_source, sql_identifier, uint256_storage_to_text
from oracle.settlement_parser import (
    OUTCOME_UNKNOWN,
    SettlementResult,
    choose_best_settlement,
    parse_fast_settlement_code,
    parse_oracle_settlement_event,
)

TRADE_ANALYTICS_SYNC_KEY = "trade_analytics_sync"
MARKET_STATUS_SNAPSHOT_SYNC_KEY = "market_status_snapshot_sync_v3"
MARKET_LIST_SERVING_DAY_SYNC_KEY = "market_list_serving_day_sync"
DEFAULT_BATCH_SIZE = 50_000
DEFAULT_WATCH_INTERVAL_SECONDS = 15
DEFAULT_ORACLE_EVENT_BATCH_SIZE = 100_000
FEE_DIVISOR = Decimal("1000000")
ZERO = Decimal("0")
COMPLETION_OPEN = "OPEN"
COMPLETION_ENDED_AWAITING_ORACLE = "ENDED_AWAITING_ORACLE"
COMPLETION_PROPOSED = "PROPOSED"
COMPLETION_DISPUTED = "DISPUTED"
COMPLETION_SETTLED = "SETTLED"
COMPLETION_CANCELLED = "CANCELLED"
COMPLETION_GAMMA_CLOSED_FALLBACK = "GAMMA_CLOSED_FALLBACK"
COMPLETION_CLOSED_UNRESOLVED = "CLOSED_UNRESOLVED"
COMPLETION_UNKNOWN = "UNKNOWN"
EXCHANGE_ADDRESSES = {
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",
}
@dataclass
class LatestTradePoint:
    trade_at: Optional[str]
    block_number: Optional[int]
    log_index: Optional[int]
    price: Optional[Decimal]


def _normalize_address(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    return text


def _normalize_side(value: Any) -> str:
    return str(value or "").strip().upper()


def _opposite_side(side: str) -> str:
    normalized = _normalize_side(side)
    if normalized == "BUY":
        return "SELL"
    if normalized == "SELL":
        return "BUY"
    return "UNKNOWN"


def _parse_decimal(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def _db_decimal(value: Decimal) -> str:
    return format(value, "f")


def _parse_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_trade_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace(" UTC", "Z").replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_mysql_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def _trade_date_text(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.date().isoformat()


def _is_newer_trade(
    block_number: Optional[int],
    log_index: Optional[int],
    current: Optional[LatestTradePoint],
) -> bool:
    if current is None:
        return True
    current_block = current.block_number if current.block_number is not None else -1
    current_log_index = current.log_index if current.log_index is not None else -1
    next_block = block_number if block_number is not None else -1
    next_log_index = log_index if log_index is not None else -1
    if next_block != current_block:
        return next_block > current_block
    return next_log_index > current_log_index


def _set_sync_state(
    conn,
    sync_state_key: str,
    *,
    value: Optional[str] = None,
    last_block: Optional[int] = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO sync_state (`key`, value, last_block, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            sync_state_key,
            value,
            last_block,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        ),
    )


def _update_sync_state(conn, last_trade_id: int, sync_state_key: str) -> None:
    _set_sync_state(conn, sync_state_key, value=str(last_trade_id), last_block=last_trade_id)


def _get_sync_state_row(conn, sync_state_key: str) -> Dict[str, Any]:
    cursor = conn.cursor()
    cursor.execute("SELECT value, last_block FROM sync_state WHERE `key` = ?", (sync_state_key,))
    row = cursor.fetchone()
    return row.as_dict() if hasattr(row, "as_dict") else dict(row) if row else {}


def _row_to_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "as_dict"):
        return row.as_dict()
    return dict(row)


def _chunked_values(values: Sequence[int], size: int = 900) -> Iterable[Sequence[int]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def get_last_processed_trade_id(
    db_path: str = DEFAULT_DB_PATH,
    sync_state_key: str = TRADE_ANALYTICS_SYNC_KEY,
) -> int:
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT last_block FROM sync_state WHERE `key` = ?", (sync_state_key,))
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def _current_stats_since_date() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


def _threshold_datetime_24h() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=1)


def _format_serving_threshold(conn, value: datetime) -> str:
    if hasattr(conn, "_raw_conn"):
        return _format_mysql_datetime(value)
    return value.isoformat().replace("+00:00", "Z")


def _format_trade_threshold(value: datetime, *, trade_source: str) -> str:
    if trade_source == TRADE_V2_CORE_TABLE:
        return _format_mysql_datetime(value)
    return value.isoformat().replace("+00:00", "Z")


def _is_at_or_before_threshold(value: Any, threshold_dt: datetime) -> bool:
    parsed = _parse_trade_time(value)
    return parsed is not None and parsed <= threshold_dt


def _fetch_price_24h_ago_map(conn, market_ids: Sequence[int], threshold_dt: datetime) -> Dict[int, Any]:
    normalized_ids = sorted({int(market_id) for market_id in market_ids if market_id})
    if not normalized_ids:
        return {}
    trade_source = sql_identifier(get_trade_stats_source())
    placeholders = ", ".join("?" for _ in normalized_ids)
    if trade_source == TRADE_V2_CORE_TABLE:
        time_column = "block_time"
        order_columns = "block_time DESC, block_number DESC, log_index DESC"
        yes_price_expr = "CASE WHEN outcome_code = 2 THEN 1 - price ELSE price END"
    else:
        time_column = "timestamp"
        order_columns = "timestamp DESC, block_number DESC, log_index DESC"
        yes_price_expr = "CASE WHEN UPPER(COALESCE(outcome, '')) = 'NO' THEN 1 - price ELSE price END"
    threshold_value = _format_trade_threshold(threshold_dt, trade_source=trade_source)
    cursor = conn.cursor()
    cursor.execute(
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
        (*normalized_ids, threshold_value),
    )
    return {
        int((row.as_dict() if hasattr(row, "as_dict") else dict(row))["market_id"]): (row.as_dict() if hasattr(row, "as_dict") else dict(row)).get("price")
        for row in cursor.fetchall()
        if (row.as_dict() if hasattr(row, "as_dict") else dict(row)).get("market_id") is not None
    }


def _refresh_market_list_price_24h_ago(conn, market_ids: Sequence[int], threshold_dt: datetime) -> int:
    normalized_ids = sorted({int(market_id) for market_id in market_ids if market_id})
    if not normalized_ids:
        return 0
    placeholders = ", ".join("?" for _ in normalized_ids)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT market_id, latest_price, latest_trade_at
        FROM market_list_serving
        WHERE market_id IN ({placeholders})
        """,
        tuple(normalized_ids),
    )
    price_map = _fetch_price_24h_ago_map(conn, normalized_ids, threshold_dt)
    rows = []
    for row in cursor.fetchall():
        payload = row.as_dict() if hasattr(row, "as_dict") else dict(row)
        market_id = payload.get("market_id")
        if market_id is None:
            continue
        market_id_int = int(market_id)
        latest_price = payload.get("latest_price")
        latest_trade_at = payload.get("latest_trade_at")
        if latest_trade_at is not None and _is_at_or_before_threshold(latest_trade_at, threshold_dt):
            price_24h_ago = latest_price
        else:
            price_24h_ago = price_map.get(market_id_int)
        rows.append((price_24h_ago, market_id_int))
    if rows:
        conn.executemany(
            """
            UPDATE market_list_serving
            SET price_24h_ago = ?
            WHERE market_id = ?
            """,
            rows,
        )
    return len(rows)


def _fetch_oracle_status_flags(conn, market_ids: Optional[Sequence[int]] = None) -> Dict[int, Tuple[int, int, int]]:
    normalized_ids = sorted({int(market_id) for market_id in market_ids or [] if market_id})
    status_map: Dict[int, Tuple[int, int, int]] = {}

    def fetch_chunk(chunk: Optional[Sequence[int]]) -> None:
        filter_sql = ""
        params: Tuple[Any, ...] = ()
        if chunk is not None:
            placeholders = ", ".join("?" for _ in chunk)
            filter_sql = f"AND market_id IN ({placeholders})"
            params = tuple(chunk)
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT
                market_id,
                MAX(CASE WHEN event_status = 'settle' THEN 1 ELSE 0 END) AS has_settle,
                MAX(CASE WHEN event_status = 'propose' THEN 1 ELSE 0 END) AS has_propose,
                MAX(CASE WHEN event_status = 'dispute' THEN 1 ELSE 0 END) AS has_dispute
            FROM oracle_events
            WHERE market_id IS NOT NULL
              {filter_sql}
            GROUP BY market_id
            """,
            params,
        )
        for row in cursor.fetchall():
            payload = _row_to_dict(row)
            market_id = payload.get("market_id")
            if market_id is None:
                continue
            status_map[int(market_id)] = (
                int(payload.get("has_settle") or 0),
                int(payload.get("has_propose") or 0),
                int(payload.get("has_dispute") or 0),
            )

    if market_ids is None:
        fetch_chunk(None)
    else:
        for chunk in _chunked_values(normalized_ids):
            fetch_chunk(chunk)
    return status_map


def _fetch_latest_oracle_settlement_map(
    conn,
    market_ids: Optional[Sequence[int]] = None,
) -> Dict[int, SettlementResult]:
    normalized_ids = sorted({int(market_id) for market_id in market_ids or [] if market_id})
    settlement_map: Dict[int, SettlementResult] = {}

    def fetch_chunk(chunk: Optional[Sequence[int]]) -> None:
        filter_sql = ""
        params: Tuple[Any, ...] = ()
        if chunk is not None:
            placeholders = ", ".join("?" for _ in chunk)
            filter_sql = f"AND market_id IN ({placeholders})"
            params = tuple(chunk)
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT
                id,
                market_id,
                event_time,
                event_status,
                block_number,
                log_index,
                settlement_transaction,
                settled_price,
                payout
            FROM oracle_events
            WHERE event_status = 'settle'
              AND market_id IS NOT NULL
              {filter_sql}
            ORDER BY market_id ASC, block_number DESC, log_index DESC, id DESC
            """,
            params,
        )
        for row in cursor.fetchall():
            payload = _row_to_dict(row)
            market_id = payload.get("market_id")
            if market_id is None:
                continue
            market_id_int = int(market_id)
            if market_id_int in settlement_map:
                continue
            settlement_map[market_id_int] = parse_oracle_settlement_event(payload)

    if market_ids is None:
        fetch_chunk(None)
    else:
        for chunk in _chunked_values(normalized_ids):
            fetch_chunk(chunk)
    return settlement_map


def _fetch_fast_resolution_map(
    conn,
    market_ids: Optional[Sequence[int]] = None,
) -> Dict[int, SettlementResult]:
    if not table_exists(conn, "market_resolution_fast"):
        return {}
    normalized_ids = sorted({int(market_id) for market_id in market_ids or [] if market_id})
    settlement_map: Dict[int, SettlementResult] = {}

    def fetch_chunk(chunk: Optional[Sequence[int]]) -> None:
        filter_sql = ""
        params: Tuple[Any, ...] = ()
        if chunk is not None:
            placeholders = ", ".join("?" for _ in chunk)
            filter_sql = f"WHERE r.market_id IN ({placeholders})"
            params = tuple(chunk)
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT r.market_id, r.settlement_code, r.closed_time, r.updated_at
            FROM market_resolution_fast r
            JOIN markets m ON m.id = r.market_id
            {filter_sql}
            ORDER BY r.market_id ASC
            """,
            params,
        )
        for row in cursor.fetchall():
            payload = _row_to_dict(row)
            market_id = payload.get("market_id")
            if market_id is None:
                continue
            event_time = payload.get("closed_time") or payload.get("updated_at")
            settlement_map[int(market_id)] = parse_fast_settlement_code(
                payload.get("settlement_code"),
                closed_time=event_time,
            )

    if market_ids is None:
        fetch_chunk(None)
    else:
        for chunk in _chunked_values(normalized_ids):
            fetch_chunk(chunk)
    return settlement_map


def _fetch_fast_closed_time_map(
    conn,
    market_ids: Optional[Sequence[int]] = None,
) -> Dict[int, Any]:
    if not table_exists(conn, "market_resolution_fast"):
        return {}
    normalized_ids = sorted({int(market_id) for market_id in market_ids or [] if market_id})
    closed_map: Dict[int, Any] = {}

    def fetch_chunk(chunk: Optional[Sequence[int]]) -> None:
        filter_sql = ""
        params: Tuple[Any, ...] = ()
        if chunk is not None:
            placeholders = ", ".join("?" for _ in chunk)
            filter_sql = f"WHERE market_id IN ({placeholders})"
            params = tuple(chunk)
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT market_id, closed_time, updated_at
            FROM market_resolution_fast
            {filter_sql}
            """,
            params,
        )
        for row in cursor.fetchall():
            payload = _row_to_dict(row)
            market_id = payload.get("market_id")
            if market_id is None:
                continue
            closed_map[int(market_id)] = payload.get("closed_time") or payload.get("updated_at")

    if market_ids is None:
        fetch_chunk(None)
    else:
        for chunk in _chunked_values(normalized_ids):
            fetch_chunk(chunk)
    return closed_map


def _fetch_market_end_date_map(
    conn,
    market_ids: Optional[Sequence[int]] = None,
) -> Dict[int, Any]:
    normalized_ids = sorted({int(market_id) for market_id in market_ids or [] if market_id})
    end_date_map: Dict[int, Any] = {}

    def fetch_chunk(chunk: Optional[Sequence[int]]) -> None:
        filter_sql = ""
        params: Tuple[Any, ...] = ()
        if chunk is not None:
            placeholders = ", ".join("?" for _ in chunk)
            filter_sql = f"WHERE id IN ({placeholders})"
            params = tuple(chunk)
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT id, end_date
            FROM markets
            {filter_sql}
            """,
            params,
        )
        for row in cursor.fetchall():
            payload = _row_to_dict(row)
            market_id = payload.get("id")
            if market_id is not None:
                end_date_map[int(market_id)] = payload.get("end_date")

    if market_ids is None:
        fetch_chunk(None)
    else:
        for chunk in _chunked_values(normalized_ids):
            fetch_chunk(chunk)
    return end_date_map


def _market_end_date_is_past(end_date: Any, now: datetime) -> bool:
    parsed = _parse_trade_time(end_date)
    return bool(parsed and parsed <= now)


def _market_completion_tuple(
    *,
    flags: Tuple[int, int, int],
    settlement: SettlementResult,
    end_date: Any,
    gamma_closed_time: Any,
    now: datetime,
) -> Tuple[Any, ...]:
    has_settle, has_propose, has_dispute = flags
    code = int(settlement.settlement_code or 0)
    is_resolved = code in {1, 2, 3}
    is_final = bool(has_settle)
    gamma_closed = gamma_closed_time is not None
    end_date_past = _market_end_date_is_past(end_date, now)
    is_trading_closed = bool(is_final or gamma_closed or end_date_past or has_propose or has_dispute)

    completion_status = COMPLETION_OPEN
    completion_source = None
    completion_time = None
    if is_final:
        completion_source = settlement.settlement_source or "oracle_settle"
        completion_time = settlement.settlement_event_time
        if code == 3:
            completion_status = COMPLETION_CANCELLED
        elif is_resolved:
            completion_status = COMPLETION_SETTLED
        else:
            completion_status = COMPLETION_UNKNOWN
    elif has_dispute:
        completion_status = COMPLETION_DISPUTED
        completion_source = "oracle_dispute"
    elif has_propose:
        completion_status = COMPLETION_PROPOSED
        completion_source = "oracle_propose"
    elif gamma_closed:
        completion_source = settlement.settlement_source or "gamma_closed"
        completion_time = gamma_closed_time or settlement.settlement_event_time
        completion_status = COMPLETION_GAMMA_CLOSED_FALLBACK if is_resolved else COMPLETION_CLOSED_UNRESOLVED
    elif end_date_past:
        completion_status = COMPLETION_ENDED_AWAITING_ORACLE
        completion_source = "end_date"
        completion_time = end_date

    if completion_time is None and is_final:
        completion_time = settlement.settlement_event_time

    return (
        bool(is_trading_closed),
        bool(is_resolved),
        bool(is_final),
        completion_status,
        completion_source,
        completion_time,
        bool(gamma_closed),
        gamma_closed_time,
    )


def _market_status_snapshot_tuple(
    market_id: int,
    flags: Tuple[int, int, int],
    settlement: SettlementResult,
    end_date: Any,
    gamma_closed_time: Any,
    now: datetime,
) -> Tuple[Any, ...]:
    return (
        market_id,
        bool(flags[0]),
        bool(flags[1]),
        bool(flags[2]),
        int(settlement.settlement_code or 0),
        settlement.settlement_outcome or OUTCOME_UNKNOWN,
        settlement.settlement_source,
        settlement.settlement_raw,
        settlement.settlement_event_id,
        settlement.settlement_event_time,
        settlement.settlement_transaction,
        *_market_completion_tuple(
            flags=flags,
            settlement=settlement,
            end_date=end_date,
            gamma_closed_time=gamma_closed_time,
            now=now,
        ),
    )


def _build_market_status_snapshot_rows(conn, market_ids: Optional[Sequence[int]] = None) -> List[Tuple[Any, ...]]:
    normalized_ids = sorted({int(market_id) for market_id in market_ids or [] if market_id})
    scoped_ids: Optional[Sequence[int]] = normalized_ids if market_ids is not None else None
    flags_map = _fetch_oracle_status_flags(conn, scoped_ids)
    oracle_map = _fetch_latest_oracle_settlement_map(conn, scoped_ids)
    fast_map = _fetch_fast_resolution_map(conn, scoped_ids)
    fast_closed_time_map = _fetch_fast_closed_time_map(conn, scoped_ids)
    market_end_date_map = _fetch_market_end_date_map(conn, scoped_ids)
    if market_ids is None:
        output_ids = sorted(set(market_end_date_map) | set(flags_map) | set(oracle_map) | set(fast_map) | set(fast_closed_time_map))
    else:
        output_ids = normalized_ids
    rows: List[Tuple[Any, ...]] = []
    now = datetime.now(timezone.utc)
    for market_id in output_ids:
        settlement = choose_best_settlement(oracle_map.get(market_id), fast_map.get(market_id))
        rows.append(
            _market_status_snapshot_tuple(
                market_id,
                flags_map.get(market_id, (0, 0, 0)),
                settlement,
                market_end_date_map.get(market_id),
                fast_closed_time_map.get(market_id),
                now,
            )
        )
    return rows


def _upsert_market_status_snapshot(conn, market_ids: Sequence[int]) -> int:
    rows = _build_market_status_snapshot_rows(conn, market_ids)
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO market_status_snapshot (
            market_id,
            has_settle,
            has_propose,
            has_dispute,
            settlement_code,
            settlement_outcome,
            settlement_source,
            settlement_raw,
            settlement_event_id,
            settlement_event_time,
            settlement_transaction,
            is_trading_closed,
            is_resolved,
            is_final,
            completion_status,
            completion_source,
            completion_time,
            gamma_closed,
            gamma_closed_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_id) DO UPDATE SET
            has_settle = excluded.has_settle,
            has_propose = excluded.has_propose,
            has_dispute = excluded.has_dispute,
            settlement_code = excluded.settlement_code,
            settlement_outcome = excluded.settlement_outcome,
            settlement_source = excluded.settlement_source,
            settlement_raw = excluded.settlement_raw,
            settlement_event_id = excluded.settlement_event_id,
            settlement_event_time = excluded.settlement_event_time,
            settlement_transaction = excluded.settlement_transaction,
            is_trading_closed = excluded.is_trading_closed,
            is_resolved = excluded.is_resolved,
            is_final = excluded.is_final,
            completion_status = excluded.completion_status,
            completion_source = excluded.completion_source,
            completion_time = excluded.completion_time,
            gamma_closed = excluded.gamma_closed,
            gamma_closed_time = excluded.gamma_closed_time,
            updated_at = CURRENT_TIMESTAMP
        """,
        rows,
    )
    return len(rows)


def _full_refresh_market_status_snapshot(conn) -> int:
    rows = _build_market_status_snapshot_rows(conn)
    conn.execute("DELETE FROM market_status_snapshot")
    if rows:
        conn.executemany(
            """
            INSERT INTO market_status_snapshot (
                market_id,
                has_settle,
                has_propose,
                has_dispute,
                settlement_code,
                settlement_outcome,
                settlement_source,
                settlement_raw,
                settlement_event_id,
                settlement_event_time,
                settlement_transaction,
                is_trading_closed,
                is_resolved,
                is_final,
                completion_status,
                completion_source,
                completion_time,
                gamma_closed,
                gamma_closed_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    max_event_id = 0
    max_row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM oracle_events").fetchone()
    if max_row:
        max_payload = _row_to_dict(max_row)
        max_event_id = int(max_payload.get("max_id") or 0)
    _set_sync_state(
        conn,
        MARKET_STATUS_SNAPSHOT_SYNC_KEY,
        value=str(max_event_id),
        last_block=max_event_id,
    )
    return len(rows)


def _fetch_markets_closed_by_end_date(conn, limit: int = 50_000) -> Set[int]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT m.id
        FROM markets m
        LEFT JOIN market_status_snapshot mss ON mss.market_id = m.id
        WHERE m.end_date IS NOT NULL
          AND m.end_date <= ?
          AND COALESCE(mss.is_trading_closed, FALSE) = FALSE
        ORDER BY m.end_date ASC
        LIMIT ?
        """,
        (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), limit),
    )
    output: Set[int] = set()
    for row in cursor.fetchall():
        payload = _row_to_dict(row)
        market_id = payload.get("id")
        if market_id is not None:
            output.add(int(market_id))
    return output


def _sync_market_status_snapshot(conn, *, oracle_batch_size: int = DEFAULT_ORACLE_EVENT_BATCH_SIZE) -> Dict[str, int]:
    sync_row = _get_sync_state_row(conn, MARKET_STATUS_SNAPSHOT_SYNC_KEY)
    last_event_id = int(sync_row.get("last_block") or 0)
    if last_event_id <= 0:
        return {"mode": 1, "updated_markets": _full_refresh_market_status_snapshot(conn), "last_event_id": int(_get_sync_state_row(conn, MARKET_STATUS_SNAPSHOT_SYNC_KEY).get("last_block") or 0)}

    updated_market_ids: Set[int] = set()
    latest_seen_event_id = last_event_id
    while True:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, market_id
            FROM oracle_events
            WHERE id > ? AND market_id IS NOT NULL
            ORDER BY id ASC
            LIMIT ?
            """,
            (latest_seen_event_id, oracle_batch_size),
        )
        rows = cursor.fetchall()
        if not rows:
            break
        for row in rows:
            payload = row.as_dict() if hasattr(row, "as_dict") else dict(row)
            market_id = payload.get("market_id")
            if market_id is not None:
                updated_market_ids.add(int(market_id))
            latest_seen_event_id = max(latest_seen_event_id, int(payload.get("id") or latest_seen_event_id))
        if len(rows) < oracle_batch_size:
            break
    if updated_market_ids:
        _upsert_market_status_snapshot(conn, sorted(updated_market_ids))
    end_date_closed_ids = _fetch_markets_closed_by_end_date(conn)
    if end_date_closed_ids:
        _upsert_market_status_snapshot(conn, sorted(end_date_closed_ids))
        updated_market_ids.update(end_date_closed_ids)
    if latest_seen_event_id != last_event_id:
        _set_sync_state(
            conn,
            MARKET_STATUS_SNAPSHOT_SYNC_KEY,
            value=str(latest_seen_event_id),
            last_block=latest_seen_event_id,
        )
    return {"mode": 0, "updated_markets": len(updated_market_ids), "last_event_id": latest_seen_event_id}


def _upsert_market_list_serving(conn, market_ids: Sequence[int], stats_since: str) -> int:
    normalized_ids = sorted({int(market_id) for market_id in market_ids if market_id})
    if not normalized_ids:
        return 0
    threshold_dt = _threshold_datetime_24h()
    placeholders = ", ".join("?" for _ in normalized_ids)
    params: Tuple[Any, ...] = (stats_since, *normalized_ids, *normalized_ids)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT
            m.id AS market_id,
            mlp.latest_yes_price AS latest_price,
            mlp.latest_trade_at,
            stats_24h.trade_count_24h,
            stats_24h.volume_24h,
            stats_24h.last_trade_at
        FROM markets m
        LEFT JOIN market_latest_prices mlp ON mlp.market_id = m.id
        LEFT JOIN (
            SELECT
                market_id,
                SUM(trade_count) AS trade_count_24h,
                SUM(volume_notional) AS volume_24h,
                MAX(last_trade_at) AS last_trade_at
            FROM market_trade_daily_stats
            WHERE trade_date >= ? AND market_id IN ({placeholders})
            GROUP BY market_id
        ) stats_24h ON stats_24h.market_id = m.id
        WHERE m.id IN ({placeholders})
        """,
        params,
    )
    rows = []
    recent_market_ids: List[int] = []
    for row in cursor.fetchall():
        payload = row.as_dict() if hasattr(row, "as_dict") else dict(row)
        market_id = payload.get("market_id")
        if market_id is None:
            continue
        market_id_int = int(market_id)
        latest_trade_at = payload.get("latest_trade_at")
        latest_price = payload.get("latest_price")
        if latest_trade_at is not None and _is_at_or_before_threshold(latest_trade_at, threshold_dt):
            price_24h_ago = latest_price
        else:
            price_24h_ago = None
            recent_market_ids.append(market_id_int)
        rows.append(
            (
                market_id_int,
                latest_price,
                latest_trade_at,
                price_24h_ago,
                int(payload.get("trade_count_24h") or 0),
                _db_decimal(_parse_decimal(payload.get("volume_24h"))),
                payload.get("last_trade_at"),
            )
        )
    price_24h_map = _fetch_price_24h_ago_map(conn, recent_market_ids, threshold_dt)
    if price_24h_map:
        normalized_rows = []
        for market_id, latest_price, latest_trade_at, price_24h_ago, trade_count_24h, volume_24h, last_trade_at in rows:
            normalized_rows.append(
                (
                    market_id,
                    latest_price,
                    latest_trade_at,
                    price_24h_map.get(market_id, price_24h_ago),
                    trade_count_24h,
                    volume_24h,
                    last_trade_at,
                )
            )
        rows = normalized_rows
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO market_list_serving (
            market_id,
            latest_price,
            latest_trade_at,
            price_24h_ago,
            trade_count_24h,
            volume_24h,
            last_trade_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_id) DO UPDATE SET
            latest_price = excluded.latest_price,
            latest_trade_at = excluded.latest_trade_at,
            price_24h_ago = excluded.price_24h_ago,
            trade_count_24h = excluded.trade_count_24h,
            volume_24h = excluded.volume_24h,
            last_trade_at = excluded.last_trade_at
        """,
        rows,
    )
    return len(rows)


def _full_refresh_market_list_serving(conn, stats_since: str) -> int:
    threshold_dt = _threshold_datetime_24h()
    serving_threshold = _format_serving_threshold(conn, threshold_dt)
    conn.execute("DELETE FROM market_list_serving")
    conn.execute(
        """
        INSERT INTO market_list_serving (
            market_id,
            latest_price,
            latest_trade_at,
            price_24h_ago,
            trade_count_24h,
            volume_24h,
            last_trade_at
        )
        SELECT
            m.id AS market_id,
            mlp.latest_yes_price AS latest_price,
            mlp.latest_trade_at,
            CASE
                WHEN mlp.latest_trade_at IS NOT NULL AND mlp.latest_trade_at <= ? THEN mlp.latest_yes_price
                ELSE NULL
            END AS price_24h_ago,
            COALESCE(stats_24h.trade_count_24h, 0) AS trade_count_24h,
            COALESCE(stats_24h.volume_24h, 0) AS volume_24h,
            stats_24h.last_trade_at
        FROM markets m
        LEFT JOIN market_latest_prices mlp ON mlp.market_id = m.id
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
        """,
        (serving_threshold, stats_since),
    )
    recent_rows = conn.execute(
        """
        SELECT market_id
        FROM market_list_serving
        WHERE latest_trade_at IS NOT NULL AND latest_trade_at > ?
        ORDER BY COALESCE(volume_24h, 0) DESC, COALESCE(trade_count_24h, 0) DESC, last_trade_at DESC
        """,
        (serving_threshold,),
    ).fetchall()
    recent_market_ids = [
        int((row.as_dict() if hasattr(row, "as_dict") else dict(row)).get("market_id"))
        for row in recent_rows
        if (row.as_dict() if hasattr(row, "as_dict") else dict(row)).get("market_id") is not None
    ]
    for index in range(0, len(recent_market_ids), 1000):
        _refresh_market_list_price_24h_ago(conn, recent_market_ids[index:index + 1000], threshold_dt)
    count_row = conn.execute("SELECT COUNT(*) AS c FROM market_list_serving").fetchone()
    _set_sync_state(conn, MARKET_LIST_SERVING_DAY_SYNC_KEY, value=stats_since, last_block=None)
    return int((count_row.as_dict() if hasattr(count_row, "as_dict") else dict(count_row)).get("c") or 0) if count_row else 0


def _fetch_trade_batch(conn, last_trade_id: int, batch_size: int) -> List[Dict[str, Any]]:
    trade_read_source = sql_identifier(get_trade_read_source())
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT
            id,
            tx_hash,
            log_index,
            market_id,
            maker,
            taker,
            price,
            size,
            side,
            outcome,
            token_id,
            block_number,
            timestamp,
            fee,
            contract
        FROM {trade_read_source}
        WHERE id > ? AND market_id IS NOT NULL AND market_id > 0
        ORDER BY id ASC
        LIMIT ?
        """,
        (last_trade_id, batch_size),
    )
    rows = cursor.fetchall()
    return [row.as_dict() if hasattr(row, "as_dict") else dict(row) for row in rows]


def _build_trade_address_rows(
    trades: Sequence[Dict[str, Any]],
    *,
    include_address_stats: bool = True,
) -> Tuple[
    List[Tuple[Any, ...]],
    Dict[Tuple[str, int], Dict[str, Any]],
    Dict[str, Dict[str, Any]],
    Dict[Tuple[str, int], Dict[str, Any]],
    Dict[str, Dict[str, Any]],
    Dict[int, Dict[str, Optional[LatestTradePoint]]],
]:
    address_rows: List[Tuple[Any, ...]] = []
    address_daily: Dict[Tuple[str, str], Dict[str, Any]] = {}
    address_totals: Dict[str, Dict[str, Any]] = {}
    address_market: Dict[Tuple[str, int], Dict[str, Any]] = {}
    market_daily: Dict[Tuple[str, int], Dict[str, Any]] = {}
    market_latest: Dict[int, Dict[str, Optional[LatestTradePoint]]] = {}

    for row in trades:
        trade_id = _parse_int(row.get("id"))
        market_id = _parse_int(row.get("market_id"))
        if trade_id is None or market_id is None or market_id <= 0:
            continue

        price = _parse_decimal(row.get("price"))
        size = _parse_decimal(row.get("size"))
        notional = price * size
        fee_raw = _parse_decimal(row.get("fee"))
        fee_amount = fee_raw / FEE_DIVISOR if fee_raw else ZERO

        block_number = _parse_int(row.get("block_number"))
        log_index = _parse_int(row.get("log_index"))
        outcome = str(row.get("outcome") or "").upper() or None

        trade_dt = _parse_trade_time(row.get("timestamp"))
        trade_time = _format_mysql_datetime(trade_dt)
        trade_date = _trade_date_text(trade_dt)
        tx_hash = str(row.get("tx_hash") or "")
        token_id = str(uint256_storage_to_text(row.get("token_id")) or "")
        contract = row.get("contract")
        base_side = _normalize_side(row.get("side"))

        if trade_date:
            market_key = (trade_date, market_id)
            market_entry = market_daily.setdefault(
                market_key,
                {
                    "trade_date": trade_date,
                    "market_id": market_id,
                    "trade_count": 0,
                    "volume_notional": ZERO,
                    "last_trade_at": None,
                    "last_block_number": None,
                },
            )
            market_entry["trade_count"] += 1
            market_entry["volume_notional"] += notional
            if trade_time and (
                market_entry["last_trade_at"] is None or trade_time > market_entry["last_trade_at"]
            ):
                market_entry["last_trade_at"] = trade_time
            if block_number is not None and (
                market_entry["last_block_number"] is None or block_number > market_entry["last_block_number"]
            ):
                market_entry["last_block_number"] = block_number

        latest_entry = market_latest.setdefault(
            market_id,
            {"latest": None, "yes": None, "no": None},
        )
        latest_point = LatestTradePoint(
            trade_at=trade_time,
            block_number=block_number,
            log_index=log_index,
            price=price,
        )
        if _is_newer_trade(block_number, log_index, latest_entry["latest"]):
            latest_entry["latest"] = latest_point
        if outcome == "YES" and _is_newer_trade(block_number, log_index, latest_entry["yes"]):
            latest_entry["yes"] = latest_point
        if outcome == "NO" and _is_newer_trade(block_number, log_index, latest_entry["no"]):
            latest_entry["no"] = latest_point

        if not include_address_stats:
            continue

        participants: List[Tuple[str, str, str, Decimal]] = []
        maker = _normalize_address(row.get("maker"))
        taker = _normalize_address(row.get("taker"))
        if maker and maker not in EXCHANGE_ADDRESSES:
            participants.append((maker, "maker", base_side or "UNKNOWN", fee_amount))
        if taker and taker not in EXCHANGE_ADDRESSES and taker != maker:
            participants.append((taker, "taker", _opposite_side(base_side), ZERO))

        for address, role, side_for_address, address_fee in participants:
            address_rows.append(
                (
                    trade_id,
                    tx_hash,
                    log_index,
                    market_id,
                    token_id,
                    outcome,
                    address,
                    role,
                    side_for_address,
                    _db_decimal(price),
                    _db_decimal(size),
                    _db_decimal(notional),
                    _db_decimal(address_fee),
                    block_number,
                    trade_time,
                    trade_date,
                    contract,
                )
            )

            if trade_date:
                address_daily_key = (trade_date, address)
                address_daily_entry = address_daily.setdefault(
                    address_daily_key,
                    {
                        "trade_date": trade_date,
                        "address": address,
                        "trade_count": 0,
                        "buy_count": 0,
                        "sell_count": 0,
                        "volume_notional": ZERO,
                        "last_trade_at": None,
                    },
                )
                address_daily_entry["trade_count"] += 1
                address_daily_entry["buy_count"] += 1 if side_for_address == "BUY" else 0
                address_daily_entry["sell_count"] += 1 if side_for_address == "SELL" else 0
                address_daily_entry["volume_notional"] += notional
                if trade_time and (
                    address_daily_entry["last_trade_at"] is None or trade_time > address_daily_entry["last_trade_at"]
                ):
                    address_daily_entry["last_trade_at"] = trade_time

            address_total_entry = address_totals.setdefault(
                address,
                {
                    "address": address,
                    "total_trade_count": 0,
                    "total_buy_count": 0,
                    "total_sell_count": 0,
                    "total_volume_notional": ZERO,
                    "first_trade_at": None,
                    "last_trade_at": None,
                },
            )
            address_total_entry["total_trade_count"] += 1
            address_total_entry["total_buy_count"] += 1 if side_for_address == "BUY" else 0
            address_total_entry["total_sell_count"] += 1 if side_for_address == "SELL" else 0
            address_total_entry["total_volume_notional"] += notional
            if trade_time and (
                address_total_entry["first_trade_at"] is None or trade_time < address_total_entry["first_trade_at"]
            ):
                address_total_entry["first_trade_at"] = trade_time
            if trade_time and (
                address_total_entry["last_trade_at"] is None or trade_time > address_total_entry["last_trade_at"]
            ):
                address_total_entry["last_trade_at"] = trade_time

            address_market_key = (address, market_id)
            address_market_entry = address_market.setdefault(
                address_market_key,
                {
                    "address": address,
                    "market_id": market_id,
                    "trade_count": 0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "volume_notional": ZERO,
                    "first_trade_at": None,
                    "last_trade_at": None,
                },
            )
            address_market_entry["trade_count"] += 1
            address_market_entry["buy_count"] += 1 if side_for_address == "BUY" else 0
            address_market_entry["sell_count"] += 1 if side_for_address == "SELL" else 0
            address_market_entry["volume_notional"] += notional
            if trade_time and (
                address_market_entry["first_trade_at"] is None or trade_time < address_market_entry["first_trade_at"]
            ):
                address_market_entry["first_trade_at"] = trade_time
            if trade_time and (
                address_market_entry["last_trade_at"] is None or trade_time > address_market_entry["last_trade_at"]
            ):
                address_market_entry["last_trade_at"] = trade_time

    return (
        address_rows,
        market_daily,
        address_daily,
        address_totals,
        address_market,
        market_latest,
    )


def _insert_trade_addresses(conn, rows: Sequence[Tuple[Any, ...]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR IGNORE INTO trade_addresses (
            trade_id,
            tx_hash,
            log_index,
            market_id,
            token_id,
            outcome,
            address,
            role,
            side_for_address,
            price,
            size,
            notional,
            fee_amount,
            block_number,
            trade_time,
            trade_date,
            contract
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _upsert_market_daily(conn, entries: Iterable[Dict[str, Any]]) -> None:
    rows = [
        (
            entry["trade_date"],
            entry["market_id"],
            entry["trade_count"],
            _db_decimal(entry["volume_notional"]),
            entry["last_trade_at"],
            entry["last_block_number"],
        )
        for entry in entries
    ]
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO market_trade_daily_stats (
            trade_date,
            market_id,
            trade_count,
            volume_notional,
            last_trade_at,
            last_block_number
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_date, market_id) DO UPDATE SET
            trade_count = market_trade_daily_stats.trade_count + excluded.trade_count,
            volume_notional = market_trade_daily_stats.volume_notional + excluded.volume_notional,
            last_trade_at = CASE
                WHEN market_trade_daily_stats.last_trade_at IS NULL THEN excluded.last_trade_at
                WHEN excluded.last_trade_at IS NULL THEN market_trade_daily_stats.last_trade_at
                WHEN excluded.last_trade_at > market_trade_daily_stats.last_trade_at THEN excluded.last_trade_at
                ELSE last_trade_at
            END,
            last_block_number = CASE
                WHEN market_trade_daily_stats.last_block_number IS NULL THEN excluded.last_block_number
                WHEN excluded.last_block_number IS NULL THEN market_trade_daily_stats.last_block_number
                WHEN excluded.last_block_number > market_trade_daily_stats.last_block_number THEN excluded.last_block_number
                ELSE last_block_number
            END
        """,
        rows,
    )


def _upsert_address_daily(conn, entries: Iterable[Dict[str, Any]]) -> None:
    rows = [
        (
            entry["trade_date"],
            entry["address"],
            entry["trade_count"],
            entry["buy_count"],
            entry["sell_count"],
            _db_decimal(entry["volume_notional"]),
            entry["last_trade_at"],
        )
        for entry in entries
    ]
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO address_trade_daily_stats (
            trade_date,
            address,
            trade_count,
            buy_count,
            sell_count,
            volume_notional,
            last_trade_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_date, address) DO UPDATE SET
            trade_count = address_trade_daily_stats.trade_count + excluded.trade_count,
            buy_count = address_trade_daily_stats.buy_count + excluded.buy_count,
            sell_count = address_trade_daily_stats.sell_count + excluded.sell_count,
            volume_notional = address_trade_daily_stats.volume_notional + excluded.volume_notional,
            last_trade_at = CASE
                WHEN address_trade_daily_stats.last_trade_at IS NULL THEN excluded.last_trade_at
                WHEN excluded.last_trade_at IS NULL THEN address_trade_daily_stats.last_trade_at
                WHEN excluded.last_trade_at > address_trade_daily_stats.last_trade_at THEN excluded.last_trade_at
                ELSE last_trade_at
            END
        """,
        rows,
    )


def _upsert_address_totals(conn, entries: Iterable[Dict[str, Any]]) -> None:
    rows = [
        (
            entry["address"],
            entry["total_trade_count"],
            entry["total_buy_count"],
            entry["total_sell_count"],
            _db_decimal(entry["total_volume_notional"]),
            entry["first_trade_at"],
            entry["last_trade_at"],
        )
        for entry in entries
    ]
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO address_trade_totals (
            address,
            total_trade_count,
            total_buy_count,
            total_sell_count,
            total_volume_notional,
            first_trade_at,
            last_trade_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(address) DO UPDATE SET
            total_trade_count = address_trade_totals.total_trade_count + excluded.total_trade_count,
            total_buy_count = address_trade_totals.total_buy_count + excluded.total_buy_count,
            total_sell_count = address_trade_totals.total_sell_count + excluded.total_sell_count,
            total_volume_notional = address_trade_totals.total_volume_notional + excluded.total_volume_notional,
            first_trade_at = CASE
                WHEN address_trade_totals.first_trade_at IS NULL THEN excluded.first_trade_at
                WHEN excluded.first_trade_at IS NULL THEN address_trade_totals.first_trade_at
                WHEN excluded.first_trade_at < address_trade_totals.first_trade_at THEN excluded.first_trade_at
                ELSE first_trade_at
            END,
            last_trade_at = CASE
                WHEN address_trade_totals.last_trade_at IS NULL THEN excluded.last_trade_at
                WHEN excluded.last_trade_at IS NULL THEN address_trade_totals.last_trade_at
                WHEN excluded.last_trade_at > address_trade_totals.last_trade_at THEN excluded.last_trade_at
                ELSE last_trade_at
            END
        """,
        rows,
    )


def _upsert_address_market(conn, entries: Iterable[Dict[str, Any]]) -> None:
    rows = [
        (
            entry["address"],
            entry["market_id"],
            entry["trade_count"],
            entry["buy_count"],
            entry["sell_count"],
            _db_decimal(entry["volume_notional"]),
            entry["first_trade_at"],
            entry["last_trade_at"],
        )
        for entry in entries
    ]
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO address_market_stats (
            address,
            market_id,
            trade_count,
            buy_count,
            sell_count,
            volume_notional,
            first_trade_at,
            last_trade_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(address, market_id) DO UPDATE SET
            trade_count = address_market_stats.trade_count + excluded.trade_count,
            buy_count = address_market_stats.buy_count + excluded.buy_count,
            sell_count = address_market_stats.sell_count + excluded.sell_count,
            volume_notional = address_market_stats.volume_notional + excluded.volume_notional,
            first_trade_at = CASE
                WHEN address_market_stats.first_trade_at IS NULL THEN excluded.first_trade_at
                WHEN excluded.first_trade_at IS NULL THEN address_market_stats.first_trade_at
                WHEN excluded.first_trade_at < address_market_stats.first_trade_at THEN excluded.first_trade_at
                ELSE first_trade_at
            END,
            last_trade_at = CASE
                WHEN address_market_stats.last_trade_at IS NULL THEN excluded.last_trade_at
                WHEN excluded.last_trade_at IS NULL THEN address_market_stats.last_trade_at
                WHEN excluded.last_trade_at > address_market_stats.last_trade_at THEN excluded.last_trade_at
                ELSE last_trade_at
            END
        """,
        rows,
    )


def _upsert_market_latest(conn, market_latest: Dict[int, Dict[str, Optional[LatestTradePoint]]]) -> None:
    rows = []
    for market_id, entry in market_latest.items():
        latest = entry.get("latest")
        latest_yes = entry.get("yes")
        latest_no = entry.get("no")
        rows.append(
            (
                market_id,
                latest.trade_at if latest else None,
                latest.block_number if latest else None,
                latest.log_index if latest else None,
                _db_decimal(latest.price) if latest and latest.price is not None else None,
                latest_yes.trade_at if latest_yes else None,
                latest_yes.block_number if latest_yes else None,
                latest_yes.log_index if latest_yes else None,
                _db_decimal(latest_yes.price) if latest_yes and latest_yes.price is not None else None,
                latest_no.trade_at if latest_no else None,
                latest_no.block_number if latest_no else None,
                latest_no.log_index if latest_no else None,
                _db_decimal(latest_no.price) if latest_no and latest_no.price is not None else None,
            )
        )
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO market_latest_prices (
            market_id,
            latest_trade_at,
            latest_trade_block,
            latest_trade_log_index,
            latest_price,
            latest_yes_trade_at,
            latest_yes_trade_block,
            latest_yes_trade_log_index,
            latest_yes_price,
            latest_no_trade_at,
            latest_no_trade_block,
            latest_no_trade_log_index,
            latest_no_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_id) DO UPDATE SET
            latest_trade_at = CASE
                WHEN COALESCE(excluded.latest_trade_block, -1) > COALESCE(market_latest_prices.latest_trade_block, -1) THEN excluded.latest_trade_at
                WHEN COALESCE(excluded.latest_trade_block, -1) = COALESCE(market_latest_prices.latest_trade_block, -1)
                     AND COALESCE(excluded.latest_trade_log_index, -1) > COALESCE(market_latest_prices.latest_trade_log_index, -1)
                THEN excluded.latest_trade_at
                ELSE latest_trade_at
            END,
            latest_trade_block = CASE
                WHEN COALESCE(excluded.latest_trade_block, -1) > COALESCE(market_latest_prices.latest_trade_block, -1) THEN excluded.latest_trade_block
                WHEN COALESCE(excluded.latest_trade_block, -1) = COALESCE(market_latest_prices.latest_trade_block, -1)
                     AND COALESCE(excluded.latest_trade_log_index, -1) > COALESCE(market_latest_prices.latest_trade_log_index, -1)
                THEN excluded.latest_trade_block
                ELSE latest_trade_block
            END,
            latest_trade_log_index = CASE
                WHEN COALESCE(excluded.latest_trade_block, -1) > COALESCE(market_latest_prices.latest_trade_block, -1) THEN excluded.latest_trade_log_index
                WHEN COALESCE(excluded.latest_trade_block, -1) = COALESCE(market_latest_prices.latest_trade_block, -1)
                     AND COALESCE(excluded.latest_trade_log_index, -1) > COALESCE(market_latest_prices.latest_trade_log_index, -1)
                THEN excluded.latest_trade_log_index
                ELSE latest_trade_log_index
            END,
            latest_price = CASE
                WHEN COALESCE(excluded.latest_trade_block, -1) > COALESCE(market_latest_prices.latest_trade_block, -1) THEN excluded.latest_price
                WHEN COALESCE(excluded.latest_trade_block, -1) = COALESCE(market_latest_prices.latest_trade_block, -1)
                     AND COALESCE(excluded.latest_trade_log_index, -1) > COALESCE(market_latest_prices.latest_trade_log_index, -1)
                THEN excluded.latest_price
                ELSE latest_price
            END,
            latest_yes_trade_at = CASE
                WHEN COALESCE(excluded.latest_yes_trade_block, -1) > COALESCE(market_latest_prices.latest_yes_trade_block, -1) THEN excluded.latest_yes_trade_at
                WHEN COALESCE(excluded.latest_yes_trade_block, -1) = COALESCE(market_latest_prices.latest_yes_trade_block, -1)
                     AND COALESCE(excluded.latest_yes_trade_log_index, -1) > COALESCE(market_latest_prices.latest_yes_trade_log_index, -1)
                THEN excluded.latest_yes_trade_at
                ELSE latest_yes_trade_at
            END,
            latest_yes_trade_block = CASE
                WHEN COALESCE(excluded.latest_yes_trade_block, -1) > COALESCE(market_latest_prices.latest_yes_trade_block, -1) THEN excluded.latest_yes_trade_block
                WHEN COALESCE(excluded.latest_yes_trade_block, -1) = COALESCE(market_latest_prices.latest_yes_trade_block, -1)
                     AND COALESCE(excluded.latest_yes_trade_log_index, -1) > COALESCE(market_latest_prices.latest_yes_trade_log_index, -1)
                THEN excluded.latest_yes_trade_block
                ELSE latest_yes_trade_block
            END,
            latest_yes_trade_log_index = CASE
                WHEN COALESCE(excluded.latest_yes_trade_block, -1) > COALESCE(market_latest_prices.latest_yes_trade_block, -1) THEN excluded.latest_yes_trade_log_index
                WHEN COALESCE(excluded.latest_yes_trade_block, -1) = COALESCE(market_latest_prices.latest_yes_trade_block, -1)
                     AND COALESCE(excluded.latest_yes_trade_log_index, -1) > COALESCE(market_latest_prices.latest_yes_trade_log_index, -1)
                THEN excluded.latest_yes_trade_log_index
                ELSE latest_yes_trade_log_index
            END,
            latest_yes_price = CASE
                WHEN COALESCE(excluded.latest_yes_trade_block, -1) > COALESCE(market_latest_prices.latest_yes_trade_block, -1) THEN excluded.latest_yes_price
                WHEN COALESCE(excluded.latest_yes_trade_block, -1) = COALESCE(market_latest_prices.latest_yes_trade_block, -1)
                     AND COALESCE(excluded.latest_yes_trade_log_index, -1) > COALESCE(market_latest_prices.latest_yes_trade_log_index, -1)
                THEN excluded.latest_yes_price
                ELSE latest_yes_price
            END,
            latest_no_trade_at = CASE
                WHEN COALESCE(excluded.latest_no_trade_block, -1) > COALESCE(market_latest_prices.latest_no_trade_block, -1) THEN excluded.latest_no_trade_at
                WHEN COALESCE(excluded.latest_no_trade_block, -1) = COALESCE(market_latest_prices.latest_no_trade_block, -1)
                     AND COALESCE(excluded.latest_no_trade_log_index, -1) > COALESCE(market_latest_prices.latest_no_trade_log_index, -1)
                THEN excluded.latest_no_trade_at
                ELSE latest_no_trade_at
            END,
            latest_no_trade_block = CASE
                WHEN COALESCE(excluded.latest_no_trade_block, -1) > COALESCE(market_latest_prices.latest_no_trade_block, -1) THEN excluded.latest_no_trade_block
                WHEN COALESCE(excluded.latest_no_trade_block, -1) = COALESCE(market_latest_prices.latest_no_trade_block, -1)
                     AND COALESCE(excluded.latest_no_trade_log_index, -1) > COALESCE(market_latest_prices.latest_no_trade_log_index, -1)
                THEN excluded.latest_no_trade_block
                ELSE latest_no_trade_block
            END,
            latest_no_trade_log_index = CASE
                WHEN COALESCE(excluded.latest_no_trade_block, -1) > COALESCE(market_latest_prices.latest_no_trade_block, -1) THEN excluded.latest_no_trade_log_index
                WHEN COALESCE(excluded.latest_no_trade_block, -1) = COALESCE(market_latest_prices.latest_no_trade_block, -1)
                     AND COALESCE(excluded.latest_no_trade_log_index, -1) > COALESCE(market_latest_prices.latest_no_trade_log_index, -1)
                THEN excluded.latest_no_trade_log_index
                ELSE latest_no_trade_log_index
            END,
            latest_no_price = CASE
                WHEN COALESCE(excluded.latest_no_trade_block, -1) > COALESCE(market_latest_prices.latest_no_trade_block, -1) THEN excluded.latest_no_price
                WHEN COALESCE(excluded.latest_no_trade_block, -1) = COALESCE(market_latest_prices.latest_no_trade_block, -1)
                     AND COALESCE(excluded.latest_no_trade_log_index, -1) > COALESCE(market_latest_prices.latest_no_trade_log_index, -1)
                THEN excluded.latest_no_price
                ELSE latest_no_price
            END
        """,
        rows,
    )


def sync_trade_analytics(
    db_path: str = DEFAULT_DB_PATH,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: Optional[int] = None,
    sync_state_key: str = TRADE_ANALYTICS_SYNC_KEY,
    include_trade_addresses: bool = False,
    include_address_stats: bool = True,
    verbose: bool = True,
) -> Dict[str, int]:
    init_schema(db_path=db_path)

    conn = get_connection(db_path)
    batches = 0
    processed_trades = 0
    last_trade_id = get_last_processed_trade_id(db_path=db_path, sync_state_key=sync_state_key)
    latest_seen_trade_id = last_trade_id
    stats_since = _current_stats_since_date()
    serving_sync_row = _get_sync_state_row(conn, MARKET_LIST_SERVING_DAY_SYNC_KEY)

    try:
        status_sync_result = _sync_market_status_snapshot(conn)
        serving_refreshed = 0
        if max_batches != 0 and serving_sync_row.get("value") != stats_since:
            serving_refreshed = _full_refresh_market_list_serving(conn, stats_since)
        conn.commit()

        while True:
            if max_batches is not None and batches >= max_batches:
                break

            trades = _fetch_trade_batch(conn, latest_seen_trade_id, batch_size)
            if not trades:
                break

            (
                address_rows,
                market_daily,
                address_daily,
                address_totals,
                address_market,
                market_latest,
            ) = _build_trade_address_rows(trades, include_address_stats=include_address_stats or include_trade_addresses)
            impacted_market_ids = set(market_daily.keys())
            impacted_market_ids = {market_id for (_trade_date, market_id) in impacted_market_ids}
            impacted_market_ids.update(int(market_id) for market_id in market_latest.keys())

            if include_trade_addresses:
                _insert_trade_addresses(conn, address_rows)
            _upsert_market_daily(conn, market_daily.values())
            if include_address_stats:
                _upsert_address_daily(conn, address_daily.values())
                _upsert_address_totals(conn, address_totals.values())
                _upsert_address_market(conn, address_market.values())
            _upsert_market_latest(conn, market_latest)
            if impacted_market_ids:
                _upsert_market_list_serving(conn, sorted(impacted_market_ids), stats_since)

            latest_seen_trade_id = max(_parse_int(row.get("id")) or latest_seen_trade_id for row in trades)
            _update_sync_state(conn, latest_seen_trade_id, sync_state_key)
            conn.commit()

            processed_trades += len(trades)
            batches += 1
            if verbose:
                print(
                    f"[trade-analytics] batch={batches} processed={processed_trades} last_trade_id={latest_seen_trade_id}",
                    file=sys.stderr,
                )

        return {
            "batches": batches,
            "processed_trades": processed_trades,
            "last_trade_id": latest_seen_trade_id,
            "status_updated_markets": int(status_sync_result.get("updated_markets") or 0),
            "serving_refreshed_rows": serving_refreshed,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync trade analytics/materialized tables from trades")
    add_db_cli_args(parser)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="每批处理多少条 trades")
    parser.add_argument("--max-batches", type=int, default=None, help="最多处理多少批；默认直到追平")
    parser.add_argument("--sync-state-key", default=TRADE_ANALYTICS_SYNC_KEY, help="sync_state 中记录处理进度的 key")
    parser.add_argument(
        "--market-only",
        action="store_true",
        help="只写 market_trade_daily_stats、market_latest_prices 和 sync_state；跳过 address_* 与 trade_addresses",
    )
    parser.add_argument("--with-trade-addresses", action="store_true", help="额外落全量 trade_addresses 明细表；更占磁盘")
    parser.add_argument("--watch", action="store_true", help="持续监控并按间隔重复增量同步")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_WATCH_INTERVAL_SECONDS,
        help=f"--watch 模式下两次同步之间的间隔秒数（默认 {DEFAULT_WATCH_INTERVAL_SECONDS}）",
    )

    args = parser.parse_args()
    configure_db_from_args(args)
    db_path = args.sqlite_path
    if args.market_only and args.with_trade_addresses:
        parser.error("--market-only 不能和 --with-trade-addresses 同时使用")

    print(f"[trade-analytics] target={describe_db_target()}", file=sys.stderr)
    if args.market_only:
        print("[trade-analytics] mode=market-only address_stats=off trade_addresses=off", file=sys.stderr)
    interval_seconds = max(1, int(args.interval))

    try:
        while True:
            started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            print(f"[trade-analytics] cycle-start started_at={started_at}", file=sys.stderr)
            result = sync_trade_analytics(
                db_path=db_path,
                batch_size=args.batch_size,
                max_batches=args.max_batches,
                sync_state_key=args.sync_state_key,
                include_trade_addresses=args.with_trade_addresses,
                include_address_stats=not args.market_only,
                verbose=True,
            )
            print(
                f"[trade-analytics] cycle-done batches={result['batches']} processed={result['processed_trades']} last_trade_id={result['last_trade_id']}",
                file=sys.stderr,
            )

            if not args.watch:
                break

            print(
                f"[trade-analytics] sleeping interval_seconds={interval_seconds}",
                file=sys.stderr,
            )
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("[trade-analytics] interrupted, exiting", file=sys.stderr)


if __name__ == "__main__":
    main()
