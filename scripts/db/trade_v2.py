#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trade v2 storage helpers.

This module keeps the hot trade table compact while preserving a compatibility
path for legacy API payloads.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Sequence, Tuple

from .db import get_mysql_settings

LEGACY_TRADES_TABLE = "trades"
TRADE_V2_CORE_TABLE = "trades_v2"
TRADE_V2_READ_VIEW = "trades_v2_read"
TRADE_V2_MIGRATION_STATE_TABLE = "trade_v2_migration_state"

_HEX_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]+$")

SIDE_UNKNOWN = 0
SIDE_BUY = 1
SIDE_SELL = 2

OUTCOME_UNKNOWN = 0
OUTCOME_YES = 1
OUTCOME_NO = 2

KNOWN_CONTRACT_LABELS = {
    "ctf exchange": "CTF Exchange",
    "negrisk ctf exchange": "NegRisk CTF Exchange",
}


def _runtime_trade_write_mode() -> str:
    mode = (os.environ.get("POLYDATA_TRADE_WRITE_MODE") or "v2").strip().lower()
    return mode if mode in {"legacy", "v2"} else "v2"


def _runtime_trade_read_source() -> str:
    explicit = (os.environ.get("POLYDATA_TRADES_READ_SOURCE") or "").strip()
    if explicit:
        return explicit
    return TRADE_V2_READ_VIEW if _runtime_trade_write_mode() == "v2" else LEGACY_TRADES_TABLE


def _runtime_trade_stats_source() -> str:
    explicit = (os.environ.get("POLYDATA_TRADES_STATS_SOURCE") or "").strip()
    if explicit:
        return explicit
    return TRADE_V2_CORE_TABLE if _runtime_trade_write_mode() == "v2" else LEGACY_TRADES_TABLE


def _runtime_address_history_source() -> str:
    explicit = (os.environ.get("POLYDATA_ADDRESS_HISTORY_SOURCE") or "").strip()
    if explicit:
        return explicit
    return TRADE_V2_CORE_TABLE if _runtime_trade_write_mode() == "v2" else LEGACY_TRADES_TABLE


def get_trade_write_mode() -> str:
    return _runtime_trade_write_mode()


def get_trade_read_source() -> str:
    return _runtime_trade_read_source()


def get_trade_stats_source() -> str:
    return _runtime_trade_stats_source()


def get_address_history_source() -> str:
    return _runtime_address_history_source()


def sql_identifier(name: str) -> str:
    text = (name or "").strip()
    if not text or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return text


def normalize_hex(value: Any, expected_bytes: int, *, prefix: bool = False) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not _HEX_RE.fullmatch(text):
        return None
    if text.startswith(("0x", "0X")):
        text = text[2:]
    text = text.lower()
    if len(text) != expected_bytes * 2:
        return None
    return ("0x" + text) if prefix else text


def hex_to_bytes(value: Any, expected_bytes: int) -> Optional[bytes]:
    text = normalize_hex(value, expected_bytes)
    if text is None:
        return None
    return bytes.fromhex(text)


def hex_to_bytes20(value: Any) -> Optional[bytes]:
    return hex_to_bytes(value, 20)


def hex_to_bytes32(value: Any) -> Optional[bytes]:
    return hex_to_bytes(value, 32)


def bytes20_to_hex(value: Optional[bytes]) -> Optional[str]:
    if value is None:
        return None
    return "0x" + value.hex()


def bytes32_to_hex(value: Optional[bytes]) -> Optional[str]:
    if value is None:
        return None
    return value.hex()


def uint256_text_to_bytes32(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith(("0x", "0X")):
        return hex_to_bytes32(text)
    if not re.fullmatch(r"[0-9]+", text):
        return None
    numeric = int(text, 10)
    if numeric < 0 or numeric >= 2**256:
        return None
    return numeric.to_bytes(32, byteorder="big", signed=False)


def uint256_storage_to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if len(raw) == 32:
            return str(int.from_bytes(raw, byteorder="big", signed=False))
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"[0-9]+", text):
        return text
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return str(int(text, 16))
    if text.startswith(("0x", "0X")) and len(text) == 66 and _HEX_RE.fullmatch(text):
        return str(int(text[2:], 16))
    return text


def normalize_side_code(value: Any) -> int:
    text = str(value or "").strip().upper()
    if text == "BUY":
        return SIDE_BUY
    if text == "SELL":
        return SIDE_SELL
    return SIDE_UNKNOWN


def side_code_to_text(value: Any) -> str:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if numeric == SIDE_BUY:
        return "BUY"
    if numeric == SIDE_SELL:
        return "SELL"
    return "UNKNOWN"


def normalize_outcome_code(value: Any) -> int:
    text = str(value or "").strip().upper()
    if text == "YES":
        return OUTCOME_YES
    if text == "NO":
        return OUTCOME_NO
    return OUTCOME_UNKNOWN


def outcome_code_to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    if numeric == OUTCOME_YES:
        return "YES"
    if numeric == OUTCOME_NO:
        return "NO"
    return "UNKNOWN"


def parse_trade_timestamp(value: Any) -> Optional[datetime]:
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


def format_trade_timestamp(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def decimal_to_text(value: Any) -> str:
    if value is None:
        return ""
    return format(parse_decimal(value), "f")


def normalize_contract(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    normalized_hex = normalize_hex(text, 20, prefix=True)
    if normalized_hex is not None:
        return normalized_hex
    return KNOWN_CONTRACT_LABELS.get(text.lower(), text)


def convert_trade_row_to_v2(row: Dict[str, Any]) -> Dict[str, Any]:
    trade_time = parse_trade_timestamp(row.get("timestamp"))
    return {
        "id": row.get("id"),
        "tx_hash": hex_to_bytes32(row.get("tx_hash")),
        "log_index": int(row.get("log_index") or 0),
        "market_id": int(row.get("market_id") or 0),
        "maker": hex_to_bytes20(row.get("maker")),
        "taker": hex_to_bytes20(row.get("taker")),
        "price": decimal_to_text(row.get("price")),
        "size": decimal_to_text(row.get("size")),
        "side_code": normalize_side_code(row.get("side")),
        "outcome_code": normalize_outcome_code(row.get("outcome")),
        "token_id": uint256_text_to_bytes32(row.get("token_id")),
        "block_number": row.get("block_number"),
        "block_time": trade_time,
        "order_hash": hex_to_bytes32(row.get("order_hash")),
        "maker_asset_id": uint256_text_to_bytes32(row.get("maker_asset_id")),
        "taker_asset_id": uint256_text_to_bytes32(row.get("taker_asset_id")),
        "contract": normalize_contract(row.get("contract")),
        "maker_amount": row.get("maker_amount"),
        "taker_amount": row.get("taker_amount"),
        "fee": row.get("fee"),
        "created_at": row.get("created_at"),
    }


def compat_maker_asset_id_sql(core_alias: str = "c") -> str:
    return f"""
        CASE {core_alias}.side_code
            WHEN {SIDE_BUY} THEN REPEAT('0', 64)
            WHEN {SIDE_SELL} THEN LOWER(HEX({core_alias}.token_id))
            ELSE NULL
        END
    """.strip()


def compat_taker_asset_id_sql(core_alias: str = "c") -> str:
    return f"""
        CASE {core_alias}.side_code
            WHEN {SIDE_BUY} THEN LOWER(HEX({core_alias}.token_id))
            WHEN {SIDE_SELL} THEN REPEAT('0', 64)
            ELSE NULL
        END
    """.strip()

def compat_trade_projection(core_alias: str = "c") -> str:
    return f"""
        {core_alias}.id AS id,
        LOWER(HEX({core_alias}.tx_hash)) AS tx_hash,
        {core_alias}.log_index AS log_index,
        {core_alias}.market_id AS market_id,
        CONCAT('0x', LOWER(HEX({core_alias}.maker))) AS maker,
        CONCAT('0x', LOWER(HEX({core_alias}.taker))) AS taker,
        CAST({core_alias}.price AS CHAR) AS price,
        CAST({core_alias}.size AS CHAR) AS size,
        CASE {core_alias}.side_code
            WHEN {SIDE_BUY} THEN 'BUY'
            WHEN {SIDE_SELL} THEN 'SELL'
            ELSE 'UNKNOWN'
        END AS side,
        CASE {core_alias}.outcome_code
            WHEN {OUTCOME_YES} THEN 'YES'
            WHEN {OUTCOME_NO} THEN 'NO'
            ELSE 'UNKNOWN'
        END AS outcome,
        LOWER(HEX({core_alias}.token_id)) AS token_id,
        {core_alias}.block_number AS block_number,
        DATE_FORMAT({core_alias}.block_time, '%Y-%m-%dT%H:%i:%sZ') AS timestamp,
        LOWER(HEX({core_alias}.order_hash)) AS order_hash,
        {compat_maker_asset_id_sql(core_alias)} AS maker_asset_id,
        {compat_taker_asset_id_sql(core_alias)} AS taker_asset_id,
        {core_alias}.maker_amount AS maker_amount,
        {core_alias}.taker_amount AS taker_amount,
        {core_alias}.fee AS fee,
        {core_alias}.contract AS contract,
        {core_alias}.created_at AS created_at
    """


def create_trade_v2_core_table(conn, table_name: str = TRADE_V2_CORE_TABLE) -> None:
    safe_table_name = sql_identifier(table_name)
    fk_name = sql_identifier(f"fk_{safe_table_name}_market_id")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {safe_table_name} (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            tx_hash BINARY(32) NOT NULL,
            log_index BIGINT NOT NULL,
            market_id BIGINT NOT NULL,
            maker BINARY(20) NOT NULL,
            taker BINARY(20) NOT NULL,
            price DECIMAL(20, 10) NOT NULL,
            size DECIMAL(30, 10) NOT NULL,
            side_code TINYINT UNSIGNED NOT NULL,
            outcome_code TINYINT UNSIGNED NOT NULL DEFAULT 0,
            token_id BINARY(32) NOT NULL,
            block_number BIGINT DEFAULT NULL,
            block_time DATETIME(6) DEFAULT NULL,
            order_hash BINARY(32) DEFAULT NULL,
            contract VARCHAR(64) DEFAULT NULL,
            maker_amount BIGINT DEFAULT NULL,
            taker_amount BIGINT DEFAULT NULL,
            fee BIGINT DEFAULT NULL,
            created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_trades_v2_tx_log (tx_hash, log_index),
            KEY idx_trades_v2_market_time_log (market_id, block_time, block_number, log_index),
            KEY idx_trades_v2_block_log (block_number, log_index),
            KEY idx_trades_v2_maker_time_log (maker, block_time, block_number, log_index),
            KEY idx_trades_v2_taker_time_log (taker, block_time, block_number, log_index),
            CONSTRAINT {fk_name} FOREIGN KEY (market_id) REFERENCES markets(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def ensure_trade_v2_schema(conn) -> None:
    create_trade_v2_core_table(conn, TRADE_V2_CORE_TABLE)
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TRADE_V2_MIGRATION_STATE_TABLE} (
            migration_name VARCHAR(64) NOT NULL,
            start_block BIGINT NOT NULL,
            end_block BIGINT NOT NULL,
            rows_read BIGINT NOT NULL DEFAULT 0,
            rows_written BIGINT NOT NULL DEFAULT 0,
            rows_validated BIGINT NOT NULL DEFAULT 0,
            status VARCHAR(32) NOT NULL,
            last_error TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (migration_name, start_block, end_block)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    ensure_trade_v2_read_view(conn)


def create_trade_v2_read_view(conn) -> None:
    conn.execute(
        f"""
        CREATE OR REPLACE VIEW {TRADE_V2_READ_VIEW} AS
        SELECT
            {compat_trade_projection('c')}
        FROM {TRADE_V2_CORE_TABLE} c
        """
    )


def ensure_trade_v2_read_view(conn, *, force_replace: bool = False) -> None:
    if force_replace:
        create_trade_v2_read_view(conn)
        return

    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.views
            WHERE table_schema = DATABASE() AND table_name = %s
            LIMIT 1
            """,
            (TRADE_V2_READ_VIEW,),
        )
        if cursor.fetchone() is not None:
            return
    finally:
        cursor.close()

    create_trade_v2_read_view(conn)


def insert_trades_v2_batch(
    conn,
    core_rows: Sequence[Dict[str, Any]],
) -> int:
    if not core_rows:
        return 0
    cursor = conn.cursor()
    cursor.executemany(
        f"""
        INSERT IGNORE INTO {TRADE_V2_CORE_TABLE} (
            id, tx_hash, log_index, market_id, maker, taker,
            price, size, side_code, outcome_code, token_id,
            block_number, block_time, order_hash,
            contract, maker_amount, taker_amount, fee, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                row["id"],
                row["tx_hash"],
                row["log_index"],
                row["market_id"],
                row["maker"],
                row["taker"],
                row["price"],
                row["size"],
                row["side_code"],
                row["outcome_code"],
                row["token_id"],
                row["block_number"],
                row["block_time"],
                row["order_hash"],
                row["contract"],
                row["maker_amount"],
                row["taker_amount"],
                row["fee"],
                row["created_at"],
            )
            for row in core_rows
        ],
    )
    conn.commit()
    return max(cursor.rowcount, 0)


def update_trade_v2_migration_state(
    conn,
    *,
    migration_name: str,
    start_block: int,
    end_block: int,
    rows_read: int,
    rows_written: int,
    rows_validated: int,
    status: str,
    last_error: Optional[str] = None,
) -> None:
    conn.execute(
        f"""
        INSERT INTO {TRADE_V2_MIGRATION_STATE_TABLE} (
            migration_name, start_block, end_block, rows_read, rows_written,
            rows_validated, status, last_error
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            rows_read = VALUES(rows_read),
            rows_written = VALUES(rows_written),
            rows_validated = VALUES(rows_validated),
            status = VALUES(status),
            last_error = VALUES(last_error)
        """,
        (
            migration_name,
            start_block,
            end_block,
            rows_read,
            rows_written,
            rows_validated,
            status,
            last_error,
        ),
    )
    conn.commit()


def mysql_read_source_exists(conn, name: str) -> bool:
    cur = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        LIMIT 1
        """,
        (get_mysql_settings()["database"], name),
    )
    if cur.fetchone():
        return True
    cur = conn.execute(
        """
        SELECT 1
        FROM information_schema.views
        WHERE table_schema = %s AND table_name = %s
        LIMIT 1
        """,
        (get_mysql_settings()["database"], name),
    )
    return bool(cur.fetchone())
