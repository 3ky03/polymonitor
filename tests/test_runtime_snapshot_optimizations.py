from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import bootstrap_service, market_service, signal_service


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

    def get(self, namespace: str, cache_key: str):
        return self.fresh.get((namespace, cache_key))

    def get_stale(self, namespace: str, cache_key: str):
        return self.stale.get((namespace, cache_key))

    def set(self, namespace: str, cache_key: str, payload: Any, ttl_seconds: int) -> None:
        self.fresh[(namespace, cache_key)] = payload
        self.stale[(namespace, cache_key)] = payload


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


class MarketFastPathTestCase(unittest.TestCase):
    def test_get_markets_payload_uses_active_snapshot_for_first_active_page(self):
        ctx = {
            "utc_now_iso": lambda: "2026-04-21T00:00:00Z",
            "utc_date_days_ago": lambda days: "2026-04-20",
        }
        expected = {"items": [{"id": 1}], "pagination": {"page": 1, "pageSize": 160, "total": 1, "totalPages": 1, "hasMore": False}}

        with patch.object(market_service, "get_active_markets_snapshot", return_value=expected) as snapshot_mock:
            payload = market_service.get_markets_payload(ctx, status="active", query="", page=1, page_size=160)

        self.assertEqual(payload, expected)
        snapshot_mock.assert_called_once_with(ctx, page_size=160, include_runtime_prices=False)

    def test_build_active_markets_payload_skips_runtime_enrichment_in_fast_mode(self):
        candidate_rows = [
            {
                "id": 11,
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-20T00:00:00Z",
                "has_settle": 0,
                "has_propose": 0,
                "trade_count_24h": 12,
                "volume_24h": "4400",
                "last_trade_at": "2026-04-21T00:00:00Z",
                "latest_trade_at": "2026-04-21T00:00:00Z",
                "price_24h_ago": "0.49",
            },
        ]
        detail_rows = [
            {
                "id": 11,
                "slug": "market-11",
                "title": "Market 11",
                "condition_id": "condition-11",
                "question_id": "question-11",
                "yes_token_id": "yes-11",
                "no_token_id": "no-11",
                "category": "Politics",
                "tags": json.dumps(["macro"]),
                "clob_token_ids": None,
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-20T00:00:00Z",
                "latest_price": "0.51",
                "latest_trade_at": "2026-04-21T00:00:00Z",
            },
        ]
        sql_calls: List[str] = []

        def fake_query_all(sql, params=()):
            sql_calls.append(" ".join(sql.split()))
            if "MAX(CASE WHEN event_status = 'settle'" in sql:
                return [{"market_id": 11, "has_settle": 0, "has_propose": 0}]
            if "WHERE m.id IN" in sql:
                return detail_rows
            return candidate_rows

        ctx = {
            "utc_now_iso": lambda: "2026-04-21T00:00:00Z",
            "utc_date_days_ago": lambda days: "2026-04-20",
            "query_all": fake_query_all,
            "parse_json_list": lambda raw: json.loads(raw) if isinstance(raw, str) and raw else [],
            "format_trade_decimal": lambda value: value,
        }

        with patch.object(market_service, "enrich_market_rows_with_runtime_prices", side_effect=AssertionError("runtime enrichment should be skipped")), \
             patch.object(market_service, "enrich_market_rows_with_24h_change", side_effect=lambda inner_ctx, rows: rows):
            payload = market_service.build_active_markets_payload(ctx, page_size=1, include_runtime_prices=False)

        self.assertEqual(payload["items"][0]["id"], 11)
        self.assertEqual(payload["items"][0]["latestPrice"], "0.51")
        self.assertEqual(str(payload["items"][0]["change24h"]), "0.02")
        self.assertEqual(len(sql_calls), 3)
        self.assertIn("hide-from-new", sql_calls[0])
        self.assertIn("updown-5m", sql_calls[0])
        self.assertIn("market_status_snapshot", sql_calls[0])
        self.assertIn("market_list_serving", sql_calls[0])
        self.assertNotIn("oracle_events", sql_calls[0])
        self.assertIn("hide-from-new", sql_calls[1])
        self.assertIn("market_status_snapshot", sql_calls[1])
        self.assertIn("market_list_serving", sql_calls[1])
        self.assertIn("m.title", sql_calls[2])

    def test_build_active_markets_payload_fills_from_db_active_when_strict_filters_are_sparse(self):
        candidate_rows = [
            {
                "id": 11,
                "slug": "market-11",
                "condition_id": "condition-11",
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-20T00:00:00Z",
                "has_settle": 0,
                "has_propose": 0,
                "trade_count_24h": 12,
                "volume_24h": "4400",
                "last_trade_at": "2026-04-21T00:00:00Z",
                "latest_trade_at": "2026-04-21T00:00:00Z",
                "price_24h_ago": "0.49",
            },
            {
                "id": 12,
                "slug": "market-12",
                "condition_id": "condition-12",
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-19T00:00:00Z",
                "has_settle": 0,
                "has_propose": 0,
                "trade_count_24h": 0,
                "volume_24h": "0",
                "last_trade_at": None,
                "latest_trade_at": None,
                "price_24h_ago": None,
            },
        ]
        detail_rows = [
            {
                "id": 11,
                "slug": "market-11",
                "title": "Market 11",
                "condition_id": "condition-11",
                "question_id": "question-11",
                "yes_token_id": "yes-11",
                "no_token_id": "no-11",
                "category": "Politics",
                "tags": json.dumps(["macro"]),
                "clob_token_ids": None,
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-20T00:00:00Z",
                "latest_price": "0.51",
                "latest_trade_at": "2026-04-21T00:00:00Z",
            },
            {
                "id": 12,
                "slug": "market-12",
                "title": "Market 12",
                "condition_id": "condition-12",
                "question_id": "question-12",
                "yes_token_id": "yes-12",
                "no_token_id": "no-12",
                "category": "Politics",
                "tags": json.dumps(["macro"]),
                "clob_token_ids": None,
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-19T00:00:00Z",
                "latest_price": "0.44",
                "latest_trade_at": None,
            },
        ]

        def fake_query_all(sql, params=()):
            if "WHERE m.id IN" in sql:
                return detail_rows
            return candidate_rows

        ctx = {
            "utc_now_iso": lambda: "2026-04-21T00:00:00Z",
            "utc_date_days_ago": lambda days: "2026-04-20",
            "query_all": fake_query_all,
            "parse_json_list": lambda raw: json.loads(raw) if isinstance(raw, str) and raw else [],
            "format_trade_decimal": lambda value: value,
            "get_gamma_active_market_filter": lambda: {"conditionIds": ["condition-11"], "slugs": []},
        }

        with patch.object(market_service, "enrich_market_rows_with_runtime_prices", side_effect=AssertionError("runtime enrichment should be skipped")), \
             patch.object(market_service, "enrich_market_rows_with_24h_change", side_effect=lambda inner_ctx, rows: rows):
            payload = market_service.build_active_markets_payload(ctx, page_size=2, include_runtime_prices=False)

        self.assertEqual([item["id"] for item in payload["items"]], [11, 12])

    def test_build_active_markets_payload_prioritizes_recent_candidates_in_snapshot_pool(self):
        volume_rows = [
            {
                "id": 11,
                "slug": "market-11",
                "condition_id": "condition-11",
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-20T00:00:00Z",
                "has_settle": 0,
                "has_propose": 0,
                "trade_count_24h": 12,
                "volume_24h": "4400",
                "last_trade_at": "2026-04-21T00:00:00Z",
                "latest_trade_at": "2026-04-21T00:00:00Z",
                "price_24h_ago": "0.49",
            },
        ]
        recent_rows = [
            {
                "id": 12,
                "slug": "market-12",
                "condition_id": "condition-12",
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-21T23:00:00Z",
                "has_settle": 0,
                "has_propose": 0,
                "trade_count_24h": 0,
                "volume_24h": "0",
                "last_trade_at": None,
                "latest_trade_at": None,
                "price_24h_ago": None,
            },
        ]
        detail_map = {
            11: {
                "id": 11,
                "slug": "market-11",
                "title": "Volume market",
                "condition_id": "condition-11",
                "question_id": "question-11",
                "yes_token_id": "yes-11",
                "no_token_id": "no-11",
                "category": "Politics",
                "tags": json.dumps(["macro"]),
                "clob_token_ids": None,
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-20T00:00:00Z",
                "latest_price": "0.51",
                "latest_trade_at": "2026-04-21T00:00:00Z",
            },
            12: {
                "id": 12,
                "slug": "market-12",
                "title": "Newest market",
                "condition_id": "condition-12",
                "question_id": "question-12",
                "yes_token_id": "yes-12",
                "no_token_id": "no-12",
                "category": "Politics",
                "tags": json.dumps(["macro"]),
                "clob_token_ids": None,
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-21T23:00:00Z",
                "latest_price": "0.44",
                "latest_trade_at": None,
            },
        }

        def fake_query_all(sql, params=()):
            normalized_sql = " ".join(sql.split())
            if "ORDER BY m.created_at DESC" in normalized_sql:
                return recent_rows
            if "WHERE m.id IN" in normalized_sql:
                return [detail_map[11], detail_map[12]]
            return volume_rows

        ctx = {
            "utc_now_iso": lambda: "2026-04-21T00:00:00Z",
            "utc_date_days_ago": lambda days: "2026-04-20",
            "query_all": fake_query_all,
            "parse_json_list": lambda raw: json.loads(raw) if isinstance(raw, str) and raw else [],
            "format_trade_decimal": lambda value: value,
            "get_gamma_active_market_filter": lambda: {"conditionIds": [], "slugs": []},
        }

        with patch.object(market_service, "enrich_market_rows_with_runtime_prices", side_effect=AssertionError("runtime enrichment should be skipped")), \
             patch.object(market_service, "enrich_market_rows_with_24h_change", side_effect=lambda inner_ctx, rows: rows):
            payload = market_service.build_active_markets_payload(ctx, page_size=2, include_runtime_prices=False)

        self.assertEqual([item["id"] for item in payload["items"]], [12, 11])

    def test_get_markets_payload_generic_uses_serving_tables(self):
        sql_calls: List[str] = []

        def fake_query_all(sql, params=()):
            sql_calls.append(" ".join(sql.split()))
            return [
                {
                    "id": 21,
                    "end_date": "2026-12-31T00:00:00Z",
                    "created_at": "2026-04-20T00:00:00Z",
                    "has_settle": 0,
                    "has_propose": 1,
                    "trade_count_24h": 14,
                    "volume_24h": "5200",
                    "last_trade_at": "2026-04-21T00:00:00Z",
                    "latest_trade_at": "2026-04-21T00:00:00Z",
                    "price_24h_ago": "0.57",
                }
            ]

        ctx = {
            "utc_now_iso": lambda: "2026-04-21T00:00:00Z",
            "utc_date_days_ago": lambda days: "2026-04-20",
            "query_all": fake_query_all,
            "get_markets_payload_cached": lambda cache_key, builder: builder(),
            "parse_json_list": lambda raw: json.loads(raw) if isinstance(raw, str) and raw else [],
            "format_trade_decimal": lambda value: value,
        }

        detail_map = {
            21: {
                "id": 21,
                "slug": "market-21",
                "title": "Market 21",
                "condition_id": "condition-21",
                "question_id": "question-21",
                "yes_token_id": "yes-21",
                "no_token_id": "no-21",
                "category": "Politics",
                "tags": json.dumps(["macro"]),
                "clob_token_ids": None,
                "end_date": "2026-12-31T00:00:00Z",
                "created_at": "2026-04-20T00:00:00Z",
                "latest_price": "0.59",
                "latest_trade_at": "2026-04-21T00:00:00Z",
            }
        }

        with patch.object(market_service, "_get_market_detail_rows_by_ids", return_value=detail_map), \
             patch.object(market_service, "enrich_market_rows_with_runtime_prices", side_effect=lambda inner_ctx, rows, max_updates=18: rows), \
             patch.object(market_service, "enrich_market_rows_with_24h_change", side_effect=lambda inner_ctx, rows: rows):
            payload = market_service.get_markets_payload(ctx, status="active", query="macro", page=2, page_size=1)

        self.assertEqual(payload["items"][0]["id"], 21)
        self.assertEqual(str(payload["items"][0]["change24h"]), "0.02")
        self.assertIn("market_status_snapshot", sql_calls[0])
        self.assertIn("market_list_serving", sql_calls[0])
        self.assertNotIn("oracle_events", sql_calls[0])


class SignalSnapshotOptimizationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        signal_service._SIGNAL_REFRESH_STATE.clear()
        bootstrap_service._PREWARM_LAST_RUN.clear()

    def make_signal_context(
        self,
        *,
        snapshot_store: Optional[FakeSnapshotStore] = None,
        tracker: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        runtime_cache: Dict[tuple[str, str], Any] = {}
        return {
            "SIGNAL_RUNTIME_TTL_SECONDS": 45,
            "SNAPSHOT_STORE": snapshot_store or FakeSnapshotStore(),
            "get_cached_runtime_payload": lambda namespace, cache_key: runtime_cache.get((namespace, cache_key)),
            "set_cached_runtime_payload": lambda namespace, cache_key, payload, ttl_seconds: runtime_cache.setdefault((namespace, cache_key), payload),
            "threading": FakeThreadingModule(tracker),
            "app": FakeApp(),
            "utc_now_iso": lambda: "2026-04-21T00:00:00Z",
        }

    def test_alpha_snapshot_returns_stale_and_schedules_one_refresh(self):
        cache_key = json.dumps({"limit": 8}, sort_keys=True, ensure_ascii=True)
        stale_payload = {"items": [{"title": "stale alpha"}], "generatedAt": "stale"}
        tracker = {"run_target": False}
        ctx = self.make_signal_context(
            snapshot_store=FakeSnapshotStore(stale={(signal_service.SIGNAL_SNAPSHOT_NAMESPACE_ALPHA, cache_key): stale_payload}),
            tracker=tracker,
        )

        with patch.object(signal_service, "_build_alpha_signal_payload", return_value={"items": [{"title": "fresh"}], "generatedAt": "fresh"}):
            first = signal_service.get_alpha_signal_snapshot(ctx, limit=8)
            second = signal_service.get_alpha_signal_snapshot(ctx, limit=8)

        self.assertEqual(first, stale_payload)
        self.assertEqual(second, stale_payload)
        self.assertEqual(tracker.get("starts"), 1)

    def test_alpha_snapshot_cold_miss_builds_and_stores_payload(self):
        cache_key = json.dumps({"limit": 8}, sort_keys=True, ensure_ascii=True)
        snapshot_store = FakeSnapshotStore()
        ctx = self.make_signal_context(snapshot_store=snapshot_store, tracker={"run_target": True})
        payload = {"items": [{"title": "fresh alpha"}], "generatedAt": "fresh"}

        with patch.object(signal_service, "_build_alpha_signal_payload", return_value=payload):
            result = signal_service.get_alpha_signal_snapshot(ctx, limit=8)

        self.assertEqual(result["items"], payload["items"])
        self.assertEqual("ok", result["status"])
        self.assertEqual("live-build", result["cacheMode"])
        self.assertEqual(snapshot_store.get(signal_service.SIGNAL_SNAPSHOT_NAMESPACE_ALPHA, cache_key)["items"], payload["items"])


if __name__ == "__main__":
    unittest.main()
