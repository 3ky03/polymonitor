from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from api.config import PROJECT_ROOT
from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
)
from api.services import trusted_hls_sources, youtube_live_probe_service


PANEL_ID = "market-tv-wire"
MARKET_TV_WIRE_SNAPSHOT_NAMESPACE = "snapshot:content:market-tv-wire"
MARKET_TV_WIRE_CACHE_KEY = "panel-v1"
DEFAULT_MARKET_TV_WIRE_LIMIT = 24
MARKET_TV_WIRE_TTL_SECONDS = 900
MARKET_TV_WIRE_SEED_ITEM_LIMIT = 240
MARKET_YOUTUBE_CHANNELS_PANEL_ID = "market-youtube-channels"
MARKET_YOUTUBE_CHANNELS_CACHE_KEY = "panel-v1"
MANIFEST_PATH = PROJECT_ROOT / "scripts" / "data" / "live_video_sources.json"

CATEGORY_ORDER = (
    "breaking",
    "politics",
    "sports",
    "crypto",
    "esports",
    "iran",
    "finance",
    "geopolitics",
    "tech",
    "culture",
    "economy",
    "weather",
    "elections",
    "macro",
    "geo",
    "news",
    "other",
)
CATEGORY_LABELS = {
    "breaking": "Breaking",
    "politics": "Politics",
    "macro": "Macro",
    "geo": "Geo",
    "geopolitics": "Geopolitics",
    "weather": "Weather",
    "sports": "Sports",
    "crypto": "Crypto",
    "esports": "Esports",
    "iran": "Iran",
    "finance": "Finance",
    "tech": "Tech",
    "culture": "Culture",
    "economy": "Economy",
    "elections": "Elections",
    "news": "News",
    "other": "Other",
}
CATEGORY_BASE_SCORE = {
    "breaking": 94,
    "politics": 90,
    "macro": 92,
    "geo": 88,
    "geopolitics": 89,
    "weather": 84,
    "sports": 80,
    "crypto": 78,
    "esports": 76,
    "iran": 88,
    "finance": 91,
    "tech": 80,
    "culture": 70,
    "economy": 90,
    "elections": 89,
    "news": 74,
    "other": 60,
}
YOUTUBE_TOPIC_ALIASES = {
    "breaking": {"breaking", "news", "world", "disaster", "courts", "public-safety"},
    "politics": {"politics", "elections", "congress", "white-house", "policy", "government", "courts", "debates", "campaign"},
    "sports": {"sports", "nba", "nfl", "mlb", "soccer", "world-cup", "fifa", "f1", "grand-prix", "playoffs", "basketball", "injury"},
    "crypto": {"crypto", "bitcoin", "ethereum", "defi", "etf", "regulation", "btc", "eth"},
    "esports": {"esports", "gaming", "valorant", "tournament", "riot"},
    "iran": {"iran", "nuclear", "sanctions", "middle-east", "conflict", "diplomacy"},
    "finance": {"finance", "stocks", "markets", "earnings", "rates", "economy", "fed", "business"},
    "geopolitics": {"geopolitics", "conflict", "war", "diplomacy", "middle-east", "china", "india", "asia", "europe", "turkey", "gulf", "un"},
    "tech": {"tech", "ai", "startups", "ipo", "platforms", "consumer", "regulation"},
    "culture": {"culture", "climate", "public-interest", "consumer", "world"},
    "economy": {"economy", "business", "macro", "policy", "markets", "consumer", "finance"},
    "weather": {"weather", "hurricane", "storm", "flood", "temperature", "forecast", "climate", "disaster"},
    "elections": {"elections", "election", "campaign", "vote", "debates", "congress", "white-house"},
    "macro": {"macro", "fed", "rates", "inflation", "economy", "markets", "finance", "stocks", "oil", "commodities"},
    "geo": {"geo", "space", "nasa", "launch", "iss", "science"},
    "news": {"news", "breaking", "world", "politics", "elections", "weather", "policy"},
}
IPTV_CATEGORY_FEEDS = (
    ("news", "https://iptv-org.github.io/iptv/categories/news.m3u"),
    ("macro", "https://iptv-org.github.io/iptv/categories/business.m3u"),
    ("sports", "https://iptv-org.github.io/iptv/categories/sports.m3u"),
    ("weather", "https://iptv-org.github.io/iptv/categories/weather.m3u"),
)
ALLOWED_CATEGORIES = set(CATEGORY_ORDER)
ALLOWED_SOURCE_TYPES = {"hls", "youtube", "external", "timelapse"}
ALLOWED_SOURCE_ROLES = {"channel", "visual"}
NOISE_TERMS = ("xxx", "adult", "shopping", "religion", "music", "kids")
EXTINF_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
SLUG_RE = re.compile(r"[^a-z0-9]+")
YOUTUBE_PROBE_ENABLED_ENV = "POLYDATA_MARKET_TV_YOUTUBE_PROBE_ENABLED"
HLS_PROBE_ENABLED_ENV = "POLYDATA_MARKET_TV_HLS_PROBE_ENABLED"
HLS_PROBE_TIMEOUT_ENV = "POLYDATA_MARKET_TV_HLS_PROBE_TIMEOUT_SECONDS"
HLS_PROBE_WORKERS_ENV = "POLYDATA_MARKET_TV_HLS_PROBE_WORKERS"
YOUTUBE_RSS_FALLBACK_ENABLED_ENV = "POLYDATA_MARKET_TV_YOUTUBE_RSS_FALLBACK_ENABLED"
YOUTUBE_RSS_REFRESH_EXISTING_LIMIT_ENV = "POLYDATA_MARKET_YOUTUBE_RSS_REFRESH_EXISTING_LIMIT"
YOUTUBE_FALLBACK_VIDEO_IDS = {
    "nasa-live-youtube": "FuuC4dpSQ1M",
    "sky-news-live": "OkExVwVzrUY",
    "dw-news-live": "LuKwFajn37U",
    "aljazeera-live": "gCNeDWCI0vo",
    "fox-weather-live": "wt6SIE7BXS8",
    "france24-english-youtube": "HvZt-nh9sGg",
    "cnbc-television-youtube": "9NyxcX3rhQs",
    "bloomberg-markets-youtube": "iEpJwprxDdk",
    "yahoo-finance-youtube": "KQp-e_XQnDE",
    "reuters-youtube": "mMAF4eNkm-U",
    "livenow-fox-youtube": "B4bb4RLwMK8",
    "bbc-news-youtube": "bjgQzJzCZKs",
    "trt-world-youtube": "ABfFhWzWs0s",
}
TRUSTED_YOUTUBE_FALLBACK_SOURCE_IDS = {
    "trusted-hls-sky-news",
    "trusted-hls-euronews",
    "trusted-hls-alarabiya",
    "trusted-hls-cbs-news",
    "trusted-hls-nbc-news",
    "trusted-hls-trt-world",
    "trusted-hls-al-hadath",
}


@dataclass(frozen=True)
class LiveVideoSourceDependencies:
    source: Mapping[str, Any]
    utc_now_iso: Callable[..., Any] | None
    http_text_get: Callable[..., Any] | None
    requests_module: Any
    youtube_probe_enabled: Any
    youtube_live_probe: Callable[..., Any] | None
    youtube_rss_fallback_enabled: Any
    youtube_rss_refresh_existing: Any
    youtube_rss_refresh_existing_limit: Any
    hls_probe_enabled: Any
    hls_probe_timeout_seconds: Any
    hls_probe_workers: Any
    hls_stream_probe: Callable[..., Any] | None
    ffprobe_path: Any
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    snapshot_store: Any
    settings: Any

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> LiveVideoSourceDependencies:
        return cls(
            source=context,
            utc_now_iso=resolve_optional_service_callable(
                context,
                "utc_now_iso",
            ),
            http_text_get=resolve_optional_service_callable(
                context,
                "http_text_get",
            ),
            requests_module=resolve_optional_service_value(
                context,
                "requests",
            ),
            youtube_probe_enabled=resolve_optional_service_value(
                context,
                "market_tv_youtube_probe_enabled",
            ),
            youtube_live_probe=resolve_optional_service_callable(
                context,
                "youtube_live_probe",
            ),
            youtube_rss_fallback_enabled=resolve_optional_service_value(
                context,
                "market_tv_youtube_rss_fallback_enabled",
            ),
            youtube_rss_refresh_existing=resolve_optional_service_value(
                context,
                "market_tv_youtube_rss_refresh_existing",
            ),
            youtube_rss_refresh_existing_limit=resolve_optional_service_value(
                context,
                "market_tv_youtube_rss_refresh_existing_limit",
            ),
            hls_probe_enabled=resolve_optional_service_value(
                context,
                "market_tv_hls_probe_enabled",
            ),
            hls_probe_timeout_seconds=resolve_optional_service_value(
                context,
                "market_tv_hls_probe_timeout_seconds",
            ),
            hls_probe_workers=resolve_optional_service_value(
                context,
                "market_tv_hls_probe_workers",
            ),
            hls_stream_probe=resolve_optional_service_callable(
                context,
                "hls_stream_probe",
            ),
            ffprobe_path=resolve_optional_service_value(
                context,
                "ffprobe_path",
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
            settings=resolve_optional_service_value(
                context,
                "SETTINGS",
            ),
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now_iso(
    dependencies: LiveVideoSourceDependencies | None = None,
) -> str:
    if dependencies is not None and dependencies.utc_now_iso is not None:
        return dependencies.utc_now_iso()
    return utc_now_iso()


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = SLUG_RE.sub("-", text).strip("-")
    return text or "source"


def _string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    output = []
    for item in value:
        text = _string(item)
        if text:
            output.append(text)
    return output


def _score_for(category: str, *, curated: bool, source_type: str, status: str, market_tags: List[str]) -> int:
    score = CATEGORY_BASE_SCORE.get(category, CATEGORY_BASE_SCORE["other"])
    if curated:
        score += 8
    if source_type == "hls":
        score += 3
    elif source_type == "youtube":
        score += 1
    if status == "not_24_7":
        score -= 14
    elif status in {"blocked", "failed"}:
        score -= 24
    elif status == "unknown":
        score -= 4
    if any(tag in {"fed", "rates", "war", "hurricane", "bitcoin", "nba", "elections"} for tag in market_tags):
        score += 2
    return max(0, min(100, int(score)))


def _status_for(source_type: str, item: Dict[str, Any]) -> str:
    explicit = str(item.get("status") or "").strip().lower()
    if explicit in {"ready", "stale", "not_24_7", "blocked", "failed", "unknown"}:
        return explicit
    if item.get("not24x7"):
        return "not_24_7"
    if source_type in {"external", "youtube"}:
        return "ready"
    if source_type == "hls" and item.get("hlsUrl"):
        return "ready"
    return "unknown"


def _availability(item: Dict[str, Any]) -> str:
    value = str(item.get("availability") or "").strip().lower()
    if value in {"public", "geo_limited", "unknown"}:
        return value
    risk = str(item.get("risk") or "").lower()
    if "region" in risk or "geo" in risk or "paywall" in risk or "provider" in risk:
        return "geo_limited"
    return "public"


def _normalize_category(value: Any) -> str:
    category = str(value or "other").strip().lower()
    return category if category in ALLOWED_CATEGORIES else "other"


def _normalize_source_type(value: Any) -> str:
    source_type = str(value or "external").strip().lower()
    return source_type if source_type in ALLOWED_SOURCE_TYPES else "external"


def _normalize_source_role(value: Any) -> str:
    source_role = str(value or "channel").strip().lower()
    return source_role if source_role in ALLOWED_SOURCE_ROLES else "channel"


def normalize_source_item(raw: Dict[str, Any], *, generated_at: str, curated: bool) -> Optional[Dict[str, Any]]:
    display_name = _string(raw.get("displayName") or raw.get("name"))
    if not display_name:
        return None
    category = _normalize_category(raw.get("category"))
    source_type = _normalize_source_type(raw.get("sourceType"))
    source_role = _normalize_source_role(raw.get("sourceRole"))
    hls_url = _string(raw.get("hlsUrl"))
    external_url = _string(raw.get("externalUrl"))
    youtube_handle = _string(raw.get("youtubeHandle"))
    youtube_channel_id = _string(raw.get("youtubeChannelId"))
    raw_id = _string(raw.get("id")) or f"{_string(raw.get('sourceName')) or 'source'}-{display_name}"
    fallback_video_id = _string(raw.get("fallbackVideoId")) or YOUTUBE_FALLBACK_VIDEO_IDS.get(_slug(raw_id))
    if source_type == "hls" and not hls_url:
        source_type = "external" if external_url else "hls"
    if source_type == "youtube" and not (external_url or youtube_handle or fallback_video_id):
        return None
    if source_type == "external" and not external_url:
        external_url = hls_url
    source_url = _string(raw.get("sourceUrl") or external_url or hls_url)
    if not source_url:
        return None
    market_tags = [tag.lower() for tag in _string_list(raw.get("marketTags"))]
    if curated and not market_tags:
        return None
    status = _status_for(source_type, raw)
    source_name = _string(raw.get("sourceName")) or ("curated" if curated else "iptv-org")
    item_id = raw_id
    item = {
        "id": _slug(item_id),
        "name": _string(raw.get("name") or display_name) or display_name,
        "displayName": display_name,
        "category": category,
        "sourceRole": source_role,
        "sourceType": source_type,
        "region": _string(raw.get("region")),
        "country": _string(raw.get("country")),
        "language": _string(raw.get("language")),
        "hlsUrl": hls_url,
        "youtubeHandle": youtube_handle,
        "youtubeChannelId": youtube_channel_id,
        "youtubeProbeEnabled": raw.get("youtubeProbeEnabled") is not False,
        "fallbackVideoId": fallback_video_id,
        "externalUrl": external_url or source_url,
        "quality": _string(raw.get("quality")),
        "status": status,
        "availability": _availability(raw),
        "sourceName": source_name,
        "sourceUrl": source_url,
        "marketTags": market_tags,
        "matchedTerms": _string_list(raw.get("matchedTerms")) or market_tags[:4],
        "marketUseCase": _string(raw.get("marketUseCase")) or "Live video context for market-moving events.",
        "relevanceScore": 0,
        "lastCheckedAt": _string(raw.get("lastCheckedAt")) or generated_at,
        "failureReason": _string(raw.get("failureReason")),
        "curated": bool(curated),
    }
    for key in ("playbackTier", "playbackStrategy", "hlsProxyRequired", "hlsProxyReferer"):
        if key in raw:
            item[key] = raw.get(key)
    item["relevanceScore"] = _score_for(category, curated=curated, source_type=source_type, status=status, market_tags=market_tags)
    return item


def _manifest_path() -> Path:
    raw = os.environ.get("POLYDATA_MARKET_TV_SOURCE_MANIFEST")
    return Path(raw).expanduser() if raw else MANIFEST_PATH


def load_manifest_items(path: Path | None = None) -> List[Dict[str, Any]]:
    manifest_path = path or _manifest_path()
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("live video source manifest must be a list")
    return [item for item in raw if isinstance(item, dict)]


def _parse_extinf(line: str) -> Dict[str, str]:
    attrs = {match.group(1): match.group(2) for match in EXTINF_ATTR_RE.finditer(line)}
    if "," in line:
        attrs["name"] = line.rsplit(",", 1)[-1].strip()
    return attrs


def parse_m3u_playlist(text: str, *, category: str, source_url: str, generated_at: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    pending: Dict[str, str] | None = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            pending = _parse_extinf(line)
            continue
        if line.startswith("#"):
            continue
        if not pending:
            continue
        stream_url = line
        pending_attrs = pending
        pending = None
        name = _string(pending_attrs.get("name") or pending_attrs.get("tvg-name") or pending_attrs.get("tvg-id"))
        if not name:
            continue
        lower_name = name.lower()
        if any(term in lower_name for term in NOISE_TERMS):
            continue
        parsed = urlparse(stream_url)
        if parsed.scheme != "https":
            continue
        is_hls = ".m3u8" in parsed.path.lower() or ".m3u8" in stream_url.lower()
        if not is_hls:
            continue
        not_24_7 = "[not 24/7]" in lower_name
        clean_name = name.replace("[Not 24/7]", "").replace("[not 24/7]", "").strip()
        country = _string(pending_attrs.get("tvg-country") or pending_attrs.get("country"))
        language = _string(pending_attrs.get("tvg-language") or pending_attrs.get("language"))
        raw_item = {
            "id": f"iptv-{category}-{pending_attrs.get('tvg-id') or clean_name}",
            "displayName": clean_name,
            "category": category,
            "sourceRole": "channel",
            "sourceType": "hls",
            "country": country,
            "language": language,
            "hlsUrl": stream_url,
            "externalUrl": stream_url,
            "sourceName": "iptv-org",
            "sourceUrl": source_url,
            "quality": _string(pending_attrs.get("height") or pending_attrs.get("quality")),
            "status": "not_24_7" if not_24_7 else "ready",
            "availability": "unknown",
            "marketTags": [category, "live", "video", "iptv"],
            "matchedTerms": [category, "iptv"],
            "marketUseCase": f"Candidate {CATEGORY_LABELS.get(category, category)} live source discovered from iptv-org.",
            "lastCheckedAt": generated_at,
        }
        item = normalize_source_item(raw_item, generated_at=generated_at, curated=False)
        if item:
            items.append(item)
    return items


def _http_text_get(
    dependencies: LiveVideoSourceDependencies,
    url: str,
    *,
    timeout: int = 10,
) -> str:
    request_timeout = max(3, min(15, int(timeout or 10)))
    headers = {"User-Agent": "polydata-market-tv-wire/1.0"}
    if dependencies.http_text_get is not None:
        return dependencies.http_text_get(
            url,
            timeout=request_timeout,
            headers=headers,
        )
    if dependencies.requests_module is None:
        raise RuntimeError("http_text_get unavailable")
    response = dependencies.requests_module.get(
        url,
        timeout=request_timeout,
        headers=headers,
    )
    response.raise_for_status()
    return response.text


def _dedupe(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    output: List[Dict[str, Any]] = []
    for item in items:
        key = str(item.get("hlsUrl") or item.get("externalUrl") or item.get("id") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _trusted_youtube_fallback_items(existing_items: List[Dict[str, Any]], *, generated_at: str) -> List[Dict[str, Any]]:
    existing_video_ids = {
        str(item.get("fallbackVideoId") or item.get("youtubeLiveVideoId") or "").strip()
        for item in existing_items
        if str(item.get("sourceType") or "").lower() == "youtube"
    }
    existing_video_ids.discard("")
    output: List[Dict[str, Any]] = []
    for source in trusted_hls_sources.TRUSTED_HLS_SOURCES:
        source_id = _slug(source.get("id"))
        fallback_video_id = _string(source.get("fallbackVideoId"))
        if source_id not in TRUSTED_YOUTUBE_FALLBACK_SOURCE_IDS or not fallback_video_id:
            continue
        if fallback_video_id in existing_video_ids:
            continue
        display_name = str(source.get("displayName") or "Trusted YouTube").replace(" HLS", "").replace("YouTube Fallback", "").strip()
        raw_item = {
            "id": f"trusted-youtube-{source_id.removeprefix('trusted-hls-')}",
            "displayName": f"{display_name} YouTube",
            "category": source.get("category"),
            "sourceRole": source.get("sourceRole") or "channel",
            "sourceType": "youtube",
            "region": source.get("region"),
            "country": source.get("country"),
            "language": source.get("language"),
            "youtubeProbeEnabled": False,
            "fallbackVideoId": fallback_video_id,
            "externalUrl": f"https://www.youtube.com/watch?v={fallback_video_id}",
            "sourceName": "worldmonitor-trusted-youtube",
            "sourceUrl": f"https://www.youtube.com/watch?v={fallback_video_id}",
            "marketTags": list(source.get("marketTags") or []),
            "matchedTerms": list(source.get("matchedTerms") or source.get("marketTags") or [])[:4],
            "marketUseCase": f"YouTube fallback for {source.get('marketUseCase') or 'trusted live market video.'}",
            "lastCheckedAt": generated_at,
        }
        item = normalize_source_item(raw_item, generated_at=generated_at, curated=True)
        if item:
            existing_video_ids.add(fallback_video_id)
            output.append(item)
    return output


def _youtube_probe_enabled(
    dependencies: LiveVideoSourceDependencies,
) -> bool:
    explicit = dependencies.youtube_probe_enabled
    if explicit is not None:
        return bool(explicit)
    value = str(os.environ.get(YOUTUBE_PROBE_ENABLED_ENV, "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _youtube_probe_can_fetch(
    dependencies: LiveVideoSourceDependencies,
) -> bool:
    return (
        dependencies.youtube_live_probe is not None
        or dependencies.http_text_get is not None
        or dependencies.requests_module is not None
    )


def _youtube_rss_fallback_enabled(
    dependencies: LiveVideoSourceDependencies,
) -> bool:
    explicit = dependencies.youtube_rss_fallback_enabled
    if explicit is not None:
        return bool(explicit)
    value = str(os.environ.get(YOUTUBE_RSS_FALLBACK_ENABLED_ENV, "0")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _hls_probe_enabled(
    dependencies: LiveVideoSourceDependencies,
) -> bool:
    explicit = dependencies.hls_probe_enabled
    if explicit is not None:
        return bool(explicit)
    value = str(os.environ.get(HLS_PROBE_ENABLED_ENV, "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _hls_probe_timeout_seconds(
    dependencies: LiveVideoSourceDependencies,
) -> int:
    explicit = dependencies.hls_probe_timeout_seconds
    if explicit is not None:
        try:
            return max(3, min(20, int(explicit)))
        except Exception:
            return 10
    try:
        return max(3, min(20, int(os.environ.get(HLS_PROBE_TIMEOUT_ENV, "10"))))
    except Exception:
        return 10


def _hls_probe_workers(
    dependencies: LiveVideoSourceDependencies,
) -> int:
    explicit = dependencies.hls_probe_workers
    if explicit is not None:
        try:
            return max(1, min(32, int(explicit)))
        except Exception:
            return 12
    try:
        return max(1, min(32, int(os.environ.get(HLS_PROBE_WORKERS_ENV, "12"))))
    except Exception:
        return 12


def _hls_probe_result(ok: bool, status: str, *, error: str | None = None, streams: List[str] | None = None) -> Dict[str, Any]:
    return {
        "ok": bool(ok),
        "status": status,
        "error": error or None,
        "streams": streams or [],
    }


def _hls_http_fetch(url: str, *, timeout: int, max_bytes: int, binary: bool = False, referer: str | None = None) -> bytes | str:
    headers = {
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0 polydata-tv-probe/1.0",
    }
    if referer:
        headers["Referer"] = referer
        referer_parts = urlparse(referer)
        if referer_parts.scheme and referer_parts.netloc:
            headers["Origin"] = f"{referer_parts.scheme}://{referer_parts.netloc}"
    if binary:
        headers["Range"] = f"bytes=0-{max(0, max_bytes - 1)}"
    request = Request(
        url,
        headers=headers,
    )
    with urlopen(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200) or 200)
        if status >= 400:
            raise RuntimeError(f"http {status}")
        data = response.read(max_bytes)
    if binary:
        return data
    return data.decode("utf-8", errors="replace")


def _next_hls_uri(lines: List[str], marker: str) -> str | None:
    for index, line in enumerate(lines):
        if not line.upper().startswith(marker):
            continue
        for candidate in lines[index + 1:]:
            candidate = candidate.strip()
            if candidate and not candidate.startswith("#"):
                return candidate
    return None


def _first_hls_segment_uri(lines: List[str]) -> str | None:
    for line in lines:
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        lower = candidate.lower()
        if lower.endswith(".m3u8") or ".m3u8?" in lower:
            continue
        return candidate
    return None


def _probe_hls_stream_http(
    dependencies: LiveVideoSourceDependencies,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    timeout_seconds = _hls_probe_timeout_seconds(dependencies)
    hls_url = _string(item.get("hlsUrl")) or ""
    referer = _string(item.get("hlsProxyReferer"))
    try:
        manifest = _hls_http_fetch(hls_url, timeout=timeout_seconds, max_bytes=1_000_000, referer=referer)
        if not isinstance(manifest, str) or "#EXTM3U" not in manifest:
            return _hls_probe_result(False, "blocked", error="missing #EXTM3U manifest")
        lines = [line.strip() for line in manifest.splitlines() if line.strip()]
        variant_uri = _next_hls_uri(lines, "#EXT-X-STREAM-INF")
        media_url = urljoin(hls_url, variant_uri) if variant_uri else hls_url
        media_manifest = manifest
        if variant_uri:
            media_manifest = _hls_http_fetch(media_url, timeout=timeout_seconds, max_bytes=1_000_000, referer=referer)
            if not isinstance(media_manifest, str) or "#EXTM3U" not in media_manifest:
                return _hls_probe_result(False, "blocked", error="variant playlist missing #EXTM3U")
        media_lines = [line.strip() for line in str(media_manifest).splitlines() if line.strip()]
        has_media_playlist = any(line.upper().startswith(("#EXTINF", "#EXT-X-TARGETDURATION", "#EXT-X-MEDIA-SEQUENCE")) for line in media_lines)
        if not has_media_playlist:
            return _hls_probe_result(False, "empty", error="no media playlist entries")
        segment_uri = _first_hls_segment_uri(media_lines)
        if segment_uri:
            segment_url = urljoin(media_url, segment_uri)
            sample = _hls_http_fetch(segment_url, timeout=timeout_seconds, max_bytes=2048, binary=True, referer=referer)
            if not sample:
                return _hls_probe_result(False, "empty", error="first media segment returned no bytes")
            return _hls_probe_result(True, "playable", streams=["manifest", "segment"])
        return _hls_probe_result(True, "playable", streams=["manifest"])
    except TimeoutError:
        return _hls_probe_result(False, "timeout", error="timeout")
    except HTTPError as exc:
        return _hls_probe_result(False, "blocked", error=f"http {exc.code}")
    except URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        return _hls_probe_result(False, "timeout" if "timed out" in reason.lower() else "blocked", error=reason[:260])
    except Exception as exc:
        message = str(exc)
        return _hls_probe_result(False, "timeout" if "timed out" in message.lower() else "blocked", error=message[:260])


def _probe_hls_stream(
    ctx: Mapping[str, Any],
    item: Dict[str, Any],
) -> Dict[str, Any]:
    return _probe_hls_stream_with_dependencies(
        LiveVideoSourceDependencies.from_context(ctx),
        item,
    )


def _probe_hls_stream_with_dependencies(
    dependencies: LiveVideoSourceDependencies,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    if dependencies.hls_stream_probe is not None:
        result = dependencies.hls_stream_probe(item)
        if isinstance(result, dict):
            status = str(result.get("status") or ("playable" if result.get("ok") else "blocked"))
            return _hls_probe_result(bool(result.get("ok")), status, error=_string(result.get("error")), streams=_string_list(result.get("streams")))
    hls_url = _string(item.get("hlsUrl"))
    if not hls_url:
        return _hls_probe_result(False, "missing", error="missing hlsUrl")
    referer = _string(item.get("hlsProxyReferer"))
    ffprobe = shutil.which(str(dependencies.ffprobe_path or "ffprobe"))
    if not ffprobe:
        return _probe_hls_stream_http(dependencies, item)
    timeout_seconds = _hls_probe_timeout_seconds(dependencies)
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-rw_timeout",
        str(timeout_seconds * 1_000_000),
        "-timeout",
        str(timeout_seconds * 1_000_000),
        "-user_agent",
        "Mozilla/5.0 polydata-tv-probe/1.0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
    ]
    if referer:
        referer_parts = urlparse(referer)
        origin = f"{referer_parts.scheme}://{referer_parts.netloc}" if referer_parts.scheme and referer_parts.netloc else referer.rstrip("/")
        cmd.extend(["-headers", f"Referer: {referer}\r\nOrigin: {origin}\r\n"])
    cmd.append(hls_url)
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds + 2)
    except subprocess.TimeoutExpired:
        return _hls_probe_result(False, "timeout", error="timeout")
    except Exception as exc:
        return _hls_probe_result(False, "error", error=str(exc))
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "").strip()
        return _hls_probe_result(False, "blocked", error=error[:260])
    try:
        payload = json.loads(completed.stdout or "{}")
    except Exception as exc:
        return _hls_probe_result(False, "error", error=f"ffprobe json parse failed: {exc}")
    streams = [str(stream.get("codec_type") or "") for stream in payload.get("streams") or [] if isinstance(stream, dict)]
    ok = "video" in streams or "audio" in streams
    return _hls_probe_result(ok, "playable" if ok else "empty", streams=streams)


def _enrich_hls_watchability(
    dependencies: LiveVideoSourceDependencies,
    items: List[Dict[str, Any]],
    *,
    generated_at: str,
) -> Dict[str, Any]:
    hls_items = [item for item in items if str(item.get("sourceType") or "").lower() == "hls"]
    if not hls_items:
        return {"status": "skipped", "count": 0, "playableCount": 0, "blockedCount": 0, "lastSuccessAt": None}
    if not _hls_probe_enabled(dependencies):
        for item in hls_items:
            item.setdefault("hlsProbeStatus", "unverified")
        return {"status": "disabled", "count": len(hls_items), "playableCount": 0, "blockedCount": 0, "lastSuccessAt": None}
    playable_count = 0
    blocked_count = 0
    timeout_count = 0
    def apply_probe(item: Dict[str, Any], probe: Dict[str, Any]) -> None:
        nonlocal playable_count, blocked_count, timeout_count
        item["hlsProbeStatus"] = probe["status"]
        item["hlsProbeError"] = probe.get("error")
        item["hlsProbeStreams"] = probe.get("streams") or []
        item["lastCheckedAt"] = generated_at
        if probe["ok"]:
            playable_count += 1
            item["status"] = "ready"
        else:
            blocked_count += 1
            if probe["status"] == "timeout":
                timeout_count += 1
            item["status"] = "blocked"
            item["failureReason"] = probe.get("error") or probe["status"]
            item["relevanceScore"] = max(0, int(item.get("relevanceScore") or 0) - 30)

    workers = min(len(hls_items), _hls_probe_workers(dependencies))
    if workers <= 1:
        for item in hls_items:
            apply_probe(
                item,
                _probe_hls_stream_with_dependencies(dependencies, item),
            )
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            probes = executor.map(
                lambda source: _probe_hls_stream_with_dependencies(
                    dependencies,
                    source,
                ),
                hls_items,
            )
            for item, probe in zip(hls_items, probes):
                apply_probe(item, probe)
    return {
        "status": "ok" if playable_count else "error",
        "count": len(hls_items),
        "playableCount": playable_count,
        "blockedCount": blocked_count,
        "timeoutCount": timeout_count,
        "lastSuccessAt": generated_at if playable_count else None,
    }


def _external_url_for_youtube(item: Dict[str, Any], video_id: str | None = None) -> str | None:
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    existing = _string(item.get("externalUrl") or item.get("sourceUrl"))
    if existing:
        return existing
    handle = _string(item.get("youtubeHandle"))
    if handle:
        return f"https://www.youtube.com/{handle if handle.startswith('@') else '@' + handle}/live"
    return None


def _youtube_embed_url(video_id: str | None = None, channel_id: str | None = None) -> tuple[str | None, str | None]:
    clean_video_id = _string(video_id)
    if clean_video_id and youtube_live_probe_service.VIDEO_ID_RE.match(clean_video_id):
        return (
            f"https://www.youtube-nocookie.com/embed/{clean_video_id}?autoplay=1&mute=1&playsinline=1&rel=0&modestbranding=1",
            "video",
        )
    return None, None


def _youtube_rss_latest_video(
    dependencies: LiveVideoSourceDependencies,
    channel_id: str,
) -> Dict[str, str] | None:
    clean_channel_id = _string(channel_id)
    if not clean_channel_id:
        return None
    text = _http_text_get(
        dependencies,
        f"https://www.youtube.com/feeds/videos.xml?channel_id={clean_channel_id}",
        timeout=5,
    )
    root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    entry = root.find("atom:entry", namespaces)
    if entry is None:
        return None
    video_id = _string(entry.findtext("yt:videoId", namespaces=namespaces))
    if not video_id or not youtube_live_probe_service.VIDEO_ID_RE.match(video_id):
        return None
    title = _string(entry.findtext("atom:title", namespaces=namespaces))
    return {"videoId": video_id, "title": title or ""}


def _youtube_rss_refresh_existing_limit(
    dependencies: LiveVideoSourceDependencies,
) -> int:
    explicit = dependencies.youtube_rss_refresh_existing_limit
    if explicit is not None:
        try:
            return max(0, min(64, int(explicit)))
        except Exception:
            return 16
    try:
        return max(0, min(64, int(os.environ.get(YOUTUBE_RSS_REFRESH_EXISTING_LIMIT_ENV, "16"))))
    except Exception:
        return 16


def _daily_refresh_sort_key(item: Dict[str, Any], generated_at: str) -> str:
    identity = _string(item.get("id")) or _string(item.get("youtubeChannelId")) or _string(item.get("displayName"))
    date_key = _string(generated_at)[:10] or "daily"
    return hashlib.sha1(f"{date_key}:{identity}".encode("utf-8")).hexdigest()


def _enrich_youtube_rss_fallbacks(
    dependencies: LiveVideoSourceDependencies,
    items: List[Dict[str, Any]],
    *,
    generated_at: str,
) -> Dict[str, Any]:
    refresh_existing = bool(dependencies.youtube_rss_refresh_existing)
    missing_items = [
        item for item in items
        if str(item.get("sourceType") or "").lower() == "youtube"
        and _string(item.get("youtubeChannelId"))
        and not _string(item.get("fallbackVideoId"))
    ]
    refresh_items: List[Dict[str, Any]] = []
    if refresh_existing:
        refresh_limit = _youtube_rss_refresh_existing_limit(dependencies)
        refresh_candidates = [
            item for item in items
            if str(item.get("sourceType") or "").lower() == "youtube"
            and _string(item.get("youtubeChannelId"))
            and _string(item.get("fallbackVideoId"))
            and item.get("youtubeProbeEnabled") is False
        ]
        refresh_items = sorted(refresh_candidates, key=lambda item: _daily_refresh_sort_key(item, generated_at))[:refresh_limit]
    seen_ids: set[str] = set()
    youtube_items: List[Dict[str, Any]] = []
    for item in [*missing_items, *refresh_items]:
        identity = _string(item.get("id")) or _string(item.get("youtubeChannelId"))
        if identity in seen_ids:
            continue
        seen_ids.add(identity)
        youtube_items.append(item)
    if not youtube_items:
        return {"status": "skipped", "count": 0, "readyCount": 0, "errorCount": 0, "lastSuccessAt": None}
    if (
        not _youtube_rss_fallback_enabled(dependencies)
        or dependencies.http_text_get is None
    ):
        return {"status": "disabled", "count": len(youtube_items), "readyCount": 0, "errorCount": 0, "lastSuccessAt": None}
    ready_count = 0
    error_count = 0
    def fetch_latest(item: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, str] | None, str | None]:
        try:
            latest = _youtube_rss_latest_video(
                dependencies,
                str(item.get("youtubeChannelId") or ""),
            )
        except Exception as exc:
            return item, None, str(exc)[:220]
        return item, latest, None

    workers = min(len(youtube_items), 8)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = executor.map(fetch_latest, youtube_items)
        for item, latest, error in results:
            if error:
                error_count += 1
                item["youtubeRssFallbackError"] = error
                continue
            if not latest:
                continue
            ready_count += 1
            item["fallbackVideoId"] = latest["videoId"]
            item["youtubeFallbackTitle"] = latest.get("title") or None
            item["youtubeFallbackSource"] = "channel-rss"
            item["lastCheckedAt"] = generated_at
    return {
        "status": "ok" if ready_count else ("degraded" if error_count < len(youtube_items) else "error"),
        "count": len(youtube_items),
        "readyCount": ready_count,
        "errorCount": error_count,
        "lastSuccessAt": generated_at if ready_count else None,
    }


def _enrich_youtube_live_sources(
    dependencies: LiveVideoSourceDependencies,
    items: List[Dict[str, Any]],
    *,
    generated_at: str,
) -> Dict[str, Any]:
    youtube_items = [
        item for item in items
        if str(item.get("sourceType") or "").lower() == "youtube" and item.get("youtubeProbeEnabled") is not False
    ]
    skipped_count = sum(1 for item in items if str(item.get("sourceType") or "").lower() == "youtube" and item.get("youtubeProbeEnabled") is False)
    if not youtube_items:
        return {"status": "skipped", "count": 0, "skippedCount": skipped_count, "liveCount": 0, "errorCount": 0, "lastSuccessAt": None}
    if not _youtube_probe_enabled(dependencies):
        return {"status": "disabled", "count": len(youtube_items), "skippedCount": skipped_count, "liveCount": 0, "errorCount": 0, "lastSuccessAt": None}
    if not _youtube_probe_can_fetch(dependencies):
        return {"status": "skipped", "count": len(youtube_items), "skippedCount": skipped_count, "liveCount": 0, "errorCount": 0, "lastSuccessAt": None}

    live_count = 0
    offline_count = 0
    error_count = 0
    for item in youtube_items:
        channel = _string(item.get("youtubeHandle"))
        fallback_video_id = _string(item.get("fallbackVideoId"))
        try:
            probe = youtube_live_probe_service.probe_youtube_live(
                dependencies.source,
                channel=channel or "",
                video_id=fallback_video_id or "",
            )
        except Exception as exc:
            error_count += 1
            item["youtubeProbeStatus"] = "error"
            item["youtubeProbeError"] = str(exc)
            item["failureReason"] = item.get("failureReason") or str(exc)
            continue

        video_id = _string(probe.get("videoId"))
        hls_url = _string(probe.get("hlsUrl"))
        title = _string(probe.get("title"))
        channel_id = _string(probe.get("channelId")) or _string(item.get("youtubeChannelId"))
        channel_name = _string(probe.get("channelName"))
        error = _string(probe.get("error"))
        is_live = bool(probe.get("isLive")) and bool(video_id)
        item["youtubeChannelExists"] = bool(probe.get("channelExists"))
        item["youtubeChannelId"] = channel_id
        item["youtubeChannelName"] = channel_name
        item["youtubeLiveVideoId"] = video_id
        item["youtubeLiveTitle"] = title
        item["youtubeHlsUrl"] = hls_url
        item["youtubeProbeError"] = error
        item["lastCheckedAt"] = generated_at
        embed_url, embed_mode = _youtube_embed_url(video_id, channel_id)
        item["youtubeEmbedUrl"] = embed_url
        item["youtubeEmbedMode"] = "live-video" if is_live and embed_mode == "video" else embed_mode
        if video_id:
            item["fallbackVideoId"] = video_id
            item["externalUrl"] = _external_url_for_youtube(item, video_id) or item.get("externalUrl")
        if is_live:
            live_count += 1
            item["youtubeProbeStatus"] = "live"
            item["status"] = "ready"
            item["relevanceScore"] = min(100, int(item.get("relevanceScore") or 0) + 4)
        elif error:
            error_count += 1
            item["youtubeProbeStatus"] = "error"
            item["failureReason"] = item.get("failureReason") or error
        else:
            offline_count += 1
            item["youtubeProbeStatus"] = "offline"
        if not item.get("externalUrl"):
            item["externalUrl"] = _external_url_for_youtube(item)

    status = "ok" if error_count == 0 else ("degraded" if live_count or offline_count else "error")
    return {
        "status": status,
        "count": len(youtube_items),
        "skippedCount": skipped_count,
        "liveCount": live_count,
        "offlineCount": offline_count,
        "errorCount": error_count,
        "lastSuccessAt": generated_at if live_count or offline_count else None,
    }


def _youtube_channel_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    items = [item for item in raw_items if isinstance(item, dict) and str(item.get("sourceType") or "").lower() == "youtube"]
    for item in items:
        video_id = _string(item.get("youtubeLiveVideoId") or item.get("fallbackVideoId"))
        channel_id = _string(item.get("youtubeChannelId"))
        embed_url = _string(item.get("youtubeEmbedUrl"))
        embed_mode = _string(item.get("youtubeEmbedMode"))
        if embed_mode and embed_mode not in {"video", "live-video"}:
            embed_url = ""
            item["youtubeEmbedUrl"] = None
            item["youtubeEmbedMode"] = None
        if "embed/live_stream" in str(embed_url or ""):
            embed_url = ""
            item["youtubeEmbedUrl"] = None
            item["youtubeEmbedMode"] = None
        if embed_url and not embed_mode and video_id:
            embed_mode = "video"
            item["youtubeEmbedMode"] = embed_mode
        if not embed_url:
            embed_url, embed_mode = _youtube_embed_url(video_id, channel_id)
            item["youtubeEmbedUrl"] = embed_url
            item["youtubeEmbedMode"] = embed_mode
        if video_id and not item.get("externalUrl"):
            item["externalUrl"] = _external_url_for_youtube(item, video_id)
    items.sort(
        key=lambda item: (
            1 if item.get("youtubeProbeStatus") == "live" else 0,
            1 if item.get("youtubeEmbedUrl") else 0,
            int(item.get("relevanceScore") or 0),
        ),
        reverse=True,
    )
    return items


def _youtube_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "total": len(items),
        "liveReady": sum(1 for item in items if item.get("youtubeProbeStatus") == "live"),
        "marketMatched": sum(1 for item in items if item.get("relevanceScore", 0) >= 80),
        "regions": len({str(item.get("region") or item.get("country") or "").strip() for item in items if item.get("region") or item.get("country")}),
        "staleCount": sum(1 for item in items if item.get("youtubeProbeStatus") in {"offline", "error", "skipped"}),
        "blockedCount": sum(1 for item in items if item.get("status") in {"blocked", "failed"}),
        "embedReady": sum(1 for item in items if item.get("youtubeEmbedUrl") and item.get("youtubeEmbedMode") in {"video", "live-video"}),
    }


def _topic_token(value: Any) -> str:
    return SLUG_RE.sub("-", str(value or "").strip().lower()).strip("-")


def _youtube_topic_tokens(item: Dict[str, Any]) -> set[str]:
    tokens = {_normalize_category(item.get("category"))}
    for key in ("marketTags", "matchedTerms"):
        for value in _string_list(item.get(key)):
            token = _topic_token(value)
            if token:
                tokens.add(token)
    for key in ("displayName", "sourceName"):
        for part in re.split(r"[^A-Za-z0-9_-]+", str(item.get(key) or "").lower()):
            token = _topic_token(part)
            if token:
                tokens.add(token)
    return tokens


def _youtube_item_matches_topic(item: Dict[str, Any], topic: str | None) -> bool:
    requested = _normalize_category(topic)
    if not topic or requested == "other":
        return True
    tokens = _youtube_topic_tokens(item)
    aliases = {requested, *YOUTUBE_TOPIC_ALIASES.get(requested, set())}
    return bool(tokens.intersection(aliases))


def _youtube_categories(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts = {category: 0 for category in CATEGORY_ORDER}
    for item in items:
        tokens = _youtube_topic_tokens(item)
        for category in CATEGORY_ORDER:
            if category == "other":
                continue
            aliases = {category, *YOUTUBE_TOPIC_ALIASES.get(category, set())}
            if tokens.intersection(aliases):
                counts[category] = counts.get(category, 0) + 1
    return [{"id": category, "label": CATEGORY_LABELS[category], "count": counts.get(category, 0)} for category in CATEGORY_ORDER if counts.get(category, 0)]


def normalize_market_youtube_channels_payload(
    payload: Any,
    *,
    ctx: Mapping[str, Any] | None = None,
    limit: int = DEFAULT_MARKET_TV_WIRE_LIMIT,
    category: str | None = None,
) -> Dict[str, Any]:
    dependencies = (
        LiveVideoSourceDependencies.from_context(ctx)
        if ctx is not None
        else None
    )
    return _normalize_market_youtube_channels_payload(
        payload,
        dependencies=dependencies,
        limit=limit,
        category=category,
    )


def _normalize_market_youtube_channels_payload(
    payload: Any,
    *,
    dependencies: LiveVideoSourceDependencies | None,
    limit: int,
    category: str | None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        empty = _empty_payload(
            dependencies,
            status="invalid",
            cache_mode="invalid",
        )
        empty["source"] = "market-youtube-channels"
        return empty
    result = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    all_items = _youtube_channel_items(result)
    requested_category = _requested_category(category)
    selected_items = [
        item for item in all_items
        if not requested_category or _youtube_item_matches_topic(item, requested_category)
    ]
    max_items = max(1, min(int(limit or DEFAULT_MARKET_TV_WIRE_LIMIT), 80))
    generated_at = str(result.get("generatedAt") or _utc_now_iso(dependencies))
    status = str(result.get("status") or ("ok" if all_items else "warming"))
    cache_mode = str(result.get("cacheMode") or "seeded")
    return {
        "generatedAt": generated_at,
        "status": "empty" if status == "ok" and not all_items else status,
        "cacheMode": cache_mode,
        "source": "market-youtube-channels",
        "sourceUrl": str(result.get("sourceUrl") or _manifest_path()),
        "summary": _youtube_summary(all_items),
        "categories": _youtube_categories(all_items),
        "sources": result.get("sources") if isinstance(result.get("sources"), dict) else {},
        "items": selected_items[:max_items],
        "errors": result.get("errors") if isinstance(result.get("errors"), list) else [],
        "selection": {
            "category": requested_category or "all",
            "total": len(selected_items),
            "returned": min(len(selected_items), max_items),
            "limit": max_items,
            "truncated": len(selected_items) > max_items,
        },
    }


def _summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    regions = {str(item.get("region") or item.get("country") or "").strip() for item in items if item.get("region") or item.get("country")}
    return {
        "total": len(items),
        "liveReady": sum(1 for item in items if item.get("status") == "ready"),
        "marketMatched": sum(1 for item in items if item.get("relevanceScore", 0) >= 80),
        "regions": len(regions),
        "staleCount": sum(1 for item in items if item.get("status") in {"stale", "not_24_7", "unknown"}),
        "blockedCount": sum(1 for item in items if item.get("status") in {"blocked", "failed"}),
    }


def _categories(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts = {category: 0 for category in CATEGORY_ORDER}
    for item in items:
        category = _normalize_category(item.get("category"))
        counts[category] = counts.get(category, 0) + 1
    return [{"id": category, "label": CATEGORY_LABELS[category], "count": counts.get(category, 0)} for category in CATEGORY_ORDER if counts.get(category, 0)]


def _empty_payload(
    dependencies: LiveVideoSourceDependencies | None = None,
    *,
    status: str = "warming",
    cache_mode: str = "warming",
) -> Dict[str, Any]:
    now = _utc_now_iso(dependencies)
    return {
        "generatedAt": now,
        "status": status,
        "cacheMode": cache_mode,
        "source": "market-tv-wire-seed",
        "sourceUrl": str(_manifest_path()),
        "summary": _summary([]),
        "categories": [],
        "sources": {},
        "items": [],
        "errors": [],
    }


def build_market_tv_wire_payload(
    ctx: Mapping[str, Any],
    *,
    include_iptv: bool = True,
) -> Dict[str, Any]:
    return _build_market_tv_wire_payload(
        LiveVideoSourceDependencies.from_context(ctx),
        include_iptv=include_iptv,
    )


def _build_market_tv_wire_payload(
    dependencies: LiveVideoSourceDependencies,
    *,
    include_iptv: bool = True,
) -> Dict[str, Any]:
    generated_at = _utc_now_iso(dependencies)
    errors: List[str] = []
    source_states: Dict[str, Any] = {}
    items: List[Dict[str, Any]] = []
    manifest_items: List[Dict[str, Any]] = []
    try:
        for raw in trusted_hls_sources.trusted_hls_items(generated_at=generated_at):
            item = normalize_source_item(raw, generated_at=generated_at, curated=True)
            if item:
                manifest_items.append(item)
        for raw in load_manifest_items():
            item = normalize_source_item(raw, generated_at=generated_at, curated=True)
            if item:
                manifest_items.append(item)
        trusted_youtube_fallbacks = _trusted_youtube_fallback_items(manifest_items, generated_at=generated_at)
        manifest_items.extend(trusted_youtube_fallbacks)
        youtube_rss_fallback_state = _enrich_youtube_rss_fallbacks(
            dependencies,
            manifest_items,
            generated_at=generated_at,
        )
        youtube_probe_state = _enrich_youtube_live_sources(
            dependencies,
            manifest_items,
            generated_at=generated_at,
        )
        source_states["trustedHls"] = {
            "status": "ok",
            "count": len([item for item in manifest_items if item.get("playbackTier") == "trusted-hls"]),
            "lastSuccessAt": generated_at,
        }
        source_states["trustedYoutubeFallback"] = {
            "status": "ok",
            "count": len(trusted_youtube_fallbacks),
            "lastSuccessAt": generated_at,
        }
        if youtube_rss_fallback_state.get("status") not in {"skipped"}:
            source_states["youtubeRssFallback"] = youtube_rss_fallback_state
        source_states["manifest"] = {"status": "ok", "count": len(manifest_items), "lastSuccessAt": generated_at}
        if youtube_probe_state.get("status") not in {"skipped"}:
            source_states["youtubeLiveProbe"] = youtube_probe_state
        items.extend(manifest_items)
    except Exception as exc:
        errors.append(f"manifest: {exc}")
        source_states["manifest"] = {"status": "error", "count": 0, "error": str(exc)}

    if include_iptv:
        iptv_count = 0
        iptv_errors = 0
        for category, feed_url in IPTV_CATEGORY_FEEDS:
            try:
                text = _http_text_get(dependencies, feed_url)
                parsed = parse_m3u_playlist(text, category=category, source_url=feed_url, generated_at=generated_at)
                iptv_count += len(parsed)
                items.extend(parsed)
            except Exception as exc:
                iptv_errors += 1
                errors.append(f"iptv:{category}: {exc}")
        source_states["iptvOrg"] = {
            "status": "ok" if iptv_errors == 0 else ("degraded" if iptv_count else "error"),
            "count": iptv_count,
            "lastSuccessAt": generated_at if iptv_count else None,
            "error": "; ".join(errors[-iptv_errors:]) if iptv_errors else None,
        }

    hls_probe_state = _enrich_hls_watchability(
        dependencies,
        items,
        generated_at=generated_at,
    )
    if hls_probe_state.get("status") not in {"skipped"}:
        source_states["hlsWatchabilityProbe"] = hls_probe_state

    items = _dedupe(items)
    items.sort(
        key=lambda item: (
            1 if item.get("status") == "ready" else 0,
            int(item.get("relevanceScore") or 0),
            1 if item.get("curated") else 0,
        ),
        reverse=True,
    )
    seed_limit = max(24, int(os.environ.get("POLYDATA_MARKET_TV_WIRE_SEED_LIMIT", MARKET_TV_WIRE_SEED_ITEM_LIMIT)))
    curated_items = [item for item in items if item.get("curated")]
    discovered_items = [item for item in items if not item.get("curated")]
    discovered_limit = max(0, seed_limit - len(curated_items))
    items = curated_items + discovered_items[:discovered_limit]
    status = "ok" if items and not errors else ("degraded" if items else "empty")
    return {
        "generatedAt": generated_at,
        "status": status,
        "cacheMode": "live-build",
        "source": "market-tv-wire-seed",
        "sourceUrl": str(_manifest_path()),
        "summary": _summary(items),
        "categories": _categories(items),
        "sources": source_states,
        "items": items,
        "errors": errors[:8],
    }


def _requested_category(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw or raw == "all":
        return None
    return raw if raw in CATEGORY_LABELS else None


def normalize_market_tv_wire_payload(
    payload: Any,
    *,
    ctx: Mapping[str, Any] | None = None,
    limit: int = DEFAULT_MARKET_TV_WIRE_LIMIT,
    category: str | None = None,
) -> Dict[str, Any]:
    dependencies = (
        LiveVideoSourceDependencies.from_context(ctx)
        if ctx is not None
        else None
    )
    return _normalize_market_tv_wire_payload(
        payload,
        dependencies=dependencies,
        limit=limit,
        category=category,
    )


def _normalize_market_tv_wire_payload(
    payload: Any,
    *,
    dependencies: LiveVideoSourceDependencies | None,
    limit: int,
    category: str | None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_payload(
            dependencies,
            status="invalid",
            cache_mode="invalid",
        )
    result = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    raw_items = result.get("items") if isinstance(result.get("items"), list) else []
    items = [
        item for item in raw_items
        if isinstance(item, dict) and str(item.get("sourceType") or "").lower() == "hls"
    ]
    items.sort(
        key=lambda item: (
            1 if item.get("status") == "ready" else 0,
            int(item.get("relevanceScore") or 0),
            1 if item.get("curated") else 0,
        ),
        reverse=True,
    )
    requested_category = _requested_category(category)
    selected_items = [
        item for item in items
        if not requested_category or _normalize_category(item.get("category")) == requested_category
    ]
    max_items = max(1, min(int(limit or DEFAULT_MARKET_TV_WIRE_LIMIT), 80))
    result["items"] = selected_items[:max_items]
    result["summary"] = _summary(items)
    result["categories"] = _categories(items)
    result["sources"] = result.get("sources") if isinstance(result.get("sources"), dict) else {}
    result["errors"] = result.get("errors") if isinstance(result.get("errors"), list) else []
    result["generatedAt"] = str(
        result.get("generatedAt")
        or _utc_now_iso(dependencies)
    )
    status = str(result.get("status") or ("ok" if items else "warming"))
    result["status"] = "empty" if status == "ok" and not items else status
    result["cacheMode"] = str(result.get("cacheMode") or "seeded")
    result["source"] = str(result.get("source") or "market-tv-wire-seed")
    result["sourceUrl"] = str(result.get("sourceUrl") or _manifest_path())
    result["selection"] = {
        "category": requested_category or "all",
        "total": len(selected_items),
        "returned": len(result["items"]),
        "limit": max_items,
        "truncated": len(selected_items) > max_items,
    }
    return result


def _with_mode(payload: Dict[str, Any], mode: str) -> Dict[str, Any]:
    status = str(payload.get("status") or "ok")
    if "stale" in mode and status == "ok":
        status = "degraded"
    return {**payload, "cacheMode": mode, "status": status}


def _read_seeded(
    dependencies: LiveVideoSourceDependencies,
) -> Optional[Dict[str, Any]]:
    if dependencies.get_cached_json is not None:
        payload = dependencies.get_cached_json(
            MARKET_TV_WIRE_SNAPSHOT_NAMESPACE,
            MARKET_TV_WIRE_CACHE_KEY,
        )
        if isinstance(payload, dict):
            return _with_mode(payload, "seeded")
    store = dependencies.snapshot_store
    if store is not None:
        payload = store.get(MARKET_TV_WIRE_SNAPSHOT_NAMESPACE, MARKET_TV_WIRE_CACHE_KEY)
        if isinstance(payload, dict):
            return _with_mode(payload, "seeded")
        stale = store.get_stale(MARKET_TV_WIRE_SNAPSHOT_NAMESPACE, MARKET_TV_WIRE_CACHE_KEY)
        if isinstance(stale, dict):
            return _with_mode(stale, "stale")
    return None


def get_market_tv_wire_snapshot(
    ctx: Mapping[str, Any],
    limit: int = DEFAULT_MARKET_TV_WIRE_LIMIT,
    *,
    category: str | None = None,
    allow_live_build: bool = False,
) -> Dict[str, Any]:
    dependencies = LiveVideoSourceDependencies.from_context(ctx)
    seeded = _read_seeded(dependencies)
    if seeded is not None:
        return _normalize_market_tv_wire_payload(
            seeded,
            dependencies=dependencies,
            limit=limit,
            category=category,
        )
    if not allow_live_build:
        return _normalize_market_tv_wire_payload(
            _empty_payload(
                dependencies,
                status="warming",
                cache_mode="warming",
            ),
            dependencies=dependencies,
            limit=limit,
            category=category,
        )
    payload = _build_market_tv_wire_payload(dependencies)
    if payload.get("items"):
        ttl = max(
            60,
            int(
                getattr(
                    dependencies.settings,
                    "market_tv_wire_ttl_seconds",
                    MARKET_TV_WIRE_TTL_SECONDS,
                )
                or MARKET_TV_WIRE_TTL_SECONDS
            ),
        )
        if dependencies.set_cached_json is not None:
            dependencies.set_cached_json(
                MARKET_TV_WIRE_SNAPSHOT_NAMESPACE,
                MARKET_TV_WIRE_CACHE_KEY,
                payload,
                ttl,
            )
        store = dependencies.snapshot_store
        if store is not None:
            store.set(MARKET_TV_WIRE_SNAPSHOT_NAMESPACE, MARKET_TV_WIRE_CACHE_KEY, payload, ttl)
    return _normalize_market_tv_wire_payload(
        payload,
        dependencies=dependencies,
        limit=limit,
        category=category,
    )


def get_market_youtube_channels_snapshot(
    ctx: Mapping[str, Any],
    limit: int = DEFAULT_MARKET_TV_WIRE_LIMIT,
    *,
    category: str | None = None,
    allow_live_build: bool = False,
) -> Dict[str, Any]:
    dependencies = LiveVideoSourceDependencies.from_context(ctx)
    seeded = _read_seeded(dependencies)
    if seeded is not None:
        return _normalize_market_youtube_channels_payload(
            seeded,
            dependencies=dependencies,
            limit=limit,
            category=category,
        )
    if not allow_live_build:
        empty = _empty_payload(
            dependencies,
            status="warming",
            cache_mode="warming",
        )
        return _normalize_market_youtube_channels_payload(
            empty,
            dependencies=dependencies,
            limit=limit,
            category=category,
        )
    payload = _build_market_tv_wire_payload(
        dependencies,
        include_iptv=False,
    )
    return _normalize_market_youtube_channels_payload(
        payload,
        dependencies=dependencies,
        limit=limit,
        category=category,
    )
