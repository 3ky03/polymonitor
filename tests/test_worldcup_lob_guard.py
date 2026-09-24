from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from runtime import worldcup_lob_guard as guard


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def _match(**overrides):
    payload = {
        "homeTeam": "Netherlands",
        "awayTeam": "Sweden",
        "kickoffUtc": "2026-06-20T17:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_worldcup_lob_guard_selects_relevant_matches_by_real_kickoff_window():
    policy = guard.GuardPolicy(lookahead_hours=6, lookback_hours=2)
    payload = {
        "matches": [
            _match(homeTeam="Old", awayTeam="Done", kickoffUtc="2026-06-20T09:00:00Z"),
            _match(homeTeam="Netherlands", awayTeam="Sweden", kickoffUtc="2026-06-20T17:00:00Z"),
            _match(homeTeam="Germany", awayTeam="CIV", kickoffUtc="2026-06-20T20:00:00Z"),
            _match(homeTeam="Far", awayTeam="Future", kickoffUtc="2026-06-21T04:00:00Z"),
        ]
    }

    matches = guard.relevant_matches(payload, now=_dt("2026-06-20T15:00:00Z"), policy=policy)

    assert [guard.match_label(item) for item in matches] == ["Netherlands vs Sweden", "Germany vs CIV"]


def test_worldcup_lob_guard_prefixes_include_new_team_aliases():
    prefixes = guard.match_prefixes(_match(homeTeam="Côte d'Ivoire", awayTeam="Germany"))

    assert "fifwc-civ-ger-2026-06-20" in prefixes
    assert "fifwc-ger-civ-2026-06-20" in prefixes


def test_worldcup_lob_guard_active_match_requires_coverage_and_recent_snapshot():
    policy = guard.GuardPolicy()
    report = guard.evaluate_match(
        _match(),
        now=_dt("2026-06-20T16:15:00Z"),
        policy=policy,
        market={"tokenized": 3},
        snapshots={"rows15m": 0, "rowsInWindow": 0, "marketsInWindow": 0},
        coverage_count=0,
        ch_stats={"enabled": False},
    )

    failed = {item["name"] for item in report["checks"] if not item["ok"]}
    assert report["phase"] == "pre-kickoff-active"
    assert {"coverage-candidate", "active-coverage", "recent-snapshot"} <= failed


def test_worldcup_lob_guard_complete_match_checks_window_first_and_last_ts():
    policy = guard.GuardPolicy(completeness_tolerance_minutes=12)
    checks = guard.completeness_checks(
        {
            "rowsInWindow": 12,
            "marketsInWindow": 2,
            "firstTs": "2026-06-20T12:00:00Z",
            "lastTs": "2026-06-20T23:00:00Z",
            "firstTsInWindow": "2026-06-20T16:02:00Z",
            "lastTsInWindow": "2026-06-20T19:21:00Z",
        },
        start=_dt("2026-06-20T16:00:00Z"),
        end=_dt("2026-06-20T19:30:00Z"),
        policy=policy,
    )

    assert all(item["ok"] for item in checks)

    checks = guard.completeness_checks(
        {
            "rowsInWindow": 12,
            "marketsInWindow": 2,
            "firstTs": "2026-06-20T12:00:00Z",
            "lastTs": "2026-06-20T23:00:00Z",
            "firstTsInWindow": "2026-06-20T16:35:00Z",
            "lastTsInWindow": "2026-06-20T19:00:00Z",
        },
        start=_dt("2026-06-20T16:00:00Z"),
        end=_dt("2026-06-20T19:30:00Z"),
        policy=policy,
    )
    failed = {item["name"] for item in checks if not item["ok"]}
    assert failed == {"complete-start-covered", "complete-end-covered"}


def test_worldcup_lob_guard_dead_letter_alerts_are_deduped(monkeypatch):
    calls = []
    monkeypatch.setattr(guard.lob_service, "write_lob_dead_letter", lambda **kwargs: calls.append(kwargs))
    guard._WRITTEN_ALERT_SIGNATURES.clear()
    report = {
        "key": "netherlands-vs-sweden|2026-06-20T17:00:00",
        "label": "Netherlands vs Sweden",
        "phase": "pre-kickoff-active",
        "checks": [{"name": "recent-snapshot", "ok": False, "detail": "rows15m=0"}],
    }

    assert guard.write_alerts([dict(report)], dry_run=False) == 1
    assert guard.write_alerts([dict(report)], dry_run=False) == 0
    assert len(calls) == 1


def test_worldcup_lob_guard_does_not_alert_old_unmapped_completed_match():
    policy = guard.GuardPolicy()
    report = guard.evaluate_match(
        _match(homeTeam="Scotland", awayTeam="Morocco", kickoffUtc="2026-06-19T22:00:00Z"),
        now=_dt("2026-06-20T07:45:00Z"),
        policy=policy,
        market={"tokenized": 0},
        snapshots={"rows15m": 0, "rowsInWindow": 0, "marketsInWindow": 0},
        coverage_count=None,
        ch_stats={"enabled": False},
    )

    assert report["prefixes"] == []
    assert report["status"] == "unmapped"
    assert report["checks"] == []
