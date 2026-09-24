"""Deterministic Polymarket execution helpers for backtests.

The strategy decides *what* it wants to do at a price row. This module decides
whether that order could have filled using only book data available at or before
the simulated execution time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from typing import Any, Literal


OrderSide = Literal["BUY_YES", "SELL_YES", "BUY_NO", "SELL_NO"]
FillStatus = Literal["FILLED", "PARTIAL", "REJECTED", "NO_BOOK", "STALE_BOOK", "INSUFFICIENT_CASH"]


@dataclass(frozen=True)
class ExecutionConfig:
    mode: str = "DEPTH"
    order_type: str = "MARKET"
    fee_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    latency_seconds: Decimal = Decimal("0")
    max_book_staleness_seconds: Decimal = Decimal("900")
    allow_partial_fill: bool = True
    min_fill_size: Decimal = Decimal("0")
    min_fill_pct: Decimal = Decimal("0")
    reject_on_stale_book: bool = True
    max_entry_price: Decimal = Decimal("1")
    min_exit_price: Decimal = Decimal("0")


@dataclass(frozen=True)
class BookSnapshot:
    snapshot_id: int
    token_id: str
    side: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    source: str = "clob_orderbook_snapshots"
    book_status: str = "unknown"
    block_number: int | None = None
    timestamp: datetime | None = None
    captured_at: datetime | None = None
    snapshot_version: str = ""

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0][0] if self.asks else None

    @property
    def spread(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def mid(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / Decimal("2")

    @property
    def bid_depth(self) -> Decimal:
        return sum((price * size for price, size in self.bids), Decimal("0"))

    @property
    def ask_depth(self) -> Decimal:
        return sum((price * size for price, size in self.asks), Decimal("0"))


@dataclass(frozen=True)
class SnapshotLookupResult:
    snapshot: BookSnapshot | None
    status: Literal["OK", "NO_BOOK", "STALE_BOOK"]
    execution_block: int | None
    execution_timestamp: datetime | None
    staleness_seconds: Decimal | None
    staleness_blocks: int | None
    warning: str = ""


@dataclass(frozen=True)
class FillResult:
    side: OrderSide
    status: FillStatus
    requested_size: Decimal
    filled_size: Decimal
    unfilled_size: Decimal
    avg_fill_price: Decimal
    requested_notional: Decimal
    filled_notional: Decimal
    fee: Decimal
    slippage: Decimal
    snapshot_id: int | None
    snapshot_version: str | None
    staleness_seconds: Decimal | None
    staleness_blocks: int | None
    levels_consumed: int
    best_bid: Decimal | None
    best_ask: Decimal | None
    spread: Decimal | None
    mid: Decimal | None
    notes: str = ""

    @property
    def fill_pct(self) -> Decimal:
        if self.requested_size <= 0:
            return Decimal("0")
        return _pct(self.filled_size, self.requested_size)


def execution_config_from_params(params: Any) -> ExecutionConfig:
    return ExecutionConfig(
        mode=str(getattr(params, "execution_price_mode", "DEPTH") or "DEPTH").upper(),
        order_type=str(getattr(params, "order_type", "MARKET") or "MARKET").upper(),
        fee_bps=_decimal(getattr(params, "fee_bps", Decimal("0"))),
        slippage_bps=_decimal(getattr(params, "slippage_bps", Decimal("0"))),
        latency_seconds=max(Decimal("0"), _decimal(getattr(params, "latency_seconds", Decimal("0")))),
        max_book_staleness_seconds=max(Decimal("0"), _decimal(getattr(params, "max_book_staleness_seconds", Decimal("900")))),
        allow_partial_fill=bool(getattr(params, "allow_partial_fill", True)),
        min_fill_size=max(Decimal("0"), _decimal(getattr(params, "min_fill_size", Decimal("0")))),
        min_fill_pct=max(Decimal("0"), _decimal(getattr(params, "min_fill_pct", Decimal("0")))),
        reject_on_stale_book=bool(getattr(params, "reject_on_stale_book", True)),
        max_entry_price=min(Decimal("1"), max(Decimal("0"), _decimal(getattr(params, "max_entry_price", Decimal("1"))))),
        min_exit_price=min(Decimal("1"), max(Decimal("0"), _decimal(getattr(params, "min_exit_price", Decimal("0"))))),
    )


def lookup_snapshot(
    snapshots: list[BookSnapshot],
    *,
    decision_block: int | None,
    decision_timestamp: datetime | None,
    config: ExecutionConfig,
) -> SnapshotLookupResult:
    execution_timestamp = decision_timestamp + timedelta(seconds=float(config.latency_seconds)) if decision_timestamp else None
    execution_block = decision_block
    candidates: list[BookSnapshot] = []
    for snapshot in snapshots:
        if execution_timestamp is not None and snapshot.timestamp is not None:
            if snapshot.timestamp <= execution_timestamp:
                candidates.append(snapshot)
            continue
        if execution_block is not None and snapshot.block_number is not None:
            if snapshot.block_number <= execution_block:
                candidates.append(snapshot)
    if not candidates:
        return SnapshotLookupResult(None, "NO_BOOK", execution_block, execution_timestamp, None, None, "no historical book at or before execution time")
    candidates.sort(key=lambda item: (
        item.timestamp or datetime.min.replace(tzinfo=timezone.utc),
        item.block_number or -1,
        item.snapshot_id,
    ), reverse=True)
    snapshot = candidates[0]
    staleness_seconds = None
    if execution_timestamp is not None and snapshot.timestamp is not None:
        staleness_seconds = Decimal(str(max(0.0, (execution_timestamp - snapshot.timestamp).total_seconds()))).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    staleness_blocks = None
    if execution_block is not None and snapshot.block_number is not None:
        staleness_blocks = max(0, int(execution_block) - int(snapshot.block_number))
    if staleness_seconds is not None and staleness_seconds > config.max_book_staleness_seconds:
        return SnapshotLookupResult(snapshot, "STALE_BOOK", execution_block, execution_timestamp, staleness_seconds, staleness_blocks, "book snapshot exceeds max staleness")
    return SnapshotLookupResult(snapshot, "OK", execution_block, execution_timestamp, staleness_seconds, staleness_blocks)


def simulate_depth_fill(
    *,
    side: OrderSide,
    target_size: Decimal,
    config: ExecutionConfig,
    lookup: SnapshotLookupResult,
    cash_available: Decimal | None = None,
) -> FillResult:
    if lookup.snapshot is None:
        return _empty_fill(side, target_size, "NO_BOOK", lookup, "no historical book snapshot")
    if lookup.status == "STALE_BOOK" and config.reject_on_stale_book:
        return _empty_fill(side, target_size, "STALE_BOOK", lookup, lookup.warning or "stale book rejected")
    snapshot = lookup.snapshot
    levels = snapshot.asks if side in {"BUY_YES", "BUY_NO"} else snapshot.bids
    limit_price = config.max_entry_price if side in {"BUY_YES", "BUY_NO"} else config.min_exit_price
    if not levels:
        return _empty_fill(side, target_size, "NO_BOOK", lookup, "book side has no levels")
    remaining = max(Decimal("0"), target_size)
    filled_size = Decimal("0")
    filled_notional = Decimal("0")
    levels_consumed = 0
    for price, size in levels:
        if side in {"BUY_YES", "BUY_NO"} and config.order_type != "MARKET" and price > limit_price:
            break
        if side in {"SELL_YES", "SELL_NO"} and config.order_type != "MARKET" and price < limit_price:
            break
        take_size = min(remaining, size)
        level_notional = take_size * price
        if cash_available is not None and side in {"BUY_YES", "BUY_NO"}:
            fee_preview = level_notional * _bps_fraction(config.fee_bps)
            if filled_notional + level_notional + fee_preview > cash_available:
                affordable = max(Decimal("0"), cash_available - filled_notional)
                affordable_size = affordable / (price * (Decimal("1") + _bps_fraction(config.fee_bps)))
                take_size = min(take_size, affordable_size)
                level_notional = take_size * price
        if take_size <= 0:
            break
        filled_size += take_size
        filled_notional += level_notional
        remaining -= take_size
        levels_consumed += 1
        if remaining <= 0:
            break
    if filled_size <= 0:
        status: FillStatus = "INSUFFICIENT_CASH" if cash_available is not None and side in {"BUY_YES", "BUY_NO"} else "REJECTED"
        return _empty_fill(side, target_size, status, lookup, "price limit, cash, or empty depth prevented fill")
    avg_price = (filled_notional / filled_size).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    slippage = (filled_notional * _bps_fraction(config.slippage_bps)).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    filled_notional_with_slip = filled_notional + slippage if side in {"BUY_YES", "BUY_NO"} else max(Decimal("0"), filled_notional - slippage)
    fee = (filled_notional_with_slip * _bps_fraction(config.fee_bps)).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    min_fill_size = max(config.min_fill_size, target_size * config.min_fill_pct / Decimal("100"))
    if filled_size < min_fill_size:
        return FillResult(
            side=side,
            status="REJECTED",
            requested_size=target_size,
            filled_size=Decimal("0"),
            unfilled_size=target_size,
            avg_fill_price=Decimal("0"),
            requested_notional=(target_size * avg_price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
            filled_notional=Decimal("0"),
            fee=Decimal("0"),
            slippage=Decimal("0"),
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.snapshot_version or None,
            staleness_seconds=lookup.staleness_seconds,
            staleness_blocks=lookup.staleness_blocks,
            levels_consumed=levels_consumed,
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
            spread=snapshot.spread,
            mid=snapshot.mid,
            notes="filled size below minimum fill threshold",
        )
    if not config.allow_partial_fill and filled_size < target_size:
        return _empty_fill(side, target_size, "PARTIAL", lookup, "partial fill disabled")
    status = "FILLED" if filled_size >= target_size else "PARTIAL"
    return FillResult(
        side=side,
        status=status,
        requested_size=target_size,
        filled_size=filled_size.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        unfilled_size=max(Decimal("0"), target_size - filled_size).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        avg_fill_price=avg_price,
        requested_notional=(target_size * avg_price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        filled_notional=filled_notional_with_slip.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        fee=fee,
        slippage=slippage,
        snapshot_id=snapshot.snapshot_id,
        snapshot_version=snapshot.snapshot_version or None,
        staleness_seconds=lookup.staleness_seconds,
        staleness_blocks=lookup.staleness_blocks,
        levels_consumed=levels_consumed,
        best_bid=snapshot.best_bid,
        best_ask=snapshot.best_ask,
        spread=snapshot.spread,
        mid=snapshot.mid,
        notes="depth fill",
    )


def snapshot_version(snapshot: BookSnapshot) -> str:
    digest = hashlib.sha256()
    digest.update(str(snapshot.token_id).encode("utf-8"))
    digest.update(b"|")
    for side in (snapshot.bids, snapshot.asks):
        for price, size in side:
            digest.update(str(price).encode("ascii"))
            digest.update(b":")
            digest.update(str(size).encode("ascii"))
            digest.update(b";")
        digest.update(b"|")
    if snapshot.timestamp:
        digest.update(snapshot.timestamp.isoformat().encode("ascii"))
    if snapshot.block_number is not None:
        digest.update(str(snapshot.block_number).encode("ascii"))
    return digest.hexdigest()[:20]


def parse_book_snapshot(row: dict[str, Any]) -> BookSnapshot:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    timestamp = _coerce_datetime(row.get("snapshot_timestamp") or row.get("fetched_at") or row.get("captured_at"))
    snapshot = BookSnapshot(
        snapshot_id=int(row["snapshot_id"]),
        token_id=str(row.get("token_id") or ""),
        side=str(row.get("side") or "YES"),
        bids=tuple(_levels(payload.get("bids"), reverse=True)),
        asks=tuple(_levels(payload.get("asks"), reverse=False)),
        source=str(row.get("source") or "clob_orderbook_snapshots"),
        book_status=str(row.get("book_status") or "unknown"),
        block_number=int(row["block_number"]) if row.get("block_number") is not None else None,
        timestamp=timestamp,
        captured_at=_coerce_datetime(row.get("captured_at") or row.get("created_at")),
        snapshot_version=str(row.get("snapshot_version") or ""),
    )
    if snapshot.snapshot_version:
        return snapshot
    return BookSnapshot(**{**snapshot.__dict__, "snapshot_version": snapshot_version(snapshot)})


def snapshot_to_dict(snapshot: BookSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "token_id": snapshot.token_id,
        "side": snapshot.side,
        "bids": [{"price": str(price), "size": str(size)} for price, size in snapshot.bids],
        "asks": [{"price": str(price), "size": str(size)} for price, size in snapshot.asks],
        "source": snapshot.source,
        "book_status": snapshot.book_status,
        "block_number": snapshot.block_number,
        "timestamp": snapshot.timestamp.isoformat() if snapshot.timestamp else None,
        "captured_at": snapshot.captured_at.isoformat() if snapshot.captured_at else None,
        "snapshot_version": snapshot.snapshot_version,
    }


def snapshot_from_any(value: Any) -> BookSnapshot | None:
    if isinstance(value, BookSnapshot):
        return value
    if not isinstance(value, dict):
        return None
    return BookSnapshot(
        snapshot_id=int(value.get("snapshot_id") or value.get("snapshotId") or 0),
        token_id=str(value.get("token_id") or value.get("tokenId") or ""),
        side=str(value.get("side") or "YES"),
        bids=tuple(_levels(value.get("bids"), reverse=True)),
        asks=tuple(_levels(value.get("asks"), reverse=False)),
        source=str(value.get("source") or "clob_orderbook_snapshots"),
        book_status=str(value.get("book_status") or value.get("bookStatus") or "unknown"),
        block_number=int(value["block_number"]) if value.get("block_number") is not None else int(value["blockNumber"]) if value.get("blockNumber") is not None else None,
        timestamp=_coerce_datetime(value.get("timestamp") or value.get("snapshot_timestamp") or value.get("snapshotTimestamp")),
        captured_at=_coerce_datetime(value.get("captured_at") or value.get("capturedAt")),
        snapshot_version=str(value.get("snapshot_version") or value.get("snapshotVersion") or ""),
    )


def _empty_fill(side: OrderSide, target_size: Decimal, status: FillStatus, lookup: SnapshotLookupResult, notes: str) -> FillResult:
    snapshot = lookup.snapshot
    return FillResult(
        side=side,
        status=status,
        requested_size=target_size,
        filled_size=Decimal("0"),
        unfilled_size=target_size,
        avg_fill_price=Decimal("0"),
        requested_notional=Decimal("0"),
        filled_notional=Decimal("0"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        snapshot_id=snapshot.snapshot_id if snapshot else None,
        snapshot_version=snapshot.snapshot_version if snapshot else None,
        staleness_seconds=lookup.staleness_seconds,
        staleness_blocks=lookup.staleness_blocks,
        levels_consumed=0,
        best_bid=snapshot.best_bid if snapshot else None,
        best_ask=snapshot.best_ask if snapshot else None,
        spread=snapshot.spread if snapshot else None,
        mid=snapshot.mid if snapshot else None,
        notes=notes,
    )


def _levels(rows: Any, *, reverse: bool) -> list[tuple[Decimal, Decimal]]:
    if not isinstance(rows, list):
        return []
    levels: list[tuple[Decimal, Decimal]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _decimal(row.get("price"))
        size = _decimal(row.get("size"))
        if price > 0 and size > 0:
            levels.append((price, size))
    levels.sort(key=lambda item: item[0], reverse=reverse)
    return levels


def _decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception:
        return Decimal("0")
    return parsed if parsed.is_finite() else Decimal("0")


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _bps_fraction(value: Decimal) -> Decimal:
    return max(Decimal("0"), Decimal(str(value))) / Decimal("10000")


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return (numerator / denominator * Decimal("100")).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
