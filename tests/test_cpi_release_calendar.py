from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import patch

from flask import Flask


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.routes.runtime_panels import create_runtime_panels_blueprint
from api.services import cpi_release_calendar_service
from runtime import cpi_release_calendar_watcher
from runtime.snapshot_store import SnapshotStore


class FakeLogger:
    def exception(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def info(self, *args, **kwargs) -> None:
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

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.set(key, value, ex=ttl)


BLS_CPI_HTML = """
Schedule of Releases for the Consumer Price Index
Reference Month Release Date Release Time
April 2026 May 12, 2026 08:30 AM
May 2026 Jun. 10, 2026 08:30 AM
"""

BLS_JOBS_HTML = """
Schedule of Releases for the Employment Situation
Reference Month Release Date Release Time
April 2026 May 08, 2026 08:30 AM
May 2026 Jun. 05, 2026 08:30 AM
"""

BEA_HTML = """
Release Schedule
Year 2026 Release
May 28
8:30 AM
N ews
Personal Income and Outlays, April 2026
June 25
8:30 AM
N ews
Personal Income and Outlays, May 2026
"""

FOMC_HTML = """
2026 FOMC Meetings
January
27-28
March
17-18*
April
28-29
June
16-17*
July
28-29
2025 FOMC Meetings
"""


def make_settings(snapshot_path: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        redis_url="redis://test/0",
        redis_prefix="polydata:",
        snapshot_sqlite_path=snapshot_path,
        cpi_calendar_bls_cpi_url="https://bls.example/cpi",
        cpi_calendar_bls_employment_url="https://bls.example/empsit",
        cpi_calendar_bea_schedule_url="https://bea.example/schedule",
        cpi_calendar_fomc_url="https://fed.example/fomc",
        cpi_calendar_source_url="https://bls.example/cpi",
        cpi_calendar_ttl_seconds=3600,
        gamma_api_base="https://gamma.example",
        polymarket_macro_map_source_url="https://gamma.example",
        polymarket_macro_map_ttl_seconds=180,
        polymarket_macro_map_search_terms=(),
    )


def make_context(*, http_text_get=None, snapshot_store=None, cached=None, macro_payload=None) -> Dict[str, Any]:
    cached = cached if cached is not None else {}
    settings = make_settings()
    fixtures = {
        settings.cpi_calendar_bls_cpi_url: BLS_CPI_HTML,
        settings.cpi_calendar_bls_employment_url: BLS_JOBS_HTML,
        settings.cpi_calendar_bea_schedule_url: BEA_HTML,
        settings.cpi_calendar_fomc_url: FOMC_HTML,
    }

    def default_text_get(url, **kwargs):
        return fixtures[url]

    if macro_payload is None:
        macro_payload = {
            "items": [
                {
                    "eventId": "evt-cpi",
                    "title": "Will CPI be 0.3% or higher?",
                    "slug": "cpi-03",
                    "categoryIds": ["cpi"],
                    "topOutcomes": [{"label": "0.3% or higher", "yesPrice": 0.57}],
                }
            ]
        }

    return {
        "SETTINGS": settings,
        "app": FakeApp(),
        "utc_now_iso": lambda: "2026-05-11T00:00:00Z",
        "http_text_get": http_text_get or default_text_get,
        "BeautifulSoup": None,
        "SNAPSHOT_STORE": snapshot_store,
        "get_cached_json": lambda namespace, key: cached.get((namespace, key)),
        "set_cached_json": lambda namespace, key, payload, ttl: cached.__setitem__((namespace, key), payload),
        "get_polymarket_macro_map_snapshot": lambda limit=20: macro_payload,
    }


def test_build_payload_maps_official_releases_and_pmkt_baseline():
    payload = cpi_release_calendar_service.get_cpi_release_calendar_snapshot(make_context(), limit=8)

    assert payload["status"] == "ok"
    assert payload["sources"]["blsCpi"] == "ok"
    assert payload["summary"]["nextCpi"]["referencePeriod"] == "April 2026"
    assert payload["summary"]["nextPce"]["referencePeriod"] == "April 2026"
    assert payload["summary"]["nextFomc"]["referencePeriod"] == "June 16-17, 2026"
    assert payload["summary"]["baselineProbability"] == 0.57
    assert payload["consensus"]["status"] == "optional-unavailable"


def test_connection_failure_returns_renderable_warming_payload():
    payload = cpi_release_calendar_service.get_cpi_release_calendar_snapshot(
        make_context(http_text_get=lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("down"))),
        limit=8,
    )

    assert payload["status"] == "degraded"
    assert payload["sources"]["blsCpi"] == "fallback"
    assert payload["sources"]["beaPce"] == "error"
    assert payload["items"]


def test_timeout_returns_renderable_payload():
    payload = cpi_release_calendar_service.get_cpi_release_calendar_snapshot(
        make_context(http_text_get=lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timeout"))),
        limit=8,
    )

    assert payload["status"] == "degraded"
    assert payload["sources"]["beaPce"] == "error"


def test_non_200_error_returns_renderable_payload():
    payload = cpi_release_calendar_service.get_cpi_release_calendar_snapshot(
        make_context(http_text_get=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("http 500"))),
        limit=8,
    )

    assert payload["status"] == "degraded"
    assert payload["sources"]["fomc"] == "error"


def test_invalid_empty_source_shape_does_not_crash():
    payload = cpi_release_calendar_service.get_cpi_release_calendar_snapshot(make_context(http_text_get=lambda *args, **kwargs: "<html></html>"), limit=8)

    assert payload["status"] == "degraded"
    assert payload["sources"]["blsCpi"] == "fallback"
    assert payload["items"]


def test_stale_snapshot_can_be_returned_by_cache_layer():
    stale = {
        "generatedAt": "2026-05-10T00:00:00Z",
        "status": "ok",
        "sources": {"blsCpi": "ok"},
        "summary": {"signal": "STALE EVENT", "nextCpi": {"referencePeriod": "April 2026"}},
        "baseline": {"status": "market-implied", "probability": 0.51},
        "items": [{"id": "stale-cpi", "kind": "cpi", "releaseAt": "2026-05-12T12:30:00Z"}],
    }

    class StaleOnlyStore:
        def get(self, namespace, cache_key):
            return None

        def get_stale(self, namespace, cache_key):
            return stale

        def set(self, *args, **kwargs):
            return None

    payload = cpi_release_calendar_service.get_cpi_release_calendar_snapshot(make_context(snapshot_store=StaleOnlyStore()), limit=8)

    assert payload["cacheMode"] == "stale-seed"
    assert payload["items"][0]["id"] == "stale-cpi"


def test_api_reads_seeded_sqlite_snapshot_without_live_fetch(tmp_path):
    seeded = {
        "generatedAt": "2026-05-11T00:00:00Z",
        "status": "ok",
        "sources": {"blsCpi": "ok"},
        "summary": {"signal": "SEEDED CPI EVENT"},
        "baseline": {"status": "market-implied", "probability": 0.55},
        "items": [{"id": "seeded-cpi", "kind": "cpi", "releaseAt": "2026-05-12T12:30:00Z"}],
    }
    store = SnapshotStore(str(tmp_path / "snapshots.sqlite3"))
    store.set(cpi_release_calendar_service.CPI_CALENDAR_SNAPSHOT_NAMESPACE, cpi_release_calendar_service.CPI_CALENDAR_CACHE_KEY, seeded, 300)
    ctx = make_context(snapshot_store=store, http_text_get=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live fetch should not run")))

    payload = cpi_release_calendar_service.get_cpi_release_calendar_snapshot(ctx, limit=8)

    assert payload["cacheMode"] == "sqlite-seed"
    assert payload["items"][0]["id"] == "seeded-cpi"


def make_watcher(tmp_path) -> tuple[cpi_release_calendar_watcher.CpiReleaseCalendarWatcher, FakeRedis]:
    fake_redis = FakeRedis()
    settings = make_settings(str(tmp_path / "snapshots.sqlite3"))
    redis_module = SimpleNamespace(from_url=lambda *args, **kwargs: fake_redis)
    session = SimpleNamespace(headers={}, get=lambda *args, **kwargs: None)
    requests_module = SimpleNamespace(Session=lambda: session)
    with patch.object(cpi_release_calendar_watcher, "redis", redis_module), patch.object(cpi_release_calendar_watcher, "requests", requests_module):
        watcher = cpi_release_calendar_watcher.CpiReleaseCalendarWatcher(
            redis_url=settings.redis_url,
            redis_prefix=settings.redis_prefix,
            snapshot_sqlite_path=settings.snapshot_sqlite_path,
            settings=settings,
            interval_seconds=3600,
        )
    return watcher, fake_redis


def sample_payload(item_id: str = "cpi-1") -> Dict[str, Any]:
    return {
        "generatedAt": "2026-05-11T00:00:00Z",
        "source": "BLS / BEA / Federal Reserve / Polymarket",
        "sourceUrl": "https://bls.example/cpi",
        "status": "ok",
        "sources": {"blsCpi": "ok"},
        "summary": {"signal": "EVENT RISK HIGH"},
        "baseline": {"status": "market-implied", "probability": 0.57},
        "items": [{"id": item_id, "kind": "cpi", "releaseAt": "2026-05-12T12:30:00Z"}],
    }


def test_watcher_stores_payload_snapshot_and_seed_meta(tmp_path):
    watcher, fake_redis = make_watcher(tmp_path)

    with patch.object(cpi_release_calendar_service, "build_cpi_release_calendar_payload", return_value=sample_payload()):
        result = watcher.run_once()

    assert result["status"] == "stored"
    stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
    assert stored["cacheMode"] == "seeded"
    assert stored["items"][0]["id"] == "cpi-1"
    snapshot = watcher.snapshot_store.get_stale(watcher.namespace(), watcher.cache_key())
    assert snapshot["items"][0]["id"] == "cpi-1"
    meta = json.loads(fake_redis.get("polydata:seed-meta:macro:cpi-release-calendar") or "{}")
    assert meta["status"] == "ok"
    assert meta["recordCount"] == 1
    assert meta["serviceName"] == "polydata-cpi-release-calendar-seed.service"


def test_watcher_preserves_previous_snapshot_when_new_payload_is_empty(tmp_path):
    watcher, fake_redis = make_watcher(tmp_path)
    previous = {**sample_payload("old-cpi"), "cacheMode": "seeded"}
    watcher.store_payload(previous)
    empty_payload = {**sample_payload("empty"), "status": "warming", "items": [], "summary": {"signal": "CALENDAR WARMING"}}

    with patch.object(cpi_release_calendar_service, "build_cpi_release_calendar_payload", return_value=empty_payload):
        result = watcher.run_once()

    assert result["status"] == "preserved"
    stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
    assert stored["items"][0]["id"] == "old-cpi"
    meta = json.loads(fake_redis.get("polydata:seed-meta:macro:cpi-release-calendar") or "{}")
    assert meta["status"] == "preserved"
    assert "Preserved previous snapshot" in meta["errorSummary"]


def test_runtime_route_clamps_limit_to_max():
    app = Flask(__name__)
    helpers = {
        "COMMODITY_SYMBOLS": [],
        "CRYPTO_SYMBOLS": [],
        "get_market_group_snapshot": lambda symbols, kind: {"kind": kind, "items": symbols},
        "get_polymarket_macro_map_snapshot": lambda limit=12: {"limit": limit},
        "get_cpi_release_calendar_snapshot": lambda limit=8: {"limit": limit},
        "get_f1_panel_snapshot": lambda limit=10: {"limit": limit},
        "get_geo_sanctions_shock_snapshot": lambda limit=6: {"limit": limit},
        "get_jin10_panel_snapshot": lambda limit=24: {"limit": limit},
        "get_nba_scoreboard_snapshot": lambda limit=10: {"limit": limit},
        "get_nba_intel_snapshot": lambda limit=12: {"limit": limit},
        "get_nba_matchup_predictor_snapshot": lambda limit=8: {"limit": limit},
        "get_inflation_nowcast_snapshot": lambda: {"items": []},
        "get_alpha_signal_snapshot": lambda limit=8: {"limit": limit},
        "get_crypto_funding_watch_snapshot": lambda limit=16: {"limit": limit},
        "get_energy_gasoline_shock_snapshot": lambda limit=6: {"limit": limit},
        "get_food_retail_basket_snapshot": lambda limit=8: {"limit": limit},
        "get_new_market_signals_snapshot": lambda limit=12: {"limit": limit},
        "get_whale_trades_snapshot": lambda limit=14: {"limit": limit},
        "get_suspicious_trades_snapshot": lambda limit=12: {"limit": limit},
    }
    app.register_blueprint(create_runtime_panels_blueprint(helpers))

    response = app.test_client().get("/runtime/macro/cpi-release-calendar?limit=99")

    assert response.status_code == 200
    assert response.get_json()["limit"] == 12
