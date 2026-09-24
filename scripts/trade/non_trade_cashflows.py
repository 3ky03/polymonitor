#!/usr/bin/env python3
"""Index Polymarket non-trade cashflows from chain logs.

Covered PnL cashflow types:

    REDEEM        ConditionalTokens PayoutRedemption root collateral payout
    MERGE         ConditionalTokens PositionsMerge root collateral return
    SPLIT         ConditionalTokens PositionSplit root collateral deposit
    MAKER_REBATE  pUSD/USDC.e Transfer from known rebate distributor(s)

Important classification rules:
  * CTF split/merge/redeem logs inside transactions that also contain
    OrderFilled are skipped. These are exchange/internal settlement legs, not
    standalone user PnL cashflows.
  * Split/Merge/Redeem are counted only when parentCollectionId == 0x00..00,
    because non-root operations mint/burn deeper positions rather than moving
    collateral cash.
  * Maker rebates are classified by token transfer from an allowlisted
    distributor address. This is chain-observable cash, with the business label
    supplied by the allowlist.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

try:
    from web3 import Web3
except ImportError as exc:  # pragma: no cover
    raise SystemExit("web3 is required: pip install web3") from exc

from config import get_rpc_url  # noqa: E402
from db import add_db_cli_args, configure_db_from_args, describe_db_target, get_backend, get_connection, init_schema  # noqa: E402
from trade.orderfilled_raw import normalize_address, normalize_hex  # noqa: E402
from trade.rpc_utils import build_web3  # noqa: E402
from trade.trade_decoder import (  # noqa: E402
    CTF_EXCHANGE_ADDRESS,
    NEG_RISK_EXCHANGE_ADDRESS,
    POLYMARKET_EXCHANGE_2026_ADDRESS,
    POLYMARKET_EXCHANGE_2026_ALT_ADDRESS,
    get_order_filled_topics,
)


NON_TRADE_CASHFLOWS_TABLE = "non_trade_cashflows"
NON_TRADE_SYNC_WINDOWS_TABLE = "non_trade_sync_windows"

CTF_ADDRESS = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
PUSD_ADDRESS = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
USDC_E_ADDRESS = "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"
DEFAULT_REBATE_DISTRIBUTORS = ("0x3a9418b2651c8164db5ebc56f12008137865e0f7",)
ZERO_BYTES32 = "0x" + ("0" * 64)
USDC_DIVISOR = Decimal("1000000")

TRANSFER_TOPIC = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex()
POSITION_SPLIT_TOPIC = "0x" + Web3.keccak(
    text="PositionSplit(address,address,bytes32,bytes32,uint256[],uint256)"
).hex()
POSITIONS_MERGE_TOPIC = "0x" + Web3.keccak(
    text="PositionsMerge(address,address,bytes32,bytes32,uint256[],uint256)"
).hex()
PAYOUT_REDEMPTION_TOPIC = "0x" + Web3.keccak(
    text="PayoutRedemption(address,address,bytes32,bytes32,uint256[],uint256)"
).hex()

SUPPORTED_COLLATERAL = {
    normalize_address(PUSD_ADDRESS): "pUSD",
    normalize_address(USDC_E_ADDRESS): "USDC.e",
}


def topic0(value: Any) -> str:
    text = value.hex() if hasattr(value, "hex") else str(value)
    return text.lower() if text.startswith("0x") else "0x" + text.lower()


def topic_address(address: str) -> str:
    clean = normalize_address(address)
    if len(clean) != 42:
        raise ValueError(f"Invalid address: {address}")
    return "0x" + ("0" * 24) + clean[2:]


def address_from_topic(value: Any) -> str:
    text = value.hex() if hasattr(value, "hex") else str(value)
    if text.startswith("0x"):
        text = text[2:]
    return "0x" + text[-40:].lower()


def tx_hash_text(value: Any) -> str:
    text = value.hex() if hasattr(value, "hex") else str(value)
    return text.lower() if text.startswith("0x") else "0x" + text.lower()


def bytes32_from_topic(value: Any) -> str:
    text = value.hex() if hasattr(value, "hex") else str(value)
    return text.lower() if text.startswith("0x") else "0x" + text.lower()


def int_from_data(value: Any) -> int:
    text = value.hex() if hasattr(value, "hex") else str(value)
    return int(text, 16)


def block_time_iso(w3: Web3, block_number: int, cache: dict[int, str]) -> str:
    if block_number not in cache:
        block = w3.eth.get_block(block_number)
        cache[block_number] = datetime.fromtimestamp(int(block["timestamp"]), tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    return cache[block_number]


def ensure_schema(conn) -> None:
    backend = get_backend()
    if backend == "mysql":
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {NON_TRADE_CASHFLOWS_TABLE} (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                address CHAR(42) NOT NULL,
                cashflow_type VARCHAR(32) NOT NULL,
                usdc_amount DECIMAL(38, 18) NOT NULL,
                collateral_token CHAR(42) NOT NULL,
                condition_id CHAR(66),
                parent_collection_id CHAR(66),
                partition_json LONGTEXT,
                tx_hash CHAR(66) NOT NULL,
                log_index BIGINT NOT NULL,
                block_number BIGINT NOT NULL,
                block_time VARCHAR(40),
                source_contract CHAR(42) NOT NULL,
                source_event VARCHAR(64) NOT NULL,
                source VARCHAR(64) NOT NULL,
                raw_json LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_non_trade_cashflow (cashflow_type, tx_hash, log_index, address),
                KEY idx_non_trade_address_block (address, block_number),
                KEY idx_non_trade_type_block (cashflow_type, block_number),
                KEY idx_non_trade_condition_block (condition_id, block_number)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {NON_TRADE_SYNC_WINDOWS_TABLE} (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                from_block BIGINT NOT NULL,
                to_block BIGINT NOT NULL,
                event_set VARCHAR(128) NOT NULL DEFAULT 'ctf_non_trade_and_rebate',
                chain_log_count BIGINT NOT NULL DEFAULT 0,
                db_row_count BIGINT NOT NULL DEFAULT 0,
                inserted_count BIGINT NOT NULL DEFAULT 0,
                skipped_count BIGINT NOT NULL DEFAULT 0,
                status VARCHAR(32) NOT NULL,
                synced_at TIMESTAMP NULL,
                last_error LONGTEXT,
                UNIQUE KEY uq_non_trade_window (from_block, to_block, event_set),
                KEY idx_non_trade_window_status (status, from_block)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        return

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {NON_TRADE_CASHFLOWS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            cashflow_type TEXT NOT NULL,
            usdc_amount TEXT NOT NULL,
            collateral_token TEXT NOT NULL,
            condition_id TEXT,
            parent_collection_id TEXT,
            partition_json TEXT,
            tx_hash TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            block_number INTEGER NOT NULL,
            block_time TEXT,
            source_contract TEXT NOT NULL,
            source_event TEXT NOT NULL,
            source TEXT NOT NULL,
            raw_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(cashflow_type, tx_hash, log_index, address)
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_non_trade_address_block ON {NON_TRADE_CASHFLOWS_TABLE}(address, block_number)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_non_trade_type_block ON {NON_TRADE_CASHFLOWS_TABLE}(cashflow_type, block_number)"
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {NON_TRADE_SYNC_WINDOWS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_block INTEGER NOT NULL,
            to_block INTEGER NOT NULL,
            event_set TEXT NOT NULL DEFAULT 'ctf_non_trade_and_rebate',
            chain_log_count INTEGER NOT NULL DEFAULT 0,
            db_row_count INTEGER NOT NULL DEFAULT 0,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            synced_at TEXT,
            last_error TEXT,
            UNIQUE(from_block, to_block, event_set)
        )
        """
    )


def upsert_sync_window(
    conn,
    *,
    from_block: int,
    to_block: int,
    chain_log_count: int,
    db_row_count: int,
    inserted_count: int,
    skipped_count: int,
    status: str,
    last_error: Optional[str] = None,
) -> None:
    ensure_schema(conn)
    if get_backend() == "mysql":
        conn.execute(
            f"""
            INSERT INTO {NON_TRADE_SYNC_WINDOWS_TABLE} (
                from_block, to_block, event_set, chain_log_count, db_row_count,
                inserted_count, skipped_count, status, synced_at, last_error
            ) VALUES (?, ?, 'ctf_non_trade_and_rebate', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON DUPLICATE KEY UPDATE
                chain_log_count = VALUES(chain_log_count),
                db_row_count = VALUES(db_row_count),
                inserted_count = VALUES(inserted_count),
                skipped_count = VALUES(skipped_count),
                status = VALUES(status),
                synced_at = VALUES(synced_at),
                last_error = VALUES(last_error)
            """,
            (from_block, to_block, chain_log_count, db_row_count, inserted_count, skipped_count, status, last_error),
        )
    else:
        conn.execute(
            f"""
            INSERT INTO {NON_TRADE_SYNC_WINDOWS_TABLE} (
                from_block, to_block, event_set, chain_log_count, db_row_count,
                inserted_count, skipped_count, status, synced_at, last_error
            ) VALUES (?, ?, 'ctf_non_trade_and_rebate', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(from_block, to_block, event_set) DO UPDATE SET
                chain_log_count = excluded.chain_log_count,
                db_row_count = excluded.db_row_count,
                inserted_count = excluded.inserted_count,
                skipped_count = excluded.skipped_count,
                status = excluded.status,
                synced_at = excluded.synced_at,
                last_error = excluded.last_error
            """,
            (from_block, to_block, chain_log_count, db_row_count, inserted_count, skipped_count, status, last_error),
        )
    conn.commit()


def insert_cashflows(conn, rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    ensure_schema(conn)
    cursor = conn.cursor()
    cursor.executemany(
        f"""
        INSERT OR IGNORE INTO {NON_TRADE_CASHFLOWS_TABLE} (
            address, cashflow_type, usdc_amount, collateral_token, condition_id,
            parent_collection_id, partition_json, tx_hash, log_index, block_number,
            block_time, source_contract, source_event, source, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["address"],
                row["cashflow_type"],
                row["usdc_amount"],
                row["collateral_token"],
                row.get("condition_id"),
                row.get("parent_collection_id"),
                row.get("partition_json"),
                row["tx_hash"],
                row["log_index"],
                row["block_number"],
                row["block_time"],
                row["source_contract"],
                row["source_event"],
                row["source"],
                row["raw_json"],
            )
            for row in rows
        ],
    )
    conn.commit()
    return max(0, int(getattr(cursor, "rowcount", 0) or 0))


def count_cashflows(conn, from_block: int, to_block: int) -> int:
    ensure_schema(conn)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM {NON_TRADE_CASHFLOWS_TABLE}
        WHERE block_number BETWEEN ? AND ?
        """,
        (from_block, to_block),
    )
    row = cursor.fetchone()
    if row is None:
        return 0
    return int(row.get("c") if hasattr(row, "get") else row[0])


def iter_windows(from_block: int, to_block: int, batch_blocks: int):
    current = from_block
    while current <= to_block:
        end = min(to_block, current + max(1, batch_blocks) - 1)
        yield current, end
        current = end + 1


def fetch_orderfilled_tx_hashes(w3: Web3, from_block: int, to_block: int) -> set[str]:
    legacy_topic, topic_2026 = get_order_filled_topics(w3)
    logs = w3.eth.get_logs(
        {
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": [
                CTF_EXCHANGE_ADDRESS,
                NEG_RISK_EXCHANGE_ADDRESS,
                POLYMARKET_EXCHANGE_2026_ADDRESS,
                POLYMARKET_EXCHANGE_2026_ALT_ADDRESS,
            ],
            "topics": [[legacy_topic, topic_2026]],
        }
    )
    return {tx_hash_text(log["transactionHash"]) for log in logs}


def fetch_ctf_logs(w3: Web3, from_block: int, to_block: int) -> list[Any]:
    return list(
        w3.eth.get_logs(
            {
                "fromBlock": from_block,
                "toBlock": to_block,
                "address": Web3.to_checksum_address(CTF_ADDRESS),
                "topics": [[POSITION_SPLIT_TOPIC, POSITIONS_MERGE_TOPIC, PAYOUT_REDEMPTION_TOPIC]],
            }
        )
    )


def fetch_rebate_logs(
    w3: Web3,
    from_block: int,
    to_block: int,
    rebate_distributors: Sequence[str],
) -> list[Any]:
    logs: list[Any] = []
    token_addresses = [Web3.to_checksum_address(PUSD_ADDRESS), Web3.to_checksum_address(USDC_E_ADDRESS)]
    for distributor in rebate_distributors:
        logs.extend(
            w3.eth.get_logs(
                {
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": token_addresses,
                    "topics": [TRANSFER_TOPIC, topic_address(distributor)],
                }
            )
        )
    return logs


def decode_split_or_merge(
    w3: Web3,
    log: Any,
    block_ts_cache: dict[int, str],
    *,
    include_block_time: bool,
    include_raw_json: bool,
) -> Optional[dict[str, Any]]:
    event_topic = topic0(log["topics"][0])
    cashflow_type = "SPLIT" if event_topic == POSITION_SPLIT_TOPIC.lower() else "MERGE"
    address = address_from_topic(log["topics"][1])
    parent_collection_id = bytes32_from_topic(log["topics"][2])
    condition_id = bytes32_from_topic(log["topics"][3])
    if parent_collection_id != ZERO_BYTES32:
        return None
    collateral_token, partition, amount = w3.codec.decode(["address", "uint256[]", "uint256"], log["data"])
    collateral_token = normalize_address(collateral_token)
    if collateral_token not in SUPPORTED_COLLATERAL:
        return None
    amount_usdc = Decimal(int(amount)) / USDC_DIVISOR
    return {
        "address": address,
        "cashflow_type": cashflow_type,
        "usdc_amount": format(amount_usdc, "f"),
        "collateral_token": collateral_token,
        "condition_id": condition_id,
        "parent_collection_id": parent_collection_id,
        "partition_json": json.dumps([int(item) for item in partition], sort_keys=True),
        "tx_hash": tx_hash_text(log["transactionHash"]),
        "log_index": int(log["logIndex"]),
        "block_number": int(log["blockNumber"]),
        "block_time": block_time_iso(w3, int(log["blockNumber"]), block_ts_cache) if include_block_time else "",
        "source_contract": normalize_address(log["address"]),
        "source_event": cashflow_type,
        "source": "ctf_event",
        "raw_json": (
            json.dumps({"topics": [topic0(item) for item in log["topics"]], "data": normalize_hex(log["data"])}, sort_keys=True)
            if include_raw_json
            else None
        ),
    }


def decode_redeem(
    w3: Web3,
    log: Any,
    block_ts_cache: dict[int, str],
    *,
    include_block_time: bool,
    include_raw_json: bool,
) -> Optional[dict[str, Any]]:
    address = address_from_topic(log["topics"][1])
    collateral_token = normalize_address(address_from_topic(log["topics"][2]))
    parent_collection_id = bytes32_from_topic(log["topics"][3])
    if parent_collection_id != ZERO_BYTES32:
        return None
    if collateral_token not in SUPPORTED_COLLATERAL:
        return None
    condition_id_raw, index_sets, payout = w3.codec.decode(["bytes32", "uint256[]", "uint256"], log["data"])
    condition_id = normalize_hex(condition_id_raw)
    amount_usdc = Decimal(int(payout)) / USDC_DIVISOR
    return {
        "address": address,
        "cashflow_type": "REDEEM",
        "usdc_amount": format(amount_usdc, "f"),
        "collateral_token": collateral_token,
        "condition_id": condition_id,
        "parent_collection_id": parent_collection_id,
        "partition_json": json.dumps([int(item) for item in index_sets], sort_keys=True),
        "tx_hash": tx_hash_text(log["transactionHash"]),
        "log_index": int(log["logIndex"]),
        "block_number": int(log["blockNumber"]),
        "block_time": block_time_iso(w3, int(log["blockNumber"]), block_ts_cache) if include_block_time else "",
        "source_contract": normalize_address(log["address"]),
        "source_event": "PayoutRedemption",
        "source": "ctf_event",
        "raw_json": (
            json.dumps({"topics": [topic0(item) for item in log["topics"]], "data": normalize_hex(log["data"])}, sort_keys=True)
            if include_raw_json
            else None
        ),
    }


def decode_rebate_transfer(
    log: Any,
    block_ts_cache: dict[int, str],
    w3: Web3,
    *,
    include_block_time: bool,
    include_raw_json: bool,
) -> Optional[dict[str, Any]]:
    if len(log["topics"]) < 3:
        return None
    token = normalize_address(log["address"])
    if token not in SUPPORTED_COLLATERAL:
        return None
    to_address = address_from_topic(log["topics"][2])
    amount_usdc = Decimal(int_from_data(log["data"])) / USDC_DIVISOR
    return {
        "address": to_address,
        "cashflow_type": "MAKER_REBATE",
        "usdc_amount": format(amount_usdc, "f"),
        "collateral_token": token,
        "condition_id": None,
        "parent_collection_id": None,
        "partition_json": None,
        "tx_hash": tx_hash_text(log["transactionHash"]),
        "log_index": int(log["logIndex"]),
        "block_number": int(log["blockNumber"]),
        "block_time": block_time_iso(w3, int(log["blockNumber"]), block_ts_cache) if include_block_time else "",
        "source_contract": token,
        "source_event": "Transfer",
        "source": "rebate_distributor_transfer",
        "raw_json": (
            json.dumps({"topics": [topic0(item) for item in log["topics"]], "data": normalize_hex(log["data"])}, sort_keys=True)
            if include_raw_json
            else None
        ),
    }


def decode_window_rows(
    w3: Web3,
    *,
    ctf_logs: list[Any],
    rebate_logs: list[Any],
    orderfilled_tx_hashes: set[str],
    quiet: bool,
    include_block_time: bool,
    include_raw_json: bool,
) -> tuple[list[dict[str, Any]], int]:
    block_ts_cache: dict[int, str] = {}
    rows: list[dict[str, Any]] = []
    skipped = 0
    for log in ctf_logs:
        if tx_hash_text(log["transactionHash"]) in orderfilled_tx_hashes:
            skipped += 1
            continue
        event_topic = topic0(log["topics"][0])
        try:
            if event_topic in {POSITION_SPLIT_TOPIC.lower(), POSITIONS_MERGE_TOPIC.lower()}:
                row = decode_split_or_merge(
                    w3,
                    log,
                    block_ts_cache,
                    include_block_time=include_block_time,
                    include_raw_json=include_raw_json,
                )
            elif event_topic == PAYOUT_REDEMPTION_TOPIC.lower():
                row = decode_redeem(
                    w3,
                    log,
                    block_ts_cache,
                    include_block_time=include_block_time,
                    include_raw_json=include_raw_json,
                )
            else:
                row = None
        except Exception as exc:
            skipped += 1
            if not quiet:
                print(f"[non-trade] failed ctf log tx={tx_hash_text(log['transactionHash'])}: {exc}", file=sys.stderr)
            continue
        if row is None:
            skipped += 1
        else:
            rows.append(row)

    for log in rebate_logs:
        row = decode_rebate_transfer(
            log,
            block_ts_cache,
            w3,
            include_block_time=include_block_time,
            include_raw_json=include_raw_json,
        )
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    return rows, skipped


def sync_window(
    *,
    conn: Any,
    w3: Web3,
    from_block: int,
    to_block: int,
    rebate_distributors: Sequence[str],
    quiet: bool,
    include_block_time: bool,
    include_raw_json: bool,
) -> dict[str, Any]:
    try:
        ctf_logs = fetch_ctf_logs(w3, from_block, to_block)
        rebate_logs = fetch_rebate_logs(w3, from_block, to_block, rebate_distributors)
        orderfilled_tx_hashes = fetch_orderfilled_tx_hashes(w3, from_block, to_block)
        rows, skipped = decode_window_rows(
            w3,
            ctf_logs=ctf_logs,
            rebate_logs=rebate_logs,
            orderfilled_tx_hashes=orderfilled_tx_hashes,
            quiet=quiet,
            include_block_time=include_block_time,
            include_raw_json=include_raw_json,
        )
        inserted = insert_cashflows(conn, rows)
        db_count = count_cashflows(conn, from_block, to_block)
        status = "complete"
        upsert_sync_window(
            conn,
            from_block=from_block,
            to_block=to_block,
            chain_log_count=len(ctf_logs) + len(rebate_logs),
            db_row_count=db_count,
            inserted_count=inserted,
            skipped_count=skipped,
            status=status,
        )
        result = {
            "from_block": from_block,
            "to_block": to_block,
            "ctf_logs": len(ctf_logs),
            "rebate_logs": len(rebate_logs),
            "orderfilled_txs": len(orderfilled_tx_hashes),
            "decoded_rows": len(rows),
            "inserted": inserted,
            "skipped": skipped,
            "db_count": db_count,
            "status": status,
        }
        if not quiet:
            print(result, file=sys.stderr, flush=True)
        return result
    except Exception as exc:
        upsert_sync_window(
            conn,
            from_block=from_block,
            to_block=to_block,
            chain_log_count=0,
            db_row_count=count_cashflows(conn, from_block, to_block),
            inserted_count=0,
            skipped_count=0,
            status="error",
            last_error=str(exc),
        )
        raise


def sync_window_with_retry(
    *,
    conn: Any,
    w3: Web3,
    from_block: int,
    to_block: int,
    rebate_distributors: Sequence[str],
    retry_attempts: int,
    min_batch_blocks: int,
    quiet: bool,
    include_block_time: bool,
    include_raw_json: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    last_error: Optional[BaseException] = None
    for attempt in range(1, max(1, retry_attempts) + 1):
        try:
            results.append(
                sync_window(
                    conn=conn,
                    w3=w3,
                    from_block=from_block,
                    to_block=to_block,
                    rebate_distributors=rebate_distributors,
                    quiet=quiet,
                    include_block_time=include_block_time,
                    include_raw_json=include_raw_json,
                )
            )
            return results
        except Exception as exc:
            last_error = exc
            if not quiet:
                print(
                    f"[non-trade] window {from_block}-{to_block} attempt {attempt}/{retry_attempts} failed: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
            time.sleep(min(30, 2**attempt))

    if to_block > from_block and (to_block - from_block + 1) > max(1, min_batch_blocks):
        mid = (from_block + to_block) // 2
        if not quiet:
            print(
                f"[non-trade] splitting hot window {from_block}-{to_block} -> {from_block}-{mid}, {mid + 1}-{to_block}",
                file=sys.stderr,
                flush=True,
            )
        results.extend(
            sync_window_with_retry(
                conn=conn,
                w3=w3,
                from_block=from_block,
                to_block=mid,
                rebate_distributors=rebate_distributors,
                retry_attempts=retry_attempts,
                min_batch_blocks=min_batch_blocks,
                quiet=quiet,
                include_block_time=include_block_time,
                include_raw_json=include_raw_json,
            )
        )
        results.extend(
            sync_window_with_retry(
                conn=conn,
                w3=w3,
                from_block=mid + 1,
                to_block=to_block,
                rebate_distributors=rebate_distributors,
                retry_attempts=retry_attempts,
                min_batch_blocks=min_batch_blocks,
                quiet=quiet,
                include_block_time=include_block_time,
                include_raw_json=include_raw_json,
            )
        )
        return results

    upsert_sync_window(
        conn,
        from_block=from_block,
        to_block=to_block,
        chain_log_count=0,
        db_row_count=count_cashflows(conn, from_block, to_block),
        inserted_count=0,
        skipped_count=0,
        status="error",
        last_error=str(last_error),
    )
    return [
        {
            "from_block": from_block,
            "to_block": to_block,
            "ctf_logs": 0,
            "rebate_logs": 0,
            "orderfilled_txs": 0,
            "decoded_rows": 0,
            "inserted": 0,
            "skipped": 0,
            "db_count": count_cashflows(conn, from_block, to_block),
            "status": "error",
            "last_error": str(last_error),
        }
    ]


def sync_window_task(
    *,
    rpc_url: str,
    from_block: int,
    to_block: int,
    rebate_distributors: Sequence[str],
    retry_attempts: int,
    min_batch_blocks: int,
    quiet: bool,
    include_block_time: bool,
    include_raw_json: bool,
) -> list[dict[str, Any]]:
    w3 = build_web3(rpc_url)
    conn = get_connection()
    ensure_schema(conn)
    try:
        return sync_window_with_retry(
            conn=conn,
            w3=w3,
            from_block=from_block,
            to_block=to_block,
            rebate_distributors=rebate_distributors,
            retry_attempts=retry_attempts,
            min_batch_blocks=min_batch_blocks,
            quiet=quiet,
            include_block_time=include_block_time,
            include_raw_json=include_raw_json,
        )
    finally:
        conn.close()


def get_last_complete_to_block(conn) -> Optional[int]:
    ensure_schema(conn)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT MAX(to_block) AS last_block
        FROM {NON_TRADE_SYNC_WINDOWS_TABLE}
        WHERE event_set = 'ctf_non_trade_and_rebate' AND status = 'complete'
        """
    )
    row = cursor.fetchone()
    if row is None:
        return None
    value = row.get("last_block") if hasattr(row, "get") else row[0]
    return None if value is None else int(value)


def latest_safe_block(w3: Web3, confirmations: int) -> int:
    return max(0, int(w3.eth.block_number) - max(0, int(confirmations)))


def parse_rebate_distributors(value: str) -> list[str]:
    return [normalize_address(item) for item in value.split(",") if item.strip()]


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    rpc_url = args.rpc or get_rpc_url()
    w3 = build_web3(rpc_url)
    init_schema()
    conn = get_connection()
    ensure_schema(conn)
    try:
        if args.continue_sync:
            last_complete = get_last_complete_to_block(conn)
            if args.from_block is not None:
                from_block = int(args.from_block)
            elif last_complete is not None:
                from_block = max(0, last_complete + 1 - max(0, int(args.overlap_blocks)))
            else:
                from_block = max(0, latest_safe_block(w3, args.confirmations) - int(args.bootstrap_lookback_blocks))
        elif args.from_block is not None:
            from_block = int(args.from_block)
        else:
            raise SystemExit("Pass --from-block, or use --continue-sync.")
        to_block = int(args.to_block) if args.to_block is not None else latest_safe_block(w3, args.confirmations)
        if from_block > to_block:
            return {"status": "noop", "from_block": from_block, "to_block": to_block}

        rebate_distributors = parse_rebate_distributors(args.rebate_distributors)
        if not args.quiet:
            print(f"[non-trade] DB={describe_db_target()}", file=sys.stderr)
            print(
                f"[non-trade] syncing blocks={from_block}-{to_block} batch={args.batch} "
                f"parallel_workers={args.parallel_workers} "
                f"include_block_time={not args.skip_block_time} include_raw_json={not args.skip_raw_json} "
                f"rebate_distributors={rebate_distributors}",
                file=sys.stderr,
            )
        results = []
        block_windows = list(iter_windows(from_block, to_block, int(args.batch)))
        parallel_workers = max(1, int(args.parallel_workers))
        if parallel_workers > 1:
            completed = 0
            with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
                future_to_window = {
                    executor.submit(
                        sync_window_task,
                        rpc_url=rpc_url,
                        from_block=start,
                        to_block=end,
                        rebate_distributors=rebate_distributors,
                        retry_attempts=int(args.retry_attempts),
                        min_batch_blocks=int(args.min_batch),
                        quiet=bool(args.quiet),
                        include_block_time=not args.skip_block_time,
                        include_raw_json=not args.skip_raw_json,
                    ): (start, end)
                    for start, end in block_windows
                }
                for future in as_completed(future_to_window):
                    start, end = future_to_window[future]
                    try:
                        window_results = future.result()
                    except Exception as exc:
                        window_results = [
                            {
                                "from_block": start,
                                "to_block": end,
                                "ctf_logs": 0,
                                "rebate_logs": 0,
                                "orderfilled_txs": 0,
                                "decoded_rows": 0,
                                "inserted": 0,
                                "skipped": 0,
                                "db_count": 0,
                                "status": "error",
                                "last_error": str(exc),
                            }
                        ]
                    results.extend(window_results)
                    completed += 1
                    if not args.quiet:
                        print(
                            {
                                "parallel_completed_windows": completed,
                                "parallel_total_windows": len(block_windows),
                                "last_window": f"{start}-{end}",
                                "last_statuses": [row.get("status") for row in window_results],
                                "last_inserted": sum(int(row.get("inserted") or 0) for row in window_results),
                                "last_decoded_rows": sum(int(row.get("decoded_rows") or 0) for row in window_results),
                            },
                            file=sys.stderr,
                            flush=True,
                        )
        else:
            for start, end in block_windows:
                results.extend(
                    sync_window_with_retry(
                        conn=conn,
                        w3=w3,
                        from_block=start,
                        to_block=end,
                        rebate_distributors=rebate_distributors,
                        retry_attempts=int(args.retry_attempts),
                        min_batch_blocks=int(args.min_batch),
                        quiet=bool(args.quiet),
                        include_block_time=not args.skip_block_time,
                        include_raw_json=not args.skip_raw_json,
                    )
                )
                time.sleep(float(args.window_delay))
    finally:
        conn.close()

    return {
        "status": "done",
        "from_block": from_block,
        "to_block": to_block,
        "windows": len(results),
        "decoded_rows": sum(int(row.get("decoded_rows") or 0) for row in results),
        "inserted": sum(int(row.get("inserted") or 0) for row in results),
        "skipped": sum(int(row.get("skipped") or 0) for row in results),
        "ctf_logs": sum(int(row.get("ctf_logs") or 0) for row in results),
        "rebate_logs": sum(int(row.get("rebate_logs") or 0) for row in results),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Index Polymarket REDEEM/MERGE/SPLIT/MAKER_REBATE cashflows.")
    add_db_cli_args(parser)
    parser.add_argument("--rpc", default=None)
    parser.add_argument("--from-block", type=int)
    parser.add_argument("--to-block", type=int)
    parser.add_argument("--continue-sync", action="store_true")
    parser.add_argument("--bootstrap-lookback-blocks", type=int, default=20000)
    parser.add_argument("--overlap-blocks", type=int, default=20)
    parser.add_argument("--confirmations", type=int, default=20)
    parser.add_argument("--batch", type=int, default=1000)
    parser.add_argument("--parallel-workers", type=int, default=1)
    parser.add_argument("--min-batch", type=int, default=100)
    parser.add_argument("--retry-attempts", type=int, default=3)
    parser.add_argument("--skip-block-time", action="store_true")
    parser.add_argument("--skip-raw-json", action="store_true")
    parser.add_argument("--window-delay", type=float, default=0.05)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--rebate-distributors", default=",".join(DEFAULT_REBATE_DISTRIBUTORS))
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_db_from_args(args)
    while True:
        summary = run_once(args)
        print(summary)
        if not args.watch:
            break
        time.sleep(max(1, int(args.interval_seconds)))


if __name__ == "__main__":
    main()
