#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_scripts_root = Path(__file__).resolve().parents[1]
_project_root = _scripts_root.parent
for _path in (str(_project_root), str(_scripts_root)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    import fcntl
except ImportError:  # pragma: no cover - non-posix fallback.
    fcntl = None

try:
    import websockets
except ImportError:  # pragma: no cover - dependency guard.
    websockets = None

from api.services import lob_service
from data_sources import POLYMARKET_CLOB_WS_URL
from quant.orderbook import OrderBookNotReady, OrderBookOutOfOrder, TokenBookIdentity, normalize_polymarket_event
from quant.orderbook.clickhouse_sink import ClickHouseLobSink, LobClickHouseSettings
from quant.orderbook.polymarket_adapter import NormalizedBookDelta, NormalizedBookSnapshot


DEFAULT_COVERAGE_LIMIT = 250
DEFAULT_COVERAGE_TOPICS = "worldcup,crypto,politics"
DEFAULT_COVERAGE_REFRESH_SECONDS = 60
DEFAULT_RECONNECT_SECONDS = 5
DEFAULT_SUBSCRIPTION_BATCH_SIZE = 80
DEFAULT_SUBSCRIPTION_BATCH_PAUSE_SECONDS = 0.2
DEFAULT_FALLBACK_SAMPLE_INTERVAL_SECONDS = 60
DEFAULT_BOOTSTRAP_MARKET_LIMIT = 6
DEFAULT_LOCK_NAME = "local-orderbook-websocket.worker.lock"
DEFAULT_DRIFT_CHECK_INTERVAL_SECONDS = 60
DEFAULT_DRIFT_CHECK_MAX_PER_TICK = 3
DEFAULT_PING_INTERVAL_SECONDS = 10
DEFAULT_RECENT_EVENT_SAMPLE_LIMIT = 12
DEFAULT_STATUS_FILE_WRITE_INTERVAL_SECONDS = 2.0
DEFAULT_SNAPSHOT_PERSIST_QUEUE_MAX_SIZE = 500
DEFAULT_SNAPSHOT_PERSIST_DRAIN_SECONDS = 5.0
DEFAULT_SNAPSHOT_PERSIST_FAILURE_BACKOFF_SECONDS = 60
DRIFT_CHECK_SECONDS_BY_TIER = {"hot": 900, "warm": 1800, "cold": 3600}
STALE_IDLE_SECONDS_BY_TIER = {"hot": 120, "warm": 300, "cold": 900}
KNOWN_MARKET_EVENT_TYPES = {
    "book",
    "price_change",
    "last_trade_price",
    "tick_size_change",
    "best_bid_ask",
    "new_market",
    "market_resolved",
}


_STATUS_LOCK = threading.Lock()
_STATUS_FILE_LAST_WRITE_AT = 0.0
_RUNTIME_STATUS: dict[str, Any] = {
    "status": "disabled",
    "mode": "local-orderbook",
    "targetCount": 0,
    "subscribedTokenCount": 0,
    "lastMessageAt": None,
    "lastPersistAt": None,
    "lastCoverageRefreshAt": None,
    "lastConnectAt": None,
    "reconnectCount": 0,
    "staleCount": 0,
    "deadLetterCount": 0,
    "driftMismatchCount": 0,
    "rawMessageCount": 0,
    "pingCount": 0,
    "pongCount": 0,
    "lastPingAt": None,
    "lastPongAt": None,
    "bookEventCount": 0,
    "priceChangeEventCount": 0,
    "lastTradePriceEventCount": 0,
    "bestBidAskEventCount": 0,
    "tickSizeChangeEventCount": 0,
    "newMarketEventCount": 0,
    "marketResolvedEventCount": 0,
    "unknownEventTypeCount": 0,
    "unparsedEventCount": 0,
    "eventTypeCounts": {},
    "recentEventSamples": [],
    "normalizedEventCount": 0,
    "normalizedDeltaCount": 0,
    "normalizedSnapshotCount": 0,
    "unknownTokenCount": 0,
    "stateApplyCount": 0,
    "stateApplyFailureCount": 0,
    "clickhouseEnabled": False,
    "clickhouseRowsInserted": 0,
    "clickhouseBufferedRows": 0,
    "clickhouse": {
        "enabled": False,
        "rowsInserted": 0,
        "bufferedRows": 0,
    },
    "snapshotPersistQueuedCount": 0,
    "snapshotPersistWrittenCount": 0,
    "snapshotPersistDropCount": 0,
    "snapshotPersistFailureCount": 0,
    "snapshotPersistCoalescedCount": 0,
    "snapshotPersistPendingCount": 0,
    "snapshotPersistBackoffUntil": None,
    "snapshotPersistBackoffSeconds": 0,
    "snapshotPersistLastFailureAt": None,
    "lastPersistQueuedAt": None,
}


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _update_runtime_status(**updates: Any) -> None:
    with _STATUS_LOCK:
        updates.setdefault("statusUpdatedAt", _utc_now_iso())
        _RUNTIME_STATUS.update(updates)
        _write_runtime_status_file_if_due_unlocked()


def _increment_runtime_status(key: str, amount: int = 1) -> None:
    with _STATUS_LOCK:
        _RUNTIME_STATUS[key] = int(_RUNTIME_STATUS.get(key) or 0) + int(amount)
        _RUNTIME_STATUS["statusUpdatedAt"] = _utc_now_iso()
        _write_runtime_status_file_if_due_unlocked()


def _record_event_diagnostic(event: dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or event.get("type") or "unknown").strip() or "unknown"
    sample = _event_sample(event, event_type=event_type)
    with _STATUS_LOCK:
        counts = dict(_RUNTIME_STATUS.get("eventTypeCounts") or {})
        counts[event_type] = int(counts.get(event_type) or 0) + 1
        recent = list(_RUNTIME_STATUS.get("recentEventSamples") or [])
        recent.append(sample)
        _RUNTIME_STATUS["eventTypeCounts"] = counts
        _RUNTIME_STATUS["recentEventSamples"] = recent[-DEFAULT_RECENT_EVENT_SAMPLE_LIMIT:]
        _RUNTIME_STATUS["statusUpdatedAt"] = _utc_now_iso()
        _write_runtime_status_file_if_due_unlocked()


def get_runtime_status() -> dict[str, Any]:
    with _STATUS_LOCK:
        local_status = dict(_RUNTIME_STATUS)
    file_status = _read_runtime_status_file()
    if not file_status:
        return local_status
    local_updated_at = str(local_status.get("statusUpdatedAt") or "")
    file_updated_at = str(file_status.get("statusUpdatedAt") or "")
    if file_updated_at >= local_updated_at or str(local_status.get("status") or "") in {"disabled", "ready"}:
        return file_status
    return local_status


def _runtime_status_path() -> Path | None:
    raw = str(os.environ.get("POLYDATA_LOB_RUNTIME_STATUS_PATH") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _write_runtime_status_file_if_due_unlocked(*, force: bool = False) -> None:
    global _STATUS_FILE_LAST_WRITE_AT
    path = _runtime_status_path()
    if path is None:
        return
    if not _env_bool("POLYDATA_LOB_WEBSOCKET_ENABLED", False):
        return
    now = time.monotonic()
    interval = _env_float(
        "POLYDATA_LOB_RUNTIME_STATUS_WRITE_INTERVAL_SECONDS",
        DEFAULT_STATUS_FILE_WRITE_INTERVAL_SECONDS,
        minimum=0.1,
    )
    if not force and now - _STATUS_FILE_LAST_WRITE_AT < interval:
        return
    _STATUS_FILE_LAST_WRITE_AT = now
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(_RUNTIME_STATUS)
        payload["statusFileWrittenAt"] = _utc_now_iso()
        tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        return


def _read_runtime_status_file() -> dict[str, Any] | None:
    path = _runtime_status_path()
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        payload["statusFileAgeSeconds"] = max(0, int(time.time() - path.stat().st_mtime))
    except Exception:
        pass
    return payload


@dataclass(frozen=True)
class CoverageTarget:
    market_id: int
    market_slug: str
    market_title: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    topic: str
    tier: str
    sample_interval_seconds: int
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> "CoverageTarget | None":
        market_id = _int_or_none(item.get("marketId") or item.get("market_id"))
        yes_token_id = str(item.get("yesTokenId") or item.get("yes_token_id") or "").strip()
        no_token_id = str(item.get("noTokenId") or item.get("no_token_id") or "").strip()
        if market_id is None or not yes_token_id or not no_token_id:
            return None
        return cls(
            market_id=market_id,
            market_slug=str(item.get("marketSlug") or item.get("market_slug") or ""),
            market_title=str(item.get("marketTitle") or item.get("market_title") or ""),
            condition_id=str(item.get("conditionId") or item.get("condition_id") or ""),
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            topic=str(item.get("topic") or ""),
            tier=str(item.get("tier") or ""),
            sample_interval_seconds=max(1, int(_int_or_none(item.get("sampleIntervalSeconds")) or DEFAULT_FALLBACK_SAMPLE_INTERVAL_SECONDS)),
            raw=dict(item),
        )

    def identities(self) -> tuple[TokenBookIdentity, TokenBookIdentity]:
        return (
            TokenBookIdentity(
                token_id=self.yes_token_id,
                market_id=self.market_id,
                condition_id=self.condition_id,
                outcome="YES",
                outcome_index=0,
                market_slug=self.market_slug or None,
            ),
            TokenBookIdentity(
                token_id=self.no_token_id,
                market_id=self.market_id,
                condition_id=self.condition_id,
                outcome="NO",
                outcome_index=1,
                market_slug=self.market_slug or None,
            ),
        )

    @property
    def token_ids(self) -> tuple[str, str]:
        return self.yes_token_id, self.no_token_id


class _Logger:
    def info(self, message: str, *args: Any) -> None:
        print(f"[local-orderbook-ws] INFO {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any) -> None:
        print(f"[local-orderbook-ws] WARN {message % args if args else message}", file=sys.stderr)

    def exception(self, message: str, *args: Any) -> None:
        print(f"[local-orderbook-ws] ERROR {message % args if args else message}", file=sys.stderr)


@dataclass(frozen=True)
class _SnapshotPersistJob:
    payload: dict[str, Any]
    yes_token_id: str
    no_token_id: str


class _SnapshotPersistWorker:
    def __init__(self, *, ctx: dict[str, Any], logger: Any, max_queue_size: int | None = None) -> None:
        self.ctx = ctx
        self.logger = logger
        self.queue: queue.Queue[str | None] = queue.Queue(
            maxsize=max(1, int(max_queue_size or _env_int("POLYDATA_LOB_SNAPSHOT_PERSIST_QUEUE_MAX_SIZE", DEFAULT_SNAPSHOT_PERSIST_QUEUE_MAX_SIZE, minimum=1)))
        )
        self._lock = threading.Lock()
        self._latest_by_key: dict[str, _SnapshotPersistJob] = {}
        self._backoff_until = 0.0
        self._thread = threading.Thread(target=self._run, name="local-orderbook-snapshot-persist", daemon=True)
        self._thread.start()
        _update_runtime_status(snapshotPersistQueueMaxSize=self.queue.maxsize, snapshotPersistPendingCount=0)

    def enqueue(self, payload: dict[str, Any], yes_token_id: str, no_token_id: str) -> bool:
        job = _SnapshotPersistJob(dict(payload), str(yes_token_id or ""), str(no_token_id or ""))
        key = _snapshot_persist_key(job.payload, job.yes_token_id, job.no_token_id)
        with self._lock:
            already_pending = key in self._latest_by_key
            self._latest_by_key[key] = job
            if already_pending:
                _increment_runtime_status("snapshotPersistCoalescedCount")
                _update_runtime_status(lastPersistQueuedAt=_utc_now_iso(), snapshotPersistPendingCount=len(self._latest_by_key))
                return True
            try:
                self.queue.put_nowait(key)
            except queue.Full:
                self._latest_by_key.pop(key, None)
                _increment_runtime_status("snapshotPersistDropCount")
                _update_runtime_status(snapshotPersistPendingCount=len(self._latest_by_key))
                return False
        _increment_runtime_status("snapshotPersistQueuedCount")
        _update_runtime_status(lastPersistQueuedAt=_utc_now_iso(), snapshotPersistPendingCount=self.pending_count())
        return True

    def drain(self, *, timeout_seconds: float = DEFAULT_SNAPSHOT_PERSIST_DRAIN_SECONDS) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while self.pending_count() > 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        _update_runtime_status(snapshotPersistPendingCount=self.pending_count())
        return self.pending_count() <= 0

    def pending_count(self) -> int:
        with self._lock:
            return len(self._latest_by_key)

    def _run(self) -> None:
        while True:
            key = self.queue.get()
            try:
                if key is None:
                    return
                self._sleep_for_backoff_if_needed()
                with self._lock:
                    job = self._latest_by_key.pop(key, None)
                if job is None:
                    continue
                try:
                    persisted = lob_service.persist_runtime_lob_payload(self.ctx, job.payload, job.yes_token_id, job.no_token_id)
                    if persisted is False:
                        raise RuntimeError("snapshot persistence returned false")
                    _increment_runtime_status("snapshotPersistWrittenCount")
                    _update_runtime_status(
                        lastPersistAt=_utc_now_iso(),
                        snapshotPersistPendingCount=self.pending_count(),
                        snapshotPersistBackoffUntil=None,
                        snapshotPersistBackoffSeconds=0,
                    )
                except Exception as exc:
                    _increment_runtime_status("snapshotPersistFailureCount")
                    self._start_failure_backoff()
                    _update_runtime_status(snapshotPersistPendingCount=self.pending_count(), snapshotPersistLastFailureAt=_utc_now_iso())
                    if self.logger:
                        self.logger.warning("snapshot persist worker failed token_id=%s error=%s", job.yes_token_id, exc)
            finally:
                self.queue.task_done()

    def _sleep_for_backoff_if_needed(self) -> None:
        remaining = self._backoff_until - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, _env_float("POLYDATA_LOB_SNAPSHOT_PERSIST_FAILURE_BACKOFF_SECONDS", DEFAULT_SNAPSHOT_PERSIST_FAILURE_BACKOFF_SECONDS, minimum=1.0)))

    def _start_failure_backoff(self) -> None:
        seconds = _env_float("POLYDATA_LOB_SNAPSHOT_PERSIST_FAILURE_BACKOFF_SECONDS", DEFAULT_SNAPSHOT_PERSIST_FAILURE_BACKOFF_SECONDS, minimum=1.0)
        self._backoff_until = time.monotonic() + seconds
        _update_runtime_status(
            snapshotPersistBackoffUntil=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds)),
            snapshotPersistBackoffSeconds=seconds,
        )


class LocalOrderBookWebsocketWatcher:
    def __init__(
        self,
        *,
        ctx: dict[str, Any],
        ws_url: str,
        limit: int = DEFAULT_COVERAGE_LIMIT,
        topics: str = DEFAULT_COVERAGE_TOPICS,
        coverage_refresh_seconds: int = DEFAULT_COVERAGE_REFRESH_SECONDS,
        subscription_batch_size: int = DEFAULT_SUBSCRIPTION_BATCH_SIZE,
        subscription_batch_pause_seconds: float = DEFAULT_SUBSCRIPTION_BATCH_PAUSE_SECONDS,
        bootstrap_market_limit: int = DEFAULT_BOOTSTRAP_MARKET_LIMIT,
        persist: bool = True,
        bootstrap: bool = True,
        logger: Any | None = None,
    ) -> None:
        if websockets is None:
            raise RuntimeError("websockets package is required. Install scripts/requirements.txt")
        self.ctx = ctx
        self.manager = ctx["LOB_RUNTIME_MANAGER"]
        self.ws_url = str(ws_url or "").strip()
        if not self.ws_url:
            raise RuntimeError("POLYDATA_CLOB_WS_URL is required for LocalOrderBook websocket watcher")
        self.limit = max(1, min(int(limit or DEFAULT_COVERAGE_LIMIT), 500))
        self.topics = str(topics or DEFAULT_COVERAGE_TOPICS)
        self.coverage_refresh_seconds = max(30, int(coverage_refresh_seconds or DEFAULT_COVERAGE_REFRESH_SECONDS))
        self.subscription_batch_size = max(1, min(int(subscription_batch_size or DEFAULT_SUBSCRIPTION_BATCH_SIZE), 500))
        self.subscription_batch_pause_seconds = max(0.0, float(subscription_batch_pause_seconds if subscription_batch_pause_seconds is not None else DEFAULT_SUBSCRIPTION_BATCH_PAUSE_SECONDS))
        self.bootstrap_market_limit = max(0, int(bootstrap_market_limit if bootstrap_market_limit is not None else DEFAULT_BOOTSTRAP_MARKET_LIMIT))
        self.persist = bool(persist)
        self.bootstrap = bool(bootstrap)
        self.logger = logger or _Logger()
        self.targets_by_market: dict[int, CoverageTarget] = {}
        self.identities_by_token: dict[str, TokenBookIdentity] = {}
        self.target_by_token: dict[str, CoverageTarget] = {}
        self.subscription_token_order: list[str] = []
        self.subscribed_tokens: set[str] = set()
        self._last_persisted_at_by_market: dict[int, float] = {}
        self._last_drift_check_at_by_market: dict[int, float] = {}
        self._last_idle_check_at = 0.0
        self.clickhouse_sink = self._build_clickhouse_sink()
        self.snapshot_persistor = _SnapshotPersistWorker(ctx=ctx, logger=self.logger) if self.persist else None
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def refresh_targets(self) -> list[CoverageTarget]:
        payload = lob_service.get_lob_coverage_targets_payload(self.ctx, limit=self.limit, topics=self.topics)
        if payload.get("_status") or payload.get("error"):
            raise RuntimeError(str(payload.get("detail") or payload.get("error") or "coverage target selection failed"))
        targets = [
            target
            for target in (CoverageTarget.from_payload(item) for item in (payload.get("items") or []))
            if target is not None
        ]
        self.targets_by_market = {target.market_id: target for target in targets}
        self.identities_by_token = {}
        self.target_by_token = {}
        token_order: list[str] = []
        for target in targets:
            yes_identity, no_identity = target.identities()
            self.identities_by_token[yes_identity.token_id] = yes_identity
            self.identities_by_token[no_identity.token_id] = no_identity
            self.target_by_token[yes_identity.token_id] = target
            self.target_by_token[no_identity.token_id] = target
            token_order.extend([yes_identity.token_id, no_identity.token_id])
        self.subscription_token_order = _dedupe_preserve_order(token_order)
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        self.logger.info("coverage refreshed markets=%s tokens=%s topics=%s", len(targets), len(self.identities_by_token), summary.get("topics"))
        _update_runtime_status(
            status="coverage_ready",
            targetCount=len(targets),
            subscribedTokenCount=len(self.subscribed_tokens),
            coverageTopics=self.topics,
            coverageSummary=summary,
            lastCoverageRefreshAt=_utc_now_iso(),
        )
        return targets

    def add_recent_selection_targets(self) -> list[CoverageTarget]:
        """Admit recently selected markets without waiting for the full DB coverage query."""
        targets = [
            target
            for target in (
                CoverageTarget.from_payload(item)
                for item in lob_service.get_dynamic_lob_warm_targets(self.ctx)
            )
            if target is not None and target.market_id not in self.targets_by_market
        ]
        added: list[CoverageTarget] = []
        for target in targets:
            self.targets_by_market[target.market_id] = target
            yes_identity, no_identity = target.identities()
            for identity in (yes_identity, no_identity):
                self.identities_by_token[identity.token_id] = identity
                self.target_by_token[identity.token_id] = target
                if identity.token_id not in self.subscription_token_order:
                    self.subscription_token_order.append(identity.token_id)
            added.append(target)
        if added:
            self.logger.info("dynamic coverage admitted markets=%s", len(added))
            _update_runtime_status(
                targetCount=len(self.targets_by_market),
                lastDynamicCoverageRefreshAt=_utc_now_iso(),
            )
        return added

    def bootstrap_targets(self, targets: Iterable[CoverageTarget] | None = None, *, force_refresh: bool = False) -> int:
        if not self.bootstrap:
            return 0
        count = 0
        for target in targets if targets is not None else self.targets_by_market.values():
            try:
                payload = self.manager.get_market_snapshot(
                    market_id=target.market_id,
                    yes_token_id=target.yes_token_id,
                    no_token_id=target.no_token_id,
                    market_title=target.market_title,
                    condition_id=target.condition_id,
                    market_slug=target.market_slug or None,
                    force_refresh=force_refresh,
                )
                self.publish_target_cache(target, payload=payload, reason="bootstrap")
                count += 1
                self.persist_target_if_due(target, force=True, reason="bootstrap")
            except Exception as exc:
                self.logger.warning("bootstrap failed market_id=%s title=%s error=%s", target.market_id, target.market_title[:80], exc)
                self.write_dead_letter(
                    "bootstrap_failed",
                    raw_payload=target.raw,
                    token_id=target.yes_token_id,
                    market_id=target.market_id,
                    condition_id=target.condition_id,
                    event_type="bootstrap",
                    detail=str(exc),
                )
        if count:
            self.logger.info("bootstrap complete markets=%s", count)
        return count

    async def run_forever(self) -> None:
        reconnect_seconds = max(1, int(os.environ.get("POLYDATA_LOB_WS_RECONNECT_SECONDS", DEFAULT_RECONNECT_SECONDS) or DEFAULT_RECONNECT_SECONDS))
        while not self._stop_event.is_set():
            try:
                await self.run_connection()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning("websocket connection failed: %s", exc)
                _increment_runtime_status("reconnectCount")
                _update_runtime_status(status="reconnecting")
                await asyncio.sleep(reconnect_seconds)

    async def run_connection(self, *, run_seconds: int | None = None) -> None:
        if self.subscribed_tokens or self.manager.runtime_book_counts().get("bookCount"):
            self.mark_registry_stale("websocket_reconnect")
        targets = self.refresh_targets()
        self.bootstrap_targets(targets[: self.bootstrap_market_limit] if self.bootstrap_market_limit else [], force_refresh=True)
        deadline = time.monotonic() + run_seconds if run_seconds and run_seconds > 0 else None
        last_refresh_at = time.monotonic()
        last_dynamic_refresh_at = 0.0
        last_drift_tick = time.monotonic()
        last_ping_at = 0.0
        ping_interval_seconds = _env_int("POLYDATA_LOB_WS_PING_INTERVAL_SECONDS", DEFAULT_PING_INTERVAL_SECONDS, minimum=0)
        self.logger.info("connecting ws_url=%s target_tokens=%s batch_size=%s", self.ws_url, len(self.subscription_token_order), self.subscription_batch_size)
        async with websockets.connect(self.ws_url, ping_interval=None, close_timeout=10, open_timeout=15, max_queue=1000) as websocket:
            self.logger.info("websocket opened")
            await self.subscribe(websocket, list(self.subscription_token_order), replace=True)
            self.logger.info("connected subscribed_tokens=%s", len(self.subscribed_tokens))
            _update_runtime_status(status="connected", subscribedTokenCount=len(self.subscribed_tokens), lastConnectAt=_utc_now_iso())
            while not self._stop_event.is_set():
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    break
                if now - last_dynamic_refresh_at >= 5:
                    dynamic_targets = self.add_recent_selection_targets()
                    if dynamic_targets:
                        self.bootstrap_targets(dynamic_targets, force_refresh=True)
                        dynamic_token_ids = [
                            token_id
                            for target in dynamic_targets
                            for token_id in target.token_ids
                            if token_id not in self.subscribed_tokens
                        ]
                        if dynamic_token_ids:
                            await self.subscribe(websocket, dynamic_token_ids, replace=False)
                    last_dynamic_refresh_at = now
                if now - last_refresh_at >= self.coverage_refresh_seconds:
                    await self.reconcile_subscriptions(websocket)
                    last_refresh_at = now
                if now - last_drift_tick >= _env_int("POLYDATA_LOB_DRIFT_CHECK_INTERVAL_SECONDS", DEFAULT_DRIFT_CHECK_INTERVAL_SECONDS, minimum=10):
                    self.run_periodic_checks()
                    last_drift_tick = now
                last_ping_at = await self.send_heartbeat_if_due(
                    websocket,
                    now=now,
                    last_ping_at=last_ping_at,
                    interval_seconds=ping_interval_seconds,
                )
                timeout = 5.0
                if deadline is not None:
                    timeout = max(0.1, min(timeout, deadline - now))
                try:
                    raw_message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                await self.handle_raw_message(str(raw_message))
        self.flush_clickhouse_sink(force=True)
        self.drain_snapshot_persistor()

    async def send_heartbeat_if_due(self, websocket: Any, *, now: float, last_ping_at: float, interval_seconds: int) -> float:
        if interval_seconds <= 0:
            return last_ping_at
        if now - float(last_ping_at or 0.0) < interval_seconds:
            return last_ping_at
        await websocket.send("PING")
        _increment_runtime_status("pingCount")
        _update_runtime_status(lastPingAt=_utc_now_iso())
        return now

    async def reconcile_subscriptions(self, websocket: Any) -> None:
        previous = set(self.subscribed_tokens)
        targets = self.refresh_targets()
        desired = set(self.identities_by_token)
        added_targets = [target for target in targets if any(token_id not in previous for token_id in target.token_ids)]
        if added_targets:
            self.bootstrap_targets(added_targets, force_refresh=True)
        to_subscribe = [token_id for token_id in self.subscription_token_order if token_id not in previous]
        to_unsubscribe = sorted(previous - desired)
        if to_subscribe:
            await self.subscribe(websocket, to_subscribe, replace=False)
        if to_unsubscribe:
            await self.unsubscribe(websocket, to_unsubscribe)
        if to_subscribe or to_unsubscribe:
            self.logger.info("reconciled subscribe_add=%s subscribe_remove=%s", len(to_subscribe), len(to_unsubscribe))

    async def subscribe(self, websocket: Any, token_ids: list[str], *, replace: bool) -> None:
        if not token_ids:
            return
        batches = list(_chunked(token_ids, self.subscription_batch_size))
        for index, batch in enumerate(batches):
            if replace and index == 0:
                payload = {"type": "market", "custom_feature_enabled": True, "assets_ids": batch}
            else:
                payload = {"operation": "subscribe", "custom_feature_enabled": True, "assets_ids": batch}
            await websocket.send(json.dumps(payload, ensure_ascii=True))
            self.subscribed_tokens.update(batch)
            _update_runtime_status(
                subscribedTokenCount=len(self.subscribed_tokens),
                subscriptionBatchSize=self.subscription_batch_size,
                subscriptionBatchCount=index + 1,
                subscriptionBatchPauseSeconds=self.subscription_batch_pause_seconds,
            )
            if self.subscription_batch_pause_seconds and index < len(batches) - 1:
                await asyncio.sleep(self.subscription_batch_pause_seconds)
        _update_runtime_status(subscribedTokenCount=len(self.subscribed_tokens))

    async def unsubscribe(self, websocket: Any, token_ids: list[str]) -> None:
        for batch in _chunked(token_ids, self.subscription_batch_size):
            await websocket.send(json.dumps({"operation": "unsubscribe", "assets_ids": batch}, ensure_ascii=True))
            for token_id in batch:
                self.subscribed_tokens.discard(token_id)
        _update_runtime_status(subscribedTokenCount=len(self.subscribed_tokens))

    async def handle_raw_message(self, raw_message: str) -> None:
        if raw_message == "PONG":
            _increment_runtime_status("pongCount")
            _update_runtime_status(lastPongAt=_utc_now_iso())
            return
        if raw_message in {"PING", ""}:
            return
        _increment_runtime_status("rawMessageCount")
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            self.logger.warning("ignoring non-json websocket message=%s", raw_message[:160])
            self.write_dead_letter("invalid_json", raw_payload=raw_message, event_type="websocket_message")
            return
        _update_runtime_status(lastMessageAt=_utc_now_iso(), status="connected")
        for event in _iter_json_events(payload):
            self.handle_event(event)

    def handle_event(self, event: dict[str, Any]) -> int:
        changed_markets: set[int] = set()
        event_type = str(event.get("event_type") or event.get("type") or "")
        _record_event_diagnostic(event)
        normalized_events = list(normalize_polymarket_event(event))
        if event_type == "book":
            _increment_runtime_status("bookEventCount")
        elif event_type == "price_change":
            _increment_runtime_status("priceChangeEventCount")
        elif event_type == "last_trade_price":
            _increment_runtime_status("lastTradePriceEventCount")
        elif event_type == "best_bid_ask":
            _increment_runtime_status("bestBidAskEventCount")
        elif event_type == "tick_size_change":
            _increment_runtime_status("tickSizeChangeEventCount")
        elif event_type == "new_market":
            _increment_runtime_status("newMarketEventCount")
        elif event_type == "market_resolved":
            _increment_runtime_status("marketResolvedEventCount")
        elif event_type not in KNOWN_MARKET_EVENT_TYPES:
            _increment_runtime_status("unknownEventTypeCount")
        if normalized_events:
            _increment_runtime_status("normalizedEventCount", len(normalized_events))
            _increment_runtime_status("normalizedDeltaCount", sum(1 for item in normalized_events if isinstance(item, NormalizedBookDelta)))
            _increment_runtime_status("normalizedSnapshotCount", sum(1 for item in normalized_events if isinstance(item, NormalizedBookSnapshot)))
        if not normalized_events and event_type in {"book", "price_change"}:
            _increment_runtime_status("unparsedEventCount")
            self.write_dead_letter("unparsed_event", raw_payload=event, event_type=event_type)
        for normalized in normalized_events:
            identity = self.identities_by_token.get(normalized.token_id)
            if identity is None:
                _increment_runtime_status("unknownTokenCount")
                self.write_dead_letter("unknown_token", raw_payload=event, token_id=normalized.token_id, event_type=event_type)
                continue
            target = self.target_by_token.get(normalized.token_id)
            try:
                state_payload = self.manager.apply_normalized_event(identity, normalized)
                _increment_runtime_status("stateApplyCount")
                self.enqueue_clickhouse_event(identity=identity, target=target, event=normalized, state_payload=state_payload)
            except (OrderBookNotReady, OrderBookOutOfOrder) as exc:
                _increment_runtime_status("stateApplyFailureCount")
                self.write_dead_letter(
                    "price_change_before_ready" if isinstance(exc, OrderBookNotReady) else "out_of_order_resnapshot",
                    raw_payload=event,
                    token_id=normalized.token_id,
                    market_id=target.market_id if target else identity.market_id,
                    condition_id=target.condition_id if target else identity.condition_id,
                    event_type=event_type,
                )
                if target is not None:
                    self.bootstrap_targets([target], force_refresh=True)
                    changed_markets.add(target.market_id)
                continue
            except ValueError as exc:
                _increment_runtime_status("stateApplyFailureCount")
                self.write_dead_letter(
                    "invalid_orderbook_event",
                    raw_payload=event,
                    token_id=normalized.token_id,
                    market_id=target.market_id if target else identity.market_id,
                    condition_id=target.condition_id if target else identity.condition_id,
                    event_type=event_type,
                    detail=str(exc),
                )
                continue
            if target is not None:
                changed_markets.add(target.market_id)
        for market_id in changed_markets:
            target = self.targets_by_market.get(market_id)
            if target is not None:
                self.publish_target_cache(target, reason=str(event.get("event_type") or "websocket"))
                self.persist_target_if_due(target, reason=str(event.get("event_type") or "websocket"))
        self.flush_clickhouse_sink()
        return len(changed_markets)

    def publish_target_cache(
        self,
        target: CoverageTarget,
        *,
        payload: dict[str, Any] | None = None,
        reason: str = "",
    ) -> bool:
        latest = payload or self.manager.get_cached_market_snapshot(
            market_id=target.market_id,
            yes_token_id=target.yes_token_id,
            no_token_id=target.no_token_id,
            market_title=target.market_title,
        )
        latest["coverage"] = {
            "topic": target.topic,
            "tier": target.tier,
            "sampleIntervalSeconds": target.sample_interval_seconds,
            "reason": reason,
        }
        latest["source"] = "local-orderbook"
        return lob_service.cache_runtime_lob_payload(
            self.ctx,
            latest,
            target.yes_token_id,
            target.no_token_id,
        )

    def persist_target_if_due(self, target: CoverageTarget, *, force: bool = False, reason: str = "") -> bool:
        if not self.persist or self.snapshot_persistor is None:
            return False
        now = time.time()
        last_at = float(self._last_persisted_at_by_market.get(target.market_id) or 0)
        if not force and now - last_at < target.sample_interval_seconds:
            return False
        payload = self.manager.get_cached_market_snapshot(
            market_id=target.market_id,
            yes_token_id=target.yes_token_id,
            no_token_id=target.no_token_id,
            market_title=target.market_title,
        )
        payload["coverage"] = {
            "topic": target.topic,
            "tier": target.tier,
            "sampleIntervalSeconds": target.sample_interval_seconds,
            "reason": reason,
        }
        payload["source"] = "local-orderbook"
        queued = self.snapshot_persistor.enqueue(payload, target.yes_token_id, target.no_token_id)
        if queued:
            self._last_persisted_at_by_market[target.market_id] = now
        return queued

    def drain_snapshot_persistor(self, *, timeout_seconds: float | None = None) -> bool:
        if self.snapshot_persistor is None:
            return True
        timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else _env_float("POLYDATA_LOB_SNAPSHOT_PERSIST_DRAIN_SECONDS", DEFAULT_SNAPSHOT_PERSIST_DRAIN_SECONDS, minimum=0.0)
        )
        return self.snapshot_persistor.drain(timeout_seconds=timeout)

    def mark_registry_stale(self, reason: str) -> int:
        count = self.manager.mark_all_stale(reason)
        counts = self.manager.runtime_book_counts()
        _update_runtime_status(status="stale", staleCount=counts.get("staleCount", count), bookCount=counts.get("bookCount", count))
        return count

    def run_periodic_checks(self) -> None:
        self.run_drift_checks()
        self.mark_idle_books_stale()
        self.flush_clickhouse_sink()

    def run_drift_checks(self) -> int:
        max_per_tick = _env_int("POLYDATA_LOB_DRIFT_CHECK_MAX_PER_TICK", DEFAULT_DRIFT_CHECK_MAX_PER_TICK, minimum=0)
        if max_per_tick <= 0:
            return 0
        now = time.time()
        checked = 0
        for target in list(self.targets_by_market.values()):
            if checked >= max_per_tick:
                break
            interval = _tier_drift_seconds(target.tier)
            if now - float(self._last_drift_check_at_by_market.get(target.market_id) or 0) < interval:
                continue
            self._last_drift_check_at_by_market[target.market_id] = now
            before = self.manager.get_cached_market_snapshot(
                market_id=target.market_id,
                yes_token_id=target.yes_token_id,
                no_token_id=target.no_token_id,
                market_title=target.market_title,
            )
            before_hash = _market_snapshot_hash(before)
            try:
                after = self.manager.get_market_snapshot(
                    market_id=target.market_id,
                    yes_token_id=target.yes_token_id,
                    no_token_id=target.no_token_id,
                    market_title=target.market_title,
                    condition_id=target.condition_id,
                    market_slug=target.market_slug or None,
                    force_refresh=True,
                )
            except Exception as exc:
                self.write_dead_letter(
                    "drift_resnapshot_failed",
                    raw_payload=target.raw,
                    token_id=target.yes_token_id,
                    market_id=target.market_id,
                    condition_id=target.condition_id,
                    event_type="drift_check",
                    detail=str(exc),
                )
                continue
            checked += 1
            if before_hash and before_hash != _market_snapshot_hash(after):
                _increment_runtime_status("driftMismatchCount")
                self.persist_target_if_due(target, force=True, reason="drift_check")
        return checked

    def mark_idle_books_stale(self) -> int:
        now = time.time()
        if now - self._last_idle_check_at < 30:
            return 0
        self._last_idle_check_at = now
        now_ms = int(now * 1000)
        changed_markets: set[int] = set()
        for target in self.targets_by_market.values():
            stale_after_ms = _tier_stale_idle_seconds(target.tier) * 1000
            changed = False
            for token_id in target.token_ids:
                changed = self.manager.mark_token_stale_if_idle(token_id, now_ms=now_ms, stale_after_ms=stale_after_ms) or changed
            if changed:
                changed_markets.add(target.market_id)
        for market_id in changed_markets:
            target = self.targets_by_market.get(market_id)
            if target is not None:
                self.persist_target_if_due(target, force=True, reason="idle_stale")
        counts = self.manager.runtime_book_counts()
        _update_runtime_status(staleCount=counts.get("staleCount", 0), bookCount=counts.get("bookCount", 0))
        return len(changed_markets)

    def write_dead_letter(
        self,
        reason: str,
        *,
        raw_payload: Any = None,
        token_id: str = "",
        market_id: int | None = None,
        condition_id: str = "",
        event_type: str = "",
        detail: str = "",
    ) -> None:
        if lob_service.write_lob_dead_letter(
            reason=reason,
            raw_payload=raw_payload,
            token_id=token_id,
            market_id=market_id,
            condition_id=condition_id,
            event_type=event_type,
            detail=detail,
        ):
            _increment_runtime_status("deadLetterCount")

    def _build_clickhouse_sink(self) -> ClickHouseLobSink | None:
        settings = LobClickHouseSettings()
        if not settings.enabled:
            _update_runtime_status(clickhouseEnabled=False, clickhouse={"enabled": False, "rowsInserted": 0, "bufferedRows": 0})
            return None
        try:
            sink = ClickHouseLobSink(settings=settings)
            sink.create_schema()
            _update_runtime_status(
                clickhouseEnabled=True,
                clickhouseTiers=sorted(settings.tiers),
                clickhouseDeltaTable=settings.delta_table,
                clickhouseLevelTable=settings.level_table,
                clickhouseTtlDays=settings.ttl_days,
                clickhouse=sink.status_snapshot(),
            )
            return sink
        except Exception as exc:
            self.logger.warning("ClickHouse LOB sink disabled after init failure: %s", exc)
            _update_runtime_status(clickhouseEnabled=False, clickhouseError=str(exc)[:240])
            return None

    def enqueue_clickhouse_event(
        self,
        *,
        identity: TokenBookIdentity,
        target: CoverageTarget | None,
        event: Any,
        state_payload: dict[str, Any],
    ) -> None:
        if self.clickhouse_sink is None or target is None:
            return
        generation = _int_or_none(state_payload.get("generation")) or 0
        received_ts_ms = int(time.time() * 1000)
        try:
            if isinstance(event, NormalizedBookDelta):
                self.clickhouse_sink.enqueue_delta(
                    identity=identity,
                    event=event,
                    tier=target.tier,
                    generation=generation,
                    source="websocket",
                    received_ts_ms=received_ts_ms,
                )
            elif isinstance(event, NormalizedBookSnapshot):
                self.clickhouse_sink.enqueue_snapshot_levels(
                    identity=identity,
                    event=event,
                    tier=target.tier,
                    generation=generation,
                    source="websocket",
                    received_ts_ms=received_ts_ms,
                    depth_limit=self.manager.depth_limit,
                )
            self._sync_clickhouse_status()
        except Exception as exc:
            self.logger.warning("ClickHouse LOB enqueue failed token_id=%s error=%s", identity.token_id, exc)
            self.write_dead_letter(
                "clickhouse_lob_enqueue_failed",
                raw_payload=getattr(event, "raw", None),
                token_id=identity.token_id,
                market_id=identity.market_id,
                condition_id=identity.condition_id,
                event_type="clickhouse_lob",
                detail=str(exc),
            )

    def flush_clickhouse_sink(self, *, force: bool = False) -> int:
        if self.clickhouse_sink is None:
            return 0
        try:
            inserted = self.clickhouse_sink.flush_if_due(force=force)
            self._sync_clickhouse_status()
            return inserted
        except Exception as exc:
            self.logger.warning("ClickHouse LOB flush failed: %s", exc)
            self.clickhouse_sink.mark_flush_failure(exc)
            self._sync_clickhouse_status(extra={"clickhouseError": str(exc)[:240]})
            return 0

    def _sync_clickhouse_status(self, *, extra: dict[str, Any] | None = None) -> None:
        if self.clickhouse_sink is None:
            return
        snapshot = self.clickhouse_sink.status_snapshot()
        updates = {
            "clickhouseEnabled": bool(snapshot.get("enabled")),
            "clickhouseRowsInserted": int(snapshot.get("rowsInserted") or 0),
            "clickhouseBufferedRows": int(snapshot.get("bufferedRows") or 0),
            "clickhouseDeltaRowsInserted": int(snapshot.get("deltaRowsInserted") or 0),
            "clickhouseLevelRowsInserted": int(snapshot.get("levelRowsInserted") or 0),
            "clickhouseDeltaRowsEnqueued": int(snapshot.get("deltaRowsEnqueued") or 0),
            "clickhouseLevelRowsEnqueued": int(snapshot.get("levelRowsEnqueued") or 0),
            "clickhouseFlushFailureCount": int(snapshot.get("flushFailureCount") or 0),
            "clickhouse": snapshot,
        }
        if extra:
            updates.update(extra)
        _update_runtime_status(**updates)


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def acquire(self) -> bool:
        if fcntl is None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            self.fd = fd
            return True
        except BlockingIOError:
            os.close(fd)
            return False

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def start_background_worker(ctx: dict[str, Any], *, lock_dir: str | Path | None = None) -> threading.Thread | None:
    if str(os.environ.get("POLYDATA_LOB_WEBSOCKET_ENABLED", "0")).strip().lower() not in {"1", "true", "yes", "on"}:
        _update_runtime_status(status="disabled")
        return None
    logger = _Logger()
    ws_url = os.environ.get("POLYDATA_CLOB_WS_URL") or POLYMARKET_CLOB_WS_URL
    if not str(ws_url or "").strip():
        logger.warning("websocket disabled: POLYDATA_CLOB_WS_URL is empty")
        _update_runtime_status(status="disabled", detail="missing websocket url")
        return None
    base_dir = Path(lock_dir or os.environ.get("POLYDATA_RUNTIME_LOCK_DIR") or "/tmp/polydata")
    lock = _FileLock(base_dir / DEFAULT_LOCK_NAME)
    if not lock.acquire():
        logger.info("websocket disabled: another process owns %s", lock.path)
        _update_runtime_status(status="standby", detail=f"lock held: {lock.path}")
        return None

    def _run() -> None:
        try:
            watcher = LocalOrderBookWebsocketWatcher(
                ctx=ctx,
                ws_url=str(ws_url),
                limit=int(os.environ.get("POLYDATA_LOB_COVERAGE_LIMIT", DEFAULT_COVERAGE_LIMIT) or DEFAULT_COVERAGE_LIMIT),
                topics=os.environ.get("POLYDATA_LOB_COVERAGE_TOPICS", DEFAULT_COVERAGE_TOPICS),
                coverage_refresh_seconds=int(os.environ.get("POLYDATA_LOB_COVERAGE_REFRESH_SECONDS", DEFAULT_COVERAGE_REFRESH_SECONDS) or DEFAULT_COVERAGE_REFRESH_SECONDS),
                subscription_batch_size=int(os.environ.get("POLYDATA_LOB_SUBSCRIPTION_BATCH_SIZE", DEFAULT_SUBSCRIPTION_BATCH_SIZE) or DEFAULT_SUBSCRIPTION_BATCH_SIZE),
                subscription_batch_pause_seconds=_env_float(
                    "POLYDATA_LOB_SUBSCRIPTION_BATCH_PAUSE_SECONDS",
                    DEFAULT_SUBSCRIPTION_BATCH_PAUSE_SECONDS,
                    minimum=0.0,
                ),
                bootstrap_market_limit=int(os.environ.get("POLYDATA_LOB_BOOTSTRAP_MARKET_LIMIT", DEFAULT_BOOTSTRAP_MARKET_LIMIT) or DEFAULT_BOOTSTRAP_MARKET_LIMIT),
                persist=str(os.environ.get("POLYDATA_LOB_WS_PERSIST_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"},
                logger=logger,
            )
            asyncio.run(watcher.run_forever())
        finally:
            lock.close()

    thread = threading.Thread(target=_run, name="local-orderbook-websocket", daemon=True)
    thread.start()
    logger.info("websocket background worker started")
    _update_runtime_status(status="starting")
    return thread


def build_default_context() -> dict[str, Any]:
    from api_server import build_service_context

    return build_service_context()


def _iter_json_events(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        yield payload
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item


def _event_sample(event: dict[str, Any], *, event_type: str) -> dict[str, Any]:
    sample: dict[str, Any] = {
        "eventType": event_type,
        "keys": sorted(str(key) for key in event.keys())[:16],
        "timestamp": str(event.get("timestamp") or "")[:32] or None,
        "market": _short_text(event.get("market"), limit=32),
        "assetId": _short_text(event.get("asset_id"), limit=32),
    }
    if isinstance(event.get("bids"), list):
        sample["bidCount"] = len(event.get("bids") or [])
    if isinstance(event.get("asks"), list):
        sample["askCount"] = len(event.get("asks") or [])
    if isinstance(event.get("price_changes"), list):
        changes = [item for item in event.get("price_changes") or [] if isinstance(item, dict)]
        sample["priceChangeCount"] = len(changes)
        if changes:
            first = changes[0]
            sample["firstPriceChange"] = {
                "keys": sorted(str(key) for key in first.keys())[:16],
                "assetId": _short_text(first.get("asset_id"), limit=32),
                "side": _short_text(first.get("side"), limit=12),
                "hasBestBid": first.get("best_bid") is not None,
                "hasBestAsk": first.get("best_ask") is not None,
            }
    if isinstance(event.get("assets_ids"), list):
        sample["assetIdCount"] = len(event.get("assets_ids") or [])
    return {key: value for key, value in sample.items() if value is not None and value != ""}


def _chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), max(1, int(size))):
        yield values[index : index + size]


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _snapshot_persist_key(payload: dict[str, Any], yes_token_id: str, no_token_id: str) -> str:
    market_id = _int_or_none(payload.get("marketId") or payload.get("market_id"))
    if market_id is not None:
        return f"market:{market_id}"
    return f"tokens:{yes_token_id}:{no_token_id}"


def _int_or_none(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _short_text(value: Any, *, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[: max(1, int(limit))]


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default) or default))
    except (TypeError, ValueError):
        return max(minimum, int(default))


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.environ.get(name, default) or default))
    except (TypeError, ValueError):
        return max(minimum, float(default))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _tier_drift_seconds(tier: str) -> int:
    tier_key = str(tier or "").strip().lower()
    default = DRIFT_CHECK_SECONDS_BY_TIER.get(tier_key, DRIFT_CHECK_SECONDS_BY_TIER["cold"])
    return _env_int(f"POLYDATA_LOB_DRIFT_SECONDS_{tier_key.upper()}", default, minimum=60)


def _tier_stale_idle_seconds(tier: str) -> int:
    tier_key = str(tier or "").strip().lower()
    default = STALE_IDLE_SECONDS_BY_TIER.get(tier_key, STALE_IDLE_SECONDS_BY_TIER["cold"])
    return _env_int(f"POLYDATA_LOB_STALE_IDLE_SECONDS_{tier_key.upper()}", default, minimum=30)


def _market_snapshot_hash(payload: dict[str, Any]) -> str:
    sides = []
    for side_name in ("yes", "no"):
        side = payload.get(side_name) if isinstance(payload, dict) else None
        if not isinstance(side, dict):
            continue
        sides.append(
            {
                "side": side_name,
                "snapshotVersion": side.get("snapshotVersion") or side.get("snapshot_version"),
                "bids": side.get("bids") or [],
                "asks": side.get("asks") or [],
            }
        )
    if not sides:
        return ""
    import hashlib

    return hashlib.sha256(json.dumps(sides, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()[:20]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply Polymarket CLOB websocket events to the LocalOrderBook registry")
    parser.add_argument("--ws-url", default=os.environ.get("POLYDATA_CLOB_WS_URL") or POLYMARKET_CLOB_WS_URL)
    parser.add_argument("--limit", type=int, default=int(os.environ.get("POLYDATA_LOB_COVERAGE_LIMIT", DEFAULT_COVERAGE_LIMIT) or DEFAULT_COVERAGE_LIMIT))
    parser.add_argument("--topics", default=os.environ.get("POLYDATA_LOB_COVERAGE_TOPICS", DEFAULT_COVERAGE_TOPICS))
    parser.add_argument("--coverage-refresh-seconds", type=int, default=int(os.environ.get("POLYDATA_LOB_COVERAGE_REFRESH_SECONDS", DEFAULT_COVERAGE_REFRESH_SECONDS) or DEFAULT_COVERAGE_REFRESH_SECONDS))
    parser.add_argument("--bootstrap-limit", type=int, default=int(os.environ.get("POLYDATA_LOB_BOOTSTRAP_MARKET_LIMIT", DEFAULT_BOOTSTRAP_MARKET_LIMIT) or DEFAULT_BOOTSTRAP_MARKET_LIMIT))
    parser.add_argument("--run-seconds", type=int, default=0)
    parser.add_argument("--bootstrap-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Apply state in memory but skip database persistence")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    watcher = LocalOrderBookWebsocketWatcher(
        ctx=build_default_context(),
        ws_url=args.ws_url,
        limit=args.limit,
        topics=args.topics,
        coverage_refresh_seconds=args.coverage_refresh_seconds,
        bootstrap_market_limit=args.bootstrap_limit,
        persist=not args.dry_run,
    )
    if args.bootstrap_only:
        targets = watcher.refresh_targets()
        watcher.bootstrap_targets(targets[: args.bootstrap_limit] if args.bootstrap_limit else [], force_refresh=True)
        return 0
    asyncio.run(watcher.run_connection(run_seconds=args.run_seconds if args.run_seconds > 0 else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
