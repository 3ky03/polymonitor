from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import lob_service
from api.services.lob_service import LocalOrderBookRuntimeManager
from runtime.local_orderbook_websocket_watcher import CoverageTarget, LocalOrderBookWebsocketWatcher, _iter_json_events, _update_runtime_status, get_runtime_status


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.content = b"{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self):
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        token_id = (params or {}).get("token_id")
        if token_id == "yes-token":
            return FakeResponse({"bids": [{"price": "0.40", "size": "10"}], "asks": [{"price": "0.42", "size": "10"}]})
        return FakeResponse({"bids": [{"price": "0.58", "size": "10"}], "asks": [{"price": "0.60", "size": "10"}]})


class FakeLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


def _target_payload():
    return {
        "marketId": 42,
        "marketSlug": "sample-market",
        "marketTitle": "Bitcoin above 100k?",
        "yesTokenId": "yes-token",
        "noTokenId": "no-token",
        "topic": "crypto",
        "tier": "hot",
        "sampleIntervalSeconds": 15,
    }


def test_coverage_target_builds_token_identities():
    target = CoverageTarget.from_payload(_target_payload())

    assert target is not None
    yes, no = target.identities()
    assert yes.token_id == "yes-token"
    assert yes.outcome == "YES"
    assert no.token_id == "no-token"
    assert no.outcome_index == 1


def test_refresh_targets_preserves_coverage_token_order(monkeypatch):
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=FakeSession(), cache_ttl_seconds=30)
    watcher = LocalOrderBookWebsocketWatcher(ctx={"LOB_RUNTIME_MANAGER": manager}, ws_url="wss://example.test/ws", persist=False, logger=FakeLogger())
    payload = {
        "items": [
            {**_target_payload(), "marketId": 1, "yesTokenId": "z-hot", "noTokenId": "a-hot"},
            {**_target_payload(), "marketId": 2, "yesTokenId": "m-warm", "noTokenId": "b-warm"},
        ],
        "summary": {"topics": {"crypto": 2}},
    }
    monkeypatch.setattr(lob_service, "get_lob_coverage_targets_payload", lambda ctx, limit, topics: payload)

    watcher.refresh_targets()

    assert watcher.subscription_token_order == ["z-hot", "a-hot", "m-warm", "b-warm"]


def test_runtime_status_can_roundtrip_through_status_file(monkeypatch, tmp_path):
    status_path = tmp_path / "lob-status.json"
    monkeypatch.setenv("POLYDATA_LOB_WEBSOCKET_ENABLED", "1")
    monkeypatch.setenv("POLYDATA_LOB_RUNTIME_STATUS_PATH", str(status_path))
    monkeypatch.setenv("POLYDATA_LOB_RUNTIME_STATUS_WRITE_INTERVAL_SECONDS", "0.1")

    _update_runtime_status(status="connected", subscribedTokenCount=7, priceChangeEventCount=11)
    status = get_runtime_status()

    assert status_path.exists()
    assert status["status"] == "connected"
    assert status["subscribedTokenCount"] == 7
    assert status["priceChangeEventCount"] == 11
    assert status["statusFileAgeSeconds"] >= 0


def test_iter_json_events_accepts_single_and_list_payloads():
    assert list(_iter_json_events({"event_type": "book"})) == [{"event_type": "book"}]
    assert list(_iter_json_events([{"event_type": "book"}, "skip", {"event_type": "price_change"}])) == [
        {"event_type": "book"},
        {"event_type": "price_change"},
    ]


def test_watcher_records_raw_event_type_diagnostics():
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=FakeSession(), cache_ttl_seconds=30)
    watcher = LocalOrderBookWebsocketWatcher(ctx={"LOB_RUNTIME_MANAGER": manager}, ws_url="wss://example.test/ws", persist=False, logger=FakeLogger())

    changed = watcher.handle_event(
        {
            "event_type": "best_bid_ask",
            "asset_id": "yes-token",
            "market": "0xcondition",
            "best_bid": "0.40",
            "best_ask": "0.42",
            "timestamp": "9999999999999",
        }
    )

    status = get_runtime_status()
    assert changed == 0
    assert status["eventTypeCounts"]["best_bid_ask"] >= 1
    assert status["bestBidAskEventCount"] >= 1
    assert status["recentEventSamples"][-1]["eventType"] == "best_bid_ask"
    assert "best_bid" in status["recentEventSamples"][-1]["keys"]


def test_watcher_sends_market_channel_heartbeat():
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=FakeSession(), cache_ttl_seconds=30)
    watcher = LocalOrderBookWebsocketWatcher(ctx={"LOB_RUNTIME_MANAGER": manager}, ws_url="wss://example.test/ws", persist=False, logger=FakeLogger())

    class FakeWebsocket:
        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(payload)

    websocket = FakeWebsocket()
    last_ping_at = asyncio.run(watcher.send_heartbeat_if_due(websocket, now=100.0, last_ping_at=0.0, interval_seconds=10))
    unchanged = asyncio.run(watcher.send_heartbeat_if_due(websocket, now=105.0, last_ping_at=last_ping_at, interval_seconds=10))

    assert websocket.sent == ["PING"]
    assert last_ping_at == 100.0
    assert unchanged == 100.0
    assert get_runtime_status()["pingCount"] >= 1


def test_watcher_subscribe_uses_smaller_batches_with_replace_first_payload():
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=FakeSession(), cache_ttl_seconds=30)
    watcher = LocalOrderBookWebsocketWatcher(
        ctx={"LOB_RUNTIME_MANAGER": manager},
        ws_url="wss://example.test/ws",
        persist=False,
        logger=FakeLogger(),
        subscription_batch_size=80,
        subscription_batch_pause_seconds=0,
    )

    class FakeWebsocket:
        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(payload)

    websocket = FakeWebsocket()
    asyncio.run(watcher.subscribe(websocket, [f"token-{index}" for index in range(201)], replace=True))
    payloads = [json.loads(item) for item in websocket.sent]

    assert [len(payload["assets_ids"]) for payload in payloads] == [80, 80, 41]
    assert payloads[0]["type"] == "market"
    assert "operation" not in payloads[0]
    assert payloads[1]["operation"] == "subscribe"
    assert payloads[2]["operation"] == "subscribe"


def test_watcher_applies_price_change_and_persists_sample(monkeypatch):
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=FakeSession(), cache_ttl_seconds=30)
    ctx = {"LOB_RUNTIME_MANAGER": manager}
    watcher = LocalOrderBookWebsocketWatcher(ctx=ctx, ws_url="wss://example.test/ws", persist=True, logger=FakeLogger())
    target = CoverageTarget.from_payload(_target_payload())
    assert target is not None
    watcher.targets_by_market = {target.market_id: target}
    yes_identity, no_identity = target.identities()
    watcher.identities_by_token = {yes_identity.token_id: yes_identity, no_identity.token_id: no_identity}
    watcher.target_by_token = {yes_identity.token_id: target, no_identity.token_id: target}
    watcher.persist = False
    watcher.bootstrap_targets([target], force_refresh=True)
    watcher.persist = True
    persisted = []
    monkeypatch.setattr(
        lob_service,
        "persist_runtime_lob_payload",
        lambda ctx, payload, yes_token_id, no_token_id: persisted.append((payload, yes_token_id, no_token_id)),
    )
    watcher._last_persisted_at_by_market.clear()

    changed = watcher.handle_event(
        {
            "event_type": "price_change",
            "timestamp": "9999999999999",
            "price_changes": [{"asset_id": "yes-token", "side": "BUY", "price": "0.41", "size": "12"}],
        }
    )
    watcher.drain_snapshot_persistor(timeout_seconds=1)

    assert changed == 1
    assert persisted
    payload, yes_token_id, no_token_id = persisted[-1]
    assert yes_token_id == "yes-token"
    assert no_token_id == "no-token"
    assert payload["yes"]["bestBid"] == "0.41"
    assert payload["coverage"]["topic"] == "crypto"


def test_watcher_snapshot_persist_is_non_blocking(monkeypatch):
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=FakeSession(), cache_ttl_seconds=30)
    ctx = {"LOB_RUNTIME_MANAGER": manager}
    watcher = LocalOrderBookWebsocketWatcher(ctx=ctx, ws_url="wss://example.test/ws", persist=True, logger=FakeLogger())
    target = CoverageTarget.from_payload(_target_payload())
    assert target is not None
    watcher.targets_by_market = {target.market_id: target}
    yes_identity, no_identity = target.identities()
    watcher.identities_by_token = {yes_identity.token_id: yes_identity, no_identity.token_id: no_identity}
    watcher.target_by_token = {yes_identity.token_id: target, no_identity.token_id: target}
    watcher.persist = False
    watcher.bootstrap_targets([target], force_refresh=True)
    watcher.persist = True
    watcher._last_persisted_at_by_market.clear()
    release = threading.Event()
    started = threading.Event()

    def slow_persist(ctx, payload, yes_token_id, no_token_id):
        started.set()
        release.wait(timeout=1)

    monkeypatch.setattr(lob_service, "persist_runtime_lob_payload", slow_persist)

    before = time.monotonic()
    changed = watcher.handle_event(
        {
            "event_type": "price_change",
            "timestamp": "9999999999999",
            "price_changes": [{"asset_id": "yes-token", "side": "BUY", "price": "0.41", "size": "12"}],
        }
    )
    elapsed = time.monotonic() - before

    assert changed == 1
    assert elapsed < 0.2
    assert started.wait(timeout=1)
    release.set()
    assert watcher.drain_snapshot_persistor(timeout_seconds=1)


def test_watcher_unknown_token_goes_to_dead_letter(monkeypatch):
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=FakeSession(), cache_ttl_seconds=30)
    watcher = LocalOrderBookWebsocketWatcher(ctx={"LOB_RUNTIME_MANAGER": manager}, ws_url="wss://example.test/ws", persist=False, logger=FakeLogger())
    dead_letters = []
    monkeypatch.setattr(
        lob_service,
        "write_lob_dead_letter",
        lambda **kwargs: dead_letters.append(kwargs) or True,
    )

    changed = watcher.handle_event(
        {
            "event_type": "price_change",
            "timestamp": "9999999999999",
            "price_changes": [{"asset_id": "unknown-token", "side": "BUY", "price": "0.41", "size": "12"}],
        }
    )

    assert changed == 0
    assert dead_letters[-1]["reason"] == "unknown_token"
    assert dead_letters[-1]["token_id"] == "unknown-token"
    assert get_runtime_status()["deadLetterCount"] >= 1


def test_watcher_price_change_before_ready_resnapshots_without_polluting_state(monkeypatch):
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=FakeSession(), cache_ttl_seconds=30)
    watcher = LocalOrderBookWebsocketWatcher(ctx={"LOB_RUNTIME_MANAGER": manager}, ws_url="wss://example.test/ws", persist=False, logger=FakeLogger())
    target = CoverageTarget.from_payload(_target_payload())
    assert target is not None
    yes_identity, no_identity = target.identities()
    watcher.targets_by_market = {target.market_id: target}
    watcher.identities_by_token = {yes_identity.token_id: yes_identity, no_identity.token_id: no_identity}
    watcher.target_by_token = {yes_identity.token_id: target, no_identity.token_id: target}
    dead_letters = []
    monkeypatch.setattr(
        lob_service,
        "write_lob_dead_letter",
        lambda **kwargs: dead_letters.append(kwargs) or True,
    )

    changed = watcher.handle_event(
        {
            "event_type": "price_change",
            "timestamp": "9999999999999",
            "price_changes": [{"asset_id": "yes-token", "side": "BUY", "price": "0.41", "size": "12"}],
        }
    )

    assert changed == 1
    assert dead_letters[-1]["reason"] == "price_change_before_ready"
    assert manager.registry.get("yes-token").ready
    assert manager.registry.get("yes-token").best_bid()[0].normalize().to_eng_string() == "0.4"


def test_watcher_reconnect_marks_existing_books_stale():
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=FakeSession(), cache_ttl_seconds=30)
    watcher = LocalOrderBookWebsocketWatcher(ctx={"LOB_RUNTIME_MANAGER": manager}, ws_url="wss://example.test/ws", persist=False, logger=FakeLogger())
    target = CoverageTarget.from_payload(_target_payload())
    assert target is not None
    watcher.bootstrap_targets([target], force_refresh=True)

    stale_count = watcher.mark_registry_stale("websocket_reconnect")

    assert stale_count == 2
    assert manager.runtime_book_counts()["staleCount"] == 2


def test_watcher_writes_clickhouse_delta_after_state_machine_apply(monkeypatch):
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=FakeSession(), cache_ttl_seconds=30)
    watcher = LocalOrderBookWebsocketWatcher(ctx={"LOB_RUNTIME_MANAGER": manager}, ws_url="wss://example.test/ws", persist=False, logger=FakeLogger())
    target = CoverageTarget.from_payload(_target_payload())
    assert target is not None
    yes_identity, no_identity = target.identities()
    watcher.targets_by_market = {target.market_id: target}
    watcher.identities_by_token = {yes_identity.token_id: yes_identity, no_identity.token_id: no_identity}
    watcher.target_by_token = {yes_identity.token_id: target, no_identity.token_id: target}
    watcher.bootstrap_targets([target], force_refresh=True)

    class FakeSink:
        def __init__(self):
            self.calls = []
            self.rows_inserted = 0
            self.delta_rows_enqueued = 0

        def enqueue_delta(self, **kwargs):
            self.calls.append(kwargs)
            self.delta_rows_enqueued += 1
            return 1

        def enqueue_snapshot_levels(self, **kwargs):
            raise AssertionError("delta test should not write levels")

        def flush_if_due(self, force=False):
            return 0

        def buffered_rows(self):
            return 0

        def status_snapshot(self):
            return {
                "enabled": True,
                "rowsInserted": self.rows_inserted,
                "bufferedRows": 0,
                "deltaRowsEnqueued": self.delta_rows_enqueued,
                "deltaRowsInserted": 0,
                "levelRowsEnqueued": 0,
                "levelRowsInserted": 0,
                "flushFailureCount": 0,
            }

    sink = FakeSink()
    watcher.clickhouse_sink = sink

    watcher.handle_event(
        {
            "event_type": "price_change",
            "timestamp": "9999999999999",
            "price_changes": [{"asset_id": "yes-token", "side": "BUY", "price": "0.41", "size": "12"}],
        }
    )

    assert sink.calls
    assert sink.calls[-1]["identity"].token_id == "yes-token"
    assert sink.calls[-1]["tier"] == "hot"
    assert sink.calls[-1]["generation"] >= 1
    status = get_runtime_status()
    assert status["clickhouse"]["deltaRowsEnqueued"] >= 1
    assert status["priceChangeEventCount"] >= 1
