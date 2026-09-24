from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote_plus, urlencode

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
)


YOUTUBE_LIVE_PROBE_NAMESPACE = "probe:youtube-live"
YOUTUBE_LIVE_POSITIVE_TTL_SECONDS = 60
YOUTUBE_LIVE_NEGATIVE_TTL_SECONDS = 30
YOUTUBE_RELAY_BASE_ENV = "POLYDATA_YOUTUBE_LIVE_RELAY_BASE_URL"
YOUTUBE_RELAY_TOKEN_ENV = "POLYDATA_YOUTUBE_LIVE_RELAY_TOKEN"
YOUTUBE_RELAY_AUTH_HEADER_ENV = "POLYDATA_YOUTUBE_LIVE_RELAY_AUTH_HEADER"
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class YouTubeLiveProbeDependencies:
    http_text_get: Callable[..., Any] | None
    http_json_get: Callable[..., Any] | None
    requests_module: Any
    relay_base_url_configured: bool
    relay_base_url: Any
    relay_token: Any
    relay_auth_header: Any
    youtube_live_probe: Callable[..., Any] | None
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    snapshot_store: Any

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> YouTubeLiveProbeDependencies:
        return cls(
            http_text_get=resolve_optional_service_callable(
                context,
                "http_text_get",
            ),
            http_json_get=resolve_optional_service_callable(
                context,
                "http_json_get",
            ),
            requests_module=resolve_optional_service_value(
                context,
                "requests",
            ),
            relay_base_url_configured="youtube_live_relay_base_url" in context,
            relay_base_url=resolve_optional_service_value(
                context,
                "youtube_live_relay_base_url",
            ),
            relay_token=resolve_optional_service_value(
                context,
                "youtube_live_relay_token",
            ),
            relay_auth_header=resolve_optional_service_value(
                context,
                "youtube_live_relay_auth_header",
            ),
            youtube_live_probe=resolve_optional_service_callable(
                context,
                "youtube_live_probe",
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


def _string(value: Any) -> str:
    return str(value or "").strip()


def _json_unescape(value: str) -> str:
    text = _string(value)
    if not text:
        return ""
    try:
        return json.loads(f'"{text}"')
    except Exception:
        return text.replace("\\u0026", "&").replace("\\/", "/")


def _empty_probe(error: str, *, channel_exists: bool = False) -> Dict[str, Any]:
    return {
        "videoId": "",
        "isLive": False,
        "channelExists": bool(channel_exists),
        "channelId": "",
        "channelName": "",
        "hlsUrl": "",
        "title": "",
        "error": error,
    }


def _normalize_channel(value: Any) -> str:
    channel = _string(value)
    if not channel:
        return ""
    return channel[1:] if channel.startswith("@") else channel


def _normalize_probe(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_probe("Invalid YouTube probe payload")
    video_id = _string(payload.get("videoId"))
    if video_id and not VIDEO_ID_RE.match(video_id):
        video_id = ""
    return {
        "videoId": video_id,
        "isLive": bool(payload.get("isLive")) and bool(video_id),
        "channelExists": bool(payload.get("channelExists")),
        "channelId": _string(payload.get("channelId")),
        "channelName": _string(payload.get("channelName")),
        "hlsUrl": _string(payload.get("hlsUrl")),
        "title": _string(payload.get("title")),
        "error": _string(payload.get("error")),
    }


def parse_channel_html(html: str) -> Dict[str, Any]:
    source = str(html or "")
    channel_exists = '"channelId"' in source or "og:url" in source

    channel_id = ""
    channel_id_match = re.search(r'"channelId"\s*:\s*"([^"]+)"', source)
    if channel_id_match:
        channel_id = _json_unescape(channel_id_match.group(1))

    channel_name = ""
    owner_match = re.search(r'"ownerChannelName"\s*:\s*"([^"]+)"', source)
    if owner_match:
        channel_name = _json_unescape(owner_match.group(1))
    else:
        author_match = re.search(r'"author"\s*:\s*"([^"]+)"', source)
        if author_match:
            channel_name = _json_unescape(author_match.group(1))

    detected_video_id = ""
    title = ""
    details_index = source.find('"videoDetails"')
    if details_index != -1:
        details_block = source[details_index : details_index + 8_000]
        video_id_match = re.search(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', details_block)
        is_live_match = re.search(r'"isLive"\s*:\s*true', details_block) or re.search(
            r'"isLiveContent"\s*:\s*true',
            details_block,
        )
        if video_id_match and is_live_match:
            detected_video_id = video_id_match.group(1)
        title_match = re.search(r'"title"\s*:\s*"([^"]+)"', details_block)
        if title_match:
            title = _json_unescape(title_match.group(1))

    hls_url = ""
    hls_match = re.search(r'"hlsManifestUrl"\s*:\s*"([^"]+)"', source)
    if hls_match and detected_video_id:
        hls_url = _json_unescape(hls_match.group(1))

    return {
        "videoId": detected_video_id,
        "isLive": bool(detected_video_id),
        "channelExists": channel_exists,
        "channelId": channel_id,
        "channelName": channel_name,
        "hlsUrl": hls_url,
        "title": title,
        "error": "",
    }


def _http_text_get(
    dependencies: YouTubeLiveProbeDependencies,
    url: str,
    *,
    timeout: int,
) -> str:
    headers = {"User-Agent": CHROME_UA, "Accept-Language": "en-US,en;q=0.8"}
    if dependencies.http_text_get is not None:
        return dependencies.http_text_get(
            url,
            timeout=timeout,
            headers=headers,
        )
    if dependencies.requests_module is None:
        raise RuntimeError("http_text_get unavailable")
    response = dependencies.requests_module.get(
        url,
        timeout=timeout,
        headers=headers,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def _http_json_get(
    dependencies: YouTubeLiveProbeDependencies,
    url: str,
    *,
    timeout: int,
) -> Dict[str, Any]:
    headers = {"User-Agent": CHROME_UA, "Accept-Language": "en-US,en;q=0.8"}
    if dependencies.http_json_get is not None:
        payload = dependencies.http_json_get(
            url,
            timeout=timeout,
            headers=headers,
        )
        return payload if isinstance(payload, dict) else {}
    if dependencies.requests_module is None:
        raise RuntimeError("http_json_get unavailable")
    response = dependencies.requests_module.get(
        url,
        timeout=timeout,
        headers=headers,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _relay_base_url(
    dependencies: YouTubeLiveProbeDependencies,
) -> str:
    if dependencies.relay_base_url_configured:
        return _string(dependencies.relay_base_url).rstrip("/")
    return _string(os.environ.get(YOUTUBE_RELAY_BASE_ENV)).rstrip("/")


def _relay_token(
    dependencies: YouTubeLiveProbeDependencies,
) -> str:
    return _string(
        dependencies.relay_token
        or os.environ.get(YOUTUBE_RELAY_TOKEN_ENV)
        or os.environ.get("RELAY_SHARED_SECRET")
    )


def _relay_auth_header(
    dependencies: YouTubeLiveProbeDependencies,
) -> str:
    header = _string(
        dependencies.relay_auth_header
        or os.environ.get(YOUTUBE_RELAY_AUTH_HEADER_ENV)
        or os.environ.get("RELAY_AUTH_HEADER")
    )
    return header or "x-relay-key"


def _try_relay(
    dependencies: YouTubeLiveProbeDependencies,
    *,
    channel: str = "",
    video_id: str = "",
) -> Optional[Dict[str, Any]]:
    base_url = _relay_base_url(dependencies)
    if not base_url:
        return None
    relay_endpoint = base_url if base_url.rstrip("/").endswith(("/youtube-live", "/youtube/live")) else f"{base_url}/youtube-live"
    params: Dict[str, str] = {}
    if channel:
        params["channel"] = channel
    if video_id and VIDEO_ID_RE.match(video_id):
        params["videoId"] = video_id
    if not params:
        return None
    separator = "&" if "?" in relay_endpoint else "?"
    relay_url = f"{relay_endpoint}{separator}{urlencode(params)}"
    headers = {"User-Agent": CHROME_UA, "Accept": "application/json"}
    token = _relay_token(dependencies)
    if token:
        relay_header = _relay_auth_header(dependencies)
        headers[relay_header] = token
        if relay_header.lower() != "authorization":
            headers["Authorization"] = f"Bearer {token}"
    if dependencies.http_json_get is not None:
        payload = dependencies.http_json_get(
            relay_url,
            timeout=8,
            headers=headers,
        )
        return payload if isinstance(payload, dict) else None
    if dependencies.requests_module is None:
        raise RuntimeError("http_json_get unavailable")
    response = dependencies.requests_module.get(
        relay_url,
        timeout=8,
        headers=headers,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def _try_channel_scrape(
    dependencies: YouTubeLiveProbeDependencies,
    channel: str,
) -> Optional[Dict[str, Any]]:
    normalized_channel = _normalize_channel(channel)
    if not normalized_channel:
        return None
    html = _http_text_get(
        dependencies,
        f"https://www.youtube.com/@{normalized_channel}/live",
        timeout=10,
    )
    return parse_channel_html(html)


def _try_oembed(
    dependencies: YouTubeLiveProbeDependencies,
    video_id: str,
) -> Optional[Dict[str, Any]]:
    if not VIDEO_ID_RE.match(video_id):
        return None
    payload = _http_json_get(
        dependencies,
        f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={quote_plus(video_id)}&format=json",
        timeout=5,
    )
    return {
        "videoId": video_id,
        "isLive": False,
        "channelExists": True,
        "channelId": "",
        "channelName": _string(payload.get("author_name")),
        "hlsUrl": "",
        "title": _string(payload.get("title")),
        "error": "",
    }


def _fetch_live_stream_info(
    ctx: Mapping[str, Any],
    *,
    channel: str = "",
    video_id: str = "",
) -> Dict[str, Any]:
    return _fetch_live_stream_info_with_dependencies(
        YouTubeLiveProbeDependencies.from_context(ctx),
        channel=channel,
        video_id=video_id,
    )


def _fetch_live_stream_info_with_dependencies(
    dependencies: YouTubeLiveProbeDependencies,
    *,
    channel: str = "",
    video_id: str = "",
) -> Dict[str, Any]:
    if dependencies.youtube_live_probe is not None:
        return _normalize_probe(
            dependencies.youtube_live_probe(
                channel=channel,
                video_id=video_id,
            )
        )

    relay_error = ""
    try:
        relayed = _try_relay(
            dependencies,
            channel=channel,
            video_id=video_id,
        )
        if relayed:
            normalized = _normalize_probe(relayed)
            if normalized.get("videoId") or (video_id and normalized.get("channelExists")):
                return normalized
            if normalized.get("error"):
                relay_error = normalized["error"]
    except Exception as exc:
        relay_error = f"Relay failed: {exc}"

    if video_id:
        try:
            oembed = _try_oembed(dependencies, video_id)
            if oembed:
                return _normalize_probe(oembed)
        except Exception as exc:
            if not channel:
                return _empty_probe(f"{relay_error}; OEmbed failed: {exc}".strip("; "))

    if channel:
        try:
            scraped = _try_channel_scrape(dependencies, channel)
            if scraped:
                return _normalize_probe(scraped)
        except Exception as exc:
            if not video_id:
                error = f"{relay_error}; Channel scrape failed: {exc}".strip("; ")
                return _empty_probe(error, channel_exists=True)

    return _empty_probe(relay_error or "Failed to detect live status", channel_exists=bool(channel))


def _cache_key(channel: str = "", video_id: str = "") -> str:
    normalized_channel = _normalize_channel(channel)
    normalized_video_id = video_id if VIDEO_ID_RE.match(video_id or "") else ""
    return f"vid:{normalized_video_id or '-'}:ch:{normalized_channel or '-'}:v1"


def probe_youtube_live(
    ctx: Mapping[str, Any],
    *,
    channel: str = "",
    video_id: str = "",
) -> Dict[str, Any]:
    return _probe_youtube_live(
        YouTubeLiveProbeDependencies.from_context(ctx),
        channel=channel,
        video_id=video_id,
    )


def _probe_youtube_live(
    dependencies: YouTubeLiveProbeDependencies,
    *,
    channel: str = "",
    video_id: str = "",
) -> Dict[str, Any]:
    channel = _string(channel)
    video_id = _string(video_id)
    if not channel and not video_id:
        return _empty_probe("Missing channel or videoId")
    if video_id and not VIDEO_ID_RE.match(video_id):
        video_id = ""

    cache_key = _cache_key(channel, video_id)
    if dependencies.get_cached_json is not None:
        cached = dependencies.get_cached_json(
            YOUTUBE_LIVE_PROBE_NAMESPACE,
            cache_key,
        )
        if isinstance(cached, dict):
            return _normalize_probe(cached)
    store = dependencies.snapshot_store
    if store is not None:
        try:
            cached = store.get(YOUTUBE_LIVE_PROBE_NAMESPACE, cache_key)
            if isinstance(cached, dict):
                return _normalize_probe(cached)
        except Exception:
            pass

    result = _normalize_probe(
        _fetch_live_stream_info_with_dependencies(
            dependencies,
            channel=channel,
            video_id=video_id,
        )
    )
    if not result.get("videoId"):
        if store is not None:
            try:
                stale = store.get_stale(YOUTUBE_LIVE_PROBE_NAMESPACE, cache_key)
                if isinstance(stale, dict):
                    normalized_stale = _normalize_probe(stale)
                    if normalized_stale.get("videoId"):
                        normalized_stale["error"] = result.get("error") or "using stale YouTube live probe"
                        result = normalized_stale
            except Exception:
                pass
    ttl = YOUTUBE_LIVE_POSITIVE_TTL_SECONDS if result.get("videoId") else YOUTUBE_LIVE_NEGATIVE_TTL_SECONDS
    if dependencies.set_cached_json is not None:
        try:
            dependencies.set_cached_json(
                YOUTUBE_LIVE_PROBE_NAMESPACE,
                cache_key,
                result,
                ttl,
            )
        except Exception:
            pass
    if store is not None:
        try:
            store.set(YOUTUBE_LIVE_PROBE_NAMESPACE, cache_key, result, ttl)
        except Exception:
            pass
    return result
