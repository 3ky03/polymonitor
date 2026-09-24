from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote_plus

from .catalog import DeliveryMode, delivery_contract
from .generic_formatter import format_generic_snapshot
from .market_linker import MarketLink, resolve_market_link
from .models import FormatOutcome, MessageCandidate

RELATED_NEWS_GROUP_LIMIT = 2
WORLDCUP_WORKSPACE_URL = "https://www.polymonitor.club/?workspace=worldcup"
QUERY_BOT_URL = "https://t.me/PolyMonitorQuery_bot"
NEW_MARKET_PLACEHOLDER_TITLE_PREFIXES = (
    "On-chain recovered market ",
    "Trade indexer placeholder market ",
)


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    if 0 <= number <= 1:
        number *= 100
    return f"{number:.0f}%"


def _price(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.0f}c" if 0 <= number <= 1 else f"{number:.2f}"


def _short_hash(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _first_url(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text.startswith("http://") or text.startswith("https://"):
            return text
    return ""


def _clean_tag(value: Any) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(value or "").strip())
    if not text:
        return ""
    if text[0].isdigit():
        text = f"T{text}"
    return f"#{text[:32]}"


def _hashtags(*values: Any, limit: int = 5) -> str:
    tags: List[str] = []
    seen: set[str] = set()
    for value in values:
        raw_values = value if isinstance(value, list) else [value]
        for raw in raw_values:
            tag = _clean_tag(raw)
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                tags.append(tag)
            if len(tags) >= limit:
                return " ".join(tags)
    return " ".join(tags)


def _polymarket_url(item: Dict[str, Any], *, title: str = "") -> str:
    direct = _first_url(item.get("marketUrl"), item.get("eventUrl"), item.get("url"))
    if direct and "polymarket.com" in direct:
        return direct
    slug = _text(item.get("slug") or item.get("marketSlug") or item.get("eventSlug"))
    if slug:
        if str(item.get("eventSlug") or "").strip() == slug or item.get("eventId"):
            return f"https://polymarket.com/event/{slug}"
        return f"https://polymarket.com/market/{slug}"
    query = _text(item.get("marketTitle") or item.get("question") or item.get("eventTitle")) or title or _text(item.get("title"))
    return f"https://polymarket.com/search?query={quote_plus(query)}" if query else ""


def _market_identity(item: Dict[str, Any], *, title: str = "") -> str:
    return _text(
        item.get("marketId")
        or item.get("eventId")
        or item.get("id")
        or item.get("slug")
        or item.get("marketSlug")
        or item.get("eventSlug")
        or item.get("marketTitle")
        or item.get("question")
        or title
    ).lower()


def _source_link(url: str, label: str = "Open link") -> str:
    return f"{label}: {url}" if url else ""


def _trade_markup(market_link: Optional[MarketLink], *, extra_buttons: List[Dict[str, str]] | None = None) -> Dict[str, Any] | None:
    buttons: List[Dict[str, str]] = []
    if market_link and market_link.url:
        buttons.append({"text": "Trade Polymarket", "url": market_link.url})
    buttons.extend(extra_buttons or [])
    return {"inline_keyboard": [buttons]} if buttons else None


def _compose_post(*, header: str, title: str, lines: Iterable[str] = (), tags: str = "", meme: str = "", url: str = "") -> str:
    parts = [header, title.strip()]
    parts.extend(line.strip() for line in lines if str(line or "").strip())
    if meme:
        parts.append(meme)
    if tags:
        parts.append(tags)
    if url:
        parts.append(url)
    return "\n".join(part for part in parts if part)


def _iter_items(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for item in payload.get("items") or []:
        if isinstance(item, dict):
            yield item


def format_nba_scoreboard(payload: Dict[str, Any]) -> List[MessageCandidate]:
    messages: List[MessageCandidate] = []
    for game in _iter_items(payload):
        game_id = _text(game.get("id") or game.get("eventId") or game.get("name"))
        away = _text(game.get("awayTeam"), "Away")
        home = _text(game.get("homeTeam"), "Home")
        away_score = _text(game.get("awayScore"), "-")
        home_score = _text(game.get("homeScore"), "-")
        status = _text(game.get("status") or game.get("state"), "scheduled")
        tipoff = _text(game.get("tipoff"))
        broadcast = _text(game.get("broadcast"))
        score_line = f"{away} {away_score} @ {home} {home_score}" if away_score != "-" or home_score != "-" else f"{away} @ {home}"
        details = " | ".join(part for part in (status, tipoff, broadcast) if part)
        market_link = resolve_market_link(game, title=f"{away} vs {home}", extra_text=(details,))
        tags = _hashtags("NBA", away, home, status)
        meme = "🏀 Vibe: tipoff radar is awake" if "scheduled" in status.lower() else "🏀 Vibe: scoreboard heat"
        text = _compose_post(
            header="🏀 NBA Scoreboard",
            title=score_line,
            lines=[details],
            tags=tags,
            meme=meme,
            url=_source_link(market_link.url, "Market") if market_link else "",
        )
        dedupe = _short_hash("nba-scoreboard", game_id, away_score, home_score, status)
        priority = "high" if str(game.get("state") or "").lower() in {"post", "final"} or "final" in status.lower() else "normal"
        messages.append(
            MessageCandidate(
                topic="nba",
                dedupe_key=dedupe,
                text=text,
                priority=priority,
                metadata={"panel": "nba-scoreboard", "marketLink": market_link.url if market_link else ""},
                link_preview=bool(market_link),
                reply_markup=_trade_markup(market_link),
            )
        )
    return messages


def format_nba_intel(payload: Dict[str, Any]) -> List[MessageCandidate]:
    messages: List[MessageCandidate] = []
    for item in _iter_items(payload):
        headline = _text(item.get("headline") or item.get("title"))
        if not headline:
            continue
        source = _text(item.get("source"), "NBA intel")
        description = _text(item.get("description"))
        url = _first_url(item.get("url"))
        market_link = resolve_market_link(item, title=headline, extra_text=(description, source, "NBA"))
        tags = _hashtags("NBA", source, "Intel")
        text = _compose_post(
            header="📰 NBA Intel",
            title=headline,
            lines=[source, description[:260] if description else ""],
            tags=tags,
            meme="🏀 Vibe: locker-room signal",
            url="\n".join(part for part in (_source_link(market_link.url, "Market") if market_link else "", _source_link(url, "Source")) if part),
        )
        dedupe = _short_hash("nba-intel", headline.lower(), url)
        messages.append(
            MessageCandidate(
                topic="nba",
                dedupe_key=dedupe,
                text=text,
                priority="normal",
                metadata={"panel": "nba-intel", "marketLink": market_link.url if market_link else ""},
                link_preview=bool(market_link or url),
                reply_markup=_trade_markup(market_link, extra_buttons=[{"text": "Source", "url": url}] if url else None),
            )
        )

    for lineup in payload.get("lineups") or []:
        if not isinstance(lineup, dict):
            continue
        label = _text(lineup.get("label"))
        status = _text(lineup.get("status"))
        starters = lineup.get("starters") if isinstance(lineup.get("starters"), list) else []
        if not label or not starters:
            continue
        starter_names = ", ".join(_text(player.get("playerName")) for player in starters[:6] if isinstance(player, dict) and _text(player.get("playerName")))
        market_link = resolve_market_link(lineup, title=label, extra_text=(starter_names, "NBA lineup"))
        text = _compose_post(
            header="🧾 NBA Lineup",
            title=label,
            lines=[status, starter_names],
            tags=_hashtags("NBA", "Lineup"),
            meme="👀 Vibe: rotation watch",
            url=_source_link(market_link.url, "Market") if market_link else "",
        )
        dedupe = _short_hash("nba-lineup", lineup.get("gameId"), status, starter_names)
        messages.append(
            MessageCandidate(
                topic="nba",
                dedupe_key=dedupe,
                text=text,
                priority="normal",
                metadata={"panel": "nba-intel", "marketLink": market_link.url if market_link else ""},
                link_preview=bool(market_link),
                reply_markup=_trade_markup(market_link),
            )
        )
    return messages


def format_nba_predictor(payload: Dict[str, Any]) -> List[MessageCandidate]:
    messages: List[MessageCandidate] = []
    for item in _iter_items(payload):
        event_id = _text(item.get("eventId") or item.get("id") or item.get("shortName"))
        away = _text(item.get("awayTeam"), "Away")
        home = _text(item.get("homeTeam"), "Home")
        away_prob = _number(item.get("awayWinProbability"))
        home_prob = _number(item.get("homeWinProbability"))
        quality = _number(item.get("matchupQuality"))
        margin = _number(item.get("projectedMargin"))
        if away_prob is None and home_prob is None and quality is None:
            continue
        line = f"{away} {_pct(away_prob)} | {home} {_pct(home_prob)}"
        extras = []
        if quality is not None:
            extras.append(f"quality {quality:.0f}")
        if margin is not None:
            extras.append(f"margin {margin:+.1f}")
        status = _text(item.get("status") or item.get("state"))
        if status:
            extras.append(status)
        market_link = resolve_market_link(item, title=f"{away} vs {home}", extra_text=(status, "NBA matchup predictor"))
        text = _compose_post(
            header="🔮 NBA Matchup Predictor",
            title=f"{away} @ {home}",
            lines=[line, " | ".join(extras)],
            tags=_hashtags("NBA", away, home, "Predictor"),
            meme="🧠 Vibe: probability board says hello",
            url=_source_link(market_link.url, "Market") if market_link else "",
        )
        dedupe = _short_hash("nba-predictor", event_id, round(away_prob or -1), round(home_prob or -1), status)
        messages.append(
            MessageCandidate(
                topic="nba",
                dedupe_key=dedupe,
                text=text,
                priority="normal",
                metadata={"panel": "espn-matchup-predictor", "marketLink": market_link.url if market_link else ""},
                link_preview=bool(market_link),
                reply_markup=_trade_markup(market_link),
            )
        )
    return messages


def _iter_list(payload: Dict[str, Any], key: str) -> Iterable[Dict[str, Any]]:
    for item in payload.get(key) or []:
        if isinstance(item, dict):
            yield item


def _weather_risk(row: Dict[str, Any]) -> int:
    current = row.get("current") if isinstance(row.get("current"), dict) else {}
    forecast = row.get("forecast") if isinstance(row.get("forecast"), list) else []
    probs = [_number(current.get("precipitationProbability")) or 0]
    for day in forecast[:3]:
        if isinstance(day, dict):
            probs.append(_number(day.get("precipitationProbability")) or 0)
    return int(max(probs or [0]))


def format_worldcup_intel(payload: Dict[str, Any]) -> List[MessageCandidate]:
    signals = list(_iter_list(payload, "signals"))
    news = list(_iter_list(payload, "news"))
    weather = list(_iter_list(payload, "weather"))
    if not signals and not news and not weather:
        return []

    lines: List[str] = []
    summary = _text(payload.get("summary"))
    if summary:
        lines.append(summary[:240])
    lines.append(f"Signals {len(signals)} | News {len(news)} | Weather {len(weather)}")

    signal_lines = []
    for item in signals[:3]:
        title = _text(item.get("title"))
        source = _text(item.get("source") or item.get("provider"), "signal")
        if title:
            signal_lines.append(f"- {source} | {title[:130]}")
    if signal_lines:
        lines.append("Signals:")
        lines.extend(signal_lines)

    news_lines = []
    for item in news[:3]:
        title = _text(item.get("title"))
        source = _text(item.get("source"), "news")
        if title:
            news_lines.append(f"- {source} | {title[:130]}")
    if news_lines:
        lines.append("News:")
        lines.extend(news_lines)

    weather_lines = []
    for item in sorted(weather, key=_weather_risk, reverse=True)[:2]:
        city = _text(item.get("cityId"), "venue")
        current = item.get("current") if isinstance(item.get("current"), dict) else {}
        temp = _text(current.get("tempC"))
        condition = _text(current.get("condition"))
        rain = _weather_risk(item)
        detail = " | ".join(part for part in (condition, f"{temp}C" if temp else "", f"rain {rain}%") if part)
        weather_lines.append(f"- {city}: {detail}" if detail else f"- {city}")
    if weather_lines:
        lines.append("Weather:")
        lines.extend(weather_lines)

    first_url = _first_url(
        *(item.get("url") for item in signals[:3]),
        *(item.get("url") for item in news[:3]),
        payload.get("sourceUrl"),
    )
    text = _compose_post(
        header="⚽ worldcup",
        title="FIFA World Cup 2026 workspace",
        lines=lines,
        tags=_hashtags("WorldCup", "Polymonitor", "Football"),
        url=_source_link(WORLDCUP_WORKSPACE_URL, "Workspace"),
    )
    version = "|".join(lines)
    dedupe = _short_hash("worldcup-intel", version)
    return [
        MessageCandidate(
            topic="worldcup",
            dedupe_key=dedupe,
            text=text,
            priority="normal",
            metadata={"panel": "worldcup-intel"},
            link_preview=bool(first_url or WORLDCUP_WORKSPACE_URL),
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Ask bot", "url": QUERY_BOT_URL}, {"text": "Matches", "url": f"{QUERY_BOT_URL}?start=worldcup"}],
                    [{"text": "Odds", "url": f"{QUERY_BOT_URL}?start=odds"}, {"text": "Open Workspace", "url": WORLDCUP_WORKSPACE_URL}],
                ]
            },
        )
    ]


def format_weather_map(payload: Dict[str, Any]) -> List[MessageCandidate]:
    messages: List[MessageCandidate] = []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    hottest = summary.get("hottestCity") if isinstance(summary.get("hottestCity"), dict) else None
    if hottest:
        city = _text(hottest.get("city"), "Unknown")
        current = _text(hottest.get("currentTemp"), "n/a")
        high = _text(hottest.get("forecastHigh"), "n/a")
        live_count = _text(summary.get("liveMarketCount"), "0")
        dedupe = _short_hash("weather-summary", city, current, high, live_count)
        text = _compose_post(
            header="🌦 Weather Monitor",
            title=f"Hottest mapped city: {city}",
            lines=[f"Current {current} | forecast high {high}", f"Live weather markets: {live_count}"],
            tags=_hashtags("Weather", city, "Heat"),
            meme="🥵 Vibe: thermometer doing cardio",
        )
        messages.append(MessageCandidate(topic="weather", dedupe_key=dedupe, text=text, priority="normal", metadata={"panel": "global-weather-map"}))

    for item in _iter_items(payload):
        top_bin = item.get("topBin") if isinstance(item.get("topBin"), dict) else {}
        market_url = _first_url(item.get("marketUrl"))
        if not top_bin or not market_url:
            continue
        city = _text(item.get("city"), "Unknown")
        label = _text(top_bin.get("label") or top_bin.get("title") or top_bin.get("raw"))
        market_link = resolve_market_link(item, title=f"{city}: {label}", extra_text=(market_url, "weather"))
        trade_url = market_link.url if market_link else market_url
        quote = _price(top_bin.get("midPriceYes"))
        coverage = _text(item.get("quoteCoverage"))
        temp = _text(item.get("currentTemp"), "n/a")
        dedupe = _short_hash("weather-market", item.get("cityId"), item.get("eventSlug"), label, quote)
        text = _compose_post(
            header="🌦 Weather Market",
            title=f"{city}: {label}",
            lines=[f"YES {quote} | current {temp} | quotes {coverage}"],
            tags=_hashtags("Weather", city, "Polymarket"),
            meme="☁️ Vibe: forecast meets order book",
            url=_source_link(trade_url, "Market"),
        )
        messages.append(
            MessageCandidate(
                topic="weather",
                dedupe_key=dedupe,
                text=text,
                priority="high",
                metadata={"panel": "global-weather-map", "marketLink": trade_url},
                link_preview=True,
                reply_markup=_trade_markup(market_link or (MarketLink(url=market_url, title=label, matched_by="payload") if market_url else None)),
            )
        )
    return messages


def format_weather_news(payload: Dict[str, Any]) -> List[MessageCandidate]:
    messages: List[MessageCandidate] = []
    for article in _iter_items(payload):
        severity = _text(article.get("severity"), "normal").lower()
        if severity not in {"warning", "watch"}:
            continue
        title = _text(article.get("title"))
        if not title:
            continue
        city = _text(article.get("city"))
        source = _text(article.get("source"), "Weather news")
        summary = _text(article.get("summary"))
        url = _first_url(article.get("url"))
        market_link = resolve_market_link(article, title=title, extra_text=(summary, city, source, "weather"))
        tags = _hashtags("Weather", severity, city, article.get("tags") or [], source)
        text = _compose_post(
            header=f"🚨 Weather {severity.title()}",
            title=title,
            lines=[" | ".join(part for part in (city, source) if part), summary[:260] if summary and summary != title else ""],
            tags=tags,
            meme="🌩 Vibe: skies are speaking",
            url="\n".join(part for part in (_source_link(market_link.url, "Market") if market_link else "", _source_link(url, "Source")) if part),
        )
        dedupe = _short_hash("weather-news", article.get("id"), title.lower(), url)
        priority = "high" if severity == "warning" else "normal"
        messages.append(
            MessageCandidate(
                topic="weather",
                dedupe_key=dedupe,
                text=text,
                priority=priority,
                metadata={"panel": "weather-news", "marketLink": market_link.url if market_link else ""},
                link_preview=bool(market_link or url),
                reply_markup=_trade_markup(market_link, extra_buttons=[{"text": "Source", "url": url}] if url else None),
            )
        )
    return messages


def format_latest_content(payload: Dict[str, Any]) -> List[MessageCandidate]:
    messages: List[MessageCandidate] = []
    for item in _iter_items(payload):
        title = _text(item.get("title") or item.get("headline"))
        if not title:
            continue
        source = _text(item.get("source"), "News")
        summary = _text(item.get("summary") or item.get("description"))
        url = _first_url(item.get("url"))
        market_link = resolve_market_link(item, title=title, extra_text=(summary, source, item.get("contentType")))
        text = _compose_post(
            header="💬 News",
            title=title,
            lines=[source, summary[:280] if summary and summary != title else ""],
            tags=_hashtags("News", source, item.get("contentType")),
            meme="🗞 Vibe: fresh tape",
            url="\n".join(part for part in (_source_link(market_link.url, "Market") if market_link else "", _source_link(url, "Source")) if part),
        )
        dedupe = _short_hash("latest-content", item.get("id"), title.lower(), url)
        messages.append(
            MessageCandidate(
                topic="news",
                dedupe_key=dedupe,
                text=text,
                metadata={"panel": "latest-content", "marketLink": market_link.url if market_link else ""},
                link_preview=bool(market_link or url),
                reply_markup=_trade_markup(market_link, extra_buttons=[{"text": "Source", "url": url}] if url else None),
            )
        )
    return messages


def format_related_news(payload: Dict[str, Any]) -> List[MessageCandidate]:
    items = list(_iter_items(payload))
    if not items:
        return []
    market_title = _text(payload.get("marketTitle") or payload.get("question"), "Focused market")
    market_id = _text(payload.get("marketId") or payload.get("localMarketId"))
    category = _text(payload.get("marketCategory"))
    market_link = resolve_market_link(payload, title=market_title, extra_text=(category,))
    counts: Dict[str, int] = {}
    for item in items:
        content_type = _text(item.get("contentType"), "news").lower()
        counts[content_type] = counts.get(content_type, 0) + 1
    count_line = " | ".join(
        f"{label} {counts.get(key, 0)}"
        for key, label in (("news", "News"), ("video", "Video"), ("report", "Reports"), ("research", "Research"))
        if counts.get(key, 0)
    )
    grouped: Dict[str, List[str]] = {"news": [], "video": [], "report": [], "research": []}
    first_url = ""
    source_labels: List[str] = []
    for item in items:
        title = _text(item.get("title") or item.get("headline"))
        if not title:
            continue
        source = _text(item.get("source"), "Related intel")
        content_type = _text(item.get("contentType"), "news").lower()
        group_key = content_type if content_type in grouped else "news"
        url = _first_url(item.get("url"))
        if not first_url and url:
            first_url = url
        if source and source not in source_labels:
            source_labels.append(source)
        if len(grouped[group_key]) < RELATED_NEWS_GROUP_LIMIT:
            grouped[group_key].append(f"- {source} | {title[:140]}")
    section_lines: List[str] = []
    for key, label in (("news", "News"), ("video", "Video"), ("report", "Reports"), ("research", "Research")):
        lines = grouped[key]
        if not lines:
            continue
        section_lines.append(f"{label}:")
        section_lines.extend(lines)
    if not section_lines:
        return []
    text = _compose_post(
        header="Market Intel",
        title=market_title,
        lines=[
            count_line,
            *section_lines,
            f"Source mode: {_text(payload.get('sourceMode'))}" if payload.get("sourceMode") else "",
        ],
        tags=_hashtags("MarketIntel", category, source_labels[:3]),
        url="\n".join(part for part in (_source_link(market_link.url, "Market") if market_link else "", _source_link(first_url, "Top source")) if part),
    )
    version_items = []
    for key in ("news", "video", "report", "research"):
        version_items.extend(grouped[key])
    version = "|".join(version_items)
    dedupe = _short_hash("related-news-summary", market_id, version, len(items), count_line)
    return [
        MessageCandidate(
            topic="intel",
            dedupe_key=dedupe,
            text=text,
            metadata={"panel": "related-news", "marketId": market_id},
            link_preview=bool(market_link or first_url),
            reply_markup=_trade_markup(market_link, extra_buttons=[{"text": "Top source", "url": first_url}] if first_url else None),
        )
    ]


def format_alpha_signal(payload: Dict[str, Any]) -> List[MessageCandidate]:
    messages: List[MessageCandidate] = []
    for item in _iter_items(payload):
        title = _text(item.get("title") or item.get("marketTitle") or item.get("question") or item.get("name") or item.get("label"))
        if not title:
            continue
        signal = _text(item.get("signal") or item.get("reason") or item.get("summary") or item.get("status"))
        score = _text(item.get("score") or item.get("confidence") or item.get("rank"))
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        action_text = " ".join(part for part in (_text(action.get("label")), _text(action.get("outcome"))) if part)
        market_link = resolve_market_link(item, title=title, extra_text=(signal, action_text, item.get("sourceTag"), item.get("labels") or []))
        url = market_link.url if market_link else _polymarket_url(item, title=title)
        lines = [signal[:260] if signal else "", f"Action: {action_text}" if action_text else "", f"score/confidence: {score}" if score else ""]
        text = _compose_post(
            header="🐳 Alpha Signal",
            title=title,
            lines=lines,
            tags=_hashtags("Alpha", "Polymarket", action.get("outcome") or item.get("outcome"), item.get("sourceTag"), item.get("labels") or []),
            meme="🐋 Vibe: smart money splashed",
            url=_source_link(url, "Market"),
        )
        direction = _text(action.get("outcome") or item.get("outcome") or action.get("label")).lower()
        dedupe = _short_hash("alpha-signal", _market_identity(item, title=title), direction)
        messages.append(
            MessageCandidate(
                topic="alpha",
                dedupe_key=dedupe,
                text=text,
                priority="high",
                metadata={"panel": "alpha-signal", "marketLink": url},
                link_preview=bool(url),
                reply_markup=_trade_markup(market_link or (MarketLink(url=url, title=title, matched_by="fallback") if url else None)),
            )
        )
    return messages


def format_new_market_signals(payload: Dict[str, Any]) -> List[MessageCandidate]:
    messages: List[MessageCandidate] = []
    for item in _iter_items(payload):
        title = _text(item.get("title") or item.get("question") or item.get("marketTitle") or item.get("eventTitle"))
        if not title or any(title.startswith(prefix) for prefix in NEW_MARKET_PLACEHOLDER_TITLE_PREFIXES):
            continue
        status = _text(item.get("status") or item.get("signal") or item.get("reason"))
        probability = _text(item.get("initialYesProbability"))
        probability_line = f"Initial YES probability: {_pct(probability)}" if probability else ""
        market_link = resolve_market_link(item, title=title, extra_text=(status,))
        url = market_link.url if market_link else _polymarket_url(item, title=title)
        asset_tags = _title_tags(title)
        text = _compose_post(
            header="🆕 New Market Signal",
            title=title,
            lines=[status[:260] if status else "", probability_line, _text(item.get("marketCreatedAt"))],
            tags=_hashtags("NewMarket", "Polymarket", asset_tags),
            meme="🧪 Vibe: fresh market just spawned",
            url=_source_link(url, "Market/Search"),
        )
        dedupe = _short_hash("new-market-signal", _market_identity(item, title=title))
        messages.append(
            MessageCandidate(
                topic="alpha",
                dedupe_key=dedupe,
                text=text,
                priority="normal",
                metadata={"panel": "new-market-signals", "marketLink": url},
                link_preview=bool(url),
                reply_markup=_trade_markup(market_link or (MarketLink(url=url, title=title, matched_by="fallback") if url else None)),
            )
        )
    return messages


def format_macro_payload(payload: Dict[str, Any], *, panel_label: str, panel_id: str) -> List[MessageCandidate]:
    messages: List[MessageCandidate] = []
    for item in _iter_items(payload):
        title = _text(item.get("title") or item.get("eventTitle") or item.get("name") or item.get("label") or item.get("metric"))
        if not title:
            continue
        status = _text(item.get("status") or item.get("signal") or item.get("summary") or item.get("description"))
        value = _text(item.get("value") or item.get("actual") or item.get("forecast") or item.get("probability"))
        source_url = _first_url(item.get("sourceUrl"))
        market_link = resolve_market_link(item, title=title, extra_text=(status, value, item.get("categoryLabels") or item.get("categoryIds") or []))
        url = market_link.url if market_link else (_polymarket_url(item, title=title) or source_url)
        top_outcomes = item.get("topOutcomes") if isinstance(item.get("topOutcomes"), list) else []
        outcome_line = ""
        if top_outcomes:
            labels = []
            for outcome in top_outcomes[:3]:
                if not isinstance(outcome, dict):
                    continue
                label = _text(outcome.get("label") or outcome.get("title"))
                yes = _price(outcome.get("yesPrice"))
                if label:
                    labels.append(f"{label} YES {yes}")
            outcome_line = " | ".join(labels)
        text = _compose_post(
            header=f"📈 {panel_label}",
            title=title,
            lines=[status[:260] if status else "", f"value: {value}" if value else "", outcome_line],
            tags=_hashtags("Macro", "Polymarket", item.get("categoryLabels") or item.get("categoryIds") or []),
            meme="📊 Vibe: macro board is moving",
            url="\n".join(part for part in (_source_link(url, "Market") if url else "", _source_link(source_url, "Source") if source_url and source_url != url else "") if part),
        )
        dedupe = _short_hash(panel_id, item.get("id") or item.get("eventId") or item.get("slug"), title.lower(), status, value)
        messages.append(
            MessageCandidate(
                topic="macro",
                dedupe_key=dedupe,
                text=text,
                priority="normal",
                metadata={"panel": panel_id, "marketLink": url},
                link_preview=bool(url or source_url),
                reply_markup=_trade_markup(market_link or (MarketLink(url=url, title=title, matched_by="fallback") if url else None), extra_buttons=[{"text": "Source", "url": source_url}] if source_url and source_url != url else None),
            )
        )
    return messages


def _title_tags(title: str) -> List[str]:
    known = ("BTC", "Bitcoin", "ETH", "Ethereum", "SOL", "Solana", "DOGE", "Dogecoin", "XRP", "Hyperliquid", "NBA", "Fed", "CPI")
    lowered = title.lower()
    return [tag for tag in known if tag.lower() in lowered]


def _format_specialized(panel_id: str, payload: Dict[str, Any]) -> List[MessageCandidate]:
    if panel_id == "latest-content":
        return format_latest_content(payload)
    if panel_id == "related-news":
        return format_related_news(payload)
    if panel_id == "alpha-signal":
        return format_alpha_signal(payload)
    if panel_id == "new-market-signals":
        return format_new_market_signals(payload)
    if panel_id == "polymarket-macro-map":
        return format_macro_payload(payload, panel_label="Macro market map", panel_id=panel_id)
    if panel_id == "cpi-release-command-center":
        return format_macro_payload(payload, panel_label="CPI command center", panel_id=panel_id)
    if panel_id == "nba-scoreboard":
        return format_nba_scoreboard(payload)
    if panel_id == "nba-intel":
        return format_nba_intel(payload)
    if panel_id == "espn-matchup-predictor":
        return format_nba_predictor(payload)
    if panel_id == "worldcup-intel":
        return format_worldcup_intel(payload)
    if panel_id == "global-weather-map":
        return format_weather_map(payload)
    if panel_id == "weather-news":
        return format_weather_news(payload)
    return []


def format_panel_snapshot_outcome(panel_id: str, payload: Dict[str, Any]) -> FormatOutcome:
    requested_id = str(panel_id or "").strip()
    canonical_id = {
        "related-intel": "related-news",
        "worldcup-dashboard": "worldcup-intel",
    }.get(requested_id, requested_id)
    contract = delivery_contract(canonical_id)
    if contract is None:
        return FormatOutcome(
            panel_id=requested_id,
            mode="unsupported",
            reason="panel is not present in the Telegram delivery catalog",
        )
    if not isinstance(payload, dict):
        return FormatOutcome(
            panel_id=canonical_id,
            mode="format-error",
            reason="payload is not an object",
        )
    try:
        if contract.mode is DeliveryMode.SPECIALIZED:
            candidates = _format_specialized(canonical_id, payload)
        elif contract.mode is DeliveryMode.GENERIC:
            candidates = format_generic_snapshot(canonical_id, payload, topic=contract.topic)
        else:
            return FormatOutcome(
                panel_id=canonical_id,
                mode=contract.mode.value,
                reason=contract.reason,
            )
    except Exception as exc:
        return FormatOutcome(
            panel_id=canonical_id,
            mode="format-error",
            reason=f"formatter raised {exc.__class__.__name__}",
        )
    reason = "" if candidates else "formatter produced no candidate for the current payload"
    return FormatOutcome(
        panel_id=canonical_id,
        mode=contract.mode.value,
        candidates=tuple(candidates),
        reason=reason,
    )


def format_panel_snapshot(panel_id: str, payload: Dict[str, Any]) -> List[MessageCandidate]:
    """Compatibility wrapper for existing callers that only consume candidates."""
    return list(format_panel_snapshot_outcome(panel_id, payload).candidates)


def format_all_snapshots_with_stats(
    snapshots: Dict[str, Dict[str, Any]],
) -> tuple[List[MessageCandidate], Dict[str, int]]:
    messages: List[MessageCandidate] = []
    stats = {
        "specialized": 0,
        "generic": 0,
        "empty": 0,
        "aggregate": 0,
        "market_scoped": 0,
        "browser_only": 0,
        "non_pushable": 0,
        "unsupported": 0,
        "format_errors": 0,
    }
    for panel_id, payload in snapshots.items():
        outcome = format_panel_snapshot_outcome(panel_id, payload)
        messages.extend(outcome.candidates)
        if outcome.mode == DeliveryMode.SPECIALIZED.value:
            stats["specialized"] += 1
        elif outcome.mode == DeliveryMode.GENERIC.value:
            stats["generic"] += 1
        elif outcome.mode == DeliveryMode.AGGREGATE.value:
            stats["aggregate"] += 1
        elif outcome.mode == DeliveryMode.MARKET_SCOPED.value:
            stats["market_scoped"] += 1
        elif outcome.mode == DeliveryMode.BROWSER_ONLY.value:
            stats["browser_only"] += 1
        elif outcome.mode == DeliveryMode.NON_PUSHABLE.value:
            stats["non_pushable"] += 1
        elif outcome.mode == "unsupported":
            stats["unsupported"] += 1
        elif outcome.mode == "format-error":
            stats["format_errors"] += 1
        if not outcome.candidates and outcome.mode in {
            DeliveryMode.SPECIALIZED.value,
            DeliveryMode.GENERIC.value,
        }:
            stats["empty"] += 1
    return messages, stats


def format_all_snapshots(snapshots: Dict[str, Dict[str, Any]]) -> List[MessageCandidate]:
    messages, _stats = format_all_snapshots_with_stats(snapshots)
    return messages
