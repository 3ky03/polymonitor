#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NBA-only Polymarket LOB live writer backed by parquet + SQLite state."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import random
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

try:
    import websockets
except ImportError:  # pragma: no cover - environment dependent
    websockets = None

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import add_db_cli_args, configure_db_from_args
from lob.lob_service import (
    DEFAULT_BBO_THROTTLE_MS,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_PRICE_CHANGE_THROTTLE_MS,
    DEFAULT_RECONNECT_BASE_SECONDS,
    DEFAULT_RECONNECT_MAX_SECONDS,
    DEFAULT_STALE_AFTER_SECONDS,
    DEFAULT_STREAM_NAME,
    DEFAULT_SUBSCRIPTION_BATCH_SIZE,
    DEFAULT_WS_URL,
    LOBNormalizer,
    SnapshotThrottle,
    TokenMapping,
    collect_asset_ids,
)
from market.market_discovery import fetch_and_upsert_markets_for_token_ids

from NBA.common import (
    DEFAULT_DATA_ROOT,
    PRICE_SCALE,
    ParquetLOBSink,
    RuntimeStateStore,
    SIZE_SCALE,
    ensure_data_root,
    iso_now,
    load_market_catalog_rows,
    load_token_catalog_rows,
    safe_json_dumps,
)
from NBA.nba_market_catalog import sync_nba_markets

DEFAULT_MAX_RECV_TIMEOUTS = 3
DEFAULT_CATALOG_REFRESH_ATTEMPTS = 3
DEFAULT_MAX_DEPTH_LEVELS = 5
PERSISTED_EVENT_TYPES = {"book", "last_trade_price"}


def _decimal_to_scaled_int(value: Any, *, scale: int) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        scaled = (Decimal(text) * Decimal(scale)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return int(scaled)


def snapshot_to_record(snapshot) -> Dict[str, Any]:
    return {
        "dedupe_key": snapshot.dedupe_key,
        "market_id": int(snapshot.market_id),
        "token_id": snapshot.token_id,
        "event_type": snapshot.event_type,
        "event_timestamp_ms": int(snapshot.event_timestamp_ms),
        "best_bid_ppm": _decimal_to_scaled_int(snapshot.best_bid, scale=PRICE_SCALE),
        "best_ask_ppm": _decimal_to_scaled_int(snapshot.best_ask, scale=PRICE_SCALE),
        "last_trade_price_ppm": _decimal_to_scaled_int(snapshot.last_trade_price, scale=PRICE_SCALE),
        "price_ppm": _decimal_to_scaled_int(snapshot.price, scale=PRICE_SCALE),
        "size_micros": _decimal_to_scaled_int(snapshot.size, scale=SIZE_SCALE),
        "side": snapshot.side,
    }


def level_records_from_snapshot(snapshot) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for level in snapshot.levels:
        rows.append(
            {
                "dedupe_key": snapshot.dedupe_key,
                "side": level.side,
                "level_index": int(level.level_index),
                "price_ppm": _decimal_to_scaled_int(level.price, scale=PRICE_SCALE),
                "size_micros": _decimal_to_scaled_int(level.size, scale=SIZE_SCALE),
            }
        )
    return rows


def build_logger(data_root: Path | str, *, verbose: bool = False) -> logging.Logger:
    ensure_data_root(data_root)
    logger = logging.getLogger("nba_lob_live_writer")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


@dataclass
class CatalogRefreshSummary:
    active_markets: int
    active_tokens: int
    tokens_upserted: int
    tokens_deactivated: int


class CatalogRegistry:
    def __init__(self, *, data_root: Path | str, db_path: Optional[str] = None) -> None:
        self.data_root = ensure_data_root(data_root)
        self.db_path = db_path
        self.market_rows: Dict[str, Dict[str, Any]] = {}
        self.token_rows: Dict[str, Dict[str, Any]] = {}
        self.backfill_attempted: Set[str] = set()

    def reload(self) -> None:
        self.market_rows = {
            str(row.get("condition_id") or ""): row
            for row in load_market_catalog_rows(self.data_root)
            if str(row.get("condition_id") or "")
        }
        self.token_rows = {
            str(row.get("token_id") or ""): row
            for row in load_token_catalog_rows(self.data_root)
            if str(row.get("token_id") or "")
        }

    def active_token_rows(self) -> List[Dict[str, Any]]:
        return [row for row in self.token_rows.values() if int(row.get("active") or 0) == 1]

    def active_market_count(self) -> int:
        return sum(1 for row in self.market_rows.values() if int(row.get("active") or 0) == 1)

    def resolve(self, token_id: str) -> Optional[TokenMapping]:
        row = self.token_rows.get(str(token_id))
        if not row:
            return None
        if int(row.get("active") or 0) != 1:
            return None
        return TokenMapping(
            market_id=int(row.get("market_id") or 0),
            condition_id=str(row.get("condition_id") or ""),
            token_id=str(row.get("token_id") or ""),
            outcome=str(row.get("outcome") or ""),
            outcome_index=int(row.get("outcome_index") or 0),
            active=bool(int(row.get("active") or 0)),
        )

    async def ensure_token(self, token_id: str) -> Optional[TokenMapping]:
        mapping = self.resolve(token_id)
        if mapping is not None:
            return mapping
        token_id = str(token_id)
        if token_id in self.backfill_attempted:
            return None
        self.backfill_attempted.add(token_id)
        await asyncio.to_thread(fetch_and_upsert_markets_for_token_ids, [token_id], self.db_path or "")
        await asyncio.to_thread(sync_nba_markets, data_root=self.data_root, db_path=self.db_path)
        self.reload()
        return self.resolve(token_id)


class NBALOBStreamingService:
    def __init__(
        self,
        *,
        data_root: Path | str,
        db_path: Optional[str],
        stream_name: str,
        ws_url: str,
        heartbeat_seconds: float,
        catalog_sync_seconds: float,
        stale_after_seconds: float,
        reconnect_base_seconds: float,
        reconnect_max_seconds: float,
        subscription_batch_size: int,
        snapshot_batch_size: int,
        throttle: SnapshotThrottle,
        logger: logging.Logger,
    ) -> None:
        self.data_root = ensure_data_root(data_root)
        self.db_path = db_path
        self.stream_name = stream_name
        self.ws_url = ws_url
        self.heartbeat_seconds = max(1.0, float(heartbeat_seconds))
        self.catalog_sync_seconds = max(5.0, float(catalog_sync_seconds))
        self.stale_after_seconds = max(5.0, float(stale_after_seconds))
        self.reconnect_base_seconds = max(1.0, float(reconnect_base_seconds))
        self.reconnect_max_seconds = max(self.reconnect_base_seconds, float(reconnect_max_seconds))
        self.subscription_batch_size = max(1, int(subscription_batch_size))
        self.throttle = throttle
        self.logger = logger
        self.state_store = RuntimeStateStore(self.data_root)
        self.catalog = CatalogRegistry(data_root=self.data_root, db_path=self.db_path)
        self.sink = ParquetLOBSink(self.data_root, batch_size=snapshot_batch_size)
        self.normalizer = LOBNormalizer(stream_name=self.stream_name, max_depth_levels=DEFAULT_MAX_DEPTH_LEVELS)
        self.stop_event = asyncio.Event()
        self.subscribed_tokens: Set[str] = set()
        self.max_recv_timeouts = DEFAULT_MAX_RECV_TIMEOUTS
        self.catalog_refresh_attempts = DEFAULT_CATALOG_REFRESH_ATTEMPTS

    def stop(self) -> None:
        self.stop_event.set()

    async def run(self, *, run_seconds: Optional[float] = None) -> None:
        if websockets is None:
            raise RuntimeError("websockets package is required for run-nba-lob-live.")
        self.state_store.init_schema()
        self.catalog.reload()
        await self._refresh_catalog(reason="startup")
        if run_seconds and run_seconds > 0:
            asyncio.create_task(self._auto_stop_after(run_seconds))

        reconnect_count = 0
        while not self.stop_event.is_set():
            desired_tokens = self.state_store.list_desired_tokens()
            if not desired_tokens:
                self.logger.info("No active NBA tokens available yet; refreshing catalog in %.1fs", self.catalog_sync_seconds)
                await self._sleep_with_stop(self.catalog_sync_seconds)
                await self._refresh_catalog(reason="empty_catalog")
                continue
            try:
                await self._run_connection(reconnect_count=reconnect_count)
                reconnect_count = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                reconnect_count += 1
                self.state_store.update_stream_state(
                    stream_name=self.stream_name,
                    ws_url=self.ws_url,
                    connection_status="reconnecting",
                    reconnect_count=reconnect_count,
                    notes=str(exc),
                    subscribed_assets=sorted(self.subscribed_tokens),
                )
                sleep_seconds = self._compute_reconnect_delay(reconnect_count)
                self.logger.warning("NBA LOB stream error: %s; reconnecting in %.1fs", exc, sleep_seconds)
                await self._sleep_with_stop(sleep_seconds)
        self.sink.flush()

    async def _auto_stop_after(self, run_seconds: float) -> None:
        await self._sleep_with_stop(run_seconds)
        if not self.stop_event.is_set():
            self.stop()

    async def _sleep_with_stop(self, seconds: float) -> None:
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return

    def _compute_reconnect_delay(self, reconnect_count: int) -> float:
        base_delay = min(
            self.reconnect_max_seconds,
            self.reconnect_base_seconds * (2 ** max(0, reconnect_count - 1)),
        )
        jitter_ceiling = min(max(0.5, base_delay * 0.25), 5.0)
        jitter = random.uniform(0.0, jitter_ceiling)
        return min(self.reconnect_max_seconds, base_delay + jitter)

    async def _refresh_catalog(self, *, reason: str, websocket=None) -> CatalogRefreshSummary:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.catalog_refresh_attempts + 1):
            try:
                summary = await asyncio.to_thread(sync_nba_markets, data_root=self.data_root, db_path=self.db_path)
                self.catalog.reload()
                desired_summary = self.state_store.upsert_desired_tokens(self.catalog.active_token_rows())
                self.state_store.save_catalog_state("nba_catalog_sync", summary)
                desired_tokens = set(self.state_store.list_desired_tokens())
                if websocket is not None:
                    to_subscribe = sorted(desired_tokens - self.subscribed_tokens)
                    to_unsubscribe = sorted(self.subscribed_tokens - desired_tokens)
                    if to_subscribe:
                        await self._subscribe_tokens(websocket, to_subscribe, replace=False)
                    if to_unsubscribe:
                        await self._unsubscribe_tokens(websocket, to_unsubscribe)
                result = CatalogRefreshSummary(
                    active_markets=self.catalog.active_market_count(),
                    active_tokens=len(desired_tokens),
                    tokens_upserted=desired_summary.tokens_upserted,
                    tokens_deactivated=desired_summary.tokens_deactivated,
                )
                self.logger.info(
                    "NBA catalog refresh[%s]: active_markets=%s active_tokens=%s upserted=%s deactivated=%s",
                    reason,
                    result.active_markets,
                    result.active_tokens,
                    result.tokens_upserted,
                    result.tokens_deactivated,
                )
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self.logger.warning(
                    "NBA catalog refresh[%s] attempt %s/%s failed: %s",
                    reason,
                    attempt,
                    self.catalog_refresh_attempts,
                    exc,
                )
                self.state_store.save_catalog_state(
                    "nba_catalog_sync_error",
                    {
                        "reason": reason,
                        "attempt": attempt,
                        "attempted_at": iso_now(),
                        "error": str(exc),
                    },
                )
                if attempt < self.catalog_refresh_attempts:
                    await self._sleep_with_stop(min(self.catalog_sync_seconds, float(attempt)))

        self.catalog.reload()
        desired_tokens = set(self.state_store.list_desired_tokens())
        fallback = CatalogRefreshSummary(
            active_markets=self.catalog.active_market_count(),
            active_tokens=len(desired_tokens),
            tokens_upserted=0,
            tokens_deactivated=0,
        )
        self.state_store.update_stream_state(
            stream_name=self.stream_name,
            ws_url=self.ws_url,
            connection_status="degraded",
            notes=f"catalog_refresh_failed[{reason}]: {last_error}",
            subscribed_assets=sorted(self.subscribed_tokens),
        )
        self.logger.warning(
            "Using last known NBA catalog after refresh[%s] failure: active_markets=%s active_tokens=%s error=%s",
            reason,
            fallback.active_markets,
            fallback.active_tokens,
            last_error,
        )
        return fallback

    async def _run_connection(self, *, reconnect_count: int) -> None:
        self.logger.info("Connecting to NBA market websocket: %s", self.ws_url)
        self.state_store.update_stream_state(
            stream_name=self.stream_name,
            ws_url=self.ws_url,
            connection_status="connecting",
            reconnect_count=reconnect_count,
        )
        async with websockets.connect(self.ws_url, ping_interval=None, close_timeout=10, max_queue=1000) as websocket:
            desired_tokens = self.state_store.list_desired_tokens()
            self.subscribed_tokens = set()
            await self._subscribe_tokens(websocket, desired_tokens, replace=True)
            self.state_store.update_stream_state(
                stream_name=self.stream_name,
                ws_url=self.ws_url,
                connection_status="connected",
                reconnect_count=reconnect_count,
                subscribed_assets=desired_tokens,
            )
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(websocket))
            catalog_task = asyncio.create_task(self._catalog_loop(websocket))
            consecutive_recv_timeouts = 0
            try:
                while not self.stop_event.is_set():
                    recv_task = asyncio.create_task(websocket.recv())
                    stop_task = asyncio.create_task(self.stop_event.wait())
                    done, pending = await asyncio.wait(
                        {recv_task, stop_task, heartbeat_task, catalog_task},
                        timeout=self.heartbeat_seconds * 3,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        if task in {recv_task, stop_task}:
                            task.cancel()
                    if self.stop_event.is_set():
                        if not recv_task.done():
                            recv_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await recv_task
                        break
                    if heartbeat_task in done:
                        exc = heartbeat_task.exception()
                        raise RuntimeError("heartbeat loop stopped unexpectedly") from exc
                    if catalog_task in done:
                        exc = catalog_task.exception()
                        raise RuntimeError("catalog loop stopped unexpectedly") from exc
                    if stop_task in done and stop_task.result():
                        if not recv_task.done():
                            recv_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await recv_task
                        break
                    if recv_task not in done:
                        consecutive_recv_timeouts += 1
                        self.state_store.update_stream_state(
                            stream_name=self.stream_name,
                            ws_url=self.ws_url,
                            connection_status="degraded",
                            reconnect_count=reconnect_count,
                            notes=f"recv_timeout[{consecutive_recv_timeouts}/{self.max_recv_timeouts}]",
                            subscribed_assets=sorted(self.subscribed_tokens),
                        )
                        self.logger.warning(
                            "NBA websocket recv timed out (%s/%s)",
                            consecutive_recv_timeouts,
                            self.max_recv_timeouts,
                        )
                        if consecutive_recv_timeouts < self.max_recv_timeouts:
                            continue
                        raise RuntimeError(f"websocket recv timed out {consecutive_recv_timeouts} times")
                    raw_message = recv_task.result()
                    consecutive_recv_timeouts = 0
                    if isinstance(raw_message, bytes):
                        raw_message = raw_message.decode("utf-8")
                    await self._handle_raw_message(websocket, str(raw_message))
            finally:
                heartbeat_task.cancel()
                catalog_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
                with contextlib.suppress(asyncio.CancelledError):
                    await catalog_task
                self.sink.flush()

    async def _heartbeat_loop(self, websocket) -> None:
        while not self.stop_event.is_set():
            await self._sleep_with_stop(self.heartbeat_seconds)
            if self.stop_event.is_set():
                break
            await websocket.send("PING")
            self.state_store.update_stream_state(
                stream_name=self.stream_name,
                ws_url=self.ws_url,
                connection_status="connected",
                last_heartbeat_at=iso_now(),
                subscribed_assets=sorted(self.subscribed_tokens),
            )

    async def _catalog_loop(self, websocket) -> None:
        while not self.stop_event.is_set():
            await self._sleep_with_stop(self.catalog_sync_seconds)
            if self.stop_event.is_set():
                break
            try:
                await self._refresh_catalog(reason="periodic", websocket=websocket)
                stale_marked = self.state_store.mark_stale_subscriptions(stale_after_seconds=self.stale_after_seconds)
                if stale_marked:
                    self.logger.warning("Marked %s NBA token subscriptions as stale", stale_marked)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Ignoring periodic NBA catalog error and continuing: %s", exc)
                self.state_store.update_stream_state(
                    stream_name=self.stream_name,
                    ws_url=self.ws_url,
                    connection_status="degraded",
                    notes=f"periodic_catalog_error: {exc}",
                    subscribed_assets=sorted(self.subscribed_tokens),
                )

    async def _subscribe_tokens(self, websocket, token_ids: Sequence[str], *, replace: bool) -> None:
        if not token_ids:
            return
        batches = [list(token_ids[idx : idx + self.subscription_batch_size]) for idx in range(0, len(token_ids), self.subscription_batch_size)]
        for index, batch in enumerate(batches):
            if replace and index == 0:
                payload = {
                    "type": "market",
                    "assets_ids": batch,
                    "custom_feature_enabled": True,
                    "initial_dump": True,
                }
            else:
                payload = {
                    "operation": "subscribe",
                    "assets_ids": batch,
                    "custom_feature_enabled": True,
                    "initial_dump": True,
                }
            await websocket.send(safe_json_dumps(payload))
            self.state_store.mark_tokens_subscribed(batch, status="subscribed")
            self.subscribed_tokens.update(batch)

    async def _unsubscribe_tokens(self, websocket, token_ids: Sequence[str]) -> None:
        if not token_ids:
            return
        for idx in range(0, len(token_ids), self.subscription_batch_size):
            batch = list(token_ids[idx : idx + self.subscription_batch_size])
            await websocket.send(safe_json_dumps({"operation": "unsubscribe", "assets_ids": batch}))
            self.state_store.mark_tokens_subscribed(batch, status="inactive")
            for token_id in batch:
                self.subscribed_tokens.discard(token_id)

    async def _handle_raw_message(self, websocket, raw_message: str) -> None:
        if raw_message == "PONG":
            self.state_store.update_stream_state(
                stream_name=self.stream_name,
                ws_url=self.ws_url,
                connection_status="connected",
                last_heartbeat_at=iso_now(),
                subscribed_assets=sorted(self.subscribed_tokens),
            )
            return
        try:
            events = self.normalizer.decode_message(raw_message)
        except json.JSONDecodeError:
            self.logger.debug("Ignoring non-JSON websocket payload: %s", raw_message[:200])
            return
        if not events:
            return
        self.state_store.update_stream_state(
            stream_name=self.stream_name,
            ws_url=self.ws_url,
            connection_status="connected",
            last_message_at=iso_now(),
            subscribed_assets=sorted(self.subscribed_tokens),
        )
        for event in events:
            await self._handle_event(websocket, event)

    async def _handle_event(self, websocket, event: Dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or "").strip()
        if event_type in {"new_market", "market_resolved"}:
            await self._refresh_catalog(reason=event_type, websocket=websocket)
            return

        token_ids = collect_asset_ids(event)
        if not token_ids:
            self.logger.debug("Skipping NBA event without asset ids: %s", event_type)
            return

        raw_payload = safe_json_dumps(event)
        for token_id in token_ids:
            try:
                mapping = await self.catalog.ensure_token(token_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("NBA token backfill failed for %s: %s", token_id, exc)
                self.state_store.write_dead_letter(
                    stream_name=self.stream_name,
                    token_id=token_id,
                    condition_id=str(event.get("market") or "") or None,
                    event_type=event_type or None,
                    reason=f"token_backfill_failed:{exc}",
                    raw_payload=raw_payload,
                )
                continue
            if mapping is None:
                self.state_store.write_dead_letter(
                    stream_name=self.stream_name,
                    token_id=token_id,
                    condition_id=str(event.get("market") or "") or None,
                    event_type=event_type or None,
                    reason="unknown_or_non_nba_token",
                    raw_payload=raw_payload,
                )
                continue
            snapshots = self.normalizer.normalize_event(event, mapping)
            for snapshot in snapshots:
                if snapshot.event_type not in PERSISTED_EVENT_TYPES:
                    self.state_store.touch_token_message(token_id, status="subscribed", when=snapshot.received_at)
                    continue
                if not self.throttle.should_write(snapshot):
                    continue
                claimed = self.state_store.claim_dedupe(
                    dedupe_key=snapshot.dedupe_key,
                    stream_name=self.stream_name,
                    market_id=int(snapshot.market_id),
                    token_id=snapshot.token_id,
                    event_type=snapshot.event_type,
                    event_timestamp_ms=int(snapshot.event_timestamp_ms),
                )
                if not claimed:
                    continue
                self.sink.append_snapshot(snapshot_to_record(snapshot))
                level_rows = level_records_from_snapshot(snapshot)
                if level_rows:
                    self.sink.append_levels(level_rows)
                self.state_store.touch_token_message(token_id, status="subscribed", when=snapshot.received_at)

    def close(self) -> None:
        self.sink.close()
        self.state_store.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NBA-only Polymarket LOB parquet writer")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run-nba-lob-live", help="Run the NBA-only LOB websocket writer")
    add_db_cli_args(run_cmd)
    run_cmd.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    run_cmd.add_argument("--stream-name", default=f"{DEFAULT_STREAM_NAME}_nba")
    run_cmd.add_argument("--ws-url", default=DEFAULT_WS_URL)
    run_cmd.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    run_cmd.add_argument("--catalog-sync-seconds", type=float, default=60.0)
    run_cmd.add_argument("--stale-after-seconds", type=float, default=DEFAULT_STALE_AFTER_SECONDS)
    run_cmd.add_argument("--reconnect-base-seconds", type=float, default=DEFAULT_RECONNECT_BASE_SECONDS)
    run_cmd.add_argument("--reconnect-max-seconds", type=float, default=DEFAULT_RECONNECT_MAX_SECONDS)
    run_cmd.add_argument("--subscription-batch-size", type=int, default=DEFAULT_SUBSCRIPTION_BATCH_SIZE)
    run_cmd.add_argument("--snapshot-batch-size", type=int, default=250)
    run_cmd.add_argument("--best-bid-ask-throttle-ms", type=int, default=DEFAULT_BBO_THROTTLE_MS)
    run_cmd.add_argument("--price-change-throttle-ms", type=int, default=DEFAULT_PRICE_CHANGE_THROTTLE_MS)
    run_cmd.add_argument("--run-seconds", type=float, default=0.0)
    run_cmd.add_argument("--verbose", action="store_true")

    reconcile_cmd = sub.add_parser("reconcile-nba-lob", help="Refresh NBA catalog/state without opening websocket")
    add_db_cli_args(reconcile_cmd)
    reconcile_cmd.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    reconcile_cmd.add_argument("--stale-after-seconds", type=float, default=DEFAULT_STALE_AFTER_SECONDS)
    return parser


async def command_run_async(args: argparse.Namespace) -> int:
    configure_db_from_args(args)
    logger = build_logger(args.data_root, verbose=bool(args.verbose))
    service = NBALOBStreamingService(
        data_root=args.data_root,
        db_path=getattr(args, "sqlite_path", None),
        stream_name=args.stream_name,
        ws_url=args.ws_url,
        heartbeat_seconds=args.heartbeat_seconds,
        catalog_sync_seconds=args.catalog_sync_seconds,
        stale_after_seconds=args.stale_after_seconds,
        reconnect_base_seconds=args.reconnect_base_seconds,
        reconnect_max_seconds=args.reconnect_max_seconds,
        subscription_batch_size=args.subscription_batch_size,
        snapshot_batch_size=args.snapshot_batch_size,
        throttle=SnapshotThrottle(
            bbo_ms=args.best_bid_ask_throttle_ms,
            price_change_ms=args.price_change_throttle_ms,
        ),
        logger=logger,
    )
    try:
        await service.run(run_seconds=(args.run_seconds if args.run_seconds and args.run_seconds > 0 else None))
    finally:
        service.close()
    return 0


def command_run(args: argparse.Namespace) -> int:
    return asyncio.run(command_run_async(args))


def command_reconcile(args: argparse.Namespace) -> int:
    configure_db_from_args(args)
    summary = sync_nba_markets(data_root=args.data_root, db_path=getattr(args, "sqlite_path", None))
    state_store = RuntimeStateStore(args.data_root)
    try:
        state_store.init_schema()
        token_rows = [row for row in load_token_catalog_rows(args.data_root) if int(row.get("active") or 0) == 1]
        desired = state_store.upsert_desired_tokens(token_rows)
        stale = state_store.mark_stale_subscriptions(stale_after_seconds=args.stale_after_seconds)
        result = {
            "catalog": summary,
            "tokens_upserted": desired.tokens_upserted,
            "tokens_deactivated": desired.tokens_deactivated,
            "stale_marked": stale,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        state_store.close()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "run-nba-lob-live":
        return command_run(args)
    if args.command == "reconcile-nba-lob":
        return command_reconcile(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
