from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Dict

from .contracts import SourceFetchResult
from .source_health import SOURCE_COVERAGE


SNAPSHOT_NAMESPACE = "snapshot:world:natural-hazards"
_SOURCE_LOCKS: dict[str, Lock] = {}
_SOURCE_LOCKS_GUARD = Lock()


def _source_lock(key: str) -> Lock:
    with _SOURCE_LOCKS_GUARD:
        lock = _SOURCE_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _SOURCE_LOCKS[key] = lock
        return lock


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def stale_source_result(snapshot_store: Any, key: str, error_code: str) -> SourceFetchResult | None:
    stale = snapshot_store.get_stale(SNAPSHOT_NAMESPACE, key)
    if not isinstance(stale, dict) or not isinstance(stale.get("events"), list):
        return None
    return {
        "key": key,
        "status": "degraded",
        "coverage": SOURCE_COVERAGE[key],
        "events": stale["events"],
        "fetchedAt": stale.get("fetchedAt"),
        "dataUpdatedAt": stale.get("dataUpdatedAt"),
        "staleAfter": stale.get("staleAfter"),
        "lastSuccessAt": stale.get("fetchedAt"),
        "errorCode": error_code,
    }


def cached_source_result(snapshot_store: Any, key: str) -> SourceFetchResult | None:
    fresh = snapshot_store.get(SNAPSHOT_NAMESPACE, key)
    if isinstance(fresh, dict) and isinstance(fresh.get("events"), list):
        return {
            "key": key,
            "status": "ok",
            "coverage": SOURCE_COVERAGE[key],
            "events": fresh["events"],
            "fetchedAt": fresh.get("fetchedAt"),
            "dataUpdatedAt": fresh.get("dataUpdatedAt"),
            "staleAfter": fresh.get("staleAfter"),
            "lastSuccessAt": fresh.get("fetchedAt"),
            "errorCode": None,
        }
    return stale_source_result(snapshot_store, key, f"{key}-cached-stale")


def fetch_with_snapshot(
    *,
    key: str,
    snapshot_store: Any,
    fetcher: Callable[[], Dict[str, Any]],
    ttl_seconds: int,
) -> SourceFetchResult:
    fresh = snapshot_store.get(SNAPSHOT_NAMESPACE, key)
    if isinstance(fresh, dict) and isinstance(fresh.get("events"), list):
        return {
            "key": key,
            "status": "ok",
            "coverage": SOURCE_COVERAGE[key],
            "events": fresh["events"],
            "fetchedAt": fresh.get("fetchedAt"),
            "dataUpdatedAt": fresh.get("dataUpdatedAt"),
            "staleAfter": fresh.get("staleAfter"),
            "lastSuccessAt": fresh.get("fetchedAt"),
            "errorCode": None,
        }

    with _source_lock(key):
        fresh = snapshot_store.get(SNAPSHOT_NAMESPACE, key)
        if isinstance(fresh, dict) and isinstance(fresh.get("events"), list):
            return {
                "key": key,
                "status": "ok",
                "coverage": SOURCE_COVERAGE[key],
                "events": fresh["events"],
                "fetchedAt": fresh.get("fetchedAt"),
                "dataUpdatedAt": fresh.get("dataUpdatedAt"),
                "staleAfter": fresh.get("staleAfter"),
                "lastSuccessAt": fresh.get("fetchedAt"),
                "errorCode": None,
            }
        try:
            provider_result = fetcher()
            if not isinstance(provider_result, dict) or not isinstance(provider_result.get("events"), list):
                raise ValueError(f"{key}-provider-contract")
            fetched_at = utc_now()
            snapshot = {
                "events": provider_result["events"],
                "fetchedAt": iso_utc(fetched_at),
                "dataUpdatedAt": provider_result.get("data_updated_at"),
                "staleAfter": iso_utc(fetched_at + timedelta(seconds=ttl_seconds)),
            }
            snapshot_store.set(SNAPSHOT_NAMESPACE, key, snapshot, ttl_seconds)
            return {
                "key": key,
                "status": "ok",
                "coverage": SOURCE_COVERAGE[key],
                "events": snapshot["events"],
                "fetchedAt": snapshot["fetchedAt"],
                "dataUpdatedAt": snapshot["dataUpdatedAt"],
                "staleAfter": snapshot["staleAfter"],
                "lastSuccessAt": snapshot["fetchedAt"],
                "errorCode": None,
            }
        except Exception as exc:
            stale = stale_source_result(
                snapshot_store,
                key,
                f"{key}-stale-after-{exc.__class__.__name__}",
            )
            if stale is not None:
                return stale
            return {
                "key": key,
                "status": "error",
                "coverage": SOURCE_COVERAGE[key],
                "events": [],
                "fetchedAt": None,
                "dataUpdatedAt": None,
                "staleAfter": None,
                "lastSuccessAt": None,
                "errorCode": f"{key}-{exc.__class__.__name__}",
            }
