from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import new_market_signal_service
from runtime.new_market_signal_watcher import NewMarketSignalWatcher
from runtime.seed_meta import SeedMetaStore
from runtime.snapshot_store import SnapshotStore


class FakeLogger:
    def exception(self, *args, **kwargs) -> None:
        return None


class FakeApp:
    logger = FakeLogger()


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.pings = 0

    def ping(self):
        self.pings += 1
        return True

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = str(value)
        return True


class NewMarketSignalServiceTestCase(unittest.TestCase):
    def test_snapshot_returns_items_from_redis_with_limit(self):
        redis_client = FakeRedis()
        redis_client.set(
            "polydata:runtime:new-market-signals:items",
            json.dumps(
                [
                    {"marketId": 2, "title": "Second", "initialYesProbability": "0.6"},
                    {"marketId": 1, "title": "First", "initialYesProbability": "0.4"},
                ]
            ),
        )
        ctx = {
            "REDIS_PREFIX": "polydata:",
            "get_redis_client": lambda: redis_client,
            "utc_now_iso": lambda: "2026-04-25T00:00:00Z",
            "app": FakeApp(),
        }

        payload = new_market_signal_service.get_new_market_signals_snapshot(ctx, limit=1)

        self.assertEqual([{"marketId": 2, "title": "Second", "initialYesProbability": "0.6"}], payload["items"])
        self.assertEqual("2026-04-25T00:00:00Z", payload["generatedAt"])
        self.assertEqual("runtime-redis", payload["cacheMode"])
        self.assertEqual("ok", payload["status"])

    def test_snapshot_prefers_seed_payload_over_legacy_runtime_key(self):
        redis_client = FakeRedis()
        redis_client.set(
            "polydata:runtime:new-market-signals:items",
            json.dumps([{"marketId": 1, "title": "Legacy"}]),
        )
        seed_payload = {
            "items": [{"marketId": 2, "title": "Seeded"}],
            "generatedAt": "2026-04-25T00:01:00Z",
            "status": "ok",
            "cacheMode": "seeded",
        }
        def get_cached_json(namespace, cache_key):
            if (namespace, cache_key) == (
                new_market_signal_service.SNAPSHOT_NAMESPACE,
                new_market_signal_service.SNAPSHOT_CACHE_KEY,
            ):
                return seed_payload
            return None

        ctx = {
            "REDIS_PREFIX": "polydata:",
            "get_redis_client": lambda: redis_client,
            "get_cached_json": get_cached_json,
            "SNAPSHOT_STORE": None,
            "utc_now_iso": lambda: "2026-04-25T00:00:00Z",
            "app": FakeApp(),
        }

        payload = new_market_signal_service.get_new_market_signals_snapshot(ctx, limit=12)

        self.assertEqual([{"marketId": 2, "title": "Seeded"}], payload["items"])
        self.assertEqual("seeded", payload["cacheMode"])

    def test_snapshot_filters_placeholder_recovered_titles(self):
        redis_client = FakeRedis()
        redis_client.set(
            "polydata:runtime:new-market-signals:items",
            json.dumps(
                [
                    {"marketId": 2, "title": "On-chain recovered market 0xabc", "initialYesProbability": "0.5"},
                    {"marketId": 1, "title": "Will real market happen?", "initialYesProbability": "0.4"},
                ]
            ),
        )
        ctx = {
            "REDIS_PREFIX": "polydata:",
            "get_redis_client": lambda: redis_client,
            "utc_now_iso": lambda: "2026-04-25T00:00:00Z",
            "app": FakeApp(),
        }

        payload = new_market_signal_service.get_new_market_signals_snapshot(ctx, limit=12)

        self.assertEqual([{"marketId": 1, "title": "Will real market happen?", "initialYesProbability": "0.4"}], payload["items"])

    def test_snapshot_degrades_when_redis_unavailable(self):
        ctx = {
            "REDIS_PREFIX": "polydata:",
            "get_redis_client": lambda: None,
            "utc_now_iso": lambda: "2026-04-25T00:00:00Z",
            "app": FakeApp(),
        }

        payload = new_market_signal_service.get_new_market_signals_snapshot(ctx, limit=12)

        self.assertEqual([], payload["items"])
        self.assertEqual("degraded", payload["status"])


class NewMarketSignalWatcherTestCase(unittest.TestCase):
    def make_watcher(self, *, max_market_age_hours: int = 0) -> NewMarketSignalWatcher:
        watcher = NewMarketSignalWatcher.__new__(NewMarketSignalWatcher)
        watcher.redis_client = FakeRedis()
        watcher.redis_prefix = "polydata:"
        watcher.last_seen_key = "polydata:runtime:new-market-signals:last_seen_market_id"
        watcher.items_key = "polydata:runtime:new-market-signals:items"
        watcher.pending_key = "polydata:runtime:new-market-signals:pending_market_ids"
        watcher.retention = 50
        watcher.max_market_age_hours = max_market_age_hours
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        watcher.snapshot_store = SnapshotStore(str(Path(snapshot_dir.name) / "seed-meta.sqlite3"))
        watcher.seed_meta_store = SeedMetaStore(redis_client=watcher.redis_client, redis_prefix="polydata:", snapshot_store=watcher.snapshot_store)
        return watcher

    def test_first_run_bootstraps_without_signals(self):
        watcher = self.make_watcher()
        watcher.get_current_max_market_id = lambda: 42

        result = watcher.run_once(limit=100)

        self.assertEqual("bootstrap", result["mode"])
        self.assertEqual(42, result["lastSeenMarketId"])
        self.assertEqual("42", watcher.redis_client.get(watcher.last_seen_key))
        self.assertIsNone(watcher.redis_client.get(watcher.items_key))
        self.assertIsNotNone(watcher.redis_client.get(watcher.snapshot_redis_key()))
        snapshot = watcher.snapshot_store.get_stale(
            new_market_signal_service.SNAPSHOT_NAMESPACE,
            new_market_signal_service.SNAPSHOT_CACHE_KEY,
        )
        self.assertEqual("seeded", snapshot["cacheMode"])
        self.assertEqual("empty", snapshot["status"])
        self.assertIsNotNone(watcher.seed_meta_store.load(watcher.seed_meta_namespace(), watcher.seed_meta_cache_key()))

    def test_scan_generates_signals_once_and_advances_watermark(self):
        watcher = self.make_watcher()
        watcher.set_last_seen_market_id(10)
        watcher.fetch_new_markets = lambda last_seen, limit: [
            {"id": 11, "title": "Will this market launch?", "yes_token_id": "yes-11", "created_at": "2026-04-25T00:00:00Z"}
        ]
        watcher.fetch_initial_yes_probability = lambda yes_token_id: ("0.57", "clob_book_midpoint")

        result = watcher.run_once(limit=100)
        items = json.loads(watcher.redis_client.get(watcher.items_key))

        self.assertEqual("scan", result["mode"])
        self.assertEqual(1, result["signals"])
        self.assertEqual("11", watcher.redis_client.get(watcher.last_seen_key))
        self.assertEqual(11, items[0]["marketId"])
        self.assertEqual("0.57", items[0]["initialYesProbability"])
        snapshot = watcher.snapshot_store.get_stale(
            new_market_signal_service.SNAPSHOT_NAMESPACE,
            new_market_signal_service.SNAPSHOT_CACHE_KEY,
        )
        self.assertEqual("seeded", snapshot["cacheMode"])
        self.assertEqual("ok", snapshot["status"])
        self.assertEqual(11, snapshot["items"][0]["marketId"])
        meta = watcher.seed_meta_store.load(watcher.seed_meta_namespace(), watcher.seed_meta_cache_key())
        self.assertEqual("scan", meta["status"])
        self.assertEqual(1, meta["recordCount"])

    def test_scan_holds_placeholder_titles_in_pending_without_signal(self):
        watcher = self.make_watcher()
        watcher.set_last_seen_market_id(10)
        watcher.fetch_markets_by_local_ids = lambda market_ids: []
        watcher.fetch_new_markets = lambda last_seen, limit: [
            {"id": 11, "title": "On-chain recovered market 0xabc", "yes_token_id": "yes-11", "created_at": None}
        ]
        watcher.fetch_initial_yes_probability = lambda yes_token_id: self.fail("placeholder should not fetch CLOB")

        result = watcher.run_once(limit=100)

        self.assertEqual(0, result["signals"])
        self.assertEqual(1, result["pending"])
        self.assertEqual("11", watcher.redis_client.get(watcher.last_seen_key))
        self.assertEqual([11], json.loads(watcher.redis_client.get(watcher.pending_key)))
        self.assertEqual([], json.loads(watcher.redis_client.get(watcher.items_key)))

    def test_pending_placeholder_emits_after_title_becomes_ready(self):
        watcher = self.make_watcher(max_market_age_hours=72)
        watcher.set_last_seen_market_id(11)
        watcher.store_pending_market_ids([11])
        recent_created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        watcher.fetch_markets_by_local_ids = lambda market_ids: [
            {"id": 11, "title": "Will the title be filled?", "yes_token_id": "yes-11", "created_at": recent_created_at}
        ]
        watcher.fetch_new_markets = lambda last_seen, limit: []
        watcher.fetch_initial_yes_probability = lambda yes_token_id: ("0.62", "clob_book_midpoint")

        result = watcher.run_once(limit=100)
        items = json.loads(watcher.redis_client.get(watcher.items_key))

        self.assertEqual(1, result["signals"])
        self.assertEqual(0, result["pending"])
        self.assertEqual([], json.loads(watcher.redis_client.get(watcher.pending_key)))
        self.assertEqual("Will the title be filled?", items[0]["title"])
        self.assertEqual("0.62", items[0]["initialYesProbability"])

    def test_scan_skips_stale_created_at_without_signal(self):
        watcher = self.make_watcher(max_market_age_hours=72)
        watcher.set_last_seen_market_id(10)
        stale_created_at = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat().replace("+00:00", "Z")
        watcher.fetch_markets_by_local_ids = lambda market_ids: []
        watcher.fetch_new_markets = lambda last_seen, limit: [
            {"id": 11, "title": "Old recovered market", "yes_token_id": "yes-11", "created_at": stale_created_at}
        ]
        watcher.fetch_initial_yes_probability = lambda yes_token_id: self.fail("stale market should not fetch CLOB")

        result = watcher.run_once(limit=100)

        self.assertEqual(0, result["signals"])
        self.assertEqual(1, result["staleCreatedAt"])
        self.assertEqual(0, result["pending"])
        self.assertEqual("11", watcher.redis_client.get(watcher.last_seen_key))
        self.assertEqual([], json.loads(watcher.redis_client.get(watcher.items_key)))

    def test_ready_market_missing_created_at_waits_in_pending(self):
        watcher = self.make_watcher(max_market_age_hours=72)
        watcher.set_last_seen_market_id(10)
        watcher.fetch_markets_by_local_ids = lambda market_ids: []
        watcher.fetch_new_markets = lambda last_seen, limit: [
            {"id": 11, "title": "Ready but missing timestamp", "yes_token_id": "yes-11", "created_at": None}
        ]
        watcher.fetch_initial_yes_probability = lambda yes_token_id: self.fail("missing created_at should not fetch CLOB")

        result = watcher.run_once(limit=100)

        self.assertEqual(0, result["signals"])
        self.assertEqual(1, result["missingCreatedAt"])
        self.assertEqual(1, result["pending"])
        self.assertEqual([11], json.loads(watcher.redis_client.get(watcher.pending_key)))


if __name__ == "__main__":
    unittest.main()
