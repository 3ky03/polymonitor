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

from api.services import runtime_service
from runtime import inflation_nowcast_watcher
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
        finance_runtime_ttl_seconds=300,
        cleveland_fed_nowcast_url="https://example.test/nowcast",
    )


def sample_payload() -> dict:
    return {
        "monthOverMonth": {"Month": "May", "CPI": "0.2"},
        "yearOverYear": {"Month": "May", "CPI": "2.5"},
        "quarterly": [{"Quarter": "Q2", "CPI": "2.4"}],
        "generatedAt": "2026-05-03T08:00:00Z",
        "source": "Cleveland Fed Inflation Nowcasting",
        "url": "https://example.test/nowcast",
        "status": "ok",
    }


class InflationNowcastSeedWatcherTestCase(unittest.TestCase):
    def make_watcher(self):
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        fake_redis = FakeRedis()
        settings = make_settings(str(Path(snapshot_dir.name) / "snapshots.sqlite3"))
        redis_module = SimpleNamespace(from_url=lambda *args, **kwargs: fake_redis)
        requests_module = SimpleNamespace(Session=lambda: SimpleNamespace())
        with patch.object(inflation_nowcast_watcher, "redis", redis_module), patch.object(
            inflation_nowcast_watcher, "requests", requests_module
        ), patch.object(inflation_nowcast_watcher, "BeautifulSoup", object):
            watcher = inflation_nowcast_watcher.InflationNowcastWatcher(
                redis_url=settings.redis_url,
                redis_prefix=settings.redis_prefix,
                snapshot_sqlite_path=settings.snapshot_sqlite_path,
                settings=settings,
                interval_seconds=1800,
            )
        return watcher, fake_redis

    def test_watcher_stores_payload_and_seed_meta(self):
        watcher, fake_redis = self.make_watcher()
        with patch.object(runtime_service, "fetch_live_inflation_nowcast_payload", return_value=sample_payload()):
            result = watcher.run_once()

        self.assertEqual("ok", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
        self.assertEqual("seeded", stored["cacheMode"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:macro:inflation-nowcast") or "{}")
        self.assertEqual("ok", meta["status"])
        self.assertEqual(1, meta["recordCount"])

    def test_api_reads_seeded_payload_without_live_fetch(self):
        with tempfile.TemporaryDirectory() as snapshot_dir:
            settings = make_settings(str(Path(snapshot_dir) / "snapshots.sqlite3"))
            store = SnapshotStore(settings.snapshot_sqlite_path)
            store.set(runtime_service.INFLATION_NOWCAST_NAMESPACE, runtime_service.INFLATION_NOWCAST_CACHE_KEY, sample_payload(), 3600)
            ctx = {
                "SETTINGS": settings,
                "FINANCE_RUNTIME_TTL_SECONDS": 300,
                "SNAPSHOT_STORE": store,
                "get_cached_json": lambda namespace, key: None,
                "set_cached_json": lambda namespace, key, payload, ttl: None,
                "requests": None,
                "BeautifulSoup": None,
                "app": SimpleNamespace(logger=SimpleNamespace(exception=lambda *args, **kwargs: None)),
                "utc_now_iso": lambda: "2026-05-03T00:00:00Z",
            }
            with patch.object(runtime_service, "fetch_live_inflation_nowcast_payload", side_effect=AssertionError("live fetch should not run")):
                payload = runtime_service.get_inflation_nowcast_snapshot(ctx)

        self.assertEqual("sqlite-seed", payload["cacheMode"])
        self.assertEqual("0.2", payload["monthOverMonth"]["CPI"])


if __name__ == "__main__":
    unittest.main()
