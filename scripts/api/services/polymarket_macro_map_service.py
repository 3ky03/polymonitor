from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
    resolve_service_value,
)


MACRO_MAP_SNAPSHOT_NAMESPACE = "snapshot:macro:polymarket-macro-map"
MACRO_MAP_CACHE_KEY = "panel-v1"
DEFAULT_ITEM_LIMIT = 12
DEFAULT_SEARCH_TERMS = (
    "cpi",
    "inflation",
    "consumer price",
    "pce",
    "fed",
    "fomc",
    "interest rate",
    "rates",
    "recession",
    "gdp",
    "unemployment",
    "payrolls",
    "jobs report",
    "oil",
    "wti",
    "brent",
    "gasoline",
    "energy",
)

MACRO_CATEGORIES = (
    {
        "id": "cpi",
        "label": "CPI / Inflation",
        "marketType": "CPI bucket / inflation prints",
        "terms": ("cpi", "inflation", "consumer price", "headline", "core cpi", "pce", "core pce"),
    },
    {
        "id": "fed",
        "label": "Fed / Rates",
        "marketType": "FOMC decision / target rate",
        "terms": ("fed", "fomc", "rate cut", "rate hike", "interest rate", "rates", "powell"),
    },
    {
        "id": "growth",
        "label": "Growth / Recession",
        "marketType": "GDP / recession / shutdown macro",
        "terms": ("recession", "gdp", "growth", "economy", "shutdown", "default"),
    },
    {
        "id": "labor",
        "label": "Labor / Jobs",
        "marketType": "NFP / unemployment / payrolls",
        "terms": ("unemployment", "payroll", "nfp", "jobs report", "jobless", "wage"),
    },
    {
        "id": "energy",
        "label": "Oil / Energy",
        "marketType": "Oil / gasoline / headline CPI driver",
        "terms": ("oil", "wti", "brent", "gasoline", "energy", "crude", "diesel"),
    },
)


@dataclass(frozen=True)
class PolymarketMacroMapDependencies:
    settings: Any
    application: Any
    http_json_get: Callable[..., Any]
    utc_now_iso: Callable[..., Any]
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    snapshot_store: Any

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> PolymarketMacroMapDependencies:
        return cls(
            settings=resolve_service_value(context, "SETTINGS"),
            application=resolve_service_value(context, "app"),
            http_json_get=resolve_service_callable(
                context,
                "http_json_get",
            ),
            utc_now_iso=resolve_service_callable(
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


PolymarketMacroMapContext = (
    Mapping[str, Any] | PolymarketMacroMapDependencies
)


def _dependencies(
    context: PolymarketMacroMapContext,
) -> PolymarketMacroMapDependencies:
    if isinstance(context, PolymarketMacroMapDependencies):
        return context
    return PolymarketMacroMapDependencies.from_context(context)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            return [item.strip() for item in text.split(",") if item.strip()]
    return [value]


def _float_value(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:
        return None
    return numeric


def _parse_timestamp(value: Any) -> float:
    if not value:
        return 0.0
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _event_tags(event: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    for item in _as_list(event.get("tags")):
        if isinstance(item, dict):
            label = item.get("label") or item.get("name") or item.get("slug")
        else:
            label = item
        text = str(label or "").strip()
        if text:
            tags.append(text)
    return tags


def _normalized_text(*parts: Any) -> str:
    return " ".join(str(part or "").lower() for part in parts)


def _event_haystack(event: Dict[str, Any]) -> str:
    markets = [
        str((market or {}).get("question") or (market or {}).get("title") or "")
        for market in (event.get("markets") or [])
        if isinstance(market, dict)
    ]
    return _normalized_text(
        event.get("title"),
        event.get("slug"),
        event.get("category"),
        event.get("categorySlug"),
        " ".join(_event_tags(event)),
        " ".join(markets),
    )


def _matches_term(haystack: str, term: str) -> bool:
    normalized = str(term or "").strip().lower()
    if not normalized:
        return False
    pattern = r"(?<![a-z0-9])" + r"\s+".join(re.escape(part) for part in normalized.split()) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def _classify_event(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    haystack = _event_haystack(event)
    hits: List[Dict[str, Any]] = []
    for category in MACRO_CATEGORIES:
        if any(_matches_term(haystack, term) for term in category["terms"]):
            hits.append({key: category[key] for key in ("id", "label", "marketType")})
    return hits


def _is_event_ended(event: Dict[str, Any], now_iso: str) -> bool:
    if event.get("closed") is True or event.get("active") is False:
        return True
    end_date = event.get("endDate") or event.get("end_date")
    if not end_date:
        return False
    end_ts = _parse_timestamp(end_date)
    now_ts = _parse_timestamp(now_iso)
    return bool(end_ts and now_ts and end_ts < now_ts)


def _market_active(market: Dict[str, Any]) -> bool:
    if market.get("closed") is True:
        return False
    if market.get("active") is False:
        return False
    if market.get("acceptingOrders") is False:
        return False
    return True


def _market_prices(market: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    prices = _as_list(market.get("outcomePrices") or market.get("outcome_prices"))
    yes_price = _float_value(prices[0]) if prices else None
    no_price = _float_value(prices[1]) if len(prices) > 1 else None
    if no_price is None and yes_price is not None:
        no_price = max(0.0, min(1.0, 1.0 - yes_price))
    return yes_price, no_price


def _normalize_outcome_key(label: Any, fallback: Any = "") -> str:
    source = str(label or fallback or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", source).strip("-")
    return cleaned or "outcome"


def _label_for_market(event_title: str, market: Dict[str, Any]) -> str:
    label = str(market.get("groupItemTitle") or market.get("group_item_title") or "").strip()
    if label:
        return label
    question = str(market.get("question") or market.get("title") or "").strip()
    if event_title and question.startswith(event_title):
        candidate = question[len(event_title) :].strip(" -:·")
        if candidate:
            return candidate
    return question or str(market.get("slug") or market.get("id") or "Outcome")


def _fetch_gamma_events(ctx: PolymarketMacroMapContext, *, order: str, target_events: int = 300, max_pages: int = 4) -> List[Dict[str, Any]]:
    base_url = str(_dependencies(ctx).settings.gamma_api_base or "").rstrip("/")
    if not base_url:
        raise RuntimeError("gamma api base missing")
    events: List[Dict[str, Any]] = []
    seen: set[str] = set()
    limit = 100
    for page in range(max(1, int(max_pages))):
        payload = _dependencies(ctx).http_json_get(
            f"{base_url}/events",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": page * limit,
                "order": order,
                "ascending": "false",
            },
            timeout=12,
            headers={"Accept": "application/json", "User-Agent": "polydata-macro-map/1.0"},
        )
        page_events = payload if isinstance(payload, list) else ((payload or {}).get("events") or (payload or {}).get("data") or [])
        if not isinstance(page_events, list):
            raise ValueError("gamma events payload is not a list")
        if not page_events:
            break
        for event in page_events:
            if not isinstance(event, dict):
                continue
            identity = str(event.get("id") or event.get("slug") or "").strip()
            if not identity or identity in seen:
                continue
            seen.add(identity)
            events.append(event)
            if len(events) >= target_events:
                return events
        if len(page_events) < limit:
            break
    return events


def _merge_unique_events(event_lists: Iterable[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for events in event_lists:
        for event in events:
            identity = str(event.get("id") or event.get("slug") or "").strip()
            if not identity or identity in seen:
                continue
            seen.add(identity)
            merged.append(event)
    return merged


def _search_terms(ctx: PolymarketMacroMapContext) -> tuple[str, ...]:
    configured = getattr(_dependencies(ctx).settings, "polymarket_macro_map_search_terms", ())
    terms = tuple(str(term).strip().lower() for term in configured if str(term).strip())
    return terms or DEFAULT_SEARCH_TERMS


def _event_matches_terms(event: Dict[str, Any], terms: tuple[str, ...]) -> bool:
    haystack = _event_haystack(event)
    return any(_matches_term(haystack, term) for term in terms)


def _normalize_event(event: Dict[str, Any], categories: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    markets = [market for market in (event.get("markets") or []) if isinstance(market, dict) and _market_active(market)]
    if not markets:
        return None
    event_title = str(event.get("title") or "").strip() or "Untitled macro market"
    outcomes: List[Dict[str, Any]] = []
    for market in markets:
        yes_price, no_price = _market_prices(market)
        label = _label_for_market(event_title, market)
        volume_24h = _float_value(market.get("volume24hr") or market.get("volume_24hr") or market.get("volume24h"))
        outcomes.append(
            {
                "outcomeKey": _normalize_outcome_key(label, market.get("id")),
                "gammaMarketId": market.get("id"),
                "label": label,
                "title": market.get("question") or market.get("title") or event_title,
                "yesPrice": yes_price,
                "noPrice": no_price,
                "volume24h": volume_24h,
                "conditionId": market.get("conditionId") or market.get("condition_id"),
                "slug": market.get("slug") or market.get("market_slug"),
            }
        )
    top_outcomes = sorted(
        [outcome for outcome in outcomes if outcome.get("yesPrice") is not None],
        key=lambda outcome: float(outcome.get("yesPrice") or 0),
        reverse=True,
    )[:3]
    volume_24h = _float_value(event.get("volume24hr") or event.get("volume_24hr") or event.get("volume24h"))
    if volume_24h is None:
        volume_24h = sum(float(outcome.get("volume24h") or 0) for outcome in outcomes) or None
    return {
        "eventId": event.get("id"),
        "slug": event.get("slug"),
        "title": event_title,
        "categoryIds": [category["id"] for category in categories],
        "categoryLabels": [category["label"] for category in categories],
        "marketTypes": [category["marketType"] for category in categories],
        "endDate": event.get("endDate") or event.get("end_date"),
        "createdAt": event.get("startDate") or event.get("createdAt") or event.get("created_at"),
        "volume24h": volume_24h,
        "liquidity": _float_value(event.get("liquidity") or event.get("liquidityNum")),
        "outcomeCount": len(outcomes),
        "topOutcomes": top_outcomes,
    }


def _score_item(item: Dict[str, Any], now_ts: float) -> tuple[float, float, float]:
    volume = _float_value(item.get("volume24h")) or 0.0
    liquidity = _float_value(item.get("liquidity")) or 0.0
    end_ts = _parse_timestamp(item.get("endDate"))
    if end_ts and now_ts:
        days_to_event = max(0.0, (end_ts - now_ts) / 86400)
        catalyst_score = max(0.0, 30.0 - min(days_to_event, 30.0))
    else:
        catalyst_score = 0.0
    category_weight = 8.0 if "cpi" in (item.get("categoryIds") or []) else 0.0
    return (category_weight + catalyst_score, volume, liquidity)


def _category_breakdown(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for category in MACRO_CATEGORIES:
        matched = [item for item in items if category["id"] in (item.get("categoryIds") or [])]
        top = max(matched, key=lambda item: _float_value(item.get("volume24h")) or 0.0) if matched else None
        rows.append(
            {
                "id": category["id"],
                "label": category["label"],
                "marketType": category["marketType"],
                "activeCount": len(matched),
                "topTitle": top.get("title") if top else None,
                "volume24h": sum(float(_float_value(item.get("volume24h")) or 0.0) for item in matched),
            }
        )
    return rows


def _top_catalyst(items: List[Dict[str, Any]], now_ts: float) -> Optional[Dict[str, Any]]:
    upcoming = [
        item
        for item in items
        if _parse_timestamp(item.get("endDate")) > now_ts > 0
    ]
    if not upcoming:
        return None
    item = min(upcoming, key=lambda candidate: _parse_timestamp(candidate.get("endDate")))
    return {
        "title": item.get("title"),
        "eventId": item.get("eventId"),
        "slug": item.get("slug"),
        "endDate": item.get("endDate"),
        "categoryLabels": item.get("categoryLabels") or [],
    }


def _summary(items: List[Dict[str, Any]], categories: List[Dict[str, Any]], now_ts: float) -> Dict[str, Any]:
    top_category = max(categories, key=lambda row: int(row.get("activeCount") or 0)) if categories else None
    top_label = str((top_category or {}).get("label") or "Macro")
    active_count = len(items)
    if active_count <= 0:
        signal = "NO MACRO CLUSTER"
    elif int((top_category or {}).get("activeCount") or 0) >= 4:
        signal = f"{top_label.upper()} CLUSTER ACTIVE"
    else:
        signal = "MACRO WATCHLIST LIVE"
    return {
        "activeCount": active_count,
        "categoryCount": sum(1 for row in categories if int(row.get("activeCount") or 0) > 0),
        "topCategory": top_label,
        "signal": signal,
        "topCatalyst": _top_catalyst(items, now_ts),
    }


def _empty_payload(ctx: PolymarketMacroMapContext, *, status: str = "degraded", source_state: str = "warming") -> Dict[str, Any]:
    return {
        "generatedAt": _dependencies(ctx).utc_now_iso(),
        "source": "Polymarket Gamma API",
        "sourceUrl": getattr(_dependencies(ctx).settings, "polymarket_macro_map_source_url", "") or _dependencies(ctx).settings.gamma_api_base,
        "status": status,
        "sources": {"gammaEvents": source_state},
        "summary": {
            "activeCount": 0,
            "categoryCount": 0,
            "topCategory": "Macro",
            "signal": "NO MACRO CLUSTER",
            "topCatalyst": None,
        },
        "categories": _category_breakdown([]),
        "items": [],
    }


def normalize_polymarket_macro_map_payload(payload: Any, *, ctx: PolymarketMacroMapContext, limit: int = DEFAULT_ITEM_LIMIT) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_payload(ctx, status="invalid", source_state="invalid")
    result = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    items = [item for item in (result.get("items") or []) if isinstance(item, dict)]
    result["items"] = items[: max(1, min(int(limit or DEFAULT_ITEM_LIMIT), DEFAULT_ITEM_LIMIT))]
    result["categories"] = [row for row in (result.get("categories") or []) if isinstance(row, dict)]
    result["summary"] = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    result["generatedAt"] = str(result.get("generatedAt") or _dependencies(ctx).utc_now_iso())
    result["source"] = str(result.get("source") or "Polymarket Gamma API")
    result["sourceUrl"] = str(result.get("sourceUrl") or getattr(_dependencies(ctx).settings, "polymarket_macro_map_source_url", "") or _dependencies(ctx).settings.gamma_api_base)
    result["status"] = str(result.get("status") or ("ok" if result["items"] else "empty"))
    return result


def _with_cache_mode(payload: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    return {**payload, "cacheMode": str(cache_mode)}


def _read_seeded_snapshot(ctx: PolymarketMacroMapContext, *, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    reader = _dependencies(ctx).get_cached_json
    if callable(reader):
        redis_payload = reader(MACRO_MAP_SNAPSHOT_NAMESPACE, MACRO_MAP_CACHE_KEY)
        if isinstance(redis_payload, dict):
            snapshot_store = _dependencies(ctx).snapshot_store
            if snapshot_store is not None:
                snapshot_store.set(MACRO_MAP_SNAPSHOT_NAMESPACE, MACRO_MAP_CACHE_KEY, redis_payload, ttl_seconds)
            return _with_cache_mode(redis_payload, "redis-seed")

    snapshot_store = _dependencies(ctx).snapshot_store
    if snapshot_store is None:
        return None

    sqlite_payload = snapshot_store.get(MACRO_MAP_SNAPSHOT_NAMESPACE, MACRO_MAP_CACHE_KEY)
    if isinstance(sqlite_payload, dict):
        setter = _dependencies(ctx).set_cached_json
        if callable(setter):
            setter(MACRO_MAP_SNAPSHOT_NAMESPACE, MACRO_MAP_CACHE_KEY, sqlite_payload, ttl_seconds)
        return _with_cache_mode(sqlite_payload, "sqlite-seed")

    stale_payload = snapshot_store.get_stale(MACRO_MAP_SNAPSHOT_NAMESPACE, MACRO_MAP_CACHE_KEY)
    if isinstance(stale_payload, dict):
        setter = _dependencies(ctx).set_cached_json
        if callable(setter):
            setter(MACRO_MAP_SNAPSHOT_NAMESPACE, MACRO_MAP_CACHE_KEY, stale_payload, min(15, ttl_seconds))
        return _with_cache_mode(stale_payload, "stale-seed")
    return None


def _store_live_build_snapshot(ctx: PolymarketMacroMapContext, payload: Dict[str, Any], *, ttl_seconds: int) -> None:
    snapshot_store = _dependencies(ctx).snapshot_store
    if snapshot_store is not None:
        snapshot_store.set(MACRO_MAP_SNAPSHOT_NAMESPACE, MACRO_MAP_CACHE_KEY, payload, ttl_seconds)
    setter = _dependencies(ctx).set_cached_json
    if callable(setter):
        setter(MACRO_MAP_SNAPSHOT_NAMESPACE, MACRO_MAP_CACHE_KEY, payload, ttl_seconds)


def build_polymarket_macro_map_payload(ctx: PolymarketMacroMapContext) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    errors: Dict[str, str] = {}
    event_batches: List[List[Dict[str, Any]]] = []
    for order in ("volume24hr", "startDate"):
        try:
            event_batches.append(_fetch_gamma_events(dependencies, order=order))
        except Exception as exc:
            errors[order] = type(exc).__name__
            dependencies.application.logger.exception(
                "polymarket macro map gamma fetch failed order=%s",
                order,
            )
    events = _merge_unique_events(event_batches)
    if not events:
        return _empty_payload(
            dependencies,
            status="degraded" if errors else "empty",
            source_state="error" if errors else "empty",
        )

    now_iso = dependencies.utc_now_iso()
    now_ts = _parse_timestamp(now_iso)
    terms = _search_terms(dependencies)
    items: List[Dict[str, Any]] = []
    for event in events:
        if _is_event_ended(event, now_iso) or not _event_matches_terms(event, terms):
            continue
        categories = _classify_event(event)
        if not categories:
            continue
        item = _normalize_event(event, categories)
        if item is not None:
            items.append(item)
    items.sort(key=lambda item: _score_item(item, now_ts), reverse=True)
    categories = _category_breakdown(items)
    status = "ok" if items else "empty"
    if errors and items:
        status = "degraded"
    return {
        "generatedAt": now_iso,
        "source": "Polymarket Gamma API",
        "sourceUrl": getattr(
            dependencies.settings,
            "polymarket_macro_map_source_url",
            "",
        )
        or dependencies.settings.gamma_api_base,
        "status": status,
        "sources": {"gammaEvents": "partial" if errors and items else ("ok" if items else "empty")},
        "summary": _summary(items, categories, now_ts),
        "categories": categories,
        "items": items,
    }


def get_polymarket_macro_map_snapshot(ctx: PolymarketMacroMapContext, limit: int = DEFAULT_ITEM_LIMIT) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    ttl_seconds = max(
        60,
        int(
            getattr(
                dependencies.settings,
                "polymarket_macro_map_ttl_seconds",
                180,
            )
            or 180
        ),
    )
    seeded_payload = _read_seeded_snapshot(
        dependencies,
        ttl_seconds=ttl_seconds,
    )
    if seeded_payload is not None:
        return normalize_polymarket_macro_map_payload(
            seeded_payload,
            ctx=dependencies,
            limit=limit,
        )

    payload = _with_cache_mode(
        build_polymarket_macro_map_payload(dependencies),
        "live-build",
    )
    if payload.get("items"):
        _store_live_build_snapshot(
            dependencies,
            payload,
            ttl_seconds=ttl_seconds,
        )
    return normalize_polymarket_macro_map_payload(
        payload,
        ctx=dependencies,
        limit=limit,
    )
