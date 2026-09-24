from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote_plus


WORLDCUP_INTEL_NAMESPACE = "snapshot:sports:worldcup-intel"
WORLDCUP_INTEL_CACHE_KEY = "worldcup-v2-live-intel-v2"
DEFAULT_LIMIT = 96
DEFAULT_TTL_SECONDS = 900

ESPN_NEWS_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/news"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WTTR_URL = "https://wttr.in"

HOST_CITIES = [
    {"id": "atlanta", "city": "Atlanta", "latitude": 33.7554, "longitude": -84.4008},
    {"id": "boston", "city": "Boston / Foxborough", "latitude": 42.0909, "longitude": -71.2643},
    {"id": "dallas", "city": "Dallas / Arlington", "latitude": 32.7473, "longitude": -97.0945},
    {"id": "houston", "city": "Houston", "latitude": 29.6847, "longitude": -95.4107},
    {"id": "kansas-city", "city": "Kansas City", "latitude": 39.0489, "longitude": -94.4839},
    {"id": "los-angeles", "city": "Los Angeles / Inglewood", "latitude": 33.9535, "longitude": -118.3392},
    {"id": "miami", "city": "Miami Gardens", "latitude": 25.958, "longitude": -80.2389},
    {"id": "new-york-new-jersey", "city": "New York / New Jersey", "latitude": 40.8135, "longitude": -74.0745},
    {"id": "philadelphia", "city": "Philadelphia", "latitude": 39.9008, "longitude": -75.1675},
    {"id": "san-francisco", "city": "San Francisco Bay Area", "latitude": 37.403, "longitude": -121.97},
    {"id": "seattle", "city": "Seattle", "latitude": 47.5952, "longitude": -122.3316},
    {"id": "guadalajara", "city": "Guadalajara / Zapopan", "latitude": 20.6818, "longitude": -103.4623},
    {"id": "mexico-city", "city": "Mexico City", "latitude": 19.3029, "longitude": -99.1505},
    {"id": "monterrey", "city": "Monterrey / Guadalupe", "latitude": 25.6683, "longitude": -100.2446},
    {"id": "toronto", "city": "Toronto", "latitude": 43.6332, "longitude": -79.4186},
    {"id": "vancouver", "city": "Vancouver", "latitude": 49.2767, "longitude": -123.1119},
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _headers(accept: str = "application/json") -> Dict[str, str]:
    return {
        "Accept": accept,
        "User-Agent": "polydata-worldcup-intel/1.0 (+https://www.polymonitor.club)",
    }


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _signal(
    *,
    source: str,
    title: str,
    summary: str,
    category: str,
    tags: Iterable[str],
    age: str,
    url: str = "#",
    accent: str = "blue",
    provider: str = "",
    match_id: str = "",
    city_id: str = "",
) -> Dict[str, Any]:
    tag_rows = []
    for tag in tags:
        label = str(tag or "").strip().upper()
        if not label:
            continue
        if label in {"ALERT", "INJURY", "SUSP", "RISK"}:
            tone = "red"
        elif label in {"RUMOR", "WATCH", "TACTIC", "TEMPO", "MODEL"}:
            tone = "gold"
        elif label in {"CONFIRM", "FACT", "OFFICIAL", "LIVE"}:
            tone = "green"
        elif label in {"MARKET", "XG", "PRED XI"}:
            tone = "purple"
        else:
            tone = "blue"
        tag_rows.append({"label": label[:16], "tone": tone})
    return {
        "id": re.sub(r"[^a-z0-9-]+", "-", f"{category}-{provider or source}-{title}".lower()).strip("-")[:96],
        "source": source,
        "title": _clean_text(title),
        "summary": _clean_text(summary),
        "category": category,
        "age": age,
        "url": url or "#",
        "tags": tag_rows[:3],
        "accent": accent,
        "provider": provider or source,
        "matchId": match_id or None,
        "cityId": city_id or None,
    }


def _rss_items(ctx: dict, query: str, *, limit: int = 10, language: str = "en-US", region: str = "US") -> List[Dict[str, str]]:
    requests_lib = ctx.get("requests")
    if requests_lib is None:
        return []
    url = f"{GOOGLE_NEWS_RSS_URL}?q={quote_plus(query)}&hl={language}&gl={region}&ceid={region}:{language.split('-')[0]}"
    try:
        text = ctx["http_text_get"](url, timeout=9, headers=_headers("application/rss+xml, application/xml, text/xml"))
    except Exception:
        return []
    soup = None
    if ctx.get("BeautifulSoup") is not None:
        try:
            soup = ctx["BeautifulSoup"](text, "xml")
        except Exception:
            soup = ctx["BeautifulSoup"](text, "html.parser")
    if soup is None:
        return []
    rows: List[Dict[str, str]] = []
    for item in soup.find_all("item")[:limit]:
        title = _clean_text(item.title.text if item.title else "")
        link = _clean_text(item.link.text if item.link else "#")
        published = _clean_text(item.pubDate.text if item.pubDate else "")
        source = _clean_text(item.source.text if item.source else "Google News")
        description = _clean_text(item.description.text if item.description else "")
        if title:
            rows.append({"title": title, "url": link, "publishedAt": published, "source": source, "summary": description})
    return rows


def _espn_news(ctx: dict, limit: int) -> List[Dict[str, Any]]:
    try:
        payload = ctx["http_json_get"](ESPN_NEWS_URL, params={"limit": limit}, timeout=9, headers=_headers())
    except Exception:
        return []
    articles = payload.get("articles") if isinstance(payload, dict) else []
    rows: List[Dict[str, Any]] = []
    for article in articles or []:
        if not isinstance(article, dict):
            continue
        rows.append(
            {
                "id": str(article.get("id") or article.get("dataSourceIdentifier") or article.get("headline") or ""),
                "title": _clean_text(article.get("headline")),
                "source": "ESPN",
                "url": str(article.get("links", {}).get("web", {}).get("href") or article.get("link") or "#"),
                "publishedAt": str(article.get("published") or article.get("lastModified") or _utc_now_iso()),
                "summary": _clean_text(article.get("description") or article.get("story")),
            }
        )
    return [row for row in rows if row["title"]][:limit]


def _espn_scoreboard_signals(ctx: dict) -> List[Dict[str, Any]]:
    try:
        payload = ctx["http_json_get"](ESPN_SCOREBOARD_URL, timeout=9, headers=_headers())
    except Exception:
        return []
    events = payload.get("events") if isinstance(payload, dict) else []
    signals: List[Dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        competitions = event.get("competitions") or []
        competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
        competitors = competition.get("competitors") or []
        teams = [
            str((competitor.get("team") or {}).get("displayName") or competitor.get("displayName") or "").strip()
            for competitor in competitors
            if isinstance(competitor, dict)
        ]
        status = ((competition.get("status") or event.get("status") or {}).get("type") or {}).get("description") or event.get("status", {}).get("type", {}).get("name")
        title = event.get("name") or "FIFA World Cup fixture"
        summary = " · ".join([part for part in [event.get("date"), competition.get("venue", {}).get("fullName"), ", ".join([team for team in teams if team])] if part])
        signals.append(
            _signal(
                source="ESPN SCOREBOARD",
                title=f"{title}: {status or 'status watch'}",
                summary=summary or "ESPN public scoreboard fixture status.",
                category="officialFacts",
                tags=["FACT", "SCOREBOARD"],
                age=str(event.get("date") or ""),
                url=str(event.get("links", [{}])[0].get("href") if event.get("links") else "#"),
                accent="green",
                provider="espn-scoreboard",
            )
        )
    return signals[:8]


def _news_to_signal(item: Dict[str, str], *, category: str, tags: List[str], accent: str) -> Dict[str, Any]:
    return _signal(
        source=item.get("source") or "NEWS",
        title=item.get("title") or "World Cup update",
        summary=item.get("summary") or "Live news item matched to World Cup intelligence query.",
        category=category,
        tags=tags,
        age=item.get("publishedAt") or "",
        url=item.get("url") or "#",
        accent=accent,
        provider="google-news-rss",
    )


def _weather_code_label(code: Any) -> str:
    labels = {
        0: "Clear",
        1: "Mostly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Fog",
        51: "Drizzle",
        53: "Drizzle",
        55: "Drizzle",
        61: "Rain",
        63: "Rain",
        65: "Heavy rain",
        71: "Snow",
        80: "Showers",
        81: "Showers",
        82: "Heavy showers",
        95: "Storm",
    }
    return labels.get(_safe_int(code), "Weather watch")


def _open_meteo_weather(ctx: dict) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        payload = ctx["http_json_get"](
            OPEN_METEO_URL,
            params={
                "latitude": ",".join(str(city["latitude"]) for city in HOST_CITIES),
                "longitude": ",".join(str(city["longitude"]) for city in HOST_CITIES),
                "current": "temperature_2m,weather_code,wind_speed_10m,precipitation",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": 5,
                "timezone": "auto",
            },
            timeout=12,
            headers=_headers(),
        )
    except Exception:
        return []
    payloads = payload if isinstance(payload, list) else [payload]
    for city, city_payload in zip(HOST_CITIES, payloads):
        payload = city_payload if isinstance(city_payload, dict) else {}
        current = payload.get("current") if isinstance(payload, dict) else {}
        daily = payload.get("daily") if isinstance(payload, dict) else {}
        times = daily.get("time") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        rain = daily.get("precipitation_probability_max") or []
        codes = daily.get("weather_code") or []
        forecast = []
        for index, date_value in enumerate(times[:5]):
            forecast.append(
                {
                    "date": str(date_value),
                    "highC": _safe_int(highs[index] if index < len(highs) else None) or 0,
                    "lowC": _safe_int(lows[index] if index < len(lows) else None) or 0,
                    "condition": _weather_code_label(codes[index] if index < len(codes) else None),
                    "precipitationProbability": _safe_int(rain[index] if index < len(rain) else None) or 0,
                }
            )
        rows.append(
            {
                "cityId": city["id"],
                "current": {
                    "tempC": _safe_int(current.get("temperature_2m")) or 0,
                    "condition": _weather_code_label(current.get("weather_code")),
                    "windKph": _safe_int(current.get("wind_speed_10m")),
                    "precipitationProbability": _safe_int(current.get("precipitation")),
                },
                "forecast": forecast,
                "generatedAt": _utc_now_iso(),
                "source": "Open-Meteo",
            }
        )
    return rows


def _wttr_weather(ctx: dict, used_city_ids: set[str] | None = None) -> List[Dict[str, Any]]:
    used_city_ids = used_city_ids or set()
    rows: List[Dict[str, Any]] = []
    for city in HOST_CITIES:
        if city["id"] in used_city_ids:
            continue
        query_city = city["city"].split("/")[0].strip()
        try:
            payload = ctx["http_json_get"](
                f"{WTTR_URL}/{quote_plus(query_city)}",
                params={"format": "j1"},
                timeout=8,
                headers=_headers(),
            )
        except Exception:
            continue
        current_rows = payload.get("current_condition") if isinstance(payload, dict) else []
        current = current_rows[0] if current_rows and isinstance(current_rows[0], dict) else {}
        weather_rows = payload.get("weather") if isinstance(payload, dict) else []
        forecast = []
        for row in weather_rows[:5]:
            if not isinstance(row, dict):
                continue
            hourly = row.get("hourly") or []
            midday = hourly[len(hourly) // 2] if hourly and isinstance(hourly[len(hourly) // 2], dict) else {}
            desc_rows = midday.get("weatherDesc") or current.get("weatherDesc") or []
            condition = desc_rows[0].get("value") if desc_rows and isinstance(desc_rows[0], dict) else "Weather watch"
            forecast.append(
                {
                    "date": str(row.get("date") or ""),
                    "highC": _safe_int(row.get("maxtempC")) or 0,
                    "lowC": _safe_int(row.get("mintempC")) or 0,
                    "condition": _clean_text(condition),
                    "precipitationProbability": _safe_int(midday.get("chanceofrain")) or 0,
                }
            )
        current_desc = current.get("weatherDesc") or []
        condition = current_desc[0].get("value") if current_desc and isinstance(current_desc[0], dict) else "Weather watch"
        rows.append(
            {
                "cityId": city["id"],
                "current": {
                    "tempC": _safe_int(current.get("temp_C")) or 0,
                    "condition": _clean_text(condition),
                    "windKph": _safe_int(current.get("windspeedKmph")),
                    "precipitationProbability": _safe_int(current.get("precipMM")),
                },
                "forecast": forecast,
                "generatedAt": _utc_now_iso(),
                "source": "wttr.in",
            }
        )
    return rows


def _build_live_payload(ctx: dict, *, limit: int) -> Dict[str, Any]:
    generated_at = _utc_now_iso()
    news = _espn_news(ctx, 18)
    injury_items = _rss_items(ctx, '"World Cup 2026" injury roster squad site:espn.com OR site:transfermarkt.com', limit=10)
    lineup_items = _rss_items(ctx, '"World Cup 2026" lineup predicted XI Flashscore SofaScore FotMob WhoScored', limit=10)
    xg_items = _rss_items(ctx, '"World Cup 2026" xG Opta Analyst FBref StatsBomb SofaScore', limit=8)
    tactic_items = _rss_items(ctx, '"World Cup 2026" tactical preview The Athletic Tifo Coaches Voice Opta', limit=8)
    local_items = []
    for query, language, region in [
        ('"World Cup 2026" TyC Sports Ole entrenamiento lesion seleccion', "es-419", "AR"),
        ('"World Cup 2026" Globo Esporte treino escalação lesão seleção', "pt-BR", "BR"),
        ('"World Cup 2026" Marca AS alineacion lesion seleccion', "es", "ES"),
        ('"World Cup 2026" L Equipe blessure entrainement equipe', "fr", "FR"),
        ('"World Cup 2026" Kicker Bild Training Verletzung Nationalmannschaft', "de", "DE"),
    ]:
        local_items.extend(_rss_items(ctx, query, limit=3, language=language, region=region))
    weather = _open_meteo_weather(ctx)
    weather_source = "open-meteo"
    if len(weather) < len(HOST_CITIES):
        missing_weather = _wttr_weather(ctx, {str(item.get("cityId")) for item in weather})
        if missing_weather:
            weather = [*weather, *missing_weather]
            weather_source = "open-meteo+wttr" if weather_source == "open-meteo" and len(weather) > len(missing_weather) else "wttr"

    signals: List[Dict[str, Any]] = []
    signals.extend(_espn_scoreboard_signals(ctx))
    signals.extend(_news_to_signal(item, category="injuryTracker", tags=["INJURY"], accent="red") for item in injury_items)
    signals.extend(_news_to_signal(item, category="lineupWatch", tags=["PRED XI"], accent="purple") for item in lineup_items)
    signals.extend(_news_to_signal(item, category="xgModel", tags=["XG"], accent="purple") for item in xg_items)
    signals.extend(_news_to_signal(item, category="tacticalMatchup", tags=["TACTIC"], accent="gold") for item in tactic_items)
    signals.extend(_news_to_signal(item, category="localMedia", tags=["LOCAL"], accent="blue") for item in local_items)
    for item in weather[:8]:
        condition = item.get("current", {}).get("condition") or "weather"
        signals.append(
            _signal(
                source=str(item.get("source") or "WEATHER"),
                title=f"{next((city['city'] for city in HOST_CITIES if city['id'] == item.get('cityId')), item.get('cityId'))}: {condition}",
                summary=f"{item.get('current', {}).get('tempC')}C · wind {item.get('current', {}).get('windKph')} kph · 5-day venue forecast attached.",
                category="refVenue",
                tags=["WEATHER"],
                age=generated_at,
                url="https://open-meteo.com/" if item.get("source") != "wttr.in" else "https://wttr.in/",
                accent="blue",
                provider=str(item.get("source") or "weather"),
                city_id=str(item.get("cityId") or ""),
            )
        )

    provider_states = {
        "espnNews": "ok" if news else "empty",
        "espnScoreboard": "ok" if any(signal.get("provider") == "espn-scoreboard" for signal in signals) else "empty",
        "googleNewsRss": "ok" if injury_items or lineup_items or xg_items or tactic_items or local_items else "empty",
        "openMeteo": "ok" if weather_source.startswith("open-meteo") and weather else "empty",
        "wttr": "ok" if "wttr" in weather_source and weather else "empty",
        "fifaMatchCentre": "restricted-or-html-only",
        "flashscore": "restricted-no-public-api",
        "sofascore": "restricted",
        "fotmob": "restricted",
        "opta": "licensed",
        "fbref": "html-provider-not-polled-live",
        "theAthletic": "subscription",
    }
    return {
        "generatedAt": generated_at,
        "status": "ok" if signals or news or weather else "empty",
        "cacheMode": "live",
        "source": f"ESPN public API / Google News / {weather_source}",
        "sourceUrl": "https://site.api.espn.com/",
        "providerStates": provider_states,
        "news": news[:limit],
        "weather": weather,
        "signals": signals[:limit],
    }


def _normalize_payload(payload: Any, *, limit: int) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    signals = [item for item in (payload.get("signals") or []) if isinstance(item, dict)][:limit]
    news = [item for item in (payload.get("news") or []) if isinstance(item, dict)][:limit]
    weather = [item for item in (payload.get("weather") or []) if isinstance(item, dict)]
    return {
        "generatedAt": str(payload.get("generatedAt") or _utc_now_iso()),
        "status": str(payload.get("status") or ("ok" if signals or news or weather else "empty")),
        "cacheMode": str(payload.get("cacheMode") or ""),
        "source": str(payload.get("source") or "World Cup runtime intel"),
        "sourceUrl": str(payload.get("sourceUrl") or ""),
        "providerStates": payload.get("providerStates") if isinstance(payload.get("providerStates"), dict) else {},
        "news": news,
        "weather": weather,
        "signals": signals,
        "summary": {
            "signals": len(signals),
            "news": len(news),
            "weatherCities": len(weather),
            "liveProviders": sum(1 for state in (payload.get("providerStates") or {}).values() if state == "ok"),
        },
    }


def _read_cached(ctx: dict, *, limit: int, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    reader = ctx.get("get_cached_json")
    if callable(reader):
        cached = reader(WORLDCUP_INTEL_NAMESPACE, WORLDCUP_INTEL_CACHE_KEY)
        if isinstance(cached, dict):
            return {**_normalize_payload(cached, limit=limit), "cacheMode": "redis"}
    store = ctx.get("SNAPSHOT_STORE")
    if store is not None:
        cached = store.get(WORLDCUP_INTEL_NAMESPACE, WORLDCUP_INTEL_CACHE_KEY)
        if isinstance(cached, dict):
            return {**_normalize_payload(cached, limit=limit), "cacheMode": "sqlite"}
        stale = store.get_stale(WORLDCUP_INTEL_NAMESPACE, WORLDCUP_INTEL_CACHE_KEY)
        if isinstance(stale, dict):
            return {**_normalize_payload(stale, limit=limit), "cacheMode": "stale"}
    return None


def _store(ctx: dict, payload: Dict[str, Any], ttl_seconds: int) -> None:
    store = ctx.get("SNAPSHOT_STORE")
    if store is not None:
        store.set(WORLDCUP_INTEL_NAMESPACE, WORLDCUP_INTEL_CACHE_KEY, payload, ttl_seconds)
    setter = ctx.get("set_cached_json")
    if callable(setter):
        setter(WORLDCUP_INTEL_NAMESPACE, WORLDCUP_INTEL_CACHE_KEY, payload, ttl_seconds)


def get_worldcup_intel_snapshot(ctx: dict, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    ttl_seconds = max(300, int(getattr(ctx.get("SETTINGS"), "sports_runtime_ttl_seconds", DEFAULT_TTL_SECONDS) or DEFAULT_TTL_SECONDS))
    cached = _read_cached(ctx, limit=limit, ttl_seconds=ttl_seconds)
    if cached and cached.get("cacheMode") != "stale":
        return cached
    try:
        payload = _normalize_payload(_build_live_payload(ctx, limit=limit), limit=limit)
        _store(ctx, payload, ttl_seconds)
        return payload
    except Exception as exc:
        if cached:
            return {**cached, "status": "stale", "error": exc.__class__.__name__}
        return _normalize_payload(
            {
                "generatedAt": _utc_now_iso(),
                "status": "error",
                "cacheMode": "source-required",
                "providerStates": {"worldcupIntel": f"error:{exc.__class__.__name__}"},
                "signals": [],
                "news": [],
                "weather": [],
            },
            limit=limit,
        )


if __name__ == "__main__":
    print(json.dumps({"status": "module-ok", "namespace": WORLDCUP_INTEL_NAMESPACE}, ensure_ascii=True))
