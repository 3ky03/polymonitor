from __future__ import annotations

import json
import hashlib
import contextlib
import concurrent.futures
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict

from api import cache as api_cache

try:
    import requests
    from requests import RequestException
except Exception:  # pragma: no cover - requests is an API dependency in normal runtime.
    requests = None
    RequestException = Exception

from quant.core.db import PostgresSettings, postgres_connection
from quant.core.schema import create_schema
from quant.orderbook import OrderBookNotReady, OrderBookOutOfOrder, TokenBookIdentity, normalize_polymarket_event, normalize_rest_book
from quant.orderbook.coverage import (
    CoverageSelectionContext,
    PRIORITY_TOPICS,
    select_orderbook_coverage_targets,
    summarize_coverage_targets,
)
from quant.orderbook.registry import OrderBookRegistry


_SNAPSHOT_SCHEMA_READY = False
_SNAPSHOT_SCHEMA_LOCK = threading.Lock()
BOOK_LEVEL_LIMIT = 12
DEFAULT_LOB_CACHE_TTL_SECONDS = 3
DEFAULT_LOB_REDIS_CACHE_TTL_SECONDS = 120
DEFAULT_LOB_WARM_REQUEST_TTL_SECONDS = 600
DEFAULT_LOB_WARM_REQUEST_LIMIT = 32
DEFAULT_UNCHANGED_SNAPSHOT_MIN_INTERVAL_SECONDS = 300
DEFAULT_LOB_MAINTENANCE_STATUS_PATH = Path(
    "/tmp/polydata/lob-maintenance-status.json"
)
LOB_COVERAGE_RAW_ROW_LIMIT = 1200
WORLDCUP_LOB_PRE_KICKOFF_MINUTES = 60
WORLDCUP_LOB_FALLBACK_MATCH_MINUTES = 150
DEFAULT_WORLDCUP_LOB_MARKET_LIMIT = 12
LOB_REDIS_CACHE_NAMESPACE = "lob:token"
LOB_WARM_REQUEST_INDEX = "lob:warm-requests:index"
LOB_WARM_REQUEST_DATA = "lob:warm-requests:data"
WORLDCUP_TEAM_SLUG_CODES = {
    "argentina": "arg",
    "australia": "aus",
    "belgium": "bel",
    "bosnia & herzegovina": "bih",
    "bosnia and herzegovina": "bih",
    "bosnia-herzegovina": "bih",
    "brazil": "bra",
    "canada": "can",
    "czech republic": "cze",
    "czechia": "cze",
    "cote d'ivoire": "civ",
    "côte d'ivoire": "civ",
    "curacao": ("cur", "cuw", "kor"),
    "curaçao": ("cur", "cuw", "kor"),
    "ecuador": "ecu",
    "england": "eng",
    "france": "fra",
    "germany": "ger",
    "ivory coast": "civ",
    "italy": "ita",
    "japan": "jpn",
    "korea republic": "kr",
    "mexico": "mex",
    "netherlands": ("nld", "ned"),
    "paraguay": "par",
    "qatar": "qat",
    "south africa": "rsa",
    "south korea": "kr",
    "spain": "esp",
    "sweden": "swe",
    "switzerland": "che",
    "tunisia": "tun",
    "turkey": "tur",
    "turkiye": "tur",
    "türkiye": "tur",
    "united states": "usa",
    "usa": "usa",
}


class LocalOrderBookRuntimeManager:
    """REST snapshot bootstrapper around the shared LocalOrderBook registry."""

    def __init__(
        self,
        *,
        api_base: str,
        timeout_seconds: int = 3,
        cache_ttl_seconds: int = DEFAULT_LOB_CACHE_TTL_SECONDS,
        depth_limit: int = BOOK_LEVEL_LIMIT,
        session: Any | None = None,
    ) -> None:
        if requests is None and session is None:  # pragma: no cover - dependency guard.
            raise RuntimeError("requests is required for LocalOrderBookRuntimeManager")
        self.api_base = str(api_base or "").rstrip("/")
        self.timeout_seconds = max(1, int(timeout_seconds or 3))
        self.cache_ttl_seconds = max(1, int(cache_ttl_seconds or DEFAULT_LOB_CACHE_TTL_SECONDS))
        self.depth_limit = max(1, min(int(depth_limit or BOOK_LEVEL_LIMIT), BOOK_LEVEL_LIMIT))
        self.registry = OrderBookRegistry()
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._fetch_locks: dict[str, threading.Lock] = {}
        self._session = session or requests.Session()
        if hasattr(self._session, "trust_env"):
            self._session.trust_env = str(os.environ.get("POLYDATA_CLOB_TRUST_ENV_PROXY") or "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        if hasattr(self._session, "headers"):
            self._session.headers.update(
                {
                    "Accept": "application/json",
                    "User-Agent": "polyData-local-orderbook/1.0",
                }
            )
    def get_market_snapshot(
        self,
        *,
        market_id: int,
        yes_token_id: str,
        no_token_id: str,
        market_title: str = "",
        condition_id: str = "",
        market_slug: str | None = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        yes_state = self.get_token_snapshot(
            token_id=yes_token_id,
            market_id=market_id,
            condition_id=condition_id,
            outcome="YES",
            outcome_index=0,
            market_slug=market_slug,
            force_refresh=force_refresh,
        )
        no_state = self.get_token_snapshot(
            token_id=no_token_id,
            market_id=market_id,
            condition_id=condition_id,
            outcome="NO",
            outcome_index=1,
            market_slug=market_slug,
            force_refresh=force_refresh,
        )
        return _panel_payload_from_state(
            market_id=market_id,
            local_market_id=market_id,
            market_title=market_title,
            yes_state=yes_state,
            no_state=no_state,
            token_mode=False,
        )

    def get_token_pair_snapshot(
        self,
        *,
        yes_token_id: str,
        no_token_id: str = "",
        market_title: str = "",
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        if no_token_id:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                yes_future = executor.submit(
                    self.get_token_snapshot,
                    token_id=yes_token_id,
                    market_id=0,
                    outcome="YES",
                    outcome_index=0,
                    force_refresh=force_refresh,
                )
                no_future = executor.submit(
                    self.get_token_snapshot,
                    token_id=no_token_id,
                    market_id=0,
                    outcome="NO",
                    outcome_index=1,
                    force_refresh=force_refresh,
                )
                yes_state = yes_future.result()
                no_state = no_future.result()
        else:
            yes_state = self.get_token_snapshot(
                token_id=yes_token_id,
                market_id=0,
                outcome="YES",
                outcome_index=0,
                force_refresh=force_refresh,
            )
            no_state = _empty_state_payload("NO")
        return _panel_payload_from_state(
            market_id=0,
            local_market_id=None,
            market_title=market_title,
            yes_state=yes_state,
            no_state=no_state,
            token_mode=True,
        )

    def get_token_snapshot(
        self,
        *,
        token_id: str,
        market_id: int = 0,
        condition_id: str = "",
        outcome: str = "YES",
        outcome_index: int = 0,
        market_slug: str | None = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        token_id = str(token_id or "").strip()
        if not token_id:
            return _empty_state_payload(outcome)
        now = time.time()
        with self._lock:
            cached = self._cache.get(token_id)
            if not force_refresh and cached and now - float(cached.get("cached_at") or 0) < self.cache_ttl_seconds:
                return dict(cached["payload"])
            if not force_refresh:
                book = self.registry.get(token_id)
                if book is not None and book.ready:
                    payload = book.snapshot_payload(depth_levels=self.depth_limit)
                    cached_payload = cached.get("payload") if cached and isinstance(cached.get("payload"), dict) else {}
                    payload["source"] = "local-orderbook"
                    payload["snapshot_source"] = cached_payload.get("snapshot_source") or "registry"
                    payload["runtime_model"] = "LocalOrderBook"
                    self._cache[token_id] = {"cached_at": now, "payload": dict(payload)}
                    return payload

        with self._lock:
            fetch_lock = self._fetch_locks.setdefault(token_id, threading.Lock())

        with fetch_lock:
            now = time.time()
            with self._lock:
                cached = self._cache.get(token_id)
                if not force_refresh and cached and now - float(cached.get("cached_at") or 0) < self.cache_ttl_seconds:
                    return dict(cached["payload"])
                if not force_refresh:
                    book = self.registry.get(token_id)
                    if book is not None and book.ready:
                        payload = book.snapshot_payload(depth_levels=self.depth_limit)
                        payload["source"] = "local-orderbook"
                        payload["snapshot_source"] = "registry"
                        payload["runtime_model"] = "LocalOrderBook"
                        self._cache[token_id] = {"cached_at": now, "payload": dict(payload)}
                        return payload

            state_payload = self._fetch_apply_snapshot(
                TokenBookIdentity(
                    token_id=token_id,
                    market_id=int(market_id or 0),
                    condition_id=str(condition_id or ""),
                    outcome=str(outcome or ""),
                    outcome_index=int(outcome_index or 0),
                    market_slug=market_slug,
                )
            )
            with self._lock:
                self._cache[token_id] = {"cached_at": time.time(), "payload": dict(state_payload)}
            return state_payload

    def get_cached_market_snapshot(
        self,
        *,
        market_id: int,
        yes_token_id: str,
        no_token_id: str,
        market_title: str = "",
    ) -> Dict[str, Any]:
        yes_state = self._state_payload_from_registry(str(yes_token_id or ""), fallback_outcome="YES")
        no_state = self._state_payload_from_registry(str(no_token_id or ""), fallback_outcome="NO") if no_token_id else _empty_state_payload("NO")
        return _panel_payload_from_state(
            market_id=market_id,
            local_market_id=market_id,
            market_title=market_title,
            yes_state=yes_state,
            no_state=no_state,
            token_mode=False,
        )

    def apply_polymarket_event(
        self,
        event: Dict[str, Any],
        identities_by_token: Dict[str, TokenBookIdentity],
    ) -> list[Dict[str, Any]]:
        applied: list[Dict[str, Any]] = []
        for normalized in normalize_polymarket_event(event):
            identity = identities_by_token.get(normalized.token_id)
            if identity is None:
                continue
            applied.append(self.apply_normalized_event(identity, normalized))
        return applied

    def apply_normalized_event(self, identity: TokenBookIdentity, event: Any) -> Dict[str, Any]:
        with self._lock:
            try:
                self.registry.apply(identity, event)
            except (OrderBookNotReady, OrderBookOutOfOrder):
                self._cache.pop(identity.token_id, None)
                raise
            payload = self._state_payload_from_registry_locked(identity.token_id, fallback_outcome=identity.outcome)
            payload["snapshot_source"] = "websocket"
            self._cache[identity.token_id] = {"cached_at": time.time(), "payload": dict(payload)}
            return payload

    def mark_all_stale(self, reason: str = "runtime_stale") -> int:
        with self._lock:
            self.registry.mark_all_stale(reason)
            self._cache.clear()
            return len(self.registry.books)

    def mark_token_stale_if_idle(self, token_id: str, *, now_ms: int, stale_after_ms: int) -> bool:
        with self._lock:
            book = self.registry.get(str(token_id or ""))
            if book is None:
                return False
            changed = book.mark_stale_if_idle(now_ms=now_ms, stale_after_ms=stale_after_ms)
            if changed:
                self._cache.pop(book.identity.token_id, None)
            return changed

    def runtime_book_counts(self) -> Dict[str, int]:
        with self._lock:
            books = list(self.registry.books.values())
        return {
            "bookCount": len(books),
            "readyCount": sum(1 for book in books if book.status == "ready"),
            "staleCount": sum(1 for book in books if book.status == "stale"),
            "notReadyCount": sum(1 for book in books if book.status == "not_ready"),
        }

    def _state_payload_from_registry(self, token_id: str, *, fallback_outcome: str = "") -> Dict[str, Any]:
        with self._lock:
            cached = self._cache.get(token_id)
            if cached and isinstance(cached.get("payload"), dict):
                return dict(cached["payload"])
            return self._state_payload_from_registry_locked(token_id, fallback_outcome=fallback_outcome)

    def _state_payload_from_registry_locked(self, token_id: str, *, fallback_outcome: str = "") -> Dict[str, Any]:
        book = self.registry.get(token_id)
        if book is None:
            return _empty_state_payload(fallback_outcome, token_id=token_id)
        payload = book.snapshot_payload(depth_levels=self.depth_limit)
        payload["source"] = "local-orderbook"
        payload["snapshot_source"] = "registry"
        payload["runtime_model"] = "LocalOrderBook"
        return payload

    def _fetch_apply_snapshot(self, identity: TokenBookIdentity) -> Dict[str, Any]:
        response = self._session.get(
            f"{self.api_base}/book",
            params={"token_id": identity.token_id},
            timeout=self.timeout_seconds,
        )
        if response.status_code == 404:
            raw_payload: dict[str, Any] = {"bids": [], "asks": [], "status_code": 404}
        else:
            response.raise_for_status()
            raw_payload = response.json() if getattr(response, "content", b"") else {}
            if not isinstance(raw_payload, dict):
                raw_payload = {}

        event = normalize_rest_book(identity.token_id, raw_payload)
        with self._lock:
            self.registry.apply(identity, event)
            book = self.registry.get(identity.token_id)
            if book is None:
                return _empty_state_payload(identity.outcome, token_id=identity.token_id)
            payload = book.snapshot_payload(depth_levels=self.depth_limit)
        payload["source"] = "local-orderbook"
        payload["snapshot_source"] = "rest-book"
        payload["runtime_model"] = "LocalOrderBook"
        if response.status_code == 404:
            payload["book_status"] = "no-book"
        return payload


def _redis_client(ctx: dict) -> Any | None:
    try:
        return api_cache.get_redis_client(ctx)
    except Exception:
        return None


def _redis_runtime_key(ctx: dict, suffix: str) -> str:
    return f"{str(ctx.get('REDIS_PREFIX') or '')}{suffix}"


def _lob_redis_ttl_seconds() -> int:
    return _int_clamped(
        os.environ.get("POLYDATA_LOB_REDIS_CACHE_TTL_SECONDS"),
        default=DEFAULT_LOB_REDIS_CACHE_TTL_SECONDS,
        minimum=15,
        maximum=900,
    )


def cache_runtime_lob_payload(
    ctx: dict,
    payload: Dict[str, Any],
    yes_token_id: str,
    no_token_id: str = "",
) -> bool:
    if not isinstance(payload, dict) or not yes_token_id:
        return False
    cached = dict(payload)
    cached.setdefault("cachedAt", _iso_utc_now())
    wrote = False
    for token_id in (str(yes_token_id or "").strip(), str(no_token_id or "").strip()):
        if not token_id:
            continue
        try:
            api_cache.set_cached_payload(
                ctx,
                LOB_REDIS_CACHE_NAMESPACE,
                token_id,
                cached,
                _lob_redis_ttl_seconds(),
            )
            wrote = True
        except Exception:
            continue
    return wrote


def _get_cached_runtime_lob_payload(ctx: dict, token_id: str) -> Dict[str, Any] | None:
    try:
        payload = api_cache.get_cached_payload(
            ctx,
            LOB_REDIS_CACHE_NAMESPACE,
            str(token_id or "").strip(),
        )
    except Exception:
        return None
    if not isinstance(payload, dict) or not _lob_payload_has_levels(payload):
        return None
    cached = dict(payload)
    cached["cacheMode"] = "redis-hot"
    return cached


def enqueue_lob_warm_request(
    ctx: dict,
    *,
    yes_token_id: str,
    no_token_id: str = "",
    market_id: int | None = None,
    market_title: str = "",
) -> bool:
    yes_token_id = str(yes_token_id or "").strip()
    if not yes_token_id:
        return False
    client = _redis_client(ctx)
    if client is None:
        return False
    now = int(time.time())
    ttl_seconds = _int_clamped(
        os.environ.get("POLYDATA_LOB_WARM_REQUEST_TTL_SECONDS"),
        default=DEFAULT_LOB_WARM_REQUEST_TTL_SECONDS,
        minimum=60,
        maximum=3600,
    )
    payload = {
        "yesTokenId": yes_token_id,
        "noTokenId": str(no_token_id or "").strip() or None,
        "marketId": int(market_id) if market_id not in (None, "") else None,
        "marketTitle": str(market_title or "")[:240] or None,
        "requestedAt": now,
        "expiresAt": now + ttl_seconds,
    }
    index_key = _redis_runtime_key(ctx, LOB_WARM_REQUEST_INDEX)
    data_key = _redis_runtime_key(ctx, LOB_WARM_REQUEST_DATA)
    try:
        pipe = client.pipeline(transaction=False)
        pipe.hset(data_key, yes_token_id, json.dumps(payload, ensure_ascii=True))
        pipe.zadd(index_key, {yes_token_id: now})
        pipe.zremrangebyscore(index_key, "-inf", now - ttl_seconds)
        pipe.expire(index_key, ttl_seconds * 2)
        pipe.expire(data_key, ttl_seconds * 2)
        pipe.execute()
        return True
    except Exception:
        return False


def _recent_lob_warm_requests(ctx: dict) -> list[Dict[str, Any]]:
    client = _redis_client(ctx)
    if client is None:
        return []
    now = int(time.time())
    ttl_seconds = _int_clamped(
        os.environ.get("POLYDATA_LOB_WARM_REQUEST_TTL_SECONDS"),
        default=DEFAULT_LOB_WARM_REQUEST_TTL_SECONDS,
        minimum=60,
        maximum=3600,
    )
    limit = _int_clamped(
        os.environ.get("POLYDATA_LOB_WARM_REQUEST_LIMIT"),
        default=DEFAULT_LOB_WARM_REQUEST_LIMIT,
        minimum=1,
        maximum=100,
    )
    index_key = _redis_runtime_key(ctx, LOB_WARM_REQUEST_INDEX)
    data_key = _redis_runtime_key(ctx, LOB_WARM_REQUEST_DATA)
    try:
        token_ids = client.zrevrangebyscore(
            index_key,
            "+inf",
            now - ttl_seconds,
            start=0,
            num=limit,
        )
        raw_values = client.hmget(data_key, token_ids) if token_ids else []
    except Exception:
        return []
    requests: list[Dict[str, Any]] = []
    for raw in raw_values:
        try:
            payload = json.loads(raw) if raw else None
        except Exception:
            payload = None
        if not isinstance(payload, dict) or int(payload.get("expiresAt") or 0) < now:
            continue
        requests.append(payload)
    return requests


def get_dynamic_lob_warm_targets(ctx: dict) -> list[Dict[str, Any]]:
    requests = _recent_lob_warm_requests(ctx)
    direct_targets: list[Dict[str, Any]] = []
    unresolved_requests: list[Dict[str, Any]] = []
    for item in requests:
        market_id = _int_or_none(item.get("marketId"))
        yes_token_id = str(item.get("yesTokenId") or "").strip()
        no_token_id = str(item.get("noTokenId") or "").strip()
        if market_id is None or not yes_token_id or not no_token_id:
            unresolved_requests.append(item)
            continue
        direct_targets.append({
            "marketId": market_id,
            "marketSlug": "",
            "marketTitle": str(item.get("marketTitle") or ""),
            "category": "dynamic",
            "tags": [],
            "conditionId": "",
            "yesTokenId": yes_token_id,
            "noTokenId": no_token_id,
            "volume24h": 0,
            "tradeCount24h": 0,
            "topic": "dynamic",
            "tier": "hot",
            "priorityScore": "1000000",
            "sampleIntervalSeconds": 15,
            "retentionDays": 14,
            "reason": "dynamic:recent-selection",
        })
    requests = unresolved_requests
    market_ids = sorted({
        int(item["marketId"])
        for item in requests
        if item.get("marketId") not in (None, "")
    })
    token_ids = sorted({
        str(item.get("yesTokenId") or "").strip()
        for item in requests
        if str(item.get("yesTokenId") or "").strip()
    })
    if not market_ids and not token_ids:
        return direct_targets
    clauses: list[str] = []
    params: list[Any] = []
    if market_ids:
        clauses.append(f"m.id IN ({', '.join('?' for _ in market_ids)})")
        params.extend(market_ids)
    if token_ids:
        placeholders = ", ".join("?" for _ in token_ids)
        clauses.append(f"(m.yes_token_id IN ({placeholders}) OR m.no_token_id IN ({placeholders}))")
        params.extend(token_ids)
        params.extend(token_ids)
    try:
        rows = ctx["query_all"](
            f"""
            SELECT
                m.id AS market_id,
                m.slug AS market_slug,
                COALESCE(m.title, m.slug) AS market_title,
                m.category,
                m.tags,
                m.condition_id,
                m.yes_token_id,
                m.no_token_id,
                COALESCE(mls.volume_24h, 0) AS volume_24h,
                COALESCE(mls.trade_count_24h, 0) AS trade_count_24h
            FROM core.markets m
            LEFT JOIN core.market_list_serving mls ON mls.market_id = m.id
            LEFT JOIN core.market_status_snapshot mss ON mss.market_id = m.id
            WHERE ({' OR '.join(clauses)})
              AND COALESCE(m.yes_token_id, '') <> ''
              AND COALESCE(m.no_token_id, '') <> ''
              AND NOT COALESCE(mss.is_trading_closed, FALSE)
              AND NOT COALESCE(mss.is_resolved, FALSE)
            """,
            tuple(params),
        )
    except Exception:
        return direct_targets
    resolved_targets = [
        {
            "marketId": row.get("market_id"),
            "marketSlug": row.get("market_slug"),
            "marketTitle": row.get("market_title"),
            "category": row.get("category"),
            "tags": row.get("tags") or [],
            "conditionId": row.get("condition_id"),
            "yesTokenId": row.get("yes_token_id"),
            "noTokenId": row.get("no_token_id"),
            "volume24h": row.get("volume_24h") or 0,
            "tradeCount24h": row.get("trade_count_24h") or 0,
            "topic": "dynamic",
            "tier": "hot",
            "priorityScore": "1000000",
            "sampleIntervalSeconds": 15,
            "retentionDays": 14,
            "reason": "dynamic:recent-selection",
        }
        for row in rows
        if row.get("market_id") is not None
    ]
    return [*direct_targets, *resolved_targets]


def _empty_book_side() -> Dict[str, Any]:
    return {"bids": [], "asks": [], "bestBid": None, "bestAsk": None, "spread": None}


def _empty_state_payload(outcome: str = "", *, token_id: str = "") -> Dict[str, Any]:
    return {
        "snapshot_id": 0,
        "token_id": str(token_id or ""),
        "market_id": 0,
        "condition_id": "",
        "outcome": str(outcome or ""),
        "side": str(outcome or ""),
        "status": "not_ready",
        "book_status": "no-book",
        "source": "local-orderbook",
        "snapshot_source": "rest-book",
        "runtime_model": "LocalOrderBook",
        "generation": 0,
        "last_event_ts_ms": None,
        "timestamp": None,
        "snapshot_timestamp": None,
        "snapshot_version": None,
        "best_bid": None,
        "best_ask": None,
        "mid": None,
        "spread": None,
        "bid_depth": "0",
        "ask_depth": "0",
        "depth_total": "0",
        "imbalance": None,
        "bids": [],
        "asks": [],
    }


def _levels_with_side(levels: Any, side: str) -> list[dict[str, Any]]:
    normalized = _normalize_levels(levels, reverse=(side == "bid"))
    return [{"side": side, **level} for level in normalized]


def _panel_side_from_state_payload(state_payload: Dict[str, Any]) -> Dict[str, Any]:
    state = dict(state_payload or {})
    bids = _levels_with_side(state.get("bids"), "bid")
    asks = _levels_with_side(state.get("asks"), "ask")
    best_bid = state.get("best_bid") or (bids[0].get("price") if bids else None)
    best_ask = state.get("best_ask") or (asks[0].get("price") if asks else None)
    return {
        **state,
        "statePayload": state,
        "tokenId": state.get("token_id") or "",
        "marketId": state.get("market_id"),
        "conditionId": state.get("condition_id") or "",
        "outcome": state.get("outcome") or state.get("side") or "",
        "status": state.get("status") or "not_ready",
        "bookStatus": state.get("book_status") or ("ok" if bids or asks else "no-book"),
        "source": "local-orderbook",
        "snapshotSource": state.get("snapshot_source") or "rest-book",
        "runtimeModel": state.get("runtime_model") or "LocalOrderBook",
        "generation": state.get("generation") or 0,
        "lastEventTsMs": state.get("last_event_ts_ms"),
        "snapshotTimestamp": state.get("snapshot_timestamp") or state.get("timestamp"),
        "snapshotVersion": state.get("snapshot_version"),
        "bestBid": best_bid,
        "bestAsk": best_ask,
        "mid": state.get("mid"),
        "spread": state.get("spread"),
        "bidDepth": state.get("bid_depth") or "0",
        "askDepth": state.get("ask_depth") or "0",
        "depthTotal": state.get("depth_total") or "0",
        "imbalance": state.get("imbalance"),
        "levelCountBid": len(bids),
        "levelCountAsk": len(asks),
        "bids": bids,
        "asks": asks,
    }


def _panel_payload_from_state(
    *,
    market_id: int,
    local_market_id: int | None,
    market_title: str,
    yes_state: Dict[str, Any],
    no_state: Dict[str, Any],
    token_mode: bool,
) -> Dict[str, Any]:
    yes = _panel_side_from_state_payload(yes_state)
    no = _panel_side_from_state_payload(no_state)
    fetched_at = _iso_utc_now()
    timestamps = [value for value in (yes.get("snapshotTimestamp"), no.get("snapshotTimestamp")) if value]
    side_snapshot_sources = {
        str(value)
        for value in (yes.get("snapshotSource"), no.get("snapshotSource"))
        if value
    }
    snapshot_source = side_snapshot_sources.pop() if len(side_snapshot_sources) == 1 else ("mixed" if side_snapshot_sources else "rest-book")
    payload = {
        "marketId": market_id,
        "localMarketId": local_market_id,
        "marketTitle": str(market_title or ""),
        "fetchedAt": fetched_at,
        "updatedAt": max(timestamps) if timestamps else fetched_at,
        "tokenMode": bool(token_mode),
        "source": "local-orderbook",
        "snapshotSource": snapshot_source,
        "runtimeModel": "LocalOrderBook",
        "yes": yes,
        "no": no,
    }
    payload["bookStatus"] = "ok" if _lob_payload_has_levels(payload) else "no-book"
    return payload


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _float_or_none(value: Any) -> float | None:
    parsed = _decimal_or_none(value)
    return float(parsed) if parsed is not None else None


def _int_or_none_local(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _snapshot_version(token_id: str, side_name: str, fetched_at: str, side_payload: Dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(str(token_id).encode("utf-8"))
    digest.update(b"|")
    digest.update(str(side_name).encode("utf-8"))
    digest.update(b"|")
    digest.update(str(fetched_at).encode("utf-8"))
    digest.update(b"|")
    for key in ("bids", "asks"):
        for level in _normalize_levels(side_payload.get(key), reverse=(key == "bids")):
            digest.update(str(level.get("price")).encode("ascii"))
            digest.update(b":")
            digest.update(str(level.get("size")).encode("ascii"))
            digest.update(b";")
        digest.update(b"|")
    return digest.hexdigest()[:20]


def _unchanged_snapshot_min_interval_seconds() -> int:
    raw = os.environ.get("POLYDATA_LOB_UNCHANGED_SNAPSHOT_MIN_INTERVAL_SECONDS")
    try:
        return max(0, int(raw if raw not in (None, "") else DEFAULT_UNCHANGED_SNAPSHOT_MIN_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_UNCHANGED_SNAPSHOT_MIN_INTERVAL_SECONDS


def _normalize_levels(rows: Any, *, reverse: bool = False) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _decimal_or_none(row.get("price"))
        size = _decimal_or_none(row.get("size"))
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        parsed.append({"price": str(price), "size": str(size)})
    parsed.sort(key=lambda item: Decimal(str(item["price"])), reverse=reverse)
    return parsed[:BOOK_LEVEL_LIMIT]


def _sum_notional(levels: list[dict[str, Any]], limit: int = BOOK_LEVEL_LIMIT) -> Decimal:
    total = Decimal("0")
    for level in levels[:limit]:
        price = _decimal_or_none(level.get("price"))
        size = _decimal_or_none(level.get("size"))
        if price is None or size is None:
            continue
        total += price * size
    return total


def _book_side_summary(side: Dict[str, Any]) -> Dict[str, Any]:
    bids = _normalize_levels(side.get("bids"), reverse=True)
    asks = _normalize_levels(side.get("asks"), reverse=False)
    best_bid = _decimal_or_none(side.get("bestBid")) or (_decimal_or_none(bids[0].get("price")) if bids else None)
    best_ask = _decimal_or_none(side.get("bestAsk")) or (_decimal_or_none(asks[0].get("price")) if asks else None)
    spread = None
    mid = None
    if best_bid is not None and best_ask is not None:
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / Decimal("2")
    bid_depth = _sum_notional(bids)
    ask_depth = _sum_notional(asks)
    depth_total = bid_depth + ask_depth
    imbalance = (bid_depth / depth_total) if depth_total > 0 else None
    return {
        "bids": bids,
        "asks": asks,
        "bestBid": str(best_bid) if best_bid is not None else None,
        "bestAsk": str(best_ask) if best_ask is not None else None,
        "spread": str(spread) if spread is not None else None,
        "mid": str(mid) if mid is not None else None,
        "bidDepth": str(bid_depth),
        "askDepth": str(ask_depth),
        "depthTotal": str(depth_total),
        "imbalance": str(imbalance) if imbalance is not None else None,
        "levelCountBid": len(bids),
        "levelCountAsk": len(asks),
    }


def _book_side_has_levels(side: Dict[str, Any]) -> bool:
    if not isinstance(side, dict):
        return False
    return bool(side.get("bids") or side.get("asks"))


def _lob_payload_has_levels(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    return _book_side_has_levels(payload.get("yes") or {}) or _book_side_has_levels(payload.get("no") or {})


def _ensure_snapshot_schema() -> None:
    global _SNAPSHOT_SCHEMA_READY
    if _SNAPSHOT_SCHEMA_READY:
        return
    with _SNAPSHOT_SCHEMA_LOCK:
        if _SNAPSHOT_SCHEMA_READY:
            return
        with postgres_connection(PostgresSettings()) as conn:
            create_schema(conn)
        _SNAPSHOT_SCHEMA_READY = True


def _persist_book_side_snapshot(
    ctx: dict,
    *,
    token_id: str,
    side_name: str,
    paired_token_id: str,
    market_title: str,
    source: str,
    book_status: str,
    fetched_at: str,
    block_number: Any = None,
    side_payload: Dict[str, Any],
    coverage_payload: Dict[str, Any] | None = None,
) -> None:
    if not token_id:
        return
    summary = _book_side_summary(side_payload)
    state_payload = side_payload.get("statePayload") if isinstance(side_payload, dict) else None
    state_payload = dict(state_payload) if isinstance(state_payload, dict) else dict(side_payload or {})
    coverage_payload = dict(coverage_payload) if isinstance(coverage_payload, dict) else {}
    market_id = _int_or_none_local(state_payload.get("market_id") or side_payload.get("marketId"))
    condition_id = str(state_payload.get("condition_id") or side_payload.get("conditionId") or "")
    market_slug = str(state_payload.get("market_slug") or side_payload.get("marketSlug") or "")
    snapshot_source = str(state_payload.get("snapshot_source") or side_payload.get("snapshotSource") or "rest-book")
    storage_tier = str(coverage_payload.get("tier") or state_payload.get("storage_tier") or side_payload.get("storageTier") or "unknown")
    book_generation = _int_or_none_local(state_payload.get("generation") or side_payload.get("generation"))
    last_event_ts_ms = _int_or_none_local(state_payload.get("last_event_ts_ms") or side_payload.get("lastEventTsMs"))
    payload = {
        **state_payload,
        **summary,
        "tokenId": token_id,
        "pairedTokenId": paired_token_id,
        "side": side_name,
        "market_id": market_id or state_payload.get("market_id"),
        "marketId": market_id,
        "condition_id": condition_id,
        "conditionId": condition_id,
        "market_slug": market_slug,
        "marketSlug": market_slug,
        "marketTitle": market_title,
        "source": source or "local-orderbook",
        "bookStatus": book_status,
        "fetchedAt": fetched_at,
        "blockNumber": block_number,
        "coverage": coverage_payload,
        "runtimeModel": state_payload.get("runtime_model") or side_payload.get("runtimeModel") or "LocalOrderBook",
        "snapshot_source": snapshot_source,
        "snapshotSource": snapshot_source,
        "storageTier": storage_tier,
        "bookGeneration": book_generation,
        "lastEventTsMs": last_event_ts_ms,
    }
    snapshot_version = str(
        state_payload.get("snapshot_version")
        or side_payload.get("snapshotVersion")
        or _snapshot_version(token_id, side_name, fetched_at, side_payload)
    )
    _ensure_snapshot_schema()
    with postgres_connection(PostgresSettings()) as conn:
        with conn.cursor() as cur:
            unchanged_min_interval_seconds = _unchanged_snapshot_min_interval_seconds()
            if snapshot_version and unchanged_min_interval_seconds > 0:
                cur.execute(
                    """
                    SELECT 1
                    FROM quant.clob_orderbook_snapshots
                    WHERE token_id = %s
                      AND side = %s
                      AND snapshot_version = %s
                      AND fetched_at >= (%s::timestamptz - (%s::text || ' seconds')::interval)
                    LIMIT 1
                    """,
                    (token_id, side_name, snapshot_version, fetched_at, str(unchanged_min_interval_seconds)),
                )
                if cur.fetchone():
                    return
            cur.execute(
                """
                INSERT INTO quant.clob_orderbook_snapshots (
                    token_id, side, paired_token_id,
                    market_id, condition_id, market_slug, market_title,
                    source, snapshot_source, storage_tier, book_generation, last_event_ts_ms, book_status,
                    block_number, snapshot_timestamp,
                    best_bid, best_ask, spread, mid,
                    bid_depth, ask_depth, depth_total, imbalance,
                    level_count_bid, level_count_ask, payload, snapshot_version, fetched_at
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s, %s
                )
                """,
                (
                    token_id,
                    side_name,
                    paired_token_id or None,
                    market_id,
                    condition_id or None,
                    market_slug or None,
                    market_title or None,
                    source or "local-orderbook",
                    snapshot_source or None,
                    storage_tier or "unknown",
                    book_generation,
                    last_event_ts_ms,
                    book_status or "unknown",
                    int(block_number) if block_number not in (None, "") else None,
                    fetched_at,
                    summary["bestBid"],
                    summary["bestAsk"],
                    summary["spread"],
                    summary["mid"],
                    summary["bidDepth"],
                    summary["askDepth"],
                    summary["depthTotal"],
                    summary["imbalance"],
                    summary["levelCountBid"],
                    summary["levelCountAsk"],
                    json.dumps(payload),
                    snapshot_version,
                    fetched_at,
                ),
            )


def _persist_orderbook_snapshots_sync(ctx: dict, payload: Dict[str, Any], yes_token_id: str, no_token_id: str) -> bool:
    try:
        fetched_at = str(payload.get("fetchedAt") or _iso_utc_now())
        source = str(payload.get("source") or "local-orderbook")
        book_status = str(payload.get("bookStatus") or ("ok" if _lob_payload_has_levels(payload) else "no-book"))
        market_title = str(payload.get("marketTitle") or "")
        block_number = payload.get("blockNumber") or payload.get("block_number")
        coverage_payload = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
        _persist_book_side_snapshot(
            ctx,
            token_id=yes_token_id,
            side_name="YES",
            paired_token_id=no_token_id,
            market_title=market_title,
            source=source,
            book_status=book_status,
            fetched_at=fetched_at,
            block_number=block_number,
            side_payload=payload.get("yes") or _empty_book_side(),
            coverage_payload=coverage_payload,
        )
        if no_token_id:
            _persist_book_side_snapshot(
                ctx,
                token_id=no_token_id,
                side_name="NO",
                paired_token_id=yes_token_id,
                market_title=market_title,
                source=source,
                book_status=book_status,
                fetched_at=fetched_at,
                block_number=block_number,
                side_payload=payload.get("no") or _empty_book_side(),
                coverage_payload=coverage_payload,
            )
        return True
    except Exception as exc:
        logger = ctx.get("app").logger if ctx.get("app") else None
        if logger:
            logger.warning("clob snapshot persistence failed token_id=%s: %s", yes_token_id, exc)
        return False


def _persist_orderbook_snapshots(ctx: dict, payload: Dict[str, Any], yes_token_id: str, no_token_id: str) -> bool:
    """Compatibility hook; API reads stay side-effect free unless explicitly enabled."""
    enabled = str(os.environ.get("POLYDATA_LOB_API_PERSIST_SNAPSHOTS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        return False
    return _persist_orderbook_snapshots_sync(ctx, payload, yes_token_id, no_token_id)


def persist_runtime_lob_payload(ctx: dict, payload: Dict[str, Any], yes_token_id: str, no_token_id: str) -> bool:
    return _persist_orderbook_snapshots_sync(ctx, payload, yes_token_id, no_token_id)


def write_lob_dead_letter(
    *,
    reason: str,
    raw_payload: Dict[str, Any] | list[Any] | str | None = None,
    token_id: str = "",
    market_id: int | None = None,
    condition_id: str = "",
    event_type: str = "",
    source: str = "local-orderbook",
    detail: str = "",
) -> bool:
    reason = str(reason or "").strip() or "unknown"
    try:
        _ensure_snapshot_schema()
        if isinstance(raw_payload, (dict, list)):
            payload_json = json.dumps(raw_payload)
        elif raw_payload is None:
            payload_json = "{}"
        else:
            payload_json = json.dumps({"raw": str(raw_payload)[:4000]})
        with postgres_connection(PostgresSettings()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO quant.clob_orderbook_dead_letters (
                        reason, token_id, market_id, condition_id, event_type, source, raw_payload, detail
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                    )
                    """,
                    (
                        reason,
                        str(token_id or "") or None,
                        market_id,
                        str(condition_id or "") or None,
                        str(event_type or "") or None,
                        str(source or "local-orderbook"),
                        payload_json,
                        str(detail or "")[:2000] or None,
                    ),
                )
        return True
    except Exception:
        return False


def get_lob_storage_status() -> Dict[str, Any]:
    maintenance: Dict[str, Any] = {}
    status_path = Path(
        os.environ.get(
            "POLYDATA_LOB_MAINTENANCE_STATUS_PATH",
            str(DEFAULT_LOB_MAINTENANCE_STATUS_PATH),
        )
    ).expanduser()
    try:
        if status_path.stat().st_size <= 1024 * 1024:
            decoded = json.loads(status_path.read_text(encoding="utf-8"))
            if isinstance(decoded, dict):
                maintenance = decoded
    except (OSError, ValueError, TypeError):
        maintenance = {}
    try:
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT set_config('statement_timeout', %s, true)
                    """,
                    (
                        str(
                            max(
                                250,
                                min(
                                    int(
                                        os.environ.get(
                                            "POLYDATA_LOB_STORAGE_HEALTH_TIMEOUT_MS",
                                            "2000",
                                        )
                                        or 2000
                                    ),
                                    10000,
                                ),
                            )
                        ),
                    ),
                )
                cur.execute(
                    """
                    SELECT fetched_at AS latest_raw_at
                    FROM quant.clob_orderbook_snapshots
                    ORDER BY snapshot_id DESC
                    LIMIT 1
                    """
                )
                snapshot_row = cur.fetchone() or {}
                cur.execute(
                    """
                    SELECT count(*) AS dead_letters_1h
                    FROM quant.clob_orderbook_dead_letters
                    WHERE created_at >= now() - interval '1 hour'
                    """
                )
                dead_row = cur.fetchone() or {}
        latest_raw_at = snapshot_row.get("latest_raw_at")
        rollup_watermark = maintenance.get("rollupWatermark")
        return {
            "status": "ok" if maintenance.get("status") in {None, "ok"} else "partial",
            "latestRawAt": latest_raw_at.isoformat().replace("+00:00", "Z") if latest_raw_at else None,
            "rawRows1h": None,
            "rollupWatermark": str(rollup_watermark) if rollup_watermark else None,
            "deadLetters1h": int(dead_row.get("dead_letters_1h") or 0),
            "maintenanceUpdatedAt": maintenance.get("updatedAt"),
            "maintenanceStatus": maintenance.get("status") or "unknown",
        }
    except Exception as exc:
        return {"status": "unavailable", "detail": str(exc)[:240]}


def get_lob_snapshots_by_token_payload(ctx: dict, token_id: str, *, side: str = "", limit: int = 48) -> Dict[str, Any]:
    token_id = str(token_id or "").strip()
    side = str(side or "").strip().upper()
    if not token_id:
        return {"error": "Missing token id", "items": [], "count": 0, "_status": 400}
    limit = max(1, min(int(limit or 48), 200))
    try:
        _ensure_snapshot_schema()
        params: list[Any] = [token_id]
        where = "token_id = %s"
        if side in {"YES", "NO"}:
            where += " AND side = %s"
            params.append(side)
        params.append(limit)
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        snapshot_id, token_id, side, paired_token_id, market_title, source, book_status,
                        block_number, snapshot_timestamp, snapshot_version,
                        best_bid, best_ask, spread, mid, bid_depth, ask_depth, depth_total, imbalance,
                        level_count_bid, level_count_ask, payload, fetched_at, created_at
                    FROM quant.clob_orderbook_snapshots
                    WHERE {where}
                    ORDER BY fetched_at DESC, snapshot_id DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
        items = []
        for row in rows:
            items.append({
                "snapshotId": row["snapshot_id"],
                "tokenId": row["token_id"],
                "side": row["side"],
                "pairedTokenId": row["paired_token_id"],
                "marketTitle": row["market_title"],
                "source": row["source"],
                "bookStatus": row["book_status"],
                "blockNumber": int(row["block_number"]) if row["block_number"] is not None else None,
                "snapshotTimestamp": row["snapshot_timestamp"].isoformat().replace("+00:00", "Z") if row["snapshot_timestamp"] else None,
                "snapshotVersion": row["snapshot_version"],
                "bestBid": _float_or_none(row["best_bid"]),
                "bestAsk": _float_or_none(row["best_ask"]),
                "spread": _float_or_none(row["spread"]),
                "mid": _float_or_none(row["mid"]),
                "bidDepth": _float_or_none(row["bid_depth"]),
                "askDepth": _float_or_none(row["ask_depth"]),
                "depthTotal": _float_or_none(row["depth_total"]),
                "imbalance": _float_or_none(row["imbalance"]),
                "levelCountBid": int(row["level_count_bid"] or 0),
                "levelCountAsk": int(row["level_count_ask"] or 0),
                "payload": row["payload"] if isinstance(row["payload"], dict) else {},
                "fetchedAt": row["fetched_at"].isoformat().replace("+00:00", "Z") if row["fetched_at"] else None,
                "createdAt": row["created_at"].isoformat().replace("+00:00", "Z") if row["created_at"] else None,
            })
        return {"tokenId": token_id, "side": side or None, "items": items, "count": len(items)}
    except Exception as exc:
        ctx["app"].logger.exception("lob snapshot history failed token_id=%s", token_id)
        return {"error": "LOB snapshot history unavailable", "detail": str(exc), "items": [], "count": 0, "_status": 502}


def get_lob_coverage_targets_payload(ctx: dict, *, limit: int = 250, topics: str | list[str] | tuple[str, ...] = "") -> Dict[str, Any]:
    limit = _int_clamped(limit, default=250, minimum=1, maximum=500)
    topic_list = _parse_coverage_topics(topics)
    try:
        rows = ctx["query_all"](
            """
            SELECT
                m.id AS market_id,
                m.slug AS market_slug,
                COALESCE(m.title, m.slug) AS market_title,
                m.category,
                m.tags,
                m.yes_token_id,
                m.no_token_id,
                m.event_slug,
                m.event_title,
                COALESCE(mls.volume_24h, 0) AS volume_24h,
                COALESCE(mls.trade_count_24h, 0) AS trade_count_24h,
                COALESCE(mls.last_trade_at, mls.latest_trade_at, m.migrated_at, m.created_at) AS activity_at
            FROM core.markets m
            LEFT JOIN core.market_list_serving mls ON mls.market_id = m.id
            LEFT JOIN core.market_status_snapshot mss ON mss.market_id = m.id
            WHERE COALESCE(m.yes_token_id, '') <> ''
              AND COALESCE(m.no_token_id, '') <> ''
              AND NOT COALESCE(mss.is_trading_closed, FALSE)
              AND NOT COALESCE(mss.is_resolved, FALSE)
              AND (
                LOWER(COALESCE(m.category, '')) SIMILAR TO '%%(worldcup|world cup|crypto|politic|election|geopolitic|sports)%%'
                OR LOWER(COALESCE(m.slug, '')) SIMILAR TO '%%(worldcup|world-cup|fifa|crypto|bitcoin|ethereum|election|trump|politic)%%'
                OR LOWER(COALESCE(m.title, '')) SIMILAR TO '%%(world cup|fifa|crypto|bitcoin|ethereum|election|trump|politic|president|senate|congress)%%'
                OR LOWER(COALESCE(m.event_slug, '')) SIMILAR TO '%%(worldcup|world-cup|fifa|crypto|bitcoin|ethereum|election|trump|politic)%%'
                OR LOWER(COALESCE(m.event_title, '')) SIMILAR TO '%%(world cup|fifa|crypto|bitcoin|ethereum|election|trump|politic|president)%%'
                OR LOWER(COALESCE(CAST(m.tags AS TEXT), '')) SIMILAR TO '%%(worldcup|world cup|fifa|crypto|bitcoin|ethereum|election|trump|politic|geopolitic)%%'
              )
            ORDER BY COALESCE(mls.volume_24h, 0) DESC, COALESCE(mls.trade_count_24h, 0) DESC, activity_at DESC NULLS LAST
            LIMIT ?
            """,
            (LOB_COVERAGE_RAW_ROW_LIMIT,),
        )
        worldcup_context, worldcup_context_payload = _build_worldcup_selection_context(ctx)
        worldcup_market_limit = _worldcup_lob_market_limit()
        targets = select_orderbook_coverage_targets(
            rows,
            global_limit=limit,
            topics=topic_list,
            topic_limits={"worldcup": worldcup_market_limit},
            context=worldcup_context,
        )
        policy_targets = [target.as_payload() for target in targets]
        dynamic_targets = get_dynamic_lob_warm_targets(ctx)
        seen_market_ids: set[int] = set()
        payload_targets: list[Dict[str, Any]] = []
        for item in [*dynamic_targets, *policy_targets]:
            market_id = _int_or_none(item.get("marketId") or item.get("market_id"))
            if market_id is None or market_id in seen_market_ids:
                continue
            seen_market_ids.add(market_id)
            payload_targets.append(item)
            if len(payload_targets) >= limit:
                break
        summary: Dict[str, Any] = {
            "topics": {topic: 0 for topic in [*PRIORITY_TOPICS, "dynamic"]},
            "tiers": {"hot": 0, "warm": 0, "cold": 0},
            "marketCount": len(payload_targets),
            "tokenCount": len(payload_targets) * 2,
        }
        for item in payload_targets:
            topic = str(item.get("topic") or "unknown")
            tier = str(item.get("tier") or "cold")
            summary["topics"][topic] = int(summary["topics"].get(topic, 0)) + 1
            summary["tiers"][tier] = int(summary["tiers"].get(tier, 0)) + 1
        return {
            "source": "local-orderbook-coverage-policy",
            "priorityTopics": topic_list or list(PRIORITY_TOPICS),
            "selectionContext": {
                "worldcup": worldcup_context_payload,
                "crypto": {"assets": ["BTC", "ETH"], "patterns": ["above", "hit"], "excluded": ["up-or-down", "5m", "15m"]},
                "dynamic": {
                    "recentSelectionCount": len(dynamic_targets),
                    "retentionSeconds": _int_clamped(
                        os.environ.get("POLYDATA_LOB_WARM_REQUEST_TTL_SECONDS"),
                        default=DEFAULT_LOB_WARM_REQUEST_TTL_SECONDS,
                        minimum=60,
                        maximum=3600,
                    ),
                },
            },
            "selectionLimits": {
                "worldcupMarketLimit": worldcup_market_limit,
            },
            "count": len(payload_targets),
            "summary": summary,
            "storagePolicy": {
                "hot": {"sampleIntervalSeconds": 15, "rawRetentionDays": 14},
                "warm": {"sampleIntervalSeconds": 60, "rawRetentionDays": 14},
                "cold": {"sampleIntervalSeconds": 300, "rawRetentionDays": 7},
                "unchangedSnapshotMinIntervalSeconds": _unchanged_snapshot_min_interval_seconds(),
            },
            "items": payload_targets,
        }
    except Exception as exc:
        ctx["app"].logger.exception("lob coverage target selection failed")
        return {"error": "LOB coverage targets unavailable", "detail": str(exc), "items": [], "count": 0, "_status": 502}


def _parse_coverage_topics(value: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = str(value or "").replace("|", ",").split(",")
    topics = []
    for item in raw_items:
        topic = str(item or "").strip().lower()
        if topic in PRIORITY_TOPICS and topic not in topics:
            topics.append(topic)
    return topics


def _worldcup_lob_market_limit() -> int:
    return _int_clamped(
        os.environ.get("POLYDATA_LOB_WORLDCUP_MARKET_LIMIT"),
        default=DEFAULT_WORLDCUP_LOB_MARKET_LIMIT,
        minimum=0,
        maximum=20,
    )


def _build_worldcup_selection_context(ctx: dict) -> tuple[CoverageSelectionContext, Dict[str, Any]]:
    getter = ctx.get("get_world_cup_match_ops_snapshot")
    if not callable(getter):
        return CoverageSelectionContext(), {"activeMatchCount": 0, "activeMarketIdCount": 0, "mode": "unavailable"}
    try:
        payload = getter(limit=48)
    except Exception as exc:
        logger = ctx.get("app").logger if ctx.get("app") else None
        if logger:
            logger.warning("worldcup match-ops unavailable for LOB coverage context: %s", exc)
        return CoverageSelectionContext(), {"activeMatchCount": 0, "activeMarketIdCount": 0, "mode": "error"}
    items = [item for item in (payload or {}).get("items") or [] if isinstance(item, dict)]
    active_items = [item for item in items if _is_active_worldcup_match_item(item)]
    market_ids: set[int] = set()
    terms: set[str] = set()
    slugs: set[str] = set()
    for item in active_items:
        for value in item.get("relatedPolymarketMarketIds") or []:
            parsed = _int_or_none(value)
            if parsed is not None:
                market_ids.add(parsed)
        for market in item.get("markets") or []:
            if not isinstance(market, dict):
                continue
            parsed = _int_or_none(market.get("marketId") or market.get("id"))
            if parsed is not None:
                market_ids.add(parsed)
            slug = str(market.get("slug") or "").strip().lower()
            if slug:
                slugs.add(slug)
        home = _coverage_term(item.get("homeTeam"))
        away = _coverage_term(item.get("awayTeam"))
        entity = _coverage_term(item.get("entity"))
        if entity:
            terms.add(entity)
        if home and away:
            terms.add(f"{home} vs {away}")
            terms.add(f"{away} vs {home}")
        slugs.update(_worldcup_fixture_slug_prefixes(item))
    return (
        CoverageSelectionContext(frozenset(market_ids), frozenset(terms), frozenset(slugs)),
        {
            "mode": "match-ops-active-window",
            "activeMatchCount": len(active_items),
            "activeMarketIdCount": len(market_ids),
            "activeTerms": sorted(terms)[:24],
            "activeSlugs": sorted(slugs)[:24],
            "windowMinutes": {
                "beforeKickoff": _worldcup_lob_pre_kickoff_minutes(),
                "fallbackAfterKickoff": _worldcup_lob_fallback_match_minutes(),
            },
        },
    )


def _is_active_worldcup_match_item(item: Dict[str, Any]) -> bool:
    status = str(item.get("matchStatus") or item.get("status") or "").strip().lower()
    if status in {"final", "complete", "completed", "ended", "closed", "resolved", "full-time", "fulltime", "ft"}:
        return False
    if status == "finished" and _worldcup_match_has_final_evidence(item):
        return False
    if status in {"in", "live", "in_progress", "in-progress", "halftime", "half-time"}:
        return True
    minutes = _int_or_none(item.get("minutesUntilKickoff"))
    if minutes is None:
        return False
    return -_worldcup_lob_fallback_match_minutes() <= minutes <= _worldcup_lob_pre_kickoff_minutes()


def _worldcup_match_has_final_evidence(item: Dict[str, Any]) -> bool:
    score = item.get("score") if isinstance(item.get("score"), dict) else {}
    if score.get("home") is not None and score.get("away") is not None:
        return True
    return bool(item.get("scoreSource") or item.get("scoreEventId") or item.get("scoreUpdatedAt"))


def _worldcup_lob_pre_kickoff_minutes() -> int:
    return _int_clamped(
        os.environ.get("POLYDATA_LOB_WORLDCUP_PRE_KICKOFF_MINUTES"),
        default=WORLDCUP_LOB_PRE_KICKOFF_MINUTES,
        minimum=0,
        maximum=240,
    )


def _worldcup_lob_fallback_match_minutes() -> int:
    return _int_clamped(
        os.environ.get("POLYDATA_LOB_WORLDCUP_FALLBACK_MATCH_MINUTES"),
        default=WORLDCUP_LOB_FALLBACK_MATCH_MINUTES,
        minimum=90,
        maximum=240,
    )


def _coverage_term(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _worldcup_fixture_slug_prefixes(item: Dict[str, Any]) -> set[str]:
    kickoff_date = str(item.get("kickoffUtc") or item.get("eventTime") or "")[:10]
    if not kickoff_date:
        return set()
    home_codes = _worldcup_team_slug_codes(item.get("homeTeam"))
    away_codes = _worldcup_team_slug_codes(item.get("awayTeam"))
    if not home_codes or not away_codes:
        return set()
    dates = {kickoff_date}
    with contextlib.suppress(Exception):
        previous = datetime.fromisoformat(kickoff_date).date() - timedelta(days=1)
        dates.add(previous.isoformat())
    return {
        f"fifwc-{left}-{right}-{date_value}"
        for date_value in dates
        for home_code in home_codes
        for away_code in away_codes
        for left, right in ((home_code, away_code), (away_code, home_code))
    }


def _worldcup_team_slug_codes(value: Any) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    normalized = text.replace("_", " ").replace("-", " ")
    normalized = " ".join(normalized.split())
    codes = WORLDCUP_TEAM_SLUG_CODES.get(normalized)
    if not codes:
        return set()
    if isinstance(codes, str):
        return {codes}
    return {str(code) for code in codes if str(code).strip()}


def _int_or_none(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _int_clamped(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _unavailable_lob_payload(market: Dict[str, Any], detail: str = "") -> Dict[str, Any]:
    return {
        "marketId": market.get("id"),
        "localMarketId": market.get("id"),
        "marketTitle": str(market.get("title") or ""),
        "fetchedAt": _iso_utc_now(),
        "source": "local-orderbook",
        "snapshotSource": "rest-book",
        "runtimeModel": "LocalOrderBook",
        "bookStatus": "unavailable",
        "detail": detail[:220],
        "yes": _empty_book_side(),
        "no": _empty_book_side(),
    }


def get_runtime_lob_by_token_payload(
    ctx: dict,
    token_id: str,
    *,
    no_token_id: str = "",
    market_title: str = "",
    market_id: int | None = None,
) -> Dict[str, Any]:
    yes_token_id = str(token_id or "").strip()
    no_token_id = str(no_token_id or "").strip()
    if not yes_token_id:
        return {"error": "Missing token id", "marketId": 0, "localMarketId": None, "_status": 400}
    enqueue_lob_warm_request(
        ctx,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        market_id=market_id,
        market_title=market_title,
    )
    cached_payload = _get_cached_runtime_lob_payload(ctx, yes_token_id)
    if cached_payload is not None:
        return cached_payload

    client = _redis_client(ctx)
    lock_key = _redis_runtime_key(ctx, f"{LOB_REDIS_CACHE_NAMESPACE}:bootstrap-lock:{yes_token_id}")
    lock_owner = uuid.uuid4().hex
    owns_lock = False
    if client is not None:
        try:
            owns_lock = bool(client.set(lock_key, lock_owner, nx=True, ex=8))
        except Exception:
            owns_lock = False
        if not owns_lock:
            deadline = time.monotonic() + 1.2
            while time.monotonic() < deadline:
                time.sleep(0.04)
                cached_payload = _get_cached_runtime_lob_payload(ctx, yes_token_id)
                if cached_payload is not None:
                    return cached_payload
            return {
                "marketId": int(market_id or 0),
                "localMarketId": int(market_id) if market_id is not None else None,
                "marketTitle": str(market_title or ""),
                "fetchedAt": _iso_utc_now(),
                "source": "local-orderbook",
                "snapshotSource": "singleflight-wait",
                "runtimeModel": "LocalOrderBook",
                "bookStatus": "warming",
                "detail": "Another worker is preparing this token; retry the focus tile shortly.",
                "yes": _empty_book_side(),
                "no": _empty_book_side(),
            }
    try:
        payload = ctx["LOB_RUNTIME_MANAGER"].get_token_pair_snapshot(
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            market_title=str(market_title or ""),
        )
        if market_id is not None:
            payload.setdefault("marketId", int(market_id))
            payload.setdefault("localMarketId", int(market_id))
        cache_runtime_lob_payload(ctx, payload, yes_token_id, no_token_id)
        _persist_orderbook_snapshots(ctx, payload, yes_token_id, no_token_id)
        return payload
    except Exception as exc:
        ctx["app"].logger.exception("local-orderbook token snapshot failed token_id=%s", yes_token_id)
        return {
            "error": "LOB token snapshot unavailable",
            "marketId": 0,
            "localMarketId": None,
            "detail": str(exc),
            "_status": 502,
        }
    finally:
        if client is not None and owns_lock:
            try:
                client.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    lock_key,
                    lock_owner,
                )
            except Exception:
                pass


def get_runtime_lob_payload(ctx: dict, market_id: int) -> Dict[str, Any]:
    market = ctx["get_market_by_id"](market_id)
    if not market:
        return {"error": "Market not found", "marketId": market_id, "localMarketId": market_id, "_status": 404}
    yes_token_id = str(market.get("yes_token_id") or "").strip()
    no_token_id = str(market.get("no_token_id") or "").strip()
    if not yes_token_id or not no_token_id:
        return {"error": "Market is missing token ids", "marketId": market_id, "localMarketId": market_id, "_status": 409}
    enqueue_lob_warm_request(
        ctx,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        market_id=market_id,
        market_title=str(market.get("title") or ""),
    )
    cached_payload = _get_cached_runtime_lob_payload(ctx, yes_token_id)
    if cached_payload is not None:
        cached_payload.setdefault("marketId", market_id)
        cached_payload.setdefault("localMarketId", market_id)
        return cached_payload
    try:
        runtime_payload = ctx["LOB_RUNTIME_MANAGER"].get_market_snapshot(
            market_id=market_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            market_title=str(market.get("title") or ""),
            condition_id=str(market.get("condition_id") or market.get("conditionId") or ""),
            market_slug=str(market.get("slug") or market.get("market_slug") or "") or None,
        )
        if isinstance(runtime_payload, dict):
            runtime_payload.setdefault("localMarketId", market_id)
            runtime_payload.setdefault("source", "local-orderbook")
            runtime_payload.setdefault("snapshotSource", "rest-book")
            runtime_payload.setdefault("runtimeModel", "LocalOrderBook")
            runtime_payload.setdefault("bookStatus", "ok" if _lob_payload_has_levels(runtime_payload) else "no-book")
        cache_runtime_lob_payload(ctx, runtime_payload, yes_token_id, no_token_id)
        _persist_orderbook_snapshots(ctx, runtime_payload, yes_token_id, no_token_id)
        return runtime_payload
    except Exception as exc:
        ctx["app"].logger.warning("local-orderbook failed market_id=%s: %s", market_id, exc)
        if isinstance(exc, RequestException):
            return _unavailable_lob_payload(market, str(exc))
        return {
            "error": "LOB runtime unavailable",
            "marketId": market_id,
            "localMarketId": market_id,
            "detail": str(exc),
            "_status": 502,
        }
