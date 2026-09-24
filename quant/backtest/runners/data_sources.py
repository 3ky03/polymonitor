"""Replay data sources for validation runners."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
from typing import Any

from ..backtest_engine import PricePoint
from ...core.db import ClickHouseClient, ClickHouseSettings, postgres_connection, safe_identifier
from .execution_replay import ReplayTradeEvent


@dataclass(frozen=True)
class MarketCandidate:
    market_id: int
    market_slug: str
    token_id: str | None
    token_side: str
    from_block: int | None
    to_block: int | None
    title: str = ""


class FixtureReplayStore:
    mode = "fixture"

    def data_version(self, name: str = "fixture") -> str:
        return hashlib.sha1(name.encode("utf-8")).hexdigest()[:20]

    def load_bars(self, name: str = "nba_fixture") -> list[PricePoint]:
        rows = {
            "no_trade": [
                (100, "0.52", "4", 1),
                (101, "0.51", "5", 2),
                (102, "0.50", "7", 2),
            ],
            "settlement_zero": [
                (200, "0.62", "30", 4),
                (201, "0.48", "40", 5),
                (202, "0.70", "12", 1),
                (203, "0", "0", 0),
            ],
            "settlement_one": [
                (210, "0.62", "30", 4),
                (211, "0.48", "40", 5),
                (212, "0.70", "12", 1),
                (213, "1", "0", 0),
            ],
        }.get(name)
        if rows is None:
            rows = [
                (100, "0.60", "20", 1),
                (101, "0.48", "30", 2),
                (102, "0.66", "30", 2),
            ]
        return [PricePoint(x_value=block, price=Decimal(price), volume=Decimal(volume), trade_count=trade_count) for block, price, volume, trade_count in rows]

    def load_trade_events(self, name: str = "single_fill") -> list[ReplayTradeEvent]:
        rows = {
            "single_fill": [
                # Same block but before submit, must not fill.
                (1, "token-yes", 100, 0, 1, "0xaaa", "0.49", "100"),
                # Same block after submit, valid candidate.
                (1, "token-yes", 100, 0, 3, "0xaab", "0.48", "10"),
                (1, "token-yes", 101, 0, 1, "0xaac", "0.47", "4"),
            ],
            "lifecycle": [
                (1, "token-yes", 100, 0, 5, "0xb01", "0.49", "10"),
                (1, "token-yes", 101, 0, 1, "0xb02", "0.48", "3"),
                (1, "token-yes", 102, 0, 1, "0xb03", "0.47", "3"),
            ],
            "illiquid": [
                (1, "token-yes", 300, 0, 1, "0xc01", "0.49", "2"),
                (1, "token-yes", 301, 0, 1, "0xc02", "0.51", "1"),
            ],
        }.get(name, [])
        return [
            ReplayTradeEvent(
                market_id=market_id,
                token_id=token_id,
                block_number=block,
                transaction_index=tx_index,
                log_index=log_index,
                tx_hash=tx_hash,
                trade_price=Decimal(price),
                size=Decimal(size),
            )
            for market_id, token_id, block, tx_index, log_index, tx_hash, price, size in rows
        ]

    def account_events(self) -> list[dict[str, Any]]:
        return [
            {"address": "0xabc", "side": "BUY_YES", "price": Decimal("0.40"), "size": Decimal("10"), "fee": Decimal("0"), "block_number": 10},
            {"address": "0xdef", "side": "BUY_YES", "price": Decimal("0.45"), "size": Decimal("5"), "fee": Decimal("0"), "block_number": 11},
            {"address": "0xabc", "side": "SELL_YES", "price": Decimal("0.55"), "size": Decimal("4"), "fee": Decimal("0"), "block_number": 12},
        ]


class PostgresBlockCloseStore:
    mode = "db"

    def load_bars(self, candidate: MarketCandidate, *, limit: int = 25000) -> list[PricePoint]:
        filters: list[str] = []
        values: list[Any] = []
        if candidate.token_id:
            filters.append("token_id = %s")
            values.append(candidate.token_id)
        else:
            filters.extend(["market_slug = %s", "token_side = %s"])
            values.extend([candidate.market_slug, candidate.token_side])
        if candidate.from_block is not None:
            filters.append("block_number >= %s")
            values.append(candidate.from_block)
        if candidate.to_block is not None:
            filters.append("block_number <= %s")
            values.append(candidate.to_block)
        values.append(limit)
        with postgres_connection(readonly=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT block_number AS x_value, close_price AS price, volume, trade_count, block_timestamp
                    FROM quant.market_token_block_close
                    WHERE {" AND ".join(filters)}
                    ORDER BY block_number ASC
                    LIMIT %s
                    """,
                    values,
                )
                return [
                    PricePoint(
                        x_value=int(row["x_value"]),
                        price=Decimal(str(row["price"])),
                        volume=Decimal(str(row["volume"] or 0)),
                        trade_count=int(row["trade_count"] or 0),
                        timestamp=row.get("block_timestamp"),
                    )
                    for row in cur.fetchall()
                ]


class ClickHouseOrderFilledStore:
    mode = "db"

    def __init__(self, settings: ClickHouseSettings | None = None) -> None:
        self.client = ClickHouseClient(settings)

    def load_trade_events(
        self,
        *,
        market_id: int,
        token_id: str,
        from_block: int,
        to_block: int,
        limit: int = 10000,
    ) -> list[ReplayTradeEvent]:
        table = safe_identifier(self.client.settings.orderfilled_table)
        database = safe_identifier(self.client.settings.database)
        has_transaction_index = self._has_column(database, table, "transaction_index")
        tx_expr = "transaction_index" if has_transaction_index else "toUInt32(0)"
        rows = self.client.query_json_rows(
            f"""
            SELECT
                market_id,
                token_id,
                block_number,
                {tx_expr} AS transaction_index,
                log_index,
                lower(tx_hash) AS tx_hash,
                price AS trade_price,
                size
            FROM {table}
            WHERE market_id = {int(market_id)}
              AND token_id = '{_ch_escape(token_id)}'
              AND block_number >= {int(from_block)}
              AND block_number <= {int(to_block)}
            ORDER BY block_number ASC, transaction_index ASC, log_index ASC, tx_hash ASC
            LIMIT {int(limit)}
            """
        )
        return [
            ReplayTradeEvent(
                market_id=int(row["market_id"]),
                token_id=str(row["token_id"]),
                block_number=int(row["block_number"]),
                transaction_index=int(row.get("transaction_index") or 0),
                log_index=int(row["log_index"]),
                tx_hash=str(row["tx_hash"]),
                trade_price=Decimal(str(row["trade_price"])),
                size=Decimal(str(row["size"])),
            )
            for row in rows
        ]

    def _has_column(self, database: str, table: str, column: str) -> bool:
        value = self.client.query_scalar(
            f"""
            SELECT count()
            FROM system.columns
            WHERE database = '{_ch_escape(database)}'
              AND table = '{_ch_escape(table)}'
              AND name = '{_ch_escape(column)}'
            """
        )
        return str(value).strip() not in {"", "0"}


def _ch_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")
