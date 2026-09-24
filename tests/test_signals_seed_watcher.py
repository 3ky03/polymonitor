from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import signal_service
from runtime import signals_watcher
from runtime.snapshot_store import SnapshotStore


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.set(key, value, ex=ttl)


class SignalsSeedWatcherTestCase(unittest.TestCase):
    def make_watcher(self, component: str = "whales", limit: int = 14):
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        fake_redis = FakeRedis()
        redis_module = SimpleNamespace(from_url=lambda *args, **kwargs: fake_redis)
        with patch.object(signals_watcher, "redis", redis_module):
            watcher = signals_watcher.SignalsWatcher(
                redis_url="redis://test/0",
                redis_prefix="polydata:",
                snapshot_sqlite_path=str(Path(snapshot_dir.name) / "snapshots.sqlite3"),
                component=component,
                limit=limit,
                interval_seconds=45,
            )
        return watcher, fake_redis

    def test_watcher_stores_whale_payload_and_seed_meta(self):
        watcher, fake_redis = self.make_watcher(component="whales", limit=14)
        payload = {"items": [{"title": "Whale flow"}], "generatedAt": "2026-05-03T08:00:00Z", "status": "ok"}
        with patch.object(watcher, "fetch_payload", return_value=payload):
            result = watcher.run_once()

        self.assertEqual("ok", result["status"])
        cache_key = signal_service.build_whale_trades_cache_key(limit=14)
        stored = json.loads(fake_redis.get(f"polydata:snapshot:signals:whales:{cache_key}") or "{}")
        self.assertEqual("seeded", stored["cacheMode"])
        self.assertEqual("ok", stored["status"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:signals:whale-trades") or "{}")
        self.assertEqual("ok", meta["status"])
        self.assertEqual(1, meta["recordCount"])

    def test_watcher_preserves_previous_payload_when_new_payload_is_empty(self):
        watcher, fake_redis = self.make_watcher(component="alpha", limit=8)
        previous = {"items": [{"title": "Old alpha"}], "generatedAt": "old", "status": "ok", "cacheMode": "seeded"}
        watcher.store_payload(previous)
        with patch.object(watcher, "fetch_payload", return_value={"items": [], "generatedAt": "new", "status": "empty"}):
            result = watcher.run_once()

        self.assertEqual("preserved", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
        self.assertEqual("Old alpha", stored["items"][0]["title"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:signals:alpha-signal") or "{}")
        self.assertEqual("preserved", meta["status"])

    def test_api_reads_seeded_signal_redis_without_live_build(self):
        with tempfile.TemporaryDirectory() as snapshot_dir:
            store = SnapshotStore(str(Path(snapshot_dir) / "snapshots.sqlite3"))
            cache_key = signal_service.build_alpha_signal_cache_key(limit=8)
            seeded = {"items": [{"title": "Seeded alpha"}], "generatedAt": "seed", "cacheMode": "seeded"}
            ctx = {
                "SIGNAL_RUNTIME_TTL_SECONDS": 45,
                "SNAPSHOT_STORE": store,
                "get_cached_runtime_payload": lambda namespace, key: None,
                "set_cached_runtime_payload": lambda namespace, key, payload, ttl: payload,
                "get_cached_json": lambda namespace, key: seeded if (namespace, key) == (signal_service.SIGNAL_SNAPSHOT_NAMESPACE_ALPHA, cache_key) else None,
                "threading": SimpleNamespace(Thread=object),
                "app": SimpleNamespace(logger=SimpleNamespace(info=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None)),
                "utc_now_iso": lambda: "2026-05-03T08:00:00Z",
            }
            with patch.object(signal_service, "fetch_live_alpha_signal_payload", side_effect=AssertionError("live build should not run")):
                payload = signal_service.get_alpha_signal_snapshot(ctx, limit=8)

        self.assertEqual("seeded", payload["cacheMode"])
        self.assertEqual("Seeded alpha", payload["items"][0]["title"])

    def test_api_trims_default_signal_seeds_for_smaller_limits_without_live_build(self):
        seeded_payloads = {
            (signal_service.SIGNAL_SNAPSHOT_NAMESPACE_ALPHA, signal_service.build_alpha_signal_cache_key(limit=8)): {
                "items": [{"title": "Alpha 1"}, {"title": "Alpha 2"}],
                "generatedAt": "seed",
                "cacheMode": "seeded",
            },
            (signal_service.SIGNAL_SNAPSHOT_NAMESPACE_WHALES, signal_service.build_whale_trades_cache_key(limit=14)): {
                "items": [{"title": "Whale 1"}, {"title": "Whale 2"}],
                "generatedAt": "seed",
                "cacheMode": "seeded",
            },
            (signal_service.SIGNAL_SNAPSHOT_NAMESPACE_SUSPICIOUS, signal_service.build_suspicious_trades_cache_key(limit=12)): {
                "items": [{"title": "Suspicious 1"}, {"title": "Suspicious 2"}],
                "generatedAt": "seed",
                "cacheMode": "seeded",
            },
        }
        ctx = {
            "SIGNAL_RUNTIME_TTL_SECONDS": 45,
            "SNAPSHOT_STORE": None,
            "get_cached_runtime_payload": lambda namespace, key: None,
            "set_cached_runtime_payload": lambda namespace, key, payload, ttl: payload,
            "get_cached_json": lambda namespace, key: seeded_payloads.get((namespace, key)),
            "threading": SimpleNamespace(Thread=object),
            "app": SimpleNamespace(logger=SimpleNamespace(info=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None)),
            "utc_now_iso": lambda: "2026-05-03T08:00:00Z",
        }
        with patch.object(signal_service, "fetch_live_alpha_signal_payload", side_effect=AssertionError("live alpha should not run")), patch.object(
            signal_service,
            "fetch_live_whale_trades_payload",
            side_effect=AssertionError("live whales should not run"),
        ), patch.object(signal_service, "fetch_live_suspicious_trades_payload", side_effect=AssertionError("live suspicious should not run")):
            alpha = signal_service.get_alpha_signal_snapshot(ctx, limit=1)
            whales = signal_service.get_whale_trades_snapshot(ctx, limit=1)
            suspicious = signal_service.get_suspicious_trades_snapshot(ctx, limit=1)

        self.assertEqual(["Alpha 1"], [item["title"] for item in alpha["items"]])
        self.assertEqual(["Whale 1"], [item["title"] for item in whales["items"]])
        self.assertEqual(["Suspicious 1"], [item["title"] for item in suspicious["items"]])
        self.assertEqual("seeded", alpha["cacheMode"])
        self.assertEqual("seeded", whales["cacheMode"])
        self.assertEqual("seeded", suspicious["cacheMode"])

    def test_alpha_live_payload_degrades_when_database_sources_fail(self):
        ctx = {
            "app": SimpleNamespace(logger=SimpleNamespace(exception=lambda *args, **kwargs: None)),
            "utc_now_iso": lambda: "2026-05-03T08:00:00Z",
            "get_recent_trades": lambda limit=24: (_ for _ in ()).throw(RuntimeError("db down")),
            "get_recent_oracle_events": lambda limit=24: (_ for _ in ()).throw(RuntimeError("db down")),
            "get_active_markets_snapshot": lambda page_size=8: (_ for _ in ()).throw(RuntimeError("db down")),
            "get_market_group_snapshot": lambda items, kind: {"items": [{"label": "BTC", "changePercent": 3.2, "price": 68000}]},
            "get_inflation_nowcast_snapshot": lambda: {"monthOverMonth": {"CPI": "0.41", "Core CPI": "0.21"}},
            "CRYPTO_SYMBOLS": [("btc", "BTC", "BTC-USD")],
            "_safe_decimal": lambda value: None,
            "_safe_float": lambda value: float(value) if value is not None else None,
            "format_trade_decimal": lambda value: value,
            "format_trade_address": lambda value: value,
            "parse_iso_datetime": lambda value: None,
            "utc_date_days_ago": lambda days: "2026-05-01",
        }

        payload = signal_service.fetch_live_alpha_signal_payload(ctx, limit=3)

        self.assertEqual("degraded", payload["status"])
        self.assertEqual([], payload["items"])

    def test_alpha_live_payload_uses_clickhouse_volume_rows_without_address_profiles(self):
        ctx = {
            "app": SimpleNamespace(logger=SimpleNamespace(exception=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)),
            "utc_now_iso": lambda: "2026-06-06T02:00:00Z",
            "query_all": lambda sql, params=(): [],
            "get_recent_trades": lambda limit=24: [],
            "_safe_decimal": lambda value: signal_service.Decimal(str(value)) if value is not None else None,
            "format_trade_decimal": lambda value: str(value) if value is not None else None,
            "format_trade_address": lambda value: value,
            "parse_iso_datetime": lambda value: None,
            "utc_date_days_ago": lambda days: "2026-05-30T02:00:00Z",
        }
        volume_rows = [
            {
                "market_id": 10,
                "market_title": "High flow market",
                "timestamp": "2026-06-06T01:59:58Z",
                "outcome": "YES",
                "side": "BUY",
                "flow_notional": "15000",
                "net_flow_notional": "14000",
                "bullish_notional": "15000",
                "bearish_notional": "1000",
                "opposite_flow_notional": "1000",
                "net_direction_strength": "0.875",
                "churn_ratio": "0.125",
                "max_trade_notional": "8000",
                "market_baseline_notional": "50000",
                "market_share": "0.30",
                "unique_trader_count": 6,
                "price_health": "0.75",
                "entry_yes_price": "0.62",
                "price_after_1m": "0.64",
                "price_after_5m": "0.67",
                "price_after_15m": "0.66",
                "edge_after_fees": "0.04",
                "edge_fee_probability": "0.01",
                "trade_count": 12,
                "avg_price": "0.62",
                "score": "88",
                "threshold_flow_notional": "1000",
                "severity": "critical",
                "source_mode": "clickhouse-volume-alpha",
                "window_minutes": 15,
            }
        ]
        with patch.object(signal_service.clickhouse_orderfilled_service, "get_alpha_volume_signal_rows", return_value=volume_rows), patch.object(
            signal_service.clickhouse_orderfilled_service,
            "get_volume_whale_rows",
            return_value=[],
        ):
            payload = signal_service.fetch_live_alpha_signal_payload(ctx, limit=3)

        self.assertEqual("ok", payload["status"])
        self.assertEqual("clickhouse-volume-alpha", payload["items"][0]["sourceMode"])
        self.assertEqual("volume-flow", payload["items"][0]["kind"])
        self.assertIn("High flow market", payload["items"][0]["title"])
        self.assertEqual("0.875", payload["items"][0]["metrics"]["netDirectionStrength"])
        self.assertEqual("0.67", payload["items"][0]["metrics"]["priceAfter5m"])
        self.assertEqual("0.04", payload["items"][0]["metrics"]["edgeAfterFees"])

    def test_whale_live_payload_uses_clickhouse_volume_rows(self):
        ctx = {
            "app": SimpleNamespace(logger=SimpleNamespace(exception=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)),
            "utc_now_iso": lambda: "2026-06-06T02:00:00Z",
            "query_all": lambda sql, params=(): [],
            "_safe_decimal": lambda value: signal_service.Decimal(str(value)) if value is not None else None,
            "format_trade_decimal": lambda value: str(value) if value is not None else None,
            "format_trade_address": lambda value: value,
            "parse_iso_datetime": lambda value: None,
            "utc_date_days_ago": lambda days: "2026-05-30T02:00:00Z",
        }
        whale_rows = [
            {
                "market_id": 20,
                "market_title": "Whale market",
                "timestamp": "2026-06-06T01:59:59Z",
                "tx_hash": "0xabc",
                "outcome": "NO",
                "side": "BUY",
                "price": "0.40",
                "size": "25000",
                "notional": "10000",
                "severity": "critical",
                "source_mode": "clickhouse-volume-whales",
                "signal_type": "single-trade",
                "market_share": "0.22",
            }
        ]
        with patch.object(signal_service.clickhouse_orderfilled_service, "get_volume_whale_rows", return_value=whale_rows):
            payload = signal_service.fetch_live_whale_trades_payload(ctx, limit=3)

        self.assertEqual("ok", payload["status"])
        self.assertEqual("clickhouse-volume-whales", payload["sourceMode"])
        self.assertEqual("10000", payload["items"][0]["notional"])
        self.assertEqual("single-trade", payload["items"][0]["signalType"])

    def test_whale_live_payload_filters_near_resolved_and_dedupes_router_splits(self):
        ctx = {
            "app": SimpleNamespace(logger=SimpleNamespace(exception=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)),
            "utc_now_iso": lambda: "2026-06-06T02:00:00Z",
            "query_all": lambda sql, params=(): [],
            "_safe_decimal": lambda value: signal_service.Decimal(str(value)) if value is not None else None,
            "format_trade_decimal": lambda value: str(value) if value is not None else None,
            "format_trade_address": lambda value: value,
            "parse_iso_datetime": lambda value: None,
            "utc_date_days_ago": lambda days: "2026-05-30T02:00:00Z",
        }
        whale_rows = [
            {
                "market_id": 20,
                "market_title": "Near resolved",
                "timestamp": "2026-06-06T01:59:59Z",
                "tx_hash": "0xnear",
                "outcome": "YES",
                "side": "BUY",
                "price": "0.991",
                "size": "25000",
                "notional": "24775",
                "taker": "0xrouter",
                "severity": "critical",
                "source_mode": "clickhouse-volume-whales",
            },
            {
                "market_id": 21,
                "market_title": "Split market",
                "timestamp": "2026-06-06T01:59:58Z",
                "tx_hash": "0xkeep",
                "outcome": "YES",
                "side": "BUY",
                "price": "0.50",
                "size": "20000",
                "notional": "10000",
                "taker": "0xrouter",
                "severity": "critical",
                "source_mode": "clickhouse-volume-whales",
            },
            {
                "market_id": 21,
                "market_title": "Split market",
                "timestamp": "2026-06-06T01:59:57Z",
                "tx_hash": "0xsplit",
                "outcome": "YES",
                "side": "BUY",
                "price": "0.51",
                "size": "12000",
                "notional": "6120",
                "taker": "0xrouter",
                "severity": "critical",
                "source_mode": "clickhouse-volume-whales",
            },
        ]
        with patch.object(signal_service.clickhouse_orderfilled_service, "get_volume_whale_rows", return_value=whale_rows):
            payload = signal_service.fetch_live_whale_trades_payload(ctx, limit=3)

        self.assertEqual("ok", payload["status"])
        self.assertEqual(["0xkeep"], [item["txHash"] for item in payload["items"]])

    def test_whale_bootstrap_fallback_filters_stale_trades(self):
        stale_bootstrap = {
            "globalTradesPreview": [
                {
                    "marketId": 1,
                    "marketTitle": "Old market",
                    "timestamp": "2026-04-28T11:00:40Z",
                    "price": "0.97",
                    "size": "60",
                    "notional": "58.2",
                    "side": "BUY",
                    "outcome": "YES",
                    "txHash": "old",
                }
            ]
        }
        ctx = {
            "app": SimpleNamespace(logger=SimpleNamespace(exception=lambda *args, **kwargs: None)),
            "utc_now_iso": lambda: "2026-06-06T01:00:00Z",
            "utc_date_days_ago": lambda days: "2026-05-30T01:00:00Z",
            "parse_iso_datetime": lambda value: datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None,
            "get_recent_trades": lambda limit=24: [],
            "get_active_markets_snapshot": lambda page_size=8: {"items": []},
            "get_cached_json": lambda namespace, key: stale_bootstrap if (namespace, key) == ("bootstrap", "workspace-default-v9") else None,
            "SNAPSHOT_STORE": None,
            "_safe_decimal": signal_service.Decimal,
            "format_trade_decimal": lambda value: str(value) if value is not None else None,
            "format_trade_address": lambda value: value,
        }

        payload = signal_service.fetch_live_whale_trades_payload(ctx, limit=3)

        self.assertEqual("empty", payload["status"])
        self.assertEqual([], payload["items"])

    def test_watcher_marks_old_payload_stale_even_with_records(self):
        watcher, fake_redis = self.make_watcher(component="whales", limit=14)
        payload = {
            "items": [{"title": "Old whale", "timestamp": "2026-04-28T11:00:40Z"}],
            "generatedAt": "2026-06-06T01:00:00Z",
            "status": "ok",
        }
        with patch.object(watcher, "fetch_payload", return_value=payload), patch.object(
            signals_watcher,
            "datetime",
            SimpleNamespace(
                now=lambda tz=None: datetime(2026, 6, 6, 1, 0, 0, tzinfo=timezone.utc),
                fromisoformat=datetime.fromisoformat,
            ),
        ):
            result = watcher.run_once()

        self.assertEqual("stale", result["status"])
        stored = json.loads(fake_redis.get(watcher.redis_key()) or "{}")
        self.assertEqual("stale", stored["status"])
        meta = json.loads(fake_redis.get("polydata:seed-meta:signals:whale-trades") or "{}")
        self.assertEqual("stale", meta["status"])
        self.assertEqual("2026-04-28T11:00:40Z", meta["metadata"]["maxItemTimestamp"])
        self.assertGreater(meta["metadata"]["dataAgeSeconds"], 7 * 24 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
