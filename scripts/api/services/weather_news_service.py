from __future__ import annotations

import hashlib
import html
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode
from xml.etree import ElementTree

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
    resolve_service_value,
)
from weather.cities import load_weather_cities


WEATHER_NEWS_SNAPSHOT_NAMESPACE = "snapshot:weather:news"
WEATHER_NEWS_CACHE_KEY = "panel-v1"
DEFAULT_NEWS_LIMIT = 24

_LIVE_REFRESH_LOCK = threading.Lock()
_LIVE_REFRESHING: set[str] = set()

RELEVANCE_RE = re.compile(r"\b(weather|forecast|storm|rain|heat|heatwave|cold|wind|snow|flood|warning|alert|temperature|typhoon|hurricane)\b", re.I)
WEATHER_CONTEXT_RE = re.compile(
    r"\b(weather|forecast|rain|heat|heatwave|cold|wind|snow|flood|temperature|typhoon|hurricane|tornado|freeze|severe storms?|thunderstorms?|storm damage|storm chance|storm threat|storm warning|storm watch|storm advisory|storm-hit|strong storms?|storms? disrupts?|storms? delays?|storms? expected|storms? bring|storms? lash|storms? batter)\b",
    re.I,
)
SPORTS_FALSE_POSITIVE_RE = re.compile(
    r"\b(nrl|nba|wnba|nfl|mlb|nhl|afl|eels|tigers|storm\s+v|v\s+storm|seattle storm|melbourne storm|realgm|odds|picks|fans|facebook|death claims|roster|fixture|score|zero tackle|fox sports|wests tigers|losing streak|bellamy|origin teammate|full time|round \d+|injury update)\b",
    re.I,
)
SEVERE_RE = re.compile(r"\b(heatwave|warning|storm|flood|extreme|alert|hurricane|typhoon|danger|record)\b", re.I)
TAG_PATTERNS = {
    "heat": re.compile(r"\b(heat|heatwave|hot|temperature)\b", re.I),
    "storm": re.compile(r"\b(storm|thunder|hurricane|typhoon)\b", re.I),
    "rain": re.compile(r"\b(rain|flood|shower)\b", re.I),
    "snow": re.compile(r"\b(snow|ice|cold|freeze)\b", re.I),
    "forecast": re.compile(r"\b(forecast|weather|outlook)\b", re.I),
}

WEATHER_TOPIC_QUERIES: List[Dict[str, str]] = [
    {"city_id": "global-hurricane", "city": "Hurricane", "marketFamily": "hurricane", "news_query": "hurricane forecast OR NOAA hurricane OR tropical storm"},
    {"city_id": "global-tornado", "city": "Tornado", "marketFamily": "tornado", "news_query": "tornado warning OR severe storm outlook OR SPC tornado"},
    {"city_id": "global-precipitation", "city": "Precipitation", "marketFamily": "precipitation", "news_query": "rainfall forecast OR flood warning OR precipitation outlook"},
    {"city_id": "global-climate", "city": "Climate", "marketFamily": "global_climate", "news_query": "global temperature OR climate anomaly OR heat record"},
    {"city_id": "global-volcano", "city": "Volcano", "marketFamily": "volcano", "news_query": "volcano eruption OR volcanic alert"},
    {"city_id": "global-pandemic", "city": "Pandemic", "marketFamily": "pandemic", "news_query": "pandemic outbreak OR WHO health emergency"},
]


@dataclass(frozen=True)
class WeatherNewsDependencies:
    settings: Any
    application: Any
    http_text_get: Callable[..., Any]
    utc_now_iso: Callable[..., Any] | None
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    snapshot_store: Any

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> WeatherNewsDependencies:
        return cls(
            settings=resolve_service_value(context, "SETTINGS"),
            application=resolve_optional_service_value(context, "app"),
            http_text_get=resolve_service_callable(
                context,
                "http_text_get",
            ),
            utc_now_iso=resolve_optional_service_callable(
                context,
                "utc_now_iso",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
        )


WeatherNewsContext = Mapping[str, Any] | WeatherNewsDependencies


def _dependencies(
    context: WeatherNewsContext,
) -> WeatherNewsDependencies:
    if isinstance(context, WeatherNewsDependencies):
        return context
    return WeatherNewsDependencies.from_context(context)


def _utc_now_iso(dependencies: WeatherNewsDependencies) -> str:
    if dependencies.utc_now_iso is not None:
        return str(dependencies.utc_now_iso())
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_pub_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        parsed = parsedate_to_datetime(text)
    except Exception:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except Exception:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _text(node: ElementTree.Element, name: str) -> str:
    child = node.find(name)
    return html.unescape(str(child.text or "").strip()) if child is not None else ""


def _strip_summary(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:260]


def _tags(text: str) -> List[str]:
    return [tag for tag, pattern in TAG_PATTERNS.items() if pattern.search(text)]


def _severity(text: str) -> str:
    if SEVERE_RE.search(text):
        return "warning"
    if re.search(r"\b(watch|advisory|rain|wind|heat|cold)\b", text, re.I):
        return "watch"
    return "normal"


def _article_id(city_id: str, title: str, source: str, url: str) -> str:
    raw = f"{city_id}|{title.lower().strip()}|{source.lower().strip()}|{url.strip()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _google_news_url(base_url: str, query: str) -> str:
    return f"{base_url}?{urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})}"


def fetch_google_news_rss(
    ctx: WeatherNewsContext,
    city: Dict[str, Any],
) -> List[Dict[str, Any]]:
    dependencies = _dependencies(ctx)
    base_url = str(getattr(dependencies.settings, "google_news_rss_url", "") or "").strip()
    if not base_url:
        raise RuntimeError("google news rss url missing")
    query = str(city.get("news_query") or f"{city.get('city')} weather OR forecast OR storm OR heatwave")
    rss_url = _google_news_url(base_url, query)
    xml_text = dependencies.http_text_get(
        rss_url,
        timeout=14,
        headers={"Accept": "application/rss+xml,application/xml,text/xml", "User-Agent": "polydata-weather-news/1.0"},
    )
    root = ElementTree.fromstring(xml_text)
    items: List[Dict[str, Any]] = []
    for item in root.findall("./channel/item"):
        title = _text(item, "title")
        link = _text(item, "link")
        summary = _strip_summary(_text(item, "description"))
        source_node = item.find("source")
        source = html.unescape(str(source_node.text or "").strip()) if source_node is not None else "Google News"
        combined = f"{title} {summary} {source}"
        if not RELEVANCE_RE.search(combined):
            continue
        if not WEATHER_CONTEXT_RE.search(combined):
            continue
        if SPORTS_FALSE_POSITIVE_RE.search(combined):
            continue
        tags = _tags(combined)
        items.append(
            {
                "id": _article_id(str(city["city_id"]), title, source, link),
                "cityId": city.get("city_id"),
                "city": city.get("city"),
                "source": source,
                "title": title,
                "summary": summary or title,
                "publishedAt": _parse_pub_date(_text(item, "pubDate")),
                "url": link,
                "severity": _severity(combined),
                "tags": tags or ["forecast"],
                "marketFamily": city.get("marketFamily"),
            }
        )
    return items


def _dedupe_rank(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for article in articles:
        key = re.sub(r"[^a-z0-9]+", " ", f"{article.get('title', '')} {article.get('source', '')}".lower()).strip()
        url_key = str(article.get("url") or "").strip()
        identity = url_key or key
        if not identity or identity in seen:
            continue
        seen.add(identity)
        deduped.append(article)
    severity_weight = {"warning": 2, "watch": 1, "normal": 0}
    deduped.sort(key=lambda row: (severity_weight.get(str(row.get("severity")), 0), _timestamp(row.get("publishedAt"))), reverse=True)
    return deduped


def build_news_summary(articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_city: Dict[str, int] = {}
    for article in articles:
        city = str(article.get("city") or "Unknown")
        by_city[city] = by_city.get(city, 0) + 1
    top_city = max(by_city.items(), key=lambda item: item[1])[0] if by_city else None
    return {
        "articleCount": len(articles),
        "cityCount": len(by_city),
        "warningCount": len([row for row in articles if row.get("severity") == "warning"]),
        "topCity": top_city,
    }


def build_weather_news_payload(
    ctx: WeatherNewsContext,
    *,
    limit: int = DEFAULT_NEWS_LIMIT,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    cities = [*load_weather_cities(), *WEATHER_TOPIC_QUERIES]
    articles: List[Dict[str, Any]] = []
    source_states: Dict[str, str] = {}

    def fetch_city(city: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]], Exception | None]:
        try:
            return city, fetch_google_news_rss(dependencies, city), None
        except Exception as exc:
            return city, [], exc

    worker_count = max(
        1,
        min(
            8,
            int(getattr(dependencies.settings, "weather_news_fetch_workers", 4) or 4),
        ),
    )
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="weather-news") as executor:
        results = executor.map(fetch_city, cities)
        for city, city_articles, error in results:
            if error is None:
                articles.extend(city_articles)
                source_states[str(city["city_id"])] = "ok" if city_articles else "empty"
                continue
            source_states[str(city["city_id"])] = "error"
            logger = getattr(dependencies.application, "logger", None)
            if logger is not None:
                logger.exception("weather news rss fetch failed city=%s error=%s", city.get("city"), error)
    ranked = _dedupe_rank(articles)
    status = "ok" if ranked else ("degraded" if any(value == "error" for value in source_states.values()) else "empty")
    if ranked and any(value == "error" for value in source_states.values()):
        status = "degraded"
    return {
        "generatedAt": _utc_now_iso(dependencies),
        "source": "Google News RSS",
        "sourceUrl": getattr(dependencies.settings, "google_news_rss_url", "https://news.google.com/rss/search"),
        "status": status,
        "sources": source_states,
        "summary": build_news_summary(ranked),
        "items": ranked[: max(1, int(limit or DEFAULT_NEWS_LIMIT))],
    }


def _empty_payload(
    dependencies: WeatherNewsDependencies,
    *,
    status: str = "warming",
) -> Dict[str, Any]:
    return {
        "generatedAt": _utc_now_iso(dependencies),
        "source": "Google News RSS",
        "sourceUrl": getattr(dependencies.settings, "google_news_rss_url", "https://news.google.com/rss/search"),
        "status": status,
        "sources": {},
        "summary": {"articleCount": 0, "cityCount": 0, "warningCount": 0, "topCity": None},
        "items": [],
    }


def normalize_weather_news_payload(
    payload: Any,
    *,
    ctx: WeatherNewsContext,
    limit: int = DEFAULT_NEWS_LIMIT,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    if not isinstance(payload, dict):
        return _empty_payload(dependencies, status="invalid")
    result = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    items = [item for item in (result.get("items") or []) if isinstance(item, dict)]
    result["items"] = items[: max(1, min(int(limit or DEFAULT_NEWS_LIMIT), 80))]
    result["summary"] = result.get("summary") if isinstance(result.get("summary"), dict) else build_news_summary(result["items"])
    result["generatedAt"] = str(result.get("generatedAt") or _utc_now_iso(dependencies))
    result["status"] = str(result.get("status") or ("ok" if result["items"] else "warming"))
    result["source"] = str(result.get("source") or "Google News RSS")
    result["sourceUrl"] = str(
        result.get("sourceUrl")
        or getattr(
            dependencies.settings,
            "google_news_rss_url",
            "https://news.google.com/rss/search",
        )
    )
    return result


def _with_cache_mode(payload: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    return {**payload, "cacheMode": cache_mode}


def _read_seeded_snapshot(
    dependencies: WeatherNewsDependencies,
    *,
    ttl_seconds: int,
) -> Optional[Dict[str, Any]]:
    reader = dependencies.get_cached_json
    if reader is not None:
        payload = reader(WEATHER_NEWS_SNAPSHOT_NAMESPACE, WEATHER_NEWS_CACHE_KEY)
        if isinstance(payload, dict):
            return _with_cache_mode(payload, "redis-seed")
    store = dependencies.snapshot_store
    if store is None:
        return None
    payload = store.get(WEATHER_NEWS_SNAPSHOT_NAMESPACE, WEATHER_NEWS_CACHE_KEY)
    if isinstance(payload, dict):
        return _with_cache_mode(payload, "sqlite-seed")
    stale = store.get_stale(WEATHER_NEWS_SNAPSHOT_NAMESPACE, WEATHER_NEWS_CACHE_KEY)
    if isinstance(stale, dict):
        return _with_cache_mode(stale, "stale-seed")
    return None


def _store_live(
    dependencies: WeatherNewsDependencies,
    payload: Dict[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    store = dependencies.snapshot_store
    if store is not None:
        store.set(WEATHER_NEWS_SNAPSHOT_NAMESPACE, WEATHER_NEWS_CACHE_KEY, payload, ttl_seconds)
    setter = dependencies.set_cached_json
    if setter is not None:
        setter(WEATHER_NEWS_SNAPSHOT_NAMESPACE, WEATHER_NEWS_CACHE_KEY, payload, ttl_seconds)


def _schedule_live_refresh(
    ctx: WeatherNewsContext,
    *,
    limit: int,
    ttl_seconds: int,
    reason: str,
) -> bool:
    dependencies = _dependencies(ctx)
    refresh_key = f"{WEATHER_NEWS_SNAPSHOT_NAMESPACE}:{WEATHER_NEWS_CACHE_KEY}"
    with _LIVE_REFRESH_LOCK:
        if refresh_key in _LIVE_REFRESHING:
            return False
        _LIVE_REFRESHING.add(refresh_key)

    def refresh() -> None:
        logger = getattr(dependencies.application, "logger", None)
        try:
            build_limit = max(limit, int(getattr(dependencies.settings, "weather_news_limit", 40) or 40))
            payload = _with_cache_mode(build_weather_news_payload(dependencies, limit=build_limit), "live-build")
            if payload.get("items"):
                _store_live(dependencies, payload, ttl_seconds=ttl_seconds)
                if logger is not None and hasattr(logger, "info"):
                    logger.info("weather news async refresh stored reason=%s items=%s", reason, len(payload.get("items") or []))
            elif logger is not None and hasattr(logger, "warning"):
                logger.warning("weather news async refresh skipped empty payload reason=%s", reason)
        except Exception:
            if logger is not None:
                logger.exception("weather news async refresh failed reason=%s", reason)
        finally:
            with _LIVE_REFRESH_LOCK:
                _LIVE_REFRESHING.discard(refresh_key)

    thread = threading.Thread(target=refresh, name="weather-news-refresh", daemon=True)
    thread.start()
    return True


def get_weather_news_snapshot(
    ctx: WeatherNewsContext,
    limit: int = DEFAULT_NEWS_LIMIT,
    *,
    allow_live_build: bool = True,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    ttl_seconds = max(300, int(getattr(dependencies.settings, "weather_news_ttl_seconds", 900) or 900))
    seeded = _read_seeded_snapshot(dependencies, ttl_seconds=ttl_seconds)
    if seeded is not None:
        if allow_live_build and seeded.get("cacheMode") == "stale-seed":
            _schedule_live_refresh(dependencies, limit=limit, ttl_seconds=ttl_seconds, reason="stale-seed")
        return normalize_weather_news_payload(seeded, ctx=dependencies, limit=limit)
    if not allow_live_build:
        return normalize_weather_news_payload(
            {**_empty_payload(dependencies), "cacheMode": "seed-miss"},
            ctx=dependencies,
            limit=limit,
        )
    scheduled = _schedule_live_refresh(dependencies, limit=limit, ttl_seconds=ttl_seconds, reason="seed-miss")
    return normalize_weather_news_payload(
        {
            **_empty_payload(dependencies),
            "cacheMode": "seed-miss-refreshing" if scheduled else "seed-miss-refresh-inflight",
        },
        ctx=dependencies,
        limit=limit,
    )
