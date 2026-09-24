#!/usr/bin/env python3
"""Audit and repair raw Polymarket OrderFilled coverage by block window.

Guarantee boundary:
  For the known exchange contracts and known OrderFilled ABIs in
  trade_decoder.py, each audited finalized window is complete when
  chain getLogs count == orderfilled_raw DB count.

This script does not depend on market discovery and is safe to rerun because
orderfilled_raw is keyed by (contract, tx_hash, log_index).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

try:
    from web3 import Web3
except ImportError as exc:
    raise SystemExit("web3 is required: pip install web3") from exc

from config import get_rpc_url
from db import add_db_cli_args, configure_db_from_args, describe_db_target, get_connection, init_schema
from trade.rpc_utils import build_web3
from trade.trade_decoder import (
    CTF_EXCHANGE_ADDRESS,
    NEG_RISK_EXCHANGE_ADDRESS,
    POLYMARKET_EXCHANGE_2026_ADDRESS,
    POLYMARKET_EXCHANGE_2026_ALT_ADDRESS,
    decode_order_filled_log,
    fast_decode_order_filled_log,
    get_order_filled_event_decoders,
    get_order_filled_topics,
)
from trade.orderfilled_raw import (
    count_orderfilled_raw,
    ensure_orderfilled_raw_schema,
    insert_orderfilled_raw_batch,
    orderfilled_raw_row,
    upsert_orderfilled_sync_window,
)


USDC_DIVISOR = Decimal("1000000")
DEFAULT_BATCH_BLOCKS = 5000


def iter_block_windows(from_block: int, to_block: int, batch_blocks: int):
    current = from_block
    while current <= to_block:
        end = min(current + batch_blocks - 1, to_block)
        yield current, end
        current = end + 1


def iso_from_block(w3: Web3, block_number: int, cache: dict[int, str]) -> str:
    if block_number not in cache:
        block = w3.eth.get_block(block_number)
        cache[block_number] = datetime.fromtimestamp(int(block["timestamp"]), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return cache[block_number]


def token_size(decoded: dict[str, Any]) -> Decimal:
    token_id = str(decoded.get("tokenId") or "")
    maker_asset = str(decoded.get("makerAssetId") or "")
    if token_id == maker_asset:
        raw = Decimal(str(decoded.get("makerAmountFilled") or "0"))
    else:
        raw = Decimal(str(decoded.get("takerAmountFilled") or "0"))
    return raw / USDC_DIVISOR


def load_cached_block_timestamps(conn: Any, logs: list[Any], cache: dict[int, str]) -> None:
    """Warm block timestamp cache from the shared block_timestamps table.

    The legacy trades indexer already maintains this table. Reusing it avoids
    one eth_getBlockByNumber call per block during raw OrderFilled backfills.
    """

    block_numbers = sorted(
        {
            int(log.get("blockNumber") or 0)
            for log in logs
            if int(log.get("blockNumber") or 0) and int(log.get("blockNumber") or 0) not in cache
        }
    )
    if not block_numbers:
        return

    cursor = conn.cursor()
    chunk_size = 1000
    for offset in range(0, len(block_numbers), chunk_size):
        chunk = block_numbers[offset : offset + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cursor.execute(
            f"SELECT block_number, timestamp FROM block_timestamps WHERE block_number IN ({placeholders})",
            chunk,
        )
        for row in cursor.fetchall():
            block_number = int(row["block_number"] if hasattr(row, "get") else row[0])
            timestamp = row["timestamp"] if hasattr(row, "get") else row[1]
            cache[block_number] = str(timestamp or "")


def fetch_orderfilled_logs(w3: Web3, from_block: int, to_block: int) -> list[Any]:
    legacy_topic, topic_2026 = get_order_filled_topics(w3)
    return list(
        w3.eth.get_logs(
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
    )


def decode_logs_for_raw(
    w3: Web3,
    logs: list[Any],
    block_ts_cache: dict[int, str],
    *,
    include_block_time: bool = True,
    include_raw_json: bool = True,
    fast_decode: bool = True,
) -> list[dict[str, Any]]:
    event_decoder = get_order_filled_event_decoders(w3)
    rows: list[dict[str, Any]] = []
    for log in logs:
        decoded = fast_decode_order_filled_log(log, w3=w3) if fast_decode else None
        if not decoded:
            decoded = decode_order_filled_log(log, w3=w3, event_decoder=event_decoder)
        if not decoded:
            continue
        block_number = int(log.get("blockNumber") or 0)
        decoded["block_number"] = block_number
        decoded["timestamp"] = iso_from_block(w3, block_number, block_ts_cache) if include_block_time else ""
        decoded["size"] = str(token_size(decoded))
        topic0 = log["topics"][0] if log.get("topics") else ""
        event_topic = topic0.hex() if hasattr(topic0, "hex") else str(topic0)
        decoded["event_topic"] = event_topic
        rows.append(orderfilled_raw_row(decoded, event_topic=event_topic, include_raw_json=include_raw_json))
    return rows


def audit_window(
    *,
    conn: Any,
    w3: Web3,
    from_block: int,
    to_block: int,
    repair: bool,
    quiet: bool,
    include_block_time: bool = True,
    include_raw_json: bool = True,
    fast_decode: bool = True,
) -> dict[str, Any]:
    block_ts_cache: dict[int, str] = {}
    try:
        logs = fetch_orderfilled_logs(w3, from_block, to_block)
        chain_count = len(logs)
        before_count = count_orderfilled_raw(conn, from_block, to_block)
        repaired_count = 0
        if repair and chain_count != before_count:
            if include_block_time:
                load_cached_block_timestamps(conn, logs, block_ts_cache)
            raw_rows = decode_logs_for_raw(
                w3,
                logs,
                block_ts_cache,
                include_block_time=include_block_time,
                include_raw_json=include_raw_json,
                fast_decode=fast_decode,
            )
            repaired_count = insert_orderfilled_raw_batch(conn, raw_rows)
        after_count = count_orderfilled_raw(conn, from_block, to_block)
        status = "complete" if chain_count == after_count else "incomplete"
        upsert_orderfilled_sync_window(
            conn,
            from_block=from_block,
            to_block=to_block,
            chain_log_count=chain_count,
            db_log_count=after_count,
            repaired_count=repaired_count,
            status=status,
        )
        result = {
            "from_block": from_block,
            "to_block": to_block,
            "chain_log_count": chain_count,
            "db_log_count_before": before_count,
            "db_log_count_after": after_count,
            "repaired_count": repaired_count,
            "missing_after": max(0, chain_count - after_count),
            "status": status,
        }
        if not quiet:
            print(result, file=sys.stderr, flush=True)
        return result
    except Exception as exc:
        upsert_orderfilled_sync_window(
            conn,
            from_block=from_block,
            to_block=to_block,
            chain_log_count=0,
            db_log_count=count_orderfilled_raw(conn, from_block, to_block),
            repaired_count=0,
            status="error",
            last_error=str(exc),
        )
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit and repair raw OrderFilled DB coverage.")
    add_db_cli_args(parser)
    parser.add_argument("--from-block", type=int, required=True)
    parser.add_argument("--to-block", type=int, help="Default: latest - confirmations")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH_BLOCKS)
    parser.add_argument("--confirmations", type=int, default=20)
    parser.add_argument("--rpc", default=None)
    parser.add_argument("--audit-only", action="store_true", help="Only compare counts; do not insert missing raw rows")
    parser.add_argument(
        "--skip-block-time",
        action="store_true",
        help="Do not fetch/store block_time during raw repair; hydrate it later from block_number.",
    )
    parser.add_argument(
        "--skip-raw-json",
        action="store_true",
        help="Do not store decoded raw_json during raw repair; all normalized OrderFilled columns are still stored.",
    )
    parser.add_argument("--web3-abi-decode", action="store_true", help="Use Web3 ABI event decoder instead of fast static decoder.")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_db_from_args(args)
    rpc_url = args.rpc or get_rpc_url()
    w3 = build_web3(rpc_url)
    latest = int(w3.eth.block_number)
    to_block = args.to_block if args.to_block is not None else max(0, latest - max(0, args.confirmations))
    if args.from_block > to_block:
        raise SystemExit(f"from_block {args.from_block} is after to_block {to_block}")

    init_schema()
    conn = get_connection()
    ensure_orderfilled_raw_schema(conn)
    if not args.quiet:
        print(f"Database target: {describe_db_target()}", file=sys.stderr)
        print(
            f"Auditing OrderFilled raw coverage blocks {args.from_block}-{to_block}, repair={not args.audit_only}",
            file=sys.stderr,
        )

    results = []
    try:
        for start, end in iter_block_windows(args.from_block, to_block, max(1, int(args.batch))):
            results.append(
                audit_window(
                    conn=conn,
                    w3=w3,
                    from_block=start,
                    to_block=end,
                    repair=not args.audit_only,
                    quiet=args.quiet,
                    include_block_time=not args.skip_block_time,
                    include_raw_json=not args.skip_raw_json,
                    fast_decode=not args.web3_abi_decode,
                )
            )
            time.sleep(0.05)
    finally:
        conn.close()

    complete = sum(1 for row in results if row["status"] == "complete")
    total_missing = sum(int(row["missing_after"]) for row in results)
    total_repaired = sum(int(row["repaired_count"]) for row in results)
    print(
        {
            "windows": len(results),
            "complete_windows": complete,
            "incomplete_windows": len(results) - complete,
            "total_repaired": total_repaired,
            "total_missing_after": total_missing,
            "all_complete": complete == len(results) and total_missing == 0,
        }
    )


if __name__ == "__main__":
    main()
