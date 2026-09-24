"""Order lifecycle rows for quant backtests."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def next_order_id(index: int) -> str:
    return f"O-{int(index):04d}"


def order_status_from_fill(fill: dict[str, Any]) -> str:
    status = str(fill.get("fill_status") or "").upper()
    if status == "FILLED":
        return "FILLED"
    if status in {"PARTIAL", "PARTIAL_FILLED"}:
        return "PARTIAL_FILLED"
    if status == "EXPIRED":
        return "EXPIRED"
    if fill.get("rejected"):
        notes = fill.get("notes") or []
        if isinstance(notes, list) and any(
            item in notes
            for item in (
                "no_orderfilled_volume",
                "buy_limit_not_crossed",
                "sell_limit_not_crossed",
                "limit_not_crossed",
                "terminal_price_limit_not_fillable",
                "unresolved_without_settlement_value",
            )
        ):
            return "NO_FILL"
        return "REJECTED"
    if Decimal(str(fill.get("filled_size") or fill.get("size") or 0)) <= 0:
        return "NO_FILL"
    return status or "SUBMITTED"


def no_fill_reason(fill: dict[str, Any]) -> str | None:
    notes = fill.get("notes")
    if isinstance(notes, list) and notes:
        return ",".join(str(item) for item in notes)
    if isinstance(notes, str) and notes:
        return notes
    status = order_status_from_fill(fill)
    if status in {"NO_FILL", "REJECTED", "CANCELED", "CANCEL_FAILED"}:
        return status.lower()
    return None


def order_from_fill(
    *,
    order_id: str,
    signal_index: int,
    x_axis: str,
    x_value: int,
    side: str,
    role: str,
    order_type: str,
    decision_price: Decimal,
    fill: dict[str, Any],
    trade_id: str | None = None,
    latency_seconds: Decimal = Decimal("0"),
    latency_blocks: int = 0,
) -> dict[str, Any]:
    avg_fill_price = fill.get("avg_fill_price") or fill.get("entry_price") or fill.get("exit_price")
    status = order_status_from_fill(fill)
    requested_size = Decimal(str(fill.get("requested_size") or fill.get("size") or 0))
    requested_notional = Decimal(str(fill.get("requested_notional") or 0))
    if status in {"NO_FILL", "REJECTED", "CANCELED", "CANCEL_FAILED"}:
        filled_size = Decimal("0")
        filled_notional = Decimal("0")
        unfilled_size = requested_size
    else:
        filled_size = Decimal(str(fill.get("filled_size") or fill.get("size") or 0))
        filled_notional = Decimal(str(fill.get("filled_notional") or 0))
        unfilled_size = Decimal(str(fill.get("unfilled_size") or 0))
    return {
        "order_id": order_id,
        "signal_index": int(signal_index),
        "trade_id": trade_id,
        "x_axis": x_axis,
        "signal_x": int(x_value),
        "submit_x": int(x_value) + int(latency_blocks or 0) if x_axis == "block_number" else int(x_value),
        "decision_price": decision_price,
        "requested_price": avg_fill_price or decision_price,
        "side": side,
        "role": role,
        "order_type": order_type,
        "status": status,
        "requested_size": requested_size,
        "requested_notional": requested_notional,
        "filled_size": filled_size,
        "filled_notional": filled_notional,
        "unfilled_size": unfilled_size,
        "avg_fill_price": avg_fill_price,
        "fill_probability": Decimal(str(fill.get("fill_probability") or 0)),
        "fill_pct": Decimal(str(fill.get("fill_pct") or 0)),
        "block_volume": Decimal(str(fill.get("block_volume") or 0)),
        "trade_count": int(fill.get("trade_count") or 0),
        "available_notional": Decimal(str(fill.get("available_notional") or 0)),
        "fee_cost": Decimal(str(fill.get("fee_cost") or 0)),
        "slippage_cost": Decimal(str(fill.get("slippage_cost") or 0)),
        "execution_cost": Decimal(str(fill.get("execution_cost") or 0)),
        "latency_blocks": int(latency_blocks or 0),
        "latency_seconds": Decimal(str(latency_seconds or 0)),
        "no_fill_reason": no_fill_reason(fill),
        "execution_source": str(fill.get("execution_source") or "unknown"),
        "meta": fill,
    }


def summarize_orders(orders: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "signal_count": len(orders),
        "submitted_count": len(orders),
        "filled_count": 0,
        "partial_fill_count": 0,
        "no_fill_count": 0,
        "rejected_count": 0,
        "expired_count": 0,
    }
    for order in orders:
        status = str(order.get("status") or "").upper()
        if status == "FILLED":
            counts["filled_count"] += 1
        elif status == "PARTIAL_FILLED":
            counts["partial_fill_count"] += 1
        elif status == "NO_FILL":
            counts["no_fill_count"] += 1
        elif status == "REJECTED":
            counts["rejected_count"] += 1
        elif status == "EXPIRED":
            counts["expired_count"] += 1
    return counts
