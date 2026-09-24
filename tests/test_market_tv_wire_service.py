from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from api.services import live_video_source_service as service
from api.services import hls_proxy_service
from api.services import youtube_embed_service
from api.services import youtube_live_probe_service as youtube_probe


def test_market_tv_wire_manifest_payload_builds_without_network() -> None:
    payload = service.build_market_tv_wire_payload(
        {
            "utc_now_iso": lambda: "2026-06-15T00:00:00Z",
            "market_tv_youtube_probe_enabled": False,
            "market_tv_hls_probe_enabled": False,
        },
        include_iptv=False,
    )

    assert payload["status"] == "ok"
    assert payload["summary"]["total"] >= 10
    assert payload["items"]
    assert payload["items"][0]["relevanceScore"] >= payload["items"][-1]["relevanceScore"]
    assert {category["id"] for category in payload["categories"]} >= {"macro", "geo", "weather"}


def test_market_tv_wire_adds_trusted_hls_sources_without_iptv() -> None:
    payload = service.build_market_tv_wire_payload(
        {
            "utc_now_iso": lambda: "2026-06-15T00:00:00Z",
            "market_tv_youtube_probe_enabled": False,
            "market_tv_hls_probe_enabled": False,
        },
        include_iptv=False,
    )

    trusted = [item for item in payload["items"] if item.get("playbackTier") == "trusted-hls"]

    assert trusted
    assert payload["sources"]["trustedHls"]["count"] == len(trusted)
    assert any(item["id"] == "trusted-hls-cnbc" and item.get("hlsProxyRequired") is True for item in trusted)


def test_market_youtube_channels_use_curated_fallback_embeds_without_probe() -> None:
    payload = service.build_market_tv_wire_payload(
        {
            "utc_now_iso": lambda: "2026-06-15T00:00:00Z",
            "market_tv_youtube_probe_enabled": False,
            "market_tv_hls_probe_enabled": False,
        },
        include_iptv=False,
    )
    youtube_payload = service.normalize_market_youtube_channels_payload(payload, limit=80)
    tv_payload = service.normalize_market_tv_wire_payload(payload, limit=80)

    youtube_ids = {item["id"] for item in youtube_payload["items"]}

    assert youtube_payload["summary"]["embedReady"] >= 16
    assert payload["sources"]["trustedYoutubeFallback"]["count"] >= 4
    assert "trusted-youtube-cbs-news" in youtube_ids
    assert "youtube-nocookie.com/embed/R9L8sDK8iEc" in next(
        item for item in youtube_payload["items"] if item["id"] == "trusted-youtube-cbs-news"
    )["youtubeEmbedUrl"]
    assert all(item["sourceType"] == "hls" for item in tv_payload["items"])


def test_market_youtube_topic_filters_match_tags_not_only_primary_category() -> None:
    payload = service.build_market_tv_wire_payload(
        {
            "utc_now_iso": lambda: "2026-06-15T00:00:00Z",
            "market_tv_youtube_probe_enabled": False,
            "market_tv_hls_probe_enabled": False,
        },
        include_iptv=False,
    )
    all_payload = service.normalize_market_youtube_channels_payload(payload, limit=80)
    elections_payload = service.normalize_market_youtube_channels_payload(payload, limit=80, category="elections")

    election_ids = {item["id"] for item in elections_payload["items"]}
    election_category = next(category for category in all_payload["categories"] if category["id"] == "elections")

    assert elections_payload["selection"]["total"] >= 8
    assert election_category["count"] == elections_payload["selection"]["total"]
    assert "cspan-youtube" in election_ids
    assert "pbs-newshour-youtube" in election_ids
    assert "abc-news-youtube" in election_ids
    assert any(item["category"] != "elections" for item in elections_payload["items"])


def test_market_youtube_channels_fill_missing_fallback_from_channel_rss(monkeypatch) -> None:
    def load_manifest() -> list[dict]:
        return [
            {
                "id": "rss-demo-youtube",
                "displayName": "RSS Demo",
                "category": "macro",
                "sourceType": "youtube",
                "youtubeHandle": "@rssdemo",
                "youtubeChannelId": "UCdemo",
                "youtubeProbeEnabled": False,
                "externalUrl": "https://www.youtube.com/@rssdemo/streams",
                "sourceUrl": "https://www.youtube.com/@rssdemo/streams",
                "marketTags": ["macro", "fed"],
            }
        ]

    rss = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <yt:videoId>rssABC12345</yt:videoId>
    <title>Macro hearing live replay</title>
  </entry>
</feed>
"""

    monkeypatch.setattr(service, "load_manifest_items", load_manifest)
    payload = service.build_market_tv_wire_payload(
        {
            "utc_now_iso": lambda: "2026-06-15T00:00:00Z",
            "http_text_get": lambda url, **kwargs: rss,
            "market_tv_youtube_rss_fallback_enabled": True,
            "market_tv_youtube_probe_enabled": False,
            "market_tv_hls_probe_enabled": False,
        },
        include_iptv=False,
    )
    youtube_payload = service.normalize_market_youtube_channels_payload(payload, limit=80)
    demo = next(item for item in youtube_payload["items"] if item["id"] == "rss-demo-youtube")

    assert payload["sources"]["youtubeRssFallback"]["readyCount"] == 1
    assert demo["fallbackVideoId"] == "rssABC12345"
    assert demo["youtubeFallbackSource"] == "channel-rss"
    assert "youtube-nocookie.com/embed/rssABC12345" in demo["youtubeEmbedUrl"]


def test_market_youtube_daily_rss_refresh_updates_existing_probe_disabled_fallback(monkeypatch) -> None:
    def load_manifest() -> list[dict]:
        return [
            {
                "id": "rss-refresh-youtube",
                "displayName": "RSS Refresh",
                "category": "economy",
                "sourceType": "youtube",
                "youtubeHandle": "@rssrefresh",
                "youtubeChannelId": "UCrefresh",
                "youtubeProbeEnabled": False,
                "fallbackVideoId": "oldVID12345",
                "externalUrl": "https://www.youtube.com/@rssrefresh/videos",
                "sourceUrl": "https://www.youtube.com/@rssrefresh/videos",
                "marketTags": ["economy", "policy"],
            }
        ]

    rss = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <yt:videoId>newVID12345</yt:videoId>
    <title>Fresh economy briefing</title>
  </entry>
</feed>
"""

    monkeypatch.setattr(service, "load_manifest_items", load_manifest)
    payload = service.build_market_tv_wire_payload(
        {
            "utc_now_iso": lambda: "2026-06-15T00:00:00Z",
            "http_text_get": lambda url, **kwargs: rss,
            "market_tv_youtube_rss_fallback_enabled": True,
            "market_tv_youtube_rss_refresh_existing": True,
            "market_tv_youtube_probe_enabled": False,
            "market_tv_hls_probe_enabled": False,
        },
        include_iptv=False,
    )
    youtube_payload = service.normalize_market_youtube_channels_payload(payload, limit=80)
    demo = next(item for item in youtube_payload["items"] if item["id"] == "rss-refresh-youtube")

    assert payload["sources"]["youtubeRssFallback"]["readyCount"] == 1
    assert demo["fallbackVideoId"] == "newVID12345"
    assert demo["youtubeFallbackTitle"] == "Fresh economy briefing"
    assert "youtube-nocookie.com/embed/newVID12345" in demo["youtubeEmbedUrl"]


def test_market_tv_wire_m3u_parser_keeps_https_hls_and_marks_not_24_7() -> None:
    text = """
#EXTM3U
#EXTINF:-1 tvg-id="Demo.News" tvg-country="US" tvg-language="en",Demo News [Not 24/7]
https://example.com/live/demo.m3u8
#EXTINF:-1 tvg-id="Bad.Http",Bad HTTP
http://example.com/live/bad.m3u8
"""
    items = service.parse_m3u_playlist(text, category="news", source_url="https://iptv.example/news.m3u", generated_at="2026-06-15T00:00:00Z")

    assert len(items) == 1
    assert items[0]["displayName"] == "Demo News"
    assert items[0]["sourceType"] == "hls"
    assert items[0]["status"] == "not_24_7"
    assert items[0]["country"] == "US"


def test_market_tv_wire_runtime_snapshot_does_not_live_build_by_default() -> None:
    payload = service.get_market_tv_wire_snapshot({"utc_now_iso": lambda: "2026-06-15T00:00:00Z"}, limit=5)

    assert payload["status"] == "warming"
    assert payload["cacheMode"] == "warming"
    assert payload["items"] == []


def test_market_tv_wire_category_filter_runs_before_limit() -> None:
    payload = {
        "generatedAt": "2026-06-15T00:00:00Z",
        "status": "ok",
        "cacheMode": "seeded",
        "items": [
            {"id": "yt-macro", "displayName": "YouTube Macro", "category": "macro", "sourceType": "youtube", "status": "ready", "relevanceScore": 101},
            {"id": "macro-1", "displayName": "Macro 1", "category": "macro", "sourceType": "hls", "status": "ready", "relevanceScore": 100},
            {"id": "macro-2", "displayName": "Macro 2", "category": "macro", "sourceType": "hls", "status": "ready", "relevanceScore": 99},
            {"id": "sports-1", "displayName": "Sports 1", "category": "sports", "sourceType": "hls", "status": "ready", "relevanceScore": 80},
            {"id": "sports-2", "displayName": "Sports 2", "category": "sports", "sourceType": "hls", "status": "ready", "relevanceScore": 79},
        ],
    }

    top_payload = service.normalize_market_tv_wire_payload(payload, limit=2)
    sports_payload = service.normalize_market_tv_wire_payload(payload, limit=2, category="sports")

    assert [item["id"] for item in top_payload["items"]] == ["macro-1", "macro-2"]
    assert all(item["sourceType"] == "hls" for item in top_payload["items"])
    assert [item["id"] for item in sports_payload["items"]] == ["sports-1", "sports-2"]
    assert sports_payload["selection"] == {
        "category": "sports",
        "total": 2,
        "returned": 2,
        "limit": 2,
        "truncated": False,
    }


def test_youtube_probe_parses_live_video_and_hls_manifest() -> None:
    html = r'''
<html><head><meta property="og:url" content="https://www.youtube.com/@Foxweather/live"></head>
<script>
{"channelId":"UCabc123","ownerChannelName":"FOX Weather","videoDetails":{"videoId":"abcDEF12345","title":"FOX Weather Live","isLive":true},"hlsManifestUrl":"https:\/\/example.com\/live.m3u8\u0026sig=1"}
</script>
</html>
'''

    payload = youtube_probe.parse_channel_html(html)

    assert payload["channelExists"] is True
    assert payload["channelId"] == "UCabc123"
    assert payload["channelName"] == "FOX Weather"
    assert payload["videoId"] == "abcDEF12345"
    assert payload["isLive"] is True
    assert payload["title"] == "FOX Weather Live"
    assert payload["hlsUrl"] == "https://example.com/live.m3u8&sig=1"


def test_youtube_probe_uses_relay_before_scrape() -> None:
    calls: list[str] = []
    seen_headers: list[dict] = []

    def http_json_get(url: str, **kwargs: object) -> dict:
        calls.append(url)
        seen_headers.append(dict(kwargs.get("headers") or {}))
        return {
            "videoId": "abcDEF12345",
            "isLive": True,
            "channelExists": True,
            "channelName": "Relay Channel",
            "title": "Relay Live",
            "hlsUrl": "https://example.com/live.m3u8",
        }

    payload = youtube_probe.probe_youtube_live(
        {
            "youtube_live_relay_base_url": "https://relay.example",
            "youtube_live_relay_token": "secret",
            "http_json_get": http_json_get,
        },
        channel="@demo",
    )

    assert calls and calls[0].startswith("https://relay.example/youtube-live?")
    assert "channel=%40demo" in calls[0]
    assert seen_headers[0]["x-relay-key"] == "secret"
    assert seen_headers[0]["Authorization"] == "Bearer secret"
    assert payload["videoId"] == "abcDEF12345"
    assert payload["isLive"] is True


def test_youtube_embed_bridge_html_posts_player_events() -> None:
    html = youtube_embed_service.build_youtube_embed_html(
        video_id="abcDEF12345",
        request_origin="https://polymonitor.club",
        parent_origin="https://polymonitor.club",
    )

    assert "https://www.youtube.com/iframe_api" in html
    assert "postMessage" in html
    assert "yt-ready" in html
    assert "yt-error" in html


def test_market_tv_wire_enriches_youtube_manifest_sources() -> None:
    def probe(channel: str, video_id: str) -> dict:
        if channel == "@Foxweather":
            return {
                "videoId": "abcDEF12345",
                "isLive": True,
                "channelExists": True,
                "channelId": "UCabc123",
                "channelName": "FOX Weather",
                "hlsUrl": "https://example.com/fox-weather.m3u8",
                "title": "FOX Weather Live Now",
                "error": "",
            }
        return {
            "videoId": "",
            "isLive": False,
            "channelExists": True,
            "channelName": "",
            "hlsUrl": "",
            "title": "",
            "error": "",
        }

    payload = service.build_market_tv_wire_payload(
        {
            "utc_now_iso": lambda: "2026-06-15T00:00:00Z",
            "youtube_live_probe": probe,
            "market_tv_hls_probe_enabled": False,
        },
        include_iptv=False,
    )
    fox_weather = next(item for item in payload["items"] if item["id"] == "fox-weather-live")

    assert payload["sources"]["youtubeLiveProbe"]["liveCount"] >= 1
    assert fox_weather["youtubeProbeStatus"] == "live"
    assert fox_weather["youtubeChannelId"] == "UCabc123"
    assert fox_weather["youtubeLiveVideoId"] == "abcDEF12345"
    assert fox_weather["youtubeHlsUrl"] == "https://example.com/fox-weather.m3u8"
    assert "youtube-nocookie.com/embed/abcDEF12345" in fox_weather["youtubeEmbedUrl"]
    assert fox_weather["fallbackVideoId"] == "abcDEF12345"
    assert fox_weather["externalUrl"] == "https://www.youtube.com/watch?v=abcDEF12345"


def test_market_tv_wire_hls_probe_uses_http_fallback_when_ffprobe_missing(monkeypatch) -> None:
    class Response:
        def __init__(self, body: bytes | str) -> None:
            self.status = 200
            self.body = body.encode() if isinstance(body, str) else body

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, max_bytes: int) -> bytes:
            return self.body[:max_bytes]

    def fake_urlopen(request: object, timeout: int = 10) -> Response:
        url = getattr(request, "full_url", "")
        if url.endswith("master.m3u8"):
            return Response("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nchild/live.m3u8\n")
        if url.endswith("child/live.m3u8"):
            return Response("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment.ts\n")
        if url.endswith("child/segment.ts"):
            return Response(b"\x47\x40\x00\x10")
        raise AssertionError(url)

    monkeypatch.setattr(service.shutil, "which", lambda _: None)
    monkeypatch.setattr(service, "urlopen", fake_urlopen)

    probe = service._probe_hls_stream(
        {"market_tv_hls_probe_timeout_seconds": 3},
        {"sourceType": "hls", "hlsUrl": "https://example.com/master.m3u8"},
    )

    assert probe["ok"] is True
    assert probe["status"] == "playable"
    assert probe["streams"] == ["manifest", "segment"]


def test_hls_proxy_rewrites_manifest_to_same_proxy_route() -> None:
    manifest = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:4,\nsegment.ts\n'

    rewritten = hls_proxy_service.rewrite_manifest(
        manifest,
        manifest_url="https://example.com/live/master.m3u8",
    )

    assert "hls-proxy?url=https%3A%2F%2Fexample.com%2Flive%2Fsegment.ts" in rewritten
    assert 'URI="hls-proxy?url=https%3A%2F%2Fexample.com%2Flive%2Fkey.bin"' in rewritten


def test_market_youtube_channels_payload_filters_curated_youtube_sources() -> None:
    payload = {
        "generatedAt": "2026-06-15T00:00:00Z",
        "status": "ok",
        "cacheMode": "seeded",
        "items": [
            {
                "id": "yt-weather",
                "displayName": "Weather Live",
                "category": "weather",
                "sourceType": "youtube",
                "youtubeProbeStatus": "live",
                "youtubeLiveVideoId": "abcDEF12345",
                "youtubeEmbedUrl": "https://www.youtube-nocookie.com/embed/abcDEF12345",
                "status": "ready",
                "relevanceScore": 95,
            },
            {
                "id": "hls-weather",
                "displayName": "HLS Weather",
                "category": "weather",
                "sourceType": "hls",
                "hlsUrl": "https://example.com/live.m3u8",
                "status": "ready",
                "relevanceScore": 99,
            },
            {
                "id": "yt-geo",
                "displayName": "Geo Live",
                "category": "geo",
                "sourceType": "youtube",
                "fallbackVideoId": "defGHI12345",
                "status": "ready",
                "relevanceScore": 90,
            },
        ],
    }

    all_payload = service.normalize_market_youtube_channels_payload(payload, limit=10)
    weather_payload = service.normalize_market_youtube_channels_payload(payload, limit=10, category="weather")

    assert [item["id"] for item in all_payload["items"]] == ["yt-weather", "yt-geo"]
    assert [item["id"] for item in weather_payload["items"]] == ["yt-weather"]
    assert all_payload["summary"]["total"] == 2
    assert all_payload["summary"]["embedReady"] == 2
    assert "youtube-nocookie.com/embed/defGHI12345" in all_payload["items"][1]["youtubeEmbedUrl"]


def test_market_youtube_channels_drops_channel_live_embed_cache() -> None:
    payload = {
        "generatedAt": "2026-06-15T00:00:00Z",
        "status": "ok",
        "cacheMode": "seeded",
        "items": [
            {
                "id": "yt-channel-only",
                "displayName": "Channel Only",
                "category": "crypto",
                "sourceType": "youtube",
                "youtubeChannelId": "UCdemo",
                "youtubeEmbedUrl": "https://www.youtube.com/embed/live_stream?channel=UCdemo",
                "youtubeEmbedMode": "channel-live",
                "status": "ready",
                "relevanceScore": 88,
            }
        ],
    }

    next_payload = service.normalize_market_youtube_channels_payload(payload, limit=10)

    assert next_payload["summary"]["embedReady"] == 0
    assert next_payload["items"][0]["youtubeEmbedUrl"] is None
    assert next_payload["items"][0]["youtubeEmbedMode"] is None


def test_market_tv_wire_seed_keeps_curated_manifest_items_when_iptv_is_large(monkeypatch) -> None:
    def load_manifest() -> list[dict]:
        return [
            {
                "id": "yt-low-score",
                "displayName": "Low Score YouTube",
                "category": "culture",
                "sourceType": "youtube",
                "youtubeHandle": "@demo",
                "youtubeChannelId": "UCdemo",
                "externalUrl": "https://www.youtube.com/@demo/streams",
                "sourceUrl": "https://www.youtube.com/@demo/streams",
                "marketTags": ["culture"],
            }
        ]

    playlist = "#EXTM3U\n" + "\n".join(
        f'#EXTINF:-1 tvg-id="Demo{i}",Demo {i}\nhttps://example.com/live/{i}.m3u8'
        for i in range(32)
    )

    monkeypatch.setattr(service, "load_manifest_items", load_manifest)
    monkeypatch.setenv("POLYDATA_MARKET_TV_WIRE_SEED_LIMIT", "24")

    payload = service.build_market_tv_wire_payload(
        {
            "utc_now_iso": lambda: "2026-06-15T00:00:00Z",
            "http_text_get": lambda url, **kwargs: playlist,
            "market_tv_youtube_probe_enabled": False,
            "market_tv_hls_probe_enabled": False,
        }
    )

    ids = [item["id"] for item in payload["items"]]

    assert "yt-low-score" in ids
    assert len(payload["items"]) >= 24
    assert not any(item_id.startswith("iptv-") for item_id in ids)
