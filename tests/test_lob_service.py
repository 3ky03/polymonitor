from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import lob_service
from api.services.lob_service import LocalOrderBookRuntimeManager, _book_side_summary, _unchanged_snapshot_min_interval_seconds
from quant.orderbook import TokenBookIdentity


def test_book_side_summary_sorts_levels_and_computes_notional_depth():
    summary = _book_side_summary({
        "bids": [
            {"price": "0.04", "size": "100"},
            {"price": "0.07", "size": "50"},
        ],
        "asks": [
            {"price": "0.98", "size": "10"},
            {"price": "0.81", "size": "20"},
        ],
    })

    assert summary["bids"][0]["price"] == "0.07"
    assert summary["asks"][0]["price"] == "0.81"
    assert summary["bestBid"] == "0.07"
    assert summary["bestAsk"] == "0.81"
    assert summary["spread"] == "0.74"
    assert summary["mid"] == "0.44"
    assert summary["bidDepth"] == "7.50"
    assert summary["askDepth"] == "26.00"
    assert summary["depthTotal"] == "33.50"
    assert summary["imbalance"].startswith("0.223880")


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, payloads):
        self.payloads = payloads
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        token_id = (params or {}).get("token_id")
        self.calls.append((url, token_id, timeout))
        payload = self.payloads.get(token_id)
        if payload is None:
            return FakeResponse({"bids": [], "asks": []}, status_code=404)
        return FakeResponse(payload)


def test_local_orderbook_runtime_manager_feeds_rest_book_into_registry():
    session = FakeSession({
        "yes-token": {
            "bids": [{"price": "0.184", "size": "25549.68"}],
            "asks": [{"price": "0.185", "size": "15090.41"}],
        },
        "no-token": {
            "bids": [{"price": "0.815", "size": "100"}],
            "asks": [{"price": "0.816", "size": "200"}],
        },
    })
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=session, cache_ttl_seconds=30)

    payload = manager.get_market_snapshot(
        market_id=42,
        yes_token_id="yes-token",
        no_token_id="no-token",
        market_title="Sample market",
        condition_id="0xcondition",
    )

    assert payload["source"] == "local-orderbook"
    assert payload["runtimeModel"] == "LocalOrderBook"
    assert payload["bookStatus"] == "ok"
    assert payload["yes"]["bestBid"] == "0.184"
    assert payload["yes"]["bestAsk"] == "0.185"
    assert payload["yes"]["statePayload"]["snapshot_source"] == "rest-book"
    assert payload["yes"]["statePayload"]["runtime_model"] == "LocalOrderBook"
    assert manager.registry.get("yes-token").snapshot_payload(depth_levels=12)["best_bid"] == "0.184"


def test_local_orderbook_runtime_manager_force_refresh_bypasses_cache():
    session = FakeSession({
        "yes-token": {"bids": [{"price": "0.40", "size": "10"}], "asks": [{"price": "0.41", "size": "10"}]},
    })
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=session, cache_ttl_seconds=30)

    manager.get_token_snapshot(token_id="yes-token")
    manager.get_token_snapshot(token_id="yes-token")
    manager.get_token_snapshot(token_id="yes-token", force_refresh=True)

    assert [call[1] for call in session.calls] == ["yes-token", "yes-token"]


def test_local_orderbook_runtime_manager_applies_websocket_price_change_to_registry():
    session = FakeSession({
        "yes-token": {"bids": [{"price": "0.40", "size": "10"}], "asks": [{"price": "0.42", "size": "10"}]},
    })
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=session, cache_ttl_seconds=30)
    identity = TokenBookIdentity("yes-token", 42, "0xcondition", "YES", 0)

    manager.get_token_snapshot(token_id="yes-token", market_id=42, condition_id="0xcondition", outcome="YES")
    applied = manager.apply_polymarket_event(
        {
            "event_type": "price_change",
            "timestamp": "9999999999999",
            "price_changes": [{"asset_id": "yes-token", "side": "BUY", "price": "0.41", "size": "12"}],
        },
        {"yes-token": identity},
    )

    assert applied
    assert applied[0]["snapshot_source"] == "websocket"
    assert applied[0]["best_bid"] == "0.41"
    cached = manager.get_cached_market_snapshot(
        market_id=42,
        yes_token_id="yes-token",
        no_token_id="",
        market_title="Sample",
    )
    assert cached["snapshotSource"] == "mixed"
    assert cached["yes"]["bestBid"] == "0.41"


def test_local_orderbook_runtime_manager_prefers_ready_registry_after_cache_expiry():
    session = FakeSession({
        "yes-token": {"bids": [{"price": "0.40", "size": "10"}], "asks": [{"price": "0.42", "size": "10"}]},
    })
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=session, cache_ttl_seconds=30)
    identity = TokenBookIdentity("yes-token", 42, "0xcondition", "YES", 0)

    manager.get_token_snapshot(token_id="yes-token", market_id=42, condition_id="0xcondition", outcome="YES")
    manager.apply_polymarket_event(
        {
            "event_type": "price_change",
            "timestamp": "9999999999999",
            "price_changes": [{"asset_id": "yes-token", "side": "BUY", "price": "0.41", "size": "12"}],
        },
        {"yes-token": identity},
    )
    manager._cache["yes-token"]["cached_at"] = 0
    before_calls = len(session.calls)

    payload = manager.get_token_snapshot(token_id="yes-token", market_id=42, condition_id="0xcondition", outcome="YES")

    assert len(session.calls) == before_calls
    assert payload["snapshot_source"] == "websocket"
    assert payload["best_bid"] == "0.41"


def test_token_lob_payload_persists_state_machine_payload(monkeypatch):
    session = FakeSession({
        "yes-token": {
            "bids": [{"price": "0.40", "size": "10"}],
            "asks": [{"price": "0.41", "size": "12"}],
        }
    })
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=session, cache_ttl_seconds=30)
    persisted = []

    monkeypatch.setattr(
        lob_service,
        "_persist_orderbook_snapshots",
        lambda ctx, payload, yes_token_id, no_token_id: persisted.append((payload, yes_token_id, no_token_id)),
    )

    class FakeLogger:
        def exception(self, *args, **kwargs):
            raise AssertionError(args)

    ctx = {"LOB_RUNTIME_MANAGER": manager, "app": type("FakeApp", (), {"logger": FakeLogger()})()}

    payload = lob_service.get_runtime_lob_by_token_payload(ctx, "yes-token", market_title="Token mode")

    assert payload["yes"]["bestBid"] == "0.4"
    assert persisted
    persisted_payload, yes_token_id, no_token_id = persisted[0]
    assert yes_token_id == "yes-token"
    assert no_token_id == ""
    assert persisted_payload["yes"]["statePayload"]["source"] == "local-orderbook"
    assert persisted_payload["yes"]["statePayload"]["snapshot_version"]


def test_lob_snapshot_persistence_writes_hardened_columns(monkeypatch):
    statements = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def execute(self, sql, params=()):
            statements.append((sql, params))

        def fetchone(self):
            return None

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    @contextmanager
    def fake_postgres_connection(*args, **kwargs):
        yield FakeConn()

    monkeypatch.setattr(lob_service, "_ensure_snapshot_schema", lambda: None)
    monkeypatch.setattr(lob_service, "postgres_connection", fake_postgres_connection)

    lob_service._persist_book_side_snapshot(
        {},
        token_id="yes-token",
        side_name="YES",
        paired_token_id="no-token",
        market_title="Bitcoin above 100k?",
        source="local-orderbook",
        book_status="ok",
        fetched_at="2026-06-18T00:00:00Z",
        side_payload={
            "statePayload": {
                "market_id": 42,
                "condition_id": "0xcondition",
                "market_slug": "bitcoin-above-100k",
                "snapshot_source": "websocket",
                "generation": 7,
                "last_event_ts_ms": 123456789,
                "snapshot_version": "abc123",
            },
            "bids": [{"price": "0.40", "size": "10"}],
            "asks": [{"price": "0.42", "size": "10"}],
        },
        coverage_payload={"tier": "hot", "topic": "crypto"},
    )

    insert_sql, params = statements[-1]
    assert "market_id, condition_id, market_slug, market_title" in insert_sql
    assert "snapshot_source, storage_tier, book_generation, last_event_ts_ms" in insert_sql
    assert params[3:12] == (
        42,
        "0xcondition",
        "bitcoin-above-100k",
        "Bitcoin above 100k?",
        "local-orderbook",
        "websocket",
        "hot",
        7,
        123456789,
    )


def test_local_orderbook_runtime_manager_marks_books_stale_and_counts():
    session = FakeSession({
        "yes-token": {"bids": [{"price": "0.40", "size": "10"}], "asks": [{"price": "0.42", "size": "10"}]},
    })
    manager = LocalOrderBookRuntimeManager(api_base="https://clob.test", session=session, cache_ttl_seconds=30)
    manager.get_token_snapshot(token_id="yes-token", market_id=42, condition_id="0xcondition", outcome="YES")

    assert manager.runtime_book_counts()["readyCount"] == 1
    assert manager.mark_all_stale("websocket_reconnect") == 1
    counts = manager.runtime_book_counts()

    assert counts["staleCount"] == 1
    assert counts["readyCount"] == 0


def test_unchanged_snapshot_min_interval_env(monkeypatch):
    monkeypatch.delenv("POLYDATA_LOB_UNCHANGED_SNAPSHOT_MIN_INTERVAL_SECONDS", raising=False)
    assert _unchanged_snapshot_min_interval_seconds() == 300

    monkeypatch.setenv("POLYDATA_LOB_UNCHANGED_SNAPSHOT_MIN_INTERVAL_SECONDS", "0")
    assert _unchanged_snapshot_min_interval_seconds() == 0

    monkeypatch.setenv("POLYDATA_LOB_UNCHANGED_SNAPSHOT_MIN_INTERVAL_SECONDS", "bad")
    assert _unchanged_snapshot_min_interval_seconds() == 300


def test_lob_coverage_targets_payload_prioritizes_requested_topics():
    class FakeLogger:
        def exception(self, *args, **kwargs):
            raise AssertionError(args)

    rows = [
        {
            "market_id": 11,
            "market_slug": "2026-fifa-world-cup-winner",
            "market_title": "2026 FIFA World Cup winner",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-wc",
            "no_token_id": "no-wc",
            "volume_24h": "70000",
            "trade_count_24h": 250,
        },
        {
            "market_id": 12,
            "market_slug": "bitcoin-100k",
            "market_title": "Bitcoin above 100k?",
            "category": "crypto",
            "tags": ["crypto", "bitcoin"],
            "yes_token_id": "yes-btc",
            "no_token_id": "no-btc",
            "volume_24h": "15000",
            "trade_count_24h": 40,
        },
        {
            "market_id": 13,
            "market_slug": "rain-nyc",
            "market_title": "Rain in NYC?",
            "category": "weather",
            "tags": ["weather"],
            "yes_token_id": "yes-rain",
            "no_token_id": "no-rain",
        },
    ]
    ctx = {
        "query_all": lambda sql, params=(): rows,
        "get_world_cup_match_ops_snapshot": lambda limit=48: {
            "items": [
                {
                    "matchStatus": "live",
                    "minutesUntilKickoff": -35,
                    "homeTeam": "Mexico",
                    "awayTeam": "South Africa",
                    "relatedPolymarketMarketIds": [11],
                    "markets": [{"marketId": 11}],
                }
            ]
        },
        "app": type("FakeApp", (), {"logger": FakeLogger()})(),
    }

    payload = lob_service.get_lob_coverage_targets_payload(ctx, limit=10, topics="worldcup,crypto")

    assert payload["source"] == "local-orderbook-coverage-policy"
    assert payload["summary"]["marketCount"] == 2
    assert payload["summary"]["tokenCount"] == 4
    assert [item["topic"] for item in payload["items"]] == ["worldcup", "crypto"]
    assert payload["items"][0]["tier"] == "hot"
    assert payload["items"][1]["sampleIntervalSeconds"] == 60


def test_lob_coverage_targets_honors_worldcup_market_limit(monkeypatch):
    class FakeLogger:
        def exception(self, *args, **kwargs):
            raise AssertionError(args)

    monkeypatch.setenv("POLYDATA_LOB_WORLDCUP_MARKET_LIMIT", "3")
    rows = [
        {
            "market_id": market_id,
            "market_slug": f"fifwc-match-{market_id}",
            "market_title": f"FIFA World Cup live match market {market_id}",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": f"yes-{market_id}",
            "no_token_id": f"no-{market_id}",
            "volume_24h": str(1000 + market_id),
        }
        for market_id in (101, 102, 103, 104)
    ]
    ctx = {
        "query_all": lambda sql, params=(): rows,
        "get_world_cup_match_ops_snapshot": lambda limit=48: {
            "items": [
                {
                    "matchStatus": "live",
                    "relatedPolymarketMarketIds": [101, 102, 103, 104],
                    "markets": [{"marketId": 101}, {"marketId": 102}, {"marketId": 103}, {"marketId": 104}],
                }
            ]
        },
        "app": type("FakeApp", (), {"logger": FakeLogger()})(),
    }

    payload = lob_service.get_lob_coverage_targets_payload(ctx, limit=10, topics="worldcup")

    assert payload["selectionLimits"]["worldcupMarketLimit"] == 3
    assert payload["summary"]["topics"]["worldcup"] == 3
    assert len(payload["items"]) == 3
    assert all(item["topic"] == "worldcup" for item in payload["items"])


def test_lob_coverage_worldcup_default_limit_allows_real_schedule_days(monkeypatch):
    class FakeLogger:
        def exception(self, *args, **kwargs):
            raise AssertionError(args)

    monkeypatch.delenv("POLYDATA_LOB_WORLDCUP_MARKET_LIMIT", raising=False)
    rows = [
        {
            "market_id": market_id,
            "market_slug": f"fifwc-match-{market_id}",
            "market_title": f"FIFA World Cup live match market {market_id}",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": f"yes-{market_id}",
            "no_token_id": f"no-{market_id}",
            "volume_24h": str(1000 + market_id),
        }
        for market_id in (101, 102, 103, 104, 105)
    ]
    ctx = {
        "query_all": lambda sql, params=(): rows,
        "get_world_cup_match_ops_snapshot": lambda limit=48: {
            "items": [
                {
                    "matchStatus": "live",
                    "relatedPolymarketMarketIds": [101, 102, 103, 104, 105],
                    "markets": [{"marketId": market_id} for market_id in (101, 102, 103, 104, 105)],
                }
            ]
        },
        "app": type("FakeApp", (), {"logger": FakeLogger()})(),
    }

    payload = lob_service.get_lob_coverage_targets_payload(ctx, limit=10, topics="worldcup")

    assert payload["selectionLimits"]["worldcupMarketLimit"] == 12
    assert payload["summary"]["topics"]["worldcup"] == 5
    assert len(payload["items"]) == 5


def test_lob_coverage_derives_worldcup_fixture_slug_prefixes(monkeypatch):
    class FakeLogger:
        def exception(self, *args, **kwargs):
            raise AssertionError(args)

    rows = [
        {
            "market_id": 101,
            "market_slug": "fifwc-che-bih-2026-06-18-spread-home-4pt5",
            "market_title": "Spread: Switzerland (-4.5)",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-101",
            "no_token_id": "no-101",
        },
        {
            "market_id": 102,
            "market_slug": "fifwc-can-qat-2026-06-18-total-7pt5",
            "market_title": "Canada vs. Qatar: O/U 7.5",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-102",
            "no_token_id": "no-102",
        },
        {
            "market_id": 103,
            "market_slug": "fifwc-mex-kr-2026-06-18-spread-home-3pt5",
            "market_title": "Spread: Mexico (-3.5)",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-103",
            "no_token_id": "no-103",
        },
        {
            "market_id": 104,
            "market_slug": "fifwc-usa-aus-2026-06-19-total-7pt5",
            "market_title": "United States vs. Australia: O/U 7.5",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-104",
            "no_token_id": "no-104",
        },
        {
            "market_id": 105,
            "market_slug": "fifwc-par-tur-2026-06-19-spread-home-3pt5",
            "market_title": "Spread: Paraguay (-3.5)",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-105",
            "no_token_id": "no-105",
        },
        {
            "market_id": 106,
            "market_slug": "fifwc-nld-swe-2026-06-20-spread-home-3pt5",
            "market_title": "Spread: Netherlands (-3.5)",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-106",
            "no_token_id": "no-106",
        },
        {
            "market_id": 107,
            "market_slug": "fifwc-ger-civ-2026-06-20-total-2pt5",
            "market_title": "Germany vs. Cote d'Ivoire: O/U 2.5",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-107",
            "no_token_id": "no-107",
        },
        {
            "market_id": 108,
            "market_slug": "fifwc-ecu-cur-2026-06-20-spread-home-2pt5",
            "market_title": "Spread: Ecuador (-2.5)",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-108",
            "no_token_id": "no-108",
        },
        {
            "market_id": 109,
            "market_slug": "fifwc-tun-jpn-2026-06-21-total-2pt5",
            "market_title": "Tunisia vs. Japan: O/U 2.5",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-109",
            "no_token_id": "no-109",
        },
    ]
    ctx = {
        "query_all": lambda sql, params=(): rows,
        "get_world_cup_match_ops_snapshot": lambda limit=48: {
            "items": [
                {"matchStatus": "scheduled", "minutesUntilKickoff": 55, "kickoffUtc": "2026-06-18T19:00:00Z", "homeTeam": "Switzerland", "awayTeam": "Bosnia & Herzegovina"},
                {"matchStatus": "scheduled", "minutesUntilKickoff": 55, "kickoffUtc": "2026-06-18T22:00:00Z", "homeTeam": "Canada", "awayTeam": "Qatar"},
                {"matchStatus": "scheduled", "minutesUntilKickoff": 55, "kickoffUtc": "2026-06-19T01:00:00Z", "homeTeam": "Mexico", "awayTeam": "South Korea"},
                {"matchStatus": "scheduled", "minutesUntilKickoff": 55, "kickoffUtc": "2026-06-19T19:00:00Z", "homeTeam": "USA", "awayTeam": "Australia"},
                {"matchStatus": "scheduled", "minutesUntilKickoff": 55, "kickoffUtc": "2026-06-20T01:00:00Z", "homeTeam": "Paraguay", "awayTeam": "Turkey"},
                {"matchStatus": "scheduled", "minutesUntilKickoff": 55, "kickoffUtc": "2026-06-20T17:00:00Z", "homeTeam": "Netherlands", "awayTeam": "Sweden"},
                {"matchStatus": "scheduled", "minutesUntilKickoff": 55, "kickoffUtc": "2026-06-20T20:00:00Z", "homeTeam": "Germany", "awayTeam": "Côte d'Ivoire"},
                {"matchStatus": "scheduled", "minutesUntilKickoff": 55, "kickoffUtc": "2026-06-21T00:00:00Z", "homeTeam": "Ecuador", "awayTeam": "Curaçao"},
                {"matchStatus": "scheduled", "minutesUntilKickoff": 55, "kickoffUtc": "2026-06-21T04:00:00Z", "homeTeam": "Tunisia", "awayTeam": "Japan"},
            ]
        },
        "app": type("FakeApp", (), {"logger": FakeLogger()})(),
    }

    payload = lob_service.get_lob_coverage_targets_payload(ctx, limit=10, topics="worldcup")

    assert payload["summary"]["topics"]["worldcup"] == 9
    assert {item["marketId"] for item in payload["items"]} == {101, 102, 103, 104, 105, 106, 107, 108, 109}
    assert (payload["selectionContext"]["worldcup"] or {}).get("activeMatchCount") == 9


def test_lob_coverage_excludes_finished_worldcup_match_even_inside_time_window(monkeypatch):
    class FakeLogger:
        def exception(self, *args, **kwargs):
            raise AssertionError(args)

    rows = [
        {
            "market_id": 101,
            "market_slug": "fifwc-cze-rsa-2026-06-18-spread-away-4pt5",
            "market_title": "Spread: South Africa (-4.5)",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-101",
            "no_token_id": "no-101",
        }
    ]
    ctx = {
        "query_all": lambda sql, params=(): rows,
        "get_world_cup_match_ops_snapshot": lambda limit=48: {
            "items": [
                {
                    "matchStatus": "final",
                    "minutesUntilKickoff": -100,
                    "homeTeam": "Czech Republic",
                    "awayTeam": "South Africa",
                    "markets": [{"slug": "fifwc-cze-rsa-2026-06-18"}],
                }
            ]
        },
        "app": type("FakeApp", (), {"logger": FakeLogger()})(),
    }

    payload = lob_service.get_lob_coverage_targets_payload(ctx, limit=10, topics="worldcup")

    assert payload["summary"]["topics"]["worldcup"] == 0
    assert payload["items"] == []


def test_lob_coverage_starts_worldcup_one_hour_before_kickoff(monkeypatch):
    class FakeLogger:
        def exception(self, *args, **kwargs):
            raise AssertionError(args)

    rows = [
        {
            "market_id": 101,
            "market_slug": "fifwc-cze-rsa-2026-06-18-spread-away-4pt5",
            "market_title": "Spread: South Africa (-4.5)",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-101",
            "no_token_id": "no-101",
        }
    ]

    def payload_for(minutes):
        ctx = {
            "query_all": lambda sql, params=(): rows,
            "get_world_cup_match_ops_snapshot": lambda limit=48: {
                "items": [
                    {
                        "matchStatus": "scheduled",
                        "minutesUntilKickoff": minutes,
                        "homeTeam": "Czech Republic",
                        "awayTeam": "South Africa",
                        "markets": [{"slug": "fifwc-cze-rsa-2026-06-18"}],
                    }
                ]
            },
            "app": type("FakeApp", (), {"logger": FakeLogger()})(),
        }
        return lob_service.get_lob_coverage_targets_payload(ctx, limit=10, topics="worldcup")

    assert payload_for(70)["items"] == []
    assert len(payload_for(55)["items"]) == 1


def test_lob_coverage_keeps_schedule_only_worldcup_match_until_fallback_end(monkeypatch):
    class FakeLogger:
        def exception(self, *args, **kwargs):
            raise AssertionError(args)

    rows = [
        {
            "market_id": 101,
            "market_slug": "fifwc-cze-rsa-2026-06-18-spread-away-4pt5",
            "market_title": "Spread: South Africa (-4.5)",
            "category": "sports",
            "tags": ["world-cup"],
            "yes_token_id": "yes-101",
            "no_token_id": "no-101",
        }
    ]

    def payload_for(minutes):
        ctx = {
            "query_all": lambda sql, params=(): rows,
            "get_world_cup_match_ops_snapshot": lambda limit=48: {
                "items": [
                    {
                        "matchStatus": "finished",
                        "minutesUntilKickoff": minutes,
                        "homeTeam": "Czech Republic",
                        "awayTeam": "South Africa",
                        "score": {"home": None, "away": None},
                        "markets": [{"slug": "fifwc-cze-rsa-2026-06-18"}],
                    }
                ]
            },
            "app": type("FakeApp", (), {"logger": FakeLogger()})(),
        }
        return lob_service.get_lob_coverage_targets_payload(ctx, limit=10, topics="worldcup")

    assert len(payload_for(-100)["items"]) == 1
    assert payload_for(-170)["items"] == []
