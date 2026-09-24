#!/usr/bin/env python3
"""ClickHouse writers for OrderFilled and non-trade cashflow live sync.

The helpers in this module intentionally keep the ClickHouse schema aligned
with /data2 PostgreSQL market metadata. They do not write MySQL-only columns
such as id, created_at, block_time, mysql_market_id, or mysql_outcome_code.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Sequence

from db.trade_v2 import normalize_outcome_code, normalize_side_code


DEFAULT_CLICKHOUSE_CONTAINER = "polydata_clickhouse_orderfilled"
DEFAULT_CLICKHOUSE_DATABASE = "poly_orderfilled"
DEFAULT_CLICKHOUSE_USER = "poly_user"
DEFAULT_CLICKHOUSE_PASSWORD = ""


@dataclass(frozen=True)
class ClickHouseOrderFilledSettings:
    container: str = DEFAULT_CLICKHOUSE_CONTAINER
    database: str = DEFAULT_CLICKHOUSE_DATABASE
    user: str = DEFAULT_CLICKHOUSE_USER
    password: str = DEFAULT_CLICKHOUSE_PASSWORD
    orderfilled_insert_table: str = "orderfilled_fact"

    @property
    def orderfilled_read_table(self) -> str:
        if self.orderfilled_insert_table == "orderfilled_fact_buffer":
            return "orderfilled_fact_buffer"
        return "orderfilled_fact"


def add_clickhouse_orderfilled_cli_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--clickhouse-write-mode",
        choices=("none", "dual", "clickhouse"),
        default=os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_WRITE_MODE", "none"),
        help=(
            "OrderFilled/non-trade ClickHouse write mode. none keeps current DB-only "
            "writes; dual writes current DB plus ClickHouse; clickhouse writes "
            "ClickHouse as the primary trade/cashflow sink while still using the "
            "configured DB for sync metadata."
        ),
    )
    parser.add_argument("--clickhouse-container", default=os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_CONTAINER", DEFAULT_CLICKHOUSE_CONTAINER))
    parser.add_argument("--clickhouse-database", default=os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_DATABASE", DEFAULT_CLICKHOUSE_DATABASE))
    parser.add_argument("--clickhouse-user", default=os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_USER", DEFAULT_CLICKHOUSE_USER))
    parser.add_argument("--clickhouse-password", default=os.environ.get("CLICKHOUSE_PASSWORD", DEFAULT_CLICKHOUSE_PASSWORD))
    parser.add_argument(
        "--clickhouse-orderfilled-insert-table",
        choices=("orderfilled_fact", "orderfilled_fact_buffer"),
        default=os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_INSERT_TABLE", "orderfilled_fact"),
        help=(
            "ClickHouse table used for OrderFilled inserts. orderfilled_fact_buffer "
            "uses the ClickHouse Buffer engine and is intended for live sync."
        ),
    )


def settings_from_args(args: argparse.Namespace) -> ClickHouseOrderFilledSettings:
    if args.clickhouse_write_mode != "none" and not args.clickhouse_password:
        raise SystemExit("--clickhouse-password or CLICKHOUSE_PASSWORD is required when ClickHouse writes are enabled")
    return ClickHouseOrderFilledSettings(
        container=args.clickhouse_container,
        database=args.clickhouse_database,
        user=args.clickhouse_user,
        password=args.clickhouse_password,
        orderfilled_insert_table=args.clickhouse_orderfilled_insert_table,
    )


def _clickhouse_cmd(settings: ClickHouseOrderFilledSettings, query: str) -> list[str]:
    return [
        "docker",
        "exec",
        "-i",
        settings.container,
        "clickhouse-client",
        "--user",
        settings.user,
        "--password",
        settings.password,
        "--database",
        settings.database,
        "--query",
        query,
    ]


def clickhouse_scalar(settings: ClickHouseOrderFilledSettings, query: str) -> str:
    cmd = _clickhouse_cmd(settings, query)
    cmd.remove("-i")
    return subprocess.check_output(cmd, text=True).strip()


def _tsv_escape(value: Any) -> str:
    if value is None:
        return r"\N"
    if isinstance(value, Decimal):
        text = format(value, "f")
    else:
        text = str(value)
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def _hex_text(value: Any, *, width: int | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        text = bytes(value).hex()
    else:
        text = str(value).strip()
        if text.startswith(("0x", "0X")):
            text = text[2:]
        elif text.isdigit() and width:
            text = hex(int(text))[2:]
    text = text.lower()
    if width:
        text = text.zfill(width)
    return text


def _address_text(value: Any) -> str:
    return _hex_text(value, width=40)


def _token_text(value: Any) -> str:
    return _hex_text(value, width=64)


def _event_key(row: dict[str, Any]) -> str:
    return ":".join(
        [
            _address_text(row.get("source_contract")),
            _hex_text(row.get("tx_hash"), width=64),
            str(int(row.get("log_index") or 0)),
            str(row.get("cashflow_type") or ""),
            _address_text(row.get("address")),
        ]
    )


def _orderfilled_row_to_tsv(row: dict[str, Any]) -> str:
    values = [
        _hex_text(row.get("tx_hash"), width=64),
        int(row.get("log_index") or 0),
        int(row.get("market_id") or 0),
        row.get("condition_id") or "",
        _token_text(row.get("token_id")),
        int(row.get("outcome_code") or normalize_outcome_code(row.get("outcome")) or 0),
        _address_text(row.get("maker")),
        _address_text(row.get("taker")),
        int(row.get("side_code") or normalize_side_code(row.get("side")) or 0),
        row.get("price") or "0",
        row.get("size") or "0",
        int(row.get("block_number") or 0),
        _hex_text(row.get("order_hash"), width=64),
        str(row.get("contract") or "").lower(),
        row.get("maker_amount"),
        row.get("taker_amount"),
        row.get("fee"),
    ]
    return "\t".join(_tsv_escape(v) for v in values)


def _non_trade_row_to_tsv(row: dict[str, Any]) -> str:
    values = [
        _event_key(row),
        _address_text(row.get("address")),
        row.get("cashflow_type") or "",
        row.get("usdc_amount") or Decimal("0"),
        _address_text(row.get("collateral_token")),
        row.get("condition_id") or "",
        _hex_text(row.get("parent_collection_id"), width=64),
        row.get("partition_json") or "",
        _hex_text(row.get("tx_hash"), width=64),
        int(row.get("log_index") or 0),
        int(row.get("block_number") or 0),
        _address_text(row.get("source_contract")),
        row.get("source_event") or "",
        row.get("source") or "",
    ]
    return "\t".join(_tsv_escape(v) for v in values)


def existing_orderfilled_keys(
    settings: ClickHouseOrderFilledSettings,
    from_block: int,
    to_block: int,
) -> set[tuple[str, int]]:
    output = clickhouse_scalar(
        settings,
        f"""
        SELECT tx_hash, log_index
        FROM {settings.orderfilled_read_table}
        WHERE block_number BETWEEN {int(from_block)} AND {int(to_block)}
        FORMAT TabSeparated
        """,
    )
    keys: set[tuple[str, int]] = set()
    for line in output.splitlines():
        if not line:
            continue
        tx_hash, log_index = line.split("\t", 1)
        keys.add((tx_hash.lower(), int(log_index)))
    return keys


def count_orderfilled_rows(settings: ClickHouseOrderFilledSettings, from_block: int, to_block: int) -> int:
    return int(
        clickhouse_scalar(
            settings,
            f"SELECT count() FROM {settings.orderfilled_read_table} WHERE block_number BETWEEN {int(from_block)} AND {int(to_block)}",
        )
        or 0
    )


def count_non_trade_cashflow_rows(settings: ClickHouseOrderFilledSettings, from_block: int, to_block: int) -> int:
    return int(
        clickhouse_scalar(
            settings,
            f"SELECT count() FROM non_trade_cashflows WHERE block_number BETWEEN {int(from_block)} AND {int(to_block)}",
        )
        or 0
    )


def insert_orderfilled_fact_rows(
    settings: ClickHouseOrderFilledSettings,
    rows: Sequence[dict[str, Any]],
    *,
    skip_existing: bool = True,
) -> int:
    if not rows:
        return 0
    filtered = list(rows)
    if skip_existing:
        blocks = [int(row.get("block_number") or 0) for row in rows if row.get("block_number") is not None]
        if blocks:
            existing = existing_orderfilled_keys(settings, min(blocks), max(blocks))
            if existing:
                filtered = [
                    row
                    for row in rows
                    if (_hex_text(row.get("tx_hash"), width=64), int(row.get("log_index") or 0)) not in existing
                ]
    if not filtered:
        return 0

    payload = "\n".join(_orderfilled_row_to_tsv(row) for row in filtered) + "\n"
    subprocess.run(
        _clickhouse_cmd(
            settings,
            f"INSERT INTO {settings.orderfilled_insert_table} "
            "(tx_hash, log_index, market_id, condition_id, token_id, outcome_code, "
            "maker, taker, side_code, price, size, block_number, order_hash, "
            "contract, maker_amount, taker_amount, fee) FORMAT TabSeparated",
        ),
        input=payload,
        text=True,
        check=True,
    )
    return len(filtered)


def insert_non_trade_cashflow_rows(
    settings: ClickHouseOrderFilledSettings,
    rows: Sequence[dict[str, Any]],
) -> int:
    if not rows:
        return 0
    payload = "\n".join(_non_trade_row_to_tsv(row) for row in rows) + "\n"
    subprocess.run(
        _clickhouse_cmd(
            settings,
            "INSERT INTO non_trade_cashflows "
            "(event_key, address, cashflow_type, usdc_amount, collateral_token, "
            "condition_id, parent_collection_id, partition_json, tx_hash, log_index, "
            "block_number, source_contract, source_event, source) FORMAT TabSeparated",
        ),
        input=payload,
        text=True,
        check=True,
    )
    return len(rows)
