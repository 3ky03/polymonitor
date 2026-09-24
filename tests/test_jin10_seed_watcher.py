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

from api.services import jin10_runtime_service
from runtime import jin10_watcher
from runtime.snapshot_store import SnapshotStore


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        if ex is not None:
            self.expiries[key] = ex

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.set(key, value, ex=ttl)


def make_settings(snapshot_path: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        redis_url="redis://test/0",
        redis_prefix="polydata:",
        snapshot_sqlite_path=snapshot_path,
        signal_runtime_ttl_seconds=45,
        jin10_flash_api_url="https://example.test/jin10",
        jin10_flash_channel="-8200",
        jin10_flash_app_id="app-id",
        jin10_flash_version="1.0.0",
        jin10_flash_detail_base_url="https://example.test/detail",
        jin10_live_url="https://example.test/live",
    )


def sample_payload(*, item_id: str = "flash-1") -> dict:
    return {
        "generatedAt": "2026-05-03T08:00:00+08:00",
        "source": "jin10-flash",
        "sourceUrl": "https://example.test/live",
        "status": "ok",
        "items": [{"id": item_id, "headline": "Fed official says policy remains data dependent"}],
    }


class Jin10SeedWatcherTestCase(unittest.TestCase):
    def make_watcher(self) -> tuple[jin10_watcher.Jin10Watcher, FakeRedis, tempfile.TemporaryDirectory]:
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        fake_redis = FakeRedis()
        settings = make_settings(str(Path(snapshot_dir.name) / "snapshots.sqlite3"))
        redis_module = SimpleNamespace(from_url=lambda *args, **kwargs: fake_redis)
        with patch.object(jin10_watcher, "redis", redis_module):
            watcher = jin10_watcher.Jin10Watcher(
                redis_url=settings.redis_url,
                redis_prefix=settings.redis_prefix,
                snapshot_sqlite_path=settings.snapshot_sqlite_path,
                settings=settings,
                limit=24,
                interval_seconds=60,
            )
        return watcher, fake_redis, snapshot_dir

    def test_watcher_stores_payload_snapshot_and_seed_meta(self):
        watcher, fake_redis, _ = self.make_watcher()
        with patch.object(jin10_runtime_service, "fetch_jin10_panel_payload", return_value=sample_payload()):
            result = watcher.run_once()

        self.assertEqual("stored", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
        self.assertEqual("seeded", stored["cacheMode"])
        self.assertEqual(1, len(stored["items"]))
        snapshot = watcher.snapshot_store.get_stale(watcher.namespace(), watcher.cache_key())
        self.assertEqual("flash-1", snapshot["items"][0]["id"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:macro:jin10-flash") or "{}")
        self.assertEqual("ok", meta["status"])
        self.assertEqual(1, meta["recordCount"])
        self.assertEqual("polydata-jin10-seed.service", meta["serviceName"])

    def test_watcher_preserves_previous_snapshot_when_new_payload_is_empty(self):
        watcher, fake_redis, _ = self.make_watcher()
        previous = {**sample_payload(item_id="old-flash"), "cacheMode": "seeded"}
        watcher.store_payload(previous)

        with patch.object(jin10_runtime_service, "fetch_jin10_panel_payload", return_value={**sample_payload(), "items": []}):
            result = watcher.run_once()

        self.assertEqual("preserved", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
        self.assertEqual("old-flash", stored["items"][0]["id"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:macro:jin10-flash") or "{}")
        self.assertEqual("preserved", meta["status"])
        self.assertIn("Preserved previous snapshot", meta["errorSummary"])

    def test_api_reads_seeded_sqlite_snapshot_without_live_fetch(self):
        with tempfile.TemporaryDirectory() as snapshot_dir:
            settings = make_settings(str(Path(snapshot_dir) / "snapshots.sqlite3"))
            store = SnapshotStore(settings.snapshot_sqlite_path)
            cache_key = jin10_runtime_service.build_jin10_cache_key(settings, limit=24)
            store.set(jin10_runtime_service.JIN10_SNAPSHOT_NAMESPACE, cache_key, sample_payload(), 300)
            cached: dict[tuple[str, str], dict] = {}
            ctx = {
                "SETTINGS": settings,
                "SIGNAL_RUNTIME_TTL_SECONDS": 45,
                "SNAPSHOT_STORE": store,
                "get_cached_json": lambda namespace, key: cached.get((namespace, key)),
                "set_cached_json": lambda namespace, key, payload, ttl: cached.__setitem__((namespace, key), payload),
                "utc_now_iso": lambda: "2026-05-03T00:00:00Z",
            }
            with patch.object(jin10_runtime_service, "fetch_jin10_panel_payload", side_effect=AssertionError("live fetch should not run")):
                payload = jin10_runtime_service.get_jin10_panel_snapshot(ctx, limit=24)

        self.assertEqual("sqlite-seed", payload["cacheMode"])
        self.assertEqual("flash-1", payload["items"][0]["id"])

    def test_api_can_slice_default_seed_for_smaller_limit(self):
        with tempfile.TemporaryDirectory() as snapshot_dir:
            settings = make_settings(str(Path(snapshot_dir) / "snapshots.sqlite3"))
            store = SnapshotStore(settings.snapshot_sqlite_path)
            cache_key = jin10_runtime_service.build_jin10_cache_key(settings, limit=24)
            payload = sample_payload()
            payload["items"] = [{"id": str(index), "headline": f"headline {index}"} for index in range(24)]
            store.set(jin10_runtime_service.JIN10_SNAPSHOT_NAMESPACE, cache_key, payload, 300)
            ctx = {
                "SETTINGS": settings,
                "SIGNAL_RUNTIME_TTL_SECONDS": 45,
                "SNAPSHOT_STORE": store,
                "get_cached_json": lambda namespace, key: None,
                "set_cached_json": lambda namespace, key, payload, ttl: None,
                "utc_now_iso": lambda: "2026-05-03T00:00:00Z",
            }
            with patch.object(jin10_runtime_service, "fetch_jin10_panel_payload", side_effect=AssertionError("live fetch should not run")):
                sliced = jin10_runtime_service.get_jin10_panel_snapshot(ctx, limit=12)

        self.assertEqual(12, len(sliced["items"]))
        self.assertEqual("0", sliced["items"][0]["id"])


if __name__ == "__main__":
    unittest.main()
