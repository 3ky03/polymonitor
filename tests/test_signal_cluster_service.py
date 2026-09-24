from decimal import Decimal

from scripts.api.services import signal_cluster_service


def _ctx(related_calls):
    def _safe_decimal(value):
        if value is None:
            return None
        return Decimal(str(value))

    def _related_content(market_id, limit=2):
        related_calls.append((market_id, limit))
        return {
            "items": [
                {
                    "source": "TestWire",
                    "title": "Related market intel",
                    "url": "https://example.test/story",
                    "publishedAt": "2026-06-06T00:00:00Z",
                }
            ]
        }

    return {
        "_safe_decimal": _safe_decimal,
        "format_trade_decimal": lambda value: None if value is None else str(value),
        "normalize_address": lambda value: str(value or "").lower(),
        "table_exists": lambda table_name: False,
        "get_related_content_by_market_id": _related_content,
    }


def _trades():
    return [
        {
            "marketId": 123,
            "marketTitle": "Test market",
            "outcome": "YES",
            "side": "BUY",
            "price": "0.60",
            "size": "300",
            "notional": "180",
            "maker": "0x1111111111111111111111111111111111111111",
            "taker": "0x2222222222222222222222222222222222222222",
            "timestamp": "2026-06-06T00:00:00Z",
            "txHash": "0xabc",
        },
        {
            "marketId": 123,
            "marketTitle": "Test market",
            "outcome": "YES",
            "side": "BUY",
            "price": "0.62",
            "size": "250",
            "notional": "155",
            "maker": "0x3333333333333333333333333333333333333333",
            "taker": "0x2222222222222222222222222222222222222222",
            "timestamp": "2026-06-06T00:00:01Z",
            "txHash": "0xdef",
        },
    ]


def test_polybeats_cluster_related_news_disabled_by_default(monkeypatch):
    monkeypatch.delenv("POLYDATA_SIGNAL_RELATED_NEWS_ENABLED", raising=False)
    related_calls = []

    clusters = signal_cluster_service.build_polybeats_clusters(_ctx(related_calls), _trades(), {}, limit=4)

    assert clusters
    assert related_calls == []
    assert clusters[0]["relatedContent"] == []
    assert clusters[0]["sourceLabel"] == "CHAIN+$"


def test_polybeats_cluster_related_news_enabled(monkeypatch):
    monkeypatch.setenv("POLYDATA_SIGNAL_RELATED_NEWS_ENABLED", "1")
    related_calls = []

    clusters = signal_cluster_service.build_polybeats_clusters(_ctx(related_calls), _trades(), {}, limit=4)

    assert related_calls == [(123, 2)]
    assert clusters[0]["relatedContent"][0]["source"] == "TestWire"
    assert clusters[0]["sourceLabel"] == "NEWS+$"
