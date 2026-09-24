"""Normalize Polymarket CLOB messages into order book state-machine inputs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import time
from typing import Any

from .local_book import BookSide


@dataclass(frozen=True)
class NormalizedBookSnapshot:
    token_id: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    event_ts_ms: int
    source_hash: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class NormalizedBookDelta:
    token_id: str
    side: BookSide
    price: Decimal
    size: Decimal
    event_ts_ms: int
    source_hash: str | None = None
    raw: dict[str, Any] | None = None


NormalizedBookEvent = NormalizedBookSnapshot | NormalizedBookDelta


def normalize_polymarket_event(event: dict[str, Any]) -> list[NormalizedBookEvent]:
    event_type = str(event.get("event_type") or "").strip()
    if event_type == "book":
        token_id = _token_id(event.get("asset_id"))
        if not token_id:
            return []
        return [
            NormalizedBookSnapshot(
                token_id=token_id,
                bids=_levels(event.get("bids")),
                asks=_levels(event.get("asks")),
                event_ts_ms=_timestamp_ms(event.get("timestamp")),
                source_hash=str(event.get("hash") or "") or None,
                raw=event,
            )
        ]
    if event_type == "price_change":
        event_ts_ms = _timestamp_ms(event.get("timestamp"))
        normalized: list[NormalizedBookEvent] = []
        for item in event.get("price_changes") or []:
            if not isinstance(item, dict):
                continue
            token_id = _token_id(item.get("asset_id"))
            side = _side_from_polymarket(item.get("side"))
            price = _optional_decimal(item.get("price"))
            size = _optional_decimal(item.get("size"))
            if not token_id or side is None or price is None or size is None:
                continue
            normalized.append(
                NormalizedBookDelta(
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=size,
                    event_ts_ms=event_ts_ms,
                    source_hash=str(item.get("hash") or event.get("hash") or "") or None,
                    raw={"event_type": "price_change", "market": event.get("market"), "timestamp": event.get("timestamp"), "price_change": item},
                )
            )
        return normalized
    return []


def normalize_rest_book(token_id: str, payload: dict[str, Any], *, event_ts_ms: int | None = None) -> NormalizedBookSnapshot:
    return NormalizedBookSnapshot(
        token_id=_token_id(token_id),
        bids=_levels(payload.get("bids")),
        asks=_levels(payload.get("asks")),
        event_ts_ms=int(event_ts_ms if event_ts_ms is not None else time.time() * 1000),
        source_hash=str(payload.get("hash") or "") or None,
        raw=payload,
    )


def _levels(rows: Any) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(rows, list):
        return ()
    parsed: list[tuple[Decimal, Decimal]] = []
    for row in rows:
        if isinstance(row, dict):
            price = _optional_decimal(row.get("price"))
            size = _optional_decimal(row.get("size"))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            price = _optional_decimal(row[0])
            size = _optional_decimal(row[1])
        else:
            continue
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        parsed.append((price, size))
    return tuple(parsed)


def _side_from_polymarket(value: Any) -> BookSide | None:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BID", "BIDS"}:
        return "bid"
    if text in {"SELL", "ASK", "ASKS"}:
        return "ask"
    return None


def _token_id(value: Any) -> str:
    return str(value or "").strip()


def _timestamp_ms(value: Any) -> int:
    if value is None or str(value).strip() == "":
        return int(time.time() * 1000)
    try:
        return int(float(str(value)))
    except ValueError:
        return int(time.time() * 1000)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None
