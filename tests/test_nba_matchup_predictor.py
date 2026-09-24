from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

from flask import Flask


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.routes.runtime_sports import create_runtime_sports_blueprint
from api.services import runtime_service


class FakeLogger:
    def exception(self, *args, **kwargs) -> None:
        return None


class FakeApp:
    logger = FakeLogger()


def make_event(event_id: str, short_name: str, away: str, home: str) -> Dict[str, Any]:
    return {
        "id": event_id,
        "name": f"{away} at {home}",
        "shortName": short_name,
        "date": "2026-04-25T00:00Z",
        "competitions": [
            {
                "id": event_id,
                "status": {"type": {"state": "pre", "description": "Scheduled"}},
                "competitors": [
                    {"homeAway": "away", "team": {"displayName": away}},
                    {"homeAway": "home", "team": {"displayName": home}},
                ],
            }
        ],
    }


def stat(name: str, value: float) -> Dict[str, Any]:
    return {"name": name, "value": value, "displayValue": str(value)}


class NbaMatchupPredictorTestCase(unittest.TestCase):
    def make_context(self, *, fail_event_id: str | None = None) -> Dict[str, Any]:
        scoreboard = {
            "events": [
                make_event("401", "LAL @ HOU", "Los Angeles Lakers", "Houston Rockets"),
                make_event("402", "BOS @ PHI", "Boston Celtics", "Philadelphia 76ers"),
            ]
        }
        predictors = {
            "401": {
                "shortName": "LAL @ HOU",
                "lastModified": "2026-04-25T02:49Z",
                "awayTeam": {
                    "statistics": [
                        stat("gameProjection", 33.6),
                        stat("matchupQuality", 11.278),
                        stat("teamPredPtDiff", -5.332),
                        stat("teamExpectedPts", 103.01),
                        stat("oppExpectedPts", 108.342),
                    ]
                },
                "homeTeam": {},
            },
            "402": {
                "shortName": "BOS @ PHI",
                "lastModified": "2026-04-25T05:24Z",
                "awayTeam": {
                    "statistics": [
                        stat("gameProjection", 62.3),
                        stat("matchupQuality", 56.571),
                        stat("teamPredPtDiff", 3.936),
                        stat("teamExpectedPts", 107.682),
                        stat("oppExpectedPts", 103.746),
                    ]
                },
                "homeTeam": {},
            },
        }

        def http_json_get(url, params=None, timeout=12, headers=None):
            if url.endswith("/scoreboard"):
                return scoreboard
            event_id = url.split("/events/", 1)[1].split("/", 1)[0]
            if event_id == fail_event_id:
                raise RuntimeError("temporary ESPN failure")
            return predictors[event_id]

        return {
            "SETTINGS": SimpleNamespace(
                espn_nba_base_url="fixture-espn-nba",
                espn_core_nba_base_url="fixture-espn-core-nba",
            ),
            "SPORTS_RUNTIME_TTL_SECONDS": 60,
            "app": FakeApp(),
            "_safe_float": lambda value: None if value in (None, "") else float(value),
            "http_json_get": http_json_get,
            "get_snapshot_payload": lambda namespace, cache_key, builder, ttl_seconds=60: builder(),
            "utc_now_iso": lambda: "2026-04-25T00:00:00Z",
        }

    def test_predictor_normalizes_away_projection_and_derives_home_projection(self):
        payload = runtime_service.get_nba_matchup_predictor_snapshot(self.make_context(), limit=2)

        self.assertEqual(2, len(payload["items"]))
        first = payload["items"][0]
        self.assertEqual("401", first["eventId"])
        self.assertEqual(33.6, first["awayWinProbability"])
        self.assertAlmostEqual(66.4, first["homeWinProbability"])
        self.assertEqual(-5.332, first["projectedMargin"])
        self.assertEqual(103.01, first["awayExpectedPoints"])
        self.assertEqual(108.342, first["homeExpectedPoints"])

    def test_predictor_returns_partial_results_when_one_event_fails(self):
        payload = runtime_service.get_nba_matchup_predictor_snapshot(self.make_context(fail_event_id="401"), limit=2)

        self.assertEqual(["402"], [item["eventId"] for item in payload["items"]])

    def test_route_clamps_invalid_and_large_limits(self):
        seen_limits = []
        app = Flask(__name__)
        app.register_blueprint(
            create_runtime_sports_blueprint(
                {
                    "get_nba_scoreboard_snapshot": lambda limit=10: {"limit": limit},
                    "get_nba_intel_snapshot": lambda limit=12: {"limit": limit},
                    "get_nba_matchup_predictor_snapshot": lambda limit=8: seen_limits.append(limit) or {"limit": limit},
                }
            )
        )

        with app.test_client() as client:
            invalid = client.get("/runtime/sports/nba-matchup-predictor?limit=nope")
            large = client.get("/runtime/sports/nba-matchup-predictor?limit=999")

        self.assertEqual(200, invalid.status_code)
        self.assertEqual(200, large.status_code)
        self.assertEqual([8, 16], seen_limits)


if __name__ == "__main__":
    unittest.main()
