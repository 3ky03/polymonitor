from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import worldcup_dashboard_service
from api.services.worldcup.odds import bookmaker
from api.services.worldcup import builder as worldcup_builder
from api.services.worldcup.panels import build_worldcup_panel_payloads, panel_ttl_seconds
from runtime import worldcup_dashboard_watcher


class _FakeOddsApiResponse:
    status_code = 401

    def json(self):
        return {
            "message": "Usage quota has been reached. See usage plans at https://the-odds-api.com",
            "error_code": "OUT_OF_USAGE_CREDITS",
        }


class _FakeOddsApiError(Exception):
    response = _FakeOddsApiResponse()


def test_worldcup_bookmaker_quota_error_is_explicit():
    def http_json_get(url, *, params=None, timeout=12, headers=None):
        raise _FakeOddsApiError()

    ctx = {
        "http_json_get": http_json_get,
        "SETTINGS": SimpleNamespace(the_odds_api_key="x" * 32, the_odds_api_base_url="https://api.the-odds-api.com"),
    }

    events, state, stats = bookmaker.fetch_bookmaker_events(ctx)

    assert events == []
    assert state == "quota-exhausted"
    assert stats["errorCode"] == "OUT_OF_USAGE_CREDITS"
    assert stats["httpStatus"] == 401


def test_worldcup_bookmaker_links_configured_main_lines():
    match = {
        "id": "wc2026-001",
        "homeTeam": "Mexico",
        "awayTeam": "South Africa",
        "kickoffUtc": "2026-06-11T19:00:00Z",
        "status": "scheduled",
    }
    event = {
        "id": "event-1",
        "home_team": "Mexico",
        "away_team": "South Africa",
        "commence_time": "2026-06-11T19:00:00Z",
        "bookmakers": [
            {
                "key": "book_a",
                "title": "Book A",
                "last_update": "2026-06-10T12:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Mexico", "price": 2.0},
                            {"name": "Draw", "price": 3.25},
                            {"name": "South Africa", "price": 4.0},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Mexico", "price": 1.91, "point": -0.5},
                            {"name": "South Africa", "price": 1.91, "point": 0.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": 1.8, "point": 2.5},
                            {"name": "Under", "price": 2.0, "point": 2.5},
                        ],
                    },
                ],
            }
        ],
    }

    def http_json_get(url, *, params=None, timeout=12, headers=None):
        assert params["markets"] == "h2h,spreads,totals"
        return [event]

    ctx = {
        "http_json_get": http_json_get,
        "SETTINGS": SimpleNamespace(
            the_odds_api_key="x" * 32,
            the_odds_api_base_url="https://api.the-odds-api.com",
            the_odds_source_url="https://the-odds-api.com/",
            worldcup_odds_markets="h2h,spreads,totals",
        ),
    }

    rows, state, stats = bookmaker.link_bookmaker_odds(ctx, [match])

    assert state == "ok"
    assert stats["matched"] == 1
    assert stats["snapshots"] == 3
    assert [row["marketKey"] for row in rows] == ["h2h", "spreads", "totals"]
    assert rows[0]["marketType"] == "moneyline"
    assert rows[1]["outcomes"][0]["point"] == -0.5
    assert rows[2]["outcomes"][0]["name"] == "Over"


def test_worldcup_dashboard_links_strict_polymarket_market():
    source_schedule = {
        "matches": [
            {
                "num": 1,
                "date": "2026-07-01",
                "time": "13:00 UTC-6",
                "team1": "Mexico",
                "team2": "South Africa",
                "group": "Group A",
                "round": "Matchday 1",
                "ground": "Mexico City",
            }
        ]
    }
    gamma_market = {
        "data": [
            {
                "title": "FIFA World Cup 2026",
                "slug": "fifa-world-cup-2026",
                "markets": [
                    {
                        "question": "Mexico vs South Africa - FIFA World Cup 2026 winner",
                        "slug": "mexico-south-africa-world-cup-2026-winner",
                        "outcomes": '["Mexico","Draw","South Africa"]',
                        "outcomePrices": '["0.52","0.26","0.22"]',
                    }
                ],
            },
            {"title": "Rihanna album 2026", "slug": "rihanna-album-2026"},
        ]
    }

    def http_json_get(url, *, params=None, timeout=12, headers=None):
        if "openfootball" in url:
            return source_schedule
        if "gamma-api.polymarket.com" in url:
            return gamma_market
        raise AssertionError(url)

    ctx = {
        "http_json_get": http_json_get,
        "SETTINGS": SimpleNamespace(worldcup_market_link_scan_limit=4),
    }

    with patch.object(
        worldcup_builder.worldcup_intel_service,
        "get_worldcup_intel_snapshot",
        return_value={"status": "ok", "weather": [], "news": [], "signals": []},
    ):
        payload = worldcup_dashboard_service.build_worldcup_dashboard_payload(ctx)

    assert payload["providerStates"]["odds"] == "ok"
    assert payload["summary"]["odds"] == 1
    assert payload["summary"]["oddsMatched"] == 1
    assert payload["marketLinker"]["matched"] == 1
    assert payload["marketLinker"]["candidates"] >= 1
    assert payload["matches"][0]["marketLinked"] is True
    assert payload["matches"][0]["oddsLinked"] is True
    odds = payload["odds"][0]
    assert odds["matchId"] == "wc2026-001"
    assert odds["probabilities"][0]["outcome"] == "Mexico"
    assert odds["probabilities"][0]["price"] == "0.52"
    assert "rihanna" not in odds["marketTitle"].lower()


def test_worldcup_dashboard_rejects_event_level_false_positive_market():
    source_schedule = {
        "matches": [
            {
                "num": 2,
                "date": "2026-07-02",
                "time": "02:00 UTC+0",
                "team1": "South Korea",
                "team2": "Czech Republic",
                "group": "Group A",
                "round": "Matchday 1",
                "ground": "Guadalajara (Zapopan)",
            }
        ]
    }
    noisy_gamma_event = {
        "data": [
            {
                "title": "South Korea vs Czech Republic - FIFA World Cup 2026",
                "slug": "south-korea-czech-republic-world-cup-2026",
                "markets": [
                    {
                        "question": "Will China invades Taiwan before GTA VI?",
                        "slug": "will-china-invades-taiwan-before-gta-vi",
                        "outcomes": '["Yes","No"]',
                        "outcomePrices": '["0.505","0.495"]',
                    }
                ],
            }
        ]
    }

    def http_json_get(url, *, params=None, timeout=12, headers=None):
        if "openfootball" in url:
            return source_schedule
        if "gamma-api.polymarket.com" in url:
            return noisy_gamma_event
        raise AssertionError(url)

    ctx = {
        "http_json_get": http_json_get,
        "SETTINGS": SimpleNamespace(worldcup_market_link_scan_limit=4),
    }

    with patch.object(
        worldcup_builder.worldcup_intel_service,
        "get_worldcup_intel_snapshot",
        return_value={"status": "ok", "weather": [], "news": [], "signals": []},
    ):
        payload = worldcup_dashboard_service.build_worldcup_dashboard_payload(ctx)

    assert payload["providerStates"]["odds"] in {"empty", "missing-key"}
    assert payload["odds"] == []
    assert payload["matches"][0]["marketLinked"] is False
    assert payload["marketLinker"]["matched"] == 0
    assert payload["marketLinker"]["rejections"]["missing-team"] >= 1


def test_worldcup_dashboard_links_match_result_market_group():
    source_schedule = {
        "matches": [
            {
                "num": 1,
                "date": "2026-07-01",
                "time": "13:00 UTC-6",
                "team1": "Mexico",
                "team2": "South Africa",
                "group": "Group A",
                "round": "Matchday 1",
                "ground": "Mexico City",
            }
        ]
    }
    gamma_event = {
        "data": [
            {
                "title": "Mexico vs South Africa - FIFA World Cup 2026",
                "slug": "mexico-vs-south-africa-fifa-world-cup-2026",
                "active": True,
                "closed": False,
                "markets": [
                    {
                        "id": "m1",
                        "question": "Mexico",
                        "groupItemTitle": "Mexico",
                        "slug": "mexico-vs-south-africa-mexico",
                        "active": True,
                        "closed": False,
                        "outcomes": '["Yes","No"]',
                        "outcomePrices": '["0.49","0.51"]',
                    },
                    {
                        "id": "m2",
                        "question": "Draw",
                        "groupItemTitle": "Draw (Mexico vs South Africa)",
                        "slug": "mexico-vs-south-africa-draw",
                        "active": True,
                        "closed": False,
                        "outcomes": '["Yes","No"]',
                        "outcomePrices": '["0.29","0.71"]',
                    },
                    {
                        "id": "m3",
                        "question": "South Africa",
                        "groupItemTitle": "South Africa",
                        "slug": "mexico-vs-south-africa-south-africa",
                        "active": True,
                        "closed": False,
                        "outcomes": '["Yes","No"]',
                        "outcomePrices": '["0.22","0.78"]',
                    },
                ],
            }
        ]
    }

    def http_json_get(url, *, params=None, timeout=12, headers=None):
        if "openfootball" in url:
            return source_schedule
        if "gamma-api.polymarket.com" in url:
            return gamma_event
        raise AssertionError(url)

    ctx = {
        "http_json_get": http_json_get,
        "SETTINGS": SimpleNamespace(worldcup_market_link_scan_limit=4),
    }

    with patch.object(
        worldcup_builder.worldcup_intel_service,
        "get_worldcup_intel_snapshot",
        return_value={"status": "ok", "weather": [], "news": [], "signals": []},
    ):
        payload = worldcup_dashboard_service.build_worldcup_dashboard_payload(ctx)

    assert payload["providerStates"]["odds"] == "ok"
    odds = payload["odds"][0]
    assert odds["marketUrl"] == "https://polymarket.com/event/mexico-vs-south-africa-fifa-world-cup-2026"
    assert odds["tradeUrl"] == odds["marketUrl"]
    assert [
        {key: row.get(key) for key in ("outcome", "price", "marketUrl")}
        for row in odds["probabilities"][:3]
    ] == [
        {
            "outcome": "Mexico",
            "price": "0.49",
            "marketUrl": "https://polymarket.com/event/mexico-vs-south-africa-fifa-world-cup-2026/mexico-vs-south-africa-mexico",
        },
        {
            "outcome": "Draw",
            "price": "0.29",
            "marketUrl": "https://polymarket.com/event/mexico-vs-south-africa-fifa-world-cup-2026/mexico-vs-south-africa-draw",
        },
        {
            "outcome": "South Africa",
            "price": "0.22",
            "marketUrl": "https://polymarket.com/event/mexico-vs-south-africa-fifa-world-cup-2026/mexico-vs-south-africa-south-africa",
        },
    ]


def test_worldcup_dashboard_merges_espn_scoreboard_results():
    source_schedule = {
        "matches": [
            {
                "num": 19,
                "date": "2026-06-13",
                "time": "20:00 UTC-5",
                "team1": "USA",
                "team2": "Paraguay",
                "group": "Group D",
                "round": "Matchday 1",
                "ground": "Los Angeles (Inglewood)",
            },
            {
                "num": 20,
                "date": "2026-06-13",
                "time": "21:00 UTC-7",
                "team1": "Australia",
                "team2": "Turkey",
                "group": "Group D",
                "round": "Matchday 1",
                "ground": "Vancouver",
            },
        ]
    }
    espn_scoreboard = {
        "events": [
            {
                "id": "760419",
                "date": "2026-06-14T01:00Z",
                "competitions": [
                    {
                        "date": "2026-06-14T01:00Z",
                        "altGameNote": "FIFA World Cup, Group D",
                        "status": {"type": {"completed": True, "state": "post", "shortDetail": "FT"}},
                        "competitors": [
                            {"homeAway": "home", "score": "3", "team": {"displayName": "United States"}},
                            {"homeAway": "away", "score": "1", "team": {"displayName": "Paraguay"}},
                        ],
                    }
                ],
            },
            {
                "id": "760421",
                "date": "2026-06-14T04:00Z",
                "competitions": [
                    {
                        "date": "2026-06-14T04:00Z",
                        "altGameNote": "FIFA World Cup, Group D",
                        "status": {"type": {"completed": True, "state": "post", "shortDetail": "FT"}},
                        "competitors": [
                            {"homeAway": "home", "score": "2", "team": {"displayName": "Australia"}},
                            {"homeAway": "away", "score": "1", "team": {"displayName": "Türkiye"}},
                        ],
                    }
                ],
            },
        ]
    }

    def http_json_get(url, *, params=None, timeout=12, headers=None):
        if "openfootball" in url:
            return source_schedule
        if "site.api.espn.com" in url:
            return espn_scoreboard
        raise AssertionError(url)

    ctx = {
        "http_json_get": http_json_get,
        "SETTINGS": SimpleNamespace(worldcup_market_link_scan_limit=0),
    }

    with patch.object(
        worldcup_builder.worldcup_intel_service,
        "get_worldcup_intel_snapshot",
        return_value={"status": "ok", "weather": [], "news": [], "signals": []},
    ):
        payload = worldcup_dashboard_service.build_worldcup_dashboard_payload(ctx, include_live_market_links=False)

    usa = next(match for match in payload["matches"] if match["id"] == "wc2026-019")
    australia = next(match for match in payload["matches"] if match["id"] == "wc2026-020")
    assert payload["providerStates"]["matchResults"] == "ok"
    assert payload["summary"]["scoreMatched"] == 2
    assert usa["homeScore"] == 3
    assert usa["awayScore"] == 1
    assert australia["homeScore"] == 2
    assert australia["awayScore"] == 1
    assert australia["scoreSource"] == "ESPN scoreboard"


def test_worldcup_split_merge_preserves_core_odds_and_live_results():
    core = {
        "generatedAt": "2026-06-16T00:00:00Z",
        "matches": [
            {
                "id": "wc2026-044",
                "homeTeam": "Saudi Arabia",
                "awayTeam": "Uruguay",
                "kickoffUtc": "2026-06-16T06:00:00Z",
                "kickoffBeijing": "2026-06-16T14:00:00+08:00",
                "kickoffLocal": "2026-06-16T02:00:00-04:00",
                "cityId": "miami",
                "city": "Miami Gardens",
                "venue": "Hard Rock Stadium",
                "stage": "group",
                "round": "Matchday 5",
                "status": "scheduled",
            }
        ],
        "odds": [{"matchId": "wc2026-044", "provider": "The Odds API", "providerType": "online_bookmaker", "marketType": "moneyline", "outcomes": []}],
        "providerStates": {"odds": "ok", "bookmakerOdds": "ok"},
        "bookmakerLinker": {"events": 57, "matched": 56},
        "marketLinker": {"matched": 4, "candidates": 292},
    }
    live = {
        "generatedAt": "2026-06-16T00:05:00Z",
        "matches": [
            {
                **core["matches"][0],
                "status": "finished",
                "homeScore": 1,
                "awayScore": 1,
                "scoreSource": "ESPN scoreboard",
                "scoreStatus": "FT",
            }
        ],
        "news": [{"id": "n1", "title": "Saudi Arabia vs Uruguay result", "source": "ESPN", "url": "", "publishedAt": "2026-06-16T00:05:00Z"}],
        "weather": [{"cityId": "miami", "current": {"tempC": 28, "condition": "Storm"}, "forecast": [], "generatedAt": "2026-06-16T00:05:00Z"}],
        "providerStates": {"matchResults": "ok", "weather": "ok", "odds": "core-seed-only", "bookmakerOdds": "core-seed-only"},
        "resultLinker": {"source": "ESPN scoreboard", "state": "ok", "matched": 1, "completed": 1},
    }

    payload = worldcup_builder.merge_worldcup_core_live_payload(core, live)

    assert payload["odds"] == core["odds"]
    assert payload["providerStates"]["odds"] == "ok"
    assert payload["providerStates"]["bookmakerOdds"] == "ok"
    assert payload["matches"][0]["status"] == "finished"
    assert payload["matches"][0]["homeScore"] == 1
    assert payload["summary"]["scoreMatched"] == 1


def test_worldcup_panel_payloads_are_independent_cache_projections():
    dashboard = {
        "generatedAt": "2026-06-16T00:05:00Z",
        "matches": [{"id": "wc2026-001", "homeTeam": "Spain", "awayTeam": "Cape Verde", "status": "scheduled"}],
        "odds": [{"matchId": "wc2026-001", "provider": "The Odds API", "providerType": "online_bookmaker", "marketType": "moneyline", "outcomes": []}],
        "news": [{"id": "n1", "title": "Spain team news", "source": "ESPN", "url": "", "publishedAt": "2026-06-16T00:05:00Z", "teams": ["Spain"]}],
        "weather": [{"cityId": "atlanta", "current": {"tempC": 28, "condition": "Clear"}, "forecast": [], "generatedAt": "2026-06-16T00:05:00Z"}],
        "providerStates": {"odds": "ok", "worldcupIntel": "ok"},
        "bookmakerLinker": {"matched": 1},
        "marketLinker": {"matched": 1},
    }

    panels = build_worldcup_panel_payloads(dashboard)

    assert "win-probability" in panels
    assert "news-impact" in panels
    assert panels["win-probability"]["panelId"] == "win-probability"
    assert panels["win-probability"]["data"]["odds"][0]["matchId"] == "wc2026-001"
    assert panels["news-impact"]["data"]["news"][0]["title"] == "Spain team news"
    assert panel_ttl_seconds("win-probability", 86400, 300) == 86400
    assert panel_ttl_seconds("news-impact", 86400, 300) == 300


def test_worldcup_dashboard_watcher_builds_new_odds_alert_candidate():
    previous = {"odds": [{"matchId": "wc2026-001"}]}
    current = {
        "odds": [
            {"matchId": "wc2026-001"},
            {
                "matchId": "wc2026-002",
                "homeTeam": "South Korea",
                "awayTeam": "Czech Republic",
                "marketTitle": "South Korea vs Czech Republic - FIFA World Cup 2026 winner",
                "marketUrl": "https://polymarket.com/event/south-korea-czech-republic",
                "probabilities": [{"outcome": "South Korea", "price": "0.42"}],
            },
        ]
    }

    new_rows = worldcup_dashboard_watcher._new_odds(previous, current)
    candidate = worldcup_dashboard_watcher._odds_alert_candidate(new_rows[0])

    assert len(new_rows) == 1
    assert candidate.topic == "worldcup"
    assert candidate.priority == "normal"
    assert "World Cup odds listed" in candidate.text
    assert "South Korea vs Czech Republic" in candidate.text
    assert candidate.reply_markup
