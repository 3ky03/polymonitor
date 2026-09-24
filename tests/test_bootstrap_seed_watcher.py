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

from api.services import bootstrap_service
from runtime import bootstrap_watcher


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value


def sample_bootstrap_payload() -> dict:
    return {
        "generatedAt": "2026-05-03T08:00:00Z",
        "defaultWorkspace": {"name": "Test", "panels": ["alpha-signal"]},
        "featuredMarket": {"id": 1},
        "activeMarketsPreview": [{"id": 1}],
        "globalTradesPreview": [{"id": "trade"}],
        "globalOraclePreview": [],
        "latestContentPreview": [],
        "commoditiesPreview": {"items": [{"id": "gold"}]},
        "recentTradesPreview": [],
        "oraclePreview": [],
        "contentPreview": [],
        "pricePreview": None,
        "systemHealth": {"apiStatus": "ok"},
    }


class BootstrapSeedWatcherTestCase(unittest.TestCase):
    def make_watcher(self):
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        fake_redis = FakeRedis()
        redis_module = SimpleNamespace(from_url=lambda *args, **kwargs: fake_redis)
        with patch.object(bootstrap_watcher, "redis", redis_module):
            watcher = bootstrap_watcher.BootstrapWatcher(
                redis_url="redis://test/0",
                redis_prefix="polydata:",
                snapshot_sqlite_path=str(Path(snapshot_dir.name) / "snapshots.sqlite3"),
                interval_seconds=60,
            )
        return watcher, fake_redis

    def test_watcher_stores_bootstrap_payload_and_seed_meta(self):
        watcher, fake_redis = self.make_watcher()
        with patch.object(watcher, "fetch_payload", return_value=sample_bootstrap_payload()):
            result = watcher.run_once()

        self.assertEqual("ok", result["status"])
        stored = json.loads(fake_redis.get("polydata:bootstrap:workspace-default-v9") or "{}")
        self.assertEqual("seeded", stored["cacheMode"])
        self.assertEqual("ok", stored["status"])
        snapshot = watcher.snapshot_store.get(bootstrap_service.BOOTSTRAP_SNAPSHOT_NAMESPACE, bootstrap_service.BOOTSTRAP_CACHE_KEY)
        self.assertEqual("seeded", snapshot["cacheMode"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:bootstrap:bootstrap") or "{}")
        self.assertEqual("ok", meta["status"])
        self.assertGreaterEqual(meta["recordCount"], 2)

    def test_watcher_preserves_previous_payload_when_new_payload_is_invalid(self):
        watcher, fake_redis = self.make_watcher()
        previous = {**sample_bootstrap_payload(), "cacheMode": "seeded", "status": "ok"}
        watcher.store_payload(previous)
        with patch.object(watcher, "fetch_payload", return_value={"generatedAt": "new"}):
            result = watcher.run_once()

        self.assertEqual("preserved", result["status"])
        stored = json.loads(fake_redis.get("polydata:bootstrap:workspace-default-v9") or "{}")
        self.assertEqual("Test", stored["defaultWorkspace"]["name"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:bootstrap:bootstrap") or "{}")
        self.assertEqual("preserved", meta["status"])


if __name__ == "__main__":
    unittest.main()
