from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from telegram.bot.commands import handle_command
from telegram.bot.config import BotSettings, load_settings
from telegram.bot.formatters import format_market_search, format_pnl_coverage, format_wallet
from telegram.bot.formatters import format_worldcup_match, format_worldcup_odds, worldcup_matches_action_links, worldcup_odds_action_links
from telegram.bot.models import CommandRequest
from telegram.bot.poller import check_alerts, run_once
from telegram.bot.polydata_api import PolyDataBotApi
from telegram.bot.router import parse_update
from telegram.bot.state import BotState


@pytest.fixture(autouse=True)
def _disable_live_market_linking(monkeypatch):
    monkeypatch.setenv("POLYDATA_TELEGRAM_MARKET_LINKING_ENABLED", "false")


class FakeApi:
    def worldcup_dashboard(self):
        return {
            "tournament": {"name": "FIFA World Cup 2026"},
            "matches": [
                {
                    "id": "wc2026-001",
                    "homeTeam": "Mexico",
                    "awayTeam": "South Africa",
                    "kickoffUtc": "2026-06-11T19:00:00Z",
                    "city": "Mexico City",
                    "cityId": "mexico-city",
                    "venue": "Estadio Azteca",
                    "group": "Group A",
                },
                {
                    "id": "wc2026-002",
                    "homeTeam": "South Korea",
                    "awayTeam": "Czech Republic",
                    "kickoffUtc": "2026-06-12T02:00:00Z",
                    "city": "Guadalajara / Zapopan",
                    "cityId": "guadalajara",
                    "venue": "Estadio Akron",
                    "group": "Group A",
                },
                {
                    "id": "wc2026-003",
                    "homeTeam": "Canada",
                    "awayTeam": "Bosnia & Herzegovina",
                    "kickoffUtc": "2026-06-12T19:00:00Z",
                    "city": "Toronto",
                    "cityId": "toronto",
                    "venue": "BMO Field",
                    "group": "Group B",
                },
                {
                    "id": "wc2026-004",
                    "homeTeam": "USA",
                    "awayTeam": "Paraguay",
                    "kickoffUtc": "2026-06-13T01:00:00Z",
                    "city": "Los Angeles / Inglewood",
                    "cityId": "los-angeles",
                    "venue": "SoFi Stadium",
                    "group": "Group D",
                },
                {
                    "id": "wc2026-005",
                    "homeTeam": "Qatar",
                    "awayTeam": "Switzerland",
                    "kickoffUtc": "2026-06-13T19:00:00Z",
                    "city": "San Francisco Bay Area",
                    "cityId": "san-francisco",
                    "venue": "Levi's Stadium",
                    "group": "Group E",
                },
                {
                    "id": "wc2026-006",
                    "homeTeam": "England",
                    "awayTeam": "Haiti",
                    "kickoffUtc": "2026-06-14T19:00:00Z",
                    "city": "Dallas / Arlington",
                    "cityId": "dallas",
                    "venue": "AT&T Stadium",
                    "group": "Group G",
                },
            ],
            "news": [
                {
                    "id": "n1",
                    "source": "ESPN",
                    "title": "Mexico World Cup opener team news",
                    "summary": "Mexico prepares for South Africa.",
                    "url": "https://www.espn.com/soccer/story/world-cup-opener",
                }
            ],
            "weather": [
                {
                    "cityId": "mexico-city",
                    "current": {"condition": "Sunny", "tempC": 24, "windKph": 8, "precipitationProbability": 4},
                    "forecast": [{"precipitationProbability": 18}],
                }
            ],
            "odds": [],
        }

    def worldcup_intel(self, *, limit: int = 24):
        return {
            "providerStates": {"espnNews": "ok", "espnScoreboard": "ok", "wttr": "ok", "fbref": "restricted"},
            "signals": [
                {
                    "id": "s1",
                    "source": "ESPN SCOREBOARD",
                    "title": "South Africa at Mexico: Scheduled",
                    "summary": "2026-06-11T19:00Z · Estadio Azteca",
                }
            ],
            "news": self.worldcup_dashboard()["news"],
            "weather": self.worldcup_dashboard()["weather"],
        }

    def worldcup_market_search(self, query: str, *, limit: int = 8):
        return self.search_markets(f"world cup {query}", limit=limit)

    def search_markets(self, query: str, *, limit: int = 5):
        return {
            "items": [
                {
                    "title": "Spurs vs. Thunder: O/U 225.5",
                    "latestPrice": "0.375",
                    "slug": "nba-sas-okc-2026-05-18-total-225pt5",
                    "tags": ["sports", "basketball", "nba"],
                    "tradeCount24h": 12,
                }
            ]
        }

    def alpha_signals(self, *, limit: int = 5):
        return {
            "items": [
                {
                    "title": "Bitcoin Up or Down",
                    "summary": "Clustered smart-wallet buying detected.",
                    "kind": "whale",
                    "severity": "watch",
                }
            ]
        }

    def wallet_summary(self, address: str, *, days: int = 30):
        return {
            "address": address,
            "summary": {
                "tradeCount": 1244,
                "buyCount": 610,
                "sellCount": 634,
                "volumeNotional": "12430",
                "activeMarkets": 18,
                "lastTradeAt": "2026-05-18T00:00:00Z",
            },
            "daily": [{"tradeCount": 10}],
            "topMarkets": [{"title": "NBA Finals Winner"}, {"title": "Bitcoin Up or Down"}],
        }

    def wallet_trades(self, address: str, *, limit: int = 5):
        return {"items": [{"marketTitle": "NBA Finals Winner", "side": "BUY", "outcome": "YES", "price": "0.42"}]}

    def pnl(self, address: str):
        return {"status": "not_ready", "coverage": {"tradeCashflows": False, "nonTradeCashflows": False, "positionSnapshot": False}}

    def crypto_markets(self):
        return {
            "items": [
                {"id": "btc", "label": "BTC", "symbol": "BTC-USD", "price": 76000.0},
                {"id": "eth", "label": "ETH", "symbol": "ETH-USD", "price": 2100.0},
            ]
        }


class FakeTelegram:
    def __init__(self, updates):
        self.updates = updates
        self.sent = []

    def get_updates(self, **kwargs):
        self.last_get_updates = kwargs
        return self.updates

    def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return [{"ok": True}]

    def answer_callback_query(self, **kwargs):
        self.last_callback_answer = kwargs
        return {"ok": True}


class FakeApiResponse:
    def __init__(self, payload, *, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeApiSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        if "bad.local" in url:
            raise __import__("requests").ReadTimeout("slow")
        return FakeApiResponse({"status": "ok", "items": [{"title": "Fallback market"}]})


def make_settings(state_path: str) -> BotSettings:
    return BotSettings(
        bot_token="token",
        telegram_api_base="https://api.telegram.org",
        polydata_api_base="http://127.0.0.1:18500",
        polydata_api_candidates=("http://127.0.0.1:18500",),
        state_path=state_path,
        request_timeout_seconds=12,
        poll_interval_seconds=2,
        long_poll_timeout_seconds=1,
        alert_check_interval_seconds=5,
        dry_run=False,
        allowed_chat_ids=set(),
        admin_user_ids=set(),
        rate_limit_per_minute=20,
    )


def test_query_bot_token_takes_priority(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("POLYDATA_TELEGRAM_QUERY_BOT_TOKEN", "query-token")
    monkeypatch.setenv("POLYDATA_TELEGRAM_BOT_TOKEN", "push-token")
    monkeypatch.setenv("POLYDATA_TELEGRAM_QUERY_BOT_STATE_PATH", str(tmp_path / "query_state.json"))

    settings = load_settings()

    assert settings.bot_token == "query-token"
    assert settings.state_path.endswith("query_state.json")


def test_query_bot_uses_query_state_path_when_legacy_state_is_set(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("POLYDATA_TELEGRAM_QUERY_BOT_TOKEN", "query-token")
    monkeypatch.setenv("POLYDATA_TELEGRAM_BOT_STATE_PATH", str(tmp_path / "legacy_state.json"))
    monkeypatch.delenv("POLYDATA_TELEGRAM_QUERY_BOT_STATE_PATH", raising=False)

    settings = load_settings()

    assert settings.bot_token == "query-token"
    assert settings.state_path.endswith("telegram_query_bot_state.json")


def test_parse_update_supports_bot_mentions_and_args():
    request = parse_update(
        {
            "update_id": 7,
            "message": {
                "message_id": 11,
                "text": "/market@PolyMonitorBot bitcoin",
                "chat": {"id": -100},
                "from": {"id": 42},
            },
        }
    )

    assert request is not None
    assert request.command == "market"
    assert request.args == "bitcoin"
    assert request.chat_id == -100
    assert request.user_id == 42


def test_parse_update_supports_callback_query_commands():
    request = parse_update(
        {
            "update_id": 8,
            "callback_query": {
                "id": "callback-1",
                "data": "/matches group a",
                "message": {"message_id": 12, "chat": {"id": -100}},
                "from": {"id": 42},
            },
        }
    )

    assert request is not None
    assert request.command == "matches"
    assert request.args == "group a"
    assert request.callback_query_id == "callback-1"


def test_parse_update_supports_chinese_commands():
    request = parse_update(
        {
            "update_id": 9,
            "message": {
                "message_id": 13,
                "text": "/比赛 墨西哥 南非",
                "chat": {"id": -100},
                "from": {"id": 42},
            },
        }
    )

    assert request is not None
    assert request.command == "比赛"
    assert request.args == "墨西哥 南非"


def test_market_formatter_adds_price_tags_and_polymarket_link():
    text = format_market_search("nba", FakeApi().search_markets("nba"))

    assert "🔎 Market Search: nba" in text
    assert "YES: 37.5%" in text
    assert "#sports #basketball #nba" in text
    assert "https://polymarket.com/event/nba-sas-okc-2026-05-18-total-225pt5" in text


def test_wallet_formatter_returns_profile_and_labels():
    api = FakeApi()
    address = "0x1234567890abcdef1234567890abcdef12345678"
    text = format_wallet(address, api.wallet_summary(address), api.wallet_trades(address))

    assert "👛 Wallet" in text
    assert "总交易次数：1,244" in text
    assert "交易量：12,430 USDC" in text
    assert "NBA Finals Winner" in text
    assert "高频交易者" in text
    assert "多市场活跃" in text


def test_pnl_formatter_is_coverage_only_when_not_ready():
    text = format_pnl_coverage("0x1234567890abcdef1234567890abcdef12345678", FakeApi().pnl("x"))

    assert "当前状态：PnL 正在接入 cashflow 层" in text
    assert "暂不输出完整 PnL" in text
    assert "trade cashflows: False" in text


def test_handle_command_routes_first_version_commands():
    api = FakeApi()
    base = {
        "update_id": 1,
        "chat_id": 1,
        "user_id": 2,
        "message_id": 3,
        "text": "",
        "raw": {},
    }

    assert "PolyMonitorBot" in handle_command(CommandRequest(command="start", args="", **base), api).text
    assert "Market:" in handle_command(CommandRequest(command="help", args="", **base), api).text
    assert "Spurs vs. Thunder" in handle_command(CommandRequest(command="market", args="nba", **base), api).text
    worldcup_reply = handle_command(CommandRequest(command="worldcup", args="", **base), api)
    assert "FIFA World Cup 2026" in worldcup_reply.text
    assert worldcup_reply.reply_markup
    assert "Mexico vs South Africa" in handle_command(CommandRequest(command="matches", args="", **base), api).text
    assert "Page 1/" in handle_command(CommandRequest(command="matches", args="", **base), api).text
    assert "没有找到比赛" in handle_command(CommandRequest(command="matches", args="today", **base), api).text
    assert "Group A" in handle_command(CommandRequest(command="matches", args="group a", **base), api).text
    assert "Next" in str(handle_command(CommandRequest(command="matches", args="", **base), api).reply_markup)
    assert "Prev" in str(handle_command(CommandRequest(command="matches", args="page 2", **base), api).reply_markup)
    assert "Kickoff" in handle_command(CommandRequest(command="match", args="mexico south africa", **base), api).text
    assert "Kickoff" in handle_command(CommandRequest(command="match", args="mex 南非", **base), api).text
    assert "Kickoff" in handle_command(CommandRequest(command="比赛", args="墨西哥 南非", **base), api).text
    assert "Mexico World Cup opener" in handle_command(CommandRequest(command="team", args="mexico", **base), api).text
    assert "Sunny" in handle_command(CommandRequest(command="venue", args="mexico", **base), api).text
    assert "Mexico World Cup opener" in handle_command(CommandRequest(command="news", args="mexico", **base), api).text
    assert "Polymarket search" in handle_command(CommandRequest(command="odds", args="mexico south africa", **base), api).text
    assert "Alpha Signals" in handle_command(CommandRequest(command="signal", args="polymarket", **base), api).text
    assert "Wallet" in handle_command(
        CommandRequest(command="wallet", args="0x1234567890abcdef1234567890abcdef12345678", **base),
        api,
    ).text
    assert "PnL" in handle_command(
        CommandRequest(command="pnl", args="0x1234567890abcdef1234567890abcdef12345678", **base),
        api,
    ).text


def test_worldcup_match_and_odds_include_polymarket_trade_links():
    dashboard = FakeApi().worldcup_dashboard()
    dashboard["odds"] = [
        {
            "matchId": "wc2026-001",
            "homeTeam": "Mexico",
            "awayTeam": "South Africa",
            "marketTitle": "Mexico vs South Africa - FIFA World Cup 2026 match result",
            "marketUrl": "https://polymarket.com/event/mexico-vs-south-africa/match-result",
            "probabilities": [
                {"outcome": "Mexico", "price": "0.49"},
                {"outcome": "Draw", "price": "0.29"},
                {"outcome": "South Africa", "price": "0.22"},
            ],
        }
    ]
    markets = {"items": dashboard["odds"]}

    match_text = format_worldcup_match("mexico south africa", dashboard, FakeApi().worldcup_intel())
    odds_text = format_worldcup_odds("mexico south africa", dashboard, markets)
    links = worldcup_odds_action_links("mexico south africa", dashboard, markets)

    assert "Polymarket:" in match_text
    assert "Mexico 49.0%" in match_text
    assert "Trade: https://polymarket.com/event/mexico-vs-south-africa/match-result" in odds_text
    assert ("查看行情", "https://polymarket.com/event/mexico-vs-south-africa/match-result") in links
    assert ("快速下单", "https://polymarket.com/event/mexico-vs-south-africa/match-result") in links


def test_query_bot_adds_trade_buttons_from_market_linker(monkeypatch):
    import telegram.bot.formatters as formatters

    dashboard = FakeApi().worldcup_dashboard()

    def fake_resolve(item, *, title="", extra_text=()):
        text = " ".join(str(value or "") for value in (title, item.get("title"), item.get("marketTitle"), *extra_text)).lower()
        if "mexico" in text and "south africa" in text:
            return formatters.MarketLink(
                url="https://polymarket.com/event/fifwc-mex-rsa-2026-06-11",
                title="Mexico vs South Africa",
                matched_by="test",
                score=1.0,
            )
        if "opener" in text:
            return formatters.MarketLink(
                url="https://polymarket.com/event/world-cup-opener",
                title="World Cup opener",
                matched_by="test",
                score=1.0,
            )
        return None

    monkeypatch.setattr(formatters, "resolve_market_link", fake_resolve)

    matches_text = formatters.format_worldcup_matches(dashboard, "group a")
    match_links = worldcup_matches_action_links(dashboard, "group a")
    news_text = formatters.format_worldcup_news("mexico", FakeApi().worldcup_intel(), dashboard)

    assert "Market: https://polymarket.com/event/fifwc-mex-rsa-2026-06-11" in matches_text
    assert ("1 Trade", "https://polymarket.com/event/fifwc-mex-rsa-2026-06-11") in match_links
    assert "Market: https://polymarket.com/event/fifwc-mex-rsa-2026-06-11" in news_text


def test_market_and_signal_commands_return_trade_buttons(monkeypatch):
    import telegram.bot.formatters as formatters

    def fake_resolve(item, *, title="", extra_text=()):
        text = " ".join(str(value or "") for value in (title, item.get("title"), item.get("marketTitle"), *extra_text)).lower()
        if "bitcoin" in text:
            return formatters.MarketLink(
                url="https://polymarket.com/event/bitcoin-up-or-down",
                title="Bitcoin Up or Down",
                matched_by="test",
                score=1.0,
            )
        return None

    monkeypatch.setattr(formatters, "resolve_market_link", fake_resolve)
    base = {
        "update_id": 1,
        "chat_id": 1,
        "user_id": 2,
        "message_id": 3,
        "text": "",
        "raw": {},
    }

    signal_reply = handle_command(CommandRequest(command="signal", args="bitcoin", **base), FakeApi())

    assert "Market: https://polymarket.com/event/bitcoin-up-or-down" in signal_reply.text
    assert signal_reply.reply_markup
    assert "Trade Polymarket" in str(signal_reply.reply_markup)


def test_alert_commands_create_list_and_remove(tmp_path: Path):
    api = FakeApi()
    state = BotState(str(tmp_path / "bot_state.json"))
    base = {
        "update_id": 1,
        "chat_id": 1,
        "user_id": 2,
        "message_id": 3,
        "text": "",
        "raw": {},
    }

    created = handle_command(CommandRequest(command="alert", args="BTC 95000", **base), api, state=state)

    assert "Alert Created" in created.text
    assert "BTC" in created.text
    assert "95,000" in created.text
    assert state.active_alerts()[0]["direction"] == "above"

    listed = handle_command(CommandRequest(command="alerts", args="", **base), api, state=state)

    assert "Active Alerts" in listed.text
    assert "BTC >= 95,000" in listed.text

    removed = handle_command(CommandRequest(command="alert_remove", args="1", **base), api, state=state)

    assert "Alert Removed" in removed.text
    assert state.active_alerts() == []


def test_check_alerts_triggers_and_marks_once(tmp_path: Path):
    state = BotState(str(tmp_path / "bot_state.json"))
    state.add_alert(
        {
            "id": 1,
            "chatId": 123,
            "userId": 456,
            "symbol": "BTC",
            "threshold": 70000,
            "direction": "above",
            "enabled": True,
        }
    )
    state.save()
    telegram = FakeTelegram([])
    settings = make_settings(str(tmp_path / "bot_state.json"))

    sent = check_alerts(settings=settings, state=state, telegram=telegram, api=FakeApi(), dry_run=False)

    assert sent == 1
    assert "Alert Triggered" in telegram.sent[0]["text"]
    assert telegram.sent[0]["chat_id"] == "123"
    assert state.active_alerts() == []

    sent_again = check_alerts(settings=settings, state=state, telegram=telegram, api=FakeApi(), dry_run=False)

    assert sent_again == 0
    assert len(telegram.sent) == 1


def test_run_once_dry_run_processes_updates_without_persisting_offset(tmp_path: Path, capsys):
    update = {
        "update_id": 10,
        "message": {
            "message_id": 2,
            "text": "/market nba",
            "chat": {"id": 123},
            "from": {"id": 456},
        },
    }
    state = BotState(str(tmp_path / "bot_state.json"))
    telegram = FakeTelegram([update])
    settings = make_settings(str(tmp_path / "bot_state.json"))

    handled = run_once(settings=settings, state=state, telegram=telegram, api=FakeApi(), dry_run=True)

    output = capsys.readouterr().out
    assert handled == 1
    assert state.offset is None
    assert telegram.sent == []
    assert "Spurs vs. Thunder" in output


def test_run_once_sends_and_persists_offset(tmp_path: Path):
    update = {
        "update_id": 10,
        "message": {
            "message_id": 2,
            "text": "/market nba",
            "chat": {"id": 123},
            "from": {"id": 456},
        },
    }
    state = BotState(str(tmp_path / "bot_state.json"))
    telegram = FakeTelegram([update])
    settings = make_settings(str(tmp_path / "bot_state.json"))

    handled = run_once(settings=settings, state=state, telegram=telegram, api=FakeApi(), dry_run=False)

    assert handled == 1
    assert state.offset == 11
    assert "Spurs vs. Thunder" in telegram.sent[0]["text"]
    assert state.data["queryAnalytics"]["commands"]["market"] == 1
    assert state.data["queryAnalytics"]["queries"]["nba"] == 1
    assert state.data["users"]["456"]["chatId"] == "123"


def test_run_once_rejects_unauthorized_chat(tmp_path: Path):
    update = {
        "update_id": 10,
        "message": {
            "message_id": 2,
            "text": "/start",
            "chat": {"id": 123},
            "from": {"id": 456},
        },
    }
    state = BotState(str(tmp_path / "bot_state.json"))
    telegram = FakeTelegram([update])
    settings = BotSettings(
        **{**make_settings(str(tmp_path / "bot_state.json")).__dict__, "allowed_chat_ids": {"999"}}
    )

    handled = run_once(settings=settings, state=state, telegram=telegram, api=FakeApi(), dry_run=False)

    assert handled == 1
    assert "未授权" in telegram.sent[0]["text"]


def test_run_once_rate_limits_user(tmp_path: Path):
    updates = [
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "text": "/worldcup",
                "chat": {"id": 123},
                "from": {"id": 456},
            },
        }
        for update_id in (10, 11)
    ]
    state = BotState(str(tmp_path / "bot_state.json"))
    telegram = FakeTelegram(updates)
    settings = BotSettings(
        **{**make_settings(str(tmp_path / "bot_state.json")).__dict__, "rate_limit_per_minute": 1}
    )

    handled = run_once(settings=settings, state=state, telegram=telegram, api=FakeApi(), dry_run=False)

    assert handled == 2
    assert "FIFA World Cup 2026" in telegram.sent[0]["text"]
    assert "查询太频繁" in telegram.sent[1]["text"]


def test_polydata_api_falls_back_to_next_base_url():
    api = PolyDataBotApi(base_url="http://bad.local/wm-api", base_urls=("http://good.local/wm-api",), timeout_seconds=1)
    api.session = FakeApiSession()

    payload = api.search_markets("nba", limit=1)

    assert payload["items"][0]["title"] == "Fallback market"
    assert api.session.calls[0][0] == "http://bad.local/wm-api/markets"
    assert api.session.calls[1][0] == "http://good.local/wm-api/markets"


def test_worldcup_market_search_skips_local_search_by_default(monkeypatch):
    api = PolyDataBotApi(base_url="http://bad.local/wm-api", timeout_seconds=1)
    api.session = FakeApiSession()
    monkeypatch.setattr(
        api,
        "gamma_search_markets",
        lambda query, limit=8: {"items": [{"title": "Mexico vs South Africa - FIFA World Cup 2026 winner"}]},
    )

    payload = api.worldcup_market_search("mexico south africa", limit=1)

    assert payload["items"][0]["title"] == "Mexico vs South Africa - FIFA World Cup 2026 winner"
    assert api.session.calls == []


def test_worldcup_market_search_survives_enabled_local_market_timeout(monkeypatch):
    monkeypatch.setenv("POLYDATA_TELEGRAM_WORLDCUP_LOCAL_MARKET_SEARCH", "1")
    api = PolyDataBotApi(base_url="http://bad.local/wm-api", timeout_seconds=1)
    api.session = FakeApiSession()
    monkeypatch.setattr(
        api,
        "gamma_search_markets",
        lambda query, limit=8: {"items": [{"title": "Mexico vs South Africa - FIFA World Cup 2026 winner"}]},
    )

    payload = api.worldcup_market_search("mexico south africa", limit=2)

    assert payload["items"][0]["title"] == "Mexico vs South Africa - FIFA World Cup 2026 winner"
    assert api.session.calls[0][0] == "http://bad.local/wm-api/markets"


def test_worldcup_market_search_expands_chinese_query_and_skips_local_timeout(monkeypatch):
    api = PolyDataBotApi(base_url="http://bad.local/wm-api", timeout_seconds=1)
    api.session = FakeApiSession()
    gamma_queries = []

    def fake_gamma(query, limit=8):
        gamma_queries.append(query)
        return {"items": [{"title": "Mexico vs South Africa - FIFA World Cup 2026 winner", "slug": query}]}

    monkeypatch.setattr(api, "gamma_search_markets", fake_gamma)

    payload = api.worldcup_market_search("墨西哥 南非", limit=2)

    assert payload["items"][0]["title"] == "Mexico vs South Africa - FIFA World Cup 2026 winner"
    assert gamma_queries[0] == "Mexico South Africa"
    assert api.session.calls == []
