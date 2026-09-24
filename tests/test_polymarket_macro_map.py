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
from api.services import polymarket_macro_map_service
from runtime import polymarket_macro_map_watcher
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


def macro_event(event_id: str = "evt-cpi") -> Dict[str, Any]:
    return {
        "id": event_id,
        "slug": "may-cpi-inflation",
        "title": "Will May CPI inflation be 0.3% or higher?",
        "active": True,
        "closed": False,
        "endDate": "2026-05-13T12:30:00Z",
        "volume24hr": 15000,
        "liquidity": 42000,
        "tags": [{"label": "Inflation"}],
        "markets": [
            {
                "id": "m-1",
                "question": "Will May CPI inflation be 0.3% or higher?",
                "active": True,
                "closed": False,
                "acceptingOrders": True,
                "outcomePrices": ["0.57", "0.43"],
                "volume24hr": 9000,
                "conditionId": "cond-1",
                "slug": "may-cpi-03",
            }
        ],
    }


def make_settings(snapshot_path: str = "", *, search_terms=()) -> SimpleNamespace:
    return SimpleNamespace(
        redis_url="redis://test/0",
        redis_prefix="polydata:",
        snapshot_sqlite_path=snapshot_path,
        gamma_api_base="https://gamma.example",
        polymarket_macro_map_source_url="https://gamma.example",
        polymarket_macro_map_ttl_seconds=180,
        polymarket_macro_map_search_terms=search_terms,
    )


def make_context(*, http_json_get=None, search_terms=(), snapshot_store=None, cached=None) -> Dict[str, Any]:
    cached = cached if cached is not None else {}
    settings = SimpleNamespace(
        gamma_api_base="https://gamma.example",
        polymarket_macro_map_source_url="https://gamma.example",
        polymarket_macro_map_ttl_seconds=180,
        polymarket_macro_map_search_terms=search_terms,
    )
    return {
        "SETTINGS": settings,
        "app": FakeApp(),
        "utc_now_iso": lambda: "2026-05-11T00:00:00Z",
        "http_json_get": http_json_get or (lambda *args, **kwargs: {"events": [macro_event()]}),
        "SNAPSHOT_STORE": snapshot_store,
        "get_cached_json": lambda namespace, key: cached.get((namespace, key)),
        "set_cached_json": lambda namespace, key, payload, ttl: cached.__setitem__((namespace, key), payload),
    }


def test_build_payload_maps_active_macro_events_to_categories():
    ctx = make_context()

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=8)

    assert payload["status"] == "ok"
    assert payload["sources"]["gammaEvents"] == "ok"
    assert payload["summary"]["activeCount"] == 1
    assert payload["summary"]["topCategory"] == "CPI / Inflation"
    assert payload["items"][0]["categoryIds"] == ["cpi"]
    assert payload["items"][0]["topOutcomes"][0]["yesPrice"] == 0.57


def test_limit_is_clamped_to_default_item_limit():
    events = [macro_event(f"evt-cpi-{index}") for index in range(20)]
    ctx = make_context(http_json_get=lambda *args, **kwargs: {"events": events})

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=99)

    assert len(payload["items"]) == polymarket_macro_map_service.DEFAULT_ITEM_LIMIT


def test_empty_matching_terms_return_renderable_empty_payload():
    ctx = make_context(search_terms=("not-a-macro-term",))

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=8)

    assert payload["status"] == "empty"
    assert payload["items"] == []
    assert payload["categories"]
    assert payload["summary"]["signal"] == "NO MACRO CLUSTER"


def test_connection_failure_returns_degraded_payload():
    def fail(*args, **kwargs):
        raise ConnectionError("network down")

    ctx = make_context(http_json_get=fail)

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=8)

    assert payload["status"] == "degraded"
    assert payload["sources"]["gammaEvents"] == "error"
    assert payload["items"] == []


def test_timeout_returns_degraded_payload():
    def fail(*args, **kwargs):
        raise TimeoutError("upstream timeout")

    ctx = make_context(http_json_get=fail)

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=8)

    assert payload["status"] == "degraded"
    assert payload["sources"]["gammaEvents"] == "error"


def test_non_200_error_returns_degraded_payload():
    def fail(*args, **kwargs):
        raise RuntimeError("http 500")

    ctx = make_context(http_json_get=fail)

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=8)

    assert payload["status"] == "degraded"
    assert payload["sources"]["gammaEvents"] == "error"


def test_invalid_json_shape_returns_degraded_payload():
    ctx = make_context(http_json_get=lambda *args, **kwargs: {"events": {"bad": "shape"}})

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=8)

    assert payload["status"] == "degraded"
    assert payload["sources"]["gammaEvents"] == "error"


def test_missing_fields_do_not_break_normalization():
    ctx = make_context(http_json_get=lambda *args, **kwargs: {"events": [{"id": "evt-fed", "title": "Fed rate decision", "markets": [{}]}]})

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=8)

    assert payload["status"] == "ok"
    assert payload["items"][0]["title"] == "Fed rate decision"
    assert payload["items"][0]["topOutcomes"] == []


def test_short_macro_terms_do_not_match_inside_unrelated_words():
    oilers_event = {
        "id": "evt-nhl",
        "title": "Will the Edmonton Oilers win the Stanley Cup?",
        "markets": [{"question": "Will the Edmonton Oilers win the Stanley Cup?", "active": True}],
    }
    ctx = make_context(http_json_get=lambda *args, **kwargs: {"events": [oilers_event]})

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=8)

    assert payload["status"] == "empty"
    assert payload["items"] == []


def test_stale_snapshot_can_be_returned_by_cache_layer():
    stale = {
        "generatedAt": "2026-05-10T00:00:00Z",
        "status": "ok",
        "sources": {"gammaEvents": "ok"},
        "summary": {"activeCount": 1, "topCategory": "CPI / Inflation", "signal": "STALE CPI CLUSTER"},
        "categories": [],
        "items": [{"eventId": "stale", "title": "Stale CPI market"}],
        "cacheMode": "stale-seed",
    }

    class StaleOnlyStore:
        def get(self, namespace, cache_key):
            return None

        def get_stale(self, namespace, cache_key):
            return stale

        def set(self, *args, **kwargs):
            return None

    store = StaleOnlyStore()
    ctx = make_context(snapshot_store=store)

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=8)

    assert payload["cacheMode"] == "stale-seed"
    assert payload["items"][0]["eventId"] == "stale"


def test_api_reads_seeded_sqlite_snapshot_without_live_fetch(tmp_path):
    seeded = {
        "generatedAt": "2026-05-11T00:00:00Z",
        "status": "ok",
        "sources": {"gammaEvents": "ok"},
        "summary": {"activeCount": 1, "topCategory": "CPI / Inflation", "signal": "CPI CLUSTER ACTIVE"},
        "categories": [],
        "items": [{"eventId": "seeded", "title": "Seeded CPI market"}],
        "cacheMode": "seeded",
    }
    store = SnapshotStore(str(tmp_path / "snapshots.sqlite3"))
    store.set(polymarket_macro_map_service.MACRO_MAP_SNAPSHOT_NAMESPACE, polymarket_macro_map_service.MACRO_MAP_CACHE_KEY, seeded, 300)
    ctx = make_context(snapshot_store=store, http_json_get=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live fetch should not run")))

    payload = polymarket_macro_map_service.get_polymarket_macro_map_snapshot(ctx, limit=8)

    assert payload["cacheMode"] == "sqlite-seed"
    assert payload["items"][0]["eventId"] == "seeded"


def make_watcher(tmp_path) -> tuple[polymarket_macro_map_watcher.PolymarketMacroMapWatcher, FakeRedis]:
    fake_redis = FakeRedis()
    settings = make_settings(str(tmp_path / "snapshots.sqlite3"))
    redis_module = SimpleNamespace(from_url=lambda *args, **kwargs: fake_redis)
    requests_module = SimpleNamespace(Session=lambda: SimpleNamespace(headers={}, get=lambda *args, **kwargs: None))
    with patch.object(polymarket_macro_map_watcher, "redis", redis_module), patch.object(polymarket_macro_map_watcher, "requests", requests_module):
        watcher = polymarket_macro_map_watcher.PolymarketMacroMapWatcher(
            redis_url=settings.redis_url,
            redis_prefix=settings.redis_prefix,
            snapshot_sqlite_path=settings.snapshot_sqlite_path,
            settings=settings,
            interval_seconds=180,
        )
    return watcher, fake_redis


def sample_payload(item_id: str = "macro-1") -> Dict[str, Any]:
    return {
        "generatedAt": "2026-05-11T00:00:00Z",
        "source": "Polymarket Gamma API",
        "sourceUrl": "https://gamma.example",
        "status": "ok",
        "sources": {"gammaEvents": "ok"},
        "summary": {"activeCount": 1, "topCategory": "CPI / Inflation", "signal": "CPI CLUSTER ACTIVE"},
        "categories": [],
        "items": [{"eventId": item_id, "title": "May CPI market"}],
    }


def test_watcher_stores_payload_snapshot_and_seed_meta(tmp_path):
    watcher, fake_redis = make_watcher(tmp_path)

    with patch.object(polymarket_macro_map_service, "build_polymarket_macro_map_payload", return_value=sample_payload()):
        result = watcher.run_once()

    assert result["status"] == "stored"
    stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
    assert stored["cacheMode"] == "seeded"
    assert stored["items"][0]["eventId"] == "macro-1"
    snapshot = watcher.snapshot_store.get_stale(watcher.namespace(), watcher.cache_key())
    assert snapshot["items"][0]["eventId"] == "macro-1"
    meta = json.loads(fake_redis.get("polydata:seed-meta:macro:polymarket-macro-map") or "{}")
    assert meta["status"] == "ok"
    assert meta["recordCount"] == 1
    assert meta["serviceName"] == "polydata-polymarket-macro-map-seed.service"


def test_watcher_preserves_previous_snapshot_when_new_payload_is_empty(tmp_path):
    watcher, fake_redis = make_watcher(tmp_path)
    previous = {**sample_payload("old-macro"), "cacheMode": "seeded"}
    watcher.store_payload(previous)

    empty_payload = {**sample_payload("empty"), "status": "empty", "items": [], "summary": {"activeCount": 0}}
    with patch.object(polymarket_macro_map_service, "build_polymarket_macro_map_payload", return_value=empty_payload):
        result = watcher.run_once()

    assert result["status"] == "preserved"
    stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
    assert stored["items"][0]["eventId"] == "old-macro"
    meta = json.loads(fake_redis.get("polydata:seed-meta:macro:polymarket-macro-map") or "{}")
    assert meta["status"] == "preserved"
    assert "Preserved previous snapshot" in meta["errorSummary"]


def test_runtime_route_clamps_limit_to_max():
    app = Flask(__name__)
    helpers = {
        "COMMODITY_SYMBOLS": [],
        "CRYPTO_SYMBOLS": [],
        "get_market_group_snapshot": lambda symbols, kind: {"kind": kind, "items": symbols},
        "get_polymarket_macro_map_snapshot": lambda limit=12: {"limit": limit},
        "get_f1_panel_snapshot": lambda limit=10: {"limit": limit},
        "get_geo_sanctions_shock_snapshot": lambda limit=6: {"limit": limit},
        "get_jin10_panel_snapshot": lambda limit=24: {"limit": limit},
        "get_nba_scoreboard_snapshot": lambda limit=10: {"limit": limit},
        "get_nba_intel_snapshot": lambda limit=12: {"limit": limit},
        "get_nba_matchup_predictor_snapshot": lambda limit=8: {"limit": limit},
        "get_inflation_nowcast_snapshot": lambda: {"items": []},
        "get_alpha_signal_snapshot": lambda limit=8: {"limit": limit},
        "get_crypto_funding_watch_snapshot": lambda limit=16: {"limit": limit},
        "get_cpi_release_calendar_snapshot": lambda limit=8: {"limit": limit},
        "get_energy_gasoline_shock_snapshot": lambda limit=6: {"limit": limit},
        "get_food_retail_basket_snapshot": lambda limit=8: {"limit": limit},
        "get_new_market_signals_snapshot": lambda limit=12: {"limit": limit},
        "get_whale_trades_snapshot": lambda limit=14: {"limit": limit},
        "get_suspicious_trades_snapshot": lambda limit=12: {"limit": limit},
    }
    app.register_blueprint(create_runtime_panels_blueprint(helpers))

    response = app.test_client().get("/runtime/macro/polymarket-map?limit=99")

    assert response.status_code == 200
    assert response.get_json()["limit"] == 20
