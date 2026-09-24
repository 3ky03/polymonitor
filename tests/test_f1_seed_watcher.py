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

from api.services import f1_runtime_service
from runtime import f1_watcher
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
        sports_runtime_ttl_seconds=60,
        f1_bwenews_rss_url="https://example.test/f1.xml",
        f1_bwenews_source_url="https://example.test/f1",
    )


def sample_payload(*, card_id: str = "f1-1") -> dict:
    return {
        "generatedAt": "2026-05-03T08:00:00Z",
        "season": 2026,
        "source": "bwenews-rss",
        "sourceUrl": "https://example.test/f1",
        "status": "ok",
        "focusMeeting": None,
        "cards": [{"id": card_id, "title": "F1 race control monitors live weather swing"}],
        "items": [{"id": card_id, "title": "F1 race control monitors live weather swing"}],
    }


class F1SeedWatcherTestCase(unittest.TestCase):
    def make_watcher(self) -> tuple[f1_watcher.F1Watcher, FakeRedis, tempfile.TemporaryDirectory]:
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        fake_redis = FakeRedis()
        settings = make_settings(str(Path(snapshot_dir.name) / "snapshots.sqlite3"))
        redis_module = SimpleNamespace(from_url=lambda *args, **kwargs: fake_redis)
        requests_module = SimpleNamespace()
        with patch.object(f1_watcher, "redis", redis_module), patch.object(f1_watcher, "requests", requests_module):
            watcher = f1_watcher.F1Watcher(
                redis_url=settings.redis_url,
                redis_prefix=settings.redis_prefix,
                snapshot_sqlite_path=settings.snapshot_sqlite_path,
                settings=settings,
                limit=10,
                interval_seconds=180,
            )
        return watcher, fake_redis, snapshot_dir

    def test_watcher_stores_payload_snapshot_and_seed_meta(self):
        watcher, fake_redis, _ = self.make_watcher()
        with patch.object(f1_runtime_service, "build_f1_panel_payload", return_value=sample_payload()):
            result = watcher.run_once()

        self.assertEqual("stored", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
        self.assertEqual("seeded", stored["cacheMode"])
        self.assertEqual(1, len(stored["cards"]))
        snapshot = watcher.snapshot_store.get_stale(watcher.namespace(), watcher.cache_key())
        self.assertEqual("f1-1", snapshot["cards"][0]["id"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:sports:f1-trackside") or "{}")
        self.assertEqual("ok", meta["status"])
        self.assertEqual(1, meta["recordCount"])
        self.assertEqual("polydata-f1-seed.service", meta["serviceName"])

    def test_watcher_preserves_previous_snapshot_when_new_payload_is_empty(self):
        watcher, fake_redis, _ = self.make_watcher()
        previous = {**sample_payload(card_id="old-card"), "cacheMode": "seeded"}
        watcher.store_payload(previous)

        with patch.object(f1_runtime_service, "build_f1_panel_payload", return_value={**sample_payload(), "cards": [], "items": [], "status": "empty"}):
            result = watcher.run_once()

        self.assertEqual("preserved", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
        self.assertEqual("old-card", stored["cards"][0]["id"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:sports:f1-trackside") or "{}")
        self.assertEqual("preserved", meta["status"])
        self.assertIn("Preserved previous snapshot", meta["errorSummary"])

    def test_api_reads_seeded_sqlite_snapshot_without_live_fetch(self):
        with tempfile.TemporaryDirectory() as snapshot_dir:
            settings = make_settings(str(Path(snapshot_dir) / "snapshots.sqlite3"))
            store = SnapshotStore(settings.snapshot_sqlite_path)
            cache_key = f1_runtime_service.build_f1_cache_key(limit=10)
            store.set(f1_runtime_service.F1_SNAPSHOT_NAMESPACE, cache_key, sample_payload(), 300)
            cached: dict[tuple[str, str], dict] = {}
            ctx = {
                "SETTINGS": settings,
                "SPORTS_RUNTIME_TTL_SECONDS": 60,
                "SNAPSHOT_STORE": store,
                "get_cached_json": lambda namespace, key: cached.get((namespace, key)),
                "set_cached_json": lambda namespace, key, payload, ttl: cached.__setitem__((namespace, key), payload),
                "utc_now_iso": lambda: "2026-05-03T00:00:00Z",
                "app": SimpleNamespace(logger=SimpleNamespace(exception=lambda *args, **kwargs: None)),
                "requests": None,
            }
            with patch.object(f1_runtime_service, "build_f1_panel_payload", side_effect=AssertionError("live fetch should not run")):
                payload = f1_runtime_service.get_f1_panel_snapshot(ctx, limit=10)

        self.assertEqual("sqlite-seed", payload["cacheMode"])
        self.assertEqual("f1-1", payload["cards"][0]["id"])


if __name__ == "__main__":
    unittest.main()
