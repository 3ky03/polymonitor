from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.routes.system import create_system_blueprint
from api.services import system_service
from runtime.snapshot_store import SnapshotStore


class SeedHealthTestCase(unittest.TestCase):
    def make_context(self, *, redis_payloads=None, stale_payloads=None):
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        store = SnapshotStore(str(Path(snapshot_dir.name) / "seed-health.sqlite3"))
        for (namespace, cache_key), payload in (stale_payloads or {}).items():
            store.set(namespace, cache_key, payload, 1)
        return {
            "SNAPSHOT_STORE": store,
            "get_cached_json": lambda namespace, cache_key: (redis_payloads or {}).get((namespace, cache_key)),
            "utc_now_iso": lambda: "2026-05-03T08:30:00Z",
        }

    def test_build_seed_health_payload_reads_redis_seed_meta(self):
        redis_payloads = {
            ("seed-meta:world", "geo-sanctions-shock"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:28:30Z",
                "lastSuccessAt": "2999-05-03T08:28:30Z",
                "recordCount": 8,
                "sourceStates": {"conflictFeed": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:markets", "new-market-signals"): {
                "status": "error",
                "lastAttemptAt": "2999-05-03T08:29:55Z",
                "lastSuccessAt": "2999-05-03T08:20:00Z",
                "recordCount": 2,
                "errorSummary": "db unavailable",
                "cacheMode": "seeded",
                "payloadStatus": "error",
            },
            ("seed-meta:macro", "jin10-flash"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:50Z",
                "lastSuccessAt": "2999-05-03T08:29:50Z",
                "recordCount": 24,
                "sourceStates": {"jin10Flash": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:sports", "f1-trackside"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:45Z",
                "lastSuccessAt": "2999-05-03T08:29:45Z",
                "recordCount": 10,
                "sourceStates": {"bwenewsRss": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:sports", "nba"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:40Z",
                "lastSuccessAt": "2999-05-03T08:29:40Z",
                "recordCount": 30,
                "sourceStates": {"scoreboard": "ok", "intel": "ok", "predictor": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:markets", "commodities-watch"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:35Z",
                "lastSuccessAt": "2999-05-03T08:29:35Z",
                "recordCount": 24,
                "sourceStates": {"commodities": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:markets", "crypto-watch"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:30Z",
                "lastSuccessAt": "2999-05-03T08:29:30Z",
                "recordCount": 13,
                "sourceStates": {"crypto": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:crypto", "funding-watch"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:25Z",
                "lastSuccessAt": "2999-05-03T08:29:25Z",
                "recordCount": 8,
                "sourceStates": {"binance": "ok", "bybit": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:macro", "inflation-nowcast"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:20Z",
                "lastSuccessAt": "2999-05-03T08:29:20Z",
                "recordCount": 1,
                "sourceStates": {"clevelandFed": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:macro", "polymarket-macro-map"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:18Z",
                "lastSuccessAt": "2999-05-03T08:29:18Z",
                "recordCount": 12,
                "sourceStates": {"gammaEvents": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:signals", "alpha-signal"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:15Z",
                "lastSuccessAt": "2999-05-03T08:29:15Z",
                "recordCount": 8,
                "sourceStates": {"database": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:signals", "whale-trades"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:10Z",
                "lastSuccessAt": "2999-05-03T08:29:10Z",
                "recordCount": 14,
                "sourceStates": {"database": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:signals", "suspicious-trades"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:05Z",
                "lastSuccessAt": "2999-05-03T08:29:05Z",
                "recordCount": 12,
                "sourceStates": {"database": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
            ("seed-meta:bootstrap", "bootstrap"): {
                "status": "ok",
                "lastAttemptAt": "2999-05-03T08:29:00Z",
                "lastSuccessAt": "2999-05-03T08:29:00Z",
                "recordCount": 24,
                "sourceStates": {"bootstrap": "ok"},
                "cacheMode": "seeded",
                "payloadStatus": "ok",
            },
        }
        payload = system_service.build_seed_health_payload(self.make_context(redis_payloads=redis_payloads))

        self.assertEqual("error", payload["status"])
        self.assertEqual(len(system_service.SEED_META_SPECS), payload["summary"]["watcherCount"])
        geo = next(item for item in payload["items"] if item["panelId"] == "geo-sanctions-shock")
        self.assertEqual("ok", geo["status"])
        self.assertEqual("fresh", geo["freshness"])
        new_market = next(item for item in payload["items"] if item["panelId"] == "new-market-signals")
        self.assertEqual("error", new_market["status"])
        self.assertEqual("db unavailable", new_market["errorSummary"])

    def test_build_seed_health_payload_falls_back_to_stale_snapshot(self):
        aging_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
        stale_payloads = {
            ("seed-meta:world", "geo-sanctions-shock"): {
                "status": "ok",
                "lastAttemptAt": aging_timestamp,
                "lastSuccessAt": aging_timestamp,
                "recordCount": 4,
            }
        }
        payload = system_service.build_seed_health_payload(self.make_context(stale_payloads=stale_payloads))

        geo = next(item for item in payload["items"] if item["panelId"] == "geo-sanctions-shock")
        self.assertEqual("degraded", geo["status"])
        self.assertEqual("aging", geo["freshness"])

    def test_seed_health_route_returns_json(self):
        app = Flask(__name__)
        helpers = {
            "build_system_health_payload": lambda: {"status": "ok"},
            "build_seed_health_payload": lambda: {"status": "ok", "items": [{"panelId": "geo-sanctions-shock"}]},
            "describe_db_target": lambda: "mysql:test",
            "get_redis_client": lambda: object(),
        }
        app.register_blueprint(create_system_blueprint(helpers))

        with app.test_client() as client:
            response = client.get("/runtime/system/seed-health")

        self.assertEqual(200, response.status_code)
        self.assertEqual("ok", response.get_json()["status"])


if __name__ == "__main__":
    unittest.main()
