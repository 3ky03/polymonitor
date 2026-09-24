from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.runtime_panels.registry import get_panel_by_id
from api.services import tech_panels_service


RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss><channel>
  <item>
    <title>OpenAI launches new model benchmark push - Reuters</title>
    <link>https://example.com/openai</link>
    <guid>openai-1</guid>
    <pubDate>Sun, 24 May 2026 10:00:00 GMT</pubDate>
    <source>Reuters</source>
    <description>OpenAI model release signal.</description>
  </item>
  <item>
    <title>TikTok app ban lawsuit update - CNBC</title>
    <link>https://example.com/tiktok</link>
    <guid>tiktok-1</guid>
    <pubDate>Sun, 24 May 2026 09:00:00 GMT</pubDate>
    <source>CNBC</source>
    <description>TikTok regulation signal.</description>
  </item>
</channel></rss>
"""

OPENROUTER_MODELS = {
    "data": [
        {
            "id": "qwen/qwen3.7-max",
            "canonical_slug": "qwen/qwen3.7-max-20260520",
            "name": "Qwen: Qwen3.7 Max",
            "created": 1779376861,
            "description": "Flagship Qwen model for agent-centric workloads.",
            "context_length": 1000000,
            "pricing": {"prompt": "0.0000025", "completion": "0.0000075"},
        },
        {
            "id": "x-ai/grok-build-0.1",
            "canonical_slug": "x-ai/grok-build-0.1-20260520",
            "name": "xAI: Grok Build 0.1",
            "created": 1779298123,
            "description": "Fast coding model.",
            "context_length": 256000,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        },
    ]
}

OPENROUTER_RANKINGS_RSC = """0:{"a":"$@1","f":"","q":"","i":false}
1:[{"date":"2026-05-23 00:00:00","variant_permaslug":"qwen/qwen3.7-max-20260520","total_completion_tokens":1000000000,"total_prompt_tokens":2000000000,"count":120000,"change":0.22},{"date":"2026-05-23 00:00:00","variant_permaslug":"x-ai/grok-build-0.1-20260520","total_completion_tokens":400000000,"total_prompt_tokens":600000000,"count":42000,"change":null}]
"""


def _ctx():
    def http_text_get(url, timeout=12, headers=None):
        assert "news.google.com" in url
        return RSS_XML

    def http_text_post(url, data="", timeout=12, headers=None):
        assert "openrouter.ai/rankings" in url
        assert "Next-Action" in (headers or {})
        return OPENROUTER_RANKINGS_RSC

    def http_json_get(url, params=None, timeout=12, headers=None):
        if "openrouter.ai/api/v1/models" in url:
            return OPENROUTER_MODELS
        assert "itunes.apple.com" in url
        if "topgrossingapplications" in url:
            title = "iTunes Store: Top Grossing Apps"
            names = [("ChatGPT", "OpenAI"), ("YouTube", "Google")]
        elif "genre=6014" in url:
            title = "iTunes Store: Top Free Applications in Games"
            names = [("Roblox", "Roblox Corporation"), ("Block Blast!", "Hungry Studio")]
        elif "genre=6009" in url:
            title = "iTunes Store: Top Free Applications in News"
            names = [("X", "X Corp."), ("Reddit", "reddit")]
        else:
            title = "iTunes Store: Top Free Apps"
            names = [("ChatGPT", "OpenAI"), ("TikTok", "TikTok Ltd.")]
        while len(names) < 10:
            names.append((f"{names[0][0]} {len(names) + 1}", names[0][1]))
        return {
            "feed": {
                "title": {"label": title},
                "entry": [
                    {
                        "im:name": {"label": name},
                        "im:artist": {"label": artist},
                        "id": {"attributes": {"im:id": str(index)}},
                        "link": {"attributes": {"href": f"https://apps.apple.com/app/{index}"}},
                    }
                    for index, (name, artist) in enumerate(names, start=1)
                ]
            }
        }

    def yahoo(symbol, interval="30m", range_name="5d", ttl_seconds=None):
        prices = {
            "NVDA": (180.0, 4_400_000_000_000, 2.4),
            "AAPL": (220.0, 3_400_000_000_000, -0.5),
            "MSFT": (510.0, 3_800_000_000_000, 0.8),
        }
        price, market_cap, change = prices.get(symbol, (100.0, 1_000_000_000_000, 0.0))
        return {
            "symbol": symbol,
            "price": price,
            "marketCap": market_cap,
            "changePercent": change,
            "points": [{"timestamp": "2026-05-24T00:00:00Z", "value": price}],
        }

    return {
        "SETTINGS": SimpleNamespace(
            tech_runtime_ttl_seconds=600,
            tech_google_news_rss_url="https://news.google.com/rss/search",
            tech_app_store_top_free_url="https://rss.applemarketingtools.com/api/v2/us/apps/top-free/25/apps.json",
        ),
        "http_text_get": http_text_get,
        "http_text_post": http_text_post,
        "http_json_get": http_json_get,
        "get_yahoo_market_snapshot": yahoo,
        "utc_now_iso": lambda: "2026-05-24T00:00:00Z",
    }


def test_tech_runtime_panels_are_registered():
    assert get_panel_by_id("ai-model-race").route == "/runtime/tech/ai-model-race"
    assert get_panel_by_id("big-tech-market-cap").route == "/runtime/tech/big-tech-market-cap"
    assert get_panel_by_id("consumer-app-pulse").route == "/runtime/tech/consumer-app-pulse"


def test_ai_model_race_payload_extracts_entity_and_tags():
    payload = tech_panels_service.build_tech_panel_payload(_ctx(), "ai-model-race", limit=36)

    assert payload["status"] == "ok"
    assert payload["sources"]["openRouterUsage"] == "ok"
    assert payload["sources"]["productSignal"] == "ok"
    assert payload["sources"]["apiPrice"] == "ok"
    assert [item["symbol"] for item in payload["summary"]["watchlist"]] == ["NEWS", "USAGE", "PRODUCT SIGNAL", "API PRICE"]
    usage_items = [item for item in payload["items"] if item.get("category") == "USAGE"]
    assert usage_items[0]["source"] == "OpenRouter Usage"
    assert usage_items[0]["metricLabel"] == "3.00B TOK"
    assert any("API PRICE" in item["tags"] for item in payload["items"])


def test_big_tech_market_cap_payload_ranks_by_market_cap():
    payload = tech_panels_service.build_tech_panel_payload(_ctx(), "big-tech-market-cap", limit=16)

    assert payload["status"] == "ok"
    assert payload["items"][0]["symbol"] == "NVDA"
    assert payload["items"][0]["metricUnit"] == "MKT CAP"
    assert payload["items"][0]["rank"] == 1
    assert len(payload["items"]) == 16
    assert payload["summary"]["tracked"] >= 20


def test_big_tech_market_cap_estimates_when_yahoo_omits_cap():
    ctx = _ctx()

    def yahoo_without_cap(symbol, interval="30m", range_name="5d", ttl_seconds=None):
        return {
            "symbol": symbol,
            "price": {"NVDA": 200.0, "AAPL": 100.0}.get(symbol, 10.0),
            "changePercent": 0.0,
            "points": [],
        }

    ctx["get_yahoo_market_snapshot"] = yahoo_without_cap
    payload = tech_panels_service.build_tech_panel_payload(ctx, "big-tech-market-cap", limit=2)

    assert payload["status"] == "ok"
    assert payload["items"][0]["symbol"] == "NVDA"
    assert payload["items"][0]["marketCap"] == 4_860_000_000_000
    assert payload["items"][0]["marketCapEstimated"] is True


def test_consumer_app_pulse_combines_app_store_and_news():
    payload = tech_panels_service.build_tech_panel_payload(_ctx(), "consumer-app-pulse", limit=40)

    assert payload["status"] == "ok"
    assert payload["sources"]["appStoreCharts"] == "ok"
    assert payload["summary"]["appStoreSourceCount"] >= 20
    assert [item["symbol"] for item in payload["summary"]["watchlist"]] == ["DOWNLOADS", "GAMES", "NEWS", "GROSSING"]
    assert [item["count"] for item in payload["summary"]["watchlist"]] == [10, 10, 10, 10]
    assert "TOP FREE" not in payload["items"][0]["tags"]
    categories = [item["category"] for item in payload["items"]]
    assert len(payload["items"]) == 40
    assert categories.count("DOWNLOADS") == 10
    assert categories.count("GAMES") == 10
    assert categories.count("NEWS") == 10
    assert categories.count("GROSSING") == 10
