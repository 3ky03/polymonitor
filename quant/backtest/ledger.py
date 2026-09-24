"""Backtest ledger helpers.

The ledger is account-view: cash deltas, share deltas, and running account
state are derived from simulated fills. It intentionally stays separate from
the trade table so later SPLIT/MERGE/REDEEM/REBATE events can be added without
rewriting trade history.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


Q = Decimal("0.0000000001")


def build_ledger_rows(trades: list[dict[str, Any]], initial_capital: Decimal) -> list[dict[str, Any]]:
    cash = Decimal(str(initial_capital))
    position = Decimal("0")
    rows: list[dict[str, Any]] = []
    ledger_index = 1
    emitted_entries: set[str] = set()
    for trade in trades:
        entry_order_id = str(trade.get("entry_order_id") or trade.get("trade_id") or "")
        exit_order_id = str(trade.get("exit_order_id") or "")
        entry_price = Decimal(str(trade.get("entry_price") or 0))
        if entry_order_id not in emitted_entries:
            entry_trades = [
                item for item in trades
                if str(item.get("entry_order_id") or item.get("trade_id") or "") == entry_order_id
            ]
            entry_size = sum((Decimal(str(item.get("size") or 0)) for item in entry_trades), Decimal("0"))
            entry_fee = sum(
                (
                    Decimal(str(item.get("entry_fee_cost")))
                    if item.get("entry_fee_cost") is not None
                    else Decimal(str(item.get("fee_cost") or 0)) / Decimal("2")
                    for item in entry_trades
                ),
                Decimal("0"),
            ).quantize(Q, rounding=ROUND_HALF_UP)
            entry_slippage = sum(
                (
                    Decimal(str(item.get("entry_slippage_cost")))
                    if item.get("entry_slippage_cost") is not None
                    else Decimal(str(item.get("slippage_cost") or 0)) / Decimal("2")
                    for item in entry_trades
                ),
                Decimal("0"),
            ).quantize(Q, rounding=ROUND_HALF_UP)
            entry_notional = (entry_price * entry_size).quantize(Q, rounding=ROUND_HALF_UP)
            # Slippage is already embedded in the simulated execution price.
            # Keep it as attribution, but do not subtract it twice from cash.
            cash_delta = -(entry_notional + entry_fee).quantize(Q, rounding=ROUND_HALF_UP)
            cash += cash_delta
            position += entry_size
            rows.append(_row(
                ledger_index=ledger_index,
                trade=trade,
                order_id=entry_order_id or None,
                event_type="BUY",
                x_value=int(trade["entry_x"]),
                shares_delta=entry_size,
                cash_delta=cash_delta,
                fee=entry_fee,
                slippage_cost=entry_slippage,
                execution_cost=entry_fee + entry_slippage,
                realized_pnl=Decimal("0"),
                position_after=position,
                cash_after=cash,
                price=entry_price,
            ))
            ledger_index += 1
            emitted_entries.add(entry_order_id)

        size = Decimal(str(trade.get("size") or 0))
        exit_price = Decimal(str(trade.get("exit_price") or 0))
        exit_notional = (exit_price * size).quantize(Q, rounding=ROUND_HALF_UP)
        fee = Decimal(str(trade.get("fee_cost") or 0)).quantize(Q, rounding=ROUND_HALF_UP)
        slippage = Decimal(str(trade.get("slippage_cost") or 0)).quantize(Q, rounding=ROUND_HALF_UP)
        buy_fee = (
            Decimal(str(trade.get("entry_fee_cost"))).quantize(Q, rounding=ROUND_HALF_UP)
            if trade.get("entry_fee_cost") is not None
            else (fee / Decimal("2")).quantize(Q, rounding=ROUND_HALF_UP)
        )
        sell_fee = (
            Decimal(str(trade.get("exit_fee_cost"))).quantize(Q, rounding=ROUND_HALF_UP)
            if trade.get("exit_fee_cost") is not None
            else (fee - buy_fee).quantize(Q, rounding=ROUND_HALF_UP)
        )
        buy_slippage = (
            Decimal(str(trade.get("entry_slippage_cost"))).quantize(Q, rounding=ROUND_HALF_UP)
            if trade.get("entry_slippage_cost") is not None
            else (slippage / Decimal("2")).quantize(Q, rounding=ROUND_HALF_UP)
        )
        sell_slippage = (
            Decimal(str(trade.get("exit_slippage_cost"))).quantize(Q, rounding=ROUND_HALF_UP)
            if trade.get("exit_slippage_cost") is not None
            else (slippage - buy_slippage).quantize(Q, rounding=ROUND_HALF_UP)
        )
        cash_delta = (exit_notional - sell_fee).quantize(Q, rounding=ROUND_HALF_UP)
        realized_pnl = Decimal(str(trade.get("pnl") or 0)).quantize(Q, rounding=ROUND_HALF_UP)
        cash += cash_delta
        position -= size
        rows.append(_row(
            ledger_index=ledger_index,
            trade=trade,
            order_id=exit_order_id or None,
            event_type="SETTLEMENT" if str(trade.get("exit_reason") or "").lower() == "settlement" else "SELL",
            x_value=int(trade["exit_x"]),
            shares_delta=-size,
            cash_delta=cash_delta,
            fee=sell_fee,
            slippage_cost=sell_slippage,
            execution_cost=sell_fee + sell_slippage,
            realized_pnl=realized_pnl,
            position_after=position,
            cash_after=cash,
            price=exit_price,
        ))
        ledger_index += 1
    return rows


def ledger_summary(rows: list[dict[str, Any]], initial_capital: Decimal) -> dict[str, Decimal]:
    cash = Decimal(str(initial_capital))
    position = Decimal("0")
    realized = Decimal("0")
    trade_exit = Decimal("0")
    settlement = Decimal("0")
    fees = Decimal("0")
    slippage = Decimal("0")
    rebate = Decimal("0")
    for row in rows:
        cash = Decimal(str(row.get("cash_after", cash)))
        position = Decimal(str(row.get("position_after", position)))
        realized += Decimal(str(row.get("realized_pnl") or 0))
        event_type = str(row.get("event_type") or "").upper()
        if event_type == "SETTLEMENT":
            settlement += Decimal(str(row.get("realized_pnl") or 0))
        elif event_type == "SELL":
            trade_exit += Decimal(str(row.get("realized_pnl") or 0))
        fees += Decimal(str(row.get("fee") or 0))
        slippage += Decimal(str(row.get("slippage_cost") or 0))
        rebate += Decimal(str(row.get("rebate") or 0))
    return {
        "cash_balance": cash.quantize(Q, rounding=ROUND_HALF_UP),
        "position_after": position.quantize(Q, rounding=ROUND_HALF_UP),
        "realized_pnl": realized.quantize(Q, rounding=ROUND_HALF_UP),
        "trade_exit_pnl": trade_exit.quantize(Q, rounding=ROUND_HALF_UP),
        "settlement_pnl": settlement.quantize(Q, rounding=ROUND_HALF_UP),
        "fee_total": fees.quantize(Q, rounding=ROUND_HALF_UP),
        "slippage_total": slippage.quantize(Q, rounding=ROUND_HALF_UP),
        "rebate_total": rebate.quantize(Q, rounding=ROUND_HALF_UP),
        "ledger_rows": Decimal(len(rows)),
    }


def _row(
    *,
    ledger_index: int,
    trade: dict[str, Any],
    order_id: str | None,
    event_type: str,
    x_value: int,
    shares_delta: Decimal,
    cash_delta: Decimal,
    fee: Decimal,
    slippage_cost: Decimal,
    execution_cost: Decimal,
    realized_pnl: Decimal,
    position_after: Decimal,
    cash_after: Decimal,
    price: Decimal,
) -> dict[str, Any]:
    return {
        "ledger_id": f"L-{ledger_index:04d}",
        "order_id": order_id,
        "trade_id": trade.get("trade_id"),
        "event_type": event_type,
        "x_axis": trade.get("x_axis", "block_number"),
        "x_value": x_value,
        "market_slug": trade.get("market_slug"),
        "token_side": trade.get("token_side"),
        "shares_delta": shares_delta.quantize(Q, rounding=ROUND_HALF_UP),
        "cash_delta": cash_delta.quantize(Q, rounding=ROUND_HALF_UP),
        "fee": fee.quantize(Q, rounding=ROUND_HALF_UP),
        "rebate": Decimal("0"),
        "slippage_cost": slippage_cost.quantize(Q, rounding=ROUND_HALF_UP),
        "execution_cost": execution_cost.quantize(Q, rounding=ROUND_HALF_UP),
        "realized_pnl": realized_pnl.quantize(Q, rounding=ROUND_HALF_UP),
        "position_after": position_after.quantize(Q, rounding=ROUND_HALF_UP),
        "cash_after": cash_after.quantize(Q, rounding=ROUND_HALF_UP),
        "price": price,
        "source": "simulated_trade",
        "meta": {"trade_id": trade.get("trade_id"), "exit_reason": trade.get("exit_reason")},
    }
