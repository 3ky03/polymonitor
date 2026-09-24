from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telegram.topics.api_client import resolve_polydata_api_base
from telegram.topics.client import TelegramClient
from telegram.topics.config import TelegramSettings, TopicConfig
from telegram.topics.formatters import (
    format_alpha_signal,
    format_latest_content,
    format_new_market_signals,
    format_nba_scoreboard,
    format_panel_snapshot,
    format_related_news,
    format_weather_map,
    format_weather_news,
    format_worldcup_intel,
)
from telegram.topics.market_linker import MarketLink
from telegram.topics.models import MessageCandidate
from telegram.topics.publisher import publish_candidates
from telegram.topics.state import PublishState


@pytest.fixture(autouse=True)
def disable_real_market_linking(monkeypatch):
    monkeypatch.setenv("POLYDATA_TELEGRAM_MARKET_LINKING_ENABLED", "false")


class FakeTelegram:
    def __init__(self) -> None:
        self.calls = []

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return [{"ok": True}]


class FakeSendResponse:
    def __init__(self, status_code, payload, headers=None):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}

    def json(self):
        return self.payload


class FakeSendSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.responses.pop(0)


def make_settings(state_path: str) -> TelegramSettings:
    return TelegramSettings(
        bot_token="token",
        telegram_api_base="https://api.telegram.org",
        polydata_api_base="http://127.0.0.1:18500",
        polydata_api_candidates=("http://127.0.0.1:18500",),
        state_path=state_path,
        request_timeout_seconds=12,
        watch_interval_seconds=60,
        dry_run=False,
        disable_notification=False,
        publish_on_api_fetch=False,
        news=TopicConfig(name="news", chat_id="@news"),
        intel=TopicConfig(name="intel", chat_id="@intel"),
        alpha=TopicConfig(name="alpha", chat_id="@alpha"),
        macro=TopicConfig(name="macro", chat_id="@macro"),
        nba=TopicConfig(name="nba", chat_id="@nba"),
        worldcup=TopicConfig(name="worldcup", chat_id="@worldcup"),
        weather=TopicConfig(name="weather", chat_id="@weather"),
        monitor=TopicConfig(name="monitor", chat_id="@monitor"),
    )


def test_nba_scoreboard_formatter_keys_score_changes():
    first = {
        "items": [
            {
                "id": "game-1",
                "awayTeam": "Lakers",
                "homeTeam": "Rockets",
                "awayScore": "101",
                "homeScore": "99",
                "status": "Final",
                "state": "post",
            }
        ]
    }
    second = {"items": [{**first["items"][0], "homeScore": "100"}]}

    first_message = format_nba_scoreboard(first)[0]
    second_message = format_nba_scoreboard(second)[0]

    assert "Lakers 101 @ Rockets 99" in first_message.text
    assert first_message.priority == "high"
    assert first_message.dedupe_key != second_message.dedupe_key


def test_weather_formatters_emit_market_and_warning_only():
    weather_map = {
        "summary": {
            "liveMarketCount": 1,
            "hottestCity": {"city": "Phoenix", "currentTemp": 102.1, "forecastHigh": 108.0},
        },
        "items": [
            {
                "cityId": "phoenix",
                "city": "Phoenix",
                "currentTemp": 102.1,
                "eventSlug": "phoenix-temp",
                "marketUrl": "https://polymarket.com/event/phoenix-temp",
                "quoteCoverage": "3/3",
                "topBin": {"label": "110F or higher", "midPriceYes": 0.37},
            }
        ],
    }
    news = {
        "items": [
            {"id": "n1", "title": "Phoenix heat warning", "severity": "warning", "city": "Phoenix", "source": "WX", "url": "https://news.example/1"},
            {"id": "n2", "title": "Mild forecast", "severity": "normal", "city": "Boston"},
        ]
    }

    map_messages = format_weather_map(weather_map)
    news_messages = format_weather_news(news)

    assert len(map_messages) == 2
    assert any(message.priority == "high" and "YES 37c" in message.text for message in map_messages)
    assert len(news_messages) == 1
    assert news_messages[0].priority == "high"


def test_rich_news_and_alpha_messages_include_tags_meme_and_links():
    news_message = format_latest_content(
        {
            "items": [
                {
                    "id": "n1",
                    "title": "Fed decision moves markets",
                    "source": "Reuters",
                    "summary": "Stocks and rates reacted after the decision.",
                    "url": "https://news.example/fed",
                }
            ]
        }
    )[0]
    alpha_message = format_alpha_signal(
        {
            "items": [
                {
                    "marketId": "m1",
                    "title": "12 accounts put $400 on YES: Bitcoin above $100k?",
                    "marketTitle": "Bitcoin above $100k?",
                    "summary": "Clustered smart-wallet buying detected.",
                    "outcome": "YES",
                    "sourceTag": "whale-flow",
                }
            ]
        }
    )[0]

    assert "#News" in news_message.text
    assert "Vibe:" in news_message.text
    assert "Source: https://news.example/fed" in news_message.text
    assert news_message.link_preview is True

    assert "#Alpha" in alpha_message.text
    assert "#Polymarket" in alpha_message.text
    assert "Vibe:" in alpha_message.text
    assert "Market: https://polymarket.com/search?query=Bitcoin+above+%24100k%3F" in alpha_message.text
    assert alpha_message.link_preview is True


def test_news_message_adds_trade_button_when_market_link_matches(monkeypatch):
    monkeypatch.setattr(
        "telegram.topics.formatters.resolve_market_link",
        lambda item, *, title="", extra_text=(): MarketLink(
            url="https://polymarket.com/event/fed-decision-june",
            title="Fed decision in June?",
            matched_by="text",
            score=0.9,
        ),
    )

    message = format_latest_content(
        {
            "items": [
                {
                    "id": "n1",
                    "title": "Fed decision moves markets",
                    "source": "Reuters",
                    "summary": "The June Fed decision changed rate odds.",
                    "url": "https://news.example/fed",
                }
            ]
        }
    )[0]

    assert "Market: https://polymarket.com/event/fed-decision-june" in message.text
    assert "Source: https://news.example/fed" in message.text
    assert message.reply_markup["inline_keyboard"][0][0]["text"] == "Trade Polymarket"
    assert message.reply_markup["inline_keyboard"][0][0]["url"] == "https://polymarket.com/event/fed-decision-june"


def test_nba_scoreboard_adds_market_link_button(monkeypatch):
    monkeypatch.setattr(
        "telegram.topics.formatters.resolve_market_link",
        lambda item, *, title="", extra_text=(): MarketLink(
            url="https://polymarket.com/event/lakers-rockets",
            title="Lakers vs Rockets",
            matched_by="text",
            score=0.92,
        ),
    )

    message = format_nba_scoreboard(
        {"items": [{"id": "game-1", "awayTeam": "Lakers", "homeTeam": "Rockets", "status": "Scheduled"}]}
    )[0]

    assert "Market: https://polymarket.com/event/lakers-rockets" in message.text
    assert message.reply_markup["inline_keyboard"][0][0]["url"] == "https://polymarket.com/event/lakers-rockets"


def test_alpha_dedupe_ignores_flow_and_score_updates_for_same_market_direction():
    base = {
        "items": [
            {
                "marketId": "m1",
                "marketTitle": "Mexico vs South Africa",
                "title": "BUY NO flow $10.9k: Mexico vs South Africa",
                "summary": "net 79.9%; 75 fills",
                "score": 81,
                "outcome": "NO",
            }
        ]
    }
    updated = {
        "items": [
            {
                "marketId": "m1",
                "marketTitle": "Mexico vs South Africa",
                "title": "BUY NO flow $12.1k: Mexico vs South Africa",
                "summary": "net 82.4%; 91 fills",
                "score": 88,
                "outcome": "NO",
            }
        ]
    }
    reversed_side = {
        "items": [
            {
                "marketId": "m1",
                "marketTitle": "Mexico vs South Africa",
                "title": "BUY YES flow: Mexico vs South Africa",
                "summary": "direction flipped",
                "outcome": "YES",
            }
        ]
    }

    assert format_alpha_signal(base)[0].dedupe_key == format_alpha_signal(updated)[0].dedupe_key
    assert format_alpha_signal(base)[0].dedupe_key != format_alpha_signal(reversed_side)[0].dedupe_key


def test_new_market_dedupe_ignores_status_updates():
    first = {"items": [{"marketId": "m1", "title": "Will it rain in NYC?", "status": "fresh"}]}
    second = {"items": [{"marketId": "m1", "title": "Will it rain in NYC?", "status": "updated"}]}

    assert format_new_market_signals(first)[0].dedupe_key == format_new_market_signals(second)[0].dedupe_key


def test_worldcup_dedupe_ignores_generated_at_when_visible_content_is_same():
    first = {"generatedAt": "2026-06-10T01:00:00Z", "signals": [{"id": "s1", "title": "Mexico vs South Africa: Scheduled"}]}
    second = {"generatedAt": "2026-06-10T01:05:00Z", "signals": [{"id": "s1", "title": "Mexico vs South Africa: Scheduled"}]}
    changed = {"generatedAt": "2026-06-10T01:10:00Z", "signals": [{"id": "s2", "title": "Canada vs Bosnia: Scheduled"}]}

    assert format_worldcup_intel(first)[0].dedupe_key == format_worldcup_intel(second)[0].dedupe_key
    assert format_worldcup_intel(first)[0].dedupe_key != format_worldcup_intel(changed)[0].dedupe_key


def test_related_news_formatter_includes_market_context_and_routes_to_intel():
    payload = {
        "marketId": 1871431,
        "marketTitle": "NBA player performance market",
        "marketCategory": "Sports",
        "items": [
            {
                "id": f"intel-{index}",
                "contentType": content_type,
                "title": f"{label} moves player props {index}",
                "source": source,
                "summary": "A starter is questionable before tipoff.",
                "url": f"https://news.example/nba-lineup-{index}",
            }
            for index, (content_type, label, source) in enumerate(
                [
                    ("news", "Lineup update", "ESPN"),
                    ("news", "Injury report", "The Athletic"),
                    ("news", "Rotation note", "AP"),
                    ("video", "Film breakdown", "YouTube"),
                    ("video", "Postgame clip", "NBA"),
                    ("report", "Betting report", "Action Network"),
                    ("report", "Model report", "NumberFire"),
                    ("research", "Research note", "SSRN"),
                    ("research", "Paper abstract", "arXiv"),
                ]
            )
        ],
    }
    messages = format_related_news(payload)
    message = messages[0]

    assert len(messages) == 1
    assert message.topic == "intel"
    assert "NBA player performance market" in message.text
    assert "News 3 | Video 2 | Reports 2 | Research 2" in message.text
    assert "News:" in message.text
    assert "- ESPN | Lineup update moves player props 0" in message.text
    assert "Video:" in message.text
    assert "- YouTube | Film breakdown moves player props 3" in message.text
    assert "Reports:" in message.text
    assert "- Action Network | Betting report moves player props 5" in message.text
    assert "Research:" in message.text
    assert "- SSRN | Research note moves player props 7" in message.text
    assert "Rotation note moves player props 2" not in message.text
    assert "Top source: https://news.example/nba-lineup-0" in message.text
    assert message.link_preview is True


def test_related_news_formatter_accepts_single_item_payload():
    message = format_related_news(
        {
            "marketId": 1871431,
            "marketTitle": "NBA player performance market",
            "marketCategory": "Sports",
            "items": [
                {
                    "id": "intel-1",
                    "contentType": "news",
                    "title": "Lineup update moves player props",
                    "source": "ESPN",
                    "summary": "A starter is questionable before tipoff.",
                    "url": "https://news.example/nba-lineup",
                }
            ],
        }
    )[0]

    assert message.topic == "intel"
    assert "NBA player performance market" in message.text
    assert "News 1" in message.text
    assert "News:" in message.text
    assert "- ESPN | Lineup update moves player props" in message.text
    assert "Top source: https://news.example/nba-lineup" in message.text
    assert message.link_preview is True


def test_worldcup_intel_formatter_routes_to_worldcup_topic():
    message = format_worldcup_intel(
        {
            "generatedAt": "2026-06-08T11:49:40Z",
            "summary": "World Cup operations board is live.",
            "signals": [
                {
                    "id": "signal-1",
                    "source": "ESPN SCOREBOARD",
                    "title": "South Africa at Mexico: Scheduled",
                    "url": "https://www.espn.com/soccer/match/_/gameId/760415/south-africa-mexico",
                }
            ],
            "news": [
                {
                    "id": "news-1",
                    "source": "ESPN",
                    "title": "World Cup opener team news",
                    "url": "https://www.espn.com/soccer/story/world-cup-opener",
                }
            ],
            "weather": [
                {
                    "cityId": "dallas",
                    "current": {"condition": "Partly cloudy", "tempC": 26, "precipitationProbability": 5},
                    "forecast": [{"precipitationProbability": 40}],
                }
            ],
        }
    )[0]

    assert message.topic == "worldcup"
    assert "⚽ worldcup" in message.text
    assert "FIFA World Cup 2026 workspace" in message.text
    assert "Signals 1 | News 1 | Weather 1" in message.text
    assert "Signals:" in message.text
    assert "- ESPN SCOREBOARD | South Africa at Mexico: Scheduled" in message.text
    assert "News:" in message.text
    assert "- ESPN | World Cup opener team news" in message.text
    assert "Weather:" in message.text
    assert "- dallas: Partly cloudy | 26C | rain 40%" in message.text
    assert "Workspace: https://www.polymonitor.club/?workspace=worldcup" in message.text
    assert message.link_preview is True
    assert message.reply_markup
    assert "PolyMonitorQuery_bot" in str(message.reply_markup)


def test_publish_state_dedupes_and_dry_run_does_not_mark(tmp_path: Path):
    state_path = str(tmp_path / "telegram_state.json")
    settings = make_settings(state_path)
    state = PublishState(state_path)
    telegram = FakeTelegram()
    candidate = MessageCandidate(
        topic="nba",
        dedupe_key="same",
        text="hello",
        reply_markup={"inline_keyboard": [[{"text": "Open", "url": "https://example.test"}]]},
    )

    dry_result = publish_candidates([candidate], settings=settings, state=state, telegram=telegram, dry_run=True)
    assert dry_result.sent == 1
    assert not state.seen("nba", "same")
    assert telegram.calls == []

    first = publish_candidates([candidate], settings=settings, state=state, telegram=telegram)
    second = publish_candidates([candidate], settings=settings, state=state, telegram=telegram)

    assert first.sent == 1
    assert second.sent == 0
    assert second.skipped_seen == 1
    assert telegram.calls[0]["chat_id"] == "@nba"
    assert telegram.calls[0]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Open"


def test_telegram_client_retries_429_and_redacts_token(monkeypatch):
    sleeps = []
    client = TelegramClient(bot_token="secret-token")
    client.session = FakeSendSession(
        [
            FakeSendResponse(429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 2}}),
            FakeSendResponse(200, {"ok": True, "result": {"message_id": 1}}),
        ]
    )
    monkeypatch.setattr("telegram.topics.client.time.sleep", lambda seconds: sleeps.append(seconds))

    result = client.send_message(chat_id="@news", text="hello")

    assert result[0]["ok"] is True
    assert sleeps == [3]
    assert len(client.session.calls) == 2

    failing_client = TelegramClient(bot_token="secret-token")
    failing_client.session = FakeSendSession([FakeSendResponse(500, {"ok": False, "description": "Server exploded"})])

    try:
        failing_client.send_message(chat_id="@news", text="hello")
    except RuntimeError as exc:
        error = str(exc)
    else:
        raise AssertionError("expected send failure")

    assert "Server exploded" in error
    assert "secret-token" not in error


class FakeHealthResponse:
    def __init__(self, payload, *, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeHealthSession:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        if "bad" in url:
            raise RuntimeError("connection refused")
        return FakeHealthResponse({"status": "ok"})


def test_resolve_polydata_api_base_skips_dead_local_and_uses_remote():
    session = FakeHealthSession()
    resolution = resolve_polydata_api_base(
        ["http://bad.local:5000", "http://remote.example/wm-api"],
        timeout_seconds=2,
        session=session,
    )

    assert resolution.healthy is True
    assert resolution.base_url == "http://remote.example/wm-api"
    assert session.calls[0][0] == "http://bad.local:5000/health"
    assert session.calls[1][0] == "http://remote.example/wm-api/health"


def test_format_panel_snapshot_routes_known_panel_ids():
    assert format_panel_snapshot("latest-content", {"items": [{"id": "n1", "title": "Hello", "source": "RSS"}]})[0].topic == "news"
    assert format_panel_snapshot("related-news", {"marketId": 1, "marketTitle": "Market", "items": [{"id": "r1", "title": "Intel"}]})[0].topic == "intel"
    assert format_panel_snapshot("alpha-signal", {"items": [{"id": "a1", "title": "Signal"}]})[0].topic == "alpha"
    assert format_panel_snapshot("polymarket-macro-map", {"items": [{"id": "m1", "title": "Macro"}]})[0].topic == "macro"
    assert format_panel_snapshot("worldcup-intel", {"signals": [{"id": "w1", "title": "Kickoff radar"}]})[0].topic == "worldcup"
    assert format_panel_snapshot("unknown", {"items": [{"id": "x", "title": "Nope"}]}) == []
