from __future__ import annotations

from datetime import datetime, timezone

from quant.backtest.runners import selectors
from quant.backtest.runners.selectors import ResolvedMarketCandidate, UniverseSpec


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self.rows)


def test_postgres_selector_query_retries_connection_timeout(monkeypatch):
    attempts = {"count": 0}

    def fake_postgres_connection(readonly=True):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("connection timeout expired")
        return FakeConnection([{"market_id": 1}])

    monkeypatch.setattr(selectors, "postgres_connection", fake_postgres_connection)
    monkeypatch.setattr(selectors.time, "sleep", lambda _seconds: None)

    rows = selectors._query_postgres_rows("select 1", ())

    assert rows == [{"market_id": 1}]
    assert attempts["count"] == 2


def test_postgres_selector_query_does_not_retry_non_connection_errors(monkeypatch):
    attempts = {"count": 0}

    def fake_postgres_connection(readonly=True):
        attempts["count"] += 1
        raise RuntimeError("syntax error")

    monkeypatch.setattr(selectors, "postgres_connection", fake_postgres_connection)

    try:
        selectors._query_postgres_rows("select broken", ())
    except RuntimeError as exc:
        assert "syntax error" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert attempts["count"] == 1


def test_replay_universe_selector_uses_file_cache(monkeypatch, tmp_path):
    calls = {"count": 0}
    candidate = ResolvedMarketCandidate(
        market_id=7,
        market_slug="nba-abc-def-2025-01-01",
        title="NBA ABC DEF",
        end_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        settlement_code=1,
        settlement_outcome="YES",
        event_slug="nba_2024_25",
        category="sports",
        token_yes_id="yes-token",
        token_no_id="no-token",
        coverage_status="raw_orderfilled",
        orderfilled_rows=123,
        block_rows=45,
    )

    def fake_uncached(spec):
        calls["count"] += 1
        return [candidate]

    monkeypatch.setenv("POLYDATA_BACKTEST_UNIVERSE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("POLYDATA_BACKTEST_UNIVERSE_CACHE_TTL_SECONDS", "3600")
    monkeypatch.setattr(selectors, "_select_replay_universe_uncached", fake_uncached)

    spec = UniverseSpec(universe_name="nba_2024_25_moneyline", universe_type="preset", limit=1, category="sports")
    first = selectors.select_replay_universe(spec)
    second = selectors.select_replay_universe(spec)

    assert calls["count"] == 1
    assert first == [candidate]
    assert second[0].market_id == candidate.market_id
    assert second[0].end_date == candidate.end_date
    assert second[0].token_yes_id == "yes-token"


def test_replay_universe_selector_cache_can_be_disabled(monkeypatch, tmp_path):
    calls = {"count": 0}

    def fake_uncached(spec):
        calls["count"] += 1
        return [
            ResolvedMarketCandidate(
                market_id=calls["count"],
                market_slug=f"market-{calls['count']}",
                title="Market",
                end_date=None,
                settlement_code=1,
                settlement_outcome="YES",
            )
        ]

    monkeypatch.setenv("POLYDATA_BACKTEST_UNIVERSE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("POLYDATA_BACKTEST_UNIVERSE_CACHE_ENABLED", "0")
    monkeypatch.setattr(selectors, "_select_replay_universe_uncached", fake_uncached)

    spec = UniverseSpec(universe_name="nba_2024_25_moneyline", universe_type="preset", limit=1)
    first = selectors.select_replay_universe(spec)
    second = selectors.select_replay_universe(spec)

    assert calls["count"] == 2
    assert first[0].market_id == 1
    assert second[0].market_id == 2
