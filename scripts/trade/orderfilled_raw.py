#!/usr/bin/env python3
"""Raw OrderFilled storage and audit helpers.

The raw table is the chain-fact layer. It deliberately does not depend on
market discovery: if a supported Polymarket exchange emits OrderFilled, this
table can store it before tokenId -> marketId mapping is known.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from db import get_backend


ORDERFILLED_RAW_TABLE = "orderfilled_raw"
ORDERFILLED_SYNC_WINDOWS_TABLE = "orderfilled_sync_windows"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "hex"):
        text = value.hex()
        return text if str(text).startswith("0x") else "0x" + str(text)
    return str(value)


def normalize_hex(value: Any, *, prefix: bool = True) -> str:
    text = _text(value).strip().lower()
    if not text:
        return ""
    if text.startswith("0x"):
        body = text[2:]
    else:
        body = text
    return ("0x" + body) if prefix else body


def normalize_address(value: Any) -> str:
    text = normalize_hex(value, prefix=True)
    return text if len(text) == 42 else text


def raw_decimal_text(value: Any) -> str:
    if value is None:
        return "0"
    try:
        return format(Decimal(str(value)), "f")
    except Exception:
        return str(value)


def normalize_block_time(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip()
    if not text:
        return ""
    normalized = text.replace(" UTC", "Z").replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ensure_orderfilled_raw_schema(conn) -> None:
    backend = get_backend()
    if backend == "mysql":
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ORDERFILLED_RAW_TABLE} (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                contract CHAR(42) NOT NULL,
                event_version VARCHAR(32) NOT NULL DEFAULT 'legacy',
                event_topic CHAR(66) NOT NULL,
                tx_hash CHAR(66) NOT NULL,
                log_index BIGINT NOT NULL,
                block_number BIGINT NOT NULL,
                block_time VARCHAR(40),
                order_hash CHAR(66),
                maker CHAR(42) NOT NULL,
                taker CHAR(42) NOT NULL,
                maker_asset_id VARCHAR(128) NOT NULL,
                taker_asset_id VARCHAR(128) NOT NULL,
                token_id VARCHAR(128) NOT NULL,
                side VARCHAR(16) NOT NULL,
                price DECIMAL(38, 18) NOT NULL DEFAULT 0,
                size DECIMAL(38, 18) NOT NULL DEFAULT 0,
                maker_amount VARCHAR(128) NOT NULL DEFAULT '0',
                taker_amount VARCHAR(128) NOT NULL DEFAULT '0',
                fee VARCHAR(128) NOT NULL DEFAULT '0',
                raw_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_orderfilled_raw_contract_tx_log (contract, tx_hash, log_index),
                KEY idx_orderfilled_raw_block_log (block_number, log_index),
                KEY idx_orderfilled_raw_maker_block (maker, block_number),
                KEY idx_orderfilled_raw_taker_block (taker, block_number),
                KEY idx_orderfilled_raw_token_block (token_id, block_number)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        ensure_orderfilled_sync_windows_schema(conn)
        return

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ORDERFILLED_RAW_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract TEXT NOT NULL,
            event_version TEXT NOT NULL DEFAULT 'legacy',
            event_topic TEXT NOT NULL,
            tx_hash TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            block_number INTEGER NOT NULL,
            block_time TEXT,
            order_hash TEXT,
            maker TEXT NOT NULL,
            taker TEXT NOT NULL,
            maker_asset_id TEXT NOT NULL,
            taker_asset_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            price TEXT NOT NULL DEFAULT '0',
            size TEXT NOT NULL DEFAULT '0',
            maker_amount TEXT NOT NULL DEFAULT '0',
            taker_amount TEXT NOT NULL DEFAULT '0',
            fee TEXT NOT NULL DEFAULT '0',
            raw_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(contract, tx_hash, log_index)
        )
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_orderfilled_raw_block_log ON {ORDERFILLED_RAW_TABLE}(block_number, log_index)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_orderfilled_raw_maker_block ON {ORDERFILLED_RAW_TABLE}(maker, block_number)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_orderfilled_raw_taker_block ON {ORDERFILLED_RAW_TABLE}(taker, block_number)")
    ensure_orderfilled_sync_windows_schema(conn)


def ensure_orderfilled_sync_windows_schema(conn) -> None:
    backend = get_backend()
    if backend == "mysql":
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ORDERFILLED_SYNC_WINDOWS_TABLE} (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                from_block BIGINT NOT NULL,
                to_block BIGINT NOT NULL,
                exchange_set VARCHAR(128) NOT NULL DEFAULT 'known_orderfilled',
                chain_log_count BIGINT NOT NULL DEFAULT 0,
                db_log_count BIGINT NOT NULL DEFAULT 0,
                missing_count BIGINT NOT NULL DEFAULT 0,
                repaired_count BIGINT NOT NULL DEFAULT 0,
                status VARCHAR(32) NOT NULL,
                audited_at TIMESTAMP NULL,
                repaired_at TIMESTAMP NULL,
                last_error LONGTEXT,
                UNIQUE KEY uq_orderfilled_window (from_block, to_block, exchange_set),
                KEY idx_orderfilled_window_status (status, from_block)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        return

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ORDERFILLED_SYNC_WINDOWS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_block INTEGER NOT NULL,
            to_block INTEGER NOT NULL,
            exchange_set TEXT NOT NULL DEFAULT 'known_orderfilled',
            chain_log_count INTEGER NOT NULL DEFAULT 0,
            db_log_count INTEGER NOT NULL DEFAULT 0,
            missing_count INTEGER NOT NULL DEFAULT 0,
            repaired_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            audited_at TEXT,
            repaired_at TEXT,
            last_error TEXT,
            UNIQUE(from_block, to_block, exchange_set)
        )
        """
    )


def orderfilled_raw_row(decoded: dict[str, Any], *, event_topic: str = "", include_raw_json: bool = True) -> dict[str, Any]:
    return {
        "contract": normalize_address(decoded.get("contract") or decoded.get("exchange")),
        "event_version": str(decoded.get("eventVersion") or "legacy"),
        "event_topic": normalize_hex(event_topic, prefix=True),
        "tx_hash": normalize_hex(decoded.get("txHash"), prefix=True),
        "log_index": int(decoded.get("logIndex") or 0),
        "block_number": int(decoded.get("block_number") or 0),
        "block_time": normalize_block_time(decoded.get("timestamp")),
        "order_hash": normalize_hex(decoded.get("orderHash"), prefix=True),
        "maker": normalize_address(decoded.get("maker")),
        "taker": normalize_address(decoded.get("taker")),
        "maker_asset_id": str(decoded.get("makerAssetId") or "0"),
        "taker_asset_id": str(decoded.get("takerAssetId") or "0"),
        "token_id": str(decoded.get("tokenId") or "0"),
        "side": str(decoded.get("side") or "UNKNOWN").upper(),
        "price": raw_decimal_text(decoded.get("price")),
        "size": raw_decimal_text(decoded.get("size")),
        "maker_amount": raw_decimal_text(decoded.get("makerAmountFilled")),
        "taker_amount": raw_decimal_text(decoded.get("takerAmountFilled")),
        "fee": raw_decimal_text(decoded.get("fee")),
        "raw_json": json.dumps(decoded, ensure_ascii=False, sort_keys=True, default=str) if include_raw_json else None,
    }


def insert_orderfilled_raw_batch(conn, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    ensure_orderfilled_raw_schema(conn)
    before_changes = getattr(conn, "total_changes", 0)
    cursor = conn.cursor()
    cursor.executemany(
        f"""
        INSERT OR IGNORE INTO {ORDERFILLED_RAW_TABLE} (
            contract, event_version, event_topic, tx_hash, log_index,
            block_number, block_time, order_hash, maker, taker,
            maker_asset_id, taker_asset_id, token_id, side, price, size,
            maker_amount, taker_amount, fee, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["contract"],
                row["event_version"],
                row["event_topic"],
                row["tx_hash"],
                row["log_index"],
                row["block_number"],
                row["block_time"],
                row["order_hash"],
                row["maker"],
                row["taker"],
                row["maker_asset_id"],
                row["taker_asset_id"],
                row["token_id"],
                row["side"],
                row["price"],
                row["size"],
                row["maker_amount"],
                row["taker_amount"],
                row["fee"],
                row["raw_json"],
            )
            for row in rows
        ],
    )
    conn.commit()
    rowcount = getattr(cursor, "rowcount", 0)
    if rowcount and rowcount > 0:
        return int(rowcount)
    return max(0, getattr(conn, "total_changes", before_changes) - before_changes)


def count_orderfilled_raw(conn, from_block: int, to_block: int) -> int:
    ensure_orderfilled_raw_schema(conn)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM {ORDERFILLED_RAW_TABLE}
        WHERE block_number BETWEEN ? AND ?
        """,
        (from_block, to_block),
    )
    row = cursor.fetchone()
    if row is None:
        return 0
    if hasattr(row, "get"):
        return int(row.get("c") or 0)
    return int(row[0] or 0)


def upsert_orderfilled_sync_window(
    conn,
    *,
    from_block: int,
    to_block: int,
    chain_log_count: int,
    db_log_count: int,
    repaired_count: int,
    status: str,
    last_error: Optional[str] = None,
) -> None:
    ensure_orderfilled_sync_windows_schema(conn)
    missing_count = max(0, int(chain_log_count) - int(db_log_count))
    if get_backend() == "mysql":
        conn.execute(
            f"""
            INSERT INTO {ORDERFILLED_SYNC_WINDOWS_TABLE} (
                from_block, to_block, exchange_set, chain_log_count, db_log_count,
                missing_count, repaired_count, status, audited_at, repaired_at, last_error
            ) VALUES (?, ?, 'known_orderfilled', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            ON DUPLICATE KEY UPDATE
                chain_log_count = VALUES(chain_log_count),
                db_log_count = VALUES(db_log_count),
                missing_count = VALUES(missing_count),
                repaired_count = VALUES(repaired_count),
                status = VALUES(status),
                audited_at = VALUES(audited_at),
                repaired_at = VALUES(repaired_at),
                last_error = VALUES(last_error)
            """,
            (from_block, to_block, chain_log_count, db_log_count, missing_count, repaired_count, status, last_error),
        )
    else:
        conn.execute(
            f"""
            INSERT INTO {ORDERFILLED_SYNC_WINDOWS_TABLE} (
                from_block, to_block, exchange_set, chain_log_count, db_log_count,
                missing_count, repaired_count, status, audited_at, repaired_at, last_error
            ) VALUES (?, ?, 'known_orderfilled', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(from_block, to_block, exchange_set) DO UPDATE SET
                chain_log_count = excluded.chain_log_count,
                db_log_count = excluded.db_log_count,
                missing_count = excluded.missing_count,
                repaired_count = excluded.repaired_count,
                status = excluded.status,
                audited_at = excluded.audited_at,
                repaired_at = excluded.repaired_at,
                last_error = excluded.last_error
            """,
            (from_block, to_block, chain_log_count, db_log_count, missing_count, repaired_count, status, last_error),
        )
    conn.commit()
