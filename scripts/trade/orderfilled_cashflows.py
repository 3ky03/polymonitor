#!/usr/bin/env python3
"""Build address-view BUY/SELL cashflows from raw OrderFilled rows.

`orderfilled_raw` is the chain-fact layer: every supported exchange log is
stored once. This script turns those raw logs into the table that PnL should
use for TRADE cashflows:

    pnl_trade_cashflows

The conversion is intentionally address-view. A raw OrderFilled log has maker
and taker sides; for PnL each non-exchange participant receives one BUY or SELL
cashflow with the USDC notional for that fill.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from db import (  # noqa: E402
    add_db_cli_args,
    configure_db_from_args,
    describe_db_target,
    get_backend,
    get_connection,
    init_schema,
)
from trade.orderfilled_raw import ORDERFILLED_RAW_TABLE, ensure_orderfilled_raw_schema, normalize_address  # noqa: E402
from trade.trade_decoder import (  # noqa: E402
    CTF_EXCHANGE_ADDRESS,
    NEG_RISK_EXCHANGE_ADDRESS,
    POLYMARKET_EXCHANGE_2026_ADDRESS,
    POLYMARKET_EXCHANGE_2026_ALT_ADDRESS,
)


PNL_TRADE_CASHFLOWS_TABLE = "pnl_trade_cashflows"
USDC_DIVISOR = Decimal("1000000")
EXCHANGE_ADDRESSES = {
    normalize_address(CTF_EXCHANGE_ADDRESS),
    normalize_address(NEG_RISK_EXCHANGE_ADDRESS),
    normalize_address(POLYMARKET_EXCHANGE_2026_ADDRESS),
    normalize_address(POLYMARKET_EXCHANGE_2026_ALT_ADDRESS),
}


@dataclass(frozen=True)
class AddressCashflow:
    tx_hash: str
    log_index: int
    block_number: int
    block_time: str
    contract: str
    address: str
    counterparty: str
    maker: str
    taker: str
    role: str
    side: str
    usdc_amount: Decimal
    size: Decimal
    price: Decimal
    token_id: str
    order_hash: str
    source: str = "orderfilled_raw"


def decimal_from_any(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else "0").strip())
    except (InvalidOperation, ValueError):
        return Decimal("0")


def money_text(value: Decimal) -> str:
    return format(value, "f")


def row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "as_dict"):
        return row.as_dict()
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def normalize_tx_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text if text.startswith("0x") else "0x" + text


def opposite_side(side: str) -> str:
    if side == "BUY":
        return "SELL"
    if side == "SELL":
        return "BUY"
    return "UNKNOWN"


def infer_maker_side(row: dict[str, Any]) -> str:
    size = decimal_from_any(row.get("size"))
    maker_amount = decimal_from_any(row.get("maker_amount")) / USDC_DIVISOR
    taker_amount = decimal_from_any(row.get("taker_amount")) / USDC_DIVISOR
    size_tolerance = max(Decimal("0.000001"), abs(size) * Decimal("0.000001"))
    if size > 0:
        if abs(maker_amount - size) <= size_tolerance:
            return "SELL"
        if abs(taker_amount - size) <= size_tolerance:
            return "BUY"

    price = decimal_from_any(row.get("price"))
    notional = price * size
    notional_tolerance = max(Decimal("0.000001"), abs(notional) * Decimal("0.000001"))
    if notional > 0:
        if abs(maker_amount - notional) <= notional_tolerance:
            return "BUY"
        if abs(taker_amount - notional) <= notional_tolerance:
            return "SELL"

    side = str(row.get("side") or "").strip().upper()
    return side if side in {"BUY", "SELL"} else "UNKNOWN"


def usdc_amount_for_fill(row: dict[str, Any]) -> Decimal:
    size = decimal_from_any(row.get("size"))
    maker_amount = decimal_from_any(row.get("maker_amount")) / USDC_DIVISOR
    taker_amount = decimal_from_any(row.get("taker_amount")) / USDC_DIVISOR
    size_tolerance = max(Decimal("0.000001"), abs(size) * Decimal("0.000001"))
    if size > 0:
        if abs(maker_amount - size) <= size_tolerance and taker_amount:
            return taker_amount
        if abs(taker_amount - size) <= size_tolerance and maker_amount:
            return maker_amount
    return decimal_from_any(row.get("price")) * size


def ensure_cashflow_schema(conn) -> None:
    backend = get_backend()
    if backend == "mysql":
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {PNL_TRADE_CASHFLOWS_TABLE} (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                tx_hash CHAR(66) NOT NULL,
                log_index BIGINT NOT NULL,
                block_number BIGINT NOT NULL,
                block_time VARCHAR(40),
                contract CHAR(42) NOT NULL,
                address CHAR(42) NOT NULL,
                counterparty CHAR(42) NOT NULL,
                maker CHAR(42) NOT NULL,
                taker CHAR(42) NOT NULL,
                role VARCHAR(16) NOT NULL,
                side VARCHAR(16) NOT NULL,
                usdc_amount DECIMAL(38, 18) NOT NULL,
                size DECIMAL(38, 18) NOT NULL,
                price DECIMAL(38, 18) NOT NULL,
                token_id VARCHAR(128) NOT NULL,
                order_hash CHAR(66),
                source VARCHAR(64) NOT NULL DEFAULT 'orderfilled_raw',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_pnl_trade_cashflow (tx_hash, log_index, address),
                KEY idx_pnl_trade_cashflows_address_block (address, block_number),
                KEY idx_pnl_trade_cashflows_block_log (block_number, log_index),
                KEY idx_pnl_trade_cashflows_token_block (token_id, block_number)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        return

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PNL_TRADE_CASHFLOWS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            block_number INTEGER NOT NULL,
            block_time TEXT,
            contract TEXT NOT NULL,
            address TEXT NOT NULL,
            counterparty TEXT NOT NULL,
            maker TEXT NOT NULL,
            taker TEXT NOT NULL,
            role TEXT NOT NULL,
            side TEXT NOT NULL,
            usdc_amount TEXT NOT NULL,
            size TEXT NOT NULL,
            price TEXT NOT NULL,
            token_id TEXT NOT NULL,
            order_hash TEXT,
            source TEXT NOT NULL DEFAULT 'orderfilled_raw',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tx_hash, log_index, address)
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_pnl_trade_cashflows_address_block ON {PNL_TRADE_CASHFLOWS_TABLE}(address, block_number)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_pnl_trade_cashflows_block_log ON {PNL_TRADE_CASHFLOWS_TABLE}(block_number, log_index)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_pnl_trade_cashflows_token_block ON {PNL_TRADE_CASHFLOWS_TABLE}(token_id, block_number)"
    )


def iter_block_windows(from_block: int, to_block: int, batch_blocks: int):
    current = from_block
    while current <= to_block:
        end = min(current + batch_blocks - 1, to_block)
        yield current, end
        current = end + 1


def fetch_raw_rows(conn, from_block: int, to_block: int, address: Optional[str]) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    params: list[Any] = [from_block, to_block]
    address_filter = ""
    if address:
        address_filter = "AND (LOWER(maker) = ? OR LOWER(taker) = ?)"
        params.extend([address, address])
    cursor.execute(
        f"""
        SELECT
            tx_hash, log_index, block_number, block_time, contract,
            order_hash, maker, taker, token_id, side, price, size,
            maker_amount, taker_amount
        FROM {ORDERFILLED_RAW_TABLE}
        WHERE block_number BETWEEN ? AND ?
        {address_filter}
        ORDER BY block_number, tx_hash, log_index
        """,
        tuple(params),
    )
    return [row_to_dict(row) for row in cursor.fetchall()]


def cashflows_from_raw_row(row: dict[str, Any]) -> list[AddressCashflow]:
    maker = normalize_address(row.get("maker"))
    taker = normalize_address(row.get("taker"))
    contract = normalize_address(row.get("contract"))
    maker_side = infer_maker_side(row)
    if maker_side not in {"BUY", "SELL"}:
        return []
    usdc_amount = usdc_amount_for_fill(row)
    if usdc_amount <= 0:
        return []

    out: list[AddressCashflow] = []
    participants = (
        ("maker", maker, taker, maker_side),
        ("taker", taker, maker, opposite_side(maker_side)),
    )
    for role, address, counterparty, side in participants:
        if not address or address in EXCHANGE_ADDRESSES:
            continue
        out.append(
            AddressCashflow(
                tx_hash=normalize_tx_hash(row.get("tx_hash")),
                log_index=int(row.get("log_index") or 0),
                block_number=int(row.get("block_number") or 0),
                block_time=str(row.get("block_time") or ""),
                contract=contract,
                address=address,
                counterparty=counterparty,
                maker=maker,
                taker=taker,
                role=role,
                side=side,
                usdc_amount=usdc_amount,
                size=decimal_from_any(row.get("size")),
                price=decimal_from_any(row.get("price")),
                token_id=str(row.get("token_id") or "0"),
                order_hash=normalize_tx_hash(row.get("order_hash")),
            )
        )
    return out


def filter_internal_legs(rows: Sequence[AddressCashflow]) -> list[AddressCashflow]:
    rows_by_tx_address: dict[tuple[str, str], list[AddressCashflow]] = defaultdict(list)
    for row in rows:
        rows_by_tx_address[(row.tx_hash, row.address)].append(row)

    kept: list[AddressCashflow] = []
    for tx_address_rows in rows_by_tx_address.values():
        exchange_counterparty_contracts = {
            row.contract
            for row in tx_address_rows
            if row.contract in EXCHANGE_ADDRESSES and row.counterparty == row.contract
        }
        if not exchange_counterparty_contracts:
            kept.extend(tx_address_rows)
            continue
        for row in tx_address_rows:
            if row.contract in exchange_counterparty_contracts and row.counterparty != row.contract:
                continue
            kept.append(row)
    return kept


def delete_existing_cashflows(conn, from_block: int, to_block: int, address: Optional[str]) -> int:
    cursor = conn.cursor()
    if address:
        cursor.execute(
            f"""
            DELETE FROM {PNL_TRADE_CASHFLOWS_TABLE}
            WHERE block_number BETWEEN ? AND ? AND LOWER(address) = ?
            """,
            (from_block, to_block, address),
        )
    else:
        cursor.execute(
            f"""
            DELETE FROM {PNL_TRADE_CASHFLOWS_TABLE}
            WHERE block_number BETWEEN ? AND ?
            """,
            (from_block, to_block),
        )
    return max(0, int(getattr(cursor, "rowcount", 0) or 0))


def insert_cashflows(conn, rows: Sequence[AddressCashflow]) -> int:
    if not rows:
        return 0
    cursor = conn.cursor()
    cursor.executemany(
        f"""
        INSERT INTO {PNL_TRADE_CASHFLOWS_TABLE} (
            tx_hash, log_index, block_number, block_time, contract,
            address, counterparty, maker, taker, role, side,
            usdc_amount, size, price, token_id, order_hash, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tx_hash, log_index, address) DO UPDATE SET
            block_number = excluded.block_number,
            block_time = excluded.block_time,
            contract = excluded.contract,
            counterparty = excluded.counterparty,
            maker = excluded.maker,
            taker = excluded.taker,
            role = excluded.role,
            side = excluded.side,
            usdc_amount = excluded.usdc_amount,
            size = excluded.size,
            price = excluded.price,
            token_id = excluded.token_id,
            order_hash = excluded.order_hash,
            source = excluded.source,
            updated_at = CURRENT_TIMESTAMP
        """,
        [
            (
                row.tx_hash,
                row.log_index,
                row.block_number,
                row.block_time,
                row.contract,
                row.address,
                row.counterparty,
                row.maker,
                row.taker,
                row.role,
                row.side,
                money_text(row.usdc_amount),
                money_text(row.size),
                money_text(row.price),
                row.token_id,
                row.order_hash,
                row.source,
            )
            for row in rows
        ],
    )
    return max(0, int(getattr(cursor, "rowcount", 0) or 0))


def summarize_rows(rows: Iterable[AddressCashflow]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    rows_list = list(rows)
    for row in rows_list:
        counts[row.side.lower()] += 1
        totals[row.side.lower()] += row.usdc_amount
    return {
        "rows": len(rows_list),
        "buy_rows": counts.get("buy", 0),
        "sell_rows": counts.get("sell", 0),
        "buy_usdc": money_text(totals.get("buy", Decimal("0"))),
        "sell_usdc": money_text(totals.get("sell", Decimal("0"))),
    }


def build_cashflows(
    *,
    from_block: int,
    to_block: int,
    batch_blocks: int,
    address: Optional[str],
    delete_existing: bool,
    quiet: bool,
) -> dict[str, Any]:
    init_schema()
    conn = get_connection()
    ensure_orderfilled_raw_schema(conn)
    ensure_cashflow_schema(conn)

    total_raw = 0
    total_before_filter = 0
    total_inserted = 0
    total_deleted = 0
    final_rows: list[AddressCashflow] = []
    try:
        for start, end in iter_block_windows(from_block, to_block, max(1, batch_blocks)):
            raw_rows = fetch_raw_rows(conn, start, end, address)
            raw_cashflows: list[AddressCashflow] = []
            for raw_row in raw_rows:
                raw_cashflows.extend(cashflows_from_raw_row(raw_row))
            if address:
                raw_cashflows = [row for row in raw_cashflows if row.address == address]
            filtered = filter_internal_legs(raw_cashflows)
            if delete_existing:
                total_deleted += delete_existing_cashflows(conn, start, end, address)
            total_inserted += insert_cashflows(conn, filtered)
            conn.commit()

            total_raw += len(raw_rows)
            total_before_filter += len(raw_cashflows)
            final_rows.extend(filtered)
            if not quiet:
                print(
                    {
                        "from_block": start,
                        "to_block": end,
                        "raw_orderfilled_rows": len(raw_rows),
                        "address_cashflows_before_filter": len(raw_cashflows),
                        "address_cashflows_after_filter": len(filtered),
                    },
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        conn.close()

    return {
        "database": describe_db_target(),
        "table": PNL_TRADE_CASHFLOWS_TABLE,
        "from_block": from_block,
        "to_block": to_block,
        "address": address,
        "raw_orderfilled_rows": total_raw,
        "cashflow_rows_before_filter": total_before_filter,
        "cashflow_rows_after_filter": len(final_rows),
        "internal_rows_dropped": total_before_filter - len(final_rows),
        "deleted_existing_rows": total_deleted,
        "inserted_or_updated_rows": total_inserted,
        "totals": summarize_rows(final_rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build address-view PnL BUY/SELL cashflows from orderfilled_raw.")
    add_db_cli_args(parser)
    parser.add_argument("--from-block", type=int, required=True)
    parser.add_argument("--to-block", type=int, required=True)
    parser.add_argument("--batch", type=int, default=5000)
    parser.add_argument("--address", help="Optional single address rebuild")
    parser.add_argument("--no-delete-existing", action="store_true", help="Do not delete existing rows in the rebuild range")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_db_from_args(args)
    address = normalize_address(args.address) if args.address else None
    if address and len(address) != 42:
        raise SystemExit(f"Invalid address: {args.address}")
    summary = build_cashflows(
        from_block=int(args.from_block),
        to_block=int(args.to_block),
        batch_blocks=int(args.batch),
        address=address,
        delete_existing=not args.no_delete_existing,
        quiet=bool(args.quiet),
    )
    print(summary)


if __name__ == "__main__":
    main()
