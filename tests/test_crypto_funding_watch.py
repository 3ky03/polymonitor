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

from api.routes.runtime_panels import create_runtime_panels_blueprint
from api.services import crypto_funding_service


class FakeLogger:
    def exception(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None


class FakeApp:
    logger = FakeLogger()


class CryptoFundingWatchTestCase(unittest.TestCase):
    def make_context(self, *, bybit_fails: bool = False, binance_url: str = "fixture-binance", bybit_url: str = "fixture-bybit") -> Dict[str, Any]:
        settings = SimpleNamespace(
            crypto_funding_watch_api_url=binance_url,
            crypto_funding_watch_bybit_api_url=bybit_url,
            crypto_funding_watch_api_key="",
            crypto_funding_watch_bybit_api_key="",
            crypto_funding_watch_source_url="fixture-source",
            crypto_funding_watch_ttl_seconds=60,
            crypto_funding_watch_symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        )

        def http_json_get(url, params=None, timeout=12, headers=None):
            if url == "fixture-binance":
                return [
                    {
                        "symbol": "BTCUSDT",
                        "lastFundingRate": "0.00010000",
                        "markPrice": "64000.0",
                        "indexPrice": "63980.0",
                        "nextFundingTime": "1777363200000",
                        "time": "1777334400000",
                    },
                    {
                        "symbol": "SOLUSDT",
                        "lastFundingRate": "0.00070000",
                        "markPrice": "145.5",
                        "indexPrice": "145.0",
                        "nextFundingTime": "1777363200000",
                        "time": "1777334400000",
                    },
                    {"symbol": "IGNOREDUSDT", "lastFundingRate": "0.9"},
                ]
            if url == "fixture-bybit":
                if bybit_fails:
                    raise RuntimeError("temporary upstream failure")
                return {
                    "result": {
                        "list": [
                            {
                                "symbol": "BTCUSDT",
                                "fundingRate": "-0.00012000",
                                "markPrice": "63990.0",
                                "indexPrice": "63970.0",
                                "nextFundingTime": "1777363200000",
                            },
                            {
                                "symbol": "ETHUSDT",
                                "fundingRate": "0.00030000",
                                "markPrice": "3200.0",
                                "indexPrice": "3199.0",
                                "nextFundingTime": "1777363200000",
                            }
                        ]
                    }
                }
            raise RuntimeError(f"unexpected url {url}")

        return {
            "SETTINGS": settings,
            "app": FakeApp(),
            "http_json_get": http_json_get,
            "get_snapshot_payload": lambda namespace, cache_key, builder, ttl_seconds=60: builder(),
            "utc_now_iso": lambda: "2026-04-28T00:00:00Z",
        }

    def test_snapshot_normalizes_and_sorts_by_abnormal_funding(self):
        payload = crypto_funding_service.get_crypto_funding_watch_snapshot(self.make_context(), limit=3)

        self.assertEqual("ok", payload["status"])
        self.assertEqual(["SOL", "ETH", "BTC"], [item["asset"] for item in payload["assets"]])
        self.assertEqual(["Binance", "Bybit"], payload["venues"])
        btc_row = next(item for item in payload["assets"] if item["asset"] == "BTC")
        self.assertEqual("mixed", btc_row["bias"])
        self.assertAlmostEqual(0.022, btc_row["spreadPercent"], places=3)
        self.assertEqual(["Binance", "Bybit"], [quote["exchange"] for quote in btc_row["quotes"]])
        self.assertEqual("critical", payload["assets"][0]["tone"])
        self.assertAlmostEqual(0.07, payload["assets"][0]["maxAbsFundingPercent"])

    def test_snapshot_returns_partial_degraded_payload_when_one_source_fails(self):
        payload = crypto_funding_service.get_crypto_funding_watch_snapshot(self.make_context(bybit_fails=True), limit=5)

        self.assertEqual("degraded", payload["status"])
        self.assertEqual("error", payload["sources"]["bybit"])
        self.assertEqual(["SOL", "BTC"], [item["asset"] for item in payload["assets"]])

    def test_snapshot_can_return_stale_payload_when_builder_is_empty(self):
        stale = {
            "generatedAt": "2026-04-27T00:00:00Z",
            "source": "binance/bybit-funding",
            "status": "ok",
            "venues": ["Binance"],
            "assets": [{"id": "stale:BTC", "asset": "BTC", "quotes": []}],
            "items": [{"id": "stale:BTCUSDT", "asset": "BTC"}],
        }

        def stale_first(namespace, cache_key, builder, ttl_seconds=60):
            fresh = builder()
            return stale if not fresh.get("items") else fresh

        ctx = self.make_context(binance_url="", bybit_url="")
        ctx["get_snapshot_payload"] = stale_first

        payload = crypto_funding_service.get_crypto_funding_watch_snapshot(ctx, limit=5)

        self.assertEqual(stale, payload)

    def test_runtime_panel_route_clamps_invalid_and_large_limits(self):
        seen_limits = []
        app = Flask(__name__)
        helpers = {
            "COMMODITY_SYMBOLS": [],
            "CRYPTO_SYMBOLS": [],
            "get_market_group_snapshot": lambda symbols, kind: {"kind": kind, "items": symbols},
            "get_f1_panel_snapshot": lambda limit=10: {"limit": limit},
            "get_geo_sanctions_shock_snapshot": lambda limit=6: {"limit": limit},
            "get_jin10_panel_snapshot": lambda limit=24: {"limit": limit},
            "get_nba_scoreboard_snapshot": lambda limit=10: {"limit": limit},
            "get_nba_intel_snapshot": lambda limit=12: {"limit": limit},
            "get_nba_matchup_predictor_snapshot": lambda limit=8: {"limit": limit},
            "get_inflation_nowcast_snapshot": lambda: {"items": []},
            "get_alpha_signal_snapshot": lambda limit=8: {"limit": limit},
            "get_crypto_funding_watch_snapshot": lambda limit=18: seen_limits.append(limit) or {"limit": limit},
            "get_cpi_release_calendar_snapshot": lambda limit=8: {"limit": limit},
            "get_energy_gasoline_shock_snapshot": lambda limit=6: {"limit": limit},
            "get_food_retail_basket_snapshot": lambda limit=8: {"limit": limit},
            "get_polymarket_macro_map_snapshot": lambda limit=12: {"limit": limit},
            "get_whale_trades_snapshot": lambda limit=14: {"limit": limit},
            "get_suspicious_trades_snapshot": lambda limit=12: {"limit": limit},
            "get_new_market_signals_snapshot": lambda limit=12: {"limit": limit},
        }
        app.register_blueprint(create_runtime_panels_blueprint(helpers))

        with app.test_client() as client:
            invalid = client.get("/runtime/crypto/funding-watch?limit=nope")
            large = client.get("/runtime/crypto/funding-watch?limit=999")

        self.assertEqual(200, invalid.status_code)
        self.assertEqual(200, large.status_code)
        self.assertEqual([18, 40], seen_limits)


if __name__ == "__main__":
    unittest.main()
