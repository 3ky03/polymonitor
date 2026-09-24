from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode
from xml.etree import ElementTree

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
)

try:
    import requests
except ImportError:  # pragma: no cover - watcher validates dependency.
    requests = None


TECH_PANEL_CACHE_KEY = "v1"
TECH_PANEL_TTL_SECONDS = 10 * 60
TECH_PANEL_IDS = (
    "ai-model-race",
    "big-tech-market-cap",
    "consumer-app-pulse",
)
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_RANKINGS_URL = "https://openrouter.ai/rankings?view=week"
OPENROUTER_MODEL_RANKINGS_ACTION = "40824635c5eb77626bdf6795ffbf382c0862b321e1"
OPENROUTER_USAGE_LIMIT = 20

AI_ENTITIES = (
    ("OpenAI", "OPENAI", ("openai", "chatgpt", "gpt")),
    ("Anthropic", "ANTH", ("anthropic", "claude")),
    ("Google", "GOOGL", ("google", "gemini", "deepmind")),
    ("Meta", "META", ("meta", "llama")),
    ("xAI", "XAI", ("xai", "grok")),
    ("Mistral", "MISTRAL", ("mistral",)),
)

BIG_TECH_SYMBOLS = (
    ("NVIDIA", "NVDA", "AI"),
    ("Apple", "AAPL", "DEVICE"),
    ("Microsoft", "MSFT", "AI"),
    ("Alphabet", "GOOGL", "SEARCH"),
    ("Amazon", "AMZN", "CLOUD"),
    ("Meta", "META", "SOCIAL"),
    ("Tesla", "TSLA", "EV"),
    ("Broadcom", "AVGO", "CHIP"),
    ("TSMC", "TSM", "CHIP"),
    ("Oracle", "ORCL", "CLOUD"),
    ("Netflix", "NFLX", "MEDIA"),
    ("AMD", "AMD", "CHIP"),
    ("SAP", "SAP", "SOFTWARE"),
    ("ASML", "ASML", "CHIP"),
    ("Salesforce", "CRM", "SAAS"),
    ("Cisco", "CSCO", "NETWORK"),
    ("IBM", "IBM", "AI"),
    ("Qualcomm", "QCOM", "CHIP"),
    ("Adobe", "ADBE", "SAAS"),
    ("Palantir", "PLTR", "AI"),
    ("ServiceNow", "NOW", "SAAS"),
    ("Shopify", "SHOP", "COMMERCE"),
    ("Arm", "ARM", "CHIP"),
    ("Intuit", "INTU", "SOFTWARE"),
)

# Approximate shares outstanding used only when Yahoo chart metadata omits
# marketCap. This keeps the panel ranked by capitalization instead of falling
# back to plain price sorting.
BIG_TECH_SHARES_OUTSTANDING = {
    "NVDA": 24_300_000_000,
    "AAPL": 14_800_000_000,
    "MSFT": 7_430_000_000,
    "GOOGL": 12_100_000_000,
    "AMZN": 10_800_000_000,
    "META": 2_500_000_000,
    "TSLA": 3_230_000_000,
    "AVGO": 4_680_000_000,
    "ORCL": 2_830_000_000,
    "NFLX": 420_000_000,
    "AMD": 1_620_000_000,
    "CRM": 960_000_000,
    "CSCO": 3_980_000_000,
    "IBM": 930_000_000,
    "QCOM": 1_090_000_000,
    "ADBE": 430_000_000,
    "PLTR": 2_380_000_000,
    "NOW": 207_000_000,
    "SHOP": 1_300_000_000,
    "ARM": 1_050_000_000,
    "INTU": 280_000_000,
}

APP_ENTITIES = (
    ("TikTok", "TIKTOK", ("tiktok",)),
    ("ChatGPT", "CHATGPT", ("chatgpt", "openai")),
    ("Instagram", "IG", ("instagram",)),
    ("YouTube", "YOUTUBE", ("youtube",)),
    ("Temu", "TEMU", ("temu",)),
    ("CapCut", "CAPCUT", ("capcut",)),
)

AI_NEWS_QUERY = (
    '("OpenAI" OR "Anthropic" OR "Google Gemini" OR "xAI Grok" OR "Meta Llama") '
    '("AI model" OR benchmark OR "model release" OR API OR outage OR valuation OR regulation) when:7d'
)
APP_NEWS_QUERY = (
    '("App Store" OR "Google Play" OR TikTok OR ChatGPT OR Instagram OR YouTube) '
    '(ranking OR downloads OR ban OR lawsuit OR regulation OR update) when:7d'
)
AI_PRODUCT_SIGNAL_QUERY = (
    '("ChatGPT" OR "Claude" OR "Google Gemini" OR "Grok" OR "Meta AI" OR "Llama") '
    '("product update" OR rollout OR feature OR app OR subscription OR "new model") when:14d'
)

APP_STORE_CHART_FEEDS = (
    ("DOWNLOADS", "US Top Free", "us", "topfreeapplications", None),
    ("GROSSING", "US Top Grossing", "us", "topgrossingapplications", None),
    ("PAID", "US Top Paid", "us", "toppaidapplications", None),
    ("GAMES", "US Free Games", "us", "topfreeapplications", "6014"),
    ("GAMES", "US Paid Games", "us", "toppaidapplications", "6014"),
    ("NEWS", "US News", "us", "topfreeapplications", "6009"),
    ("PRODUCTIVITY", "US Productivity", "us", "topfreeapplications", "6007"),
    ("SOCIAL", "US Social", "us", "topfreeapplications", "6005"),
    ("PHOTO", "US Photo/Video", "us", "topfreeapplications", "6008"),
    ("FINANCE", "US Finance", "us", "topfreeapplications", "6015"),
    ("DOWNLOADS", "GB Top Free", "gb", "topfreeapplications", None),
    ("DOWNLOADS", "CA Top Free", "ca", "topfreeapplications", None),
    ("DOWNLOADS", "AU Top Free", "au", "topfreeapplications", None),
    ("DOWNLOADS", "JP Top Free", "jp", "topfreeapplications", None),
    ("DOWNLOADS", "KR Top Free", "kr", "topfreeapplications", None),
    ("DOWNLOADS", "DE Top Free", "de", "topfreeapplications", None),
    ("DOWNLOADS", "FR Top Free", "fr", "topfreeapplications", None),
    ("DOWNLOADS", "SG Top Free", "sg", "topfreeapplications", None),
    ("DOWNLOADS", "HK Top Free", "hk", "topfreeapplications", None),
    ("DOWNLOADS", "IN Top Free", "in", "topfreeapplications", None),
    ("DOWNLOADS", "BR Top Free", "br", "topfreeapplications", None),
    ("DOWNLOADS", "MX Top Free", "mx", "topfreeapplications", None),
)


@dataclass(frozen=True)
class TechPanelsDependencies:
    settings: Any
    application: Any
    http_text_get: Callable[..., Any] | None
    http_text_post: Callable[..., Any] | None
    http_json_get: Callable[..., Any] | None
    get_yahoo_market_snapshot: Callable[..., Any] | None
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    get_snapshot_payload: Callable[..., Any] | None
    snapshot_store: Any

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> TechPanelsDependencies:
        return cls(
            settings=resolve_optional_service_value(context, "SETTINGS"),
            application=resolve_optional_service_value(context, "app"),
            http_text_get=resolve_optional_service_callable(
                context,
                "http_text_get",
            ),
            http_text_post=resolve_optional_service_callable(
                context,
                "http_text_post",
            ),
            http_json_get=resolve_optional_service_callable(
                context,
                "http_json_get",
            ),
            get_yahoo_market_snapshot=resolve_optional_service_callable(
                context,
                "get_yahoo_market_snapshot",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
            get_snapshot_payload=resolve_optional_service_callable(
                context,
                "get_snapshot_payload",
            ),
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
        )


TechPanelsContext = Mapping[str, Any] | TechPanelsDependencies


def _dependencies(
    context: TechPanelsContext,
) -> TechPanelsDependencies:
    if isinstance(context, TechPanelsDependencies):
        return context
    return TechPanelsDependencies.from_context(context)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def tech_panel_namespace(panel_id: str) -> str:
    return f"runtime:tech:{panel_id}"


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _strip_html(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _compact_source(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:34] if text else "source"


def _format_price(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    return f"{number:,.{digits}f}"


def _format_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    return f"{number:+.2f}%"


def _format_usd(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    sign = "-" if number < 0 else ""
    number = abs(number)
    for suffix, divisor in (("T", 1_000_000_000_000), ("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if number >= divisor:
            return f"{sign}${number / divisor:.2f}{suffix}" if suffix == "T" else f"{sign}${number / divisor:.1f}{suffix}"
    return f"{sign}${number:.0f}"


def _format_compact_number(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    sign = "-" if number < 0 else ""
    number = abs(number)
    for suffix, divisor in (("T", 1_000_000_000_000), ("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if number >= divisor:
            return f"{sign}{number / divisor:.2f}{suffix}" if suffix in {"T", "B"} else f"{sign}{number / divisor:.1f}{suffix}"
    return f"{sign}{number:,.0f}"


def _tone(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "neutral"
    if number > 0:
        return "up"
    if number < 0:
        return "down"
    return "neutral"


def _payload(
    panel_id: str,
    *,
    title: str,
    items: List[Dict[str, Any]],
    summary: Optional[Dict[str, Any]] = None,
    sources: Optional[Dict[str, str]] = None,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    source_states = dict(sources or {})
    has_ok_source = any(str(value).lower() == "ok" for value in source_states.values())
    resolved_status = status or ("ok" if items and has_ok_source else "partial" if items else "empty")
    return {
        "generatedAt": utc_now_iso(),
        "status": resolved_status,
        "panelId": panel_id,
        "title": title,
        "summary": dict(summary or {}),
        "sources": source_states,
        "items": items,
    }


def _http_text_get(
    ctx: TechPanelsContext,
    url: str,
    *,
    timeout: int = 12,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    getter = _dependencies(ctx).http_text_get
    if getter is not None:
        return getter(url, timeout=timeout, headers=headers)
    if requests is None:
        raise RuntimeError("requests package is required")
    response = requests.get(url, timeout=timeout, headers=headers)
    response.raise_for_status()
    return response.text


def _http_text_post(
    ctx: TechPanelsContext,
    url: str,
    *,
    data: str,
    timeout: int = 12,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    poster = _dependencies(ctx).http_text_post
    if poster is not None:
        return poster(url, data=data, timeout=timeout, headers=headers)
    if requests is None:
        raise RuntimeError("requests package is required")
    response = requests.post(url, data=data, timeout=timeout, headers=headers)
    response.raise_for_status()
    return response.text


def _http_json_get(
    ctx: TechPanelsContext,
    url: str,
    *,
    timeout: int = 12,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    getter = _dependencies(ctx).http_json_get
    if getter is not None:
        return getter(url, timeout=timeout, headers=headers)
    if requests is None:
        raise RuntimeError("requests package is required")
    response = requests.get(url, timeout=timeout, headers=headers)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _news_url(ctx: TechPanelsContext, query: str) -> str:
    settings = _dependencies(ctx).settings
    base = str(
        getattr(settings, "tech_google_news_rss_url", "")
        or getattr(settings, "google_news_rss_url", "")
        or getattr(settings, "finance_google_news_rss_url", "")
        or "https://news.google.com/rss/search"
    ).rstrip("?")
    return f"{base}?{urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})}"


def _parse_rss_items(xml_text: str, *, panel_id: str, limit: int) -> List[Dict[str, Any]]:
    root = ElementTree.fromstring(xml_text)
    rows: List[Dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = _strip_html(item.findtext("title"))
        if not title:
            continue
        source = _strip_html(item.findtext("source")) or _compact_source(title.split(" - ")[-1] if " - " in title else "")
        clean_title = re.sub(r"\s+-\s+[^-]{2,48}$", "", title).strip() or title
        summary = _strip_html(item.findtext("description")) or clean_title
        published_at = None
        try:
            parsed = parsedate_to_datetime(item.findtext("pubDate") or "")
            if parsed:
                published_at = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            published_at = None
        rows.append(
            {
                "id": item.findtext("guid") or item.findtext("link") or f"{panel_id}:{len(rows)}",
                "label": _detect_label(clean_title, panel_id),
                "symbol": _detect_symbol(clean_title, panel_id),
                "title": clean_title,
                "summary": summary,
                "source": source,
                "url": item.findtext("link"),
                "publishedAt": published_at,
                "metricLabel": _detect_event_label(clean_title),
                "secondaryLabel": source,
                "tags": _detect_tags(clean_title, panel_id),
                "tone": _detect_tone(clean_title),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _detect_label(title: str, panel_id: str) -> str:
    haystack = title.lower()
    candidates = APP_ENTITIES if panel_id == "consumer-app-pulse" else AI_ENTITIES
    for label, _symbol, aliases in candidates:
        if any(alias in haystack for alias in aliases):
            return label
    return "TECH"


def _detect_symbol(title: str, panel_id: str) -> str:
    haystack = title.lower()
    candidates = APP_ENTITIES if panel_id == "consumer-app-pulse" else AI_ENTITIES
    for _label, symbol, aliases in candidates:
        if any(alias in haystack for alias in aliases):
            return symbol
    return "SIGNAL"


def _detect_event_label(title: str) -> str:
    haystack = title.lower()
    checks = (
        ("release", "RELEASE"),
        ("launch", "LAUNCH"),
        ("benchmark", "BENCH"),
        ("rank", "RANK"),
        ("price", "PRICE"),
        ("valuation", "VALUE"),
        ("funding", "VALUE"),
        ("outage", "OUTAGE"),
        ("ban", "BAN"),
        ("lawsuit", "LEGAL"),
        ("regulat", "REG"),
    )
    for needle, label in checks:
        if needle in haystack:
            return label
    return "WATCH"


def _detect_tags(title: str, panel_id: str) -> List[str]:
    haystack = title.lower()
    tags: List[str] = []
    for needle, tag in (
        ("benchmark", "BENCHMARK"),
        ("release", "RELEASE"),
        ("launch", "RELEASE"),
        ("api", "API"),
        ("pricing", "PRICING"),
        ("price", "PRICING"),
        ("valuation", "VALUATION"),
        ("funding", "VALUATION"),
        ("outage", "OUTAGE"),
        ("security", "RISK"),
        ("regulat", "REG"),
        ("lawsuit", "LEGAL"),
        ("ban", "BAN"),
        ("download", "DOWNLOAD"),
        ("ranking", "RANK"),
        ("rank", "RANK"),
    ):
        if needle in haystack and tag not in tags:
            tags.append(tag)
    if not tags:
        tags.append("AI" if panel_id == "ai-model-race" else "APP")
    return tags[:4]


def _detect_tone(title: str) -> str:
    haystack = title.lower()
    if any(word in haystack for word in ("outage", "lawsuit", "ban", "delay", "probe", "investigation", "risk")):
        return "down"
    if any(word in haystack for word in ("release", "launch", "raise", "record", "tops", "surge", "growth")):
        return "up"
    if any(word in haystack for word in ("regulat", "valuation", "benchmark", "ranking", "rank")):
        return "watch"
    return "neutral"


def _entity_summary(rows: List[Dict[str, Any]], entities: tuple) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {label: 0 for label, _symbol, _aliases in entities}
    tones: Dict[str, str] = {label: "neutral" for label, _symbol, _aliases in entities}
    latest: Dict[str, str] = {}
    for row in rows:
        label = str(row.get("label") or "")
        if label not in counts:
            continue
        counts[label] += 1
        if label not in latest and row.get("title"):
            latest[label] = str(row.get("title"))
        if row.get("tone") in {"up", "down", "watch"}:
            tones[label] = str(row.get("tone"))
    summary_rows = []
    for label, symbol, _aliases in entities:
        summary_rows.append({"label": label, "symbol": symbol, "count": counts[label], "tone": tones[label], "latest": latest.get(label)})
    summary_rows.sort(key=lambda item: (int(item.get("count") or 0), str(item.get("label"))), reverse=True)
    return summary_rows


def _provider_from_model_id(value: Any) -> str:
    model_id = str(value or "").strip()
    if "/" not in model_id:
        return "AI"
    provider = model_id.split("/", 1)[0]
    return provider.replace("-", " ").replace("_", " ").upper()[:14]


def _openrouter_model_url(model_id: Any) -> str:
    return f"https://openrouter.ai/{str(model_id or '').strip()}"


def _format_model_price(pricing: Any) -> str:
    if not isinstance(pricing, dict):
        return "PRICE --"
    prompt = _safe_float(pricing.get("prompt"))
    completion = _safe_float(pricing.get("completion"))
    if prompt == 0 and completion == 0:
        return "FREE"
    if prompt is None and completion is None:
        return "PRICE --"
    prompt_label = f"${(prompt or 0) * 1_000_000:.2f}"
    completion_label = f"${(completion or 0) * 1_000_000:.2f}"
    return f"{prompt_label}/{completion_label} 1M"


def _format_token_price(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    if number < 0:
        return "--"
    return "FREE" if number == 0 else f"${number * 1_000_000:.2f}/1M"


def _row_category(rows: List[Dict[str, Any]], category: str, tag: str, metric_label: Optional[str] = None) -> List[Dict[str, Any]]:
    for row in rows:
        row["category"] = category
        tags = [tag] + [str(value) for value in row.get("tags") or [] if str(value) != tag]
        row["tags"] = tags[:4]
        if metric_label and not row.get("metricLabel"):
            row["metricLabel"] = metric_label
    return rows


def _parse_openrouter_rsc_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    for line in str(text or "").splitlines():
        if ":" not in line:
            continue
        prefix, payload = line.split(":", 1)
        if not prefix.isdigit():
            continue
        payload = payload.strip()
        if not payload or payload[0] not in "[{":
            continue
        try:
            value, _offset = decoder.raw_decode(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return value
    return []


def _openrouter_models(ctx: dict) -> List[Dict[str, Any]]:
    payload = _http_json_get(
        ctx,
        OPENROUTER_MODELS_URL,
        timeout=14,
        headers={"User-Agent": "polydata-tech-panels/1.0", "Accept": "application/json"},
    )
    rows = payload.get("data") if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)]


def _openrouter_latest_model_rows(models: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    sorted_models = sorted(models, key=lambda item: int(_safe_float(item.get("created")) or 0), reverse=True)
    for model in sorted_models[:limit]:
        model_id = str(model.get("id") or model.get("canonical_slug") or "").strip()
        if not model_id:
            continue
        created = _safe_float(model.get("created"))
        published_at = datetime.fromtimestamp(created, timezone.utc).isoformat().replace("+00:00", "Z") if created else None
        context_length = _safe_float(model.get("context_length"))
        rows.append(
            {
                "id": f"openrouter-model:{model_id}",
                "label": _provider_from_model_id(model_id),
                "symbol": "MODEL",
                "title": str(model.get("name") or model_id),
                "summary": _strip_html(model.get("description"))[:180],
                "source": "OpenRouter Models",
                "url": _openrouter_model_url(model_id),
                "publishedAt": published_at,
                "metric": context_length,
                "metricLabel": "NEW",
                "metricUnit": "MODEL",
                "secondaryLabel": f"CTX {_format_compact_number(context_length)}",
                "tags": ["NEW MODEL", "OPENROUTER"],
                "tone": "up",
            }
        )
    return rows


def _openrouter_usage_rows(ctx: dict, models: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    response = _http_text_post(
        ctx,
        OPENROUTER_RANKINGS_URL,
        data=json.dumps(["week"]),
        timeout=16,
        headers={
            "User-Agent": "polydata-tech-panels/1.0",
            "Accept": "text/x-component",
            "Content-Type": "text/plain;charset=UTF-8",
            "Next-Action": OPENROUTER_MODEL_RANKINGS_ACTION,
        },
    )
    raw_rows = _parse_openrouter_rsc_json(response)
    model_lookup = {
        str(model.get("canonical_slug") or model.get("id") or "").lower(): model
        for model in models
        if model.get("canonical_slug") or model.get("id")
    }
    totals: Dict[str, Dict[str, Any]] = {}
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("variant_permaslug") or row.get("model_permaslug") or "").strip()
        if not slug:
            continue
        total_tokens = (_safe_float(row.get("total_prompt_tokens")) or 0) + (_safe_float(row.get("total_completion_tokens")) or 0)
        if total_tokens <= 0:
            continue
        bucket = totals.setdefault(slug, {"tokens": 0.0, "count": 0.0, "change": None, "date": row.get("date")})
        bucket["tokens"] += total_tokens
        bucket["count"] += _safe_float(row.get("count")) or 0
        if row.get("change") is not None:
            bucket["change"] = row.get("change")
    ranked = sorted(totals.items(), key=lambda pair: pair[1]["tokens"], reverse=True)
    rows: List[Dict[str, Any]] = []
    for rank, (slug, aggregate) in enumerate(ranked[:limit], start=1):
        base_slug = re.sub(r"-20\d{6}$", "", slug)
        model = model_lookup.get(slug.lower()) or model_lookup.get(base_slug.lower()) or {}
        title = str(model.get("name") or base_slug.replace("/", ": ").replace("-", " "))
        change = _safe_float(aggregate.get("change"))
        rows.append(
            {
                "id": f"openrouter-usage:{slug}",
                "label": _provider_from_model_id(slug),
                "symbol": "USAGE",
                "title": f"#{rank} {title}",
                "summary": f"{_format_compact_number(aggregate.get('count'))} requests on OpenRouter weekly leaderboard",
                "source": "OpenRouter Usage",
                "url": _openrouter_model_url(base_slug),
                "metric": aggregate["tokens"],
                "metricLabel": f"{_format_compact_number(aggregate['tokens'])} TOK",
                "metricUnit": "TOKENS",
                "secondaryLabel": f"{_format_compact_number(aggregate.get('count'))} REQ",
                "tags": ["USAGE", "OPENROUTER"],
                "tone": _tone(change) if change is not None else "watch",
                "rank": rank,
                "change": change,
            }
        )
    return rows


def _openrouter_price_rows(models: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    priced_models = []
    for model in models:
        pricing = model.get("pricing")
        if not isinstance(pricing, dict):
            continue
        prompt = _safe_float(pricing.get("prompt"))
        completion = _safe_float(pricing.get("completion"))
        if prompt is None and completion is None:
            continue
        if (prompt is not None and prompt < 0) or (completion is not None and completion < 0):
            continue
        priced_models.append((prompt if prompt is not None else 999, completion if completion is not None else 999, model))
    priced_models.sort(key=lambda item: (item[0], item[1], str(item[2].get("name") or item[2].get("id") or "")))
    for rank, (_prompt, _completion, model) in enumerate(priced_models[:limit], start=1):
        model_id = str(model.get("id") or model.get("canonical_slug") or "").strip()
        if not model_id:
            continue
        pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
        rows.append(
            {
                "id": f"openrouter-price:{model_id}",
                "label": _provider_from_model_id(model_id),
                "symbol": "PRICE",
                "title": str(model.get("name") or model_id),
                "summary": "OpenRouter listed API token pricing",
                "source": "OpenRouter API Price",
                "url": _openrouter_model_url(model_id),
                "metric": _safe_float(pricing.get("prompt")),
                "metricLabel": f"IN {_format_token_price(pricing.get('prompt'))}",
                "metricUnit": "INPUT",
                "secondaryLabel": f"OUT {_format_token_price(pricing.get('completion'))}",
                "tags": ["API PRICE", "OPENROUTER"],
                "tone": "watch",
                "rank": rank,
                "category": "API PRICE",
            }
        )
    return rows


def build_ai_model_race_payload(ctx: dict, limit: int) -> Dict[str, Any]:
    sources = {"news": "empty", "openRouterUsage": "empty", "productSignal": "empty", "apiPrice": "empty"}
    error_summary = None
    models: List[Dict[str, Any]] = []
    news_rows: List[Dict[str, Any]] = []
    usage_rows: List[Dict[str, Any]] = []
    product_rows: List[Dict[str, Any]] = []
    price_rows: List[Dict[str, Any]] = []
    try:
        models = _openrouter_models(ctx)
    except Exception as exc:
        error_summary = str(exc)
        models = []
    try:
        usage_rows = _row_category(_openrouter_usage_rows(ctx, models, OPENROUTER_USAGE_LIMIT), "USAGE", "USAGE")
        sources["openRouterUsage"] = "ok" if usage_rows else "empty"
    except Exception as exc:
        error_summary = str(exc)
        sources["openRouterUsage"] = "error"
    try:
        news_rows = _row_category(
            _parse_rss_items(_http_text_get(ctx, _news_url(ctx, AI_NEWS_QUERY), timeout=14), panel_id="ai-model-race", limit=8),
            "NEWS",
            "NEWS",
        )
        sources["news"] = "ok" if news_rows else "empty"
    except Exception as exc:
        error_summary = str(exc)
        sources["news"] = "error"
    try:
        latest_model_rows = _row_category(_openrouter_latest_model_rows(models, 8), "PRODUCT SIGNAL", "PRODUCT")
        product_news_rows = _row_category(
            _parse_rss_items(_http_text_get(ctx, _news_url(ctx, AI_PRODUCT_SIGNAL_QUERY), timeout=14), panel_id="ai-model-race", limit=8),
            "PRODUCT SIGNAL",
            "PRODUCT",
        )
        product_rows = product_news_rows + latest_model_rows
        sources["productSignal"] = "ok" if product_rows else "empty"
    except Exception as exc:
        error_summary = str(exc)
        sources["productSignal"] = "error"
    try:
        price_rows = _openrouter_price_rows(models, 10)
        sources["apiPrice"] = "ok" if price_rows else "empty"
    except Exception as exc:
        error_summary = str(exc)
        sources["apiPrice"] = "error"
    rows = (news_rows[:4] + usage_rows[:OPENROUTER_USAGE_LIMIT] + product_rows[:6] + price_rows[:6])[:limit]
    watchlist_rows = [
        {"label": "News", "symbol": "NEWS", "count": len(news_rows), "tone": "watch"},
        {"label": "Usage", "symbol": "USAGE", "count": len(usage_rows), "tone": "up"},
        {"label": "Product Signal", "symbol": "PRODUCT SIGNAL", "count": len(product_rows), "tone": "up"},
        {"label": "API Price", "symbol": "API PRICE", "count": len(price_rows), "tone": "watch"},
    ]
    summary_rows = _entity_summary(rows, AI_ENTITIES)
    return _payload(
        "ai-model-race",
        title="AI MODEL RACE",
        items=rows,
        summary={
            "watchlist": watchlist_rows,
            "entities": summary_rows,
            "query": AI_NEWS_QUERY,
            "productQuery": AI_PRODUCT_SIGNAL_QUERY,
            "error": error_summary,
            "openRouterUsageRows": len(usage_rows),
            "productRows": len(product_rows),
            "priceRows": len(price_rows),
        },
        sources=sources,
    )


def _fetch_quote(
    ctx: TechPanelsContext,
    symbol: str,
) -> Optional[Dict[str, Any]]:
    dependencies = _dependencies(ctx)
    getter = dependencies.get_yahoo_market_snapshot
    if getter is None:
        return None
    try:
        return getter(symbol, interval="30m", range_name="5d", ttl_seconds=300)
    except Exception:
        logger = getattr(dependencies.application, "logger", None)
        if logger:
            logger.exception("tech quote fetch failed symbol=%s", symbol)
        return None


def _market_cap_from_quote(symbol: str, quote: Dict[str, Any]) -> tuple[Optional[float], bool]:
    market_cap = _safe_float(quote.get("marketCap"))
    if market_cap is not None:
        return market_cap, False
    price = _safe_float(quote.get("price"))
    shares = BIG_TECH_SHARES_OUTSTANDING.get(symbol)
    if price is None or not shares:
        return None, False
    return price * shares, True


def build_big_tech_market_cap_payload(ctx: dict, limit: int) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for label, symbol, group in BIG_TECH_SYMBOLS:
        quote = _fetch_quote(ctx, symbol)
        if not isinstance(quote, dict):
            continue
        change = _safe_float(quote.get("changePercent"))
        market_cap, estimated_cap = _market_cap_from_quote(symbol, quote)
        rows.append(
            {
                "id": symbol,
                "label": label,
                "symbol": symbol,
                "title": f"{label} market cap rank watch",
                "metric": market_cap if market_cap is not None else quote.get("price"),
                "metricLabel": _format_usd(market_cap) if market_cap is not None else _format_price(quote.get("price")),
                "metricUnit": "MKT CAP" if market_cap is not None else "PRICE",
                "secondary": quote.get("price"),
                "secondaryLabel": _format_price(quote.get("price")),
                "change": change,
                "changeLabel": _format_pct(change),
                "tags": [group, "MEGA"],
                "tone": _tone(change),
                "points": quote.get("points") or [],
                "marketCap": market_cap,
                "marketCapEstimated": estimated_cap,
                "price": quote.get("price"),
            }
        )
    rows.sort(key=lambda item: (_safe_float(item.get("marketCap")) is not None, _safe_float(item.get("marketCap")) or 0), reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
        row["title"] = f"#{index} {row['label']} mega-cap watch"
    avg = sum(_safe_float(row.get("change")) or 0.0 for row in rows) / len(rows) if rows else None
    return _payload(
        "big-tech-market-cap",
        title="BIG TECH CAP",
        items=rows[:limit],
        summary={"avgChange": avg, "rankBy": "marketCap", "tracked": len(BIG_TECH_SYMBOLS)},
        sources={"yahoo": "ok" if rows else "empty"},
    )


def _nested_value(payload: Dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _apple_chart_url(country: str, chart: str, per_feed_limit: int, genre: Optional[str]) -> str:
    url = f"https://itunes.apple.com/{country}/rss/{chart}/limit={per_feed_limit}"
    if genre:
        url = f"{url}/genre={genre}"
    return f"{url}/json"


def _parse_apple_chart_entries(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    feed = payload.get("feed") if isinstance(payload, dict) else {}
    if not isinstance(feed, dict):
        return []
    results = feed.get("results")
    if isinstance(results, list):
        return [row for row in results if isinstance(row, dict)]
    entries = feed.get("entry")
    if isinstance(entries, dict):
        entries = [entries]
    return [row for row in entries or [] if isinstance(row, dict)]


def _apple_entry_value(app: Dict[str, Any], modern_key: str, legacy_path: tuple[str, ...], default: str = "") -> str:
    direct = app.get(modern_key)
    value = direct if direct not in (None, "") else _nested_value(app, legacy_path)
    return str(value or default).strip()


def _fetch_app_store_rows(ctx: dict, limit: int) -> tuple[List[Dict[str, Any]], int, Dict[str, int]]:
    per_feed_limit = max(3, min(10, math.ceil(limit / 4)))
    rows: List[Dict[str, Any]] = []
    style_counts: Dict[str, int] = {}
    ok_sources = 0
    seen: set[str] = set()
    for style, label, country, chart, genre in APP_STORE_CHART_FEEDS:
        url = _apple_chart_url(country, chart, per_feed_limit, genre)
        try:
            payload = _http_json_get(ctx, url, timeout=9, headers={"User-Agent": "polydata-tech-panels/1.0", "Accept": "application/json"})
        except Exception:
            continue
        entries = _parse_apple_chart_entries(payload)
        if not entries:
            continue
        ok_sources += 1
        for index, app in enumerate(entries[:per_feed_limit], start=1):
            name = _apple_entry_value(app, "name", ("im:name", "label"), "App")
            artist = _apple_entry_value(app, "artistName", ("im:artist", "label"))
            app_id = _apple_entry_value(app, "id", ("id", "attributes", "im:id"), f"{country}-{chart}-{genre or 'all'}-{index}")
            url_value = _apple_entry_value(app, "url", ("link", "attributes", "href"))
            unique = f"{style}:{country}:{chart}:{genre or 'all'}:{app_id}:{index}"
            if unique in seen:
                continue
            seen.add(unique)
            style_counts[style] = style_counts.get(style, 0) + 1
            rows.append(
                {
                    "id": f"app-store-chart:{unique}",
                    "label": name,
                    "symbol": style,
                    "title": f"#{index} {name}",
                    "summary": f"{artist} · {label}",
                    "source": label,
                    "url": url_value,
                    "metric": index,
                    "metricLabel": f"#{index}",
                    "metricUnit": "RANK",
                    "secondaryLabel": country.upper(),
                    "tags": ["APP STORE", style],
                    "tone": "up" if index <= 3 else "neutral",
                    "category": style,
                }
            )
    return rows, ok_sources, style_counts


def _app_tab_rows(rows: List[Dict[str, Any]], per_category_limit: int = 10) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    tabs = ("DOWNLOADS", "GAMES", "NEWS", "GROSSING")
    buckets: Dict[str, List[Dict[str, Any]]] = {key: [] for key in tabs}
    for row in rows:
        category = str(row.get("category") or "")
        if category in buckets:
            buckets[category].append(row)
    selected: List[Dict[str, Any]] = []
    tab_counts: Dict[str, int] = {}
    for category in tabs:
        bucket = buckets[category][:per_category_limit]
        tab_counts[category] = len(bucket)
        selected.extend(bucket)
    return selected, tab_counts


def build_consumer_app_pulse_payload(ctx: dict, limit: int) -> Dict[str, Any]:
    sources = {"appStoreCharts": "empty", "googleNewsRss": "empty"}
    try:
        app_rows, ok_chart_sources, style_counts = _fetch_app_store_rows(ctx, max(limit, 40))
        sources["appStoreCharts"] = "ok" if app_rows else "empty"
    except Exception as exc:
        app_rows = []
        ok_chart_sources = 0
        style_counts = {}
        app_error = str(exc)
        sources["appStoreCharts"] = "error"
    news_limit = 4
    try:
        news_rows = _parse_rss_items(_http_text_get(ctx, _news_url(ctx, APP_NEWS_QUERY), timeout=14), panel_id="consumer-app-pulse", limit=news_limit)
        sources["googleNewsRss"] = "ok" if news_rows else "empty"
    except Exception as exc:
        news_rows = []
        news_error = str(exc)
        sources["googleNewsRss"] = "error"
    rows, tab_counts = _app_tab_rows(app_rows, per_category_limit=10)
    chart_buttons = [
        {"label": "Downloads", "symbol": "DOWNLOADS", "count": tab_counts.get("DOWNLOADS", 0), "tone": "up"},
        {"label": "Games", "symbol": "GAMES", "count": tab_counts.get("GAMES", 0), "tone": "up"},
        {"label": "News", "symbol": "NEWS", "count": tab_counts.get("NEWS", 0), "tone": "watch"},
        {"label": "Grossing", "symbol": "GROSSING", "count": tab_counts.get("GROSSING", 0), "tone": "watch"},
    ]
    return _payload(
        "consumer-app-pulse",
        title="APP PULSE",
        items=rows,
        summary={
            "watchlist": chart_buttons,
            "entities": _entity_summary(news_rows, APP_ENTITIES),
            "query": APP_NEWS_QUERY,
            "appStoreRows": len(app_rows),
            "appStoreSourceCount": len(APP_STORE_CHART_FEEDS),
            "appStoreOkSourceCount": ok_chart_sources,
            "appStoreStyleCounts": style_counts,
            "appStoreTabCounts": tab_counts,
            "newsRows": len(news_rows),
            "appStoreError": locals().get("app_error"),
            "newsError": locals().get("news_error"),
        },
        sources=sources,
    )


def build_tech_panel_payload(ctx: dict, panel_id: str, limit: int = 10) -> Dict[str, Any]:
    limit = max(3, min(60, int(limit or 10)))
    if panel_id == "ai-model-race":
        return build_ai_model_race_payload(ctx, limit)
    if panel_id == "big-tech-market-cap":
        return build_big_tech_market_cap_payload(ctx, limit)
    if panel_id == "consumer-app-pulse":
        return build_consumer_app_pulse_payload(ctx, limit)
    raise KeyError(f"unknown tech panel: {panel_id}")


def build_all_tech_panel_payloads(ctx: dict, limit: int = 24) -> Dict[str, Dict[str, Any]]:
    payloads: Dict[str, Dict[str, Any]] = {}
    for panel_id in TECH_PANEL_IDS:
        try:
            payloads[panel_id] = build_tech_panel_payload(ctx, panel_id, limit=limit)
        except Exception as exc:
            payloads[panel_id] = _payload(panel_id, title=panel_id.upper(), items=[], status="error", sources={"builder": "error"}, summary={"error": str(exc)})
    return payloads


def _read_seeded_snapshot(
    ctx: TechPanelsContext,
    panel_id: str,
    ttl_seconds: int,
) -> Optional[Dict[str, Any]]:
    dependencies = _dependencies(ctx)
    def usable(payload: Dict[str, Any]) -> bool:
        if str(payload.get("status") or "").lower() == "error" and not payload.get("items"):
            return False
        return True

    namespace = tech_panel_namespace(panel_id)
    reader = dependencies.get_cached_json
    if reader is not None:
        redis_payload = reader(namespace, TECH_PANEL_CACHE_KEY)
        if isinstance(redis_payload, dict) and usable(redis_payload):
            dependencies.snapshot_store.set(
                namespace,
                TECH_PANEL_CACHE_KEY,
                redis_payload,
                ttl_seconds,
            )
            return {**redis_payload, "cacheMode": str(redis_payload.get("cacheMode") or "redis-seed")}
    snapshot_store = dependencies.snapshot_store
    if snapshot_store is None:
        return None
    sqlite_payload = snapshot_store.get(namespace, TECH_PANEL_CACHE_KEY)
    if isinstance(sqlite_payload, dict) and usable(sqlite_payload):
        setter = dependencies.set_cached_json
        if setter is not None:
            setter(namespace, TECH_PANEL_CACHE_KEY, sqlite_payload, ttl_seconds)
        return {**sqlite_payload, "cacheMode": str(sqlite_payload.get("cacheMode") or "sqlite-seed")}
    stale_payload = snapshot_store.get_stale(namespace, TECH_PANEL_CACHE_KEY)
    if isinstance(stale_payload, dict) and usable(stale_payload):
        return {**stale_payload, "cacheMode": "stale-seed"}
    return None


def _trim_payload(payload: Dict[str, Any], limit: int) -> Dict[str, Any]:
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    limit = max(0, int(limit or 10))
    return {
        **payload,
        "items": items[:limit],
        "summary": {
            **(payload.get("summary") if isinstance(payload.get("summary"), dict) else {}),
            "count": min(len(items), limit),
            "totalCount": len(items),
        },
    }


def get_tech_panel_snapshot(
    ctx: TechPanelsContext,
    panel_id: str,
    limit: int = 10,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    limit = max(3, min(60, int(limit or 10)))
    settings = dependencies.settings
    ttl_seconds = int(getattr(settings, "tech_runtime_ttl_seconds", TECH_PANEL_TTL_SECONDS) or TECH_PANEL_TTL_SECONDS)
    seeded = _read_seeded_snapshot(dependencies, panel_id, ttl_seconds)
    if seeded is not None:
        return _trim_payload(seeded, limit)

    def _builder() -> Dict[str, Any]:
        return build_tech_panel_payload(
            dependencies,
            panel_id,
            limit=max(limit, 24),
        )

    if dependencies.get_snapshot_payload is not None:
        payload = dependencies.get_snapshot_payload(
            tech_panel_namespace(panel_id),
            TECH_PANEL_CACHE_KEY,
            _builder,
            ttl_seconds=ttl_seconds,
        )
    else:
        payload = _builder()
    return _trim_payload(payload if isinstance(payload, dict) else {}, limit)
