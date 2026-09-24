"""Token-level in-memory order book state.

The book is the source of truth; database rows are sampled projections of this
state. Updates are intentionally dict-based: price lookup is the hot path, while
sorting is delayed until top-N or metrics are requested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Literal


BookSide = Literal["bid", "ask"]
BookStatus = Literal["not_ready", "ready", "stale"]


class OrderBookNotReady(RuntimeError):
    """Raised when a delta arrives before a trusted snapshot baseline."""


class OrderBookOutOfOrder(RuntimeError):
    """Raised when an update would move the local book backward in time."""


@dataclass(frozen=True)
class TokenBookIdentity:
    token_id: str
    market_id: int
    condition_id: str
    outcome: str
    outcome_index: int = 0
    market_slug: str | None = None


@dataclass(frozen=True)
class BookLevel:
    side: BookSide
    price: Decimal
    size: Decimal
    level_index: int

    def as_payload(self) -> dict[str, str]:
        return {"price": _decimal_text(self.price), "size": _decimal_text(self.size)}


@dataclass(frozen=True)
class BookMetrics:
    token_id: str
    market_id: int
    outcome: str
    status: BookStatus
    generation: int
    last_event_ts_ms: int | None
    best_bid: Decimal | None
    best_bid_size: Decimal | None
    best_ask: Decimal | None
    best_ask_size: Decimal | None
    mid: Decimal | None
    spread: Decimal | None
    bid_depth: Decimal
    ask_depth: Decimal
    depth_total: Decimal
    l1_imbalance: Decimal | None
    depth_imbalance: Decimal | None
    level_count_bid: int
    level_count_ask: int
    stale_reason: str | None = None


@dataclass
class LocalOrderBook:
    identity: TokenBookIdentity
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    status: BookStatus = "not_ready"
    generation: int = 0
    last_event_ts_ms: int | None = None
    last_snapshot_ts_ms: int | None = None
    last_hash: str | None = None
    stale_reason: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def reset(self, reason: str = "reset") -> None:
        self.bids.clear()
        self.asks.clear()
        self.status = "not_ready"
        self.generation += 1
        self.last_event_ts_ms = None
        self.last_snapshot_ts_ms = None
        self.stale_reason = reason
        self.last_hash = None

    def mark_stale(self, reason: str = "stale") -> None:
        self.status = "stale"
        self.stale_reason = reason

    def mark_stale_if_idle(self, *, now_ms: int, stale_after_ms: int) -> bool:
        if self.last_event_ts_ms is None:
            if self.status == "stale" and self.stale_reason == "no_events":
                return False
            self.mark_stale("no_events")
            return True
        if int(now_ms) - int(self.last_event_ts_ms) >= int(stale_after_ms):
            if self.status == "stale" and self.stale_reason == "idle_timeout":
                return False
            self.mark_stale("idle_timeout")
            return True
        return False

    def apply_snapshot(
        self,
        *,
        bids: Iterable[tuple[Any, Any]],
        asks: Iterable[tuple[Any, Any]],
        event_ts_ms: int | None = None,
        source_hash: str | None = None,
    ) -> BookMetrics:
        self.bids = _normalize_side(bids)
        self.asks = _normalize_side(asks)
        self.status = "ready"
        self.generation += 1
        self.stale_reason = None
        self.last_hash = source_hash or self.fingerprint()
        if event_ts_ms is not None:
            self.last_event_ts_ms = int(event_ts_ms)
            self.last_snapshot_ts_ms = int(event_ts_ms)
        return self.metrics()

    def apply_change(
        self,
        *,
        side: BookSide,
        price: Any,
        size: Any,
        event_ts_ms: int | None = None,
    ) -> BookMetrics:
        if not self.ready:
            raise OrderBookNotReady(f"book for token {self.identity.token_id} is not ready")
        if event_ts_ms is not None:
            event_ts_ms = int(event_ts_ms)
            if self.last_event_ts_ms is not None and event_ts_ms < self.last_event_ts_ms:
                self.mark_stale("out_of_order")
                raise OrderBookOutOfOrder(
                    f"out-of-order book update for token {self.identity.token_id}: "
                    f"{event_ts_ms} < {self.last_event_ts_ms}"
                )
        target = self.bids if side == "bid" else self.asks
        parsed_price = _decimal(price)
        parsed_size = _decimal(size)
        if parsed_price <= 0:
            raise ValueError(f"invalid order book price: {price!r}")
        if parsed_size <= 0:
            target.pop(parsed_price, None)
        else:
            target[parsed_price] = parsed_size
        if event_ts_ms is not None:
            self.last_event_ts_ms = event_ts_ms
        self.last_hash = self.fingerprint()
        return self.metrics()

    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        if not self.bids:
            return None
        price = max(self.bids)
        return price, self.bids[price]

    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        if not self.asks:
            return None
        price = min(self.asks)
        return price, self.asks[price]

    def top_n(self, n: int = 10) -> tuple[tuple[BookLevel, ...], tuple[BookLevel, ...]]:
        limit = max(0, int(n))
        bid_levels = tuple(
            BookLevel("bid", price, size, idx)
            for idx, (price, size) in enumerate(sorted(self.bids.items(), reverse=True)[:limit])
        )
        ask_levels = tuple(
            BookLevel("ask", price, size, idx)
            for idx, (price, size) in enumerate(sorted(self.asks.items())[:limit])
        )
        return bid_levels, ask_levels

    def metrics(self, *, depth_levels: int = 5) -> BookMetrics:
        bid = self.best_bid()
        ask = self.best_ask()
        best_bid, best_bid_size = bid if bid else (None, None)
        best_ask, best_ask_size = ask if ask else (None, None)
        mid = (best_bid + best_ask) / Decimal("2") if best_bid is not None and best_ask is not None else None
        spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
        bid_top, ask_top = self.top_n(depth_levels)
        bid_depth = sum((level.price * level.size for level in bid_top), Decimal("0"))
        ask_depth = sum((level.price * level.size for level in ask_top), Decimal("0"))
        depth_total = bid_depth + ask_depth
        l1_total = Decimal("0")
        if best_bid is not None and best_bid_size is not None:
            l1_total += best_bid * best_bid_size
        if best_ask is not None and best_ask_size is not None:
            l1_total += best_ask * best_ask_size
        l1_bid_notional = best_bid * best_bid_size if best_bid is not None and best_bid_size is not None else Decimal("0")
        return BookMetrics(
            token_id=self.identity.token_id,
            market_id=self.identity.market_id,
            outcome=self.identity.outcome,
            status=self.status,
            generation=self.generation,
            last_event_ts_ms=self.last_event_ts_ms,
            best_bid=best_bid,
            best_bid_size=best_bid_size,
            best_ask=best_ask,
            best_ask_size=best_ask_size,
            mid=mid,
            spread=spread,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            depth_total=depth_total,
            l1_imbalance=(l1_bid_notional / l1_total) if l1_total > 0 else None,
            depth_imbalance=(bid_depth / depth_total) if depth_total > 0 else None,
            level_count_bid=len(self.bids),
            level_count_ask=len(self.asks),
            stale_reason=self.stale_reason,
        )

    def snapshot_payload(self, *, depth_levels: int = 10) -> dict[str, Any]:
        bid_levels, ask_levels = self.top_n(depth_levels)
        metrics = self.metrics(depth_levels=depth_levels)
        snapshot_version = self.fingerprint(depth_levels=depth_levels)
        return {
            "snapshot_id": 0,
            "token_id": self.identity.token_id,
            "market_id": self.identity.market_id,
            "market_slug": self.identity.market_slug or "",
            "condition_id": self.identity.condition_id,
            "outcome": self.identity.outcome,
            "side": self.identity.outcome,
            "status": self.status,
            "book_status": "ok" if self.ready else self.status,
            "source": "local_orderbook",
            "generation": self.generation,
            "last_event_ts_ms": self.last_event_ts_ms,
            "timestamp": _ms_to_iso(self.last_event_ts_ms),
            "snapshot_timestamp": _ms_to_iso(self.last_event_ts_ms),
            "snapshot_version": snapshot_version,
            "best_bid": _optional_decimal_text(metrics.best_bid),
            "best_ask": _optional_decimal_text(metrics.best_ask),
            "mid": _optional_decimal_text(metrics.mid),
            "spread": _optional_decimal_text(metrics.spread),
            "bid_depth": _decimal_text(metrics.bid_depth),
            "ask_depth": _decimal_text(metrics.ask_depth),
            "depth_total": _decimal_text(metrics.depth_total),
            "imbalance": _optional_decimal_text(metrics.depth_imbalance),
            "bids": [level.as_payload() for level in bid_levels],
            "asks": [level.as_payload() for level in ask_levels],
        }

    def fingerprint(self, *, depth_levels: int | None = None) -> str:
        import hashlib

        bid_items = sorted(self.bids.items(), reverse=True)
        ask_items = sorted(self.asks.items())
        if depth_levels is not None:
            limit = max(0, int(depth_levels))
            bid_items = bid_items[:limit]
            ask_items = ask_items[:limit]
        digest = hashlib.sha256()
        digest.update(self.identity.token_id.encode("utf-8"))
        digest.update(b"|")
        for side in (bid_items, ask_items):
            for price, size in side:
                digest.update(_decimal_text(price).encode("ascii"))
                digest.update(b":")
                digest.update(_decimal_text(size).encode("ascii"))
                digest.update(b";")
            digest.update(b"|")
        return digest.hexdigest()[:20]


def _normalize_side(rows: Iterable[tuple[Any, Any]]) -> dict[Decimal, Decimal]:
    normalized: dict[Decimal, Decimal] = {}
    for raw_price, raw_size in rows:
        price = _decimal(raw_price)
        size = _decimal(raw_size)
        if price <= 0 or size <= 0:
            continue
        normalized[price] = size
    return normalized


def _decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"non-finite decimal value: {value!r}")
    return parsed


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text == "-0" else text


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return _decimal_text(value) if value is not None else None


def _ms_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
