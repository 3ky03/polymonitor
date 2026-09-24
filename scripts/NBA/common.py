#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers for the NBA-only LOB pipeline."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

UTC = timezone.utc
DEFAULT_DATA_ROOT = Path("/data/hy/myPolyDB/nba_lob")
DEFAULT_RUNTIME_DB_NAME = "runtime_state.sqlite"
PRICE_SCALE = 1_000_000
SIZE_SCALE = 1_000_000


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def date_from_timestamp_ms(value: Any) -> str:
    try:
        timestamp_ms = int(value)
    except (TypeError, ValueError):
        return utc_now().date().isoformat()
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC).date().isoformat()


def safe_json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ensure_data_root(data_root: Path | str) -> Path:
    root = Path(data_root).expanduser().resolve()
    for relative in (
        "catalog",
        "snapshots",
        "levels",
        "state",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return root


def parse_json_list(value: Any) -> List[str]:
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


def normalize_token_id(value: Any) -> str:
    return str(value or "").strip()


def runtime_db_path(data_root: Path | str) -> Path:
    root = ensure_data_root(data_root)
    return root / "state" / DEFAULT_RUNTIME_DB_NAME


def open_runtime_connection(data_root: Path | str) -> sqlite3.Connection:
    db_path = runtime_db_path(data_root)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def market_catalog_path(data_root: Path | str) -> Path:
    return ensure_data_root(data_root) / "catalog" / "market_catalog.parquet"


def token_catalog_path(data_root: Path | str) -> Path:
    return ensure_data_root(data_root) / "catalog" / "token_catalog.parquet"


def catalog_state_path(data_root: Path | str) -> Path:
    return ensure_data_root(data_root) / "catalog" / "catalog_state.json"


def snapshot_glob(data_root: Path | str) -> str:
    return str(ensure_data_root(data_root) / "snapshots" / "*" / "*" / "*.parquet")


def level_glob(data_root: Path | str) -> str:
    return str(ensure_data_root(data_root) / "levels" / "*" / "*.parquet")


def _import_pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("pyarrow is required for NBA parquet storage. Please install pyarrow.") from exc
    return pa, pq


def load_parquet_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    pa, pq = _import_pyarrow()
    table = pq.read_table(path)
    if table.num_rows <= 0:
        return []
    return table.to_pylist()


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_bytes(payload)
    tmp_path.replace(target)


def write_json_atomic(target: Path, payload: Dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    _atomic_write_bytes(target, encoded)


def write_table_atomic(target: Path, rows: Sequence[Dict[str, Any]], schema) -> None:
    pa, pq = _import_pyarrow()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    table = pa.Table.from_pylist(list(rows), schema=schema)
    pq.write_table(table, tmp_path, compression="snappy")
    tmp_path.replace(target)


MARKET_CATALOG_COLUMNS: Tuple[str, ...] = (
    "market_id",
    "condition_id",
    "slug",
    "title",
    "yes_token_id",
    "no_token_id",
    "clob_token_ids",
    "tags",
    "enable_neg_risk",
    "active",
    "end_date",
    "discovered_at",
    "last_seen_at",
)

TOKEN_CATALOG_COLUMNS: Tuple[str, ...] = (
    "market_id",
    "condition_id",
    "slug",
    "title",
    "token_id",
    "outcome",
    "outcome_index",
    "active",
    "end_date",
    "discovered_at",
    "last_seen_at",
)


def market_catalog_schema():
    pa, _ = _import_pyarrow()
    return pa.schema(
        [
            ("market_id", pa.int64()),
            ("condition_id", pa.string()),
            ("slug", pa.string()),
            ("title", pa.string()),
            ("yes_token_id", pa.string()),
            ("no_token_id", pa.string()),
            ("clob_token_ids", pa.string()),
            ("tags", pa.string()),
            ("enable_neg_risk", pa.int8()),
            ("active", pa.int8()),
            ("end_date", pa.string()),
            ("discovered_at", pa.string()),
            ("last_seen_at", pa.string()),
        ]
    )


def token_catalog_schema():
    pa, _ = _import_pyarrow()
    return pa.schema(
        [
            ("market_id", pa.int64()),
            ("condition_id", pa.string()),
            ("slug", pa.string()),
            ("title", pa.string()),
            ("token_id", pa.string()),
            ("outcome", pa.string()),
            ("outcome_index", pa.int32()),
            ("active", pa.int8()),
            ("end_date", pa.string()),
            ("discovered_at", pa.string()),
            ("last_seen_at", pa.string()),
        ]
    )


def snapshot_schema():
    pa, _ = _import_pyarrow()
    return pa.schema(
        [
            ("dedupe_key", pa.string()),
            ("market_id", pa.int64()),
            ("token_id", pa.string()),
            ("event_type", pa.string()),
            ("event_timestamp_ms", pa.int64()),
            ("best_bid_ppm", pa.int32()),
            ("best_ask_ppm", pa.int32()),
            ("last_trade_price_ppm", pa.int32()),
            ("price_ppm", pa.int32()),
            ("size_micros", pa.int64()),
            ("side", pa.string()),
        ]
    )


def level_schema():
    pa, _ = _import_pyarrow()
    return pa.schema(
        [
            ("dedupe_key", pa.string()),
            ("side", pa.string()),
            ("level_index", pa.int32()),
            ("price_ppm", pa.int32()),
            ("size_micros", pa.int64()),
        ]
    )


def load_market_catalog_rows(data_root: Path | str) -> List[Dict[str, Any]]:
    return load_parquet_rows(market_catalog_path(data_root))


def load_token_catalog_rows(data_root: Path | str) -> List[Dict[str, Any]]:
    return load_parquet_rows(token_catalog_path(data_root))


def load_catalog_state(data_root: Path | str) -> Dict[str, Any]:
    path = catalog_state_path(data_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_catalog_artifacts(
    data_root: Path | str,
    market_rows: Sequence[Dict[str, Any]],
    token_rows: Sequence[Dict[str, Any]],
    state_payload: Dict[str, Any],
) -> None:
    root = ensure_data_root(data_root)
    write_table_atomic(root / "catalog" / "market_catalog.parquet", market_rows, market_catalog_schema())
    write_table_atomic(root / "catalog" / "token_catalog.parquet", token_rows, token_catalog_schema())
    write_json_atomic(root / "catalog" / "catalog_state.json", state_payload)


@dataclass
class DesiredTokenSummary:
    tokens_upserted: int = 0
    tokens_deactivated: int = 0


class RuntimeStateStore:
    """Control-plane SQLite state for NBA LOB runtime."""

    def __init__(self, data_root: Path | str) -> None:
        self.data_root = ensure_data_root(data_root)
        self.db_path = runtime_db_path(data_root)
        self.conn: Optional[sqlite3.Connection] = None

    def _ensure_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = open_runtime_connection(self.data_root)
            self.init_schema()
        return self.conn

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def init_schema(self) -> None:
        conn = self.conn or open_runtime_connection(self.data_root)
        close_after = self.conn is None
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stream_state (
                    stream_name TEXT PRIMARY KEY,
                    ws_url TEXT,
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS token_subscriptions (
                    token_id TEXT PRIMARY KEY,
                    market_id INTEGER NOT NULL,
                    condition_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    outcome_index INTEGER NOT NULL DEFAULT 0,
                    desired_active INTEGER NOT NULL DEFAULT 1,
                    subscribed_active INTEGER NOT NULL DEFAULT 0,
                    subscribe_status TEXT NOT NULL DEFAULT 'pending',
                    last_subscribed_at TEXT,
                    last_message_at TEXT,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dead_letters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stream_name TEXT NOT NULL,
                    token_id TEXT,
                    condition_id TEXT,
                    event_type TEXT,
                    reason TEXT NOT NULL,
                    payload_hash TEXT NOT NULL UNIQUE,
                    raw_payload TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    resolved INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshot_dedupe (
                    dedupe_key TEXT PRIMARY KEY,
                    stream_name TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    token_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_timestamp_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS catalog_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_token_subscriptions_desired ON token_subscriptions(desired_active, subscribed_active)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dead_letters_reason ON dead_letters(reason, resolved)"
            )
            conn.commit()
        finally:
            if close_after:
                conn.close()

    def upsert_desired_tokens(self, token_rows: Sequence[Dict[str, Any]]) -> DesiredTokenSummary:
        conn = self._ensure_conn()
        summary = DesiredTokenSummary()
        now = iso_now()
        expected: set[str] = set()
        for row in token_rows:
            token_id = normalize_token_id(row.get("token_id"))
            if not token_id:
                continue
            expected.add(token_id)
            conn.execute(
                """
                INSERT INTO token_subscriptions (
                    token_id, market_id, condition_id, outcome, outcome_index,
                    desired_active, subscribed_active, subscribe_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(token_id) DO UPDATE SET
                    market_id=excluded.market_id,
                    condition_id=excluded.condition_id,
                    outcome=excluded.outcome,
                    outcome_index=excluded.outcome_index,
                    desired_active=excluded.desired_active,
                    updated_at=excluded.updated_at,
                    subscribe_status=CASE
                        WHEN token_subscriptions.subscribe_status = 'inactive' AND excluded.desired_active = 1 THEN 'pending'
                        WHEN token_subscriptions.subscribe_status IN ('error', 'stale') AND excluded.desired_active = 1 THEN token_subscriptions.subscribe_status
                        ELSE token_subscriptions.subscribe_status
                    END
                """,
                (
                    token_id,
                    int(row.get("market_id") or 0),
                    str(row.get("condition_id") or ""),
                    str(row.get("outcome") or ""),
                    int(row.get("outcome_index") or 0),
                    1 if int(row.get("active") or 0) else 0,
                    0,
                    "pending" if int(row.get("active") or 0) else "inactive",
                    now,
                ),
            )
            summary.tokens_upserted += 1

        current = conn.execute(
            """
            SELECT token_id
            FROM token_subscriptions
            WHERE desired_active = 1 OR subscribed_active = 1
            """
        ).fetchall()
        for row in current:
            token_id = str(row[0])
            if token_id in expected:
                continue
            conn.execute(
                """
                UPDATE token_subscriptions
                SET desired_active = 0,
                    subscribed_active = 0,
                    subscribe_status = 'inactive',
                    updated_at = ?
                WHERE token_id = ?
                """,
                (now, token_id),
            )
            summary.tokens_deactivated += 1
        conn.commit()
        return summary

    def list_desired_tokens(self) -> List[str]:
        conn = self._ensure_conn()
        rows = conn.execute(
            """
            SELECT token_id
            FROM token_subscriptions
            WHERE desired_active = 1
            ORDER BY market_id ASC, outcome_index ASC
            """
        ).fetchall()
        return [str(row[0]) for row in rows]

    def mark_tokens_subscribed(self, token_ids: Sequence[str], *, status: str = "subscribed") -> None:
        if not token_ids:
            return
        conn = self._ensure_conn()
        now = iso_now()
        conn.executemany(
            """
            UPDATE token_subscriptions
            SET subscribed_active = CASE WHEN ? = 'inactive' THEN 0 ELSE 1 END,
                subscribe_status = ?,
                last_subscribed_at = ?,
                updated_at = ?,
                error_count = CASE WHEN ? = 'error' THEN error_count + 1 ELSE error_count END
            WHERE token_id = ?
            """,
            [(status, status, now, now, status, token_id) for token_id in token_ids],
        )
        conn.commit()

    def touch_token_message(self, token_id: str, *, status: str = "subscribed", when: Optional[str] = None) -> None:
        conn = self._ensure_conn()
        ts = when or iso_now()
        conn.execute(
            """
            UPDATE token_subscriptions
            SET last_message_at = ?,
                subscribe_status = ?,
                updated_at = ?
            WHERE token_id = ?
            """,
            (ts, status, ts, token_id),
        )
        conn.commit()

    def update_stream_state(
        self,
        *,
        stream_name: str,
        ws_url: str,
        connection_status: str,
        reconnect_count: int = 0,
        last_heartbeat_at: Optional[str] = None,
        last_message_at: Optional[str] = None,
        subscribed_assets: Optional[Sequence[str]] = None,
        notes: Optional[str] = None,
    ) -> None:
        conn = self._ensure_conn()
        conn.execute(
            """
            INSERT INTO stream_state (
                stream_name, ws_url, connection_status, reconnect_count,
                last_heartbeat_at, last_message_at, subscribed_assets, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stream_name) DO UPDATE SET
                ws_url=excluded.ws_url,
                connection_status=excluded.connection_status,
                reconnect_count=excluded.reconnect_count,
                last_heartbeat_at=COALESCE(excluded.last_heartbeat_at, stream_state.last_heartbeat_at),
                last_message_at=COALESCE(excluded.last_message_at, stream_state.last_message_at),
                subscribed_assets=COALESCE(excluded.subscribed_assets, stream_state.subscribed_assets),
                notes=COALESCE(excluded.notes, stream_state.notes),
                updated_at=excluded.updated_at
            """,
            (
                stream_name,
                ws_url,
                connection_status,
                reconnect_count,
                last_heartbeat_at,
                last_message_at,
                safe_json_dumps(list(subscribed_assets)) if subscribed_assets is not None else None,
                notes,
                iso_now(),
            ),
        )
        conn.commit()

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
        conn = self._ensure_conn()
        now = iso_now()
        payload_hash = sha256_text(raw_payload)
        conn.execute(
            """
            INSERT INTO dead_letters (
                stream_name, token_id, condition_id, event_type, reason, payload_hash,
                raw_payload, first_seen_at, last_seen_at, attempts, resolved
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
            ON CONFLICT(payload_hash) DO UPDATE SET
                last_seen_at=excluded.last_seen_at,
                attempts=dead_letters.attempts + 1,
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
        conn.commit()

    def claim_dedupe(
        self,
        *,
        dedupe_key: str,
        stream_name: str,
        market_id: int,
        token_id: str,
        event_type: str,
        event_timestamp_ms: int,
    ) -> bool:
        conn = self._ensure_conn()
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO snapshot_dedupe (
                dedupe_key, stream_name, market_id, token_id, event_type, event_timestamp_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (dedupe_key, stream_name, market_id, token_id, event_type, event_timestamp_ms, iso_now()),
        )
        conn.commit()
        return conn.total_changes > before

    def save_catalog_state(self, key: str, payload: Dict[str, Any]) -> None:
        conn = self._ensure_conn()
        conn.execute(
            """
            INSERT INTO catalog_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, safe_json_dumps(payload), iso_now()),
        )
        conn.commit()

    def load_catalog_state(self, key: str) -> Dict[str, Any]:
        conn = self._ensure_conn()
        row = conn.execute("SELECT value FROM catalog_state WHERE key = ?", (key,)).fetchone()
        if not row or not row[0]:
            return {}
        try:
            return json.loads(str(row[0]))
        except json.JSONDecodeError:
            return {}

    def mark_stale_subscriptions(self, *, stale_after_seconds: float) -> int:
        conn = self._ensure_conn()
        threshold = datetime.fromtimestamp(time.time() - max(0.0, stale_after_seconds), tz=UTC).isoformat()
        rows = conn.execute(
            """
            SELECT token_id
            FROM token_subscriptions
            WHERE desired_active = 1
              AND subscribed_active = 1
              AND (last_message_at IS NULL OR last_message_at < ?)
            """,
            (threshold,),
        ).fetchall()
        token_ids = [str(row[0]) for row in rows]
        if not token_ids:
            return 0
        now = iso_now()
        conn.executemany(
            """
            UPDATE token_subscriptions
            SET subscribe_status = 'stale',
                updated_at = ?
            WHERE token_id = ?
            """,
            [(now, token_id) for token_id in token_ids],
        )
        conn.commit()
        return len(token_ids)


class ParquetLOBSink:
    """Buffered parquet append sink for NBA snapshots and levels."""

    def __init__(self, data_root: Path | str, *, batch_size: int = 250) -> None:
        self.data_root = ensure_data_root(data_root)
        self.batch_size = max(1, int(batch_size))
        self._snapshot_buffers: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self._level_buffers: Dict[str, List[Dict[str, Any]]] = {}

    def append_snapshot(self, record: Dict[str, Any]) -> None:
        day = date_from_timestamp_ms(record.get("event_timestamp_ms"))
        event_type = str(record.get("event_type") or "unknown")
        key = (day, event_type)
        bucket = self._snapshot_buffers.setdefault(key, [])
        bucket.append(record)
        if len(bucket) >= self.batch_size:
            self._flush_snapshot_partition(day, event_type)

    def append_levels(self, rows: Sequence[Dict[str, Any]]) -> None:
        if not rows:
            return
        day = date_from_timestamp_ms(rows[0].get("event_timestamp_ms"))
        bucket = self._level_buffers.setdefault(day, [])
        bucket.extend(rows)
        if len(bucket) >= self.batch_size:
            self._flush_level_partition(day)

    def flush(self) -> None:
        for day, event_type in list(self._snapshot_buffers.keys()):
            self._flush_snapshot_partition(day, event_type)
        for day in list(self._level_buffers.keys()):
            self._flush_level_partition(day)

    def close(self) -> None:
        self.flush()

    def _flush_snapshot_partition(self, day: str, event_type: str) -> None:
        rows = self._snapshot_buffers.get((day, event_type)) or []
        if not rows:
            return
        path = (
            self.data_root
            / "snapshots"
            / f"dt={day}"
            / f"event_type={event_type}"
            / f"part-{int(time.time() * 1000)}-{uuid.uuid4().hex}.parquet"
        )
        self._write_partition(path, rows, snapshot_schema())
        self._snapshot_buffers[(day, event_type)] = []

    def _flush_level_partition(self, day: str) -> None:
        rows = self._level_buffers.get(day) or []
        if not rows:
            return
        path = (
            self.data_root
            / "levels"
            / f"dt={day}"
            / f"part-{int(time.time() * 1000)}-{uuid.uuid4().hex}.parquet"
        )
        self._write_partition(path, rows, level_schema())
        self._level_buffers[day] = []

    def _write_partition(self, path: Path, rows: Sequence[Dict[str, Any]], schema) -> None:
        pa, pq = _import_pyarrow()
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(list(rows), schema=schema)
        pq.write_table(table, path, compression="snappy")
