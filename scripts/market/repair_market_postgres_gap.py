#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repair PostgreSQL market gaps after the MySQL -> PostgreSQL cutover.

Two repair paths are intentionally separate:

1. mysql-gap copies markets that still exist in the legacy MySQL database but
   are missing from PostgreSQL.
2. gamma-window backfills a date window from Gamma /markets/keyset. This is for
   windows where MySQL itself has no rows, so MySQL cannot be the source.

Neither path advances last_market_discovery_at. The live sync waterline should
only be reset after verification passes.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    import requests
except ImportError:  # pragma: no cover - handled at runtime.
    requests = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from migrate_mysql_to_postgres import (  # noqa: E402
    DEFAULT_BAD_ROWS_DIR,
    DEFAULT_MYSQL_CHARSET,
    DEFAULT_MYSQL_DATABASE,
    DEFAULT_MYSQL_HOST,
    DEFAULT_MYSQL_PASSWORD,
    DEFAULT_MYSQL_PORT,
    DEFAULT_MYSQL_USER,
    DEFAULT_PG_DATABASE,
    DEFAULT_PG_HOST,
    DEFAULT_PG_PASSWORD,
    DEFAULT_PG_PORT,
    DEFAULT_PG_USER,
    BadRowLogger,
    TABLE_SPECS,
    TableSpec,
    create_schema,
    mysql_connect,
    pg_connect,
    quote_ident,
    quote_table,
    require_drivers,
    write_rows,
)
from db import (  # noqa: E402
    configure_runtime_db,
    get_connection,
    init_schema,
)
from market.market_discovery import (  # noqa: E402
    GAMMA_MARKETS_KEYSET_URL,
    REQUEST_TIMEOUT,
    _attach_embedded_event_meta_to_market,
    _get_market_created_at,
    batch_upsert_markets,
    normalize_market_from_gamma,
    upsert_market_tokens_for_conditions,
)


MARKET_COPY_TABLES = ("markets", "market_tokens", "market_resolution_fast")
MARKET_CHILD_TABLES = (
    "core.market_tokens",
    "core.market_resolution_fast",
    "core.market_status_snapshot",
    "core.market_latest_prices",
    "core.market_list_serving",
    "core.market_trade_daily_stats",
    "oracle.oracle_events",
)


def _utc_dt(text: str) -> datetime:
    raw = str(text or "").strip()
    if not raw:
        raise argparse.ArgumentTypeError("date/time cannot be empty")
    if len(raw) == 10:
        raw = raw + "T00:00:00+00:00"
    elif raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _mysql_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _read_password_command(command: Optional[str]) -> str:
    if not command:
        return ""
    return subprocess.check_output(command, shell=True, text=True).strip()


def _apply_password_commands(args: argparse.Namespace) -> None:
    if getattr(args, "mysql_password_command", None):
        args.mysql_password = _read_password_command(args.mysql_password_command)


def _market_where_sql(since_date: datetime, until_date: Optional[datetime], last_id: int = 0) -> Tuple[str, List[Any]]:
    where = ["`id` > %s", "`created_at` >= %s"]
    params: List[Any] = [int(last_id), _mysql_dt(since_date)]
    if until_date is not None:
        where.append("`created_at` < %s")
        params.append(_mysql_dt(until_date))
    return " AND ".join(where), params


def _fetch_mysql_market_batch(
    mysql_conn,
    *,
    since_date: datetime,
    until_date: Optional[datetime],
    last_id: int,
    batch_size: int,
) -> List[Mapping[str, Any]]:
    spec = TABLE_SPECS["markets"]
    columns = ", ".join(f"`{col}`" for col in spec.source_columns)
    where_sql, params = _market_where_sql(since_date, until_date, last_id)
    params.append(int(batch_size))
    sql = (
        f"SELECT {columns} FROM `{spec.source_table}` "
        f"WHERE {where_sql} ORDER BY `{spec.order_column}` LIMIT %s"
    )
    with mysql_conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def _fetch_mysql_rows_by_market_ids(
    mysql_conn,
    spec: TableSpec,
    market_ids: Sequence[int],
) -> List[Mapping[str, Any]]:
    if not market_ids:
        return []
    columns = ", ".join(f"`{col}`" for col in spec.source_columns)
    placeholders = ", ".join(["%s"] * len(market_ids))
    sql = (
        f"SELECT {columns} FROM `{spec.source_table}` "
        f"WHERE `market_id` IN ({placeholders}) ORDER BY `{spec.order_column}`"
    )
    with mysql_conn.cursor() as cur:
        cur.execute(sql, tuple(int(item) for item in market_ids))
        return list(cur.fetchall())


def _pg_existing_conditions(pg_conn, condition_ids: Sequence[str]) -> set[str]:
    ids = [str(item) for item in condition_ids if item]
    if not ids:
        return set()
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT condition_id FROM core.markets WHERE condition_id = ANY(%s)",
            (ids,),
        )
        return {str(row["condition_id"]) for row in cur.fetchall()}


def _pg_existing_id_map(pg_conn, market_ids: Sequence[int]) -> Dict[int, str]:
    ids = [int(item) for item in market_ids if item is not None]
    if not ids:
        return {}
    with pg_conn.cursor() as cur:
        cur.execute("SELECT id, condition_id FROM core.markets WHERE id = ANY(%s)", (ids,))
        return {int(row["id"]): str(row["condition_id"] or "") for row in cur.fetchall()}


def _transform_rows(
    spec: TableSpec,
    rows: Sequence[Mapping[str, Any]],
    bad_rows: BadRowLogger,
) -> List[Dict[str, Any]]:
    transformed: List[Dict[str, Any]] = []
    for row in rows:
        try:
            transformed.append(spec.transform(row, bad_rows))
        except Exception as exc:
            bad_rows.log(spec.key, row, f"transform failed: {exc}")
            raise
    return transformed


def _advance_pg_market_sequence(pg_conn) -> None:
    with pg_conn.cursor() as cur:
        cur.execute("SELECT pg_get_serial_sequence('core.markets', 'id') AS seq")
        row = cur.fetchone()
        seq = row["seq"] if row else None
        if seq:
            cur.execute(
                "SELECT setval(%s, (SELECT GREATEST(COALESCE(MAX(id), 0), 1) FROM core.markets), true)",
                (seq,),
            )
    pg_conn.commit()


def _child_ref_counts(pg_conn, market_ids: Sequence[int]) -> Dict[str, int]:
    ids = [int(item) for item in market_ids if item is not None]
    if not ids:
        return {}
    counts: Dict[str, int] = {}
    with pg_conn.cursor() as cur:
        for table in MARKET_CHILD_TABLES:
            cur.execute(f"SELECT COUNT(*) AS c FROM {quote_table(table)} WHERE market_id = ANY(%s)", (ids,))
            row = cur.fetchone()
            counts[table] = int(row["c"] if row else 0)
    return counts


def _relocate_pg_id_conflicts(
    pg_conn,
    conflicts: Sequence[Tuple[int, str, str]],
    *,
    dry_run: bool,
) -> int:
    """Move polluted PG-only rows away from ids needed by MySQL.

    We only relocate rows with no child references. That is the current expected
    case for the accidentally inserted live-sync rows; if a row already has child
    references, moving it needs a dedicated FK-aware migration.
    """
    if not conflicts:
        return 0
    old_ids = [int(item[0]) for item in conflicts]
    ref_counts = _child_ref_counts(pg_conn, old_ids)
    non_empty_refs = {table: count for table, count in ref_counts.items() if count}
    if non_empty_refs:
        sample_ids = old_ids[:10]
        raise RuntimeError(
            "Refusing to relocate id-conflict rows with child references: "
            f"refs={non_empty_refs}, sample_ids={sample_ids}"
        )
    if dry_run:
        print(f"[relocate] would move {len(conflicts)} PG-only id-conflict rows to new local ids", flush=True)
        return 0

    _advance_pg_market_sequence(pg_conn)
    moved = 0
    with pg_conn.cursor() as cur:
        cur.execute("SELECT pg_get_serial_sequence('core.markets', 'id') AS seq")
        row = cur.fetchone()
        seq = row["seq"] if row else None
        if not seq:
            raise RuntimeError("core.markets.id has no sequence; cannot allocate replacement ids")
        for old_id, _mysql_condition_id, pg_condition_id in conflicts:
            cur.execute("SELECT nextval(%s) AS id", (seq,))
            new_id = int(cur.fetchone()["id"])
            cur.execute(
                """
                UPDATE core.markets
                SET id = %s
                WHERE id = %s AND LOWER(condition_id) = LOWER(%s)
                """,
                (new_id, int(old_id), pg_condition_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(
                    f"failed to relocate PG market id={old_id}; rowcount={cur.rowcount}"
                )
            moved += 1
    pg_conn.commit()
    print(f"[relocate] moved {moved} PG-only id-conflict rows to fresh local ids", flush=True)
    return moved


def _mysql_daily_counts(mysql_conn, since_date: datetime, until_date: Optional[datetime]) -> List[Tuple[str, int]]:
    where_sql, params = _market_where_sql(since_date, until_date, 0)
    sql = (
        "SELECT DATE(`created_at`) AS day, COUNT(*) AS c "
        f"FROM `markets` WHERE {where_sql.replace('`id` > %s AND ', '')} "
        "GROUP BY day ORDER BY day"
    )
    # Drop the id parameter that _market_where_sql always creates.
    params = params[1:]
    with mysql_conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return [(str(row["day"]), int(row["c"])) for row in cur.fetchall()]


def _pg_daily_counts(pg_conn, since_date: datetime, until_date: Optional[datetime]) -> List[Tuple[str, int]]:
    params: List[Any] = [since_date]
    until_sql = ""
    if until_date is not None:
        until_sql = "AND created_at < %s"
        params.append(until_date)
    with pg_conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT created_at::date AS day, COUNT(*) AS c
            FROM core.markets
            WHERE created_at >= %s {until_sql}
            GROUP BY day ORDER BY day
            """,
            tuple(params),
        )
        return [(str(row["day"]), int(row["c"])) for row in cur.fetchall()]


def _pg_quality(pg_conn, since_date: datetime, until_date: Optional[datetime]) -> Dict[str, int]:
    params: List[Any] = [since_date]
    until_sql = ""
    if until_date is not None:
        until_sql = "AND m.created_at < %s"
        params.append(until_date)
    with pg_conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE gamma_market_id IS NULL OR gamma_market_id = '') AS missing_gamma_market_id,
                COUNT(*) FILTER (WHERE condition_id IS NULL OR condition_id = '') AS missing_condition_id,
                COUNT(*) FILTER (WHERE question_id IS NULL OR question_id = '') AS missing_question_id,
                COUNT(*) FILTER (WHERE yes_token_id IS NULL OR yes_token_id = '') AS missing_yes_token_id,
                COUNT(*) FILTER (WHERE no_token_id IS NULL OR no_token_id = '') AS missing_no_token_id,
                COUNT(*) FILTER (
                    WHERE NOT EXISTS (
                        SELECT 1 FROM core.market_tokens mt
                        WHERE mt.market_id = m.id
                          AND mt.token_id IN (m.yes_token_id, m.no_token_id)
                    )
                ) AS missing_market_tokens
            FROM core.markets m
            WHERE m.created_at >= %s {until_sql}
            """,
            tuple(params),
        )
        row = cur.fetchone() or {}
        return {key: int(row[key] or 0) for key in row.keys()}


def _sync_market_tokens_for_pg_window(args: argparse.Namespace) -> int:
    _configure_postgres_runtime(args)
    conn = get_connection(backend="postgres")
    try:
        params: List[Any] = [args.since_date]
        until_sql = ""
        if args.until_date is not None:
            until_sql = "AND created_at < ?"
            params.append(args.until_date)
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT condition_id
            FROM markets
            WHERE created_at >= ?
              {until_sql}
              AND condition_id IS NOT NULL
              AND TRIM(condition_id) <> ''
            """,
            tuple(params),
        )
        condition_ids = [str(row[0]) for row in cur.fetchall()]
        return upsert_market_tokens_for_conditions(conn, condition_ids)
    finally:
        conn.close()


def count_missing_mysql_conditions(
    mysql_conn,
    pg_conn,
    *,
    since_date: datetime,
    until_date: Optional[datetime],
    batch_size: int,
) -> int:
    last_id = 0
    missing = 0
    while True:
        batch = _fetch_mysql_market_batch(
            mysql_conn,
            since_date=since_date,
            until_date=until_date,
            last_id=last_id,
            batch_size=batch_size,
        )
        if not batch:
            break
        existing = _pg_existing_conditions(pg_conn, [str(row.get("condition_id") or "") for row in batch])
        missing += sum(1 for row in batch if str(row.get("condition_id") or "") not in existing)
        last_id = int(batch[-1]["id"])
    return missing


def run_mysql_gap(args: argparse.Namespace) -> None:
    require_drivers()
    _apply_password_commands(args)
    if not args.dry_run and not args.yes:
        raise SystemExit("Refusing to write PostgreSQL without --yes. Use --dry-run for a safe check.")

    bad_rows = BadRowLogger(Path(args.bad_rows_dir))
    mysql_conn = mysql_connect(args)
    pg_conn = pg_connect(args)
    try:
        if args.create_schema and not args.dry_run:
            create_schema(pg_conn, MARKET_COPY_TABLES)
            print("[schema] PostgreSQL market schema is ready", flush=True)

        last_id = 0
        scanned = 0
        missing_markets = 0
        written_markets = 0
        written_tokens = 0
        written_resolution = 0

        while True:
            batch = _fetch_mysql_market_batch(
                mysql_conn,
                since_date=args.since_date,
                until_date=args.until_date,
                last_id=last_id,
                batch_size=args.batch_size,
            )
            if not batch:
                break
            scanned += len(batch)
            last_id = int(batch[-1]["id"])
            existing_conditions = _pg_existing_conditions(
                pg_conn,
                [str(row.get("condition_id") or "") for row in batch],
            )
            missing = [
                row
                for row in batch
                if str(row.get("condition_id") or "") not in existing_conditions
            ]
            if missing:
                id_map = _pg_existing_id_map(pg_conn, [int(row["id"]) for row in missing])
                conflicts = [
                    (int(row["id"]), str(row.get("condition_id") or ""), id_map.get(int(row["id"]), ""))
                    for row in missing
                    if int(row["id"]) in id_map
                    and str(row.get("condition_id") or "").lower() != id_map[int(row["id"])].lower()
                ]
                if conflicts:
                    if not args.relocate_id_conflicts:
                        sample = conflicts[:5]
                        raise RuntimeError(
                            "PostgreSQL id collisions detected; refusing to overwrite. "
                            f"Re-run with --relocate-id-conflicts after reviewing sample={sample}"
                        )
                    _relocate_pg_id_conflicts(pg_conn, conflicts, dry_run=args.dry_run)

                market_ids = [int(row["id"]) for row in missing]
                missing_markets += len(missing)
                if not args.dry_run:
                    markets_spec = TABLE_SPECS["markets"]
                    written_markets += write_rows(
                        pg_conn,
                        markets_spec,
                        _transform_rows(markets_spec, missing, bad_rows),
                        skip_bad_rows=False,
                        bad_rows=bad_rows,
                    )

                    tokens_spec = TABLE_SPECS["market_tokens"]
                    token_rows = _fetch_mysql_rows_by_market_ids(mysql_conn, tokens_spec, market_ids)
                    if token_rows:
                        written_tokens += write_rows(
                            pg_conn,
                            tokens_spec,
                            _transform_rows(tokens_spec, token_rows, bad_rows),
                            skip_bad_rows=False,
                            bad_rows=bad_rows,
                        )

                    if args.copy_resolution_fast:
                        resolution_spec = TABLE_SPECS["market_resolution_fast"]
                        resolution_rows = _fetch_mysql_rows_by_market_ids(mysql_conn, resolution_spec, market_ids)
                        if resolution_rows:
                            written_resolution += write_rows(
                                pg_conn,
                                resolution_spec,
                                _transform_rows(resolution_spec, resolution_rows, bad_rows),
                                skip_bad_rows=False,
                                bad_rows=bad_rows,
                            )

            print(
                f"[mysql-gap] scanned={scanned} missing={missing_markets} "
                f"written_markets={written_markets} written_tokens={written_tokens} "
                f"written_resolution_fast={written_resolution} last_id={last_id}",
                flush=True,
            )

        if not args.dry_run:
            _advance_pg_market_sequence(pg_conn)
            if args.sync_market_tokens:
                synced_tokens = _sync_market_tokens_for_pg_window(args)
                print(f"[tokens] synced market_tokens rows from markets table: {synced_tokens}", flush=True)

        if args.verify:
            missing_after = count_missing_mysql_conditions(
                mysql_conn,
                pg_conn,
                since_date=args.since_date,
                until_date=args.until_date,
                batch_size=args.batch_size,
            )
            print("[verify] mysql daily counts:", _mysql_daily_counts(mysql_conn, args.since_date, args.until_date), flush=True)
            print("[verify] postgres daily counts:", _pg_daily_counts(pg_conn, args.since_date, args.until_date), flush=True)
            print("[verify] postgres quality:", _pg_quality(pg_conn, args.since_date, args.until_date), flush=True)
            print(f"[verify] mysql_conditions_missing_in_postgres={missing_after}", flush=True)
            if missing_after:
                raise RuntimeError(f"{missing_after} MySQL market condition_id values are still missing in PostgreSQL")

        print(
            f"[done] scanned={scanned} missing={missing_markets} "
            f"written_markets={written_markets} written_tokens={written_tokens} "
            f"written_resolution_fast={written_resolution}",
            flush=True,
        )
    finally:
        bad_rows.close()
        try:
            pg_conn.close()
        finally:
            mysql_conn.close()


def _configure_postgres_runtime(args: argparse.Namespace) -> None:
    configure_runtime_db(
        backend="postgres",
        postgres_host=args.postgres_host,
        postgres_port=args.postgres_port,
        postgres_user=args.postgres_user,
        postgres_password=args.postgres_password,
        postgres_database=args.postgres_database,
        postgres_search_path="core,oracle,ops,public",
    )


def _fetch_keyset_page(session: requests.Session, params: Dict[str, Any]) -> Dict[str, Any]:
    resp = session.get(GAMMA_MARKETS_KEYSET_URL, params=params, timeout=REQUEST_TIMEOUT)
    try:
        resp.raise_for_status()
        data = resp.json()
    finally:
        resp.close()
    if not isinstance(data, dict):
        raise RuntimeError(f"GET {GAMMA_MARKETS_KEYSET_URL}: expected object response")
    return data


def run_gamma_window(args: argparse.Namespace) -> None:
    if requests is None:
        raise SystemExit("requests is not installed")
    if not args.dry_run and not args.yes:
        raise SystemExit("Refusing to write PostgreSQL without --yes. Use --dry-run for a safe check.")
    if args.until_date <= args.since_date:
        raise SystemExit("--until-date must be later than --since-date")

    _configure_postgres_runtime(args)
    init_schema(db_path=None)
    conn = get_connection(backend="postgres")
    session = requests.Session()
    cursor: Optional[str] = None
    pages = 0
    seen = 0
    in_window = 0
    normalized = 0
    written = 0
    newest_seen: Optional[datetime] = None
    oldest_seen: Optional[datetime] = None
    buffer: List[Dict[str, Any]] = []

    def flush() -> None:
        nonlocal written, normalized, buffer
        if not buffer:
            return
        if args.dry_run:
            normalized += len(buffer)
            buffer = []
            return
        written += batch_upsert_markets(conn, buffer)
        normalized += len(buffer)
        print(f"[gamma-window] wrote={written} normalized={normalized}", flush=True)
        buffer = []

    try:
        while True:
            params: Dict[str, Any] = {
                "limit": min(100, int(args.page_size)),
                "order": "createdAt",
                "ascending": "false",
            }
            if cursor:
                params["after_cursor"] = cursor
            data = _fetch_keyset_page(session, params)
            markets = data.get("markets") or []
            if not isinstance(markets, list) or not markets:
                break

            pages += 1
            page_has_window_or_newer = False
            for raw in markets:
                if not isinstance(raw, dict):
                    continue
                seen += 1
                _attach_embedded_event_meta_to_market(raw)
                created = _get_market_created_at(raw)
                if created is None:
                    continue
                if newest_seen is None or created > newest_seen:
                    newest_seen = created
                if oldest_seen is None or created < oldest_seen:
                    oldest_seen = created
                if created >= args.until_date:
                    page_has_window_or_newer = True
                    continue
                if created < args.since_date:
                    continue
                page_has_window_or_newer = True
                in_window += 1
                norm = normalize_market_from_gamma(raw)
                if norm:
                    buffer.append(norm)
                    if len(buffer) >= args.batch_size:
                        flush()

            if pages % 25 == 0:
                print(
                    f"[gamma-window] pages={pages} seen={seen} in_window={in_window} "
                    f"normalized={normalized + len(buffer)} oldest_seen={oldest_seen}",
                    flush=True,
                )

            cursor = data.get("next_cursor") or data.get("nextCursor") or data.get("cursor")
            if not cursor:
                break
            if not page_has_window_or_newer:
                break
            if args.max_pages is not None and pages >= args.max_pages:
                break
            if args.delay > 0:
                time.sleep(args.delay)

        flush()
        if args.verify:
            pg_conn = pg_connect(args)
            try:
                print("[verify] postgres daily counts:", _pg_daily_counts(pg_conn, args.since_date, args.until_date), flush=True)
                print("[verify] postgres quality:", _pg_quality(pg_conn, args.since_date, args.until_date), flush=True)
            finally:
                pg_conn.close()
        print(
            f"[done] pages={pages} seen={seen} in_window={in_window} "
            f"normalized={normalized} written={written} newest_seen={newest_seen} oldest_seen={oldest_seen}",
            flush=True,
        )
    finally:
        conn.close()
        session.close()


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mysql-host", default=DEFAULT_MYSQL_HOST)
    parser.add_argument("--mysql-port", type=int, default=DEFAULT_MYSQL_PORT)
    parser.add_argument("--mysql-user", default=DEFAULT_MYSQL_USER)
    parser.add_argument("--mysql-password", default=DEFAULT_MYSQL_PASSWORD)
    parser.add_argument("--mysql-password-command", default=None)
    parser.add_argument("--mysql-database", default=DEFAULT_MYSQL_DATABASE)
    parser.add_argument("--mysql-charset", default=DEFAULT_MYSQL_CHARSET)
    parser.add_argument("--mysql-read-timeout", type=int, default=3600)
    parser.add_argument("--mysql-write-timeout", type=int, default=3600)
    parser.add_argument("--postgres-host", default=DEFAULT_PG_HOST)
    parser.add_argument("--postgres-port", type=int, default=DEFAULT_PG_PORT)
    parser.add_argument("--postgres-user", default=DEFAULT_PG_USER)
    parser.add_argument("--postgres-password", default=DEFAULT_PG_PASSWORD)
    parser.add_argument("--postgres-database", default=DEFAULT_PG_DATABASE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair PostgreSQL market gaps")
    sub = parser.add_subparsers(dest="command", required=True)

    mysql_gap = sub.add_parser("mysql-gap", help="Copy MySQL markets missing from PostgreSQL")
    add_connection_args(mysql_gap)
    mysql_gap.add_argument("--since-date", type=_utc_dt, required=True)
    mysql_gap.add_argument("--until-date", type=_utc_dt, default=None)
    mysql_gap.add_argument("--batch-size", type=int, default=5000)
    mysql_gap.add_argument("--bad-rows-dir", default=str(DEFAULT_BAD_ROWS_DIR))
    mysql_gap.add_argument("--create-schema", action="store_true")
    mysql_gap.add_argument("--copy-resolution-fast", action="store_true", default=True)
    mysql_gap.add_argument("--no-copy-resolution-fast", dest="copy_resolution_fast", action="store_false")
    mysql_gap.add_argument(
        "--relocate-id-conflicts",
        action="store_true",
        help=(
            "Move PG-only rows that occupy MySQL source ids to fresh local ids before copying. "
            "The script refuses to move rows that already have child references."
        ),
    )
    mysql_gap.add_argument("--sync-market-tokens", action="store_true", default=True)
    mysql_gap.add_argument("--no-sync-market-tokens", dest="sync_market_tokens", action="store_false")
    mysql_gap.add_argument("--verify", action="store_true")
    mysql_gap.add_argument("--dry-run", action="store_true")
    mysql_gap.add_argument("--yes", action="store_true")
    mysql_gap.set_defaults(func=run_mysql_gap)

    gamma_window = sub.add_parser("gamma-window", help="Backfill a date window from Gamma /markets/keyset")
    add_connection_args(gamma_window)
    gamma_window.add_argument("--since-date", type=_utc_dt, required=True)
    gamma_window.add_argument("--until-date", type=_utc_dt, required=True)
    gamma_window.add_argument("--page-size", type=int, default=100)
    gamma_window.add_argument("--batch-size", type=int, default=500)
    gamma_window.add_argument("--delay", type=float, default=0.1)
    gamma_window.add_argument("--max-pages", type=int, default=None)
    gamma_window.add_argument("--verify", action="store_true")
    gamma_window.add_argument("--dry-run", action="store_true")
    gamma_window.add_argument("--yes", action="store_true")
    gamma_window.set_defaults(func=run_gamma_window)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    if getattr(args, "batch_size", 1) <= 0:
        raise SystemExit("--batch-size must be positive")
    args.func(args)


if __name__ == "__main__":
    main()
