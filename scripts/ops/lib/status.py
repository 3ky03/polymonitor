"""Canonical status vocabulary and aggregation for operations snapshots."""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable


class Status(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    DISABLED = "disabled"


_ALIASES = {
    "ok": Status.HEALTHY,
    "ready": Status.HEALTHY,
    "active": Status.HEALTHY,
    "online": Status.HEALTHY,
    "fresh": Status.HEALTHY,
    "connected": Status.HEALTHY,
    "aging": Status.WARNING,
    "warn": Status.WARNING,
    "warning": Status.WARNING,
    "partial": Status.DEGRADED,
    "preserved": Status.DEGRADED,
    "stale": Status.DEGRADED,
    "degraded": Status.DEGRADED,
    "failed": Status.UNHEALTHY,
    "error": Status.UNHEALTHY,
    "missing": Status.UNHEALTHY,
    "offline": Status.UNHEALTHY,
    "unavailable": Status.UNHEALTHY,
    "inactive": Status.UNHEALTHY,
    "unknown": Status.UNKNOWN,
    "warming": Status.UNKNOWN,
    "disabled": Status.DISABLED,
    "skipped": Status.DISABLED,
}

_SEVERITY = {
    Status.DISABLED: -1,
    Status.HEALTHY: 0,
    Status.UNKNOWN: 1,
    Status.WARNING: 2,
    Status.DEGRADED: 3,
    Status.UNHEALTHY: 4,
}


def normalize(value: Any, *, default: Status = Status.UNKNOWN) -> Status:
    if isinstance(value, Status):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return default
    try:
        return Status(text)
    except ValueError:
        return _ALIASES.get(text, default)


def aggregate(values: Iterable[Any], *, empty: Status = Status.UNKNOWN) -> Status:
    statuses = [normalize(value) for value in values]
    enabled = [value for value in statuses if value is not Status.DISABLED]
    if not enabled:
        return Status.DISABLED if statuses else empty
    return max(enabled, key=_SEVERITY.__getitem__)


def threshold_status(
    value: float | int | None,
    *,
    warning: float,
    critical: float,
    lower_is_worse: bool = False,
) -> Status:
    if value is None:
        return Status.UNKNOWN
    numeric = float(value)
    if lower_is_worse:
        if numeric <= critical:
            return Status.UNHEALTHY
        if numeric <= warning:
            return Status.WARNING
    else:
        if numeric >= critical:
            return Status.UNHEALTHY
        if numeric >= warning:
            return Status.WARNING
    return Status.HEALTHY


def is_actionable(value: Any) -> bool:
    return normalize(value) in {Status.WARNING, Status.DEGRADED, Status.UNHEALTHY}
