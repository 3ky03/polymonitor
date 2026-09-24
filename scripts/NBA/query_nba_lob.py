#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DuckDB query interface for the NBA-only parquet LOB dataset."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from glob import glob
from typing import Any, Dict, List, Optional, Sequence, Tuple

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from NBA.common import DEFAULT_DATA_ROOT, PRICE_SCALE, SIZE_SCALE, level_glob, market_catalog_path, snapshot_glob, token_catalog_path


def _import_duckdb():
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("duckdb is required for query-nba-lob. Please install duckdb.") from exc
    return duckdb


def _sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _fetch_rows(conn, sql: str) -> List[Dict[str, Any]]:
    cursor = conn.execute(sql)
    columns = [item[0] for item in cursor.description]
    rows = cursor.fetchall()
    return [{columns[idx]: row[idx] for idx in range(len(columns))} for row in rows]


def _catalog_exists(data_root: Path | str) -> bool:
    return market_catalog_path(data_root).exists()


def _snapshot_files_exist(data_root: Path | str) -> bool:
    return bool(glob(snapshot_glob(data_root)))


def _level_files_exist(data_root: Path | str) -> bool:
    return bool(glob(level_glob(data_root)))


def _snapshot_reader_sql(data_root: Path | str) -> str:
    return f"read_parquet({_sql_string(snapshot_glob(data_root))}, hive_partitioning=1, union_by_name=1)"


def _level_reader_sql(data_root: Path | str) -> str:
    return f"read_parquet({_sql_string(level_glob(data_root))}, hive_partitioning=1, union_by_name=1)"


def _price_sql(expr: str) -> str:
    return f"CASE WHEN {expr} IS NULL THEN NULL ELSE {expr} / {float(PRICE_SCALE)} END"


def _size_sql(expr: str) -> str:
    return f"CASE WHEN {expr} IS NULL THEN NULL ELSE {expr} / {float(SIZE_SCALE)} END"


def _build_market_filters(args: argparse.Namespace, alias: str = "m") -> List[str]:
    filters: List[str] = []
    if args.market_id is not None:
        filters.append(f"{alias}.market_id = {int(args.market_id)}")
    if args.condition_id:
        filters.append(f"{alias}.condition_id = {_sql_string(str(args.condition_id))}")
    if args.slug:
        filters.append(f"{alias}.slug = {_sql_string(str(args.slug))}")
    if args.token_id:
        filters.append(
            f"({alias}.yes_token_id = {_sql_string(str(args.token_id))} OR "
            f"{alias}.no_token_id = {_sql_string(str(args.token_id))} OR "
            f"EXISTS (SELECT 1 WHERE {alias}.clob_token_ids LIKE '%{str(args.token_id).replace('%', '%%')}%'))"
        )
    if args.title_contains:
        filters.append(f"LOWER({alias}.title) LIKE LOWER({_sql_string('%' + str(args.title_contains) + '%')})")
    return filters


def _build_time_filters(args: argparse.Namespace, alias: str = "s") -> List[str]:
    filters: List[str] = []
    if args.start_time:
        filters.append(f"{alias}.event_timestamp_ms >= {int(_parse_as_of_ms(args.start_time))}")
    if args.end_time:
        filters.append(f"{alias}.event_timestamp_ms <= {int(_parse_as_of_ms(args.end_time))}")
    if args.event_type:
        filters.append(f"{alias}.event_type = {_sql_string(str(args.event_type))}")
    return filters


def _parse_as_of_ms(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def query_markets(conn, args: argparse.Namespace) -> List[Dict[str, Any]]:
    if not _catalog_exists(args.data_root):
        return []
    filters = _build_market_filters(args, alias="m")
    if args.active_only:
        filters.append("m.active = 1")
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    limit_sql = f"LIMIT {int(args.limit)}" if args.limit and args.limit > 0 else ""
    sql = f"""
    SELECT
        m.market_id,
        m.condition_id,
        m.slug,
        m.title,
        m.yes_token_id,
        m.no_token_id,
        m.enable_neg_risk,
        m.active,
        m.end_date,
        m.discovered_at,
        m.last_seen_at
    FROM read_parquet({_sql_string(str(market_catalog_path(args.data_root)))}) AS m
    {where_sql}
    ORDER BY m.active DESC, m.market_id ASC
    {limit_sql}
    """
    return _fetch_rows(conn, sql)


def query_latest_bbo(conn, args: argparse.Namespace) -> List[Dict[str, Any]]:
    if not _catalog_exists(args.data_root) or not _snapshot_files_exist(args.data_root):
        return []
    filters = _build_market_filters(args, alias="m")
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    limit_sql = f"LIMIT {int(args.limit)}" if args.limit and args.limit > 0 else ""
    sql = f"""
    WITH matched_markets AS (
        SELECT m.market_id, m.condition_id, m.slug, m.title
        FROM read_parquet({_sql_string(str(market_catalog_path(args.data_root)))}) AS m
        {where_sql}
    ),
    matched_tokens AS (
        SELECT t.market_id, t.token_id, t.outcome, t.outcome_index
        FROM read_parquet({_sql_string(str(token_catalog_path(args.data_root)))}) AS t
        JOIN matched_markets mm ON mm.market_id = t.market_id
        WHERE t.active = 1
    ),
    ranked AS (
        SELECT
            s.market_id,
            mm.condition_id,
            mm.slug,
            mm.title,
            s.token_id,
            mt.outcome,
            {_price_sql("s.best_bid_ppm")} AS best_bid,
            {_price_sql("s.best_ask_ppm")} AS best_ask,
            s.event_type,
            s.event_timestamp_ms,
            mt.outcome_index,
            ROW_NUMBER() OVER (
                PARTITION BY s.market_id, s.token_id
                ORDER BY s.event_timestamp_ms DESC
            ) AS rn
        FROM {_snapshot_reader_sql(args.data_root)} AS s
        JOIN matched_markets mm ON mm.market_id = s.market_id
        JOIN matched_tokens mt ON mt.market_id = s.market_id AND mt.token_id = s.token_id
        WHERE s.event_type = 'book'
    )
    SELECT market_id, condition_id, slug, title, token_id, outcome, best_bid, best_ask, event_type, event_timestamp_ms
    FROM ranked
    WHERE rn = 1
    ORDER BY market_id ASC, outcome_index ASC
    {limit_sql}
    """
    return _fetch_rows(conn, sql)


def query_snapshots(conn, args: argparse.Namespace) -> List[Dict[str, Any]]:
    if not _catalog_exists(args.data_root) or not _snapshot_files_exist(args.data_root):
        return []
    market_filters = _build_market_filters(args, alias="m")
    time_filters = _build_time_filters(args, alias="s")
    where_market = f"WHERE {' AND '.join(market_filters)}" if market_filters else ""
    where_snapshot = f"AND {' AND '.join(time_filters)}" if time_filters else ""
    limit_sql = f"LIMIT {int(args.limit)}" if args.limit and args.limit > 0 else ""
    sql = f"""
    WITH matched_markets AS (
        SELECT m.market_id, m.condition_id, m.slug, m.title
        FROM read_parquet({_sql_string(str(market_catalog_path(args.data_root)))}) AS m
        {where_market}
    ),
    matched_tokens AS (
        SELECT t.market_id, t.token_id, t.outcome, t.outcome_index
        FROM read_parquet({_sql_string(str(token_catalog_path(args.data_root)))}) AS t
        JOIN matched_markets mm ON mm.market_id = t.market_id
    )
    SELECT
        s.market_id,
        mm.condition_id,
        mm.slug,
        mm.title,
        s.token_id,
        mt.outcome,
        s.event_type,
        s.event_timestamp_ms,
        {_price_sql("s.best_bid_ppm")} AS best_bid,
        {_price_sql("s.best_ask_ppm")} AS best_ask,
        {_price_sql("s.last_trade_price_ppm")} AS last_trade_price,
        {_price_sql("s.price_ppm")} AS price,
        {_size_sql("s.size_micros")} AS size,
        s.side,
        s.dedupe_key
    FROM {_snapshot_reader_sql(args.data_root)} AS s
    JOIN matched_markets mm ON mm.market_id = s.market_id
    LEFT JOIN matched_tokens mt ON mt.market_id = s.market_id AND mt.token_id = s.token_id
    WHERE 1 = 1
    {where_snapshot}
    ORDER BY s.event_timestamp_ms DESC
    {limit_sql}
    """
    return _fetch_rows(conn, sql)


def query_depth(conn, args: argparse.Namespace) -> List[Dict[str, Any]]:
    if not _catalog_exists(args.data_root) or not _snapshot_files_exist(args.data_root) or not _level_files_exist(args.data_root):
        return []
    market_filters = _build_market_filters(args, alias="m")
    where_market = f"WHERE {' AND '.join(market_filters)}" if market_filters else ""
    as_of_ms = _parse_as_of_ms(args.as_of_time)
    if as_of_ms is None:
        order_clause = "ORDER BY s.event_timestamp_ms DESC"
    else:
        order_clause = f"ORDER BY ABS(s.event_timestamp_ms - {as_of_ms}) ASC, s.event_timestamp_ms DESC"
    sql = f"""
    WITH matched_markets AS (
        SELECT m.market_id, m.condition_id, m.slug, m.title
        FROM read_parquet({_sql_string(str(market_catalog_path(args.data_root)))}) AS m
        {where_market}
    ),
    matched_tokens AS (
        SELECT t.market_id, t.token_id, t.outcome, t.outcome_index
        FROM read_parquet({_sql_string(str(token_catalog_path(args.data_root)))}) AS t
        JOIN matched_markets mm ON mm.market_id = t.market_id
        WHERE t.active = 1
    ),
    target_books AS (
        SELECT
            s.dedupe_key,
            s.market_id,
            s.token_id,
            s.event_timestamp_ms,
            mt.outcome,
            mt.outcome_index,
            ROW_NUMBER() OVER (
                PARTITION BY s.market_id, s.token_id
                {order_clause}
            ) AS rn
        FROM {_snapshot_reader_sql(args.data_root)} AS s
        JOIN matched_markets mm ON mm.market_id = s.market_id
        JOIN matched_tokens mt ON mt.market_id = s.market_id AND mt.token_id = s.token_id
        WHERE s.event_type = 'book'
    )
    SELECT
        tb.market_id,
        mm.condition_id,
        mm.slug,
        mm.title,
        tb.token_id,
        tb.outcome,
        tb.event_timestamp_ms,
        l.side,
        l.level_index,
        {_price_sql("l.price_ppm")} AS price,
        {_size_sql("l.size_micros")} AS size
    FROM {_level_reader_sql(args.data_root)} AS l
    JOIN target_books tb ON tb.dedupe_key = l.dedupe_key
    JOIN matched_markets mm ON mm.market_id = tb.market_id
    WHERE tb.rn = 1
    ORDER BY tb.market_id ASC, tb.outcome_index ASC, l.side ASC, l.level_index ASC
    """
    return _fetch_rows(conn, sql)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query the NBA-only parquet LOB dataset with DuckDB")
    sub = parser.add_subparsers(dest="command", required=True)

    query_cmd = sub.add_parser("query-nba-lob", help="Query NBA LOB parquet data")
    query_cmd.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    query_cmd.add_argument("--mode", choices=["markets", "latest-bbo", "snapshots", "depth"], default="latest-bbo")
    query_cmd.add_argument("--market-id", type=int)
    query_cmd.add_argument("--condition-id")
    query_cmd.add_argument("--slug")
    query_cmd.add_argument("--token-id")
    query_cmd.add_argument("--title-contains")
    query_cmd.add_argument("--event-type")
    query_cmd.add_argument("--start-time")
    query_cmd.add_argument("--end-time")
    query_cmd.add_argument("--as-of-time")
    query_cmd.add_argument("--active-only", action="store_true")
    query_cmd.add_argument("--limit", type=int, default=50)
    return parser


def command_query(args: argparse.Namespace) -> int:
    duckdb = _import_duckdb()
    conn = duckdb.connect()
    try:
        if args.mode == "markets":
            rows = query_markets(conn, args)
        elif args.mode == "latest-bbo":
            rows = query_latest_bbo(conn, args)
        elif args.mode == "snapshots":
            rows = query_snapshots(conn, args)
        elif args.mode == "depth":
            rows = query_depth(conn, args)
        else:
            raise RuntimeError(f"Unsupported query mode: {args.mode}")
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    finally:
        conn.close()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "query-nba-lob":
        return command_query(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
