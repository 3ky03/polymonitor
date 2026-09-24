from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from api.runtime_panels import get_panel_by_id
from api.services import breaking_event_radar_service, world_cup_match_ops_service


def test_breaking_event_radar_builds_from_gdelt_and_wikimedia() -> None:
    def http_json_get(url, params=None, timeout=12, headers=None):
        if "gdeltproject" in url:
            return {
                "articles": [
                    {
                        "title": "Iran talks intensify as sanctions risk returns",
                        "url": "https://example.com/iran-talks",
                        "domain": "example.com",
                        "sourcecountry": "US",
                        "seendate": "20260616093000",
                        "tone": "-2.4",
                    },
                    {
                        "title": "Tehran says nuclear deal remains possible",
                        "url": "https://example.org/tehran",
                        "domain": "example.org",
                        "sourcecountry": "GB",
                        "seendate": "20260616090000",
                    },
                ]
            }
        if "wikimedia" in url:
            return {"items": [{"views": 100}, {"views": 110}, {"views": 170}]}
        raise AssertionError(url)

    def search_markets(query, limit=4):
        return [{"id": "pm-1", "slug": "iran-sanctions", "question": "Will Iran sanctions return?"}]

    ctx = {
        "http_json_get": http_json_get,
        "search_markets": search_markets,
        "utc_now_iso": lambda: "2026-06-16T10:00:00Z",
        "SETTINGS": object(),
    }

    payload = breaking_event_radar_service.build_breaking_event_radar_payload(ctx, limit=3)

    assert payload["status"] == "ok"
    assert payload["items"]
    first = payload["items"][0]
    assert first["velocityScore"] > 0
    assert first["sourceDiversity"] == 2
    assert first["countrySpread"] == 2
    assert first["relatedPolymarketMarketIds"] == ["pm-1"]
    assert first["markets"][0]["matchReasons"] == ["entity", "topic"]


def test_breaking_event_radar_seed_miss_does_not_live_build_when_disabled() -> None:
    payload = breaking_event_radar_service.get_breaking_event_radar_snapshot(
        {"utc_now_iso": lambda: "2026-06-16T10:00:00Z", "SETTINGS": object()},
        limit=4,
        allow_live_build=False,
    )

    assert payload["status"] == "warming"
    assert payload["cacheMode"] == "seed-miss"
    assert payload["items"] == []


def test_world_cup_match_ops_transforms_dashboard_payload() -> None:
    dashboard = {
        "generatedAt": "2026-06-16T10:00:00Z",
        "providerStates": {"schedule": "ok", "weather": "ok", "odds": "ok"},
        "matches": [
            {
                "id": "wc2026-002",
                "homeTeam": "USA",
                "awayTeam": "Paraguay",
                "kickoffUtc": "2026-06-13T20:00:00Z",
                "kickoffLocal": "Sat, 13 Jun, 15:00",
                "cityId": "los-angeles",
                "city": "Los Angeles / Inglewood",
                "venue": "SoFi Stadium",
                "status": "scheduled",
                "stage": "group",
                "group": "Group D",
            },
            {
                "id": "wc2026-001",
                "homeTeam": "Mexico",
                "awayTeam": "South Africa",
                "kickoffUtc": "2026-06-11T19:00:00Z",
                "kickoffLocal": "Thu, 11 Jun, 13:00",
                "cityId": "mexico-city",
                "city": "Mexico City",
                "venue": "Estadio Azteca",
                "status": "scheduled",
                "stage": "group",
                "group": "Group A",
                "marketLinked": True,
            },
        ],
        "weather": [
            {"cityId": "mexico-city", "temperature": 30, "precipitationProbability": 40, "windSpeed": 15, "source": "Open-Meteo"},
            {"cityId": "los-angeles", "temperature": 22, "precipitationProbability": 0, "windSpeed": 5, "source": "Open-Meteo"},
        ],
        "odds": [
            {
                "matchId": "wc2026-001",
                "marketId": "m1",
                "marketTitle": "Mexico vs South Africa winner",
                "marketUrl": "https://polymarket.com/event/mexico-south-africa",
            }
        ],
    }
    ctx = {"get_worldcup_dashboard_snapshot": lambda: dashboard, "utc_now_iso": lambda: "2026-06-10T10:00:00Z", "SETTINGS": object()}

    payload = world_cup_match_ops_service.build_world_cup_match_ops_payload(ctx, limit=2)

    assert payload["items"][0]["id"] == "wc2026-001"
    assert payload["items"][0]["marketLinked"] is True
    assert payload["items"][0]["relatedPolymarketMarketIds"] == ["m1"]
    assert payload["items"][0]["weatherRisk"]["level"] in {"watch", "high"}
    assert payload["summary"]["linkedMarkets"] == 1


def test_world_cup_match_ops_seed_miss_does_not_live_build_when_disabled() -> None:
    payload = world_cup_match_ops_service.get_world_cup_match_ops_snapshot(
        {"utc_now_iso": lambda: "2026-06-16T10:00:00Z", "SETTINGS": object()},
        limit=4,
        allow_live_build=False,
    )

    assert payload["status"] == "warming"
    assert payload["cacheMode"] == "seed-miss"
    assert payload["items"] == []


def test_live_evidence_runtime_panels_are_registered() -> None:
    breaking = get_panel_by_id("breaking-event-radar")
    match_ops = get_panel_by_id("world-cup-match-ops")

    assert breaking is not None
    assert breaking.route == "/runtime/evidence/breaking-event-radar"
    assert match_ops is not None
    assert match_ops.route == "/runtime/sports/world-cup-match-ops"
