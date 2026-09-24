from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol

import requests

from .formatters import (
    crypto_price_map,
    format_alert_created,
    format_alert_removed,
    format_alerts,
    format_market_search,
    format_pnl_coverage,
    format_signals,
    format_wallet,
    format_worldcup_match,
    format_worldcup_matches,
    format_worldcup_news,
    format_worldcup_odds,
    format_worldcup_overview,
    format_worldcup_team,
    format_worldcup_venue,
    help_text,
    is_address,
    market_search_action_links,
    signal_action_links,
    start_text,
    worldcup_match_action_links,
    worldcup_matches_action_links,
    worldcup_news_action_links,
    worldcup_odds_action_links,
    worldcup_matches_page_info,
)
from .models import BotReply, CommandRequest
from .state import BotState


class BotApi(Protocol):
    def search_markets(self, query: str, *, limit: int = 5): ...
    def alpha_signals(self, *, limit: int = 5): ...
    def wallet_summary(self, address: str, *, days: int = 30): ...
    def wallet_trades(self, address: str, *, limit: int = 5): ...
    def pnl(self, address: str): ...
    def crypto_markets(self): ...
    def worldcup_dashboard(self): ...
    def worldcup_intel(self, *, limit: int = 24): ...
    def worldcup_market_search(self, query: str, *, limit: int = 8): ...


def _keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    keyboard = []
    for row in rows:
        buttons = []
        for label, target in row:
            if target.startswith("http"):
                buttons.append({"text": label, "url": target})
            else:
                buttons.append({"text": label, "callback_data": target})
        keyboard.append(buttons)
    return {"inline_keyboard": keyboard}


def _worldcup_keyboard() -> dict:
    return _keyboard(
        [
            [("Next Matches", "/matches"), ("News", "/news")],
            [("Weather", "/weather mexico"), ("Odds", "/odds mexico south africa")],
            [("Open Workspace", "https://www.polymonitor.club/?workspace=worldcup")],
        ]
    )


def _onboarding_keyboard() -> dict:
    return _keyboard(
        [
            [("World Cup", "/worldcup"), ("Matches", "/matches")],
            [("Odds", "/odds mexico south africa"), ("Help", "/help")],
        ]
    )


def _matches_keyboard(dashboard: dict, args: str) -> dict:
    info = worldcup_matches_page_info(dashboard, args)
    page = int(info.get("page") or 1)
    total_pages = int(info.get("totalPages") or 1)
    rows: list[list[tuple[str, str]]] = [
        [("Today", "/matches today"), ("Tomorrow", "/matches tomorrow")],
        [("Group A", "/matches group a"), ("Group B", "/matches group b"), ("Group C", "/matches group c")],
    ]
    pager: list[tuple[str, str]] = []
    if page > 1:
        pager.append(("Prev", f"/matches page {page - 1}"))
    if page < total_pages:
        pager.append(("Next", f"/matches page {page + 1}"))
    if pager:
        rows.append(pager)
    trade_links = worldcup_matches_action_links(dashboard, args)
    if trade_links:
        rows.append(trade_links[:3])
    rows.append([("Overview", "/worldcup"), ("Open Workspace", "https://www.polymonitor.club/?workspace=worldcup")])
    return _keyboard(rows)


def _market_search_keyboard(payload: dict) -> dict | None:
    links = market_search_action_links(payload)
    if not links:
        return None
    return _keyboard([links[:2]])


def _signal_keyboard(payload: dict) -> dict | None:
    links = signal_action_links(payload)
    if not links:
        return None
    return _keyboard([links[:2], [("Open Workspace", "https://www.polymonitor.club")]])


def _worldcup_match_keyboard(args: str, dashboard: dict, markets: dict | None = None) -> dict:
    links = worldcup_match_action_links(args, dashboard, markets)
    rows: list[list[tuple[str, str]]] = []
    if links:
        rows.append(links[:2])
    rows.extend(
        [
            [("Odds", f"/odds {args}"), ("Team", f"/team {args.split()[0]}")],
            [("Overview", "/worldcup"), ("Next Matches", "/matches")],
        ]
    )
    return _keyboard(rows)


def _worldcup_news_keyboard(args: str, intel: dict, dashboard: dict) -> dict | None:
    links = worldcup_news_action_links(args, intel, dashboard)
    rows: list[list[tuple[str, str]]] = []
    if links:
        rows.append(links[:2])
    if len(links) > 2:
        rows.append(links[2:3])
    rows.append([("Next Matches", "/matches"), ("Open Workspace", "https://www.polymonitor.club/?workspace=worldcup")])
    return _keyboard(rows)


def _worldcup_odds_keyboard(args: str, dashboard: dict, markets: dict | None = None) -> dict:
    links = worldcup_odds_action_links(args, dashboard, markets)
    rows: list[list[tuple[str, str]]] = []
    if links:
        rows.append(links[:2])
    if len(links) > 2:
        rows.append(links[2:4])
    rows.append([("Match", f"/match {args}"), ("Next Matches", "/matches")])
    return _keyboard(rows)


COMMAND_ALIASES = {
    "世界杯": "worldcup",
    "赛程": "matches",
    "比赛": "match",
    "球队": "team",
    "场馆": "venue",
    "天气": "weather",
    "新闻": "news",
    "赔率": "odds",
    "胜率": "odds",
    "帮助": "help",
}


def _usage(command: str) -> BotReply:
    usages = {
        "market": "请使用：/market nba 或 /market bitcoin",
        "wallet": "请使用：/wallet 0x...",
        "pnl": "请使用：/pnl 0x...",
        "signal": "请使用：/signal polymarket",
        "alert": "请使用：/alert BTC 95000",
        "match": "请使用：/match mexico south africa",
        "team": "请使用：/team mexico",
        "venue": "请使用：/venue dallas",
        "weather": "请使用：/weather dallas",
        "odds": "请使用：/odds mexico south africa",
    }
    return BotReply(f"⚠️ {command}\n{usages.get(command, '请使用 /help 查看命令')}")


def _service_error(label: str, detail: str = "") -> BotReply:
    hints = {
        "worldcup": "赛程缓存可能仍可用；如果连续失败，说明 World Cup runtime API 正在恢复。",
        "worldcup matches": "赛程缓存暂时读不到，稍后再试。",
        "worldcup weather": "天气 provider 可能为空，比赛/场馆数据通常不受影响。",
        "worldcup odds": "Polymarket 搜索或市场 linker 暂时超时，避免返回不确定胜率。",
    }
    lines = [f"⚠️ {label}", detail or "服务暂时不可用，稍后再试。"]
    if label in hints:
        lines.append(hints[label])
    return BotReply("\n".join(lines))


def _parse_alert_args(args: str) -> tuple[str, float] | None:
    parts = args.split()
    if len(parts) < 2:
        return None
    symbol = parts[0].strip().upper().replace("-USD", "")
    try:
        threshold = float(parts[1].replace(",", ""))
    except ValueError:
        return None
    if not symbol or threshold <= 0:
        return None
    return symbol, threshold


def handle_command(request: CommandRequest, api: BotApi, state: Optional[BotState] = None) -> BotReply:
    command = COMMAND_ALIASES.get(request.command, request.command)
    args = request.args.strip()
    if command == "start":
        if state is not None:
            state.record_user(chat_id=request.chat_id, user_id=request.user_id)
            state.save()
        if args.lower() == "worldcup":
            try:
                dashboard = api.worldcup_dashboard()
                intel = api.worldcup_intel(limit=24)
                return BotReply(format_worldcup_overview(dashboard, intel), link_preview=False, reply_markup=_worldcup_keyboard())
            except requests.RequestException:
                return _service_error("worldcup")
        if args.lower() == "odds":
            return BotReply("⚽ worldcup odds\n请选择一场比赛，或输入：/odds mexico south africa", reply_markup=_worldcup_keyboard())
        return BotReply(start_text(), link_preview=False, reply_markup=_onboarding_keyboard())
    if command == "help":
        return BotReply(help_text())
    if command == "market":
        if not args:
            return _usage("market")
        try:
            payload = api.search_markets(args, limit=5)
            return BotReply(
                format_market_search(args, payload),
                link_preview=False,
                reply_markup=_market_search_keyboard(payload),
            )
        except requests.RequestException:
            return _service_error("Market")
    if command == "worldcup":
        try:
            dashboard = api.worldcup_dashboard()
            intel = api.worldcup_intel(limit=24)
            return BotReply(format_worldcup_overview(dashboard, intel), link_preview=False, reply_markup=_worldcup_keyboard())
        except requests.RequestException:
            return _service_error("worldcup")
    if command == "matches":
        try:
            dashboard = api.worldcup_dashboard()
            return BotReply(
                format_worldcup_matches(dashboard, args),
                link_preview=False,
                reply_markup=_matches_keyboard(dashboard, args),
            )
        except requests.RequestException:
            return _service_error("worldcup matches")
    if command == "match":
        if not args:
            return _usage("match")
        try:
            dashboard = api.worldcup_dashboard()
            intel = api.worldcup_intel(limit=36)
            markets = api.worldcup_market_search(args, limit=5)
            return BotReply(
                format_worldcup_match(args, dashboard, intel),
                link_preview=False,
                reply_markup=_worldcup_match_keyboard(args, dashboard, markets),
            )
        except requests.RequestException:
            return _service_error("worldcup match")
    if command == "team":
        if not args:
            return _usage("team")
        try:
            dashboard = api.worldcup_dashboard()
            intel = api.worldcup_intel(limit=36)
            return BotReply(format_worldcup_team(args, dashboard, intel), link_preview=False)
        except requests.RequestException:
            return _service_error("worldcup team")
    if command in {"venue", "weather"}:
        if not args:
            return _usage(command)
        try:
            return BotReply(format_worldcup_venue(args, api.worldcup_dashboard()), link_preview=False)
        except requests.RequestException:
            return _service_error("worldcup weather")
    if command == "news":
        try:
            dashboard = api.worldcup_dashboard()
            intel = api.worldcup_intel(limit=36)
            return BotReply(
                format_worldcup_news(args, intel, dashboard),
                link_preview=True,
                reply_markup=_worldcup_news_keyboard(args, intel, dashboard),
            )
        except requests.RequestException:
            return _service_error("worldcup news")
    if command == "odds":
        if not args:
            return _usage("odds")
        try:
            dashboard = api.worldcup_dashboard()
            markets = api.worldcup_market_search(args, limit=8)
            return BotReply(
                format_worldcup_odds(args, dashboard, markets),
                link_preview=False,
                reply_markup=_worldcup_odds_keyboard(args, dashboard, markets),
            )
        except requests.RequestException:
            return _service_error("worldcup odds")
    if command == "signal":
        topic = args or "polymarket"
        try:
            payload = api.alpha_signals(limit=5)
            return BotReply(format_signals(topic, payload), link_preview=True, reply_markup=_signal_keyboard(payload))
        except requests.RequestException:
            return _service_error("Signal")
    if command == "wallet":
        if not args or not is_address(args.split()[0]):
            return _usage("wallet")
        address = args.split()[0].lower()
        try:
            summary = api.wallet_summary(address, days=30)
            trades = api.wallet_trades(address, limit=5)
        except requests.RequestException:
            return BotReply(
                "\n".join(
                    [
                        "⚠️ Wallet",
                        "地址服务暂时不可用。",
                        f"地址：{address[:6]}...{address[-4:]}",
                    ]
                )
            )
        return BotReply(format_wallet(address, summary, trades), link_preview=False)
    if command == "pnl":
        if not args or not is_address(args.split()[0]):
            return _usage("pnl")
        address = args.split()[0].lower()
        payload = {}
        try:
            payload = api.pnl(address)
        except requests.RequestException:
            payload = {}
        return BotReply(format_pnl_coverage(address, payload), link_preview=False)
    if command == "alert":
        if state is None:
            return BotReply("⚠️ Alert\n状态存储不可用。")
        parsed = _parse_alert_args(args)
        if parsed is None:
            return _usage("alert")
        symbol, threshold = parsed
        current_price = None
        try:
            current_price = crypto_price_map(api.crypto_markets()).get(symbol)
        except requests.RequestException:
            current_price = None
        direction = "above" if current_price is None or threshold >= current_price else "below"
        alert = {
            "id": state.next_alert_id(),
            "chatId": request.chat_id,
            "userId": request.user_id,
            "symbol": symbol,
            "threshold": threshold,
            "direction": direction,
            "createdPrice": current_price,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "enabled": True,
        }
        state.add_alert(alert)
        state.save()
        return BotReply(format_alert_created(alert))
    if command == "alerts":
        if state is None:
            return BotReply("⚠️ Alerts\n状态存储不可用。")
        return BotReply(format_alerts(state.alerts_for(chat_id=request.chat_id, user_id=request.user_id)))
    if command in {"alert_remove", "alertremove", "remove_alert"}:
        if state is None:
            return BotReply("⚠️ Alert\n状态存储不可用。")
        try:
            alert_id = int(args.split()[0])
        except (IndexError, ValueError):
            return BotReply("⚠️ Alert\n请使用：/alert_remove <id>")
        removed = state.remove_alert(alert_id=alert_id, chat_id=request.chat_id, user_id=request.user_id)
        state.save()
        return BotReply(format_alert_removed(alert_id, removed))
    return BotReply(f"⚠️ Unknown command: /{command}\n使用 /help 查看可用命令。")
