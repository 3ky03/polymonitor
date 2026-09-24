from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote_plus

from telegram.topics.market_linker import MarketLink, resolve_market_link


ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
WORLDCUP_WORKSPACE_URL = "https://www.polymonitor.club/?workspace=worldcup"
MATCHES_PAGE_SIZE = 5
TEAM_ALIASES = {
    "usa": "USA United States America 美国",
    "us": "USA United States America 美国",
    "united states": "USA United States America 美国",
    "美国": "USA United States America",
    "korea": "South Korea Korea 韩国",
    "south korea": "South Korea Korea 韩国",
    "韩国": "South Korea Korea",
    "mex": "Mexico 墨西哥",
    "mexico": "Mexico 墨西哥",
    "墨西哥": "Mexico",
    "south africa": "South Africa 南非",
    "南非": "South Africa",
    "czechia": "Czech Republic Czechia 捷克",
    "czech republic": "Czech Republic Czechia 捷克",
    "捷克": "Czech Republic Czechia",
    "canada": "Canada 加拿大",
    "加拿大": "Canada",
    "argentina": "Argentina 阿根廷",
    "阿根廷": "Argentina",
    "brazil": "Brazil 巴西",
    "巴西": "Brazil",
    "england": "England 英格兰",
    "英格兰": "England",
    "france": "France 法国",
    "法国": "France",
    "germany": "Germany 德国",
    "德国": "Germany",
    "spain": "Spain 西班牙",
    "西班牙": "Spain",
    "portugal": "Portugal 葡萄牙",
    "葡萄牙": "Portugal",
    "australia": "Australia 澳大利亚",
    "澳大利亚": "Australia",
    "belgium": "Belgium 比利时",
    "比利时": "Belgium",
    "bosnia": "Bosnia Herzegovina Bosnia & Herzegovina 波黑 波斯尼亚",
    "bosnia & herzegovina": "Bosnia Herzegovina Bosnia & Herzegovina 波黑 波斯尼亚",
    "波黑": "Bosnia Herzegovina Bosnia & Herzegovina",
    "波斯尼亚": "Bosnia Herzegovina Bosnia & Herzegovina",
    "colombia": "Colombia 哥伦比亚",
    "哥伦比亚": "Colombia",
    "croatia": "Croatia 克罗地亚",
    "克罗地亚": "Croatia",
    "denmark": "Denmark 丹麦",
    "丹麦": "Denmark",
    "ecuador": "Ecuador 厄瓜多尔",
    "厄瓜多尔": "Ecuador",
    "egypt": "Egypt 埃及",
    "埃及": "Egypt",
    "ghana": "Ghana 加纳",
    "加纳": "Ghana",
    "haiti": "Haiti 海地",
    "海地": "Haiti",
    "iran": "Iran 伊朗",
    "伊朗": "Iran",
    "italy": "Italy 意大利",
    "意大利": "Italy",
    "japan": "Japan 日本",
    "日本": "Japan",
    "morocco": "Morocco 摩洛哥",
    "摩洛哥": "Morocco",
    "netherlands": "Netherlands Holland 荷兰",
    "holland": "Netherlands Holland 荷兰",
    "荷兰": "Netherlands Holland",
    "new zealand": "New Zealand 新西兰",
    "新西兰": "New Zealand",
    "norway": "Norway 挪威",
    "挪威": "Norway",
    "paraguay": "Paraguay 巴拉圭",
    "巴拉圭": "Paraguay",
    "poland": "Poland 波兰",
    "波兰": "Poland",
    "qatar": "Qatar 卡塔尔",
    "卡塔尔": "Qatar",
    "saudi arabia": "Saudi Arabia 沙特 沙特阿拉伯",
    "沙特": "Saudi Arabia",
    "沙特阿拉伯": "Saudi Arabia",
    "senegal": "Senegal 塞内加尔",
    "塞内加尔": "Senegal",
    "serbia": "Serbia 塞尔维亚",
    "塞尔维亚": "Serbia",
    "switzerland": "Switzerland 瑞士",
    "瑞士": "Switzerland",
    "tunisia": "Tunisia 突尼斯",
    "突尼斯": "Tunisia",
    "uruguay": "Uruguay 乌拉圭",
    "乌拉圭": "Uruguay",
    "mexico city": "Mexico City mexico-city Estadio Azteca 墨西哥城 阿兹特克",
    "墨西哥城": "Mexico City mexico-city Estadio Azteca",
    "dallas": "Dallas Arlington AT&T Stadium 达拉斯 阿灵顿",
    "达拉斯": "Dallas Arlington AT&T Stadium",
    "los angeles": "Los Angeles Inglewood SoFi Stadium 洛杉矶",
    "洛杉矶": "Los Angeles Inglewood SoFi Stadium",
    "toronto": "Toronto BMO Field 多伦多",
    "多伦多": "Toronto BMO Field",
    "seattle": "Seattle Lumen Field 西雅图",
    "西雅图": "Seattle Lumen Field",
    "atlanta": "Atlanta Mercedes-Benz Stadium 亚特兰大",
    "亚特兰大": "Atlanta Mercedes-Benz Stadium",
    "vancouver": "Vancouver BC Place 温哥华",
    "温哥华": "Vancouver BC Place",
    "miami": "Miami Gardens Hard Rock Stadium 迈阿密",
    "迈阿密": "Miami Gardens Hard Rock Stadium",
    "boston": "Boston Foxborough Gillette Stadium 波士顿",
    "波士顿": "Boston Foxborough Gillette Stadium",
    "san francisco": "San Francisco Bay Area Levi's Stadium 旧金山",
    "旧金山": "San Francisco Bay Area Levi's Stadium",
    "houston": "Houston NRG Stadium 休斯顿",
    "休斯顿": "Houston NRG Stadium",
    "kansas city": "Kansas City Arrowhead Stadium 堪萨斯城",
    "堪萨斯城": "Kansas City Arrowhead Stadium",
    "philadelphia": "Philadelphia Lincoln Financial Field 费城",
    "费城": "Philadelphia Lincoln Financial Field",
    "monterrey": "Monterrey Guadalupe Estadio BBVA 蒙特雷",
    "蒙特雷": "Monterrey Guadalupe Estadio BBVA",
    "guadalajara": "Guadalajara Zapopan Estadio Akron 瓜达拉哈拉",
    "瓜达拉哈拉": "Guadalajara Zapopan Estadio Akron",
}


def is_address(value: str) -> bool:
    return bool(ADDRESS_RE.match(str(value or "").strip()))


def short_address(value: str) -> str:
    text = str(value or "").strip()
    if len(text) <= 14:
        return text
    return f"{text[:6]}...{text[-4:]}"


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _money(value: Any) -> str:
    number = _decimal(value)
    if number is None:
        return "n/a"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _pct(value: Any) -> str:
    number = _decimal(value)
    if number is None:
        return "n/a"
    if Decimal("0") <= number <= Decimal("1"):
        number *= Decimal("100")
    return f"{number:.1f}%"


def money(value: Any) -> str:
    return _money(value)


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _truncate(value: Any, limit: int = 120) -> str:
    text = _text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _tag(value: Any) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "").strip())
    if not text:
        return ""
    if text[0].isdigit():
        text = f"T{text}"
    return f"#{text[:32]}"


def _tags(values: Iterable[Any], *, limit: int = 4) -> str:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = [value]
        for raw in raw_items:
            tag = _tag(raw)
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                result.append(tag)
            if len(result) >= limit:
                return " ".join(result)
    return " ".join(result)


def _polymarket_url(item: Dict[str, Any]) -> str:
    for key in ("marketUrl", "eventUrl", "url"):
        value = _text(item.get(key))
        if value.startswith("https://polymarket.com") or value.startswith("http://polymarket.com"):
            return value
    event_slug = _text(item.get("eventSlug"))
    slug = _text(item.get("slug") or item.get("marketSlug") or item.get("eventSlug"))
    if event_slug and slug and event_slug != slug:
        return f"https://polymarket.com/event/{event_slug}/{slug}"
    if slug:
        return f"https://polymarket.com/event/{slug}"
    title = _text(item.get("title") or item.get("marketTitle") or item.get("question"))
    return f"https://polymarket.com/search?query={quote_plus(title)}" if title else ""


def _resolved_market_link(
    item: Dict[str, Any],
    *,
    title: str = "",
    extra_text: Iterable[Any] = (),
) -> Optional[MarketLink]:
    link = resolve_market_link(item, title=title, extra_text=extra_text)
    if link is not None:
        return link
    url = _polymarket_url(item)
    fallback_title = _text(title or item.get("title") or item.get("marketTitle") or item.get("question") or item.get("eventTitle"))
    if url and fallback_title:
        return MarketLink(url=url, title=fallback_title, matched_by="payload", score=1.0)
    return None


def _trade_links_from_items(items: Iterable[Dict[str, Any]], *, limit: int = 2) -> List[tuple[str, str]]:
    links: List[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        link = _resolved_market_link(
            item,
            title=_text(item.get("title") or item.get("marketTitle") or item.get("question") or item.get("eventTitle")),
            extra_text=(item.get("summary"), item.get("source"), item.get("category")),
        )
        if link is None or not link.url or link.url in seen:
            continue
        seen.add(link.url)
        label = "Trade Polymarket" if not links else "More Markets"
        links.append((label, link.url))
        if len(links) >= limit:
            break
    return links


def _parse_time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _beijing_time(value: Any) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return _text(value, "n/a")
    beijing = parsed.astimezone(timezone(timedelta(hours=8)))
    return beijing.strftime("%Y-%m-%d %H:%M Beijing")


def _items(payload: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    rows = payload.get(key) if isinstance(payload.get(key), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            import json
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _worldcup_match_label(match: Dict[str, Any]) -> str:
    home = _text(match.get("homeTeam"), "Home")
    away = _text(match.get("awayTeam"), "Away")
    return f"{home} vs {away}"


def _worldcup_match_text(match: Dict[str, Any]) -> str:
    parts = [
        _worldcup_match_label(match),
        _beijing_time(match.get("kickoffUtc")),
        _text(match.get("city")),
        _text(match.get("venue")),
    ]
    return " | ".join(part for part in parts if part)


def _worldcup_match_market_link(match: Dict[str, Any], dashboard: Dict[str, Any]) -> Optional[MarketLink]:
    odds_rows = _odds_for_match(match, dashboard, limit=1)
    if odds_rows:
        link = _resolved_market_link(
            odds_rows[0],
            title=_text(odds_rows[0].get("marketTitle") or odds_rows[0].get("title")),
            extra_text=(_worldcup_match_label(match), match.get("kickoffUtc"), "FIFA World Cup 2026"),
        )
        if link is not None:
            return link
    return _resolved_market_link(
        match,
        title=_worldcup_match_label(match),
        extra_text=(),
    )


def _query_terms(query: str) -> List[str]:
    expanded = str(query or "").lower()
    for alias, value in TEAM_ALIASES.items():
        if alias in expanded:
            expanded += " " + value.lower()
    return [part for part in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", expanded) if part]


def _match_score(query: str, item: Dict[str, Any], fields: Iterable[str]) -> int:
    terms = _query_terms(query)
    if not terms:
        return 0
    haystack = " ".join(_text(item.get(field)) for field in fields).lower()
    return sum(1 for term in terms if term in haystack)


def _find_matches(query: str, dashboard: Dict[str, Any], *, limit: int = 6) -> List[Dict[str, Any]]:
    matches = _items(dashboard, "matches")
    if not query.strip():
        return matches[:limit]
    scored = [
        (_match_score(query, match, ("homeTeam", "awayTeam", "city", "venue", "group", "round")), match)
        for match in matches
    ]
    return [match for score, match in sorted(scored, key=lambda row: row[0], reverse=True) if score > 0][:limit]


def _odds_for_match(match: Dict[str, Any], dashboard: Dict[str, Any], *, limit: int = 3) -> List[Dict[str, Any]]:
    match_id = _text(match.get("id"))
    home = _text(match.get("homeTeam")).lower()
    away = _text(match.get("awayTeam")).lower()
    rows = []
    for item in _items(dashboard, "odds"):
        if match_id and _text(item.get("matchId")) == match_id:
            rows.append(item)
            continue
        text = " ".join(_text(item.get(field)) for field in ("title", "marketTitle", "homeTeam", "awayTeam", "eventTitle", "slug", "eventSlug")).lower()
        if home and away and home in text and away in text:
            rows.append(item)
    return rows[:limit]


def _probability_pairs(item: Dict[str, Any], *, limit: int = 4) -> List[str]:
    probabilities = item.get("probabilities") if isinstance(item.get("probabilities"), list) else []
    pairs: List[str] = []
    for row in probabilities[:limit]:
        if isinstance(row, dict):
            pairs.append(f"{_truncate(row.get('outcome'), 24)} {_pct(row.get('price'))}")
    if pairs:
        return pairs
    outcomes = _json_list(item.get("outcomes"))
    outcome_prices = _json_list(item.get("outcomePrices"))
    for label, value in zip(outcomes[:limit], outcome_prices[:limit]):
        pairs.append(f"{_truncate(label, 24)} {_pct(value)}")
    return pairs


def _beijing_date(value: Any) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return ""
    return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _parse_matches_args(query: str) -> Dict[str, Any]:
    text = str(query or "").strip().lower()
    page = 1
    page_match = re.search(r"\bpage\s+(\d+)\b", text)
    if page_match:
        page = max(1, int(page_match.group(1)))
        text = re.sub(r"\bpage\s+\d+\b", "", text).strip()
    group = ""
    group_match = re.search(r"\bgroup\s+([a-l])\b", text)
    if group_match:
        group = f"Group {group_match.group(1).upper()}"
        text = re.sub(r"\bgroup\s+[a-l]\b", "", text).strip()
    date_filter = ""
    if "tomorrow" in text or "明天" in text:
        date_filter = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=1)).strftime("%Y-%m-%d")
        text = text.replace("tomorrow", "").replace("明天", "").strip()
    elif "today" in text or "今天" in text:
        date_filter = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        text = text.replace("today", "").replace("今天", "").strip()
    return {"query": text, "page": page, "group": group, "date": date_filter}


def worldcup_matches_page_info(dashboard: Dict[str, Any], query: str = "") -> Dict[str, Any]:
    parsed = _parse_matches_args(query)
    raw_matches = _items(dashboard, "matches")
    filtered = _filter_matches(raw_matches, group=parsed["group"], date_filter=parsed["date"])
    if parsed["query"]:
        filtered = _find_matches(parsed["query"], {**dashboard, "matches": filtered}, limit=200)
    total_pages = max(1, (len(filtered) + MATCHES_PAGE_SIZE - 1) // MATCHES_PAGE_SIZE)
    page = min(max(1, int(parsed["page"])), total_pages)
    return {**parsed, "page": page, "totalPages": total_pages, "totalMatches": len(filtered)}


def _filter_matches(matches: List[Dict[str, Any]], *, group: str = "", date_filter: str = "") -> List[Dict[str, Any]]:
    rows = matches
    if group:
        rows = [match for match in rows if _text(match.get("group")).lower() == group.lower()]
    if date_filter:
        rows = [match for match in rows if _beijing_date(match.get("kickoffUtc")) == date_filter]
    return rows


def _find_weather(query: str, dashboard: Dict[str, Any]) -> List[Dict[str, Any]]:
    weather = _items(dashboard, "weather")
    if not query.strip():
        return weather[:5]
    scored = [(_match_score(query, row, ("cityId", "city", "venue")), row) for row in weather]
    return [row for score, row in sorted(scored, key=lambda row: row[0], reverse=True) if score > 0][:5]


def _weather_rain(row: Dict[str, Any]) -> int:
    current = row.get("current") if isinstance(row.get("current"), dict) else {}
    forecast = row.get("forecast") if isinstance(row.get("forecast"), list) else []
    values = [_decimal(current.get("precipitationProbability")) or Decimal("0")]
    for day in forecast[:3]:
        if isinstance(day, dict):
            values.append(_decimal(day.get("precipitationProbability")) or Decimal("0"))
    return int(max(values or [Decimal("0")]))


def start_text() -> str:
    return "\n".join(
        [
            "PolyMonitorBot",
            "",
            "World Cup:",
            "/worldcup - 世界杯总览",
            "/matches - 最近比赛",
            "/matches today / tomorrow / group a / page 2",
            "/match mexico south africa - 查比赛时间/地点/情报",
            "/team mexico - 查球队新闻和赛程",
            "/venue dallas - 查场馆天气",
            "/news mexico - 查相关新闻",
            "/odds mexico south africa - 查 Polymarket 市场/胜率",
            "/比赛 墨西哥 南非 - 中文查询也支持",
            "",
            "可用命令：",
            "/market bitcoin - 搜索 Polymarket 市场",
            "/wallet 0x... - 查看地址交易画像",
            "/pnl 0x... - 查看地址 PnL 覆盖状态",
            "/signal polymarket - 查看最新 alpha signals",
            "/alert BTC 95000 - 创建价格提醒",
            "",
            f"Workspace: {WORLDCUP_WORKSPACE_URL}",
        ]
    )


def help_text() -> str:
    return "\n".join(
        [
            "PolyMonitorBot Help",
            "",
            "World Cup:",
            "  /worldcup",
            "  /matches",
            "  /matches today",
            "  /matches tomorrow",
            "  /matches group a",
            "  /matches page 2",
            "  /match mexico south africa",
            "  /team mexico",
            "  /venue dallas",
            "  /weather dallas",
            "  /news mexico",
            "  /odds mexico south africa",
            "  /比赛 墨西哥 南非",
            "  /赔率 墨西哥 南非",
            "",
            "Market:",
            "  /market nba",
            "  /market bitcoin",
            "",
            "Wallet:",
            "  /wallet 0x123...",
            "  /pnl 0x123...",
            "",
            "Signals:",
            "  /signal polymarket",
            "",
            "Alerts:",
            "  /alert BTC 95000",
            "  /alerts",
            "  /alert_remove 1",
        ]
    )


def format_market_search(query: str, payload: Dict[str, Any]) -> str:
    raw_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not raw_items and payload.get("title"):
        raw_items = [payload]
    if not raw_items:
        return f"⚠️ Market\n没有找到：{query}\n试试：/market bitcoin 或 /market nba"
    lines = [f"🔎 Market Search: {query}", ""]
    for index, item in enumerate(raw_items[:5], start=1):
        if not isinstance(item, dict):
            continue
        title = _truncate(item.get("title") or item.get("marketTitle") or item.get("question"), 100)
        price = item.get("latestPrice") or item.get("price") or item.get("probability")
        volume = item.get("volume24h") or item.get("volume")
        trades = item.get("tradeCount24h") or item.get("tradeCount")
        tags = _tags([item.get("tags") or [], item.get("category")])
        link = _resolved_market_link(item, title=title, extra_text=(query, item.get("eventTitle"), item.get("category")))
        url = link.url if link else ""
        lines.append(f"{index}. {title}")
        if price not in (None, ""):
            lines.append(f"YES: {_pct(price)}")
        detail_parts = []
        if volume not in (None, ""):
            detail_parts.append(f"Volume 24h: {_money(volume)}")
        if trades not in (None, ""):
            detail_parts.append(f"Trades 24h: {trades}")
        if detail_parts:
            lines.append(" | ".join(detail_parts))
        if tags:
            lines.append(tags)
        if url:
            lines.append(url)
        lines.append("")
    return "\n".join(lines).strip()


def market_search_action_links(payload: Dict[str, Any]) -> List[tuple[str, str]]:
    raw_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not raw_items and payload.get("title"):
        raw_items = [payload]
    return _trade_links_from_items([item for item in raw_items if isinstance(item, dict)], limit=2)


def _wallet_labels(summary: Dict[str, Any], daily: List[Dict[str, Any]]) -> List[str]:
    labels: List[str] = []
    trade_count = int(summary.get("tradeCount") or 0)
    active_markets = int(summary.get("activeMarkets") or 0)
    last_trade_at = _parse_time(summary.get("lastTradeAt"))
    first_trade_at = _parse_time(summary.get("firstTradeAt"))
    now = datetime.now(timezone.utc)
    if trade_count >= 100:
        labels.append("高频交易者")
    if active_markets >= 10:
        labels.append("多市场活跃")
    if last_trade_at and last_trade_at >= now - timedelta(days=7):
        labels.append("最近 7 天活跃")
    if trade_count <= 3 or (first_trade_at and first_trade_at >= now - timedelta(days=14)):
        labels.append("新地址")
    recent_trade_count = sum(int(row.get("tradeCount") or 0) for row in daily[-7:] if isinstance(row, dict))
    if recent_trade_count >= 50 and "高频交易者" not in labels:
        labels.append("高频交易者")
    return labels or ["已追踪地址"]


def format_wallet(address: str, summary_payload: Dict[str, Any], trades_payload: Dict[str, Any] | None = None) -> str:
    if summary_payload.get("error") or not summary_payload.get("summary"):
        return "\n".join(
            [
                "⚠️ Wallet",
                "地址服务暂时不可用，或该地址暂无本地统计。",
                f"地址：{short_address(address)}",
                "稍后再试，或先使用 /market 查询市场。",
            ]
        )
    summary = summary_payload.get("summary") if isinstance(summary_payload.get("summary"), dict) else {}
    daily = summary_payload.get("daily") if isinstance(summary_payload.get("daily"), list) else []
    top_markets = summary_payload.get("topMarkets") if isinstance(summary_payload.get("topMarkets"), list) else []
    recent_trades = (trades_payload or {}).get("items") if isinstance((trades_payload or {}).get("items"), list) else []
    lines = [
        "👛 Wallet",
        f"地址：{short_address(summary_payload.get('address') or address)}",
        f"总交易次数：{int(summary.get('tradeCount') or 0):,}",
        f"买入/卖出：{int(summary.get('buyCount') or 0):,} / {int(summary.get('sellCount') or 0):,}",
        f"交易量：{_money(summary.get('volumeNotional'))} USDC",
        f"活跃市场数：{int(summary.get('activeMarkets') or 0):,}",
    ]
    if summary.get("lastTradeAt"):
        lines.append(f"最近交易：{summary.get('lastTradeAt')}")
    if top_markets:
        lines.extend(["", "主要交易市场："])
        for index, market in enumerate(top_markets[:3], start=1):
            title = _truncate(market.get("title") or market.get("slug") or market.get("marketId"), 72)
            lines.append(f"{index}. {title}")
    if recent_trades:
        lines.extend(["", "最近交易："])
        for trade in recent_trades[:3]:
            title = _truncate(trade.get("marketTitle") or trade.get("market_title") or trade.get("marketId"), 56)
            side = _text(trade.get("side"))
            outcome = _text(trade.get("outcome"))
            price = _text(trade.get("price"))
            lines.append(f"- {side} {outcome} @ {price} | {title}")
    labels = _wallet_labels(summary, daily)
    lines.extend(["", "风险标签："])
    lines.extend(f"- {label}" for label in labels)
    return "\n".join(lines)


def format_pnl_coverage(address: str, payload: Dict[str, Any] | None = None) -> str:
    payload = payload or {}
    if payload.get("status") == "ok" and payload.get("tradingPnl") is not None:
        return "\n".join(
            [
                "📊 Wallet PnL",
                f"地址：{short_address(address)}",
                f"Trading PnL：{_money(payload.get('tradingPnl'))} USDC",
                f"Realized cash：{_money(payload.get('realizedCash'))} USDC",
                f"Unrealized value：{_money(payload.get('unrealizedValue'))} USDC",
            ]
        )
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    return "\n".join(
        [
            "📊 PnL",
            f"地址：{short_address(address)}",
            "",
            "当前状态：PnL 正在接入 cashflow 层",
            "暂不输出完整 PnL，避免用不完整数据误导。",
            "",
            "Data coverage:",
            f"- trade cashflows: {coverage.get('tradeCashflows', False)}",
            f"- non-trade cashflows: {coverage.get('nonTradeCashflows', False)}",
            f"- position snapshot: {coverage.get('positionSnapshot', False)}",
        ]
    )


def format_signals(topic: str, payload: Dict[str, Any]) -> str:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not items:
        return f"⚠️ Signal\n暂时没有找到信号：{topic}"
    lines = [f"🐳 Alpha Signals: {topic}", ""]
    for index, item in enumerate(items[:5], start=1):
        if not isinstance(item, dict):
            continue
        title = _truncate(item.get("title") or item.get("marketTitle") or item.get("summary"), 100)
        summary = _truncate(item.get("summary") or item.get("signal") or item.get("reason"), 150)
        tags = _tags([item.get("kind"), item.get("severity"), item.get("contributors") or [], item.get("sourceTag")])
        lines.append(f"{index}. {title}")
        if summary and summary != title:
            lines.append(summary)
        if tags:
            lines.append(tags)
        link = _resolved_market_link(item, title=title, extra_text=(summary, topic, item.get("sourceTag"), item.get("kind")))
        if link:
            lines.append(f"Market: {link.url}")
        lines.append("")
    return "\n".join(lines).strip()


def signal_action_links(payload: Dict[str, Any]) -> List[tuple[str, str]]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return _trade_links_from_items([item for item in items if isinstance(item, dict)], limit=2)


def format_worldcup_overview(dashboard: Dict[str, Any], intel: Dict[str, Any] | None = None) -> str:
    intel = intel or {}
    tournament = dashboard.get("tournament") if isinstance(dashboard.get("tournament"), dict) else {}
    matches = _items(dashboard, "matches")
    news = _items(intel, "news") or _items(dashboard, "news")
    signals = _items(intel, "signals")
    weather = _items(intel, "weather") or _items(dashboard, "weather")
    provider_states = intel.get("providerStates") if isinstance(intel.get("providerStates"), dict) else {}
    live_providers = sum(1 for value in provider_states.values() if str(value).lower() == "ok")
    lines = [
        "⚽ worldcup",
        _text(tournament.get("name"), "FIFA World Cup 2026"),
        f"Providers {live_providers} | Matches {len(matches)} | Signals {len(signals)} | News {len(news)} | Weather {len(weather)}",
        "",
        "Next matches:",
    ]
    for match in matches[:5]:
        lines.append(f"- {_worldcup_match_label(match)}")
        lines.append(f"  {_beijing_time(match.get('kickoffUtc'))}")
        lines.append(f"  {_text(match.get('city'))} · {_text(match.get('venue'))}")
        link = _worldcup_match_market_link(match, dashboard)
        if link:
            lines.append(f"  Market: {link.url}")
    lines.extend(["", f"Workspace: {WORLDCUP_WORKSPACE_URL}"])
    return "\n".join(lines).strip()


def format_worldcup_matches(dashboard: Dict[str, Any], query: str = "") -> str:
    parsed = _parse_matches_args(query)
    raw_matches = _items(dashboard, "matches")
    filtered = _filter_matches(raw_matches, group=parsed["group"], date_filter=parsed["date"])
    if parsed["query"]:
        filtered = _find_matches(parsed["query"], {**dashboard, "matches": filtered}, limit=200)
    page = int(parsed["page"])
    start = (page - 1) * MATCHES_PAGE_SIZE
    matches = filtered[start:start + MATCHES_PAGE_SIZE]
    if not matches:
        return f"⚠️ worldcup matches\n没有找到比赛：{query or 'next'}\n试试：/matches, /matches group a, /matches tomorrow, /match mexico"
    label_parts = [part for part in (parsed["query"], parsed["group"], parsed["date"], f"page {page}") if part]
    header = f"⚽ worldcup matches: {' | '.join(label_parts)}" if label_parts else "⚽ worldcup next matches"
    lines = [header, ""]
    for index, match in enumerate(matches, start=1):
        lines.append(f"{start + index}. {_worldcup_match_label(match)}")
        lines.append(f"   {_beijing_time(match.get('kickoffUtc'))}")
        lines.append(f"   {_text(match.get('city'))} · {_text(match.get('venue'))}")
        group = _text(match.get("group") or match.get("round") or match.get("stage"))
        if group:
            lines.append(f"   {group}")
        link = _worldcup_match_market_link(match, dashboard)
        if link:
            lines.append(f"   Market: {link.url}")
    total_pages = max(1, (len(filtered) + MATCHES_PAGE_SIZE - 1) // MATCHES_PAGE_SIZE)
    if total_pages > 1:
        lines.append("")
        lines.append(f"Page {page}/{total_pages} · /matches page {min(total_pages, page + 1)}")
    lines.extend(["", f"Workspace: {WORLDCUP_WORKSPACE_URL}"])
    return "\n".join(lines).strip()


def format_worldcup_match(query: str, dashboard: Dict[str, Any], intel: Dict[str, Any] | None = None) -> str:
    matches = _find_matches(query, dashboard, limit=1)
    if not matches:
        return f"⚠️ worldcup match\n没有找到比赛：{query}\n试试：/match mexico south africa"
    match = matches[0]
    intel = intel or {}
    label = _worldcup_match_label(match)
    terms = " ".join((_text(match.get("homeTeam")), _text(match.get("awayTeam")), query))
    signals = [
        item for item in _items(intel, "signals")
        if _match_score(terms, item, ("title", "summary", "source", "category")) > 0
    ][:4]
    news = [
        item for item in (_items(intel, "news") or _items(dashboard, "news"))
        if _match_score(terms, item, ("title", "summary", "source")) > 0
    ][:3]
    weather_rows = [row for row in _items(dashboard, "weather") if _text(row.get("cityId")) == _text(match.get("cityId"))]
    odds_rows = _odds_for_match(match, dashboard, limit=2)
    lines = [
        f"⚽ {label}",
        f"Kickoff: {_beijing_time(match.get('kickoffUtc'))}",
        f"Venue: {_text(match.get('venue'))}, {_text(match.get('city'))}",
        f"Group/Round: {_text(match.get('group') or match.get('round') or match.get('stage'), 'n/a')}",
    ]
    if weather_rows:
        current = weather_rows[0].get("current") if isinstance(weather_rows[0].get("current"), dict) else {}
        lines.append(f"Weather: {_text(current.get('condition'), 'n/a')} | {_text(current.get('tempC'), 'n/a')}C | rain {_weather_rain(weather_rows[0])}%")
    if signals:
        lines.extend(["", "Signals:"])
        for item in signals:
            lines.append(f"- {_truncate(item.get('title'), 120)}")
    if news:
        lines.extend(["", "News:"])
        for item in news:
            lines.append(f"- {_text(item.get('source'), 'news')} | {_truncate(item.get('title'), 120)}")
    if odds_rows:
        lines.extend(["", "Polymarket:"])
        for item in odds_rows:
            lines.append(f"- {_truncate(item.get('marketTitle') or item.get('title'), 120)}")
            pairs = _probability_pairs(item)
            if pairs:
                lines.append("  " + " | ".join(pairs))
            url = _polymarket_url(item)
            if url:
                lines.append(f"  Trade: {url}")
    else:
        link = _worldcup_match_market_link(match, dashboard)
        if link:
            lines.extend(["", "Polymarket:", f"- {_truncate(link.title, 120)}", f"  Trade: {link.url}"])
        else:
            linker = dashboard.get("marketLinker") if isinstance(dashboard.get("marketLinker"), dict) else {}
            if linker:
                lines.extend(["", f"Polymarket: no matched market yet · scanned {int(linker.get('candidates') or 0):,} candidates"])
    lines.extend(["", f"Odds: /odds {query}", f"Workspace: {WORLDCUP_WORKSPACE_URL}"])
    return "\n".join(lines).strip()


def format_worldcup_team(query: str, dashboard: Dict[str, Any], intel: Dict[str, Any] | None = None) -> str:
    matches = _find_matches(query, dashboard, limit=5)
    intel = intel or {}
    news = [
        item for item in (_items(intel, "news") or _items(dashboard, "news"))
        if _match_score(query, item, ("title", "summary", "source")) > 0
    ][:4]
    signals = [
        item for item in _items(intel, "signals")
        if _match_score(query, item, ("title", "summary", "source", "category")) > 0
    ][:4]
    if not matches and not news and not signals:
        return f"⚠️ worldcup team\n没有找到球队相关信息：{query}\n试试：/team mexico"
    lines = [f"⚽ team: {query}", ""]
    if matches:
        lines.append("Matches:")
        for match in matches:
            lines.append(f"- {_worldcup_match_text(match)}")
    if signals:
        lines.extend(["", "Signals:"])
        for item in signals:
            lines.append(f"- {_truncate(item.get('title'), 120)}")
    if news:
        lines.extend(["", "News:"])
        for item in news:
            lines.append(f"- {_text(item.get('source'), 'news')} | {_truncate(item.get('title'), 120)}")
    lines.extend(["", f"Workspace: {WORLDCUP_WORKSPACE_URL}"])
    return "\n".join(lines).strip()


def format_worldcup_venue(query: str, dashboard: Dict[str, Any]) -> str:
    rows = _find_weather(query, dashboard)
    if not rows:
        return f"⚠️ worldcup venue\n没有找到场馆/城市：{query}\n试试：/venue dallas 或 /weather atlanta"
    lines = [f"⚽ venue/weather: {query or 'top venues'}", ""]
    for row in rows[:5]:
        current = row.get("current") if isinstance(row.get("current"), dict) else {}
        city = _text(row.get("cityId"), "venue")
        lines.append(f"- {city}: {_text(current.get('condition'), 'n/a')} | {_text(current.get('tempC'), 'n/a')}C | wind {_text(current.get('windKph'), 'n/a')} kph | rain {_weather_rain(row)}%")
    lines.extend(["", f"Workspace: {WORLDCUP_WORKSPACE_URL}"])
    return "\n".join(lines).strip()


def format_worldcup_news(query: str, intel: Dict[str, Any], dashboard: Dict[str, Any] | None = None) -> str:
    rows = _items(intel, "news") or _items(dashboard or {}, "news")
    if query.strip():
        rows = [row for row in rows if _match_score(query, row, ("title", "summary", "source")) > 0]
    if not rows:
        return f"⚠️ worldcup news\n没有找到相关新闻：{query or 'latest'}"
    lines = [f"⚽ worldcup news: {query or 'latest'}", ""]
    for index, item in enumerate(rows[:5], start=1):
        lines.append(f"{index}. {_text(item.get('source'), 'news')} | {_truncate(item.get('title'), 130)}")
        summary = _truncate(item.get("summary"), 160)
        if summary:
            lines.append(summary)
        url = _text(item.get("url"))
        if url.startswith("http"):
            lines.append(url)
        link = _resolved_market_link(item, title=_text(item.get("title")), extra_text=(item.get("summary"), item.get("source"), query, "FIFA World Cup 2026"))
        if link:
            lines.append(f"Market: {link.url}")
        lines.append("")
    return "\n".join(lines).strip()


def worldcup_news_action_links(query: str, intel: Dict[str, Any], dashboard: Dict[str, Any] | None = None) -> List[tuple[str, str]]:
    rows = _items(intel, "news") or _items(dashboard or {}, "news")
    if query.strip():
        rows = [row for row in rows if _match_score(query, row, ("title", "summary", "source")) > 0]
    links = _trade_links_from_items(rows[:5], limit=2)
    source_url = ""
    for row in rows[:5]:
        url = _text(row.get("url"))
        if url.startswith("http"):
            source_url = url
            break
    if source_url:
        links.append(("Source", source_url))
    return links[:3]


def format_worldcup_odds(query: str, dashboard: Dict[str, Any], market_payload: Dict[str, Any]) -> str:
    matches = _find_matches(query, dashboard, limit=1)
    odds = _items(dashboard, "odds")
    relevant_odds = []
    if matches:
        relevant_odds = _odds_for_match(matches[0], dashboard, limit=3)
    if not relevant_odds:
        relevant_odds = [row for row in odds if _match_score(query, row, ("title", "marketTitle", "homeTeam", "awayTeam", "eventTitle")) > 0][:3]
    lines = [f"⚽ worldcup odds: {query}", ""]
    market_query = query
    if matches:
        match = matches[0]
        market_query = f"{_text(match.get('homeTeam'))} {_text(match.get('awayTeam'))} world cup"
        lines.append(_worldcup_match_text(match))
        lines.append("")
    if relevant_odds:
        lines.append("Matched odds:")
        for item in relevant_odds:
            lines.append(f"- {_truncate(item.get('title') or item.get('marketTitle'), 120)}")
            pairs = _probability_pairs(item)
            if pairs:
                lines.append("  " + " | ".join(pairs))
            url = _polymarket_url(item)
            if url:
                lines.append(f"  Trade: {url}")
        lines.append("")
    else:
        lines.append("当前 World Cup dashboard 暂未直接匹配到该场 Polymarket 胜率。")
        linker = dashboard.get("marketLinker") if isinstance(dashboard.get("marketLinker"), dict) else {}
        if linker:
            lines.append(f"Market linker: scanned {int(linker.get('candidates') or 0):,}, matched {int(linker.get('matched') or 0):,}")
        lines.append("")
    raw_markets = market_payload.get("items") if isinstance(market_payload.get("items"), list) else []
    markets = [
        item for item in raw_markets
        if isinstance(item, dict) and _match_score(market_query, item, ("title", "marketTitle", "question", "eventTitle", "slug")) >= 2
    ]
    if markets:
        lines.append("Polymarket search:")
        for index, item in enumerate(markets[:3], start=1):
            title = _truncate(item.get("title") or item.get("marketTitle") or item.get("question"), 120)
            price = item.get("latestPrice") or item.get("price") or item.get("probability")
            url = _polymarket_url(item)
            lines.append(f"{index}. {title}")
            if price not in (None, ""):
                lines.append(f"   YES: {_pct(price)}")
            outcomes = item.get("outcomes")
            outcome_prices = item.get("outcomePrices")
            pairs = _probability_pairs({**item, "outcomes": outcomes, "outcomePrices": outcome_prices})
            if pairs:
                lines.append("   " + " | ".join(pairs))
            if url:
                lines.append(f"   Trade: {url}")
    else:
        lines.append("Polymarket search: no listed match market found yet.")
    lines.extend(["", f"Workspace: {WORLDCUP_WORKSPACE_URL}"])
    return "\n".join(lines).strip()


def worldcup_odds_action_links(query: str, dashboard: Dict[str, Any], market_payload: Dict[str, Any] | None = None) -> List[tuple[str, str]]:
    matches = _find_matches(query, dashboard, limit=1)
    rows: List[Dict[str, Any]] = []
    if matches:
        rows.extend(_odds_for_match(matches[0], dashboard, limit=2))
    if market_payload:
        raw_markets = market_payload.get("items") if isinstance(market_payload.get("items"), list) else []
        for item in raw_markets:
            if isinstance(item, dict):
                rows.append(item)
    links: List[tuple[str, str]] = []
    seen: set[str] = set()
    first_url = ""
    for item in rows:
        url = _polymarket_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        if not first_url:
            first_url = url
        label = "查看行情" if not links else "更多行情"
        links.append((label, url))
        if len(links) >= 2:
            break
    if not links:
        first_url = f"https://polymarket.com/search?query={quote_plus(query)}"
        links.append(("Search Polymarket", first_url))
    if first_url:
        links.insert(1, ("快速下单", first_url))
    links.append(("Open Workspace", WORLDCUP_WORKSPACE_URL))
    return links


def worldcup_match_action_links(query: str, dashboard: Dict[str, Any], market_payload: Dict[str, Any] | None = None) -> List[tuple[str, str]]:
    matches = _find_matches(query, dashboard, limit=1)
    rows: List[Dict[str, Any]] = []
    if matches:
        odds_rows = _odds_for_match(matches[0], dashboard, limit=2)
        rows.extend(odds_rows)
        match_link = _worldcup_match_market_link(matches[0], dashboard)
        if match_link:
            rows.append({"title": match_link.title, "url": match_link.url})
    if market_payload:
        raw_markets = market_payload.get("items") if isinstance(market_payload.get("items"), list) else []
        rows.extend(item for item in raw_markets if isinstance(item, dict))
    links = _trade_links_from_items(rows, limit=2)
    if not links and query:
        links.append(("Search Polymarket", f"https://polymarket.com/search?query={quote_plus(query)}"))
    links.append(("Open Workspace", WORLDCUP_WORKSPACE_URL))
    return links


def worldcup_matches_action_links(dashboard: Dict[str, Any], query: str = "") -> List[tuple[str, str]]:
    parsed = _parse_matches_args(query)
    raw_matches = _items(dashboard, "matches")
    filtered = _filter_matches(raw_matches, group=parsed["group"], date_filter=parsed["date"])
    if parsed["query"]:
        filtered = _find_matches(parsed["query"], {**dashboard, "matches": filtered}, limit=200)
    page_info = worldcup_matches_page_info(dashboard, query)
    page = int(page_info.get("page") or 1)
    start = (page - 1) * MATCHES_PAGE_SIZE
    links: List[tuple[str, str]] = []
    seen: set[str] = set()
    for index, match in enumerate(filtered[start:start + MATCHES_PAGE_SIZE], start=1):
        link = _worldcup_match_market_link(match, dashboard)
        if link is None or not link.url or link.url in seen:
            continue
        seen.add(link.url)
        links.append((f"{start + index} Trade", link.url))
        if len(links) >= 3:
            break
    return links


def crypto_price_map(payload: Dict[str, Any]) -> Dict[str, float]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    prices: Dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        label = _text(item.get("label") or item.get("id") or item.get("symbol")).upper()
        symbol = _text(item.get("symbol")).upper().replace("-USD", "")
        price = _decimal(item.get("price"))
        if price is None:
            continue
        for key in (label, symbol):
            if key:
                prices[key] = float(price)
    return prices


def format_alert_created(alert: Dict[str, Any]) -> str:
    direction_text = "高于或等于" if alert.get("direction") == "above" else "低于或等于"
    current = alert.get("createdPrice")
    current_line = f"当前价格：{_money(current)}" if current not in (None, "") else "当前价格：暂不可用"
    return "\n".join(
        [
            "🔔 Alert Created",
            f"ID：{alert.get('id')}",
            f"标的：{alert.get('symbol')}",
            f"条件：价格{direction_text} {_money(alert.get('threshold'))}",
            current_line,
            "",
            "查看：/alerts",
            f"删除：/alert_remove {alert.get('id')}",
        ]
    )


def format_alerts(alerts: list[Dict[str, Any]]) -> str:
    if not alerts:
        return "🔔 Alerts\n当前没有活跃提醒。\n创建示例：/alert BTC 95000"
    lines = ["🔔 Active Alerts", ""]
    for alert in alerts[:20]:
        direction_text = ">=" if alert.get("direction") == "above" else "<="
        lines.append(f"{alert.get('id')}. {alert.get('symbol')} {direction_text} {_money(alert.get('threshold'))}")
    lines.extend(["", "删除：/alert_remove <id>"])
    return "\n".join(lines)


def format_alert_removed(alert_id: int, removed: bool) -> str:
    if removed:
        return f"🔕 Alert Removed\n已删除提醒：{alert_id}"
    return f"⚠️ Alert\n没有找到可删除的提醒：{alert_id}"


def format_alert_triggered(alert: Dict[str, Any], price: float) -> str:
    direction_text = "突破" if alert.get("direction") == "above" else "跌破"
    return "\n".join(
        [
            "🚨 Alert Triggered",
            f"{alert.get('symbol')} 已{direction_text} {_money(alert.get('threshold'))}",
            f"当前价格：{_money(price)}",
            f"Alert ID：{alert.get('id')}",
        ]
    )
