"""Local order book state machines for Polymarket CLOB data."""

from .local_book import (
    BookLevel,
    BookMetrics,
    LocalOrderBook,
    OrderBookNotReady,
    OrderBookOutOfOrder,
    TokenBookIdentity,
)
from .polymarket_adapter import (
    NormalizedBookDelta,
    NormalizedBookSnapshot,
    normalize_polymarket_event,
    normalize_rest_book,
)
from .backtest import book_snapshot_from_local_book
from .coverage import (
    OrderBookCoverageTarget,
    build_coverage_target,
    classify_priority_topic,
    select_orderbook_coverage_targets,
    summarize_coverage_targets,
)

__all__ = [
    "BookLevel",
    "BookMetrics",
    "LocalOrderBook",
    "NormalizedBookDelta",
    "NormalizedBookSnapshot",
    "OrderBookCoverageTarget",
    "OrderBookNotReady",
    "OrderBookOutOfOrder",
    "TokenBookIdentity",
    "book_snapshot_from_local_book",
    "build_coverage_target",
    "classify_priority_topic",
    "normalize_polymarket_event",
    "normalize_rest_book",
    "select_orderbook_coverage_targets",
    "summarize_coverage_targets",
]
