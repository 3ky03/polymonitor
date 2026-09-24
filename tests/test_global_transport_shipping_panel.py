from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.runtime_panels import get_panel_by_id
from api.services import global_transport_shipping_service
from runtime.snapshot_store import SnapshotStore


AIRPORTS = """1,"Alpha Hub","Alpha City","United States","AAA","KAAA",40.0,-74.0,10,-5,A,America/New_York,airport,OurAirports
2,"Bravo Field","Bravo City","France","BBB","LBBB",48.0,2.0,20,1,E,Europe/Paris,airport,OurAirports
3,"Cargo Port","Cargo City","Japan","CCC","RJCC",35.0,139.0,30,9,U,Asia/Tokyo,airport,OurAirports
"""

AIRLINES = """10,"Fixture Air",\\N,"FX","FXA","Fixture","United States","Y"
11,"Transit Jet",\\N,"TJ","TJA","Transit","France","Y"
"""

ROUTES = """FX,10,AAA,1,BBB,2,,0,320
FX,10,AAA,1,CCC,3,,0,777
TJ,11,BBB,2,AAA,1,,0,320
TJ,11,CCC,3,AAA,1,,0,787
"""

TRANSITLAND = {
    "feeds": [
        {
            "id": "f-us-alpha",
            "spec": "gtfs",
            "urls": {"static_current": "https://example.test/alpha.zip"},
            "operators": [{"onestop_id": "o-alpha", "name": "Alpha Transit"}],
        },
        {
            "id": "f-fr-bravo",
            "spec": "gtfs",
            "authorization": {"type": "api_key"},
            "operators": [{"onestop_id": "o-bravo", "name": "Bravo Metro"}],
        },
    ]
}


def _write_openflights_fixture(root: Path) -> None:
    data = root / "data"
    data.mkdir(parents=True)
    (data / "airports.dat").write_text(AIRPORTS, encoding="utf-8")
    (data / "airlines.dat").write_text(AIRLINES, encoding="utf-8")
    (data / "routes.dat").write_text(ROUTES, encoding="utf-8")


def test_build_global_transport_shipping_payload_from_structured_sources(tmp_path, monkeypatch):
    openflights_root = tmp_path / "openflights"
    _write_openflights_fixture(openflights_root)
    monkeypatch.setenv("POLYDATA_OPENFLIGHTS_ROOT", str(openflights_root))
    monkeypatch.setenv("POLYDATA_TRANSITLAND_ATLAS_URL", "https://example.test/transitland.dmfr.json")
    monkeypatch.delenv("POLYDATA_TRANSITLAND_ATLAS_URLS", raising=False)
    monkeypatch.delenv("POLYDATA_AISSTREAM_API_KEY", raising=False)
    monkeypatch.delenv("POLYDATA_OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("POLYDATA_OPENSKY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("POLYDATA_ADSB_FALLBACK_ENABLED", "0")

    def http_text_get(url: str, **_: object) -> str:
        assert url == "https://example.test/transitland.dmfr.json"
        return json.dumps(TRANSITLAND)

    payload = global_transport_shipping_service.build_global_transport_shipping_payload(
        {
            "http_text_get": http_text_get,
            "utc_now_iso": lambda: "2026-06-16T01:00:00Z",
            "search_markets": lambda query, limit=3: [{"id": "pm-1", "slug": "transport-disruption", "question": query}],
        },
        limit=8,
    )

    assert payload["panelId"] == "global-transport-shipping"
    assert payload["status"] == "ok"
    assert payload["summary"]["airports"] == 3
    assert payload["summary"]["routes"] == 4
    assert payload["summary"]["transitFeeds"] == 2
    assert payload["summary"]["transitCatalogFiles"] == 1
    assert payload["summary"]["transitScannedFiles"] == 1
    assert payload["summary"]["aisStatus"] == "missing-key"
    assert payload["summary"]["openSkyStatus"] == "missing-key"
    assert payload["aviation"]["mode"] == "seeded-route-graph"
    assert payload["aviation"]["hubs"][0]["code"] == "AAA"
    assert payload["aviation"]["routes"][0]["fromCode"] in {"AAA", "BBB", "CCC"}
    assert payload["aviation"]["flights"][0]["id"].startswith("flight-")
    assert payload["aviation"]["liveFlights"] == []
    assert payload["aviation"]["ops"][0]["routeCount"] >= 1
    assert payload["aviation"]["airlines"][0]["name"] == "Fixture Air"
    assert payload["aviation"]["news"][0]["source"] == "OpenFlights"
    assert any(item["evidenceType"] == "OPENFLIGHTS" and item["entity"] == "AAA" for item in payload["items"])
    assert any(item["evidenceType"] == "TRANSITLAND" and item["metric"] == 2 for item in payload["items"])
    assert any(item["evidenceType"] == "AISSTREAM" and item["severity"] == "watch" for item in payload["items"])
    assert payload["items"][0]["relatedPolymarketMarketIds"]


def test_build_global_transport_shipping_payload_accepts_dict_market_search(tmp_path, monkeypatch):
    openflights_root = tmp_path / "openflights"
    _write_openflights_fixture(openflights_root)
    monkeypatch.setenv("POLYDATA_OPENFLIGHTS_ROOT", str(openflights_root))
    monkeypatch.setenv("POLYDATA_TRANSITLAND_ATLAS_URL", "https://example.test/transitland.dmfr.json")
    monkeypatch.delenv("POLYDATA_TRANSITLAND_ATLAS_URLS", raising=False)
    monkeypatch.delenv("POLYDATA_AISSTREAM_API_KEY", raising=False)
    monkeypatch.delenv("POLYDATA_OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("POLYDATA_OPENSKY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("POLYDATA_ADSB_FALLBACK_ENABLED", "0")

    def http_text_get(url: str, **_: object) -> str:
        assert url == "https://example.test/transitland.dmfr.json"
        return json.dumps(TRANSITLAND)

    payload = global_transport_shipping_service.build_global_transport_shipping_payload(
        {
            "http_text_get": http_text_get,
            "utc_now_iso": lambda: "2026-06-16T01:00:00Z",
            "search_markets": lambda query, limit=3: {
                "items": [{"id": "pm-2", "slug": "airport-disruption", "question": query}]
            },
        },
        limit=8,
    )

    assert payload["items"][0]["relatedPolymarketMarketIds"]


def test_get_global_transport_shipping_snapshot_returns_seed_miss_without_live_build(tmp_path):
    store = SnapshotStore(str(tmp_path / "snapshots.sqlite3"))
    payload = global_transport_shipping_service.get_global_transport_shipping_snapshot(
        {
            "SNAPSHOT_STORE": store,
            "utc_now_iso": lambda: "2026-06-16T01:00:00Z",
        },
        allow_live_build=False,
    )

    assert payload["status"] == "warming"
    assert payload["cacheMode"] == "seed-miss"
    assert payload["items"] == []


def test_transitland_catalog_defaults_to_index_sampling(monkeypatch):
    monkeypatch.delenv("POLYDATA_TRANSITLAND_ATLAS_URL", raising=False)
    monkeypatch.delenv("POLYDATA_TRANSITLAND_ATLAS_URLS", raising=False)
    monkeypatch.setenv("POLYDATA_TRANSITLAND_ATLAS_FILE_LIMIT", "2")
    index = [
        {"name": "a.dmfr.json", "download_url": "https://raw.test/a.dmfr.json"},
        {"name": "b.dmfr.json", "download_url": "https://raw.test/b.dmfr.json"},
        {"name": "c.dmfr.json", "download_url": "https://raw.test/c.dmfr.json"},
    ]
    payloads = {
        "https://raw.test/a.dmfr.json": {"feeds": [{"id": "f-us-a", "spec": "gtfs", "operators": [{"onestop_id": "o-a", "name": "A"}]}]},
        "https://raw.test/c.dmfr.json": {"feeds": [{"id": "f-fr-c", "spec": "gtfs-rt", "operators": [{"onestop_id": "o-c", "name": "C"}]}]},
    }

    def http_text_get(url: str, **_: object) -> str:
        if "api.github.com" in url:
            return json.dumps(index)
        return json.dumps(payloads[url])

    stats, source_url = global_transport_shipping_service._fetch_transitland_catalog({"http_text_get": http_text_get})

    assert source_url.endswith("/contents/feeds")
    assert stats["catalogFileCount"] == 3
    assert stats["scannedFileCount"] == 2
    assert stats["feedCount"] == 2
    assert stats["specCounts"] == {"gtfs": 1, "gtfs-rt": 1}


def test_aisstream_status_uses_plain_env_alias_and_fresh_cache(monkeypatch):
    monkeypatch.delenv("POLYDATA_AISSTREAM_API_KEY", raising=False)
    monkeypatch.setenv("AISSTREAM_API_KEY", "test-key")
    monkeypatch.setenv("POLYDATA_AISSTREAM_MIN_SAMPLE_INTERVAL_SECONDS", "21600")

    def fail_sample(*_: object, **__: object) -> None:
        raise AssertionError("fresh AIS cache should avoid websocket sampling")

    monkeypatch.setattr(global_transport_shipping_service, "_sample_aisstream", fail_sample)
    sampled_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = global_transport_shipping_service._aisstream_status(
        {
            "utc_now_iso": lambda: sampled_at,
            "get_cached_json": lambda namespace, cache_key: {
                "status": "ok",
                "messageCount": 7,
                "sampledAt": sampled_at,
                "sourceUrl": "https://aisstream.io/documentation",
            },
        }
    )

    assert payload["status"] == "ok"
    assert payload["messageCount"] == 7
    assert payload["cacheMode"] == "ais-cache"


def test_opensky_live_status_uses_oauth_and_state_cache(monkeypatch):
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "client-id")
    monkeypatch.setenv("OPENSKY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("POLYDATA_OPENSKY_REGION_LIMIT", "1")
    cache = {}
    calls = {"token": 0, "states": 0}

    def http_form_post(url: str, data=None, **_: object) -> dict:
        calls["token"] += 1
        assert "openid-connect/token" in url
        assert data["grant_type"] == "client_credentials"
        return {"access_token": "token-value", "expires_in": 1800}

    def http_json_get(url: str, params=None, headers=None, **_: object) -> dict:
        calls["states"] += 1
        assert "states/all" in url
        assert headers["Authorization"] == "Bearer token-value"
        assert params["lamin"] is not None
        return {
            "time": 1780000000,
            "states": [
                ["abc123", "TEST123 ", "United States", 1780000000, 1780000000, -73.7, 40.6, 3200, False, 210, 88, -1.2, None, 3300, "1234", False, 0],
                ["nogeo", "DROP ", "United States", 1780000000, 1780000000, None, None, 3200, False, 210, 88, -1.2, None, 3300, "1234", False, 0],
            ],
        }

    sampled_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    ctx = {
        "utc_now_iso": lambda: sampled_at,
        "http_form_post": http_form_post,
        "http_json_get": http_json_get,
        "get_cached_json": lambda namespace, key: cache.get((namespace, key)),
        "set_cached_json": lambda namespace, key, payload, ttl: cache.__setitem__((namespace, key), payload),
    }

    first = global_transport_shipping_service._opensky_live_status(ctx)
    second = global_transport_shipping_service._opensky_live_status(ctx)

    assert first["status"] == "ok"
    assert first["aircraftCount"] == 1
    assert first["aircraft"][0]["callsign"] == "TEST123"
    assert first["regions"][0]["returned"] == 1
    assert second["cacheMode"] == "cache"
    assert calls == {"token": 1, "states": 1}


def test_opensky_auth_failure_degrades_without_breaking_payload(tmp_path, monkeypatch):
    openflights_root = tmp_path / "openflights"
    _write_openflights_fixture(openflights_root)
    monkeypatch.setenv("POLYDATA_OPENFLIGHTS_ROOT", str(openflights_root))
    monkeypatch.setenv("POLYDATA_TRANSITLAND_ATLAS_URL", "https://example.test/transitland.dmfr.json")
    monkeypatch.delenv("POLYDATA_TRANSITLAND_ATLAS_URLS", raising=False)
    monkeypatch.delenv("POLYDATA_AISSTREAM_API_KEY", raising=False)
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "client-id")
    monkeypatch.setenv("OPENSKY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("POLYDATA_ADSB_FALLBACK_ENABLED", "0")

    cache = {}

    def fail_token(ctx: dict) -> tuple[str, dict]:
        raise TimeoutError("opensky auth timeout")

    def http_text_get(url: str, **_: object) -> str:
        assert url == "https://example.test/transitland.dmfr.json"
        return json.dumps(TRANSITLAND)

    monkeypatch.setattr(global_transport_shipping_service, "_opensky_access_token", fail_token)
    payload = global_transport_shipping_service.build_global_transport_shipping_payload(
        {
            "http_text_get": http_text_get,
            "utc_now_iso": lambda: "2026-06-16T01:00:00Z",
            "search_markets": lambda query, limit=3: [],
            "get_cached_json": lambda namespace, key: cache.get((namespace, key)),
            "set_cached_json": lambda namespace, key, value, ttl: cache.__setitem__((namespace, key), value),
        },
        limit=8,
    )

    assert payload["status"] == "ok"
    assert payload["summary"]["openSkyStatus"] == "error"
    assert payload["summary"]["liveFlightSamples"] == 0
    assert payload["sourceHealth"]["opensky"] == "degraded"
    assert payload["sources"]["opensky"]["status"] == "error"
    assert payload["aviation"]["mode"] == "seeded-route-graph"
    assert payload["aviation"]["liveFlights"] == []
    assert cache[(global_transport_shipping_service.OPENSKY_SNAPSHOT_NAMESPACE, global_transport_shipping_service.OPENSKY_CACHE_KEY)]["status"] == "error"


def test_adsb_fallback_supplies_live_aircraft_when_opensky_degraded(tmp_path, monkeypatch):
    openflights_root = tmp_path / "openflights"
    _write_openflights_fixture(openflights_root)
    monkeypatch.setenv("POLYDATA_OPENFLIGHTS_ROOT", str(openflights_root))
    monkeypatch.setenv("POLYDATA_TRANSITLAND_ATLAS_URL", "https://example.test/transitland.dmfr.json")
    monkeypatch.delenv("POLYDATA_TRANSITLAND_ATLAS_URLS", raising=False)
    monkeypatch.delenv("POLYDATA_AISSTREAM_API_KEY", raising=False)
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "client-id")
    monkeypatch.setenv("OPENSKY_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("POLYDATA_ADSB_HUB_LIMIT", "1")
    monkeypatch.setenv("POLYDATA_ADSB_PER_HUB_AIRCRAFT_LIMIT", "6")
    monkeypatch.setenv("POLYDATA_ADSB_AIRCRAFT_LIMIT", "12")
    cache = {}

    def fail_token(ctx: dict) -> tuple[str, dict]:
        raise TimeoutError("opensky auth timeout")

    def http_text_get(url: str, **_: object) -> str:
        assert url == "https://example.test/transitland.dmfr.json"
        return json.dumps(TRANSITLAND)

    def http_json_get(url: str, **_: object) -> dict:
        assert "/point/" in url
        return {
            "ac": [
                {
                    "hex": "a53207",
                    "flight": "AAL1567 ",
                    "r": "N434AN",
                    "t": "A21N",
                    "alt_baro": 17900,
                    "gs": 405.7,
                    "track": 273.25,
                    "baro_rate": 320,
                    "lat": 33.9,
                    "lon": -84.1,
                    "emergency": "none",
                    "seen": 2,
                },
                {"hex": "nogeo", "flight": "DROP", "gs": 120},
            ]
        }

    monkeypatch.setattr(global_transport_shipping_service, "_opensky_access_token", fail_token)
    payload = global_transport_shipping_service.build_global_transport_shipping_payload(
        {
            "http_text_get": http_text_get,
            "http_json_get": http_json_get,
            "utc_now_iso": lambda: "2026-06-16T01:00:00Z",
            "search_markets": lambda query, limit=3: [],
            "get_cached_json": lambda namespace, key: cache.get((namespace, key)),
            "set_cached_json": lambda namespace, key, value, ttl: cache.__setitem__((namespace, key), value),
        },
        limit=10,
    )

    assert payload["summary"]["openSkyStatus"] == "error"
    assert payload["summary"]["adsbStatus"] == "ok"
    assert payload["summary"]["liveFlightSource"] == "ADSB.lol"
    assert payload["summary"]["liveFlightSamples"] == 1
    assert payload["sourceHealth"]["opensky"] == "degraded"
    assert payload["sourceHealth"]["adsb"] == "fresh"
    assert payload["aviation"]["mode"] == "live-aircraft"
    assert payload["aviation"]["liveFlights"][0]["source"] == "ADSB.lol"
    assert payload["aviation"]["liveFlights"][0]["callsign"] == "AAL1567"
    assert any(item["evidenceType"] == "ADSBLOL" and item["metric"] == 1 for item in payload["items"])


def test_global_transport_shipping_runtime_panel_registered():
    panel = get_panel_by_id("global-transport-shipping")

    assert panel is not None
    assert panel.route == "/runtime/transport/global-shipping"
