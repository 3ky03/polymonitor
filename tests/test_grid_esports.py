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

from api.services import grid_esports_service
from runtime import grid_esports_watcher
from runtime.snapshot_store import SnapshotStore


class FakeLogger:
    def exception(self, *args, **kwargs) -> None:
        return None


class FakeApp:
    logger = FakeLogger()


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


def make_settings(snapshot_path: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        redis_url="redis://test/0",
        redis_prefix="polydata:",
        snapshot_sqlite_path=snapshot_path,
        grid_central_data_graphql_url="fixture-central",
        grid_series_state_graphql_url="fixture-state",
        grid_api_key="fixture-key",
        grid_source_url="https://grid.gg/open-access/",
        grid_esports_ttl_seconds=120,
        grid_esports_lookback_days=1,
        grid_esports_lookahead_days=2,
        grid_esports_pm_search_enabled=True,
    )


def all_series_payload() -> dict:
    return {
        "data": {
            "allSeries": {
                "totalCount": 2,
                "edges": [
                    {
                        "node": {
                            "id": "series-1",
                            "startTimeScheduled": "2026-05-12T12:00:00Z",
                            "teams": [
                                {"baseInfo": {"id": "a", "name": "Alpha"}},
                                {"baseInfo": {"id": "b", "name": "Beta"}},
                            ],
                            "tournament": {"id": "t1", "name": "Open Circuit"},
                            "title": {"id": "cs2", "nameShortened": "CS2"},
                        }
                    },
                    {
                        "node": {
                            "id": "series-2",
                            "startTimeScheduled": "2026-05-13T12:00:00Z",
                            "teams": [
                                {"baseInfo": {"id": "c", "name": "Gamma"}},
                                {"baseInfo": {"id": "d", "name": "Delta"}},
                            ],
                            "tournament": {"id": "t2", "name": "Dota Circuit"},
                            "title": {"id": "dota", "nameShortened": "DOTA2"},
                        }
                    },
                ],
            }
        }
    }


def state_payload(*, started: bool = True, finished: bool = False) -> dict:
    return {
        "data": {
            "seriesState": {
                "startedAt": "2026-05-12T12:05:00Z" if started else None,
                "started": started,
                "finished": finished,
                "teams": [
                    {"won": False, "score": 1, "kills": 24, "deaths": 18, "players": []},
                    {"won": False, "score": 0, "kills": 18, "deaths": 24, "players": []},
                ],
            }
        }
    }


class GridEsportsServiceTestCase(unittest.TestCase):
    def make_context(self, *, empty: bool = False, state_errors: bool = False, missing_key: bool = False) -> dict:
        settings = make_settings()
        if missing_key:
            settings.grid_api_key = ""

        def http_json_post(url, payload, timeout=15, headers=None):
            self.assertNotIn("fixture-key", json.dumps(payload))
            if url == "fixture-central":
                return {"data": {"allSeries": {"totalCount": 0, "edges": []}}} if empty else all_series_payload()
            if url == "fixture-state":
                if state_errors:
                    return {"errors": [{"message": "state unavailable"}]}
                return state_payload()
            raise RuntimeError(f"unexpected url {url}")

        return {
            "SETTINGS": settings,
            "app": FakeApp(),
            "http_json_post": http_json_post,
            "search_markets": lambda query, limit=3: [{"id": 7, "title": query, "latestYesPrice": "0.62"}],
            "get_snapshot_payload": lambda namespace, cache_key, builder, ttl_seconds=60: builder(),
            "utc_now_iso": lambda: "2026-05-12T00:00:00Z",
        }

    def test_live_payload_normalizes_grid_series_state_and_pm_context(self):
        payload = grid_esports_service.get_grid_esports_snapshot(self.make_context(), limit=2)

        self.assertEqual("ok", payload["status"])
        self.assertEqual(2, len(payload["items"]))
        first = payload["items"][0]
        self.assertEqual("Alpha vs Beta", first["series"])
        self.assertEqual("live", first["state"])
        self.assertEqual("1-0", first["score"])
        self.assertEqual(99, first["momentum"])
        self.assertEqual("matched", first["pm"]["status"])
        self.assertEqual(2, payload["summary"]["officialSnapshots"])

    def test_pm_context_accepts_search_payload_dict(self):
        ctx = self.make_context()
        ctx["search_markets"] = lambda query, limit=3: {"items": [{"id": 8, "title": query, "latestPrice": "0.51"}]}

        payload = grid_esports_service.get_grid_esports_snapshot(ctx, limit=1)

        self.assertEqual("ok", payload["status"])
        self.assertEqual("matched", payload["items"][0]["pm"]["status"])
        self.assertEqual(0.51, payload["items"][0]["pm"]["probability"])

    def test_pm_context_prefers_match_winner_over_handicap_market(self):
        ctx = self.make_context()
        limits = []

        def search_markets(query, limit=3):
            limits.append(limit)
            return {
                "items": [
                    {"id": 1877091, "title": "Map 1 Rounds Handicap: Legacy (-9.5) vs Monte (+9.5)", "latestPrice": "0.45"},
                    {"id": 1876693, "title": "Map 1 Rounds Handicap: Monte (-3.5) vs Legacy (+3.5)", "latestPrice": "0.52"},
                    {"id": 1876760, "title": "Map 1 Rounds Handicap: Legacy (-6.5) vs Monte (+6.5)", "latestPrice": "0.49"},
                    {"id": 1871322, "title": "Map 1 Rounds Handicap: Legacy (-3.5) vs Monte (+3.5)", "latestPrice": "0.47"},
                    {"id": 1871323, "title": "Counter-Strike: Monte vs Legacy (BO1) - IEM Cologne Major Stage 2", "latestPrice": "0.58"},
                ][:limit]
            }

        ctx["search_markets"] = search_markets

        payload = grid_esports_service.get_grid_esports_snapshot(ctx, limit=1)

        self.assertEqual("matched", payload["items"][0]["pm"]["status"])
        self.assertEqual(1871323, payload["items"][0]["pm"]["marketId"])
        self.assertEqual(0.58, payload["items"][0]["pm"]["probability"])
        self.assertEqual([grid_esports_service.DEFAULT_GRID_PM_SEARCH_LIMIT], limits)

    def test_live_payload_is_degraded_when_series_state_errors(self):
        payload = grid_esports_service.get_grid_esports_snapshot(self.make_context(state_errors=True), limit=2)

        self.assertEqual("degraded", payload["status"])
        self.assertEqual("empty", payload["sources"]["gridSeriesState"])
        self.assertEqual("pending-state", payload["items"][0]["state"])

    def test_missing_key_returns_renderable_degraded_payload(self):
        payload = grid_esports_service.get_grid_esports_snapshot(self.make_context(missing_key=True), limit=2)

        self.assertEqual("degraded", payload["status"])
        self.assertEqual("missing-key", payload["sources"]["gridCentralData"])
        self.assertEqual([], payload["items"])

    def test_central_data_failure_returns_renderable_degraded_payload(self):
        ctx = self.make_context()

        def fail_post(url, payload, timeout=15, headers=None):
            raise TimeoutError("central timeout")

        ctx["http_json_post"] = fail_post
        payload = grid_esports_service.get_grid_esports_snapshot(ctx, limit=2)

        self.assertEqual("degraded", payload["status"])
        self.assertEqual("error", payload["sources"]["gridCentralData"])
        self.assertEqual([], payload["items"])

    def test_api_reads_seeded_sqlite_snapshot_without_live_fetch(self):
        with tempfile.TemporaryDirectory() as snapshot_dir:
            settings = make_settings(str(Path(snapshot_dir) / "snapshots.sqlite3"))
            store = SnapshotStore(settings.snapshot_sqlite_path)
            cache_key = grid_esports_service.build_grid_esports_cache_key(settings, limit=10)
            seeded = {
                "generatedAt": "2026-05-12T00:00:00Z",
                "source": "GRID Open Access",
                "status": "ok",
                "items": [{"id": "seeded", "series": "Seed A vs Seed B", "state": "live"}],
            }
            store.set(grid_esports_service.GRID_ESPORTS_NAMESPACE, cache_key, seeded, 300)
            ctx = {
                "SETTINGS": settings,
                "SNAPSHOT_STORE": store,
                "get_cached_json": lambda namespace, key: None,
                "set_cached_json": lambda namespace, key, payload, ttl: None,
                "utc_now_iso": lambda: "2026-05-12T00:00:00Z",
            }
            with patch.object(grid_esports_service, "fetch_live_grid_esports_payload", side_effect=AssertionError("live fetch should not run")):
                payload = grid_esports_service.get_grid_esports_snapshot(ctx, limit=10)

        self.assertEqual("sqlite-seed", payload["cacheMode"])
        self.assertEqual("seeded", payload["items"][0]["id"])


class GridEsportsWatcherTestCase(unittest.TestCase):
    def make_watcher(self) -> tuple[grid_esports_watcher.GridEsportsWatcher, FakeRedis, tempfile.TemporaryDirectory]:
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        fake_redis = FakeRedis()
        settings = make_settings(str(Path(snapshot_dir.name) / "snapshots.sqlite3"))
        redis_module = SimpleNamespace(from_url=lambda *args, **kwargs: fake_redis)
        with patch.object(grid_esports_watcher, "redis", redis_module):
            watcher = grid_esports_watcher.GridEsportsWatcher(
                redis_url=settings.redis_url,
                redis_prefix=settings.redis_prefix,
                snapshot_sqlite_path=settings.snapshot_sqlite_path,
                settings=settings,
                limit=10,
                interval_seconds=120,
            )
        return watcher, fake_redis, snapshot_dir

    def test_watcher_stores_payload_snapshot_and_seed_meta(self):
        watcher, fake_redis, _ = self.make_watcher()
        payload = {
            "generatedAt": "2026-05-12T00:00:00Z",
            "source": "GRID Open Access",
            "status": "ok",
            "sources": {"gridCentralData": "ok"},
            "items": [{"id": "series-1", "series": "Alpha vs Beta"}],
        }
        with patch.object(grid_esports_service, "fetch_live_grid_esports_payload", return_value=payload):
            result = watcher.run_once()

        self.assertEqual("ok", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
        self.assertEqual("seeded", stored["cacheMode"])
        snapshot = watcher.snapshot_store.get_stale(watcher.namespace(), watcher.cache_key())
        self.assertEqual("series-1", snapshot["items"][0]["id"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:esports:esports-intel") or "{}")
        self.assertEqual("ok", meta["status"])
        self.assertEqual("polydata-grid-esports-seed.service", meta["serviceName"])

    def test_watcher_injects_market_search_when_pm_matching_enabled(self):
        watcher, _, _ = self.make_watcher()
        seen_queries: list[str] = []

        def fake_search(query: str, limit: int = 10) -> dict:
            seen_queries.append(query)
            return {"items": [{"id": 7, "title": query, "latestPrice": "0.61"}]}

        def fake_fetch(ctx: dict, limit: int = 10) -> dict:
            self.assertIn("search_markets", ctx)
            result = ctx["search_markets"]("Alpha vs Beta", limit=3)
            return {
                "generatedAt": "2026-05-12T00:00:00Z",
                "status": "ok",
                "items": [{"id": "series-1", "pm": result["items"][0]}],
            }

        with (
            patch.object(grid_esports_watcher, "build_market_search", return_value=fake_search),
            patch.object(grid_esports_service, "fetch_live_grid_esports_payload", side_effect=fake_fetch),
        ):
            result = watcher.run_once()

        self.assertEqual("ok", result["status"])
        self.assertEqual(["Alpha vs Beta"], seen_queries)

    def test_watcher_preserves_previous_snapshot_when_new_payload_is_empty(self):
        watcher, fake_redis, _ = self.make_watcher()
        previous = {
            "generatedAt": "2026-05-12T00:00:00Z",
            "source": "GRID Open Access",
            "status": "ok",
            "items": [{"id": "old-series"}],
            "cacheMode": "seeded",
        }
        watcher.store_payload(previous)

        empty_payload = {
            "generatedAt": "2026-05-12T00:01:00Z",
            "source": "GRID Open Access",
            "status": "empty",
            "sources": {"gridCentralData": "ok"},
            "items": [],
        }
        with patch.object(grid_esports_service, "fetch_live_grid_esports_payload", return_value=empty_payload):
            result = watcher.run_once()

        self.assertEqual("preserved", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
        self.assertEqual("old-series", stored["items"][0]["id"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:esports:esports-intel") or "{}")
        self.assertEqual("preserved", meta["status"])
        self.assertIn("Preserved previous GRID esports snapshot", meta["errorSummary"])


if __name__ == "__main__":
    unittest.main()
