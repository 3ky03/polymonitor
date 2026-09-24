from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from flask import Flask


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.routes.bootstrap import create_bootstrap_blueprint
from api.services import bootstrap_service


class FakeLogger:
    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def exception(self, *args, **kwargs) -> None:
        return None


class FakeApp:
    logger = FakeLogger()


class FakeSnapshotStore:
    def __init__(self, fresh: Optional[Dict[tuple[str, str], Any]] = None, stale: Optional[Dict[tuple[str, str], Any]] = None):
        self.fresh = dict(fresh or {})
        self.stale = dict(stale or {})
        self.set_calls: List[tuple[str, str, Any, int]] = []

    def get(self, namespace: str, cache_key: str):
        return self.fresh.get((namespace, cache_key))

    def get_stale(self, namespace: str, cache_key: str):
        return self.stale.get((namespace, cache_key))

    def set(self, namespace: str, cache_key: str, payload: Any, ttl_seconds: int) -> None:
        self.fresh[(namespace, cache_key)] = payload
        self.stale[(namespace, cache_key)] = payload
        self.set_calls.append((namespace, cache_key, payload, ttl_seconds))


class FakeThread:
    def __init__(self, tracker: Dict[str, Any], target=None, name: str | None = None, daemon: bool | None = None):
        self._tracker = tracker
        self._target = target
        self.name = name
        self.daemon = daemon

    def start(self) -> None:
        self._tracker["starts"] = self._tracker.get("starts", 0) + 1
        if self._tracker.get("run_target") and self._target is not None:
            self._target()


class FakeThreadingModule:
    def __init__(self, tracker: Optional[Dict[str, Any]] = None):
        self._tracker = tracker or {}
        self.Lock = threading.Lock

    def Thread(self, target=None, name: str | None = None, daemon: bool | None = None):
        return FakeThread(self._tracker, target=target, name=name, daemon=daemon)


class FakeLOBManager:
    def get_market_snapshot(self, *args, **kwargs):
        raise AssertionError("LOB runtime should not be called from bootstrap")


class BootstrapPhase1TestCase(unittest.TestCase):
    def make_market_row(
        self,
        market_id: int,
        *,
        status: str = "Active",
        yes_token_id: str = "",
        no_token_id: str = "",
        volume_24h: str = "0",
        trade_count_24h: int = 0,
        last_trade_at: str | None = None,
        created_at: str = "2026-04-20T00:00:00Z",
    ) -> Dict[str, Any]:
        return {
            "id": market_id,
            "slug": f"market-{market_id}",
            "title": f"Market {market_id}",
            "condition_id": f"condition-{market_id}",
            "question_id": f"question-{market_id}",
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "category": "Politics",
            "tags": json.dumps(["macro", "election"]),
            "end_date": "2026-12-31T00:00:00Z",
            "created_at": created_at,
            "latest_price": "0.55",
            "latest_trade_at": last_trade_at,
            "trade_count_24h": trade_count_24h,
            "volume_24h": volume_24h,
            "last_trade_at": last_trade_at,
            "status": status,
        }

    def make_service_context(
        self,
        *,
        candidate_rows: Optional[List[Dict[str, Any]]] = None,
        status_rows: Optional[List[Dict[str, Any]]] = None,
        markets_by_id: Optional[Dict[int, Dict[str, Any]]] = None,
        fallback_featured_id: Optional[int] = None,
        latest_content_from_db: bool = True,
        cached_json: Optional[Dict[str, Any]] = None,
        snapshot_store: Optional[FakeSnapshotStore] = None,
        fake_threading: Optional[FakeThreadingModule] = None,
    ) -> Dict[str, Any]:
        candidate_rows = list(candidate_rows or [])
        status_rows = list(status_rows or [])
        markets_by_id = dict(markets_by_id or {})
        snapshot_store = snapshot_store or FakeSnapshotStore()
        redis_cache: Dict[str, Any] = {}
        if cached_json is not None:
            redis_cache["bootstrap"] = cached_json

        def parse_json_list(raw: Any) -> List[str]:
            if isinstance(raw, str) and raw:
                return json.loads(raw)
            if isinstance(raw, list):
                return raw
            return []

        def query_all(sql: str, params=()):
            if "FROM markets m" in sql and "market_trade_daily_stats" in sql:
                return [dict(row) for row in candidate_rows]
            if "FROM oracle_events" in sql and "GROUP BY market_id" in sql:
                return [dict(row) for row in status_rows]
            return []

        def query_one(sql: str, params=()):
            if "WITH settled_markets AS" in sql and "SELECT m.id" in sql:
                return {"id": fallback_featured_id}
            if "SELECT id FROM markets ORDER BY created_at DESC LIMIT 1" in sql:
                return {"id": fallback_featured_id or (sorted(markets_by_id.keys())[-1] if markets_by_id else None)}
            if "FROM market_latest_prices" in sql:
                market_id = int(params[0])
                market = markets_by_id.get(market_id, {})
                return {
                    "market_id": market_id,
                    "latest_price": market.get("latest_price", "0.55"),
                    "latest_yes_price": market.get("latest_yes_price", "0.55"),
                    "latest_no_price": market.get("latest_no_price", "0.45"),
                    "latest_trade_at": market.get("latest_trade_at", "2026-04-21T00:00:00Z"),
                }
            if "SUM(trade_count) AS trade_count_24h" in sql and "market_trade_daily_stats" in sql:
                market_id = int(params[0])
                market = markets_by_id.get(market_id, {})
                return {
                    "trade_count_24h": market.get("trade_count_24h", 0),
                    "volume_24h": market.get("volume_24h", "0"),
                    "updated_at": market.get("last_trade_at"),
                }
            return {}

        def get_market_by_id(market_id: int):
            return dict(markets_by_id.get(int(market_id), {})) if int(market_id) in markets_by_id else None

        def normalize_market(row: Dict[str, Any]):
            return {
                "id": row.get("id"),
                "slug": row.get("slug"),
                "title": row.get("title"),
                "status": row.get("status"),
                "yesTokenId": row.get("yes_token_id"),
                "noTokenId": row.get("no_token_id"),
                "latestPrice": row.get("latest_price"),
                "category": row.get("category"),
                "tags": parse_json_list(row.get("tags")),
            }

        def get_bootstrap_component_cached(component_key: str, builder, *, ttl_seconds: int = 60):
            return builder()

        ctx: Dict[str, Any] = {
            "BOOTSTRAP_CACHE_TTL_SECONDS": 30,
            "BOOTSTRAP_COMPONENT_TTL_SECONDS": 60,
            "FINANCE_RUNTIME_TTL_SECONDS": 300,
            "COMMODITY_SYMBOLS": [],
            "SNAPSHOT_STORE": snapshot_store,
            "_bootstrap_cache": {"value": None, "expires_at": 0.0, "refresh_in_progress": False},
            "_bootstrap_cache_lock": threading.Lock(),
            "app": FakeApp(),
            "threading": fake_threading or FakeThreadingModule({"run_target": True}),
            "get_cached_json": lambda namespace, cache_key: redis_cache.get(namespace),
            "set_cached_json": lambda namespace, cache_key, payload, ttl_seconds: redis_cache.__setitem__(namespace, payload),
            "get_bootstrap_component_cached": get_bootstrap_component_cached,
            "get_market_by_id": get_market_by_id,
            "normalize_market": normalize_market,
            "get_trades_by_market_id": lambda market_id, limit=12, offset=0: [{"marketId": market_id, "txHash": "0xtrade"}],
            "get_oracle_events_by_market_id": lambda market_id: [{"marketId": market_id, "eventStatus": "propose"}],
            "get_related_content_by_market_id": lambda market_id, limit=6: {"items": [{"id": 1, "title": "Linked article"}]},
            "get_recent_trades_snapshot": lambda limit=18: [{"marketId": 1, "txHash": "0xglobal"}],
            "get_recent_oracle_snapshot": lambda limit=12: [{"marketId": 1, "eventStatus": "propose"}],
            "get_latest_content_snapshot": lambda limit=8: {"items": [{"id": 7, "title": "Latest article"}]},
            "get_market_group_snapshot": lambda symbols, kind="commodities": {"kind": kind, "items": []},
            "get_gamma_active_market_filter": lambda: {},
            "enrich_market_rows_with_runtime_prices": lambda rows, max_updates=18, force_refresh=False: rows,
            "build_system_health_payload": lambda: {"apiStatus": "ok", "redis": True},
            "table_exists": lambda table_name: latest_content_from_db if table_name in {"content_items", "content_links"} else True,
            "parse_json_list": parse_json_list,
            "query_all": query_all,
            "query_one": query_one,
            "utc_now_iso": lambda: "2026-04-21T00:00:00Z",
            "utc_date_days_ago": lambda days: "2026-04-20",
            "LOB_RUNTIME_MANAGER": FakeLOBManager(),
        }
        return ctx

    def test_build_bootstrap_payload_skips_lob_and_keeps_shape(self):
        candidate_rows = [
            self.make_market_row(11, status="Proposed", yes_token_id="yes-11", no_token_id="no-11", volume_24h="90", trade_count_24h=8),
            self.make_market_row(22, status="Active", yes_token_id="yes-22", no_token_id="no-22", volume_24h="140", trade_count_24h=12, last_trade_at="2026-04-21T00:00:00Z"),
            self.make_market_row(33, status="Active", yes_token_id="", no_token_id="", volume_24h="999", trade_count_24h=50),
        ]
        status_rows = [
            {"market_id": 11, "has_settle": 0, "has_propose": 1},
            {"market_id": 22, "has_settle": 0, "has_propose": 0},
            {"market_id": 33, "has_settle": 0, "has_propose": 0},
        ]
        markets_by_id = {int(row["id"]): dict(row) for row in candidate_rows}
        ctx = self.make_service_context(candidate_rows=candidate_rows, status_rows=status_rows, markets_by_id=markets_by_id)

        payload = bootstrap_service.build_bootstrap_payload(ctx)

        self.assertEqual(payload["featuredMarket"]["id"], 22)
        self.assertIn("activeMarketsPreview", payload)
        self.assertIn("globalTradesPreview", payload)
        self.assertIn("globalOraclePreview", payload)
        self.assertIn("latestContentPreview", payload)
        self.assertIn("systemHealth", payload)
        self.assertEqual(payload["pricePreview"]["marketId"], 22)

    def test_build_bootstrap_payload_falls_back_to_local_db_market(self):
        fallback_market = self.make_market_row(77, status="Active", yes_token_id="yes-77", no_token_id="no-77")
        ctx = self.make_service_context(
            candidate_rows=[],
            status_rows=[],
            markets_by_id={77: fallback_market},
            fallback_featured_id=77,
        )

        payload = bootstrap_service.build_bootstrap_payload(ctx)

        self.assertEqual(payload["featuredMarket"]["id"], 77)
        self.assertEqual(payload["recentTradesPreview"][0]["marketId"], 77)

    def test_get_bootstrap_payload_cached_returns_fresh_cache(self):
        cached_payload = {"generatedAt": "fresh-cache"}
        ctx = self.make_service_context(cached_json=cached_payload)

        payload = bootstrap_service.get_bootstrap_payload_cached(ctx)

        self.assertEqual(payload, cached_payload)
        self.assertEqual(ctx["_bootstrap_cache"]["value"], cached_payload)

    def test_get_bootstrap_payload_cached_returns_stale_and_schedules_one_refresh(self):
        stale_payload = {"generatedAt": "stale-cache"}
        tracker = {"run_target": False}
        ctx = self.make_service_context(
            snapshot_store=FakeSnapshotStore(stale={(bootstrap_service.BOOTSTRAP_SNAPSHOT_NAMESPACE, bootstrap_service.BOOTSTRAP_CACHE_KEY): stale_payload}),
            fake_threading=FakeThreadingModule(tracker),
        )

        with patch.object(bootstrap_service, "build_bootstrap_payload", return_value={"generatedAt": "fresh-cache"}):
            first = bootstrap_service.get_bootstrap_payload_cached(ctx)
            second = bootstrap_service.get_bootstrap_payload_cached(ctx)

        self.assertEqual(first, stale_payload)
        self.assertEqual(second, stale_payload)
        self.assertEqual(tracker.get("starts"), 1)

    def test_get_bootstrap_payload_cached_builds_synchronously_on_cold_miss(self):
        ctx = self.make_service_context()
        cold_payload = {"generatedAt": "cold-build", "defaultWorkspace": {"name": "Hackathon Demo", "panels": []}}

        with patch.object(bootstrap_service, "build_bootstrap_payload", return_value=cold_payload):
            payload = bootstrap_service.get_bootstrap_payload_cached(ctx)

        self.assertEqual(payload, cold_payload)
        self.assertEqual(ctx["_bootstrap_cache"]["value"], cold_payload)
        self.assertEqual(
            ctx["SNAPSHOT_STORE"].fresh[(bootstrap_service.BOOTSTRAP_SNAPSHOT_NAMESPACE, bootstrap_service.BOOTSTRAP_CACHE_KEY)],
            cold_payload,
        )

    def test_prewarm_snapshot_payloads_continues_after_failure(self):
        counters: Dict[str, int] = {}
        ctx = {
            "FINANCE_RUNTIME_TTL_SECONDS": 300,
            "SIGNAL_RUNTIME_TTL_SECONDS": 45,
            "COMMODITY_SYMBOLS": [],
            "get_bootstrap_component_cached": lambda component_key, builder, ttl_seconds=60: builder(),
            "get_market_group_snapshot": lambda symbols, kind="commodities": {"kind": kind, "items": []},
            "get_recent_oracle_snapshot": lambda limit=12: (_ for _ in ()).throw(RuntimeError("boom")) if limit == 12 else counters.__setitem__("oracle16", counters.get("oracle16", 0) + 1),
            "get_recent_trades_snapshot": lambda limit=18: counters.__setitem__(f"trades{limit}", counters.get(f"trades{limit}", 0) + 1),
            "get_active_markets_snapshot": lambda page_size=40: {"items": []},
            "get_bootstrap_payload_cached": lambda: counters.__setitem__("bootstrap", counters.get("bootstrap", 0) + 1),
            "get_whale_trades_snapshot": lambda limit=14: {"items": []},
            "get_suspicious_trades_snapshot": lambda limit=12: {"items": []},
            "get_alpha_signal_snapshot": lambda limit=8: {"items": []},
            "get_jin10_panel_snapshot": lambda limit=12: {"items": []},
            "table_exists": lambda table_name: table_name == "content_items",
            "get_latest_content_snapshot": lambda limit=8: {"items": [{"id": limit}]},
            "query_all": lambda sql, params=(): [],
            "query_one": lambda sql, params=(): {},
            "utc_now_iso": lambda: "2026-04-21T00:00:00Z",
            "utc_date_days_ago": lambda days: "2026-04-20",
            "parse_json_list": lambda raw: [],
            "app": FakeApp(),
        }

        bootstrap_service.prewarm_snapshot_payloads(ctx)

        self.assertEqual(counters.get("bootstrap"), 1)
        self.assertEqual(counters.get("trades18"), 1)
        self.assertEqual(counters.get("trades24"), 1)
        self.assertEqual(counters.get("oracle16"), 1)

    def test_bootstrap_route_returns_payload_without_lob_runtime(self):
        market = self.make_market_row(88, status="Active", yes_token_id="yes-88", no_token_id="no-88", volume_24h="50")
        ctx = self.make_service_context(
            candidate_rows=[market],
            status_rows=[{"market_id": 88, "has_settle": 0, "has_propose": 0}],
            markets_by_id={88: market},
        )
        app = Flask(__name__)
        app.register_blueprint(create_bootstrap_blueprint({"get_bootstrap_payload_cached": lambda: bootstrap_service.get_bootstrap_payload_cached(ctx)}))

        with app.test_client() as client:
            response = client.get("/bootstrap")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("featuredMarket", payload)
        self.assertIn("activeMarketsPreview", payload)
        self.assertIn("globalTradesPreview", payload)
        self.assertIn("globalOraclePreview", payload)
        self.assertIn("latestContentPreview", payload)
        self.assertIn("systemHealth", payload)


if __name__ == "__main__":
    unittest.main()
