"""OrderFilled-first execution replay helpers.

The helpers model Polymarket orders as resting limit orders over immutable
historical fills. Price crossing makes a fill candidate; available historical
volume and the liquidity cap decide actual fill size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal


Q = Decimal("0.0000000001")
TimeInForce = Literal["GTC", "GTD", "FOK", "FAK"]
OrderStatus = Literal["FILLED", "PARTIAL_FILLED", "NO_FILL", "REJECTED", "EXPIRED"]
OrderSide = Literal["BUY_YES", "SELL_YES"]
SequenceKey = tuple[int, int, int, str]


def sequence_key(
    block_number: int,
    transaction_index: int | None = None,
    log_index: int | None = None,
    tx_hash: str | None = None,
) -> SequenceKey:
    return (
        int(block_number),
        int(transaction_index or 0),
        int(log_index or 0),
        str(tx_hash or ""),
    )


@dataclass(frozen=True)
class ReplayTradeEvent:
    market_id: int
    token_id: str
    block_number: int
    log_index: int
    tx_hash: str
    trade_price: Decimal
    size: Decimal
    transaction_index: int = 0
    maker: str | None = None
    taker: str | None = None
    side_code: str | None = None

    @property
    def event_sequence(self) -> SequenceKey:
        return sequence_key(self.block_number, self.transaction_index, self.log_index, self.tx_hash)


@dataclass(frozen=True)
class OrderIntent:
    side: OrderSide
    limit_price: Decimal
    size: Decimal
    time_in_force: TimeInForce = "GTC"
    submit_sequence: SequenceKey = field(default_factory=lambda: sequence_key(0))
    expire_sequence: SequenceKey | None = None
    liquidity_cap_pct: Decimal = Decimal("100")
    fee_bps: Decimal = Decimal("0")
    order_id: str = "O-0001"


@dataclass
class OrderReplayResult:
    order_id: str
    status: OrderStatus
    filled_size: Decimal
    unfilled_size: Decimal
    avg_fill_price: Decimal
    cash_delta: Decimal
    fee: Decimal
    position_delta: Decimal
    candidate_events: list[ReplayTradeEvent]
    requested_size: Decimal
    requested_notional: Decimal
    filled_notional: Decimal
    fill_pct: Decimal
    no_fill_reason: str = ""

    def to_fill_dict(self) -> dict[str, Any]:
        price_key = "entry_price" if self.position_delta > 0 else "exit_price"
        notes = [self.no_fill_reason] if self.no_fill_reason else []
        return {
            "requested_notional": self.requested_notional,
            "filled_notional": self.filled_notional,
            "fill_pct": self.fill_pct,
            "fill_probability": self.fill_pct,
            "size": self.filled_size,
            price_key: self.avg_fill_price,
            "avg_fill_price": self.avg_fill_price,
            "liquidity_cap_pct": Decimal("0") if not self.candidate_events else None,
            "partial_fill": self.status == "PARTIAL_FILLED",
            "rejected": self.status in {"NO_FILL", "REJECTED", "EXPIRED"},
            "fill_status": "PARTIAL" if self.status == "PARTIAL_FILLED" else self.status,
            "requested_size": self.requested_size,
            "filled_size": self.filled_size,
            "unfilled_size": self.unfilled_size,
            "block_volume": sum((event.size for event in self.candidate_events), Decimal("0")).quantize(Q, rounding=ROUND_HALF_UP),
            "trade_count": len(self.candidate_events),
            "available_notional": self.filled_notional,
            "fee_cost": self.fee,
            "slippage_cost": Decimal("0"),
            "execution_cost": self.fee,
            "execution_source": "orderfilled_limit_replay",
            "order_role": "maker",
            "limit_price": self.avg_fill_price,
            "notes": notes,
            "candidate_events": [
                {
                    "block_number": event.block_number,
                    "transaction_index": event.transaction_index,
                    "log_index": event.log_index,
                    "tx_hash": event.tx_hash,
                    "trade_price": event.trade_price,
                    "size": event.size,
                }
                for event in self.candidate_events
            ],
        }


def replay_limit_order(order: OrderIntent, events: list[ReplayTradeEvent]) -> OrderReplayResult:
    ordered_events = sorted(events, key=lambda item: item.event_sequence)
    candidates = [
        event
        for event in ordered_events
        if _is_candidate(order, event)
    ]
    if order.time_in_force in {"FOK", "FAK"}:
        candidates = [event for event in candidates if event.event_sequence[0] == order.submit_sequence[0]]
    requested_size = max(Decimal("0"), Decimal(str(order.size))).quantize(Q, rounding=ROUND_HALF_UP)
    limit_price = max(Decimal("0.0000000001"), Decimal(str(order.limit_price))).quantize(Q, rounding=ROUND_HALF_UP)
    requested_notional = (requested_size * limit_price).quantize(Q, rounding=ROUND_HALF_UP)
    fillable = _fillable_size(candidates, order.liquidity_cap_pct)

    if order.time_in_force == "FOK" and fillable < requested_size:
        return _empty_result(order, "REJECTED", "fok_insufficient_volume", requested_size, limit_price, candidates)
    if fillable <= 0:
        if order.time_in_force == "GTD" and order.expire_sequence is not None:
            return _empty_result(order, "EXPIRED", "gtd_expired", requested_size, limit_price, candidates)
        return _empty_result(order, "NO_FILL", "limit_not_crossed", requested_size, limit_price, candidates)

    filled_size = min(requested_size, fillable).quantize(Q, rounding=ROUND_HALF_UP)
    if filled_size < requested_size and order.time_in_force == "GTC":
        status: OrderStatus = "PARTIAL_FILLED"
    elif filled_size < requested_size and order.time_in_force in {"GTD", "FAK"}:
        status = "PARTIAL_FILLED"
    else:
        status = "FILLED"
    return _filled_result(order, status, requested_size, filled_size, limit_price, candidates)


def _is_candidate(order: OrderIntent, event: ReplayTradeEvent) -> bool:
    if event.event_sequence <= order.submit_sequence:
        return False
    if order.expire_sequence is not None and event.event_sequence > order.expire_sequence:
        return False
    price = Decimal(str(event.trade_price))
    limit = Decimal(str(order.limit_price))
    if order.side == "BUY_YES":
        return price <= limit
    return price >= limit


def _fillable_size(events: list[ReplayTradeEvent], liquidity_cap_pct: Decimal) -> Decimal:
    cap = max(Decimal("0"), Decimal(str(liquidity_cap_pct))) / Decimal("100")
    return sum((max(Decimal("0"), Decimal(str(event.size))) * cap for event in events), Decimal("0")).quantize(Q, rounding=ROUND_HALF_UP)


def _filled_result(
    order: OrderIntent,
    status: OrderStatus,
    requested_size: Decimal,
    filled_size: Decimal,
    price: Decimal,
    candidates: list[ReplayTradeEvent],
) -> OrderReplayResult:
    filled_notional = (filled_size * price).quantize(Q, rounding=ROUND_HALF_UP)
    fee = (filled_notional * max(Decimal("0"), Decimal(str(order.fee_bps))) / Decimal("10000")).quantize(Q, rounding=ROUND_HALF_UP)
    sign = Decimal("1") if order.side == "BUY_YES" else Decimal("-1")
    cash_delta = (-(filled_notional + fee) if order.side == "BUY_YES" else (filled_notional - fee)).quantize(Q, rounding=ROUND_HALF_UP)
    return OrderReplayResult(
        order_id=order.order_id,
        status=status,
        filled_size=filled_size,
        unfilled_size=max(Decimal("0"), requested_size - filled_size).quantize(Q, rounding=ROUND_HALF_UP),
        avg_fill_price=price,
        cash_delta=cash_delta,
        fee=fee,
        position_delta=(filled_size * sign).quantize(Q, rounding=ROUND_HALF_UP),
        candidate_events=candidates,
        requested_size=requested_size,
        requested_notional=(requested_size * price).quantize(Q, rounding=ROUND_HALF_UP),
        filled_notional=filled_notional,
        fill_pct=(filled_size * Decimal("100") / requested_size).quantize(Q, rounding=ROUND_HALF_UP) if requested_size else Decimal("0"),
    )


def _empty_result(
    order: OrderIntent,
    status: OrderStatus,
    reason: str,
    requested_size: Decimal,
    price: Decimal,
    candidates: list[ReplayTradeEvent],
) -> OrderReplayResult:
    return OrderReplayResult(
        order_id=order.order_id,
        status=status,
        filled_size=Decimal("0"),
        unfilled_size=requested_size,
        avg_fill_price=price,
        cash_delta=Decimal("0"),
        fee=Decimal("0"),
        position_delta=Decimal("0"),
        candidate_events=candidates,
        requested_size=requested_size,
        requested_notional=(requested_size * price).quantize(Q, rounding=ROUND_HALF_UP),
        filled_notional=Decimal("0"),
        fill_pct=Decimal("0"),
        no_fill_reason=reason,
    )
