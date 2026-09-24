from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


def iso_from_epoch_ms(value: Any) -> str | None:
    try:
        timestamp = float(value) / 1000.0
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def iso_timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def valid_point(coordinates: Any) -> list[float] | None:
    if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
        return None
    lon = finite_number(coordinates[0])
    lat = finite_number(coordinates[1])
    if lon is None or lat is None or lon < -180 or lon > 180 or lat < -90 or lat > 90:
        return None
    return [lon, lat]


def compact_text(value: Any, limit: int = 500) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
