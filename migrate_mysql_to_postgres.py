#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migrate polyData market core tables from MySQL to PostgreSQL.

This is the first-stage migration script for the database refactor.
It only migrates the market core group:

- core.markets
- core.market_tokens
- core.market_resolution_fast
- core.market_status_snapshot copied from MySQL when snapshot mode is auto

Oracle migration is intentionally split into migrate_oracle_mysql_to_postgres.py.

- Source: current MySQL `poly_data`.
- Target: PostgreSQL `poly_data_core`.
- Default scope: market relational core only.
- Excluded: oracle_events, oracle mapping tables, sync_state, trades_v2,
  OrderFilled, market serving analytics, address analytics.

The script is intentionally conservative:

- It requires `--yes` before writing data.
- It can create the PostgreSQL core schema/tables.
- It migrates in primary-key batches.
- It normalizes timestamp and JSON fields.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:  # pragma: no cover - handled at runtime.
    pymysql = None
    DictCursor = None

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - handled at runtime.
    psycopg = None
    dict_row = None
    Jsonb = None


PROJECT_ROOT = Path(__file__).resolve().parent


def _load_dotenv_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / ".env.local",
        PROJECT_ROOT / "scripts" / ".env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)


_load_dotenv_files()


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value != "":
            return value
    return default


DEFAULT_MYSQL_HOST = env_first("POLYMARKET_MYSQL_HOST", default="127.0.0.1")
DEFAULT_MYSQL_PORT = int(env_first("POLYMARKET_MYSQL_PORT", default="43306"))
DEFAULT_MYSQL_USER = env_first("POLYMARKET_MYSQL_USER", default="poly_user")
DEFAULT_MYSQL_PASSWORD = env_first("POLYMARKET_MYSQL_PASSWORD")
DEFAULT_MYSQL_DATABASE = env_first("POLYMARKET_MYSQL_DATABASE", default="poly_data")
DEFAULT_MYSQL_CHARSET = env_first("POLYMARKET_MYSQL_CHARSET", default="utf8mb4")

DEFAULT_PG_HOST = env_first(
    "POLYDATA_POSTGRES_HOST",
    "POLYMARKET_POSTGRES_HOST",
    "POLYMARKET_PostgreSQL_HOST",
    default="127.0.0.1",
)
DEFAULT_PG_PORT = int(
    env_first(
        "POLYDATA_POSTGRES_PORT",
        "POLYMARKET_POSTGRES_PORT",
        "POLYMARKET_PostgreSQL_PORT",
        default="45432",
    )
)
DEFAULT_PG_USER = env_first(
    "POLYDATA_POSTGRES_USER",
    "POLYMARKET_POSTGRES_USER",
    "POLYMARKET_PostgreSQL_USER",
    default="poly_user",
)
DEFAULT_PG_PASSWORD = env_first(
    "POLYDATA_POSTGRES_PASSWORD",
    "POLYMARKET_POSTGRES_PASSWORD",
    "POLYMARKET_POSTGRESQL_PASSWORD",
    "POLYMARKET_PostgreSQL_PASSWORD",
)
DEFAULT_PG_DATABASE = env_first(
    "POLYDATA_POSTGRES_DATABASE",
    "POLYMARKET_POSTGRES_DATABASE",
    "POLYMARKET_PostgreSQL_DATABASE",
    default="poly_data_core",
)

DEFAULT_BAD_ROWS_DIR = Path(
    os.environ.get(
        "POLYDATA_MIGRATION_BAD_ROWS_DIR",
        "data/postgres/backups/migration_bad_rows",
    )
)


MARKET_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS core.markets (
  id BIGINT PRIMARY KEY,
  gamma_market_id TEXT,
  slug TEXT NOT NULL,
  condition_id TEXT NOT NULL UNIQUE,
  question_id TEXT,
  oracle TEXT,
  yes_token_id TEXT NOT NULL,
  no_token_id TEXT NOT NULL,
  title TEXT,
  description TEXT,
  enable_neg_risk BOOLEAN NOT NULL DEFAULT FALSE,
  end_date TIMESTAMPTZ,
  raw_end_date TEXT,
  created_at TIMESTAMPTZ,
  raw_created_at TEXT,
  category TEXT,
  tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  clob_token_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  migrated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE core.markets DROP CONSTRAINT IF EXISTS markets_slug_key;

COMMENT ON COLUMN core.markets.id IS
  'Local surrogate market id for internal joins. Do not use for Gamma API /markets/{id}.';
COMMENT ON COLUMN core.markets.gamma_market_id IS
  'Gamma API market id. Use this value for gamma-api.polymarket.com/markets/{id}.';
COMMENT ON COLUMN core.markets.condition_id IS
  'CTF/CLOB condition id. Use this for CLOB market identity and websocket market subscriptions.';
COMMENT ON COLUMN core.markets.yes_token_id IS
  'YES outcome ERC1155/CLOB token id. Use token ids for orderbook, price history, and trade matching.';
COMMENT ON COLUMN core.markets.no_token_id IS
  'NO outcome ERC1155/CLOB token id. Use token ids for orderbook, price history, and trade matching.';

CREATE INDEX IF NOT EXISTS idx_markets_gamma_market_id ON core.markets (gamma_market_id);
CREATE INDEX IF NOT EXISTS idx_markets_slug ON core.markets (slug);
CREATE INDEX IF NOT EXISTS idx_markets_question_id ON core.markets (question_id);
CREATE INDEX IF NOT EXISTS idx_markets_yes_token_id ON core.markets (yes_token_id);
CREATE INDEX IF NOT EXISTS idx_markets_no_token_id ON core.markets (no_token_id);
CREATE INDEX IF NOT EXISTS idx_markets_end_date ON core.markets (end_date);
CREATE INDEX IF NOT EXISTS idx_markets_created_at ON core.markets (created_at);
CREATE INDEX IF NOT EXISTS idx_markets_tags_gin ON core.markets USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_markets_clob_token_ids_gin ON core.markets USING GIN (clob_token_ids);
CREATE INDEX IF NOT EXISTS idx_markets_category_lower ON core.markets (lower(category));
CREATE INDEX IF NOT EXISTS idx_markets_search_text_simple_gin
  ON core.markets USING GIN (
    to_tsvector(
      'simple',
      (((coalesce(title, '') || ' ') || coalesce(slug, '')) || ' ') || coalesce(category, '')
    )
  );

CREATE TABLE IF NOT EXISTS core.market_tokens (
  id BIGINT PRIMARY KEY,
  market_id BIGINT NOT NULL REFERENCES core.markets(id),
  condition_id TEXT NOT NULL,
  token_id TEXT NOT NULL UNIQUE,
  outcome TEXT NOT NULL,
  outcome_index INTEGER NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  end_date TIMESTAMPTZ,
  raw_end_date TEXT,
  created_at TIMESTAMPTZ,
  raw_created_at TEXT,
  updated_at TIMESTAMPTZ,
  raw_updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_market_tokens_market_id ON core.market_tokens (market_id);
CREATE INDEX IF NOT EXISTS idx_market_tokens_condition_id ON core.market_tokens (condition_id);
CREATE INDEX IF NOT EXISTS idx_market_tokens_active ON core.market_tokens (active);

CREATE TABLE IF NOT EXISTS core.market_status_snapshot (
  market_id BIGINT PRIMARY KEY REFERENCES core.markets(id),
  has_settle BOOLEAN NOT NULL DEFAULT FALSE,
  has_propose BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_market_status_snapshot_flags
  ON core.market_status_snapshot (has_settle, has_propose, market_id);

CREATE INDEX IF NOT EXISTS idx_market_status_snapshot_settled
  ON core.market_status_snapshot (market_id)
  WHERE has_settle;

CREATE INDEX IF NOT EXISTS idx_market_status_snapshot_proposed_unsettled
  ON core.market_status_snapshot (market_id)
  WHERE has_propose AND NOT has_settle;

CREATE TABLE IF NOT EXISTS core.market_resolution_fast (
  market_id BIGINT PRIMARY KEY REFERENCES core.markets(id),
  settlement_code SMALLINT NOT NULL CHECK (settlement_code IN (0, 1, 2, 3)),
  condition_id TEXT,
  slug TEXT,
  closed_time TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_mrf_settlement_code ON core.market_resolution_fast (settlement_code);
CREATE INDEX IF NOT EXISTS idx_mrf_condition_id ON core.market_resolution_fast (condition_id);
CREATE INDEX IF NOT EXISTS idx_mrf_slug ON core.market_resolution_fast (slug);
CREATE INDEX IF NOT EXISTS idx_mrf_closed_time ON core.market_resolution_fast (closed_time);

COMMENT ON COLUMN core.market_resolution_fast.settlement_code IS
  '0=UNKNOWN, 1=YES, 2=NO, 3=CANCELLED/VOID';
"""

def _jsonb(value: Any) -> Any:
    if Jsonb is None:
        return json.dumps(value, ensure_ascii=False)
    return Jsonb(value)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_timestamp_text(text: str) -> str:
    candidate = text.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    if candidate.endswith(" UTC"):
        candidate = f"{candidate[:-4]}+00:00"
    return candidate


def parse_timestamp(value: Any) -> Tuple[Optional[datetime], Optional[str]]:
    if value is None:
        return None, None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc), None

    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "nan"}:
        return None, None

    candidate = _normalize_timestamp_text(text)
    try:
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc), None
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return dt, None
        except ValueError:
            continue
    return None, text


def parse_json_array(value: Any) -> Tuple[List[Any], Optional[str]]:
    if value is None:
        return [], None
    if isinstance(value, (list, tuple)):
        return list(value), None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

    text = str(value).strip()
    if not text:
        return [], None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()], text
        return [], text

    if parsed is None:
        return [], None
    if isinstance(parsed, list):
        return parsed, None
    return [parsed], text


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n", ""}:
        return False
    return default


def require_drivers() -> None:
    missing = []
    if pymysql is None:
        missing.append("PyMySQL")
    if psycopg is None:
        missing.append("psycopg[binary]")
    if missing:
        raise SystemExit(
            "Missing Python database driver(s): "
            + ", ".join(missing)
            + ". Install with: python -m pip install -r scripts/requirements.txt"
        )


def quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def quote_table(name: str) -> str:
    return ".".join(quote_ident(part) for part in name.split("."))


class BadRowLogger:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._handles: Dict[str, Any] = {}

    def log(self, table: str, row: Mapping[str, Any], reason: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        handle = self._handles.get(table)
        if handle is None:
            handle = (self.root / f"{table}.jsonl").open("a", encoding="utf-8")
            self._handles[table] = handle
        payload = {"reason": reason, "row": dict(row)}
        handle.write(json.dumps(payload, ensure_ascii=False, default=str))
        handle.write("\n")
        handle.flush()

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()


TransformFn = Callable[[Mapping[str, Any], BadRowLogger], Dict[str, Any]]


@dataclass(frozen=True)
class TableSpec:
    key: str
    source_table: str
    target_table: str
    source_columns: Sequence[str]
    target_columns: Sequence[str]
    conflict_columns: Sequence[str]
    order_column: str
    transform: TransformFn


def transform_markets(row: Mapping[str, Any], bad_rows: BadRowLogger) -> Dict[str, Any]:
    end_date, raw_end_date = parse_timestamp(row.get("end_date"))
    created_at, raw_created_at = parse_timestamp(row.get("created_at"))
    tags, raw_tags = parse_json_array(row.get("tags"))
    clob_token_ids, raw_clob_token_ids = parse_json_array(row.get("clob_token_ids"))
    if raw_tags is not None:
        bad_rows.log("markets", row, "tags was not a clean JSON array; normalized for JSONB")
    if raw_clob_token_ids is not None:
        bad_rows.log("markets", row, "clob_token_ids was not a clean JSON array; normalized for JSONB")
    return {
        "id": row["id"],
        "gamma_market_id": row.get("gamma_market_id"),
        "slug": row.get("slug"),
        "condition_id": row.get("condition_id"),
        "question_id": row.get("question_id"),
        "oracle": row.get("oracle"),
        "yes_token_id": row.get("yes_token_id"),
        "no_token_id": row.get("no_token_id"),
        "title": row.get("title"),
        "description": row.get("description"),
        "enable_neg_risk": as_bool(row.get("enable_neg_risk"), default=False),
        "end_date": end_date,
        "raw_end_date": raw_end_date,
        "created_at": created_at,
        "raw_created_at": raw_created_at,
        "category": row.get("category"),
        "tags": _jsonb(tags),
        "clob_token_ids": _jsonb(clob_token_ids),
    }


def transform_market_tokens(row: Mapping[str, Any], bad_rows: BadRowLogger) -> Dict[str, Any]:
    end_date, raw_end_date = parse_timestamp(row.get("end_date"))
    created_at, raw_created_at = parse_timestamp(row.get("created_at"))
    updated_at, raw_updated_at = parse_timestamp(row.get("updated_at"))
    return {
        "id": row["id"],
        "market_id": row.get("market_id"),
        "condition_id": row.get("condition_id"),
        "token_id": row.get("token_id"),
        "outcome": row.get("outcome"),
        "outcome_index": row.get("outcome_index"),
        "active": as_bool(row.get("active"), default=True),
        "end_date": end_date,
        "raw_end_date": raw_end_date,
        "created_at": created_at,
        "raw_created_at": raw_created_at,
        "updated_at": updated_at,
        "raw_updated_at": raw_updated_at,
    }


def transform_market_status_snapshot(
    row: Mapping[str, Any],
    bad_rows: BadRowLogger,
) -> Dict[str, Any]:
    return {
        "market_id": row.get("market_id"),
        "has_settle": as_bool(row.get("has_settle"), default=False),
        "has_propose": as_bool(row.get("has_propose"), default=False),
        "updated_at": _utc_now(),
    }


def transform_market_resolution_fast(
    row: Mapping[str, Any],
    bad_rows: BadRowLogger,
) -> Dict[str, Any]:
    settlement_code = int(row.get("settlement_code"))
    if settlement_code not in {0, 1, 2, 3}:
        raise ValueError(f"unexpected settlement_code={settlement_code}")
    closed_time, raw_closed_time = parse_timestamp(row.get("closed_time"))
    updated_at, raw_updated_at = parse_timestamp(row.get("updated_at"))
    if raw_closed_time is not None:
        bad_rows.log("market_resolution_fast", row, "closed_time could not be parsed")
    if raw_updated_at is not None:
        bad_rows.log("market_resolution_fast", row, "updated_at could not be parsed")
    return {
        "market_id": row.get("market_id"),
        "settlement_code": settlement_code,
        "condition_id": row.get("condition_id"),
        "slug": row.get("slug"),
        "closed_time": closed_time,
        "updated_at": updated_at,
    }


TABLE_SPECS: Dict[str, TableSpec] = {
    "markets": TableSpec(
        key="markets",
        source_table="markets",
        target_table="core.markets",
        source_columns=(
            "id",
            "gamma_market_id",
            "slug",
            "condition_id",
            "question_id",
            "oracle",
            "yes_token_id",
            "no_token_id",
            "title",
            "description",
            "enable_neg_risk",
            "end_date",
            "created_at",
            "category",
            "tags",
            "clob_token_ids",
        ),
        target_columns=(
            "id",
            "gamma_market_id",
            "slug",
            "condition_id",
            "question_id",
            "oracle",
            "yes_token_id",
            "no_token_id",
            "title",
            "description",
            "enable_neg_risk",
            "end_date",
            "raw_end_date",
            "created_at",
            "raw_created_at",
            "category",
            "tags",
            "clob_token_ids",
        ),
        conflict_columns=("id",),
        order_column="id",
        transform=transform_markets,
    ),
    "market_tokens": TableSpec(
        key="market_tokens",
        source_table="market_tokens",
        target_table="core.market_tokens",
        source_columns=(
            "id",
            "market_id",
            "condition_id",
            "token_id",
            "outcome",
            "outcome_index",
            "active",
            "end_date",
            "created_at",
            "updated_at",
        ),
        target_columns=(
            "id",
            "market_id",
            "condition_id",
            "token_id",
            "outcome",
            "outcome_index",
            "active",
            "end_date",
            "raw_end_date",
            "created_at",
            "raw_created_at",
            "updated_at",
            "raw_updated_at",
        ),
        conflict_columns=("id",),
        order_column="id",
        transform=transform_market_tokens,
    ),
    "market_status_snapshot": TableSpec(
        key="market_status_snapshot",
        source_table="market_status_snapshot",
        target_table="core.market_status_snapshot",
        source_columns=("market_id", "has_settle", "has_propose"),
        target_columns=("market_id", "has_settle", "has_propose", "updated_at"),
        conflict_columns=("market_id",),
        order_column="market_id",
        transform=transform_market_status_snapshot,
    ),
    "market_resolution_fast": TableSpec(
        key="market_resolution_fast",
        source_table="market_resolution_fast",
        target_table="core.market_resolution_fast",
        source_columns=("market_id", "settlement_code", "condition_id", "slug", "closed_time", "updated_at"),
        target_columns=("market_id", "settlement_code", "condition_id", "slug", "closed_time", "updated_at"),
        conflict_columns=("market_id",),
        order_column="market_id",
        transform=transform_market_resolution_fast,
    ),
}

TABLE_GROUPS: Dict[str, Sequence[str]] = {
    "market": ("markets", "market_tokens", "market_resolution_fast"),
    "all": ("markets", "market_tokens", "market_resolution_fast"),
}

TRUNCATE_TARGETS = (
    "core.market_tokens",
    "core.market_status_snapshot",
    "core.market_resolution_fast",
    "core.markets",
)


def expand_tables(raw: str) -> List[str]:
    tables: List[str] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if item in TABLE_GROUPS:
            for table in TABLE_GROUPS[item]:
                if table not in tables:
                    tables.append(table)
            continue
        if item not in TABLE_SPECS:
            valid = sorted([*TABLE_GROUPS.keys(), *TABLE_SPECS.keys()])
            raise SystemExit(f"Unknown table/group {item!r}. Valid values: {', '.join(valid)}")
        if item not in tables:
            tables.append(item)
    return tables


def mysql_connect(args: argparse.Namespace):
    require_drivers()
    return pymysql.connect(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
        charset=args.mysql_charset,
        cursorclass=DictCursor,
        autocommit=False,
        read_timeout=args.mysql_read_timeout,
        write_timeout=args.mysql_write_timeout,
    )


def pg_connect(args: argparse.Namespace):
    require_drivers()
    return psycopg.connect(
        host=args.postgres_host,
        port=args.postgres_port,
        user=args.postgres_user,
        password=args.postgres_password,
        dbname=args.postgres_database,
        row_factory=dict_row,
        autocommit=False,
    )


def create_schema(pg_conn, selected_tables: Sequence[str]) -> None:
    with pg_conn.cursor() as cur:
        cur.execute(MARKET_SCHEMA_SQL)
    pg_conn.commit()


def make_upsert_sql(spec: TableSpec) -> str:
    target = quote_table(spec.target_table)
    columns = ", ".join(quote_ident(col) for col in spec.target_columns)
    placeholders = ", ".join(f"%({col})s" for col in spec.target_columns)
    conflicts = ", ".join(quote_ident(col) for col in spec.conflict_columns)
    update_columns = [col for col in spec.target_columns if col not in spec.conflict_columns]
    if update_columns:
        updates = ", ".join(
            f"{quote_ident(col)} = EXCLUDED.{quote_ident(col)}" for col in update_columns
        )
        conflict_clause = f"DO UPDATE SET {updates}"
    else:
        conflict_clause = "DO NOTHING"
    return (
        f"INSERT INTO {target} ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflicts}) {conflict_clause}"
    )


def mysql_count(mysql_conn, table: str, limit: Optional[int] = None) -> int:
    with mysql_conn.cursor() as cur:
        if limit is None:
            cur.execute(f"SELECT COUNT(*) AS c FROM `{table}`")
        else:
            cur.execute(f"SELECT COUNT(*) AS c FROM (SELECT 1 FROM `{table}` LIMIT %s) AS t", (limit,))
        row = cur.fetchone()
        return int(row["c"] if row else 0)


def pg_count(pg_conn, target_table: str) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS c FROM {quote_table(target_table)}")
        row = cur.fetchone()
        return int(row["c"] if row else 0)


def safe_pg_count(pg_conn, target_table: str) -> Optional[int]:
    try:
        return pg_count(pg_conn, target_table)
    except Exception:
        pg_conn.rollback()
        return None


def pg_max_value(pg_conn, target_table: str, column: str) -> Any:
    with pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT MAX({quote_ident(column)}) AS v FROM {quote_table(target_table)}"
        )
        row = cur.fetchone()
        return row["v"] if row else None


def fetch_mysql_batch(
    mysql_conn,
    spec: TableSpec,
    last_value: Any,
    batch_size: int,
    remaining: Optional[int],
) -> List[Mapping[str, Any]]:
    columns = ", ".join(f"`{col}`" for col in spec.source_columns)
    limit = batch_size if remaining is None else min(batch_size, remaining)
    where_sql = ""
    params: List[Any] = []
    if last_value is not None:
        where_sql = f"WHERE `{spec.order_column}` > %s"
        params.append(last_value)
    params.append(limit)
    sql = (
        f"SELECT {columns} FROM `{spec.source_table}` "
        f"{where_sql} ORDER BY `{spec.order_column}` LIMIT %s"
    )
    with mysql_conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def write_rows(
    pg_conn,
    spec: TableSpec,
    rows: Sequence[Mapping[str, Any]],
    *,
    skip_bad_rows: bool,
    bad_rows: BadRowLogger,
) -> int:
    if not rows:
        return 0
    sql = make_upsert_sql(spec)
    try:
        with pg_conn.cursor() as cur:
            cur.executemany(sql, rows)
        pg_conn.commit()
        return len(rows)
    except Exception:
        pg_conn.rollback()
        if not skip_bad_rows:
            raise

    inserted = 0
    with pg_conn.cursor() as cur:
        for row in rows:
            try:
                cur.execute(sql, row)
                inserted += 1
            except Exception as exc:  # pragma: no cover - row-specific recovery path.
                pg_conn.rollback()
                bad_rows.log(spec.key, row, f"postgres insert failed: {exc}")
            else:
                pg_conn.commit()
    return inserted


def migrate_one_table(
    mysql_conn,
    pg_conn,
    spec: TableSpec,
    args: argparse.Namespace,
    bad_rows: BadRowLogger,
) -> int:
    source_count = mysql_count(mysql_conn, spec.source_table, args.limit_per_table)
    if args.dry_run:
        target_count = safe_pg_count(pg_conn, spec.target_table)
        target_text = "missing" if target_count is None else str(target_count)
        print(
            f"[dry-run] {spec.key}: source_count={source_count} "
            f"target_count={target_text} target={spec.target_table}",
            flush=True,
        )
        return 0

    remaining = args.limit_per_table
    last_value = None
    if args.resume and args.limit_per_table is None:
        last_value = pg_max_value(pg_conn, spec.target_table, spec.order_column)
        if last_value is not None:
            print(f"  [{spec.key}] resume from {spec.order_column}>{last_value}", flush=True)

    migrated = 0
    while remaining is None or remaining > 0:
        batch = fetch_mysql_batch(mysql_conn, spec, last_value, args.batch_size, remaining)
        if not batch:
            break
        transformed: List[Dict[str, Any]] = []
        for row in batch:
            try:
                transformed.append(spec.transform(row, bad_rows))
            except Exception as exc:
                bad_rows.log(spec.key, row, f"transform failed: {exc}")
                if not args.skip_bad_rows:
                    raise
        if transformed:
            migrated += write_rows(
                pg_conn,
                spec,
                transformed,
                skip_bad_rows=args.skip_bad_rows,
                bad_rows=bad_rows,
            )
        last_value = batch[-1][spec.order_column]
        if remaining is not None:
            remaining -= len(batch)
        print(
            f"  [{spec.key}] migrated={migrated} source_count={source_count} "
            f"last_{spec.order_column}={last_value}",
            flush=True,
        )
    return migrated


def truncate_targets(pg_conn, selected_tables: Sequence[str], snapshot_mode: str) -> None:
    selected_targets = [TABLE_SPECS[name].target_table for name in selected_tables]
    if snapshot_mode == "copy" and "core.market_status_snapshot" not in selected_targets:
        selected_targets.append("core.market_status_snapshot")
    ordered = [target for target in TRUNCATE_TARGETS if target in set(selected_targets)]
    if not ordered:
        return
    sql = "TRUNCATE TABLE " + ", ".join(quote_table(t) for t in ordered) + " RESTART IDENTITY CASCADE"
    with pg_conn.cursor() as cur:
        cur.execute(sql)
    pg_conn.commit()
    print(f"[truncate] {', '.join(ordered)}", flush=True)


def resolve_snapshot_mode(requested: str, selected_tables: Sequence[str]) -> str:
    if requested != "auto":
        return requested
    if any(name in selected_tables for name in ("markets", "market_tokens", "market_resolution_fast")):
        return "copy"
    return "skip"


def verify_counts(
    mysql_conn,
    pg_conn,
    selected_tables: Sequence[str],
    snapshot_mode: str,
    limit: Optional[int],
) -> None:
    print("[verify] row counts", flush=True)
    for name in selected_tables:
        spec = TABLE_SPECS[name]
        source_count = mysql_count(mysql_conn, spec.source_table, limit)
        target_count = pg_count(pg_conn, spec.target_table)
        status = "ok" if limit is not None or source_count == target_count else "mismatch"
        print(
            f"  [{status}] {name}: source={source_count} target={target_count}",
            flush=True,
        )
        if limit is None and source_count != target_count:
            raise RuntimeError(f"row count mismatch for {name}: source={source_count} target={target_count}")


def print_status_distributions(mysql_conn, pg_conn) -> None:
    with mysql_conn.cursor() as cur:
        cur.execute(
            "SELECT has_settle, has_propose, COUNT(*) AS c "
            "FROM market_status_snapshot GROUP BY has_settle, has_propose "
            "ORDER BY has_settle, has_propose"
        )
        mysql_rows = cur.fetchall()
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_settle, has_propose, COUNT(*) AS c "
            "FROM core.market_status_snapshot GROUP BY has_settle, has_propose "
            "ORDER BY has_settle, has_propose"
        )
        pg_rows = cur.fetchall()
    print("[verify] market_status_snapshot distribution", flush=True)
    print(f"  mysql: {mysql_rows}", flush=True)
    print(f"  postgres: {pg_rows}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate polyData market core data from MySQL to PostgreSQL"
    )
    parser.add_argument("--mysql-host", default=DEFAULT_MYSQL_HOST)
    parser.add_argument("--mysql-port", type=int, default=DEFAULT_MYSQL_PORT)
    parser.add_argument("--mysql-user", default=DEFAULT_MYSQL_USER)
    parser.add_argument("--mysql-database", default=DEFAULT_MYSQL_DATABASE)
    parser.add_argument("--mysql-charset", default=DEFAULT_MYSQL_CHARSET)
    parser.add_argument("--mysql-read-timeout", type=int, default=3600)
    parser.add_argument("--mysql-write-timeout", type=int, default=3600)

    parser.add_argument("--postgres-host", default=DEFAULT_PG_HOST)
    parser.add_argument("--postgres-port", type=int, default=DEFAULT_PG_PORT)
    parser.add_argument("--postgres-user", default=DEFAULT_PG_USER)
    parser.add_argument("--postgres-database", default=DEFAULT_PG_DATABASE)

    parser.add_argument(
        "--tables",
        default="market",
        help=(
            "Comma separated market tables or groups. Groups: market, all. "
            "Default: market"
        ),
    )
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--limit-per-table", type=int, default=None)
    parser.add_argument("--bad-rows-dir", default=str(DEFAULT_BAD_ROWS_DIR))
    parser.add_argument(
        "--snapshot-mode",
        choices=["auto", "copy", "skip"],
        default="auto",
        help=(
            "How to populate core.market_status_snapshot. "
            "auto=copy for market runs. Oracle rebuild is handled by "
            "migrate_oracle_mysql_to_postgres.py."
        ),
    )

    parser.add_argument("--create-schema", action="store_true", help="Create target schemas/tables first")
    parser.add_argument("--schema-only", action="store_true", help="Create schema and exit")
    parser.add_argument("--truncate-target", action="store_true", help="Truncate selected target tables first")
    parser.add_argument("--resume", action="store_true", help="Resume from target max(order_column)")
    parser.add_argument("--skip-bad-rows", action="store_true", help="Log and skip transform/insert failures")
    parser.add_argument("--verify", action="store_true", help="Verify row counts after migration")
    parser.add_argument("--dry-run", action="store_true", help="Connect and print counts without writing")
    parser.add_argument("--yes", action="store_true", help="Confirm that writes to PostgreSQL are intended")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.mysql_password = DEFAULT_MYSQL_PASSWORD
    args.postgres_password = DEFAULT_PG_PASSWORD
    selected_tables = expand_tables(args.tables)
    snapshot_mode = resolve_snapshot_mode(args.snapshot_mode, selected_tables)

    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")
    if args.limit_per_table is not None and args.limit_per_table <= 0:
        raise SystemExit("--limit-per-table must be positive when provided")
    if args.truncate_target and args.resume:
        raise SystemExit("--truncate-target and --resume cannot be used together")
    if args.schema_only:
        args.create_schema = True
    if not args.dry_run and not args.yes and not args.schema_only:
        raise SystemExit("Refusing to write PostgreSQL without --yes. Use --dry-run for a safe check.")

    require_drivers()

    print(
        "[connect] "
        f"mysql={args.mysql_user}@{args.mysql_host}:{args.mysql_port}/{args.mysql_database} "
        f"postgres={args.postgres_user}@{args.postgres_host}:{args.postgres_port}/{args.postgres_database}",
        flush=True,
    )
    print(
        f"[plan] tables={','.join(selected_tables)} snapshot_mode={snapshot_mode} "
        f"batch_size={args.batch_size} dry_run={args.dry_run}",
        flush=True,
    )

    bad_rows = BadRowLogger(Path(args.bad_rows_dir))
    try:
        mysql_conn = mysql_connect(args)
    except Exception as exc:
        raise SystemExit(
            "MySQL connection failed: "
            f"{args.mysql_user}@{args.mysql_host}:{args.mysql_port}/{args.mysql_database}: {exc}"
        ) from exc

    try:
        pg_conn = pg_connect(args)
    except Exception as exc:
        mysql_conn.close()
        raise SystemExit(
            "PostgreSQL connection failed: "
            f"{args.postgres_user}@{args.postgres_host}:{args.postgres_port}/{args.postgres_database}: {exc}"
        ) from exc
    try:
        if args.create_schema:
            if args.dry_run:
                print("[dry-run] would create PostgreSQL schemas and tables", flush=True)
            else:
                create_schema(pg_conn, selected_tables)
                print("[schema] PostgreSQL schemas/tables are ready", flush=True)
        if args.schema_only:
            return

        if args.truncate_target:
            truncate_targets(pg_conn, selected_tables, snapshot_mode)

        total = 0
        for name in selected_tables:
            spec = TABLE_SPECS[name]
            print(f"[migrate] {name}: {spec.source_table} -> {spec.target_table}", flush=True)
            total += migrate_one_table(mysql_conn, pg_conn, spec, args, bad_rows)

        if snapshot_mode == "copy" and "market_status_snapshot" not in selected_tables:
            spec = TABLE_SPECS["market_status_snapshot"]
            print(f"[migrate] market_status_snapshot: {spec.source_table} -> {spec.target_table}", flush=True)
            total += migrate_one_table(mysql_conn, pg_conn, spec, args, bad_rows)
            selected_tables = [*selected_tables, "market_status_snapshot"]

        if args.verify:
            verify_counts(mysql_conn, pg_conn, selected_tables, snapshot_mode, args.limit_per_table)
            if snapshot_mode == "copy":
                print_status_distributions(mysql_conn, pg_conn)

        print(f"[done] migrated_rows={total}", flush=True)
    finally:
        bad_rows.close()
        try:
            pg_conn.close()
        finally:
            mysql_conn.close()


if __name__ == "__main__":
    main()
