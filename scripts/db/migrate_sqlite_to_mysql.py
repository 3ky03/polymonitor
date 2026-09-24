#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将现有 SQLite 数据迁移到 MySQL。

默认来源：/data/hy/myPolyDB/polymarket_indexer.db
默认目标：scripts.db.db 中的 MySQL 配置
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import DEFAULT_DB_PATH, get_connection, init_schema

TABLE_ORDER = [
    "markets",
    "block_timestamps",
    "sync_state",
    "uma_adapter_mapping",
    "trades",
    "oracle_events",
]

RESUME_KEY_BY_TABLE = {
    "markets": "id",
    "trades": "id",
    "oracle_events": "id",
}


def _sqlite_has_table(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table,),
    )
    return cur.fetchone() is not None


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def _sqlite_count(conn: sqlite3.Connection, table: str, limit: int | None) -> int:
    if limit is None:
        cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
        return int(cur.fetchone()[0])
    cur = conn.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM {table} LIMIT ?)", (int(limit),))
    return int(cur.fetchone()[0])


def _mysql_count(conn, table: str) -> int:
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
    return int(cur.fetchone()[0])


def _mysql_max_value(conn, table: str, column: str):
    cur = conn.execute(f"SELECT MAX({column}) FROM {table}")
    row = cur.fetchone()
    return row[0] if row else None


def _escape_mysql_field(value) -> str:
    if value is None:
        return r"\N"
    text = str(value)
    text = text.replace("\\", r"\\")
    text = text.replace("\t", r"\t")
    text = text.replace("\n", r"\n")
    text = text.replace("\r", r"\r")
    return text


def _write_chunk_file(columns: List[str], rows) -> str:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".tsv") as tmp:
        for row in rows:
            values = [_escape_mysql_field(row[col]) for col in columns]
            tmp.write("\t".join(values))
            tmp.write("\n")
        return tmp.name


def _load_chunk(mysql_conn, table: str, cols: List[str], chunk_path: str) -> None:
    mysql_cols = ", ".join([f"`{col}`" for col in cols])
    mysql_conn.execute(
        (
            f"LOAD DATA LOCAL INFILE ? INTO TABLE {table} "
            "FIELDS TERMINATED BY '\\t' ESCAPED BY '\\\\' "
            "LINES TERMINATED BY '\\n' "
            f"({mysql_cols})"
        ),
        (chunk_path,),
    )
    mysql_conn.commit()


def truncate_target_tables(mysql_conn, tables: List[str]) -> None:
    mysql_conn.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in reversed(tables):
        mysql_conn.execute(f"TRUNCATE TABLE {table}")
    mysql_conn.execute("SET FOREIGN_KEY_CHECKS = 1")
    mysql_conn.commit()


def migrate_table(
    sqlite_conn: sqlite3.Connection,
    mysql_conn,
    table: str,
    batch_size: int,
    limit: int | None,
    resume: bool = False,
) -> int:
    if not _sqlite_has_table(sqlite_conn, table):
        print(f"[skip] source table missing: {table}")
        return 0

    cols = _sqlite_columns(sqlite_conn, table)
    select_sql = f"SELECT {', '.join(cols)} FROM {table}"
    select_params = []
    source_count = _sqlite_count(sqlite_conn, table, limit=None)
    target_count = _mysql_count(mysql_conn, table)
    if resume and target_count == source_count and limit is None:
        print(f"  [{table}] already complete, skipping", flush=True)
        return 0

    if resume:
        resume_key = RESUME_KEY_BY_TABLE.get(table)
        if resume_key and resume_key in cols:
            last_value = _mysql_max_value(mysql_conn, table, resume_key)
            if last_value is not None:
                select_sql += f" WHERE {resume_key} > ? ORDER BY {resume_key}"
                select_params.append(last_value)
                print(f"  [{table}] resume from {resume_key}>{last_value}", flush=True)
            else:
                select_sql += f" ORDER BY {resume_key}"
        elif target_count > 0 and limit is None:
            raise RuntimeError(
                f"resume is not supported for partially migrated table {table} without a monotonic key"
            )
    if limit is not None:
        select_sql += f" LIMIT {int(limit)}"
    cur = sqlite_conn.execute(select_sql, tuple(select_params))
    inserted = 0

    while True:
        batch = cur.fetchmany(batch_size)
        if not batch:
            break
        chunk_path = _write_chunk_file(cols, batch)
        try:
            _load_chunk(mysql_conn, table, cols, chunk_path)
        finally:
            try:
                os.unlink(chunk_path)
            except FileNotFoundError:
                pass
        inserted += len(batch)
        print(f"  [{table}] migrated {inserted} rows", flush=True)

    return inserted


def verify_table_counts(sqlite_conn: sqlite3.Connection, mysql_conn, table: str, limit: int | None) -> None:
    source_count = _sqlite_count(sqlite_conn, table, limit)
    target_count = _mysql_count(mysql_conn, table)
    print(f"  [{table}] source_count={source_count} target_count={target_count}", flush=True)
    if source_count != target_count:
        raise RuntimeError(
            f"row count mismatch for {table}: source={source_count}, target={target_count}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate SQLite data to MySQL")
    parser.add_argument("--source-sqlite", default=DEFAULT_DB_PATH, help="source sqlite file path")
    parser.add_argument("--batch-size", type=int, default=100000, help="rows per bulk-load chunk")
    parser.add_argument("--limit-per-table", type=int, default=None, help="for smoke test only")
    parser.add_argument("--tables", default=",".join(TABLE_ORDER), help="comma separated tables")
    parser.add_argument("--truncate-target", action="store_true", help="truncate target tables before migration")
    parser.add_argument("--resume", action="store_true", help="resume from target max(id) when supported")
    args = parser.parse_args()

    source_path = Path(args.source_sqlite).expanduser().resolve()
    if not source_path.exists():
        raise SystemExit(f"SQLite source not found: {source_path}")

    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    sqlite_conn = sqlite3.connect(str(source_path))
    try:
        sqlite_conn.row_factory = sqlite3.Row
        mysql_conn = get_connection()
        try:
            init_schema(conn=mysql_conn)
            mysql_conn.execute("SET SESSION FOREIGN_KEY_CHECKS = 0")
            mysql_conn.execute("SET SESSION UNIQUE_CHECKS = 0")
            if args.truncate_target:
                truncate_target_tables(mysql_conn, tables)
            total = 0
            for table in tables:
                print(f"Migrating table: {table}")
                total += migrate_table(
                    sqlite_conn,
                    mysql_conn,
                    table,
                    args.batch_size,
                    args.limit_per_table,
                    resume=args.resume,
                )
                verify_table_counts(sqlite_conn, mysql_conn, table, args.limit_per_table)
            mysql_conn.execute("SET SESSION UNIQUE_CHECKS = 1")
            mysql_conn.execute("SET SESSION FOREIGN_KEY_CHECKS = 1")
            print(f"Migration finished. total_rows={total}")
        finally:
            mysql_conn.close()
    finally:
        sqlite_conn.close()


if __name__ == "__main__":
    main()