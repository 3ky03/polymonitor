#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket LOB streaming service.

This module keeps Polymarket market metadata and LOB subscriptions aligned:
- derive token subscriptions from the existing `markets` table
- normalize WebSocket market-channel messages into durable database rows
- backfill unknown token -> market mappings before any LOB row is persisted
- support long-running live streaming with reconnect, heartbeat, and reconciliation
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import math
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:  # pragma: no cover - exercised by runtime env, not unit tests
    websockets = None
    ConnectionClosed = Exception

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import add_db_cli_args, configure_db_from_args, describe_db_target, get_connection, init_schema
from data_sources import POLYMARKET_CLOB_WS_URL

UTC = timezone.utc
DEFAULT_STREAM_NAME = "polymarket_lob"
DEFAULT_WS_URL = POLYMARKET_CLOB_WS_URL
DEFAULT_SYNC_STATE_KEY = "lob_market_sync"
DEFAULT_HEARTBEAT_SECONDS = 10.0
DEFAULT_SUBSCRIPTION_SYNC_SECONDS = 60.0
DEFAULT_STALE_AFTER_SECONDS = 300.0
DEFAULT_RECONNECT_BASE_SECONDS = 3.0
DEFAULT_RECONNECT_MAX_SECONDS = 60.0
DEFAULT_SUBSCRIPTION_BATCH_SIZE = 500
DEFAULT_PRICE_CHANGE_THROTTLE_MS = 250
DEFAULT_BBO_THROTTLE_MS = 1000
DEFAULT_INITIAL_SYNC_MARKET_LIMIT = 500
DEFAULT_NEW_MARKET_POLL_SECONDS = 5.0


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def safe_json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_token_id(value: Any) -> str:
    text = str(value or "").strip()
    return text


def parse_decimal_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        quantized = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return format(quantized.normalize(), "f")


def parse_decimal_float(value: Any) -> Optional[float]:
    text = parse_decimal_text(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def decimal_midpoint(best_bid: Optional[str], best_ask: Optional[str]) -> Optional[str]:
    if best_bid is None or best_ask is None:
        return None
    try:
        value = (Decimal(best_bid) + Decimal(best_ask)) / Decimal("2")
    except (InvalidOperation, ValueError):
        return None
    return format(value.normalize(), "f")


def decimal_spread(best_bid: Optional[str], best_ask: Optional[str]) -> Optional[str]:
    if best_bid is None or best_ask is None:
        return None
    try:
        value = Decimal(best_ask) - Decimal(best_bid)
    except (InvalidOperation, ValueError):
        return None
    return format(value.normalize(), "f")


def parse_timestamp_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def ms_to_iso(value: Optional[int]) -> str:
    if value is None:
        return utc_now().isoformat()
    return datetime.fromtimestamp(value / 1000.0, tz=UTC).isoformat()


def parse_datetime_any(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    for parser in (datetime.fromisoformat,):
        try:
            dt = parser(text)
            return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def chunked(values: Sequence[str], size: int) -> Iterable[List[str]]:
    if size <= 0:
        yield list(values)
        return
    for idx in range(0, len(values), size):
        yield list(values[idx : idx + size])


def coerce_json_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]


def sql_now_text() -> str:
    return utc_now().isoformat()


@dataclass
class TokenMapping:
    market_id: int
    condition_id: str
    token_id: str
    outcome: str
    outcome_index: int
    active: bool
    end_date: Optional[datetime] = None


@dataclass
class LOBLevel:
    side: str
    price: str
    size: str
    level_index: int


@dataclass
class LOBSnapshot:
    stream_name: str
    market_id: int
    condition_id: str
    token_id: str
    outcome: str
    event_type: str
    event_timestamp_ms: int
    event_time: str
    received_at: str
    best_bid: Optional[str] = None
    best_ask: Optional[str] = None
    midpoint: Optional[str] = None
    spread: Optional[str] = None
    last_trade_price: Optional[str] = None
    last_trade_side: Optional[str] = None
    last_trade_size: Optional[str] = None
    price: Optional[str] = None
    size: Optional[str] = None
    side: Optional[str] = None
    tick_size: Optional[str] = None
    source_hash: Optional[str] = None
    dedupe_key: str = ""
    raw_payload: str = ""
    levels: List[LOBLevel] = field(default_factory=list)

    def fingerprint(self) -> str:
        basis = {
            "best_ask": self.best_ask,
            "best_bid": self.best_bid,
            "event_type": self.event_type,
            "last_trade_price": self.last_trade_price,
            "last_trade_side": self.last_trade_side,
            "last_trade_size": self.last_trade_size,
            "outcome": self.outcome,
            "price": self.price,
            "side": self.side,
            "size": self.size,
            "tick_size": self.tick_size,
            "token_id": self.token_id,
        }
        return sha256_text(safe_json_dumps(basis))


@dataclass
class SyncSummary:
    markets_seen: int = 0
    markets_processed: int = 0
    tokens_upserted: int = 0
    subscriptions_upserted: int = 0
    subscriptions_deactivated: int = 0
    batches_committed: int = 0


@dataclass
class ReconcileSummary:
    refreshed_subscriptions: int = 0
    stale_marked: int = 0
    orphaned_marked: int = 0


class SnapshotThrottle:
    def __init__(self, *, bbo_ms: int = 0, price_change_ms: int = 0) -> None:
        self.bbo_ms = max(0, int(bbo_ms))
        self.price_change_ms = max(0, int(price_change_ms))
        self._seen: Dict[Tuple[str, str], Tuple[int, str]] = {}

    def should_write(self, snapshot: LOBSnapshot) -> bool:
        limit = 0
        if snapshot.event_type == "best_bid_ask":
            limit = self.bbo_ms
        elif snapshot.event_type == "price_change":
            limit = self.price_change_ms
        if limit <= 0:
            return True
        key = (snapshot.token_id, snapshot.event_type)
        current = (snapshot.event_timestamp_ms, snapshot.fingerprint())
        previous = self._seen.get(key)
        if previous is None:
            self._seen[key] = current
            return True
        prev_ts, prev_fp = previous
        if snapshot.event_timestamp_ms <= prev_ts and snapshot.fingerprint() == prev_fp:
            return False
        if snapshot.event_timestamp_ms - prev_ts < limit:
            return False
        self._seen[key] = current
        return True


def _mysql_last_insert_id(conn) -> Optional[int]:
    cur = conn.execute("SELECT LAST_INSERT_ID()")
    row = cur.fetchone()
    if not row:
        return None
    return int(row[0]) if row[0] is not None else None


def init_lob_schema(conn=None, db_path: Optional[str] = None) -> None:
    close_after = False
    if conn is None:
        conn = get_connection(db_path)
        close_after = True
    try:
        init_schema(conn=conn, db_path=db_path or "")
        if isinstance(conn, sqlite3.Connection):
            _init_lob_sqlite_schema(conn)
        else:
            _init_lob_mysql_schema(conn)
        conn.commit()
    finally:
        if close_after:
            conn.close()


def _init_lob_sqlite_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS market_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id INTEGER NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            outcome TEXT NOT NULL,
            outcome_index INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            end_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(token_id),
            FOREIGN KEY (market_id) REFERENCES markets(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS market_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id INTEGER NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            outcome TEXT NOT NULL,
            desired_active INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            subscribe_status TEXT NOT NULL DEFAULT 'pending',
            last_subscribed_at TEXT,
            last_message_at TEXT,
            error_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(token_id),
            FOREIGN KEY (market_id) REFERENCES markets(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS lob_stream_state (
            stream_name TEXT PRIMARY KEY,
            ws_url TEXT,
            stream_cursor TEXT,
            connection_status TEXT,
            reconnect_count INTEGER NOT NULL DEFAULT 0,
            last_heartbeat_at TEXT,
            last_message_at TEXT,
            subscribed_assets TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS lob_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_name TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            outcome TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_timestamp_ms INTEGER NOT NULL,
            event_time TEXT NOT NULL,
            received_at TEXT NOT NULL,
            best_bid TEXT,
            best_ask TEXT,
            midpoint TEXT,
            spread TEXT,
            last_trade_price TEXT,
            last_trade_side TEXT,
            last_trade_size TEXT,
            price TEXT,
            size TEXT,
            side TEXT,
            tick_size TEXT,
            source_hash TEXT,
            dedupe_key TEXT NOT NULL UNIQUE,
            raw_payload TEXT,
            FOREIGN KEY (market_id) REFERENCES markets(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS lob_levels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            side TEXT NOT NULL,
            level_index INTEGER NOT NULL,
            price TEXT NOT NULL,
            size TEXT NOT NULL,
            UNIQUE(snapshot_id, side, level_index),
            FOREIGN KEY (snapshot_id) REFERENCES lob_snapshots(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS lob_dead_letters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_name TEXT NOT NULL,
            token_id TEXT,
            condition_id TEXT,
            event_type TEXT,
            reason TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            raw_payload TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 1,
            resolved INTEGER NOT NULL DEFAULT 0,
            UNIQUE(payload_hash)
        )
        """
    )
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_market_tokens_market_id ON market_tokens(market_id)",
        "CREATE INDEX IF NOT EXISTS idx_market_tokens_condition_id ON market_tokens(condition_id)",
        "CREATE INDEX IF NOT EXISTS idx_market_subscriptions_status ON market_subscriptions(active, subscribe_status)",
        "CREATE INDEX IF NOT EXISTS idx_market_subscriptions_market_id ON market_subscriptions(market_id)",
        "CREATE INDEX IF NOT EXISTS idx_lob_snapshots_market_ts ON lob_snapshots(market_id, event_timestamp_ms)",
        "CREATE INDEX IF NOT EXISTS idx_lob_snapshots_token_ts ON lob_snapshots(token_id, event_timestamp_ms)",
        "CREATE INDEX IF NOT EXISTS idx_lob_stream_state_status ON lob_stream_state(connection_status, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_lob_dead_letters_reason ON lob_dead_letters(reason, resolved)",
    ):
        cur.execute(ddl)


def _init_lob_mysql_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_tokens (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            market_id BIGINT NOT NULL,
            condition_id VARCHAR(255) NOT NULL,
            token_id VARCHAR(255) NOT NULL,
            outcome VARCHAR(64) NOT NULL,
            outcome_index INT NOT NULL,
            active TINYINT NOT NULL DEFAULT 1,
            end_date VARCHAR(255),
            created_at VARCHAR(255) NOT NULL,
            updated_at VARCHAR(255) NOT NULL,
            UNIQUE KEY uq_market_tokens_token (token_id),
            KEY idx_market_tokens_market_id (market_id),
            KEY idx_market_tokens_condition_id (condition_id),
            CONSTRAINT fk_market_tokens_market_id FOREIGN KEY (market_id) REFERENCES markets(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_subscriptions (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            market_id BIGINT NOT NULL,
            condition_id VARCHAR(255) NOT NULL,
            token_id VARCHAR(255) NOT NULL,
            outcome VARCHAR(64) NOT NULL,
            desired_active TINYINT NOT NULL DEFAULT 1,
            active TINYINT NOT NULL DEFAULT 1,
            subscribe_status VARCHAR(64) NOT NULL DEFAULT 'pending',
            last_subscribed_at VARCHAR(255),
            last_message_at VARCHAR(255),
            error_count INT NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at VARCHAR(255) NOT NULL,
            UNIQUE KEY uq_market_subscriptions_token (token_id),
            KEY idx_market_subscriptions_status (active, subscribe_status),
            KEY idx_market_subscriptions_market_id (market_id),
            CONSTRAINT fk_market_subscriptions_market_id FOREIGN KEY (market_id) REFERENCES markets(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lob_stream_state (
            stream_name VARCHAR(128) PRIMARY KEY,
            ws_url VARCHAR(255),
            stream_cursor LONGTEXT,
            connection_status VARCHAR(64),
            reconnect_count INT NOT NULL DEFAULT 0,
            last_heartbeat_at VARCHAR(255),
            last_message_at VARCHAR(255),
            subscribed_assets LONGTEXT,
            notes LONGTEXT,
            updated_at VARCHAR(255) NOT NULL,
            KEY idx_lob_stream_state_status (connection_status, updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lob_snapshots (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            stream_name VARCHAR(128) NOT NULL,
            market_id BIGINT NOT NULL,
            condition_id VARCHAR(255) NOT NULL,
            token_id VARCHAR(255) NOT NULL,
            outcome VARCHAR(64) NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            event_timestamp_ms BIGINT NOT NULL,
            event_time VARCHAR(255) NOT NULL,
            received_at VARCHAR(255) NOT NULL,
            best_bid VARCHAR(64),
            best_ask VARCHAR(64),
            midpoint VARCHAR(64),
            spread VARCHAR(64),
            last_trade_price VARCHAR(64),
            last_trade_side VARCHAR(16),
            last_trade_size VARCHAR(64),
            price VARCHAR(64),
            size VARCHAR(64),
            side VARCHAR(16),
            tick_size VARCHAR(64),
            source_hash VARCHAR(255),
            dedupe_key CHAR(64) NOT NULL,
            raw_payload LONGTEXT,
            UNIQUE KEY uq_lob_snapshots_dedupe (dedupe_key),
            KEY idx_lob_snapshots_market_ts (market_id, event_timestamp_ms),
            KEY idx_lob_snapshots_token_ts (token_id, event_timestamp_ms),
            CONSTRAINT fk_lob_snapshots_market_id FOREIGN KEY (market_id) REFERENCES markets(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lob_levels (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            snapshot_id BIGINT NOT NULL,
            side VARCHAR(8) NOT NULL,
            level_index INT NOT NULL,
            price VARCHAR(64) NOT NULL,
            size VARCHAR(64) NOT NULL,
            UNIQUE KEY uq_lob_levels_snapshot_side_level (snapshot_id, side, level_index),
            CONSTRAINT fk_lob_levels_snapshot_id FOREIGN KEY (snapshot_id) REFERENCES lob_snapshots(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lob_dead_letters (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            stream_name VARCHAR(128) NOT NULL,
            token_id VARCHAR(255),
            condition_id VARCHAR(255),
            event_type VARCHAR(64),
            reason VARCHAR(255) NOT NULL,
            payload_hash CHAR(64) NOT NULL,
            raw_payload LONGTEXT NOT NULL,
            first_seen_at VARCHAR(255) NOT NULL,
            last_seen_at VARCHAR(255) NOT NULL,
            attempts INT NOT NULL DEFAULT 1,
            resolved TINYINT NOT NULL DEFAULT 0,
            UNIQUE KEY uq_lob_dead_letters_payload_hash (payload_hash),
            KEY idx_lob_dead_letters_reason (reason, resolved)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def determine_market_active(row: Dict[str, Any], now: Optional[datetime] = None, grace_seconds: float = 0.0) -> bool:
    dt = parse_datetime_any(row.get("end_date"))
    if dt is None:
        return True
    now = now or utc_now()
    return dt >= (now - timedelta(seconds=grace_seconds))


def derive_market_token_rows(row: Dict[str, Any], *, now: Optional[datetime] = None, grace_seconds: float = 0.0) -> List[Dict[str, Any]]:
    market_id = row.get("id")
    condition_id = str(row.get("condition_id") or "").strip()
    if market_id is None or not condition_id:
        return []
    yes_token = normalize_token_id(row.get("yes_token_id"))
    no_token = normalize_token_id(row.get("no_token_id"))
    tokens = coerce_json_list(row.get("clob_token_ids"))
    if not tokens:
        tokens = [token for token in (yes_token, no_token) if token]
    if yes_token and yes_token not in tokens:
        tokens.insert(0, yes_token)
    if no_token and no_token not in tokens:
        tokens.append(no_token)
    active = determine_market_active(row, now=now, grace_seconds=grace_seconds)
    created = sql_now_text()
    derived: List[Dict[str, Any]] = []
    for idx, token_id in enumerate(tokens):
        outcome = f"OUTCOME_{idx}"
        if token_id == yes_token:
            outcome = "YES"
        elif token_id == no_token:
            outcome = "NO"
        derived.append(
            {
                "market_id": int(market_id),
                "condition_id": condition_id,
                "token_id": token_id,
                "outcome": outcome,
                "outcome_index": idx,
                "active": 1 if active else 0,
                "end_date": row.get("end_date"),
                "created_at": created,
                "updated_at": created,
            }
        )
    return derived


class LobRepository:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path
        self.conn = None

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            finally:
                self.conn = None

    def _ensure_conn(self):
        if self.conn is None:
            self.conn = get_connection(self.db_path)
        return self.conn

    def _run(self, operation: Callable[[Any], Any], *, commit: bool = False):
        last_error: Optional[Exception] = None
        for _ in range(2):
            conn = self._ensure_conn()
            try:
                result = operation(conn)
                if commit:
                    conn.commit()
                return result
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
                self.conn = None
        if last_error is not None:
            raise last_error
        raise RuntimeError("repository operation failed without error")

    def init_schema(self) -> None:
        self._run(lambda conn: init_lob_schema(conn=conn, db_path=self.db_path), commit=False)

    def sync_market_tokens(
        self,
        *,
        grace_seconds: float = 0.0,
        active_only: bool = True,
        batch_size: int = 200,
        progress_every: int = 500,
        max_markets: Optional[int] = None,
        progress_callback: Optional[Callable[[SyncSummary], None]] = None,
    ) -> SyncSummary:
        summary = SyncSummary()

        def operation(conn):
            now = utc_now()
            threshold = (now - timedelta(seconds=grace_seconds)).isoformat().replace("+00:00", "Z")
            base_query = """
                SELECT id, condition_id, yes_token_id, no_token_id, clob_token_ids, end_date
                FROM markets
                WHERE condition_id IS NOT NULL
                  AND TRIM(condition_id) <> ''
            """
            params: List[Any] = []
            if active_only:
                active_filter = """
                  AND (
                        end_date IS NULL OR
                        TRIM(COALESCE(end_date, '')) = '' OR
                        end_date >= ?
                  )
                """
                base_query += active_filter
                params.append(threshold)
            base_query += " ORDER BY id ASC"
            if max_markets is not None and max_markets > 0:
                base_query += " LIMIT ?"
                params.append(int(max_markets))
            cur = conn.execute(base_query, tuple(params) if params else None)
            expected_tokens: Set[str] = set()
            idx = 0
            while True:
                fetched = cur.fetchone()
                if fetched is None:
                    break
                idx += 1
                row = dict(fetched) if not hasattr(fetched, "as_dict") else fetched.as_dict()
                summary.markets_processed = idx
                summary.markets_seen = idx
                for token_row in derive_market_token_rows(row, now=now, grace_seconds=grace_seconds):
                    expected_tokens.add(token_row["token_id"])
                    conn.execute(
                        """
                        INSERT INTO market_tokens (
                            market_id, condition_id, token_id, outcome, outcome_index, active, end_date, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(token_id) DO UPDATE SET
                            market_id=excluded.market_id,
                            condition_id=excluded.condition_id,
                            outcome=excluded.outcome,
                            outcome_index=excluded.outcome_index,
                            active=excluded.active,
                            end_date=excluded.end_date,
                            updated_at=excluded.updated_at
                        """,
                        (
                            token_row["market_id"],
                            token_row["condition_id"],
                            token_row["token_id"],
                            token_row["outcome"],
                            token_row["outcome_index"],
                            token_row["active"],
                            token_row["end_date"],
                            token_row["created_at"],
                            token_row["updated_at"],
                        ),
                    )
                    summary.tokens_upserted += 1
                    conn.execute(
                        """
                        INSERT INTO market_subscriptions (
                            market_id, condition_id, token_id, outcome, desired_active, active, subscribe_status, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(token_id) DO UPDATE SET
                            market_id=excluded.market_id,
                            condition_id=excluded.condition_id,
                            outcome=excluded.outcome,
                            desired_active=excluded.desired_active,
                            active=excluded.active,
                            updated_at=excluded.updated_at,
                            subscribe_status=CASE
                                WHEN market_subscriptions.subscribe_status IN ('error', 'stale', 'orphaned')
                                    THEN market_subscriptions.subscribe_status
                                ELSE market_subscriptions.subscribe_status
                            END
                        """,
                        (
                            token_row["market_id"],
                            token_row["condition_id"],
                            token_row["token_id"],
                            token_row["outcome"],
                            token_row["active"],
                            token_row["active"],
                            "pending" if token_row["active"] else "inactive",
                            token_row["updated_at"],
                        ),
                    )
                    summary.subscriptions_upserted += 1
                if batch_size > 0 and idx % batch_size == 0:
                    conn.commit()
                    summary.batches_committed += 1
                    if progress_callback and (progress_every <= 0 or idx % progress_every == 0):
                        progress_callback(summary)

            if progress_callback and idx and (progress_every <= 0 or idx % progress_every != 0):
                progress_callback(summary)

            current = conn.execute(
                """
                SELECT token_id
                FROM market_subscriptions
                WHERE desired_active = 1 OR active = 1
                """
            ).fetchall()
            for row in current:
                token_id = str(row[0])
                if token_id in expected_tokens:
                    continue
                conn.execute(
                    """
                    UPDATE market_subscriptions
                    SET desired_active = 0,
                        active = 0,
                        subscribe_status = 'orphaned',
                        updated_at = ?
                    WHERE token_id = ?
                    """,
                    (sql_now_text(), token_id),
                )
                summary.subscriptions_deactivated += 1
            conn.commit()
            summary.batches_committed += 1

        self._run(operation, commit=True)
        return summary

    def _upsert_market_token_row(self, conn, token_row: Dict[str, Any], summary: SyncSummary) -> None:
        conn.execute(
            """
            INSERT INTO market_tokens (
                market_id, condition_id, token_id, outcome, outcome_index, active, end_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_id) DO UPDATE SET
                market_id=excluded.market_id,
                condition_id=excluded.condition_id,
                outcome=excluded.outcome,
                outcome_index=excluded.outcome_index,
                active=excluded.active,
                end_date=excluded.end_date,
                updated_at=excluded.updated_at
            """,
            (
                token_row["market_id"],
                token_row["condition_id"],
                token_row["token_id"],
                token_row["outcome"],
                token_row["outcome_index"],
                token_row["active"],
                token_row["end_date"],
                token_row["created_at"],
                token_row["updated_at"],
            ),
        )
        summary.tokens_upserted += 1
        conn.execute(
            """
            INSERT INTO market_subscriptions (
                market_id, condition_id, token_id, outcome, desired_active, active, subscribe_status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_id) DO UPDATE SET
                market_id=excluded.market_id,
                condition_id=excluded.condition_id,
                outcome=excluded.outcome,
                desired_active=excluded.desired_active,
                active=excluded.active,
                updated_at=excluded.updated_at,
                subscribe_status=CASE
                    WHEN market_subscriptions.subscribe_status IN ('error', 'stale', 'orphaned')
                        THEN market_subscriptions.subscribe_status
                    ELSE market_subscriptions.subscribe_status
                END
            """,
            (
                token_row["market_id"],
                token_row["condition_id"],
                token_row["token_id"],
                token_row["outcome"],
                token_row["active"],
                token_row["active"],
                "pending" if token_row["active"] else "inactive",
                token_row["updated_at"],
            ),
        )
        summary.subscriptions_upserted += 1

    def get_latest_market_id(self) -> int:
        def operation(conn):
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM markets").fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        return self._run(operation, commit=False)

    def sync_market_tokens_since_id(
        self,
        min_market_id_exclusive: int,
        *,
        grace_seconds: float = 0.0,
        batch_size: int = 200,
    ) -> Tuple[SyncSummary, int]:
        summary = SyncSummary()
        latest_seen_id = int(min_market_id_exclusive or 0)

        def operation(conn):
            nonlocal latest_seen_id
            now = utc_now()
            threshold = (now - timedelta(seconds=grace_seconds)).isoformat().replace("+00:00", "Z")
            cur = conn.execute(
                """
                SELECT id, condition_id, yes_token_id, no_token_id, clob_token_ids, end_date
                FROM markets
                WHERE id > ?
                  AND condition_id IS NOT NULL
                  AND TRIM(condition_id) <> ''
                  AND (
                        end_date IS NULL OR
                        TRIM(COALESCE(end_date, '')) = '' OR
                        end_date >= ?
                  )
                ORDER BY id ASC
                """,
                (int(min_market_id_exclusive), threshold),
            )
            idx = 0
            while True:
                fetched = cur.fetchone()
                if fetched is None:
                    break
                idx += 1
                row = dict(fetched) if not hasattr(fetched, "as_dict") else fetched.as_dict()
                latest_seen_id = max(latest_seen_id, int(row["id"]))
                summary.markets_seen += 1
                summary.markets_processed = summary.markets_seen
                for token_row in derive_market_token_rows(row, now=now, grace_seconds=grace_seconds):
                    self._upsert_market_token_row(conn, token_row, summary)
                if batch_size > 0 and idx % batch_size == 0:
                    conn.commit()
                    summary.batches_committed += 1
            if idx:
                conn.commit()
                summary.batches_committed += 1
            else:
                row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM markets").fetchone()
                latest_seen_id = int(row[0]) if row and row[0] is not None else latest_seen_id

        self._run(operation, commit=True)
        return summary, latest_seen_id

    def load_active_token_mappings(self) -> Dict[str, TokenMapping]:
        def operation(conn):
            cur = conn.execute(
                """
                SELECT mt.market_id, mt.condition_id, mt.token_id, mt.outcome, mt.outcome_index, ms.active, mt.end_date
                FROM market_tokens mt
                LEFT JOIN market_subscriptions ms ON ms.token_id = mt.token_id
                """
            )
            mappings: Dict[str, TokenMapping] = {}
            for row in cur.fetchall():
                record = row.as_dict() if hasattr(row, "as_dict") else dict(row)
                mappings[str(record["token_id"])] = TokenMapping(
                    market_id=int(record["market_id"]),
                    condition_id=str(record["condition_id"]),
                    token_id=str(record["token_id"]),
                    outcome=str(record["outcome"]),
                    outcome_index=int(record["outcome_index"]),
                    active=bool(record.get("active", 0)),
                    end_date=parse_datetime_any(record.get("end_date")),
                )
            return mappings

        return self._run(operation, commit=False)

    def list_desired_subscription_tokens(self) -> List[str]:
        def operation(conn):
            cur = conn.execute(
                """
                SELECT token_id
                FROM market_subscriptions
                WHERE desired_active = 1 AND active = 1
                ORDER BY market_id ASC, outcome ASC
                """
            )
            return [str(row[0]) for row in cur.fetchall()]

        return self._run(operation)

    def mark_tokens_subscribed(self, token_ids: Sequence[str], *, status: str = "subscribed") -> None:
        if not token_ids:
            return

        def operation(conn):
            ts = sql_now_text()
            for token_id in token_ids:
                conn.execute(
                    """
                    UPDATE market_subscriptions
                    SET subscribe_status = ?,
                        last_subscribed_at = ?,
                        updated_at = ?,
                        error_count = CASE WHEN ? = 'error' THEN error_count + 1 ELSE error_count END
                    WHERE token_id = ?
                    """,
                    (status, ts, ts, status, token_id),
                )

        self._run(operation, commit=True)

    def mark_market_inactive(self, condition_id: str) -> List[str]:
        tokens: List[str] = []

        def operation(conn):
            nonlocal tokens
            cur = conn.execute(
                "SELECT token_id FROM market_subscriptions WHERE condition_id = ?",
                (condition_id,),
            )
            tokens = [str(row[0]) for row in cur.fetchall()]
            if not tokens:
                return
            ts = sql_now_text()
            conn.execute(
                """
                UPDATE market_subscriptions
                SET desired_active = 0,
                    active = 0,
                    subscribe_status = 'inactive',
                    updated_at = ?
                WHERE condition_id = ?
                """,
                (ts, condition_id),
            )
            conn.execute(
                """
                UPDATE market_tokens
                SET active = 0,
                    updated_at = ?
                WHERE condition_id = ?
                """,
                (ts, condition_id),
            )

        self._run(operation, commit=True)
        return tokens

    def update_stream_state(
        self,
        *,
        stream_name: str,
        ws_url: str,
        connection_status: str,
        reconnect_count: Optional[int] = None,
        last_heartbeat_at: Optional[str] = None,
        last_message_at: Optional[str] = None,
        subscribed_assets: Optional[Sequence[str]] = None,
        notes: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> None:
        def operation(conn):
            existing = conn.execute(
                "SELECT reconnect_count FROM lob_stream_state WHERE stream_name = ?",
                (stream_name,),
            ).fetchone()
            reconnect_value = reconnect_count
            if reconnect_value is None:
                reconnect_value = int(existing[0]) if existing and existing[0] is not None else 0
            conn.execute(
                """
                INSERT INTO lob_stream_state (
                    stream_name, ws_url, stream_cursor, connection_status, reconnect_count,
                    last_heartbeat_at, last_message_at, subscribed_assets, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stream_name) DO UPDATE SET
                    ws_url=excluded.ws_url,
                    stream_cursor=COALESCE(excluded.stream_cursor, lob_stream_state.stream_cursor),
                    connection_status=excluded.connection_status,
                    reconnect_count=excluded.reconnect_count,
                    last_heartbeat_at=COALESCE(excluded.last_heartbeat_at, lob_stream_state.last_heartbeat_at),
                    last_message_at=COALESCE(excluded.last_message_at, lob_stream_state.last_message_at),
                    subscribed_assets=COALESCE(excluded.subscribed_assets, lob_stream_state.subscribed_assets),
                    notes=COALESCE(excluded.notes, lob_stream_state.notes),
                    updated_at=excluded.updated_at
                """,
                (
                    stream_name,
                    ws_url,
                    cursor,
                    connection_status,
                    reconnect_value,
                    last_heartbeat_at,
                    last_message_at,
                    safe_json_dumps(list(subscribed_assets)) if subscribed_assets is not None else None,
                    notes,
                    sql_now_text(),
                ),
            )

        self._run(operation, commit=True)

    def write_dead_letter(
        self,
        *,
        stream_name: str,
        token_id: Optional[str],
        condition_id: Optional[str],
        event_type: Optional[str],
        reason: str,
        raw_payload: str,
    ) -> None:
        payload_hash = sha256_text(raw_payload)

        def operation(conn):
            now = sql_now_text()
            conn.execute(
                """
                INSERT INTO lob_dead_letters (
                    stream_name, token_id, condition_id, event_type, reason, payload_hash,
                    raw_payload, first_seen_at, last_seen_at, attempts, resolved
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
                ON CONFLICT(payload_hash) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    attempts=lob_dead_letters.attempts + 1,
                    reason=excluded.reason
                """,
                (
                    stream_name,
                    token_id,
                    condition_id,
                    event_type,
                    reason,
                    payload_hash,
                    raw_payload,
                    now,
                    now,
                ),
            )

        self._run(operation, commit=True)

    def touch_token_message(self, token_id: str, *, status: str = "subscribed", when: Optional[str] = None) -> None:
        def operation(conn):
            ts = when or sql_now_text()
            conn.execute(
                """
                UPDATE market_subscriptions
                SET last_message_at = ?,
                    subscribe_status = ?,
                    updated_at = ?
                WHERE token_id = ?
                """,
                (ts, status, ts, token_id),
            )

        self._run(operation, commit=True)

    def insert_snapshot(self, snapshot: LOBSnapshot) -> bool:
        inserted = False

        def operation(conn):
            nonlocal inserted
            before_changes = getattr(conn, "total_changes", None)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO lob_snapshots (
                    stream_name, market_id, condition_id, token_id, outcome, event_type,
                    event_timestamp_ms, event_time, received_at, best_bid, best_ask, midpoint,
                    spread, last_trade_price, last_trade_side, last_trade_size, price, size,
                    side, tick_size, source_hash, dedupe_key, raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.stream_name,
                    snapshot.market_id,
                    snapshot.condition_id,
                    snapshot.token_id,
                    snapshot.outcome,
                    snapshot.event_type,
                    snapshot.event_timestamp_ms,
                    snapshot.event_time,
                    snapshot.received_at,
                    snapshot.best_bid,
                    snapshot.best_ask,
                    snapshot.midpoint,
                    snapshot.spread,
                    snapshot.last_trade_price,
                    snapshot.last_trade_side,
                    snapshot.last_trade_size,
                    snapshot.price,
                    snapshot.size,
                    snapshot.side,
                    snapshot.tick_size,
                    snapshot.source_hash,
                    snapshot.dedupe_key,
                    snapshot.raw_payload,
                ),
            )
            after_changes = getattr(conn, "total_changes", None)
            if before_changes is not None and after_changes is not None:
                inserted = after_changes > before_changes
            else:
                rowcount = getattr(cur, "rowcount", None)
                inserted = rowcount is None or rowcount > 0
            if not inserted:
                inserted = False
                return
            row = conn.execute(
                "SELECT id FROM lob_snapshots WHERE dedupe_key = ?",
                (snapshot.dedupe_key,),
            ).fetchone()
            if not row:
                return
            snapshot_id = int(row[0])
            if snapshot.levels:
                for level in snapshot.levels:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO lob_levels (snapshot_id, side, level_index, price, size)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot_id,
                            level.side,
                            level.level_index,
                            level.price,
                            level.size,
                        ),
                    )
            inserted = True

        self._run(operation, commit=True)
        return inserted

    def mark_stale_subscriptions(self, *, stale_after_seconds: float) -> int:
        updated = 0

        def operation(conn):
            nonlocal updated
            threshold = (utc_now() - timedelta(seconds=stale_after_seconds)).isoformat()
            cur = conn.execute(
                """
                SELECT token_id
                FROM market_subscriptions
                WHERE desired_active = 1
                  AND active = 1
                  AND (
                        last_message_at IS NULL OR
                        last_message_at < ?
                  )
                """,
                (threshold,),
            )
            token_ids = [str(row[0]) for row in cur.fetchall()]
            if not token_ids:
                return
            ts = sql_now_text()
            for token_id in token_ids:
                conn.execute(
                    """
                    UPDATE market_subscriptions
                    SET subscribe_status = 'stale',
                        updated_at = ?
                    WHERE token_id = ?
                    """,
                    (ts, token_id),
                )
            updated = len(token_ids)

        self._run(operation, commit=True)
        return updated


class MarketResolver:
    def __init__(
        self,
        repo: LobRepository,
        *,
        db_path: Optional[str] = None,
        sync_grace_seconds: float = 0.0,
        discovery_callback: Optional[Callable[[Sequence[str], Optional[str]], int]] = None,
    ) -> None:
        self.repo = repo
        self.db_path = db_path
        self.sync_grace_seconds = sync_grace_seconds
        self.discovery_callback = discovery_callback or self._default_discovery_callback
        self._cache: Dict[str, TokenMapping] = {}

    def refresh(self) -> Dict[str, TokenMapping]:
        self._cache = self.repo.load_active_token_mappings()
        return self._cache

    def resolve(self, token_id: str) -> Optional[TokenMapping]:
        if token_id not in self._cache:
            self.refresh()
        return self._cache.get(token_id)

    def ensure(self, token_id: str) -> Optional[TokenMapping]:
        mapping = self.resolve(token_id)
        if mapping is not None:
            return mapping
        self.discovery_callback([token_id], self.db_path)
        self.repo.sync_market_tokens(grace_seconds=self.sync_grace_seconds)
        self.refresh()
        return self._cache.get(token_id)

    @staticmethod
    def _default_discovery_callback(token_ids: Sequence[str], db_path: Optional[str]) -> int:
        if not token_ids:
            return 0
        from market.market_discovery import fetch_and_upsert_markets_for_token_ids

        return fetch_and_upsert_markets_for_token_ids(list(token_ids), db_path or "")


class LOBNormalizer:
    def __init__(self, *, stream_name: str, max_depth_levels: int = 10) -> None:
        self.stream_name = stream_name
        self.max_depth_levels = max(0, int(max_depth_levels))

    def decode_message(self, raw_message: str) -> List[Dict[str, Any]]:
        text = str(raw_message or "").strip()
        if not text or text in {"PONG", "PING"}:
            return []
        payload = json.loads(text)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
        return []

    def normalize_event(self, event: Dict[str, Any], mapping: TokenMapping) -> List[LOBSnapshot]:
        event_type = str(event.get("event_type") or "").strip()
        received_at = sql_now_text()
        if event_type == "book":
            return [self._build_book_snapshot(event, mapping, received_at)]
        if event_type == "best_bid_ask":
            return [self._build_best_bid_ask_snapshot(event, mapping, received_at)]
        if event_type == "last_trade_price":
            return [self._build_last_trade_snapshot(event, mapping, received_at)]
        if event_type == "tick_size_change":
            return [self._build_tick_size_snapshot(event, mapping, received_at)]
        if event_type == "price_change":
            return self._build_price_change_snapshots(event, mapping, received_at)
        return []

    def _dedupe_key(self, *, mapping: TokenMapping, event_type: str, event_timestamp_ms: int, payload: Dict[str, Any], source_hash: Optional[str]) -> str:
        payload_hash = source_hash or sha256_text(safe_json_dumps(payload))
        basis = f"{self.stream_name}|{mapping.market_id}|{mapping.token_id}|{event_type}|{event_timestamp_ms}|{payload_hash}"
        return sha256_text(basis)

    def _build_levels(self, levels: Sequence[Dict[str, Any]], side: str) -> List[LOBLevel]:
        normalized: List[LOBLevel] = []
        for idx, level in enumerate(levels[: self.max_depth_levels]):
            price = parse_decimal_text(level.get("price"))
            size = parse_decimal_text(level.get("size"))
            if price is None or size is None:
                continue
            normalized.append(LOBLevel(side=side, price=price, size=size, level_index=idx))
        return normalized

    def _best_price(self, levels: Sequence[Dict[str, Any]], *, side: str) -> Optional[str]:
        candidates: List[Decimal] = []
        for level in levels:
            price = parse_decimal_text(level.get("price"))
            if price is None:
                continue
            candidates.append(Decimal(price))
        if not candidates:
            return None
        chosen = max(candidates) if side == "bid" else min(candidates)
        return format(chosen.normalize(), "f")

    def _build_book_snapshot(self, event: Dict[str, Any], mapping: TokenMapping, received_at: str) -> LOBSnapshot:
        bids = event.get("bids") or []
        asks = event.get("asks") or []
        best_bid = self._best_price(bids, side="bid")
        best_ask = self._best_price(asks, side="ask")
        event_timestamp_ms = parse_timestamp_ms(event.get("timestamp")) or int(time.time() * 1000)
        raw_payload = safe_json_dumps(event)
        return LOBSnapshot(
            stream_name=self.stream_name,
            market_id=mapping.market_id,
            condition_id=mapping.condition_id,
            token_id=mapping.token_id,
            outcome=mapping.outcome,
            event_type="book",
            event_timestamp_ms=event_timestamp_ms,
            event_time=ms_to_iso(event_timestamp_ms),
            received_at=received_at,
            best_bid=best_bid,
            best_ask=best_ask,
            midpoint=decimal_midpoint(best_bid, best_ask),
            spread=decimal_spread(best_bid, best_ask),
            source_hash=str(event.get("hash") or ""),
            dedupe_key=self._dedupe_key(
                mapping=mapping,
                event_type="book",
                event_timestamp_ms=event_timestamp_ms,
                payload=event,
                source_hash=str(event.get("hash") or ""),
            ),
            raw_payload=raw_payload,
            levels=self._build_levels(bids, "bid") + self._build_levels(asks, "ask"),
        )

    def _build_best_bid_ask_snapshot(self, event: Dict[str, Any], mapping: TokenMapping, received_at: str) -> LOBSnapshot:
        best_bid = parse_decimal_text(event.get("best_bid"))
        best_ask = parse_decimal_text(event.get("best_ask"))
        event_timestamp_ms = parse_timestamp_ms(event.get("timestamp")) or int(time.time() * 1000)
        return LOBSnapshot(
            stream_name=self.stream_name,
            market_id=mapping.market_id,
            condition_id=mapping.condition_id,
            token_id=mapping.token_id,
            outcome=mapping.outcome,
            event_type="best_bid_ask",
            event_timestamp_ms=event_timestamp_ms,
            event_time=ms_to_iso(event_timestamp_ms),
            received_at=received_at,
            best_bid=best_bid,
            best_ask=best_ask,
            midpoint=decimal_midpoint(best_bid, best_ask),
            spread=parse_decimal_text(event.get("spread")) or decimal_spread(best_bid, best_ask),
            dedupe_key=self._dedupe_key(
                mapping=mapping,
                event_type="best_bid_ask",
                event_timestamp_ms=event_timestamp_ms,
                payload=event,
                source_hash=None,
            ),
            raw_payload=safe_json_dumps(event),
        )

    def _build_last_trade_snapshot(self, event: Dict[str, Any], mapping: TokenMapping, received_at: str) -> LOBSnapshot:
        event_timestamp_ms = parse_timestamp_ms(event.get("timestamp")) or int(time.time() * 1000)
        price = parse_decimal_text(event.get("price"))
        size = parse_decimal_text(event.get("size"))
        return LOBSnapshot(
            stream_name=self.stream_name,
            market_id=mapping.market_id,
            condition_id=mapping.condition_id,
            token_id=mapping.token_id,
            outcome=mapping.outcome,
            event_type="last_trade_price",
            event_timestamp_ms=event_timestamp_ms,
            event_time=ms_to_iso(event_timestamp_ms),
            received_at=received_at,
            last_trade_price=price,
            last_trade_side=str(event.get("side") or "").upper() or None,
            last_trade_size=size,
            price=price,
            size=size,
            side=str(event.get("side") or "").upper() or None,
            dedupe_key=self._dedupe_key(
                mapping=mapping,
                event_type="last_trade_price",
                event_timestamp_ms=event_timestamp_ms,
                payload=event,
                source_hash=None,
            ),
            raw_payload=safe_json_dumps(event),
        )

    def _build_tick_size_snapshot(self, event: Dict[str, Any], mapping: TokenMapping, received_at: str) -> LOBSnapshot:
        event_timestamp_ms = parse_timestamp_ms(event.get("timestamp")) or int(time.time() * 1000)
        return LOBSnapshot(
            stream_name=self.stream_name,
            market_id=mapping.market_id,
            condition_id=mapping.condition_id,
            token_id=mapping.token_id,
            outcome=mapping.outcome,
            event_type="tick_size_change",
            event_timestamp_ms=event_timestamp_ms,
            event_time=ms_to_iso(event_timestamp_ms),
            received_at=received_at,
            tick_size=parse_decimal_text(event.get("new_tick_size")),
            dedupe_key=self._dedupe_key(
                mapping=mapping,
                event_type="tick_size_change",
                event_timestamp_ms=event_timestamp_ms,
                payload=event,
                source_hash=None,
            ),
            raw_payload=safe_json_dumps(event),
        )

    def _build_price_change_snapshots(self, event: Dict[str, Any], mapping: TokenMapping, received_at: str) -> List[LOBSnapshot]:
        snapshots: List[LOBSnapshot] = []
        event_timestamp_ms = parse_timestamp_ms(event.get("timestamp")) or int(time.time() * 1000)
        for price_change in event.get("price_changes") or []:
            asset_id = normalize_token_id(price_change.get("asset_id"))
            if asset_id and asset_id != mapping.token_id:
                continue
            best_bid = parse_decimal_text(price_change.get("best_bid"))
            best_ask = parse_decimal_text(price_change.get("best_ask"))
            payload = {
                "event_type": "price_change",
                "market": event.get("market"),
                "price_change": price_change,
                "timestamp": event.get("timestamp"),
            }
            snapshots.append(
                LOBSnapshot(
                    stream_name=self.stream_name,
                    market_id=mapping.market_id,
                    condition_id=mapping.condition_id,
                    token_id=mapping.token_id,
                    outcome=mapping.outcome,
                    event_type="price_change",
                    event_timestamp_ms=event_timestamp_ms,
                    event_time=ms_to_iso(event_timestamp_ms),
                    received_at=received_at,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    midpoint=decimal_midpoint(best_bid, best_ask),
                    spread=decimal_spread(best_bid, best_ask),
                    price=parse_decimal_text(price_change.get("price")),
                    size=parse_decimal_text(price_change.get("size")),
                    side=str(price_change.get("side") or "").upper() or None,
                    source_hash=str(price_change.get("hash") or ""),
                    dedupe_key=self._dedupe_key(
                        mapping=mapping,
                        event_type="price_change",
                        event_timestamp_ms=event_timestamp_ms,
                        payload=payload,
                        source_hash=str(price_change.get("hash") or ""),
                    ),
                    raw_payload=safe_json_dumps(payload),
                )
            )
        return snapshots


def collect_asset_ids(event: Dict[str, Any]) -> List[str]:
    event_type = str(event.get("event_type") or "").strip()
    if event_type in {"book", "best_bid_ask", "last_trade_price", "tick_size_change"}:
        token_id = normalize_token_id(event.get("asset_id"))
        return [token_id] if token_id else []
    if event_type == "price_change":
        return [normalize_token_id(item.get("asset_id")) for item in (event.get("price_changes") or []) if normalize_token_id(item.get("asset_id"))]
    if event_type in {"new_market", "market_resolved"}:
        return [normalize_token_id(item) for item in (event.get("assets_ids") or []) if normalize_token_id(item)]
    return []


class LOBStreamingService:
    def __init__(
        self,
        *,
        repo: LobRepository,
        resolver: MarketResolver,
        normalizer: LOBNormalizer,
        stream_name: str = DEFAULT_STREAM_NAME,
        ws_url: str = DEFAULT_WS_URL,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        sync_interval_seconds: float = DEFAULT_SUBSCRIPTION_SYNC_SECONDS,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        reconnect_base_seconds: float = DEFAULT_RECONNECT_BASE_SECONDS,
        reconnect_max_seconds: float = DEFAULT_RECONNECT_MAX_SECONDS,
        subscription_batch_size: int = DEFAULT_SUBSCRIPTION_BATCH_SIZE,
        new_market_poll_seconds: float = DEFAULT_NEW_MARKET_POLL_SECONDS,
        throttle: Optional[SnapshotThrottle] = None,
        subscription_grace_seconds: float = 0.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.repo = repo
        self.resolver = resolver
        self.normalizer = normalizer
        self.stream_name = stream_name
        self.ws_url = ws_url
        self.heartbeat_seconds = heartbeat_seconds
        self.sync_interval_seconds = sync_interval_seconds
        self.stale_after_seconds = stale_after_seconds
        self.reconnect_base_seconds = reconnect_base_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self.subscription_batch_size = max(1, int(subscription_batch_size))
        self.new_market_poll_seconds = max(1.0, float(new_market_poll_seconds))
        self.subscription_grace_seconds = subscription_grace_seconds
        self.throttle = throttle or SnapshotThrottle()
        self.logger = logger or logging.getLogger(__name__)
        self.stop_event = asyncio.Event()
        self.subscribed_tokens: Set[str] = set()
        self.last_seen_market_id = 0

    def stop(self) -> None:
        self.stop_event.set()

    async def run(self, *, run_seconds: Optional[float] = None, bootstrap_market_limit: int = 0) -> None:
        if websockets is None:
            raise RuntimeError("websockets package is not installed. Please `pip install websockets`.")
        self.logger.info("Initializing LOB schema")
        self.repo.init_schema()
        desired_tokens = self.repo.list_desired_subscription_tokens()
        if desired_tokens:
            self.logger.info("Found %s existing active LOB subscriptions; starting with them", len(desired_tokens))
        else:
            sync_summary = self._sync_active_subscriptions(
                batch_size=200,
                progress_every=500,
                max_markets=DEFAULT_INITIAL_SYNC_MARKET_LIMIT,
            )
            self.resolver.refresh()
            desired_tokens = self.repo.list_desired_subscription_tokens()
            self.logger.info(
                "LOB initial seed ready: active_markets=%s tokens_upserted=%s active_subscriptions=%s deactivated=%s",
                sync_summary.markets_seen,
                sync_summary.tokens_upserted,
                len(desired_tokens),
                sync_summary.subscriptions_deactivated,
            )
        self.last_seen_market_id = self.repo.get_latest_market_id()
        self.logger.info("LOB market watermark initialized at market_id=%s", self.last_seen_market_id)
        if bootstrap_market_limit > 0 and not desired_tokens:
            self._bootstrap_markets(limit=bootstrap_market_limit)
            desired_tokens = self.repo.list_desired_subscription_tokens()
            self.last_seen_market_id = self.repo.get_latest_market_id()
            self.logger.info("LOB bootstrap ready: active_subscriptions=%s", len(desired_tokens))
        if not desired_tokens:
            self.logger.warning("No active LOB subscriptions found in database; waiting for future market syncs")
        if run_seconds and run_seconds > 0:
            asyncio.create_task(self._auto_stop_after(run_seconds))

        reconnect_count = 0
        while not self.stop_event.is_set():
            try:
                await self._run_connection(reconnect_count=reconnect_count)
                reconnect_count = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                reconnect_count += 1
                self.repo.update_stream_state(
                    stream_name=self.stream_name,
                    ws_url=self.ws_url,
                    connection_status="reconnecting",
                    reconnect_count=reconnect_count,
                    notes=str(exc),
                    subscribed_assets=sorted(self.subscribed_tokens),
                )
                sleep_seconds = min(
                    self.reconnect_max_seconds,
                    self.reconnect_base_seconds * (2 ** max(0, reconnect_count - 1)),
                )
                self.logger.warning("LOB stream error: %s; reconnecting in %.1fs", exc, sleep_seconds)
                await asyncio.sleep(sleep_seconds)

    def _sync_active_subscriptions(
        self,
        *,
        batch_size: int,
        progress_every: int,
        max_markets: Optional[int],
    ) -> SyncSummary:
        self.logger.info(
            "Syncing active market subscriptions (grace_seconds=%s, batch_size=%s, max_markets=%s)",
            self.subscription_grace_seconds,
            batch_size,
            max_markets if max_markets is not None else "all",
        )
        last_progress_at = 0.0

        def log_sync_progress(summary: SyncSummary) -> None:
            nonlocal last_progress_at
            now_monotonic = time.monotonic()
            if now_monotonic - last_progress_at < 1.5 and summary.markets_processed > 0:
                return
            last_progress_at = now_monotonic
            self.logger.info(
                "LOB sync progress: processed=%s markets tokens=%s subscriptions=%s batches=%s",
                summary.markets_processed,
                summary.tokens_upserted,
                summary.subscriptions_upserted,
                summary.batches_committed,
            )

        return self.repo.sync_market_tokens(
            grace_seconds=self.subscription_grace_seconds,
            active_only=True,
            batch_size=batch_size,
            progress_every=progress_every,
            max_markets=max_markets,
            progress_callback=log_sync_progress,
        )

    async def _auto_stop_after(self, run_seconds: float) -> None:
        await asyncio.sleep(run_seconds)
        self.stop()

    def _bootstrap_markets(self, *, limit: int) -> None:
        from market.market_discovery import batch_upsert_markets, fetch_all_markets, normalize_market_from_gamma

        self.logger.info("No active subscription candidates found; bootstrapping up to %s active markets", limit)
        raw_markets = fetch_all_markets(
            limit=max(1, int(limit)),
            active_only=True,
            requests_delay=0.0,
        )
        normalized = [normalize_market_from_gamma(item) for item in raw_markets]
        normalized = [item for item in normalized if item]
        if normalized:
            conn = get_connection(self.repo.db_path)
            try:
                batch_upsert_markets(conn, normalized)
                conn.commit()
            finally:
                conn.close()
        self.repo.sync_market_tokens(grace_seconds=self.subscription_grace_seconds)
        self.resolver.refresh()

    async def _run_connection(self, *, reconnect_count: int) -> None:
        self.logger.info("Connecting to %s", self.ws_url)
        self.repo.update_stream_state(
            stream_name=self.stream_name,
            ws_url=self.ws_url,
            connection_status="connecting",
            reconnect_count=reconnect_count,
        )
        async with websockets.connect(self.ws_url, ping_interval=None, close_timeout=10, max_queue=1000) as websocket:
            tokens = self.repo.list_desired_subscription_tokens()
            self.logger.info("Preparing to subscribe %s active LOB tokens", len(tokens))
            self.subscribed_tokens = set()
            await self._subscribe_tokens(websocket, tokens, replace=True)
            self.repo.update_stream_state(
                stream_name=self.stream_name,
                ws_url=self.ws_url,
                connection_status="connected",
                reconnect_count=reconnect_count,
                subscribed_assets=tokens,
            )
            self.logger.info("LOB websocket connected; subscribed_tokens=%s", len(tokens))
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(websocket))
            new_market_task = asyncio.create_task(self._new_market_loop(websocket))
            sync_task = asyncio.create_task(self._sync_loop(websocket))
            try:
                while not self.stop_event.is_set():
                    recv_task = asyncio.create_task(websocket.recv())
                    stop_task = asyncio.create_task(self.stop_event.wait())
                    done, pending = await asyncio.wait(
                        {recv_task, stop_task},
                        timeout=self.heartbeat_seconds * 3,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    if stop_task in done and stop_task.result():
                        if not recv_task.done():
                            recv_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await recv_task
                        break
                    if recv_task not in done:
                        raise RuntimeError("websocket recv timed out")
                    raw_message = recv_task.result()
                    if isinstance(raw_message, bytes):
                        raw_message = raw_message.decode("utf-8")
                    await self._handle_raw_message(websocket, str(raw_message))
            finally:
                heartbeat_task.cancel()
                new_market_task.cancel()
                sync_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
                with contextlib.suppress(asyncio.CancelledError):
                    await new_market_task
                with contextlib.suppress(asyncio.CancelledError):
                    await sync_task

    async def _heartbeat_loop(self, websocket) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(self.heartbeat_seconds)
            await websocket.send("PING")
            self.repo.update_stream_state(
                stream_name=self.stream_name,
                ws_url=self.ws_url,
                connection_status="connected",
                last_heartbeat_at=sql_now_text(),
                subscribed_assets=sorted(self.subscribed_tokens),
            )

    async def _sync_loop(self, websocket) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(self.sync_interval_seconds)
            summary = self._sync_active_subscriptions(
                batch_size=200,
                progress_every=1000,
                max_markets=None,
            )
            self.resolver.refresh()
            desired = set(self.repo.list_desired_subscription_tokens())
            to_subscribe = sorted(desired - self.subscribed_tokens)
            to_unsubscribe = sorted(self.subscribed_tokens - desired)
            if to_subscribe:
                await self._subscribe_tokens(websocket, to_subscribe, replace=False)
            if to_unsubscribe:
                await self._unsubscribe_tokens(websocket, to_unsubscribe)
            stale_marked = self.repo.mark_stale_subscriptions(stale_after_seconds=self.stale_after_seconds)
            self.logger.info(
                "LOB reconcile: markets=%s tokens=%s subscribe_add=%s subscribe_remove=%s stale=%s",
                summary.markets_seen,
                summary.tokens_upserted,
                len(to_subscribe),
                len(to_unsubscribe),
                stale_marked,
            )

    async def _new_market_loop(self, websocket) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(self.new_market_poll_seconds)
            summary, latest_market_id = self.repo.sync_market_tokens_since_id(
                self.last_seen_market_id,
                grace_seconds=self.subscription_grace_seconds,
                batch_size=100,
            )
            if latest_market_id > self.last_seen_market_id:
                self.last_seen_market_id = latest_market_id
            if summary.markets_seen <= 0:
                continue
            self.resolver.refresh()
            desired = set(self.repo.list_desired_subscription_tokens())
            to_subscribe = sorted(desired - self.subscribed_tokens)
            if to_subscribe:
                await self._subscribe_tokens(websocket, to_subscribe, replace=False)
            self.logger.info(
                "LOB fast-market-sync: new_markets=%s new_tokens=%s subscribe_add=%s market_watermark=%s",
                summary.markets_seen,
                summary.tokens_upserted,
                len(to_subscribe),
                self.last_seen_market_id,
            )

    async def _subscribe_tokens(self, websocket, token_ids: Sequence[str], *, replace: bool) -> None:
        if not token_ids:
            return
        batches = list(chunked(list(token_ids), self.subscription_batch_size))
        if replace:
            for idx, batch in enumerate(batches):
                if idx == 0:
                    payload = {
                        "type": "market",
                        "custom_feature_enabled": True,
                        "assets_ids": batch,
                    }
                else:
                    payload = {
                        "operation": "subscribe",
                        "custom_feature_enabled": True,
                        "assets_ids": batch,
                    }
                await websocket.send(safe_json_dumps(payload))
                self.repo.mark_tokens_subscribed(batch, status="subscribed")
                self.subscribed_tokens.update(batch)
        else:
            for batch in batches:
                payload = {
                    "operation": "subscribe",
                    "assets_ids": batch,
                    "custom_feature_enabled": True,
                }
                await websocket.send(safe_json_dumps(payload))
                self.repo.mark_tokens_subscribed(batch, status="subscribed")
                self.subscribed_tokens.update(batch)

    async def _unsubscribe_tokens(self, websocket, token_ids: Sequence[str]) -> None:
        if not token_ids:
            return
        for batch in chunked(list(token_ids), self.subscription_batch_size):
            payload = {
                "operation": "unsubscribe",
                "assets_ids": batch,
            }
            await websocket.send(safe_json_dumps(payload))
            self.repo.mark_tokens_subscribed(batch, status="inactive")
            for token_id in batch:
                self.subscribed_tokens.discard(token_id)

    async def _handle_raw_message(self, websocket, raw_message: str) -> None:
        if raw_message == "PONG":
            self.repo.update_stream_state(
                stream_name=self.stream_name,
                ws_url=self.ws_url,
                connection_status="connected",
                last_heartbeat_at=sql_now_text(),
                subscribed_assets=sorted(self.subscribed_tokens),
            )
            return
        try:
            events = self.normalizer.decode_message(raw_message)
        except json.JSONDecodeError:
            self.logger.debug("Ignoring non-JSON websocket message: %s", raw_message[:200])
            return
        if not events:
            return
        self.repo.update_stream_state(
            stream_name=self.stream_name,
            ws_url=self.ws_url,
            connection_status="connected",
            last_message_at=sql_now_text(),
            subscribed_assets=sorted(self.subscribed_tokens),
        )
        for event in events:
            await self._handle_event(websocket, event)

    async def _handle_event(self, websocket, event: Dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or "").strip()
        if event_type == "new_market":
            await self._handle_new_market_event(websocket, event)
            return
        if event_type == "market_resolved":
            await self._handle_market_resolved_event(websocket, event)
            return
        token_ids = collect_asset_ids(event)
        if not token_ids:
            self.logger.debug("Skipping event without token ids: %s", event_type)
            return
        for token_id in token_ids:
            mapping = self.resolver.ensure(token_id)
            if mapping is None:
                self.repo.write_dead_letter(
                    stream_name=self.stream_name,
                    token_id=token_id,
                    condition_id=str(event.get("market") or "") or None,
                    event_type=event_type or None,
                    reason="unknown_token_mapping",
                    raw_payload=safe_json_dumps(event),
                )
                continue
            snapshots = self.normalizer.normalize_event(event, mapping)
            for snapshot in snapshots:
                if not self.throttle.should_write(snapshot):
                    continue
                inserted = self.repo.insert_snapshot(snapshot)
                if inserted:
                    self.repo.touch_token_message(token_id, status="subscribed", when=snapshot.received_at)

    async def _handle_new_market_event(self, websocket, event: Dict[str, Any]) -> None:
        token_ids = collect_asset_ids(event)
        if token_ids:
            self.resolver.discovery_callback(token_ids, self.repo.db_path)
            self.repo.sync_market_tokens(grace_seconds=self.subscription_grace_seconds)
            self.resolver.refresh()
            desired = set(self.repo.list_desired_subscription_tokens())
            to_subscribe = sorted(desired - self.subscribed_tokens)
            if to_subscribe:
                await self._subscribe_tokens(websocket, to_subscribe, replace=False)

    async def _handle_market_resolved_event(self, websocket, event: Dict[str, Any]) -> None:
        condition_id = str(event.get("market") or "").strip()
        if not condition_id:
            return
        tokens = self.repo.mark_market_inactive(condition_id)
        if tokens:
            await self._unsubscribe_tokens(websocket, tokens)


def build_logger(verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("lob_service")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polymarket LOB streaming + database service")
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser("init-schema", help="Initialize base schema and LOB tables")
    add_db_cli_args(init_cmd)
    init_cmd.add_argument("--sync-subscriptions", action="store_true", help="Also derive market_tokens and market_subscriptions from markets")
    init_cmd.add_argument("--subscription-grace-seconds", type=float, default=0.0, help="Treat recently-ended markets as active for this many seconds")

    sync_cmd = sub.add_parser("sync-markets", help="Refresh market_tokens and market_subscriptions from markets")
    add_db_cli_args(sync_cmd)
    sync_cmd.add_argument("--subscription-grace-seconds", type=float, default=0.0)

    reconcile_cmd = sub.add_parser("reconcile", help="Refresh subscriptions and mark stale markets")
    add_db_cli_args(reconcile_cmd)
    reconcile_cmd.add_argument("--subscription-grace-seconds", type=float, default=0.0)
    reconcile_cmd.add_argument("--stale-after-seconds", type=float, default=DEFAULT_STALE_AFTER_SECONDS)

    live_cmd = sub.add_parser("run-live", help="Run the production LOB websocket writer")
    add_db_cli_args(live_cmd)
    live_cmd.add_argument("--stream-name", default=DEFAULT_STREAM_NAME)
    live_cmd.add_argument("--ws-url", default=DEFAULT_WS_URL)
    live_cmd.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    live_cmd.add_argument("--sync-interval-seconds", type=float, default=DEFAULT_SUBSCRIPTION_SYNC_SECONDS)
    live_cmd.add_argument("--stale-after-seconds", type=float, default=DEFAULT_STALE_AFTER_SECONDS)
    live_cmd.add_argument("--reconnect-base-seconds", type=float, default=DEFAULT_RECONNECT_BASE_SECONDS)
    live_cmd.add_argument("--reconnect-max-seconds", type=float, default=DEFAULT_RECONNECT_MAX_SECONDS)
    live_cmd.add_argument("--subscription-batch-size", type=int, default=DEFAULT_SUBSCRIPTION_BATCH_SIZE)
    live_cmd.add_argument("--new-market-poll-seconds", type=float, default=DEFAULT_NEW_MARKET_POLL_SECONDS)
    live_cmd.add_argument("--max-depth-levels", type=int, default=10)
    live_cmd.add_argument("--subscription-grace-seconds", type=float, default=0.0)
    live_cmd.add_argument("--best-bid-ask-throttle-ms", type=int, default=DEFAULT_BBO_THROTTLE_MS)
    live_cmd.add_argument("--price-change-throttle-ms", type=int, default=DEFAULT_PRICE_CHANGE_THROTTLE_MS)
    live_cmd.add_argument("--bootstrap-market-limit", type=int, default=0, help="Bootstrap active markets with Market Discovery when DB has no active subscriptions")
    live_cmd.add_argument("--run-seconds", type=float, default=0.0, help="Optional bounded runtime for smoke tests")
    live_cmd.add_argument("--verbose", action="store_true")

    return parser


def command_init_schema(args: argparse.Namespace) -> int:
    configure_db_from_args(args)
    repo = LobRepository(db_path=getattr(args, "sqlite_path", None))
    try:
        repo.init_schema()
        if args.sync_subscriptions:
            summary = repo.sync_market_tokens(grace_seconds=args.subscription_grace_seconds)
            print(
                safe_json_dumps(
                    {
                        "db_target": describe_db_target(),
                        "markets_seen": summary.markets_seen,
                        "subscriptions_upserted": summary.subscriptions_upserted,
                        "tokens_upserted": summary.tokens_upserted,
                    }
                )
            )
        else:
            print(safe_json_dumps({"db_target": describe_db_target(), "status": "initialized"}))
    finally:
        repo.close()
    return 0


def command_sync_markets(args: argparse.Namespace) -> int:
    configure_db_from_args(args)
    repo = LobRepository(db_path=getattr(args, "sqlite_path", None))
    try:
        repo.init_schema()
        summary = repo.sync_market_tokens(grace_seconds=args.subscription_grace_seconds)
        print(
            safe_json_dumps(
                {
                    "db_target": describe_db_target(),
                    "markets_seen": summary.markets_seen,
                    "tokens_upserted": summary.tokens_upserted,
                    "subscriptions_upserted": summary.subscriptions_upserted,
                    "subscriptions_deactivated": summary.subscriptions_deactivated,
                }
            )
        )
    finally:
        repo.close()
    return 0


def command_reconcile(args: argparse.Namespace) -> int:
    configure_db_from_args(args)
    repo = LobRepository(db_path=getattr(args, "sqlite_path", None))
    try:
        repo.init_schema()
        sync_summary = repo.sync_market_tokens(grace_seconds=args.subscription_grace_seconds)
        stale = repo.mark_stale_subscriptions(stale_after_seconds=args.stale_after_seconds)
        print(
            safe_json_dumps(
                {
                    "db_target": describe_db_target(),
                    "markets_seen": sync_summary.markets_seen,
                    "tokens_upserted": sync_summary.tokens_upserted,
                    "subscriptions_upserted": sync_summary.subscriptions_upserted,
                    "stale_marked": stale,
                }
            )
        )
    finally:
        repo.close()
    return 0


async def command_run_live_async(args: argparse.Namespace) -> int:
    configure_db_from_args(args)
    logger = build_logger(verbose=args.verbose)
    repo = LobRepository(db_path=getattr(args, "sqlite_path", None))
    resolver = MarketResolver(
        repo,
        db_path=getattr(args, "sqlite_path", None),
        sync_grace_seconds=args.subscription_grace_seconds,
    )
    normalizer = LOBNormalizer(stream_name=args.stream_name, max_depth_levels=args.max_depth_levels)
    service = LOBStreamingService(
        repo=repo,
        resolver=resolver,
        normalizer=normalizer,
        stream_name=args.stream_name,
        ws_url=args.ws_url,
        heartbeat_seconds=args.heartbeat_seconds,
        sync_interval_seconds=args.sync_interval_seconds,
        stale_after_seconds=args.stale_after_seconds,
        reconnect_base_seconds=args.reconnect_base_seconds,
        reconnect_max_seconds=args.reconnect_max_seconds,
        subscription_batch_size=args.subscription_batch_size,
        new_market_poll_seconds=args.new_market_poll_seconds,
        throttle=SnapshotThrottle(
            bbo_ms=args.best_bid_ask_throttle_ms,
            price_change_ms=args.price_change_throttle_ms,
        ),
        subscription_grace_seconds=args.subscription_grace_seconds,
        logger=logger,
    )
    try:
        await service.run(
            run_seconds=(args.run_seconds if args.run_seconds and args.run_seconds > 0 else None),
            bootstrap_market_limit=args.bootstrap_market_limit,
        )
    finally:
        repo.close()
    return 0


def command_run_live(args: argparse.Namespace) -> int:
    return asyncio.run(command_run_live_async(args))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "init-schema":
        return command_init_schema(args)
    if args.command == "sync-markets":
        return command_sync_markets(args)
    if args.command == "reconcile":
        return command_reconcile(args)
    if args.command == "run-live":
        return command_run_live(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
