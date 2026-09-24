"""Compressed ClickHouse sink for high-frequency LocalOrderBook history."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any, Iterable

from quant.core.db import ClickHouseClient, ClickHouseSettings, env_bool, env_first, env_int, safe_identifier

from .local_book import TokenBookIdentity
from .polymarket_adapter import NormalizedBookDelta, NormalizedBookSnapshot


DEFAULT_DELTA_TABLE = "quant_lob_delta_fact"
DEFAULT_LEVEL_TABLE = "quant_lob_level_fact"
DEFAULT_CLICKHOUSE_TIERS = "hot,warm"
DEFAULT_BATCH_SIZE = 250
DEFAULT_FLUSH_INTERVAL_SECONDS = 5
DEFAULT_TTL_DAYS = 30
DEFAULT_SCHEMA_TIMEOUT_SECONDS = 5
DEFAULT_INSERT_TIMEOUT_SECONDS = 10
DEFAULT_REPORT_TIMEOUT_SECONDS = 5
PRICE_SCALE = Decimal("1000000")
SIZE_SCALE = Decimal("1000000")


DELTA_COLUMNS = (
    "event_ts",
    "received_ts",
    "market_id",
    "market_slug",
    "condition_id",
    "token_id",
    "token_side",
    "book_side",
    "action",
    "price_ppm",
    "size_micros",
    "generation",
    "source",
    "event_hash",
    "tier",
)

LEVEL_COLUMNS = (
    "event_ts",
    "received_ts",
    "market_id",
    "market_slug",
    "condition_id",
    "token_id",
    "token_side",
    "generation",
    "book_side",
    "level_index",
    "price_ppm",
    "size_micros",
    "snapshot_hash",
    "source",
    "tier",
)


@dataclass(frozen=True)
class LobClickHouseSettings:
    enabled: bool = field(default_factory=lambda: env_bool("POLYDATA_LOB_CLICKHOUSE_ENABLED", False))
    tiers: frozenset[str] = field(default_factory=lambda: _parse_tiers(env_first("POLYDATA_LOB_CLICKHOUSE_TIERS", default=DEFAULT_CLICKHOUSE_TIERS)))
    delta_table: str = field(default_factory=lambda: safe_identifier(env_first("POLYDATA_LOB_CLICKHOUSE_DELTA_TABLE", default=DEFAULT_DELTA_TABLE)))
    level_table: str = field(default_factory=lambda: safe_identifier(env_first("POLYDATA_LOB_CLICKHOUSE_LEVEL_TABLE", default=DEFAULT_LEVEL_TABLE)))
    batch_size: int = field(default_factory=lambda: max(1, env_int("POLYDATA_LOB_CLICKHOUSE_BATCH_SIZE", DEFAULT_BATCH_SIZE)))
    flush_interval_seconds: int = field(default_factory=lambda: max(1, env_int("POLYDATA_LOB_CLICKHOUSE_FLUSH_INTERVAL_SECONDS", DEFAULT_FLUSH_INTERVAL_SECONDS)))
    ttl_days: int = field(default_factory=lambda: max(1, env_int("POLYDATA_LOB_CLICKHOUSE_TTL_DAYS", DEFAULT_TTL_DAYS)))
    write_levels: bool = field(default_factory=lambda: env_bool("POLYDATA_LOB_CLICKHOUSE_WRITE_LEVELS", True))
    schema_timeout_seconds: int = field(default_factory=lambda: max(1, env_int("POLYDATA_LOB_CLICKHOUSE_SCHEMA_TIMEOUT_SECONDS", DEFAULT_SCHEMA_TIMEOUT_SECONDS)))
    insert_timeout_seconds: int = field(default_factory=lambda: max(1, env_int("POLYDATA_LOB_CLICKHOUSE_INSERT_TIMEOUT_SECONDS", DEFAULT_INSERT_TIMEOUT_SECONDS)))
    report_timeout_seconds: int = field(default_factory=lambda: max(1, env_int("POLYDATA_LOB_CLICKHOUSE_REPORT_TIMEOUT_SECONDS", DEFAULT_REPORT_TIMEOUT_SECONDS)))

    def tier_allowed(self, tier: str) -> bool:
        return str(tier or "").strip().lower() in self.tiers


@dataclass
class ClickHouseLobSinkStats:
    delta_events_seen: int = 0
    delta_rows_enqueued: int = 0
    delta_rows_inserted: int = 0
    delta_rows_skipped_disabled: int = 0
    delta_rows_skipped_tier: int = 0
    delta_rows_skipped_invalid: int = 0
    snapshot_events_seen: int = 0
    level_rows_enqueued: int = 0
    level_rows_inserted: int = 0
    level_rows_skipped_disabled: int = 0
    level_rows_skipped_tier: int = 0
    level_rows_skipped_invalid: int = 0
    flush_count: int = 0
    flush_failure_count: int = 0
    last_enqueue_at: str | None = None
    last_flush_at: str | None = None
    last_insert_at: str | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deltaEventsSeen": self.delta_events_seen,
            "deltaRowsEnqueued": self.delta_rows_enqueued,
            "deltaRowsInserted": self.delta_rows_inserted,
            "deltaRowsSkippedDisabled": self.delta_rows_skipped_disabled,
            "deltaRowsSkippedTier": self.delta_rows_skipped_tier,
            "deltaRowsSkippedInvalid": self.delta_rows_skipped_invalid,
            "snapshotEventsSeen": self.snapshot_events_seen,
            "levelRowsEnqueued": self.level_rows_enqueued,
            "levelRowsInserted": self.level_rows_inserted,
            "levelRowsSkippedDisabled": self.level_rows_skipped_disabled,
            "levelRowsSkippedTier": self.level_rows_skipped_tier,
            "levelRowsSkippedInvalid": self.level_rows_skipped_invalid,
            "flushCount": self.flush_count,
            "flushFailureCount": self.flush_failure_count,
            "lastEnqueueAt": self.last_enqueue_at,
            "lastFlushAt": self.last_flush_at,
            "lastInsertAt": self.last_insert_at,
            "lastError": self.last_error,
        }


class ClickHouseLobSink:
    def __init__(self, *, settings: LobClickHouseSettings | None = None, client: ClickHouseClient | None = None) -> None:
        self.settings = settings or LobClickHouseSettings()
        self.client = client or ClickHouseClient(ClickHouseSettings())
        self._delta_rows: list[str] = []
        self._level_rows: list[str] = []
        self._last_flush_at = time.monotonic()
        self.rows_inserted = 0
        self.flush_count = 0
        self.stats = ClickHouseLobSinkStats()

    @classmethod
    def from_env(cls) -> "ClickHouseLobSink":
        return cls(settings=LobClickHouseSettings())

    def create_schema(self) -> None:
        create_lob_clickhouse_schema(self.client, self.settings)

    def enqueue_delta(
        self,
        *,
        identity: TokenBookIdentity,
        event: NormalizedBookDelta,
        tier: str,
        generation: int = 0,
        source: str = "websocket",
        received_ts_ms: int | None = None,
    ) -> int:
        self.stats.delta_events_seen += 1
        if not self.settings.enabled:
            self.stats.delta_rows_skipped_disabled += 1
            return 0
        if not self.settings.tier_allowed(tier):
            self.stats.delta_rows_skipped_tier += 1
            return 0
        row = delta_event_to_tsv(
            identity=identity,
            event=event,
            tier=tier,
            generation=generation,
            source=source,
            received_ts_ms=received_ts_ms,
        )
        if not row:
            self.stats.delta_rows_skipped_invalid += 1
            return 0
        self._delta_rows.append(row)
        self.stats.delta_rows_enqueued += 1
        self.stats.last_enqueue_at = _utc_now_iso()
        self.flush_if_due()
        return 1

    def enqueue_snapshot_levels(
        self,
        *,
        identity: TokenBookIdentity,
        event: NormalizedBookSnapshot,
        tier: str,
        generation: int = 0,
        source: str = "websocket",
        received_ts_ms: int | None = None,
        depth_limit: int = 12,
    ) -> int:
        self.stats.snapshot_events_seen += 1
        if not self.settings.enabled or not self.settings.write_levels:
            self.stats.level_rows_skipped_disabled += 1
            return 0
        if not self.settings.tier_allowed(tier):
            self.stats.level_rows_skipped_tier += 1
            return 0
        rows = snapshot_event_to_level_tsv(
            identity=identity,
            event=event,
            tier=tier,
            generation=generation,
            source=source,
            received_ts_ms=received_ts_ms,
            depth_limit=depth_limit,
        )
        if not rows:
            self.stats.level_rows_skipped_invalid += 1
            return 0
        self._level_rows.extend(rows)
        self.stats.level_rows_enqueued += len(rows)
        self.stats.last_enqueue_at = _utc_now_iso()
        self.flush_if_due()
        return len(rows)

    def flush_if_due(self, *, force: bool = False) -> int:
        row_count = len(self._delta_rows) + len(self._level_rows)
        if row_count <= 0:
            return 0
        due = force or row_count >= self.settings.batch_size or (time.monotonic() - self._last_flush_at) >= self.settings.flush_interval_seconds
        if not due:
            return 0
        return self.flush()

    def flush(self) -> int:
        inserted = 0
        if self._delta_rows:
            rows = self._delta_rows
            self._delta_rows = []
            self._insert_tsv(self.settings.delta_table, DELTA_COLUMNS, rows)
            inserted += len(rows)
            self.stats.delta_rows_inserted += len(rows)
        if self._level_rows:
            rows = self._level_rows
            self._level_rows = []
            self._insert_tsv(self.settings.level_table, LEVEL_COLUMNS, rows)
            inserted += len(rows)
            self.stats.level_rows_inserted += len(rows)
        self.rows_inserted += inserted
        self.flush_count += 1 if inserted else 0
        self.stats.flush_count = self.flush_count
        self._last_flush_at = time.monotonic()
        self.stats.last_flush_at = _utc_now_iso()
        if inserted:
            self.stats.last_insert_at = self.stats.last_flush_at
        return inserted

    def buffered_rows(self) -> int:
        return len(self._delta_rows) + len(self._level_rows)

    def buffered_rows_by_table(self) -> dict[str, int]:
        return {"delta": len(self._delta_rows), "level": len(self._level_rows)}

    def mark_flush_failure(self, error: Exception | str) -> None:
        self.stats.flush_failure_count += 1
        self.stats.last_error = str(error)[:240]

    def status_snapshot(self) -> dict[str, Any]:
        stats = self.stats.as_dict()
        buffered = self.buffered_rows_by_table()
        stats.update(
            {
                "enabled": self.settings.enabled,
                "tiers": sorted(self.settings.tiers),
                "deltaTable": self.settings.delta_table,
                "levelTable": self.settings.level_table,
                "ttlDays": self.settings.ttl_days,
                "batchSize": self.settings.batch_size,
                "flushIntervalSeconds": self.settings.flush_interval_seconds,
                "schemaTimeoutSeconds": self.settings.schema_timeout_seconds,
                "insertTimeoutSeconds": self.settings.insert_timeout_seconds,
                "writeLevels": self.settings.write_levels,
                "bufferedRows": buffered["delta"] + buffered["level"],
                "bufferedDeltaRows": buffered["delta"],
                "bufferedLevelRows": buffered["level"],
                "rowsInserted": self.rows_inserted,
            }
        )
        return stats

    def _insert_tsv(self, table: str, columns: Iterable[str], rows: list[str]) -> None:
        if not rows:
            return
        table_name = safe_identifier(table)
        column_sql = ", ".join(safe_identifier(column) for column in columns)
        payload = "\n".join(rows) + "\n"
        self.client.execute(
            f"INSERT INTO {table_name} ({column_sql}) FORMAT TabSeparated",
            stdin=payload,
            timeout_seconds=self.settings.insert_timeout_seconds,
        )


def create_lob_clickhouse_schema(client: ClickHouseClient | None = None, settings: LobClickHouseSettings | None = None) -> None:
    settings = settings or LobClickHouseSettings()
    client = client or ClickHouseClient(ClickHouseSettings())
    delta_table = safe_identifier(settings.delta_table)
    level_table = safe_identifier(settings.level_table)
    ttl_days = max(1, int(settings.ttl_days))
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {delta_table} (
            event_date Date MATERIALIZED toDate(event_ts),
            event_ts DateTime64(3, 'UTC'),
            received_ts DateTime64(3, 'UTC'),
            market_id UInt64,
            market_slug LowCardinality(String),
            condition_id String,
            token_id String,
            token_side UInt8,
            book_side UInt8,
            action UInt8,
            price_ppm UInt32,
            size_micros UInt64,
            generation UInt32,
            source UInt8,
            event_hash FixedString(20),
            tier LowCardinality(String)
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(event_date)
        ORDER BY (market_id, token_id, event_ts, book_side, price_ppm)
        TTL event_date + INTERVAL {ttl_days} DAY DELETE
        SETTINGS index_granularity = 8192
        """,
        timeout_seconds=settings.schema_timeout_seconds,
    )
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {level_table} (
            event_date Date MATERIALIZED toDate(event_ts),
            event_ts DateTime64(3, 'UTC'),
            received_ts DateTime64(3, 'UTC'),
            market_id UInt64,
            market_slug LowCardinality(String),
            condition_id String,
            token_id String,
            token_side UInt8,
            generation UInt32,
            book_side UInt8,
            level_index UInt8,
            price_ppm UInt32,
            size_micros UInt64,
            snapshot_hash FixedString(20),
            source UInt8,
            tier LowCardinality(String)
        )
        ENGINE = MergeTree
        PARTITION BY toYYYYMM(event_date)
        ORDER BY (market_id, token_id, event_ts, book_side, level_index)
        TTL event_date + INTERVAL {ttl_days} DAY DELETE
        SETTINGS index_granularity = 8192
        """,
        timeout_seconds=settings.schema_timeout_seconds,
    )


def delta_event_to_tsv(
    *,
    identity: TokenBookIdentity,
    event: NormalizedBookDelta,
    tier: str,
    generation: int = 0,
    source: str = "websocket",
    received_ts_ms: int | None = None,
) -> str | None:
    price_ppm = _scaled_uint(event.price, PRICE_SCALE)
    size_micros = _scaled_uint(event.size, SIZE_SCALE)
    if price_ppm is None or size_micros is None:
        return None
    action = 2 if event.size <= 0 else 1
    values = [
        _ch_datetime64(event.event_ts_ms),
        _ch_datetime64(received_ts_ms or int(time.time() * 1000)),
        int(identity.market_id or 0),
        identity.market_slug or "",
        identity.condition_id or "",
        identity.token_id,
        _token_side_code(identity.outcome),
        _book_side_code(event.side),
        action,
        price_ppm,
        size_micros,
        max(0, int(generation or 0)),
        _source_code(source),
        _event_hash(event.source_hash, identity.token_id, event.side, str(event.price), str(event.size), str(event.event_ts_ms)),
        str(tier or "unknown").lower(),
    ]
    return "\t".join(_tsv_escape(value) for value in values)


def snapshot_event_to_level_tsv(
    *,
    identity: TokenBookIdentity,
    event: NormalizedBookSnapshot,
    tier: str,
    generation: int = 0,
    source: str = "websocket",
    received_ts_ms: int | None = None,
    depth_limit: int = 12,
) -> list[str]:
    rows: list[str] = []
    snapshot_hash = _event_hash(event.source_hash, identity.token_id, "snapshot", str(event.event_ts_ms))
    for book_side, levels in (("bid", sorted(event.bids, reverse=True)), ("ask", sorted(event.asks))):
        for level_index, (price, size) in enumerate(levels[: max(1, int(depth_limit))]):
            price_ppm = _scaled_uint(price, PRICE_SCALE)
            size_micros = _scaled_uint(size, SIZE_SCALE)
            if price_ppm is None or size_micros is None or size_micros <= 0:
                continue
            values = [
                _ch_datetime64(event.event_ts_ms),
                _ch_datetime64(received_ts_ms or int(time.time() * 1000)),
                int(identity.market_id or 0),
                identity.market_slug or "",
                identity.condition_id or "",
                identity.token_id,
                _token_side_code(identity.outcome),
                max(0, int(generation or 0)),
                _book_side_code(book_side),
                int(level_index),
                price_ppm,
                size_micros,
                snapshot_hash,
                _source_code(source),
                str(tier or "unknown").lower(),
            ]
            rows.append("\t".join(_tsv_escape(value) for value in values))
    return rows


def clickhouse_lob_storage_report(client: ClickHouseClient | None = None, settings: LobClickHouseSettings | None = None) -> dict[str, Any]:
    settings = settings or LobClickHouseSettings()
    client = client or ClickHouseClient(ClickHouseSettings())
    table_names = [safe_identifier(settings.delta_table), safe_identifier(settings.level_table)]
    quoted_tables = ", ".join("'" + table.replace("\\", "\\\\").replace("'", "\\'") + "'" for table in table_names)
    parts_rows = client.query_json_rows(
        f"""
        SELECT
            table,
            sum(rows) AS rows,
            sum(bytes_on_disk) AS bytes_on_disk
        FROM system.parts
        WHERE active AND database = currentDatabase() AND table IN ({quoted_tables})
        GROUP BY table
        """,
        timeout_seconds=settings.report_timeout_seconds,
    )
    by_table: dict[str, dict[str, Any]] = {
        table: {"table": table, "rows": 0, "bytesOnDisk": 0, "rows1h": 0, "estimatedBytesPerRow": 0.0}
        for table in table_names
    }
    for row in parts_rows:
        table = str(row.get("table") or "")
        if table in by_table:
            rows = int(row.get("rows") or 0)
            bytes_on_disk = int(row.get("bytes_on_disk") or 0)
            by_table[table].update(
                {
                    "rows": rows,
                    "bytesOnDisk": bytes_on_disk,
                    "estimatedBytesPerRow": (bytes_on_disk / rows) if rows > 0 else 0.0,
                }
            )
    for table in table_names:
        try:
            rows_1h = int(
                client.query_scalar(
                    f"SELECT count() FROM {table} WHERE event_ts >= now() - INTERVAL 1 HOUR",
                    timeout_seconds=settings.report_timeout_seconds,
                )
                or 0
            )
        except Exception:
            rows_1h = 0
        by_table[table]["rows1h"] = rows_1h
        by_table[table]["projectedRowsPerDay"] = rows_1h * 24
        by_table[table]["projectedBytesPerDay"] = int(rows_1h * 24 * float(by_table[table]["estimatedBytesPerRow"] or 0))
        by_table[table]["projectedRetentionBytes"] = int(by_table[table]["projectedBytesPerDay"] * max(1, int(settings.ttl_days)))
    total_bytes = sum(int(item["bytesOnDisk"]) for item in by_table.values())
    return {
        "enabled": settings.enabled,
        "tiers": sorted(settings.tiers),
        "ttlDays": settings.ttl_days,
        "tables": list(by_table.values()),
        "totalBytesOnDisk": total_bytes,
        "projectedBytesPerDay": sum(int(item["projectedBytesPerDay"]) for item in by_table.values()),
        "projectedRetentionBytes": sum(int(item["projectedRetentionBytes"]) for item in by_table.values()),
    }


def _parse_tiers(value: str) -> frozenset[str]:
    tiers = {item.strip().lower() for item in str(value or "").replace("|", ",").split(",") if item.strip()}
    return frozenset(tier for tier in tiers if tier in {"hot", "warm", "cold"}) or frozenset({"hot", "warm"})


def _scaled_uint(value: Decimal, scale: Decimal) -> int | None:
    try:
        scaled = (Decimal(value) * scale).to_integral_value(rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if scaled < 0:
        return None
    return int(scaled)


def _ch_datetime64(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:23]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _token_side_code(outcome: str) -> int:
    text = str(outcome or "").strip().upper()
    if text == "YES":
        return 1
    if text == "NO":
        return 2
    return 0


def _book_side_code(side: str) -> int:
    text = str(side or "").strip().lower()
    if text == "bid":
        return 1
    if text == "ask":
        return 2
    return 0


def _source_code(source: str) -> int:
    text = str(source or "").strip().lower()
    if text == "websocket":
        return 1
    if text in {"rest", "rest-book"}:
        return 2
    return 0


def _event_hash(*parts: Any) -> str:
    for part in parts:
        text = str(part or "").strip()
        if len(text) >= 20:
            return text[:20]
    digest = hashlib.sha1("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()
    return digest[:20]


def _tsv_escape(value: Any) -> str:
    if value is None:
        return r"\N"
    text = str(value)
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
