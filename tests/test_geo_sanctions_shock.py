from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

from flask import Flask


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.routes.runtime_panels import create_runtime_panels_blueprint
from api.config import load_api_settings
from api.services import geo_sanctions_shock_service
from runtime.geo_sanctions_shock_watcher import GeoSanctionsShockWatcher
from runtime.seed_meta import SeedMetaStore
from runtime.snapshot_store import SnapshotStore


SDN_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sdnList xmlns="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML">
  <publshInformation>
    <Publish_Date>04/28/2026</Publish_Date>
    <Record_Count>4</Record_Count>
  </publshInformation>
  <sdnEntry>
    <uid>100</uid>
    <lastName>IRAN SHIPPING GROUP</lastName>
    <sdnType>Entity</sdnType>
    <programList><program>IRAN</program></programList>
    <addressList><address><country>Iran</country></address></addressList>
  </sdnEntry>
</sdnList>
"""

CONSOLIDATED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sdnList xmlns="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML">
  <publshInformation>
    <Publish_Date>04/27/2026</Publish_Date>
    <Record_Count>2</Record_Count>
  </publshInformation>
  <sdnEntry>
    <uid>200</uid>
    <lastName>BEIJING EXPORT FIRM</lastName>
    <sdnType>Entity</sdnType>
    <programList><program>NS-CMIC</program></programList>
    <addressList><address><country>China</country></address></addressList>
  </sdnEntry>
</sdnList>
"""


class FakeLogger:
    def exception(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def info(self, *args, **kwargs) -> None:
        return None


class FakeApp:
    logger = FakeLogger()


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200, json_data: Any = None):
        self.content = content
        self.status_code = status_code
        self._json_data = json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.content.decode("utf-8"))


class FakeRequests:
    def __init__(self) -> None:
        self.last_get_headers: Dict[str, str] | None = None
        self.last_get_url: str | None = None

    def get(self, url: str, params: Dict[str, Any] | None = None, timeout: int = 20, headers: Dict[str, str] | None = None):
        self.last_get_url = url
        self.last_get_headers = headers
        if url == "sdn-url":
            return FakeResponse(SDN_XML)
        if url == "cons-url":
            return FakeResponse(CONSOLIDATED_XML)
        if url == "acled-api-url":
            return FakeResponse(
                b"{}",
                json_data={
                    "data": [
                        {
                            "event_id_cnty": "IRN100",
                            "event_date": "2026-04-25",
                            "event_type": "Political violence",
                            "sub_event_type": "Shelling/artillery/missile attack",
                            "country": "Iran",
                            "admin1": "Tehran",
                            "location": "Natanz",
                            "actor1": "Military Forces of Iran",
                            "actor2": "Unidentified Armed Group",
                            "fatalities": 3,
                            "notes": "Missile strike near Iranian nuclear site",
                        }
                    ]
                },
            )
        if url == "ucdp-api-url":
            return FakeResponse(
                b"{}",
                json_data={
                    "Result": [
                        {
                            "id": 9001,
                            "date_start": "2026-04-26",
                            "date_end": "2026-04-26",
                            "country": "Ukraine",
                            "region": "Europe",
                            "conflict_name": "Russia - Ukraine",
                            "dyad_name": "Government of Russia - Government of Ukraine",
                            "side_a": "Government of Russia",
                            "side_b": "Government of Ukraine",
                            "where_coordinates": "Kyiv",
                            "best": 4,
                        }
                    ]
                },
            )
        raise RuntimeError(f"unexpected url {url}")

    def post(self, url: str, data: Dict[str, Any] | None = None, timeout: int = 20, headers: Dict[str, str] | None = None):
        if url == "acled-token-url":
            grant_type = (data or {}).get("grant_type")
            if grant_type == "refresh_token":
                return FakeResponse(
                    b'{"access_token":"refreshed-acled-token","refresh_token":"refresh-2","expires_in":86400}',
                    json_data={"access_token": "refreshed-acled-token", "refresh_token": "refresh-2", "expires_in": 86400},
                )
            return FakeResponse(
                b'{"access_token":"fake-acled-token","refresh_token":"refresh-1","expires_in":86400}',
                json_data={"access_token": "fake-acled-token", "refresh_token": "refresh-1", "expires_in": 86400},
            )
        raise RuntimeError(f"unexpected url {url}")


class FakeRedis:
    def __init__(self) -> None:
        self.values: Dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value

    def ping(self) -> bool:
        return True


class GeoSanctionsShockSeedBuilderTestCase(unittest.TestCase):
    def make_context(
        self,
        *,
        requests_lib: Any = None,
        conflict_url: str = "conflict-url",
        previous_total: int = 3,
        acled_enabled: bool = True,
        ucdp_enabled: bool = False,
    ) -> Dict[str, Any]:
        settings = SimpleNamespace(
            geo_shock_ofac_sdn_url="sdn-url",
            geo_shock_ofac_consolidated_url="cons-url",
            geo_shock_federal_register_api_url="fr-url",
            geo_shock_conflict_api_url=conflict_url,
            geo_shock_gdelt_doc_api_url="gdelt-url",
            geo_shock_ucdp_api_url="ucdp-api-url",
            geo_shock_ucdp_access_token="ucdp-token" if ucdp_enabled else "",
            geo_shock_acled_token_url="acled-token-url",
            geo_shock_acled_api_url="acled-api-url",
            geo_shock_acled_email="user@example.com" if acled_enabled else "",
            geo_shock_acled_password="secret" if acled_enabled else "",
            geo_shock_source_url="https://ofac.treasury.gov/sanctions-list-service",
            geo_shock_ttl_seconds=900,
        )

        def http_json_get(url, params=None, timeout=12, headers=None):
            if url == "fr-url":
                term = str((params or {}).get("conditions[term]") or "")
                if term == "Iran sanctions":
                    return {
                        "results": [
                            {
                                "title": "Notice of OFAC Sanctions Action",
                                "abstract": "Treasury is designating persons in Iran.",
                                "document_number": "2026-00001",
                                "html_url": "https://federalregister.gov/d/2026-00001",
                                "publication_date": "2026-04-24",
                                "type": "Notice",
                            }
                        ]
                    }
                if term == "China sanctions":
                    return {
                        "results": [
                            {
                                "title": "Executive Order on Export Controls to China",
                                "abstract": "Restrictions on strategic exports to China.",
                                "document_number": "2026-00002",
                                "html_url": "https://federalregister.gov/d/2026-00002",
                                "publication_date": "2026-04-23",
                                "type": "Presidential Document",
                            }
                        ]
                    }
                return {"results": []}
            if url == "conflict-url":
                return {
                    "items": [
                        {
                            "id": "evt-1",
                            "headline": "Missile strike near Iranian nuclear site",
                            "country": "Iran",
                            "event_date": "2026-04-25T00:00:00Z",
                            "tags": ["military", "nuclear"],
                            "source": "WorldMonitor",
                        }
                    ]
                }
            if url == "gdelt-url":
                return {
                    "articles": [
                        {
                            "url": "https://example.com/iran-strike",
                            "title": "Missile strike near Iranian nuclear site",
                            "seendate": "20260425T000000Z",
                            "domain": "example.com",
                        }
                    ]
                }
            raise RuntimeError(f"unexpected url {url}")

        return {
            "SETTINGS": settings,
            "app": FakeApp(),
            "requests": requests_lib,
            "http_json_get": http_json_get,
            "utc_now_iso": lambda: "2026-04-28T00:00:00Z",
            "get_cached_json": lambda namespace, cache_key: {"ofacRecordCountTotal": previous_total},
            "SNAPSHOT_STORE": SimpleNamespace(set=lambda *args, **kwargs: None),
        }

    def test_seed_builder_builds_summary_metrics_from_external_sources(self):
        payload = geo_sanctions_shock_service.build_geo_sanctions_shock_seed_payload(
            self.make_context(requests_lib=FakeRequests()),
            previous={"ofacRecordCountTotal": 3},
        )

        self.assertEqual("ok", payload["status"])
        self.assertEqual(1, payload["summary"]["hotspotCount"])
        self.assertEqual(3, payload["summary"]["newSanctionsCount"])
        self.assertIn("IRAN", payload["summary"]["targetLabels"])
        self.assertEqual("elevated", payload["summary"]["nuclearRisk"])
        self.assertEqual("active", payload["summary"]["militaryFeed"])
        self.assertTrue(payload["items"])
        self.assertTrue(payload["targetBreakdown"])
        self.assertEqual("IRAN", payload["targetBreakdown"][0]["label"])
        self.assertEqual([], payload["linkedMarkets"])

    def test_seed_builder_returns_renderable_degraded_payload_when_sources_fail(self):
        def broken_http_json_get(url, params=None, timeout=12, headers=None):
            raise RuntimeError("upstream down")

        ctx = self.make_context(requests_lib=None, conflict_url="", acled_enabled=False)
        ctx["http_json_get"] = broken_http_json_get

        payload = geo_sanctions_shock_service.build_geo_sanctions_shock_seed_payload(ctx, previous={})

        self.assertEqual("degraded", payload["status"])
        self.assertEqual([], payload["items"])
        self.assertEqual("requests-missing", payload["sources"]["ofacSdn"])
        self.assertEqual("error", payload["sources"]["conflictFeed"])

    def test_seed_builder_uses_gdelt_fallback_when_conflict_url_missing(self):
        ctx = self.make_context(requests_lib=FakeRequests(), conflict_url="", acled_enabled=False)

        payload = geo_sanctions_shock_service.build_geo_sanctions_shock_seed_payload(ctx, previous={})

        self.assertIn(payload["sources"]["conflictFeed"], {"ok", "empty"})
        self.assertGreaterEqual(payload["summary"]["hotspotCount"], 1)
        self.assertIn(payload["summary"]["militaryFeed"], {"active", "quiet"})
        self.assertEqual("GDELT", payload["conflictProvider"])

    def test_seed_builder_uses_ucdp_before_gdelt_when_token_is_present(self):
        requests_lib = FakeRequests()
        ctx = self.make_context(requests_lib=requests_lib, conflict_url="", acled_enabled=False, ucdp_enabled=True)

        payload = geo_sanctions_shock_service.build_geo_sanctions_shock_seed_payload(ctx, previous={})

        self.assertEqual("ok", payload["sources"]["conflictFeed"])
        self.assertEqual("UCDP", payload["conflictProvider"])
        self.assertEqual("ucdp-api-url", requests_lib.last_get_url)
        self.assertEqual("ucdp-token", (requests_lib.last_get_headers or {}).get("x-ucdp-access-token"))
        self.assertTrue(any(item.get("source") == "UCDP" for item in payload["items"]))
        ucdp_items = [item for item in payload["items"] if item.get("source") == "UCDP"]
        self.assertTrue(any("UKRAINE" in (item.get("targetLabels") or []) for item in ucdp_items))

    def test_seed_builder_prefers_ucdp_over_acled_when_both_tokens_are_present(self):
        ctx = self.make_context(requests_lib=FakeRequests(), conflict_url="", acled_enabled=True, ucdp_enabled=True)

        payload = geo_sanctions_shock_service.build_geo_sanctions_shock_seed_payload(ctx, previous={})

        self.assertEqual("ok", payload["sources"]["conflictFeed"])
        self.assertEqual("UCDP", payload["conflictProvider"])
        self.assertTrue(any(item.get("source") == "UCDP" for item in payload["items"]))

    def test_seed_builder_marks_gdelt_rate_limited_and_preserves_previous_conflict_items(self):
        def rate_limited_http_json_get(url, params=None, timeout=12, headers=None):
            if url == "fr-url":
                return {"results": []}
            if url == "gdelt-url":
                raise RuntimeError("http 429")
            return {"results": []}

        ctx = self.make_context(requests_lib=FakeRequests(), conflict_url="", acled_enabled=False)
        ctx["http_json_get"] = rate_limited_http_json_get
        previous = {
            "items": [
                {
                    "id": "conflict:prev",
                    "kind": "conflict",
                    "headline": "Cached missile report near Iran",
                    "country": "Iran",
                    "occurredAt": "2026-04-27T00:00:00Z",
                    "severity": "warning",
                    "targetLabels": ["IRAN"],
                    "source": "GDELT DOC 2.0",
                }
            ]
        }

        payload = geo_sanctions_shock_service.build_geo_sanctions_shock_seed_payload(ctx, previous=previous)

        self.assertEqual("stale", payload["sources"]["conflictFeed"])
        self.assertEqual("GDELT", payload["conflictProvider"])
        self.assertEqual("cached", payload["summary"]["militaryFeed"])
        self.assertTrue(any(item.get("id") == "conflict:prev" for item in payload["items"]))

    def test_seed_builder_prefers_acled_when_credentials_are_present(self):
        ctx = self.make_context(requests_lib=FakeRequests(), conflict_url="")

        payload = geo_sanctions_shock_service.build_geo_sanctions_shock_seed_payload(ctx, previous={})

        self.assertEqual("ok", payload["sources"]["conflictFeed"])
        self.assertTrue(any(item.get("source") == "ACLED" for item in payload["items"]))
        self.assertEqual("IRAN", payload["targetBreakdown"][0]["label"])

    def test_acled_token_manager_refreshes_expired_token(self):
        requests_lib = FakeRequests()
        stored: Dict[str, Any] = {}
        ctx = self.make_context(requests_lib=requests_lib, conflict_url="")
        ctx["get_acled_auth_state"] = lambda: {
            "access_token": "expired-token",
            "refresh_token": "refresh-1",
            "access_expires_at": 1,
        }
        ctx["store_acled_auth_state"] = lambda payload: stored.update(payload)

        token = geo_sanctions_shock_service._fetch_acled_access_token(ctx)

        self.assertEqual("refreshed-acled-token", token)
        self.assertEqual("refreshed-acled-token", stored["access_token"])
        self.assertEqual("refresh-2", stored["refresh_token"])

    def test_settings_accepts_worldmonitor_ucdp_alias(self):
        previous = {key: os.environ.get(key) for key in ("POLYDATA_GEO_SHOCK_UCDP_ACCESS_TOKEN", "UCDP_API_TOKEN", "UCDP_API_Token", "UCDP_ACCESS_TOKEN", "UC_DP_KEY")}
        try:
            for key in previous:
                os.environ.pop(key, None)
            os.environ["UCDP_ACCESS_TOKEN"] = "worldmonitor-token"
            load_api_settings.cache_clear()
            settings = load_api_settings()
            self.assertEqual("worldmonitor-token", settings.geo_shock_ucdp_access_token)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            load_api_settings.cache_clear()


class GeoSanctionsShockSnapshotReadPathTestCase(unittest.TestCase):
    def make_context(self, *, redis_payload: Dict[str, Any] | None = None, stale_payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        settings = SimpleNamespace(
            geo_shock_source_url="https://ofac.treasury.gov/sanctions-list-service",
            geo_shock_ttl_seconds=900,
        )
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        store = SnapshotStore(str(Path(snapshot_dir.name) / "snapshots.sqlite3"))
        if stale_payload is not None:
            store.set(
                geo_sanctions_shock_service.GEO_SHOCK_SNAPSHOT_NAMESPACE,
                geo_sanctions_shock_service.GEO_SHOCK_CACHE_KEY,
                stale_payload,
                1,
            )
        redis_cache: Dict[tuple[str, str], Dict[str, Any]] = {}
        if redis_payload is not None:
            redis_cache[(geo_sanctions_shock_service.GEO_SHOCK_SNAPSHOT_NAMESPACE, geo_sanctions_shock_service.GEO_SHOCK_CACHE_KEY)] = redis_payload
        return {
            "SETTINGS": settings,
            "app": FakeApp(),
            "SNAPSHOT_STORE": store,
            "get_cached_json": lambda namespace, cache_key: redis_cache.get((namespace, cache_key)),
            "set_cached_json": lambda namespace, cache_key, payload, ttl_seconds: redis_cache.__setitem__((namespace, cache_key), payload),
            "utc_now_iso": lambda: "2026-04-28T00:00:00Z",
        }

    def test_snapshot_reads_seeded_payload_from_redis_without_building_live_sources(self):
        seeded = {
            "status": "ok",
            "summary": {"targetSummary": "IRAN / CHINA", "newSanctionsCount": 4},
            "items": [{"id": "seed-1"}, {"id": "seed-2"}],
            "targetBreakdown": [{"label": "IRAN", "count": 2}],
            "linkedMarkets": [],
        }
        ctx = self.make_context(redis_payload=seeded)

        payload = geo_sanctions_shock_service.get_geo_sanctions_shock_snapshot(ctx, limit=1)

        self.assertEqual("ok", payload["status"])
        self.assertEqual(1, len(payload["items"]))
        self.assertEqual("IRAN / CHINA", payload["summary"]["targetSummary"])

    def test_snapshot_uses_stale_payload_when_no_fresh_seed_exists(self):
        stale = {
            "status": "ok",
            "summary": {"targetSummary": "IRAN / RUSSIA"},
            "items": [{"id": "stale-1", "headline": "Stale item"}],
            "targetBreakdown": [{"label": "IRAN", "count": 1}],
            "linkedMarkets": [],
        }
        ctx = self.make_context(stale_payload=stale)

        payload = geo_sanctions_shock_service.get_geo_sanctions_shock_snapshot(ctx, limit=4)

        self.assertEqual("IRAN / RUSSIA", payload["summary"]["targetSummary"])
        self.assertEqual(stale["items"], payload["items"])

    def test_snapshot_returns_warming_fallback_when_no_seed_exists(self):
        ctx = self.make_context()

        payload = geo_sanctions_shock_service.get_geo_sanctions_shock_snapshot(ctx, limit=4)

        self.assertEqual("degraded", payload["status"])
        self.assertEqual("warming", payload["sources"]["ofacSdn"])
        self.assertEqual([], payload["items"])

    def test_runtime_panel_route_clamps_invalid_and_large_limits(self):
        seen_limits = []
        app = Flask(__name__)
        helpers = {
            "COMMODITY_SYMBOLS": [],
            "CRYPTO_SYMBOLS": [],
            "get_market_group_snapshot": lambda symbols, kind: {"kind": kind, "items": symbols},
            "get_f1_panel_snapshot": lambda limit=10: {"limit": limit},
            "get_geo_sanctions_shock_snapshot": lambda limit=6: seen_limits.append(limit) or {"limit": limit},
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
            "get_polymarket_macro_map_snapshot": lambda limit=12: {"limit": limit},
            "get_whale_trades_snapshot": lambda limit=14: {"limit": limit},
            "get_suspicious_trades_snapshot": lambda limit=12: {"limit": limit},
            "get_new_market_signals_snapshot": lambda limit=12: {"limit": limit},
        }
        app.register_blueprint(create_runtime_panels_blueprint(helpers))

        with app.test_client() as client:
            invalid = client.get("/runtime/world/geo-sanctions-shock?limit=oops")
            large = client.get("/runtime/world/geo-sanctions-shock?limit=999")

        self.assertEqual(200, invalid.status_code)
        self.assertEqual(200, large.status_code)
        self.assertEqual([6, 12], seen_limits)


class GeoSanctionsShockWatcherTestCase(unittest.TestCase):
    def make_watcher(self) -> GeoSanctionsShockWatcher:
        watcher = GeoSanctionsShockWatcher.__new__(GeoSanctionsShockWatcher)
        watcher.settings = SimpleNamespace(geo_shock_ttl_seconds=900)
        watcher.redis_prefix = "polydata:"
        watcher.redis_client = FakeRedis()
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        watcher.snapshot_store = SnapshotStore(str(Path(snapshot_dir.name) / "watcher.sqlite3"))
        watcher.seed_meta_store = SeedMetaStore(redis_client=watcher.redis_client, redis_prefix="polydata:", snapshot_store=watcher.snapshot_store)
        watcher.requests = FakeRequests()
        watcher._acled_auth_state = None
        watcher._http_json_get = lambda url, params=None, timeout=15, headers=None: {
            "fr-url": {"results": []},
            "conflict-url": {
                "items": [
                    {
                        "id": "evt-1",
                        "headline": "Missile strike near Iranian nuclear site",
                        "country": "Iran",
                        "event_date": "2026-04-25T00:00:00Z",
                        "tags": ["military", "nuclear"],
                        "source": "WorldMonitor",
                    }
                ]
            },
        }[url]
        watcher.settings.geo_shock_ofac_sdn_url = "sdn-url"
        watcher.settings.geo_shock_ofac_consolidated_url = "cons-url"
        watcher.settings.geo_shock_federal_register_api_url = "fr-url"
        watcher.settings.geo_shock_conflict_api_url = "conflict-url"
        watcher.settings.geo_shock_gdelt_doc_api_url = "gdelt-url"
        watcher.settings.geo_shock_ucdp_api_url = "ucdp-api-url"
        watcher.settings.geo_shock_ucdp_access_token = ""
        watcher.settings.geo_shock_acled_token_url = "acled-token-url"
        watcher.settings.geo_shock_acled_api_url = "acled-api-url"
        watcher.settings.geo_shock_acled_email = "user@example.com"
        watcher.settings.geo_shock_acled_password = "secret"
        watcher.settings.geo_shock_source_url = "https://ofac.treasury.gov/sanctions-list-service"
        return watcher

    def test_watcher_stores_payload_into_redis_and_snapshot_store(self):
        watcher = self.make_watcher()

        result = watcher.run_once()

        self.assertEqual("stored", result["status"])
        raw = watcher.redis_client.get(watcher.redis_key())
        self.assertIsNotNone(raw)
        parsed = json.loads(str(raw))
        self.assertEqual("seeded", parsed["cacheMode"])
        self.assertIsNotNone(
            watcher.snapshot_store.get(
                geo_sanctions_shock_service.GEO_SHOCK_SNAPSHOT_NAMESPACE,
                geo_sanctions_shock_service.GEO_SHOCK_CACHE_KEY,
            )
        )
        meta = watcher.seed_meta_store.load(watcher.seed_meta_namespace(), watcher.seed_meta_cache_key())
        self.assertEqual("ok", meta["status"])
        self.assertEqual(len(parsed["items"]), meta["recordCount"])

    def test_watcher_persists_acled_auth_state_when_acled_path_is_used(self):
        watcher = self.make_watcher()
        watcher.settings.geo_shock_conflict_api_url = ""

        result = watcher.run_once()

        self.assertEqual("stored", result["status"])
        self.assertIsNotNone(watcher.redis_client.get(watcher.acled_auth_redis_key()))

    def test_watcher_preserves_previous_payload_when_new_result_has_no_material_signal(self):
        watcher = self.make_watcher()
        previous = {
            "status": "ok",
            "summary": {"targetSummary": "IRAN / RUSSIA", "targetLabels": ["IRAN", "RUSSIA"], "newSanctionsCount": 2, "hotspotCount": 1, "nuclearRisk": "elevated", "militaryFeed": "active"},
            "items": [{"id": "seed-1"}],
            "targetBreakdown": [{"label": "IRAN", "count": 2}],
            "linkedMarkets": [],
        }
        watcher.store_payload(previous)
        watcher.requests = None
        watcher._http_json_get = lambda *args, **kwargs: {"results": []}
        watcher.build_payload = lambda: {
            "status": "degraded",
            "summary": {
                "hotspotCount": 0,
                "newSanctionsCount": 0,
                "targetLabels": [],
                "targetSummary": "MONITORING",
                "nuclearRisk": "guarded",
                "militaryFeed": "standby",
            },
            "items": [],
            "targetBreakdown": [],
            "linkedMarkets": [],
        }

        result = watcher.run_once()

        self.assertEqual("preserved", result["status"])
        raw = watcher.redis_client.get(watcher.redis_key())
        self.assertEqual(previous["summary"]["targetSummary"], json.loads(str(raw))["summary"]["targetSummary"])
