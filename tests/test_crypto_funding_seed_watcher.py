from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import crypto_funding_service
from runtime import crypto_funding_watcher
from runtime.snapshot_store import SnapshotStore


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value


def make_settings(snapshot_path: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        redis_url="redis://test/0",
        redis_prefix="polydata:",
        snapshot_sqlite_path=snapshot_path,
        crypto_funding_watch_api_url="binance",
        crypto_funding_watch_bybit_api_url="bybit",
        crypto_funding_watch_api_key="",
        crypto_funding_watch_bybit_api_key="",
        crypto_funding_watch_source_url="source",
        crypto_funding_watch_ttl_seconds=15,
        crypto_funding_watch_symbols=("BTCUSDT",),
    )


def sample_payload() -> dict:
    return {
        "generatedAt": "2026-05-03T08:00:00Z",
        "source": "binance/bybit-funding",
        "sourceUrl": "source",
        "status": "ok",
        "sources": {"binance": "ok", "bybit": "ok"},
        "venues": ["Binance"],
        "assets": [{"id": "BTC", "asset": "BTC", "quotes": []}],
        "items": [{"id": "binance:BTCUSDT", "asset": "BTC"}],
    }


class CryptoFundingSeedWatcherTestCase(unittest.TestCase):
    def make_watcher(self):
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        fake_redis = FakeRedis()
        settings = make_settings(str(Path(snapshot_dir.name) / "snapshots.sqlite3"))
        redis_module = SimpleNamespace(from_url=lambda *args, **kwargs: fake_redis)
        requests_module = SimpleNamespace(Session=lambda: SimpleNamespace(get=lambda *args, **kwargs: None))
        with patch.object(crypto_funding_watcher, "redis", redis_module), patch.object(crypto_funding_watcher, "requests", requests_module):
            watcher = crypto_funding_watcher.CryptoFundingWatcher(
                redis_url=settings.redis_url,
                redis_prefix=settings.redis_prefix,
                snapshot_sqlite_path=settings.snapshot_sqlite_path,
                settings=settings,
                limit=18,
                interval_seconds=30,
            )
        return watcher, fake_redis

    def test_watcher_stores_payload_and_seed_meta(self):
        watcher, fake_redis = self.make_watcher()
        with patch.object(crypto_funding_service, "fetch_live_crypto_funding_watch_payload", return_value=sample_payload()):
            result = watcher.run_once()

        self.assertEqual("ok", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
        self.assertEqual("seeded", stored["cacheMode"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:crypto:funding-watch") or "{}")
        self.assertEqual("ok", meta["status"])
        self.assertEqual({"binance": "ok", "bybit": "ok"}, meta["sourceStates"])

    def test_api_reads_seeded_payload_without_live_fetch(self):
        with tempfile.TemporaryDirectory() as snapshot_dir:
            settings = make_settings(str(Path(snapshot_dir) / "snapshots.sqlite3"))
            store = SnapshotStore(settings.snapshot_sqlite_path)
            cache_key = crypto_funding_service.build_crypto_funding_cache_key(settings, limit=18)
            store.set(crypto_funding_service.CRYPTO_FUNDING_NAMESPACE, cache_key, sample_payload(), 60)
            ctx = {
                "SETTINGS": settings,
                "SNAPSHOT_STORE": store,
                "get_cached_json": lambda namespace, key: None,
                "set_cached_json": lambda namespace, key, payload, ttl: None,
                "http_json_get": lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live fetch should not run")),
                "utc_now_iso": lambda: "2026-05-03T00:00:00Z",
            }
            payload = crypto_funding_service.get_crypto_funding_watch_snapshot(ctx, limit=18)

        self.assertEqual("sqlite-seed", payload["cacheMode"])
        self.assertEqual("BTC", payload["assets"][0]["asset"])

    def test_api_trims_default_seed_for_smaller_limit_without_live_fetch(self):
        with tempfile.TemporaryDirectory() as snapshot_dir:
            settings = make_settings(str(Path(snapshot_dir) / "snapshots.sqlite3"))
            store = SnapshotStore(settings.snapshot_sqlite_path)
            default_cache_key = crypto_funding_service.build_crypto_funding_cache_key(
                settings,
                limit=crypto_funding_service.DEFAULT_CRYPTO_FUNDING_LIMIT,
            )
            seeded = {
                **sample_payload(),
                "assets": [
                    {"id": "BTC", "asset": "BTC", "quotes": []},
                    {"id": "ETH", "asset": "ETH", "quotes": []},
                ],
            }
            store.set(crypto_funding_service.CRYPTO_FUNDING_NAMESPACE, default_cache_key, seeded, 60)
            ctx = {
                "SETTINGS": settings,
                "SNAPSHOT_STORE": store,
                "get_cached_json": lambda namespace, key: None,
                "set_cached_json": lambda namespace, key, payload, ttl: None,
                "http_json_get": lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live fetch should not run")),
                "utc_now_iso": lambda: "2026-05-03T00:00:00Z",
            }
            payload = crypto_funding_service.get_crypto_funding_watch_snapshot(ctx, limit=1)

        self.assertEqual("sqlite-seed", payload["cacheMode"])
        self.assertEqual(["BTC"], [item["asset"] for item in payload["assets"]])


if __name__ == "__main__":
    unittest.main()
