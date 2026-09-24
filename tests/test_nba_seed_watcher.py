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
from runtime import nba_watcher
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


class FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


def make_settings(snapshot_path: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        redis_url="redis://test/0",
        redis_prefix="polydata:",
        snapshot_sqlite_path=snapshot_path,
        sports_runtime_ttl_seconds=60,
        espn_nba_base_url="https://example.test/nba",
        espn_core_nba_base_url="https://example.test/core/nba",
        nba_lineups_base_url="https://example.test/lineups",
        nba_official_base_url="https://www.nba.com",
    )


def scoreboard_payload(*, item_id: str = "game-1") -> dict:
    return {
        "items": [{"id": item_id, "name": "LAL @ HOU", "homeTeam": "Houston Rockets", "awayTeam": "Los Angeles Lakers"}],
        "generatedAt": "2026-05-03T08:00:00Z",
        "status": "ok",
        "source": "ESPN NBA Scoreboard",
    }


def intel_payload(*, item_id: str = "news-1") -> dict:
    return {
        "items": [{"headline": item_id, "source": "ESPN", "type": "news"}],
        "lineups": [{"gameId": "game-1", "label": "LAL @ HOU", "starters": []}],
        "generatedAt": "2026-05-03T08:00:00Z",
        "status": "ok",
        "source": "ESPN NBA Intel",
    }


def predictor_payload(*, item_id: str = "game-1") -> dict:
    return {
        "items": [{"eventId": item_id, "awayWinProbability": 42.0, "homeWinProbability": 58.0}],
        "generatedAt": "2026-05-03T08:00:00Z",
        "status": "ok",
        "source": "ESPN Matchup Predictor",
    }


class NbaSeedWatcherTestCase(unittest.TestCase):
    def make_watcher(self) -> tuple[nba_watcher.NbaWatcher, FakeRedis, tempfile.TemporaryDirectory]:
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        fake_redis = FakeRedis()
        settings = make_settings(str(Path(snapshot_dir.name) / "snapshots.sqlite3"))
        redis_module = SimpleNamespace(from_url=lambda *args, **kwargs: fake_redis)
        requests_module = SimpleNamespace(Session=lambda: FakeSession())
        with patch.object(nba_watcher, "redis", redis_module), patch.object(nba_watcher, "requests", requests_module):
            watcher = nba_watcher.NbaWatcher(
                redis_url=settings.redis_url,
                redis_prefix=settings.redis_prefix,
                snapshot_sqlite_path=settings.snapshot_sqlite_path,
                settings=settings,
                scoreboard_limit=10,
                intel_limit=12,
                predictor_limit=8,
                interval_seconds=60,
            )
        return watcher, fake_redis, snapshot_dir

    def test_watcher_stores_three_payloads_and_seed_meta(self):
        watcher, fake_redis, _ = self.make_watcher()
        with patch.object(runtime_service, "fetch_live_nba_scoreboard_payload", return_value=scoreboard_payload()), patch.object(
            runtime_service, "fetch_live_nba_intel_payload", return_value=intel_payload()
        ), patch.object(runtime_service, "fetch_live_nba_matchup_predictor_payload", return_value=predictor_payload()):
            result = watcher.run_once()

        self.assertEqual("ok", result["status"])
        scoreboard_key = watcher.redis_key(
            runtime_service.NBA_SCOREBOARD_NAMESPACE,
            runtime_service.build_nba_scoreboard_cache_key(limit=10),
        )
        stored_scoreboard = json.loads(fake_redis.get(scoreboard_key) or "{}")
        self.assertEqual("seeded", stored_scoreboard["cacheMode"])
        self.assertEqual("game-1", stored_scoreboard["items"][0]["id"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:sports:nba") or "{}")
        self.assertEqual("ok", meta["status"])
        self.assertEqual(4, meta["recordCount"])
        self.assertEqual({"scoreboard": "ok", "intel": "ok", "predictor": "ok"}, meta["sourceStates"])

    def test_watcher_preserves_previous_component_when_new_payload_is_empty(self):
        watcher, fake_redis, _ = self.make_watcher()
        namespace = runtime_service.NBA_SCOREBOARD_NAMESPACE
        cache_key = runtime_service.build_nba_scoreboard_cache_key(limit=10)
        watcher.store_payload(namespace, cache_key, {**scoreboard_payload(item_id="old-game"), "cacheMode": "seeded"})

        with patch.object(runtime_service, "fetch_live_nba_scoreboard_payload", return_value={"items": [], "generatedAt": "x"}), patch.object(
            runtime_service, "fetch_live_nba_intel_payload", return_value=intel_payload()
        ), patch.object(runtime_service, "fetch_live_nba_matchup_predictor_payload", return_value=predictor_payload()):
            result = watcher.run_once()

        self.assertEqual("degraded", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key(namespace, cache_key)) or "{}")
        self.assertEqual("old-game", stored["items"][0]["id"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:sports:nba") or "{}")
        self.assertEqual("preserved", meta["sourceStates"]["scoreboard"])

    def test_api_reads_seeded_nba_snapshot_without_live_fetch(self):
        with tempfile.TemporaryDirectory() as snapshot_dir:
            settings = make_settings(str(Path(snapshot_dir) / "snapshots.sqlite3"))
            store = SnapshotStore(settings.snapshot_sqlite_path)
            cache_key = runtime_service.build_nba_scoreboard_cache_key(limit=10)
            store.set(runtime_service.NBA_SCOREBOARD_NAMESPACE, cache_key, scoreboard_payload(), 300)
            cached: dict[tuple[str, str], dict] = {}
            ctx = {
                "SETTINGS": settings,
                "SPORTS_RUNTIME_TTL_SECONDS": 60,
                "SNAPSHOT_STORE": store,
                "get_cached_json": lambda namespace, key: cached.get((namespace, key)),
                "set_cached_json": lambda namespace, key, payload, ttl: cached.__setitem__((namespace, key), payload),
                "http_json_get": lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live fetch should not run")),
                "utc_now_iso": lambda: "2026-05-03T00:00:00Z",
            }
            payload = runtime_service.get_nba_scoreboard_snapshot(ctx, limit=10)

        self.assertEqual("sqlite-seed", payload["cacheMode"])
        self.assertEqual("game-1", payload["items"][0]["id"])


if __name__ == "__main__":
    unittest.main()
