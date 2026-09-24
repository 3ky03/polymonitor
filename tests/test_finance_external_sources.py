from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import finance_external_sources_service, finance_panels_service


def _ctx():
    def http_json_post(url, json_payload=None, timeout=12, headers=None):
        assert "hyperliquid" in url
        return [
            {"universe": [{"name": "BTC"}, {"name": "ETH"}, {"name": "DOGE"}]},
            [
                {"markPx": "77000", "oraclePx": "77001", "openInterest": "10", "dayNtlVlm": "1000000", "funding": "0.0001"},
                {"markPx": "2100", "oraclePx": "2101", "openInterest": "20", "dayNtlVlm": "2000000", "funding": "-0.0002"},
                {"markPx": "0.1", "openInterest": "1", "dayNtlVlm": "2", "funding": "0"},
            ],
        ]

    def http_json_get(url, params=None, timeout=12, headers=None):
        if "stablecoins" in url:
            return {
                "peggedAssets": [
                    {
                        "symbol": "USDT",
                        "name": "Tether",
                        "price": 0.999,
                        "pegMechanism": "fiat-backed",
                        "circulating": {"peggedUSD": 1000},
                        "circulatingPrevDay": {"peggedUSD": 990},
                        "circulatingPrevWeek": {"peggedUSD": 980},
                    }
                ]
            }
        if "cftc" in url:
            return [
                {
                    "market_and_exchange_names": "BITCOIN - CHICAGO MERCANTILE EXCHANGE",
                    "report_date_as_yyyy_mm_dd": "2026-05-12T00:00:00.000",
                    "open_interest_all": "100",
                    "noncomm_positions_long_all": "60",
                    "noncomm_positions_short_all": "40",
                }
            ]
        raise AssertionError(url)

    def yahoo(symbol, interval="30m", range_name="5d", ttl_seconds=None):
        return {"symbol": symbol, "price": 10, "changePercent": 2, "volume24h": 1000}

    return {
        "http_json_get": http_json_get,
        "http_json_post": http_json_post,
        "get_yahoo_market_snapshot": yahoo,
        "utc_now_iso": lambda: "2026-05-19T00:00:00Z",
    }


def test_finance_external_sources_payload_has_seeded_sources():
    payload = finance_external_sources_service.build_finance_external_sources_payload(_ctx())

    assert payload["status"] == "ok"
    assert payload["sources"]["hyperliquid"] == "ok"
    assert payload["sources"]["tradexyz"] == "proxy"
    assert payload["summary"]["perpCount"] == 2
    assert payload["summary"]["etfCount"] >= 1
    assert payload["summary"]["cotCount"] == 5
    assert payload["summary"]["stablecoinCount"] == 1


def test_finance_external_sources_uses_etf_proxy_when_yahoo_empty():
    ctx = _ctx()
    ctx["get_yahoo_market_snapshot"] = lambda *args, **kwargs: None

    payload = finance_external_sources_service.build_finance_external_sources_payload(ctx)

    assert payload["sources"]["etfFlow"] == "proxy"
    assert payload["summary"]["etfCount"] == 2
    assert payload["etfFlow"]["items"][0]["source"] == "hyperliquid-etf-proxy"


def test_liquidity_regime_uses_external_seed_components():
    external = finance_external_sources_service.build_finance_external_sources_payload(_ctx())

    def get_cached_json(namespace, cache_key):
        if namespace == finance_external_sources_service.FINANCE_EXTERNAL_NAMESPACE:
            return external
        return None

    ctx = {
        "get_cached_json": get_cached_json,
        "get_snapshot_payload": lambda namespace, cache_key, builder, ttl_seconds=60: builder(),
        "get_market_groups_payload": lambda **kwargs: {
            "items": [
                {
                    "title": "When will Bitcoin hit $150k?",
                    "category": "crypto",
                    "volume24h": 100000,
                    "topOutcomes": [{"marketId": 1, "yesPrice": 0.25, "volume24h": 100000}],
                }
            ]
        },
        "get_yahoo_market_snapshot": lambda *args, **kwargs: {"price": 10, "changePercent": 1, "volume24h": 1000},
        "get_crypto_funding_watch_snapshot": lambda limit=8: {"assets": []},
        "utc_now_iso": lambda: "2026-05-19T00:00:00Z",
    }

    payload = finance_panels_service.get_finance_liquidity_regime_snapshot(ctx, limit=12)
    component_keys = {item["key"] for item in payload["components"]}
    sources = payload["sources"]

    assert {"cot", "etfFlow", "stablecoin"}.issubset(component_keys)
    assert sources["cot"] == "ok"
    assert sources["etf"] == "ok"
    assert sources["stablecoin"] == "ok"
