#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone watcher for new Polymarket market panel signals.

The watcher is intentionally independent from market_discovery.py. It only reads
the indexed markets table, snapshots the first visible YES probability from the
CLOB order book, and stores recent panel items in Redis.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_scripts_root = Path(__file__).resolve().parents[1]
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

try:
    import redis
except ImportError:
    redis = None

try:
    import requests
except ImportError:
    requests = None

from db import add_db_cli_args, configure_db_from_args, describe_db_target, dict_from_row, get_connection
from data_sources import POLYMARKET_CLOB_API_BASE
from api.config import load_api_settings
from api.services.new_market_signal_service import (
    SNAPSHOT_CACHE_KEY,
    SNAPSHOT_NAMESPACE,
    SNAPSHOT_TTL_SECONDS,
    normalize_new_market_signals_payload,
)
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload
from runtime.snapshot_store import SnapshotStore
from runtime.telegram_panel_publish import publish_cached_panel_snapshot


DEFAULT_NAMESPACE = "runtime:new-market-signals"
DEFAULT_INTERVAL_SECONDS = 20
DEFAULT_BATCH_LIMIT = 100
DEFAULT_RETENTION = 50
DEFAULT_PENDING_RETENTION = 500
DEFAULT_CLOB_TIMEOUT_SECONDS = 10
DEFAULT_DB_READ_TIMEOUT_SECONDS = 12
DEFAULT_MAX_MARKET_AGE_HOURS = 72
PLACEHOLDER_TITLE_PREFIXES = (
    "On-chain recovered market ",
    "Trade indexer placeholder market ",
)
PERMANENT_PLACEHOLDER_TITLE_PREFIXES = ("Trade indexer placeholder market ",)
SEED_META_NAMESPACE = "seed-meta:markets"
SEED_META_CACHE_KEY = "new-market-signals"
SEED_META_SERVICE_NAME = "polydata-new-market-signal.service"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return default


def _safe_decimal(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _format_decimal(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    normalized = format(value, "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _redis_key(prefix: str, namespace: str, suffix: str) -> str:
    clean_prefix = str(prefix or "")
    return f"{clean_prefix}{namespace}:{suffix}"


def _clean_title(value: Any) -> str:
    return str(value or "").strip()


def _parse_market_created_at(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.fromisoformat(text.replace(" ", "T"))
            except ValueError:
                return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_placeholder_market_title(value: Any) -> bool:
    title = _clean_title(value)
    if not title:
        return True
    return any(title.startswith(prefix) for prefix in PLACEHOLDER_TITLE_PREFIXES)


def is_permanent_placeholder_market_title(value: Any) -> bool:
    title = _clean_title(value)
    return any(title.startswith(prefix) for prefix in PERMANENT_PLACEHOLDER_TITLE_PREFIXES)


class NewMarketSignalWatcher:
    def __init__(
        self,
        *,
        redis_url: str,
        redis_prefix: str,
        namespace: str = DEFAULT_NAMESPACE,
        clob_api_base: str,
        snapshot_sqlite_path: str,
        clob_timeout_seconds: int = DEFAULT_CLOB_TIMEOUT_SECONDS,
        retention: int = DEFAULT_RETENTION,
        max_market_age_hours: int = DEFAULT_MAX_MARKET_AGE_HOURS,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if requests is None:
            raise RuntimeError("requests package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for new market signal watcher")
        if not str(clob_api_base or "").strip():
            raise RuntimeError("POLYDATA_CLOB_API_BASE is required for probability snapshots")

        self.redis_prefix = redis_prefix
        self.namespace = namespace
        self.last_seen_key = _redis_key(redis_prefix, namespace, "last_seen_market_id")
        self.items_key = _redis_key(redis_prefix, namespace, "items")
        self.pending_key = _redis_key(redis_prefix, namespace, "pending_market_ids")
        self.retention = max(1, int(retention))
        self.max_market_age_hours = int(max_market_age_hours)
        self.clob_api_base = str(clob_api_base).rstrip("/")
        self.clob_timeout_seconds = max(1, int(clob_timeout_seconds))
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(redis_client=self.redis_client, redis_prefix=redis_prefix, snapshot_store=self.snapshot_store)
        self.http = requests.Session()
        self.http.trust_env = False
        self.http.headers.update({"Accept": "application/json", "User-Agent": "polyData-new-market-signal/1.0"})

    def snapshot_redis_key(self) -> str:
        return _redis_key(self.redis_prefix, SNAPSHOT_NAMESPACE, SNAPSHOT_CACHE_KEY)

    def seed_meta_namespace(self) -> str:
        return SEED_META_NAMESPACE

    def seed_meta_cache_key(self) -> str:
        return SEED_META_CACHE_KEY

    def load_seed_meta(self) -> Dict[str, Any]:
        payload = self.seed_meta_store.load(self.seed_meta_namespace(), self.seed_meta_cache_key())
        return payload if isinstance(payload, dict) else {}

    def store_seed_meta(
        self,
        *,
        status: str,
        record_count: int,
        error_summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        preserve_last_success: bool = False,
    ) -> Dict[str, Any]:
        previous = self.load_seed_meta()
        attempted_at = utc_now_iso()
        success_statuses = {"ok", "bootstrap", "scan"}
        last_success_at = previous.get("lastSuccessAt")
        if not preserve_last_success and str(status or "").strip().lower() in success_statuses:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id="new-market-signals",
            namespace=self.seed_meta_namespace(),
            cache_key=self.seed_meta_cache_key(),
            service_name=SEED_META_SERVICE_NAME,
            expected_interval_seconds=DEFAULT_INTERVAL_SECONDS,
            status=status,
            last_attempt_at=attempted_at,
            last_success_at=last_success_at or attempted_at,
            record_count=record_count,
            source_states={"database": "ok" if not error_summary else "error", "clob": "ok" if not error_summary else "error"},
            error_summary=error_summary,
            cache_mode="seeded",
            payload_status=status,
            metadata=metadata,
        )
        return self.seed_meta_store.store(self.seed_meta_namespace(), self.seed_meta_cache_key(), payload)

    def get_last_seen_market_id(self) -> Optional[int]:
        raw = self.redis_client.get(self.last_seen_key)
        if raw in (None, ""):
            return None
        try:
            return int(str(raw))
        except (TypeError, ValueError):
            return None

    def set_last_seen_market_id(self, market_id: int) -> None:
        self.redis_client.set(self.last_seen_key, int(market_id))

    def get_current_max_market_id(self) -> int:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM markets")
            row = cursor.fetchone()
            data = dict_from_row(row)
            return int(data.get("max_id") or 0)
        finally:
            conn.close()

    def fetch_new_markets(self, last_seen_market_id: int, limit: int) -> List[Dict[str, Any]]:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, gamma_market_id, condition_id, slug, title, yes_token_id, created_at
                FROM markets
                WHERE id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (int(last_seen_market_id), int(limit)),
            )
            return [dict_from_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def fetch_markets_by_local_ids(self, market_ids: Iterable[int]) -> List[Dict[str, Any]]:
        ids = sorted({int(market_id) for market_id in market_ids if int(market_id or 0) > 0})
        if not ids:
            return []
        placeholders = ",".join(["?"] * len(ids))
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id, gamma_market_id, condition_id, slug, title, yes_token_id, created_at
                FROM markets
                WHERE id IN ({placeholders})
                ORDER BY id ASC
                """,
                ids,
            )
            return [dict_from_row(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def fetch_initial_yes_probability(self, yes_token_id: Any) -> tuple[Optional[str], str]:
        token_id = str(yes_token_id or "").strip()
        if not token_id:
            return None, "missing_yes_token"
        try:
            response = self.http.get(
                f"{self.clob_api_base}/book",
                params={"token_id": token_id},
                timeout=self.clob_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json() if response.content else {}
        except Exception:
            return None, "clob_book_error"

        bids = payload.get("bids") if isinstance(payload, dict) else []
        asks = payload.get("asks") if isinstance(payload, dict) else []
        best_bid = _safe_decimal((bids or [{}])[0].get("price") if isinstance(bids, list) and bids else None)
        best_ask = _safe_decimal((asks or [{}])[0].get("price") if isinstance(asks, list) and asks else None)

        if best_bid is not None and best_ask is not None:
            return _format_decimal((best_bid + best_ask) / Decimal("2")), "clob_book_midpoint"
        if best_bid is not None:
            return _format_decimal(best_bid), "clob_book_best_bid"
        if best_ask is not None:
            return _format_decimal(best_ask), "clob_book_best_ask"
        return None, "clob_book_empty"

    def load_items(self) -> List[Dict[str, Any]]:
        raw = self.redis_client.get(self.items_key)
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []

    def store_items(self, items: Iterable[Dict[str, Any]]) -> None:
        normalized = [item for item in items if not is_placeholder_market_title(item.get("title"))][: self.retention]
        self.redis_client.set(self.items_key, json.dumps(normalized, ensure_ascii=True, default=str))

    def build_snapshot_payload(self, *, status: str | None = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        items = self.load_items()
        payload = normalize_new_market_signals_payload(
            {
                "items": items,
                "generatedAt": utc_now_iso(),
                "status": status or ("ok" if items else "empty"),
                "cacheMode": "seeded",
                "source": "polyData new-market-signal seed",
                "sources": {"database": "ok", "clob": "ok"},
                "metadata": dict(metadata or {}),
            },
            limit=self.retention,
            generated_at=utc_now_iso(),
            cache_mode="seeded",
        )
        return payload

    def store_snapshot_payload(self, *, status: str | None = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = self.build_snapshot_payload(status=status, metadata=metadata)
        self.snapshot_store.set(SNAPSHOT_NAMESPACE, SNAPSHOT_CACHE_KEY, payload, SNAPSHOT_TTL_SECONDS)
        self.redis_client.set(self.snapshot_redis_key(), json.dumps(payload, ensure_ascii=True, default=str), ex=SNAPSHOT_TTL_SECONDS)
        publish_cached_panel_snapshot("new-market-signals", payload)
        return payload

    def preserve_current_snapshot(self, *, reason: str) -> Dict[str, Any]:
        payload = self.store_snapshot_payload(
            status="ok" if self.load_items() else "empty",
            metadata={"result": "preserved-current", "reason": reason},
        )
        self.store_seed_meta(
            status="scan",
            record_count=len(payload.get("items") or []),
            metadata={"result": "preserved-current", "reason": reason},
        )
        return {"mode": "preserved", "signals": len(payload.get("items") or []), "reason": reason}

    def load_pending_market_ids(self) -> List[int]:
        raw = self.redis_client.get(self.pending_key)
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        if not isinstance(parsed, list):
            return []
        ids: List[int] = []
        for value in parsed:
            try:
                market_id = int(value)
            except (TypeError, ValueError):
                continue
            if market_id > 0:
                ids.append(market_id)
        return ids

    def store_pending_market_ids(self, market_ids: Iterable[int]) -> None:
        normalized = sorted({int(market_id) for market_id in market_ids if int(market_id or 0) > 0})
        self.redis_client.set(self.pending_key, json.dumps(normalized[-DEFAULT_PENDING_RETENTION:]))

    def is_signal_ready(self, row: Dict[str, Any]) -> bool:
        return not is_placeholder_market_title(row.get("title"))

    def market_freshness_state(self, row: Dict[str, Any], *, now: Optional[datetime] = None) -> str:
        if self.max_market_age_hours <= 0:
            return "fresh"
        created_at = _parse_market_created_at(row.get("created_at"))
        if created_at is None:
            return "missing_created_at"
        now_dt = now or datetime.now(timezone.utc)
        if created_at > now_dt + timedelta(hours=1):
            return "future_created_at"
        if now_dt - created_at > timedelta(hours=self.max_market_age_hours):
            return "stale_created_at"
        return "fresh"

    def run_once(self, *, limit: int = DEFAULT_BATCH_LIMIT) -> Dict[str, Any]:
        self.redis_client.ping()
        last_seen = self.get_last_seen_market_id()
        if last_seen is None:
            baseline = self.get_current_max_market_id()
            self.set_last_seen_market_id(baseline)
            result = {"mode": "bootstrap", "lastSeenMarketId": baseline, "signals": 0, "maxMarketAgeHours": self.max_market_age_hours}
            self.store_snapshot_payload(status="empty", metadata=result)
            self.store_seed_meta(status="bootstrap", record_count=0, metadata=result)
            return result

        pending_ids = self.load_pending_market_ids()
        pending_rows = self.fetch_markets_by_local_ids(pending_ids)
        # The snapshot is also the publisher input. Never advance the cursor over
        # more fresh rows than the snapshot can retain, otherwise the truncated
        # rows can never be published on a later scan.
        rows = self.fetch_new_markets(last_seen, limit=min(max(1, int(limit)), self.retention))
        if not rows and not pending_rows:
            result = {
                "mode": "scan",
                "lastSeenMarketId": last_seen,
                "signals": 0,
                "pending": len(pending_ids),
                "maxMarketAgeHours": self.max_market_age_hours,
            }
            self.store_snapshot_payload(status="ok" if self.load_items() else "empty", metadata=result)
            self.store_seed_meta(status="scan", record_count=len(self.load_items()), metadata=result)
            return result

        observed_at = utc_now_iso()
        now_dt = datetime.now(timezone.utc)
        signals: List[Dict[str, Any]] = []
        max_seen = last_seen
        next_pending_ids = set(pending_ids)
        skipped = 0
        stale = 0
        missing_created_at = 0
        candidate_rows = [*pending_rows, *rows]
        seen_candidate_ids = set()
        for row in candidate_rows:
            if len(signals) >= self.retention:
                break
            market_id = int(row.get("id") or 0)
            if market_id <= 0 or market_id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(market_id)
            max_seen = max(max_seen, market_id)
            if not self.is_signal_ready(row):
                if is_permanent_placeholder_market_title(row.get("title")):
                    next_pending_ids.discard(market_id)
                else:
                    next_pending_ids.add(market_id)
                skipped += 1
                continue
            freshness_state = self.market_freshness_state(row, now=now_dt)
            if freshness_state == "missing_created_at":
                next_pending_ids.add(market_id)
                missing_created_at += 1
                skipped += 1
                continue
            if freshness_state == "stale_created_at":
                next_pending_ids.discard(market_id)
                stale += 1
                skipped += 1
                continue
            probability, source = self.fetch_initial_yes_probability(row.get("yes_token_id"))
            next_pending_ids.discard(market_id)
            signals.append(
                {
                    "marketId": market_id,
                    "localMarketId": market_id,
                    "gammaMarketId": row.get("gamma_market_id"),
                    "conditionId": row.get("condition_id"),
                    "slug": row.get("slug"),
                    "title": _clean_title(row.get("title")) or f"Market {market_id}",
                    "initialYesProbability": probability,
                    "probabilitySource": source,
                    "observedAt": observed_at,
                    "marketCreatedAt": row.get("created_at"),
                }
            )

        existing = self.load_items()
        seen_ids = {int(item.get("marketId")) for item in signals if item.get("marketId") is not None}
        deduped_existing = [item for item in existing if int(item.get("marketId") or 0) not in seen_ids]
        self.store_items([*reversed(signals), *deduped_existing])
        self.store_pending_market_ids(next_pending_ids)
        self.set_last_seen_market_id(max_seen)
        stored_items = self.load_items()
        result = {
            "mode": "scan",
            "lastSeenMarketId": max_seen,
            "signals": len(signals),
            "pending": len(next_pending_ids),
            "skipped": skipped,
            "staleCreatedAt": stale,
            "missingCreatedAt": missing_created_at,
            "maxMarketAgeHours": self.max_market_age_hours,
        }
        self.store_snapshot_payload(status="ok" if stored_items else "empty", metadata=result)
        self.store_seed_meta(status="scan", record_count=len(stored_items), metadata=result)
        return result


def main() -> None:
    settings = load_api_settings()
    parser = argparse.ArgumentParser(description="Watch new markets and store panel signals in Redis")
    add_db_cli_args(parser)
    parser.add_argument("--watch", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="Polling interval in seconds")
    parser.add_argument("--limit", type=int, default=DEFAULT_BATCH_LIMIT, help="Max markets to process per scan")
    parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION, help="Recent Redis signal retention")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="Redis namespace after POLYDATA_REDIS_PREFIX")
    parser.add_argument("--redis-url", default=settings.redis_url, help="Redis URL")
    parser.add_argument("--redis-prefix", default=settings.redis_prefix, help="Redis key prefix")
    parser.add_argument("--clob-api-base", default=settings.clob_api_base or POLYMARKET_CLOB_API_BASE, help="Polymarket CLOB API base")
    parser.add_argument("--clob-timeout-seconds", type=int, default=settings.clob_timeout_seconds, help="CLOB request timeout")
    parser.add_argument(
        "--max-market-age-hours",
        type=int,
        default=_env_int("POLYDATA_NEW_MARKET_SIGNAL_MAX_AGE_HOURS", DEFAULT_MAX_MARKET_AGE_HOURS),
        help="Only emit markets whose market.created_at is within this many hours; <=0 disables the guard",
    )
    args = parser.parse_args()
    db_read_timeout = max(3, int(os.environ.get("POLYDATA_NEW_MARKET_SIGNAL_DB_READ_TIMEOUT_SECONDS", DEFAULT_DB_READ_TIMEOUT_SECONDS)))
    current_read_timeout = int(os.environ.get("POLYMARKET_MYSQL_READ_TIMEOUT", "60") or "60")
    if current_read_timeout > db_read_timeout:
        os.environ["POLYMARKET_MYSQL_READ_TIMEOUT"] = str(db_read_timeout)
    configure_db_from_args(args)

    watcher = NewMarketSignalWatcher(
        redis_url=args.redis_url,
        redis_prefix=args.redis_prefix,
        namespace=args.namespace,
        clob_api_base=args.clob_api_base,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        clob_timeout_seconds=args.clob_timeout_seconds,
        retention=args.retention,
        max_market_age_hours=args.max_market_age_hours,
    )
    print(f"[new-market-signal] db={describe_db_target()} redis_key={watcher.items_key}", file=sys.stderr)

    while True:
        started = time.perf_counter()
        try:
            result = watcher.run_once(limit=args.limit)
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"[new-market-signal] {json.dumps(result, ensure_ascii=True)} duration_ms={elapsed_ms:.1f}", file=sys.stderr)
        except Exception as exc:
            result = watcher.preserve_current_snapshot(reason=str(exc))
            print(f"[new-market-signal] scan failed: {exc}", file=sys.stderr)
            print(f"[new-market-signal] {json.dumps(result, ensure_ascii=True)} duration_ms={(time.perf_counter() - started) * 1000:.1f}", file=sys.stderr)
            if not args.watch:
                raise
        if not args.watch:
            return
        time.sleep(max(1, int(args.interval)))


if __name__ == "__main__":
    main()
