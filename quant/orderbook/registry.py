"""Small multi-token registry around LocalOrderBook."""

from __future__ import annotations

from dataclasses import dataclass, field

from .local_book import BookMetrics, LocalOrderBook, TokenBookIdentity
from .polymarket_adapter import NormalizedBookDelta, NormalizedBookEvent, NormalizedBookSnapshot


@dataclass
class OrderBookRegistry:
    books: dict[str, LocalOrderBook] = field(default_factory=dict)

    def ensure(self, identity: TokenBookIdentity) -> LocalOrderBook:
        book = self.books.get(identity.token_id)
        if book is None:
            book = LocalOrderBook(identity)
            self.books[identity.token_id] = book
        return book

    def get(self, token_id: str) -> LocalOrderBook | None:
        return self.books.get(str(token_id))

    def apply(self, identity: TokenBookIdentity, event: NormalizedBookEvent) -> BookMetrics:
        if event.token_id != identity.token_id:
            raise ValueError(f"event token {event.token_id!r} does not match identity token {identity.token_id!r}")
        book = self.ensure(identity)
        if isinstance(event, NormalizedBookSnapshot):
            return book.apply_snapshot(
                bids=event.bids,
                asks=event.asks,
                event_ts_ms=event.event_ts_ms,
                source_hash=event.source_hash,
            )
        if isinstance(event, NormalizedBookDelta):
            return book.apply_change(
                side=event.side,
                price=event.price,
                size=event.size,
                event_ts_ms=event.event_ts_ms,
            )
        raise TypeError(f"unsupported order book event: {type(event)!r}")

    def mark_all_stale(self, reason: str = "registry_stale") -> None:
        for book in self.books.values():
            book.mark_stale(reason)
