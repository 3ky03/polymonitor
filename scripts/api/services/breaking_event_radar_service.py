from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import quote

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
)


PANEL_ID = "breaking-event-radar"
BREAKING_EVENT_RADAR_SNAPSHOT_NAMESPACE = "snapshot:evidence:breaking-event-radar"
BREAKING_EVENT_RADAR_CACHE_KEY = "panel-v1"
DEFAULT_LIMIT = 12
DEFAULT_TTL_SECONDS = 300
GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
WIKIMEDIA_PAGEVIEWS_BASE_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia.org/all-access/user"

_LIVE_REFRESH_LOCK = threading.Lock()
_LIVE_REFRESHING: set[str] = set()

DEFAULT_TOPIC_SEEDS: List[Dict[str, Any]] = [
    {
        "id": "iran-geopolitics",
        "topic": "geopolitics",
        "entity": "Iran",
        "country": "Iran",
        "query": '(Iran OR Tehran) (missile OR nuclear OR sanctions OR attack OR conflict)',
        "wikiTitles": ["Iran", "Iran_nuclear_deal_framework"],
    },
    {
        "id": "us-politics",
        "topic": "politics",
        "entity": "US Politics",
        "country": "United States",
        "query": '("United States" OR Washington) (election OR senate OR congress OR president OR campaign)',
        "wikiTitles": ["Politics_of_the_United_States", "2026_United_States_elections"],
    },
    {
        "id": "geopolitical-risk",
        "topic": "geopolitics",
        "entity": "Geopolitical Risk",
        "country": "Global",
        "query": '(war OR ceasefire OR invasion OR sanctions OR military) (risk OR crisis OR talks OR strike)',
        "wikiTitles": ["Geopolitics", "International_sanctions"],
    },
    {
        "id": "crypto-policy",
        "topic": "crypto",
        "entity": "Crypto Policy",
        "country": "Global",
        "query": '(bitcoin OR crypto OR stablecoin OR ethereum) (ETF OR regulation OR SEC OR treasury)',
        "wikiTitles": ["Cryptocurrency", "Bitcoin"],
    },
    {
        "id": "extreme-weather",
        "topic": "weather",
        "entity": "Extreme Weather",
        "country": "Global",
        "query": '(hurricane OR flood OR wildfire OR heatwave OR storm) (warning OR damage OR forecast OR emergency)',
        "wikiTitles": ["Extreme_weather", "Tropical_cyclone"],
    },
]


@dataclass(frozen=True)
class BreakingEventRadarDependencies:
    utc_now_iso: Callable[..., Any] | None
    settings: Any
    http_json_get: Callable[..., Any] | None
    search_markets: Callable[..., Any] | None
    get_cached_json: Callable[..., Any] | None
    snapshot_store: Any
    set_cached_json: Callable[..., Any] | None
    application: Any

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> BreakingEventRadarDependencies:
        return cls(
            utc_now_iso=resolve_optional_service_callable(
                context,
                "utc_now_iso",
            ),
            settings=resolve_optional_service_value(
                context,
                "SETTINGS",
            ),
            http_json_get=resolve_optional_service_callable(
                context,
                "http_json_get",
            ),
            search_markets=resolve_optional_service_callable(
                context,
                "search_markets",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
            application=resolve_optional_service_value(
                context,
                "app",
            ),
        )


def _utc_now_iso(
    dependencies: BreakingEventRadarDependencies | None = None,
) -> str:
    if dependencies is not None and dependencies.utc_now_iso is not None:
        return dependencies.utc_now_iso()
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [
        text.replace("Z", "+00:00"),
        text[:4] + "-" + text[4:6] + "-" + text[6:8] + "T" + text[8:10] + ":" + text[10:12] + ":" + text[12:14] + "+00:00"
        if re.fullmatch(r"\d{14}", text)
        else "",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _timestamp(value: Any) -> float:
    parsed = _parse_iso(value)
    return parsed.timestamp() if parsed else 0.0


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _load_topic_seeds() -> List[Dict[str, Any]]:
    raw = os.environ.get("POLYDATA_BREAKING_EVENT_TOPICS_JSON", "").strip()
    if raw:
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                rows = [row for row in decoded if isinstance(row, dict) and row.get("query")]
                if rows:
                    return rows
        except Exception:
            pass
    return DEFAULT_TOPIC_SEEDS


def breaking_event_topic_ids() -> List[str]:
    return [
        str(seed.get("id") or "").strip()
        for seed in _load_topic_seeds()
        if str(seed.get("id") or "").strip()
    ]


def _gdelt_url(dependencies: BreakingEventRadarDependencies) -> str:
    return str(
        os.environ.get("POLYDATA_BREAKING_EVENT_GDELT_DOC_API_URL")
        or getattr(
            dependencies.settings,
            "breaking_event_gdelt_doc_api_url",
            "",
        )
        or GDELT_DOC_API_URL
    ).strip()


def _wikimedia_base_url(
    dependencies: BreakingEventRadarDependencies,
) -> str:
    return str(
        os.environ.get("POLYDATA_BREAKING_EVENT_WIKIMEDIA_PAGEVIEWS_BASE_URL")
        or getattr(
            dependencies.settings,
            "breaking_event_wikimedia_pageviews_base_url",
            "",
        )
        or WIKIMEDIA_PAGEVIEWS_BASE_URL
    ).rstrip("/")


def _fetch_gdelt_articles(
    dependencies: BreakingEventRadarDependencies,
    seed: Dict[str, Any],
    *,
    max_records: int,
) -> List[Dict[str, Any]]:
    if dependencies.http_json_get is None:
        raise RuntimeError("http_json_get helper missing")
    timeout = max(
        2,
        min(
            int(os.environ.get("POLYDATA_BREAKING_EVENT_GDELT_TIMEOUT_SECONDS", "20") or 20),
            60,
        ),
    )
    payload = dependencies.http_json_get(
        _gdelt_url(dependencies),
        params={
            "query": str(seed.get("query") or ""),
            "mode": "artlist",
            "format": "json",
            "maxrecords": max(1, min(max_records, 50)),
            "sort": "hybridrel",
        },
        timeout=timeout,
        headers={"Accept": "application/json", "User-Agent": "polydata-breaking-event-radar/1.0"},
    )
    articles = payload.get("articles") if isinstance(payload, dict) else []
    return [row for row in articles if isinstance(row, dict)]


def _source_failure(exc: Exception) -> tuple[str, str]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code == 429:
        return "rate-limited", "rate-limited"
    if isinstance(status_code, int):
        return "error", f"http-{status_code}"
    error_name = exc.__class__.__name__
    if "timeout" in error_name.lower():
        return "timeout", "timeout"
    return "error", error_name


def _fetch_pageviews(
    dependencies: BreakingEventRadarDependencies,
    title: str,
    *,
    days: int = 7,
) -> Dict[str, Any]:
    if dependencies.http_json_get is None:
        raise RuntimeError("http_json_get helper missing")
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(2, days))
    encoded_title = quote(str(title or "").replace(" ", "_"), safe="")
    url = f"{_wikimedia_base_url(dependencies)}/{encoded_title}/daily/{start:%Y%m%d}/{end:%Y%m%d}"
    timeout = max(2, min(int(os.environ.get("POLYDATA_BREAKING_EVENT_WIKIMEDIA_TIMEOUT_SECONDS", "4") or 4), 12))
    payload = dependencies.http_json_get(
        url,
        timeout=timeout,
        headers={
            "Accept": "application/json",
            "User-Agent": "polydata-breaking-event-radar/1.0",
        },
    )
    items = payload.get("items") if isinstance(payload, dict) else []
    views = [int(row.get("views") or 0) for row in items if isinstance(row, dict)]
    latest = views[-1] if views else 0
    baseline = sum(views[:-1]) / max(1, len(views[:-1])) if len(views) > 1 else 0
    delta = latest - baseline
    pct = (delta / baseline * 100) if baseline else (100.0 if latest else 0.0)
    return {"title": title, "latestViews": latest, "baselineViews": round(baseline, 2), "deltaViews": round(delta, 2), "deltaPct": round(pct, 2)}


def _article_title(article: Dict[str, Any]) -> str:
    return str(article.get("title") or article.get("seendate") or "Breaking source").strip()


def _article_time(article: Dict[str, Any]) -> str:
    parsed = _parse_iso(article.get("seendate") or article.get("datetime") or article.get("date"))
    return parsed.isoformat().replace("+00:00", "Z") if parsed else ""


def _build_event_item(
    dependencies: BreakingEventRadarDependencies,
    seed: Dict[str, Any],
    articles: List[Dict[str, Any]],
    pageviews: List[Dict[str, Any]],
) -> Dict[str, Any]:
    domains = sorted({str(row.get("domain") or row.get("sourceCommonName") or "").strip() for row in articles if row.get("domain") or row.get("sourceCommonName")})
    countries = sorted({str(row.get("sourcecountry") or row.get("country") or "").strip() for row in articles if row.get("sourcecountry") or row.get("country")})
    latest_article = max(articles, key=lambda row: _timestamp(row.get("seendate") or row.get("datetime") or row.get("date")), default={})
    mention_count = len(articles)
    wiki_delta = sum(float(row.get("deltaPct") or 0) for row in pageviews)
    source_diversity = len(domains)
    country_spread = len(countries)
    velocity = min(100, int(mention_count * 5 + source_diversity * 8 + country_spread * 3 + max(0.0, wiki_delta) * 0.15))
    confidence = min(0.98, 0.35 + min(mention_count, 20) * 0.02 + min(source_diversity, 8) * 0.04 + (0.08 if pageviews else 0.0))
    query = str(seed.get("entity") or seed.get("query") or "")
    markets = _match_markets(
        dependencies,
        query,
        topic=str(seed.get("topic") or ""),
        limit=4,
    )
    source_url = str(latest_article.get("url") or latest_article.get("shareurl") or GDELT_DOC_API_URL)
    return {
        "id": _stable_id(seed.get("id"), _article_title(latest_article), source_url),
        "topicId": str(seed.get("id") or ""),
        "topic": str(seed.get("topic") or "breaking"),
        "entity": str(seed.get("entity") or seed.get("id") or "Breaking Event"),
        "country": str(seed.get("country") or "Global"),
        "team": None,
        "title": _article_title(latest_article) if latest_article else str(seed.get("entity") or "Source warming"),
        "summary": str(latest_article.get("description") or latest_article.get("summary") or seed.get("query") or "")[:260],
        "eventTime": _article_time(latest_article)
        or _utc_now_iso(dependencies),
        "source": str(latest_article.get("domain") or latest_article.get("sourceCommonName") or "GDELT"),
        "sourceUrl": source_url,
        "evidenceType": "WIRE",
        "mentionCount": mention_count,
        "mentionCount15m": mention_count,
        "mentionCount1h": mention_count,
        "mentionCount24h": mention_count,
        "velocityScore": velocity,
        "sourceDiversity": source_diversity,
        "countrySpread": country_spread,
        "tone": _safe_float(latest_article.get("tone")),
        "wikiPageviewDelta": round(wiki_delta, 2),
        "confidence": round(confidence, 2),
        "severity": "alert" if velocity >= 75 else "watch" if velocity >= 45 else "normal",
        "tags": [str(seed.get("topic") or "breaking"), "wire" if articles else "proxy"],
        "relatedPolymarketMarketIds": [market.get("marketId") for market in markets if market.get("marketId")],
        "markets": markets,
        "evidence": {
            "articles": [
                {
                    "title": _article_title(row),
                    "source": row.get("domain") or row.get("sourceCommonName"),
                    "url": row.get("url") or row.get("shareurl"),
                    "publishedAt": _article_time(row),
                }
                for row in articles[:5]
            ],
            "pageviews": pageviews,
        },
    }


def _safe_float(value: Any) -> Optional[float]:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _match_markets(
    dependencies: BreakingEventRadarDependencies,
    query: str,
    *,
    topic: str,
    limit: int,
) -> List[Dict[str, Any]]:
    if dependencies.search_markets is None or not query:
        return []
    try:
        rows = dependencies.search_markets(query, limit=limit)
    except Exception:
        return []
    markets: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        market_id = row.get("id") or row.get("marketId")
        title = row.get("question") or row.get("title") or row.get("slug")
        markets.append(
            {
                "marketId": market_id,
                "slug": row.get("slug"),
                "question": title,
                "matchScore": 0.62,
                "matchReasons": [reason for reason in ("entity", "topic" if topic else "") if reason],
            }
        )
    return markets


def _summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    alert_count = len([item for item in items if item.get("severity") == "alert"])
    watch_count = len([item for item in items if item.get("severity") == "watch"])
    top = items[0] if items else {}
    return {
        "total": len(items),
        "alerts": alert_count,
        "watch": watch_count,
        "topEntity": top.get("entity"),
        "topVelocity": top.get("velocityScore"),
    }


def build_breaking_event_radar_payload(
    ctx: Mapping[str, Any],
    *,
    limit: int = DEFAULT_LIMIT,
    topic_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    return _build_breaking_event_radar_payload(
        BreakingEventRadarDependencies.from_context(ctx),
        limit=limit,
        topic_ids=topic_ids,
    )


def _build_breaking_event_radar_payload(
    dependencies: BreakingEventRadarDependencies,
    *,
    limit: int,
    topic_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    max_records = max(5, min(int(os.environ.get("POLYDATA_BREAKING_EVENT_GDELT_MAX_RECORDS", "20") or 20), 50))
    gdelt_min_interval_seconds = max(
        0.0,
        min(
            float(
                os.environ.get(
                    "POLYDATA_BREAKING_EVENT_GDELT_MIN_INTERVAL_SECONDS",
                    "10",
                )
                or 5
            ),
            30.0,
        ),
    )
    wikimedia_enabled = str(os.environ.get("POLYDATA_BREAKING_EVENT_WIKIMEDIA_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
    max_wiki_titles = max(0, min(int(os.environ.get("POLYDATA_BREAKING_EVENT_WIKIMEDIA_TITLES_PER_TOPIC", "1") or 1), 4))
    seeds = _load_topic_seeds()
    requested_topic_ids = {
        str(topic_id or "").strip()
        for topic_id in (topic_ids or [])
        if str(topic_id or "").strip()
    }
    if requested_topic_ids:
        seeds = [
            seed
            for seed in seeds
            if str(seed.get("id") or "").strip() in requested_topic_ids
        ]
    items: List[Dict[str, Any]] = []
    sources: Dict[str, Any] = {"gdelt": {"status": "empty", "count": 0}, "wikimedia": {"status": "empty", "count": 0}}
    errors: List[str] = []
    gdelt_failure_statuses: List[str] = []
    last_gdelt_attempt_at: Optional[float] = None
    for seed in seeds:
        articles: List[Dict[str, Any]] = []
        pageviews: List[Dict[str, Any]] = []
        if last_gdelt_attempt_at is not None and gdelt_min_interval_seconds > 0:
            wait_seconds = gdelt_min_interval_seconds - (
                time.monotonic() - last_gdelt_attempt_at
            )
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        last_gdelt_attempt_at = time.monotonic()
        try:
            articles = _fetch_gdelt_articles(
                dependencies,
                seed,
                max_records=max_records,
            )
            sources["gdelt"]["count"] += len(articles)
        except Exception as exc:
            failure_status, failure_label = _source_failure(exc)
            gdelt_failure_statuses.append(failure_status)
            sources["gdelt"]["errorCount"] = int(
                sources["gdelt"].get("errorCount") or 0
            ) + 1
            errors.append(f"gdelt:{seed.get('id')}:{failure_label}")
        if wikimedia_enabled and max_wiki_titles > 0:
            for title in (seed.get("wikiTitles") or [])[:max_wiki_titles]:
                try:
                    pageviews.append(
                        _fetch_pageviews(dependencies, str(title))
                    )
                    sources["wikimedia"]["count"] += 1
                    sources["wikimedia"]["status"] = "ok"
                except Exception as exc:
                    if sources["wikimedia"]["status"] != "ok":
                        sources["wikimedia"]["status"] = "error"
                    errors.append(f"wikimedia:{title}:{exc.__class__.__name__}")
        elif not wikimedia_enabled:
            sources["wikimedia"]["status"] = "disabled"
        if articles or pageviews:
            items.append(
                _build_event_item(
                    dependencies,
                    seed,
                    articles,
                    pageviews,
                )
            )
    if gdelt_failure_statuses:
        sources["gdelt"]["status"] = (
            "partial"
            if sources["gdelt"]["count"]
            else gdelt_failure_statuses[-1]
        )
    elif sources["gdelt"]["count"]:
        sources["gdelt"]["status"] = "ok"
    items.sort(key=lambda row: (int(row.get("velocityScore") or 0), str(row.get("eventTime") or "")), reverse=True)
    limited = items[: max(1, min(int(limit or DEFAULT_LIMIT), 80))]
    status = "ok" if limited and not errors else "degraded" if limited else "empty" if not errors else "degraded"
    return {
        "panelId": PANEL_ID,
        "generatedAt": _utc_now_iso(dependencies),
        "status": status,
        "cacheMode": "live-build",
        "freshness": "live" if status == "ok" else "degraded" if limited else "warming",
        "source": "GDELT + Wikimedia Pageviews",
        "sourceUrl": _gdelt_url(dependencies),
        "sources": sources,
        "summary": _summary(limited),
        "items": limited,
        "errors": errors[:12],
    }


def _topic_id_for_item(item: Dict[str, Any]) -> str:
    explicit = str(item.get("topicId") or "").strip()
    if explicit:
        return explicit
    entity = str(item.get("entity") or "").strip().lower()
    topic = str(item.get("topic") or "").strip()
    for seed in _load_topic_seeds():
        if entity and entity == str(seed.get("entity") or "").strip().lower():
            return str(seed.get("id") or topic).strip()
    return topic


def merge_breaking_event_radar_payloads(
    previous: Dict[str, Any],
    current: Dict[str, Any],
    *,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    allowed_topic_ids = set(breaking_event_topic_ids())
    previous_items = {
        _topic_id_for_item(item): item
        for item in (previous.get("items") or [])
        if isinstance(item, dict)
        and _topic_id_for_item(item) in allowed_topic_ids
    }
    current_items = {
        _topic_id_for_item(item): item
        for item in (current.get("items") or [])
        if isinstance(item, dict)
        and _topic_id_for_item(item) in allowed_topic_ids
    }
    merged_items = dict(previous_items)
    for topic_id, item in current_items.items():
        articles = (
            (item.get("evidence") or {}).get("articles")
            if isinstance(item.get("evidence"), dict)
            else []
        )
        previous_item = previous_items.get(topic_id)
        previous_articles = (
            (previous_item.get("evidence") or {}).get("articles")
            if isinstance(previous_item, dict)
            and isinstance(previous_item.get("evidence"), dict)
            else []
        )
        if articles or not previous_articles:
            merged_items[topic_id] = item
        else:
            merged_items[topic_id] = {
                **previous_item,
                "evidenceFreshness": "preserved",
            }
    ranked = sorted(
        merged_items.values(),
        key=lambda row: (
            int(row.get("velocityScore") or 0),
            str(row.get("eventTime") or ""),
        ),
        reverse=True,
    )[: max(1, min(int(limit or DEFAULT_LIMIT), 80))]
    return {
        **current,
        "items": ranked,
        "summary": _summary(ranked),
    }


def _empty_payload(
    dependencies: BreakingEventRadarDependencies,
    *,
    status: str = "warming",
    cache_mode: str = "warming",
) -> Dict[str, Any]:
    return {
        "panelId": PANEL_ID,
        "generatedAt": _utc_now_iso(dependencies),
        "status": status,
        "cacheMode": cache_mode,
        "freshness": "warming",
        "source": "GDELT + Wikimedia Pageviews",
        "sourceUrl": _gdelt_url(dependencies),
        "sources": {},
        "summary": _summary([]),
        "items": [],
        "errors": [],
    }


def normalize_breaking_event_radar_payload(
    payload: Any,
    *,
    ctx: Mapping[str, Any],
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    return _normalize_breaking_event_radar_payload(
        payload,
        dependencies=BreakingEventRadarDependencies.from_context(ctx),
        limit=limit,
    )


def _normalize_breaking_event_radar_payload(
    payload: Any,
    *,
    dependencies: BreakingEventRadarDependencies,
    limit: int,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_payload(
            dependencies,
            status="invalid",
            cache_mode="invalid",
        )
    result = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    result["items"] = items[: max(1, min(int(limit or DEFAULT_LIMIT), 80))]
    result["panelId"] = str(result.get("panelId") or PANEL_ID)
    result["generatedAt"] = str(
        result.get("generatedAt") or _utc_now_iso(dependencies)
    )
    result["status"] = str(result.get("status") or ("ok" if result["items"] else "warming"))
    result["cacheMode"] = str(result.get("cacheMode") or "seeded")
    result["freshness"] = str(result.get("freshness") or ("live" if result["status"] == "ok" else "degraded"))
    result["source"] = str(result.get("source") or "GDELT + Wikimedia Pageviews")
    result["sourceUrl"] = str(
        result.get("sourceUrl") or _gdelt_url(dependencies)
    )
    result["sources"] = result.get("sources") if isinstance(result.get("sources"), dict) else {}
    result["summary"] = result.get("summary") if isinstance(result.get("summary"), dict) else _summary(result["items"])
    result["summary"] = {**_summary(result["items"]), **result["summary"]}
    result["errors"] = [str(error) for error in result.get("errors") or []]
    return result


def _with_cache_mode(payload: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    freshness = "stale" if "stale" in cache_mode else "seeded"
    return {**payload, "cacheMode": cache_mode, "freshness": payload.get("freshness") or freshness}


def _read_seeded_snapshot(
    dependencies: BreakingEventRadarDependencies,
) -> Optional[Dict[str, Any]]:
    if dependencies.get_cached_json is not None:
        payload = dependencies.get_cached_json(
            BREAKING_EVENT_RADAR_SNAPSHOT_NAMESPACE,
            BREAKING_EVENT_RADAR_CACHE_KEY,
        )
        if isinstance(payload, dict):
            return _with_cache_mode(payload, "redis-seed")
    store = dependencies.snapshot_store
    if store is None:
        return None
    payload = store.get(BREAKING_EVENT_RADAR_SNAPSHOT_NAMESPACE, BREAKING_EVENT_RADAR_CACHE_KEY)
    if isinstance(payload, dict):
        return _with_cache_mode(payload, "sqlite-seed")
    stale = store.get_stale(BREAKING_EVENT_RADAR_SNAPSHOT_NAMESPACE, BREAKING_EVENT_RADAR_CACHE_KEY)
    if isinstance(stale, dict):
        return _with_cache_mode(stale, "stale-seed")
    return None


def _store_live(
    dependencies: BreakingEventRadarDependencies,
    payload: Dict[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    store = dependencies.snapshot_store
    if store is not None:
        store.set(BREAKING_EVENT_RADAR_SNAPSHOT_NAMESPACE, BREAKING_EVENT_RADAR_CACHE_KEY, payload, ttl_seconds)
    if dependencies.set_cached_json is not None:
        dependencies.set_cached_json(
            BREAKING_EVENT_RADAR_SNAPSHOT_NAMESPACE,
            BREAKING_EVENT_RADAR_CACHE_KEY,
            payload,
            ttl_seconds,
        )


def _schedule_live_refresh(
    dependencies: BreakingEventRadarDependencies,
    *,
    limit: int,
    ttl_seconds: int,
    reason: str,
) -> bool:
    refresh_key = f"{BREAKING_EVENT_RADAR_SNAPSHOT_NAMESPACE}:{BREAKING_EVENT_RADAR_CACHE_KEY}"
    with _LIVE_REFRESH_LOCK:
        if refresh_key in _LIVE_REFRESHING:
            return False
        _LIVE_REFRESHING.add(refresh_key)

    def refresh() -> None:
        logger = getattr(dependencies.application, "logger", None)
        try:
            payload = {
                **_build_breaking_event_radar_payload(
                    dependencies,
                    limit=limit,
                ),
                "cacheMode": "live-build",
            }
            if payload.get("items"):
                _store_live(
                    dependencies,
                    payload,
                    ttl_seconds=ttl_seconds,
                )
            elif logger is not None and hasattr(logger, "warning"):
                logger.warning("breaking event radar refresh skipped empty payload reason=%s", reason)
        except Exception:
            if logger is not None:
                logger.exception("breaking event radar refresh failed reason=%s", reason)
        finally:
            with _LIVE_REFRESH_LOCK:
                _LIVE_REFRESHING.discard(refresh_key)

    thread = threading.Thread(target=refresh, name="breaking-event-radar-refresh", daemon=True)
    thread.start()
    return True


def get_breaking_event_radar_snapshot(
    ctx: Mapping[str, Any],
    limit: int = DEFAULT_LIMIT,
    *,
    allow_live_build: bool = True,
) -> Dict[str, Any]:
    dependencies = BreakingEventRadarDependencies.from_context(ctx)
    ttl_seconds = max(120, int(os.environ.get("POLYDATA_BREAKING_EVENT_RADAR_TTL_SECONDS", DEFAULT_TTL_SECONDS) or DEFAULT_TTL_SECONDS))
    seeded = _read_seeded_snapshot(dependencies)
    if seeded is not None:
        if allow_live_build and seeded.get("cacheMode") == "stale-seed":
            _schedule_live_refresh(
                dependencies,
                limit=limit,
                ttl_seconds=ttl_seconds,
                reason="stale-seed",
            )
        return _normalize_breaking_event_radar_payload(
            seeded,
            dependencies=dependencies,
            limit=limit,
        )
    if not allow_live_build:
        return _normalize_breaking_event_radar_payload(
            _empty_payload(dependencies, cache_mode="seed-miss"),
            dependencies=dependencies,
            limit=limit,
        )
    scheduled = _schedule_live_refresh(
        dependencies,
        limit=limit,
        ttl_seconds=ttl_seconds,
        reason="seed-miss",
    )
    mode = "seed-miss-refreshing" if scheduled else "seed-miss-refresh-inflight"
    return _normalize_breaking_event_radar_payload(
        _empty_payload(dependencies, cache_mode=mode),
        dependencies=dependencies,
        limit=limit,
    )
