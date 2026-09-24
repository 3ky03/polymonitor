"""Bridge local order book state into the existing backtest execution model."""

from __future__ import annotations

from .local_book import LocalOrderBook, OrderBookNotReady
from ..backtest.execution import BookSnapshot, snapshot_from_any


def book_snapshot_from_local_book(
    book: LocalOrderBook,
    *,
    snapshot_id: int = 0,
    depth_levels: int = 10,
) -> BookSnapshot:
    """Convert a ready local book into the BookSnapshot consumed by DEPTH mode."""

    if not book.ready:
        raise OrderBookNotReady(f"book for token {book.identity.token_id} is not ready")
    payload = book.snapshot_payload(depth_levels=depth_levels)
    payload["snapshot_id"] = int(snapshot_id)
    snapshot = snapshot_from_any(payload)
    if snapshot is None:
        raise ValueError(f"could not convert local book for token {book.identity.token_id} into BookSnapshot")
    return snapshot
