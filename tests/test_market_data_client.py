from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.clients import market_data_client


class FakeResponse:
    content = b"{}"

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeRequests:
    def __init__(self, payload):
        self.payload = payload

    def get(self, *args, **kwargs):
        return FakeResponse(self.payload)


def make_chart_payload(*, symbol: str, current: float, previous: float, closes: list[float]):
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "regularMarketPrice": current,
                        "chartPreviousClose": previous,
                        "currency": "USD",
                        "regularMarketVolume": 1000,
                    },
                    "timestamp": [1714700000 + index * 1800 for index in range(len(closes))],
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


def make_context(payload):
    return {
        "SETTINGS": SimpleNamespace(yahoo_chart_base_url="https://example.test/chart"),
        "FINANCE_RUNTIME_TTL_SECONDS": 300,
        "requests": FakeRequests(payload),
        "_safe_float": lambda value: None if value in (None, "") else float(value),
        "get_cached_runtime_payload": lambda namespace, cache_key: None,
        "set_cached_runtime_payload": lambda namespace, cache_key, payload, ttl_seconds: payload,
    }


class MarketDataClientTestCase(unittest.TestCase):
    def test_yahoo_snapshot_falls_back_when_previous_close_scale_is_incompatible(self):
        payload = make_chart_payload(symbol="ZR=F", current=12.455, previous=1250.0, closes=[12.5, 12.455])

        snapshot = market_data_client.get_yahoo_market_snapshot(make_context(payload), "ZR=F")

        self.assertEqual(12.455, snapshot["price"])
        self.assertEqual(-0.36, snapshot["changePercent"])

    def test_yahoo_snapshot_uses_previous_close_when_scale_is_compatible(self):
        payload = make_chart_payload(symbol="GC=F", current=4353.9, previous=4475.2, closes=[4546.1, 4353.9])

        snapshot = market_data_client.get_yahoo_market_snapshot(make_context(payload), "GC=F")

        self.assertEqual(4353.9, snapshot["price"])
        self.assertEqual(-2.71, snapshot["changePercent"])


if __name__ == "__main__":
    unittest.main()
