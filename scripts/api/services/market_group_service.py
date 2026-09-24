from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, cast

from api.context import (
    resolve_optional_service_callable,
    resolve_service_callable,
    resolve_service_value,
)
from api.services import clickhouse_orderfilled_service

MARKET_GROUPS_LIST_NAMESPACE = "snapshot:market-groups:list"
MARKET_GROUPS_DETAIL_NAMESPACE = "snapshot:market-groups:detail"
MARKET_GROUPS_CHART_NAMESPACE = "snapshot:market-groups:chart"
MARKET_GROUPS_LIST_TTL_SECONDS = 120
MARKET_GROUPS_DETAIL_TTL_SECONDS = 180
TERMINAL_PROBABILITY_LOW = 0.03
TERMINAL_PROBABILITY_HIGH = 0.97
ACTIVE_DEFAULT_MIN_VOLUME_24H = 25.0
ACTIVE_DEFAULT_MIN_TRADES_24H = 1.0
ACTIVE_GROUP_LOOKAHEAD_MULTIPLIER = 5
ACTIVE_GROUP_STALE_SECONDS = 24 * 60 * 60

CHART_RANGE_INTERVALS: Dict[str, str] = {
    "1h": "5m",
    "6h": "5m",
    "1d": "15m",
    "1w": "1h",
    "1m": "6h",
    "all": "1d",
}

CHART_RANGE_TTLS: Dict[str, int] = {
    "1h": 180,
    "6h": 240,
    "1d": 600,
    "1w": 900,
    "1m": 1200,
    "all": 1800,
}

SERIES_PALETTE = [
    "#ff5b57",
    "#d8b04b",
    "#7cb6ff",
    "#4469d8",
    "#42c37b",
    "#c48bff",
]


NOISY_MARKET_TERMS = (
    "hide-from-new",
    "recurring",
    "onchain-registry",
    "updown-5m",
    "updown-15m",
)

GENERIC_TAGS = {
    "all",
    "featured",
    "hide-from-new",
    "recurring",
    "onchain-registry",
    "up-or-down",
    "crypto-prices",
    "5m",
    "15m",
}


def _service_callable(
    context: Mapping[str, Any],
    name: str,
) -> Callable[..., Any]:
    return cast(Callable[..., Any], resolve_service_callable(context, name))


@dataclass(frozen=True)
class MarketGroupDependencies:
    source: Mapping[str, Any]
    application: Any
    settings: Any
    query_all: Callable[..., Any]
    get_cached_runtime_payload: Callable[..., Any]
    set_cached_runtime_payload: Callable[..., Any]
    http_json_get: Callable[..., Any]
    utc_now_iso: Callable[..., Any]
    get_snapshot_payload: Callable[..., Any]
    get_market_clob_price_series: Callable[..., Any]
    get_backend: Callable[..., Any] | None
    table_exists: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MarketGroupDependencies:
        return cls(
            source=context,
            application=resolve_service_value(context, "app"),
            settings=resolve_service_value(context, "SETTINGS"),
            query_all=_service_callable(context, "query_all"),
            get_cached_runtime_payload=_service_callable(
                context,
                "get_cached_runtime_payload",
            ),
            set_cached_runtime_payload=_service_callable(
                context,
                "set_cached_runtime_payload",
            ),
            http_json_get=_service_callable(context, "http_json_get"),
            utc_now_iso=_service_callable(context, "utc_now_iso"),
            get_snapshot_payload=_service_callable(
                context,
                "get_snapshot_payload",
            ),
            get_market_clob_price_series=_service_callable(
                context,
                "get_market_clob_price_series",
            ),
            get_backend=resolve_optional_service_callable(
                context,
                "get_backend",
            ),
            table_exists=resolve_optional_service_callable(
                context,
                "table_exists",
            ),
        )


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


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None and value != ""), None)


def _positive_count_or_none(value: Any) -> Optional[int]:
    numeric = _float_value(value)
    if numeric is None or numeric <= 0:
        return None
    return int(numeric)


def _is_terminal_probability(value: Any) -> bool:
    numeric = _float_value(value)
    if numeric is None:
        return False
    return numeric <= TERMINAL_PROBABILITY_LOW or numeric >= TERMINAL_PROBABILITY_HIGH


def _outcome_probability(outcome: Dict[str, Any]) -> Optional[float]:
    price = _float_value((outcome or {}).get("blockCloseYesPrice"))
    if price is None:
        price = _float_value((outcome or {}).get("yesPrice"))
    return price


def _outcome_is_terminal(outcome: Dict[str, Any]) -> bool:
    price = _outcome_probability(outcome)
    return price is not None and _is_terminal_probability(price)


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, value))


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
        if label not in (None, ""):
            tags.append(str(label))
    return tags


def _slugify_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _normalized_event_tags(event: Dict[str, Any]) -> List[str]:
    normalized: List[str] = []
    for tag in _event_tags(event):
        slug = _slugify_token(tag)
        if slug:
            normalized.append(slug)
    return normalized


def _normalized_text(*parts: Any) -> str:
    return " ".join(str(part or "").lower() for part in parts)


def _is_noisy_event(event: Dict[str, Any]) -> bool:
    text = _normalized_text(event.get("title"), event.get("slug"), *_normalized_event_tags(event))
    if " up or down - " in text:
        return True
    return any(term in text for term in NOISY_MARKET_TERMS)


def _is_event_ended(event: Dict[str, Any], now_iso: str) -> bool:
    if event.get("closed") is True or event.get("active") is False:
        return True
    end_date = event.get("endDate") or event.get("end_date")
    if not end_date:
        return False
    return _parse_timestamp(end_date) > 0 and _parse_timestamp(end_date) < _parse_timestamp(now_iso)


def _market_active(market: Dict[str, Any]) -> bool:
    if market.get("closed") is True:
        return False
    if market.get("active") is False:
        return False
    if market.get("acceptingOrders") is False:
        return False
    return True


def _market_prices(market: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
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


def _terminal_event(active_markets: List[Dict[str, Any]]) -> bool:
    prices = [
        yes
        for yes, _no in (_market_prices(market) for market in active_markets)
        if yes is not None
    ]
    if not prices:
        return False
    return all(price <= TERMINAL_PROBABILITY_LOW or price >= TERMINAL_PROBABILITY_HIGH for price in prices)


def _category_for_event(event: Dict[str, Any]) -> str:
    tags = _normalized_event_tags(event)
    raw_category = str(event.get("category") or event.get("categorySlug") or event.get("category_slug") or "").strip()
    text = _normalized_text(event.get("title"), event.get("slug"), raw_category, *tags)
    if any(term in text for term in ("bitcoin", "ethereum", "solana", "xrp", "dogecoin", "crypto", "btc", "eth")):
        return "crypto"
    if any(term in text for term in ("election", "president", "senate", "congress", "politic")):
        return "politics"
    if any(term in text for term in ("nba", "nfl", "mlb", "nhl", "soccer", "tennis", "sports")):
        return "sports"
    if any(term in text for term in ("fed", "inflation", "rate", "economy", "finance", "macro")):
        return "macro"
    if any(term in text for term in ("ai", "openai", "tech")):
        return "tech"
    for tag in tags:
        if tag and tag not in GENERIC_TAGS:
            return tag
    return raw_category.lower() or "market"


def _label_for_market(event_title: str, market: Dict[str, Any]) -> str:
    label = str(market.get("groupItemTitle") or market.get("group_item_title") or "").strip()
    if label:
        return label
    question = str(market.get("question") or market.get("title") or "").strip()
    if event_title and question.startswith(event_title):
        candidate = question[len(event_title):].strip(" -:·")
        if candidate:
            return candidate
    match = re.match(r"Will\s+(.+?)\s+win\b", question, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return question or str(market.get("slug") or market.get("id") or "Outcome")


def _market_identity(market: Dict[str, Any]) -> Tuple[str, str, str]:
    condition_id = str(market.get("conditionId") or market.get("condition_id") or "").strip().lower()
    slug = str(market.get("slug") or market.get("market_slug") or "").strip().lower()
    token_ids = _market_token_ids(market)
    yes_token_id = token_ids[0] if token_ids else ""
    return condition_id, slug, yes_token_id


def _market_token_ids(market: Dict[str, Any]) -> List[str]:
    return [str(item).strip() for item in _as_list(market.get("clobTokenIds") or market.get("clob_token_ids")) if str(item).strip()]


def _chunk(values: Iterable[str], size: int = 450) -> Iterable[List[str]]:
    batch: List[str] = []
    for value in values:
        if not value:
            continue
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _query_market_rows(
    dependencies: MarketGroupDependencies,
    column_expr: str,
    values: Iterable[str],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    unique_values = sorted({str(value).strip().lower() for value in values if str(value).strip()})
    for batch in _chunk(unique_values):
        placeholders = ",".join("?" for _ in batch)
        rows.extend(
            dependencies.query_all(
                f"""
                SELECT
                    m.id,
                    m.slug,
                    m.title,
                    LOWER(COALESCE(m.condition_id, '')) AS condition_key,
                    LOWER(COALESCE(m.slug, '')) AS slug_key,
                    COALESCE(m.yes_token_id, '') AS yes_token_id,
                    m.gamma_market_id,
                    COALESCE(mlp.latest_yes_price, mls.latest_price) AS latest_yes_price,
                    mlp.latest_no_price,
                    mls.volume_24h,
                    mls.trade_count_24h,
                    COALESCE(mls.last_trade_at, mls.latest_trade_at) AS last_trade_at,
                    mls.price_24h_ago
                FROM markets m
                LEFT JOIN market_latest_prices mlp ON mlp.market_id = m.id
                LEFT JOIN market_list_serving mls ON mls.market_id = m.id
                WHERE {column_expr} IN ({placeholders})
                """,
                batch,
            )
        )
    return rows


def _local_market_lookup(
    dependencies: MarketGroupDependencies,
    events: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    condition_ids: set[str] = set()
    gamma_market_ids: set[str] = set()
    slugs: set[str] = set()
    yes_token_ids: set[str] = set()
    for event in events:
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            condition_id, slug, yes_token_id = _market_identity(market)
            gamma_market_id = str(market.get("id") or market.get("gamma_market_id") or "").strip()
            if condition_id:
                condition_ids.add(condition_id)
            if gamma_market_id:
                gamma_market_ids.add(gamma_market_id)
            if slug:
                slugs.add(slug)
            if yes_token_id:
                yes_token_ids.add(yes_token_id)

    by_condition: Dict[str, Dict[str, Any]] = {}
    by_gamma: Dict[str, Dict[str, Any]] = {}
    by_slug: Dict[str, Dict[str, Any]] = {}
    by_yes_token: Dict[str, Dict[str, Any]] = {}
    try:
        for row in _query_market_rows(
            dependencies,
            "m.condition_id",
            condition_ids,
        ):
            by_condition.setdefault(str(row.get("condition_key") or ""), row)
        for row in _query_market_rows(
            dependencies,
            "m.gamma_market_id",
            gamma_market_ids,
        ):
            by_gamma.setdefault(str(row.get("gamma_market_id") or ""), row)
        for row in _query_market_rows(
            dependencies,
            "m.slug",
            slugs,
        ):
            by_slug.setdefault(str(row.get("slug_key") or ""), row)
        for row in _query_market_rows(
            dependencies,
            "m.yes_token_id",
            yes_token_ids,
        ):
            by_yes_token.setdefault(str(row.get("yes_token_id") or ""), row)
    except Exception:
        logger = getattr(dependencies.application, "logger", None)
        if logger:
            logger.exception("market-group local lookup failed; falling back to Gamma-only groups")
    return by_condition, by_gamma, by_slug, by_yes_token


def _fetch_gamma_events(
    dependencies: MarketGroupDependencies,
    *,
    max_pages: int = 10,
    target_events: int = 240,
    order: str = "volume24hr",
    ttl_seconds: int = 45,
) -> List[Dict[str, Any]]:
    cache_key = json.dumps(
        {
            "kind": "market-groups-gamma-events",
            "maxPages": int(max_pages),
            "order": str(order or "volume24hr"),
            "targetEvents": int(target_events),
            "v": 2,
        },
        sort_keys=True,
    )
    cached = dependencies.get_cached_runtime_payload(
        "market-groups",
        cache_key,
    )
    if isinstance(cached, list):
        return cached

    events: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    limit = 100
    for page in range(max(1, int(max_pages))):
        payload = dependencies.http_json_get(
            f"{dependencies.settings.gamma_api_base.rstrip('/')}/events",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": page * limit,
                "order": order,
                "ascending": "false",
            },
            timeout=12,
            headers={"Accept": "application/json", "User-Agent": "polydata-runtime/1.0"},
        )
        page_events = payload if isinstance(payload, list) else ((payload or {}).get("events") or (payload or {}).get("data") or [])
        if not isinstance(page_events, list) or not page_events:
            break
        for event in page_events:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or event.get("slug") or "").strip()
            if not event_id or event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            events.append(event)
            if len(events) >= target_events:
                break
        if len(events) >= target_events:
            break
        if len(page_events) < limit:
            break
    return dependencies.set_cached_runtime_payload(
        "market-groups",
        cache_key,
        events,
        ttl_seconds=ttl_seconds,
    )


def _extract_events(payload: Any) -> List[Dict[str, Any]]:
    events = payload if isinstance(payload, list) else ((payload or {}).get("events") or (payload or {}).get("data") or [])
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def _fetch_gamma_event_by_id(
    dependencies: MarketGroupDependencies,
    event_id: str,
) -> Optional[Dict[str, Any]]:
    identifier = str(event_id or "").strip()
    if not identifier:
        return None
    for params in ({"id": identifier}, {"slug": identifier}):
        try:
            payload = dependencies.http_json_get(
                f"{dependencies.settings.gamma_api_base.rstrip('/')}/events",
                params=params,
                timeout=12,
                headers={"Accept": "application/json", "User-Agent": "polydata-runtime/1.0"},
            )
        except Exception:
            continue
        for event in _extract_events(payload):
            if str(event.get("id") or "").strip() == identifier or str(event.get("slug") or "").strip() == identifier:
                return event
        if len(_extract_events(payload)) == 1:
            return _extract_events(payload)[0]

    for event in _fetch_gamma_events(
        dependencies,
        target_events=300,
    ):
        if str(event.get("id") or "").strip() == identifier or str(event.get("slug") or "").strip() == identifier:
            return event
    return None


def _normalize_group(
    dependencies: MarketGroupDependencies,
    event: Dict[str, Any],
    lookups: Tuple[
        Dict[str, Dict[str, Any]],
        Dict[str, Dict[str, Any]],
        Dict[str, Dict[str, Any]],
        Dict[str, Dict[str, Any]],
    ],
) -> Optional[Dict[str, Any]]:
    now_iso = dependencies.utc_now_iso()
    if _is_event_ended(event, now_iso) or _is_noisy_event(event):
        return None
    markets = [market for market in (event.get("markets") or []) if isinstance(market, dict) and _market_active(market)]
    if not markets or _terminal_event(markets):
        return None

    by_condition, by_gamma, by_slug, by_yes_token = lookups
    event_title = str(event.get("title") or "").strip() or "Untitled event"
    outcomes: List[Dict[str, Any]] = []
    for market in markets:
        condition_id, slug, yes_token_id = _market_identity(market)
        gamma_market_id = str(market.get("id") or market.get("gamma_market_id") or "").strip()
        token_ids = _market_token_ids(market)
        no_token_id = token_ids[1] if len(token_ids) > 1 else ""
        local = by_condition.get(condition_id) or by_gamma.get(gamma_market_id) or by_slug.get(slug) or by_yes_token.get(yes_token_id) or {}
        gamma_yes, gamma_no = _market_prices(market)
        local_yes = _float_value(local.get("latest_yes_price"))
        yes_price = gamma_yes if gamma_yes is not None else local_yes
        no_price = _float_value(local.get("latest_no_price"))
        if no_price is None:
            no_price = gamma_no
        change_24h = _float_value(market.get("oneDayPriceChange") or market.get("one_day_price_change"))
        if change_24h is None and yes_price is not None:
            price_24h_ago = _float_value(local.get("price_24h_ago"))
            if price_24h_ago is not None:
                change_24h = yes_price - price_24h_ago
        gamma_volume_24h = _float_value(
            _first_present(market.get("volume24hr"), market.get("volume_24hr"), market.get("volume24h"))
        )
        volume_24h = gamma_volume_24h
        if volume_24h is None:
            volume_24h = _float_value(local.get("volume_24h"))
        gamma_activity_at = market.get("updatedAt") or market.get("updated_at") or event.get("updatedAt") or event.get("updated_at")
        local_activity_at = local.get("last_trade_at")
        last_trade_at = local_activity_at
        if gamma_volume_24h is not None and gamma_volume_24h > 0 and _parse_timestamp(gamma_activity_at) > _parse_timestamp(local_activity_at):
            last_trade_at = gamma_activity_at
        label = _label_for_market(event_title, market)
        outcomes.append(
            {
                "outcomeKey": _normalize_outcome_key(label, market.get("id")),
                "marketId": local.get("id"),
                "localMarketId": local.get("id"),
                "gammaMarketId": market.get("id"),
                "label": label,
                "title": market.get("question") or market.get("title") or local.get("title") or event_title,
                "yesPrice": yes_price,
                "noPrice": no_price,
                "change24h": change_24h,
                "volume24h": volume_24h,
                "tradeCount24h": _positive_count_or_none(local.get("trade_count_24h")),
                "volumeScope": "market",
                "volumeSource": "gamma-market" if gamma_volume_24h is not None else "postgres-serving",
                "lastTradeAt": last_trade_at,
                "conditionId": condition_id or None,
                "slug": slug or market.get("slug") or local.get("slug"),
                "yesTokenId": yes_token_id or None,
                "noTokenId": no_token_id or None,
            }
        )

    top_outcomes = sorted(
        [outcome for outcome in outcomes if outcome.get("yesPrice") is not None],
        key=lambda outcome: float(outcome.get("yesPrice") or 0),
        reverse=True,
    )
    last_activity_at = None
    last_activity_ts = 0.0
    for outcome in outcomes:
        candidate = outcome.get("lastTradeAt")
        candidate_ts = _parse_timestamp(candidate)
        if candidate_ts > last_activity_ts:
            last_activity_ts = candidate_ts
            last_activity_at = candidate
    def _default_outcome_score(outcome: Dict[str, Any]) -> Tuple[float, float, float]:
        price = _float_value(outcome.get("yesPrice"))
        volume = _float_value(outcome.get("volume24h")) or 0.0
        trades = _float_value(outcome.get("tradeCount24h")) or 0.0
        label = str(outcome.get("label") or "").lower()
        score = 0.0
        if outcome.get("marketId") is not None:
            score += 12.0
        if outcome.get("yesTokenId"):
            score += 8.0
        if volume > 0:
            score += min(40.0, volume ** 0.25)
        if trades > 0:
            score += min(20.0, trades)
        if price is not None:
            if 0.02 < price < 0.98:
                score += 40.0
            if price <= TERMINAL_PROBABILITY_LOW or price >= TERMINAL_PROBABILITY_HIGH:
                score -= 60.0
        if "completed match" in label or label.strip() in {"completed", "match completed"}:
            score -= 30.0
        return (score, volume, trades)

    default_candidates = [outcome for outcome in outcomes if outcome.get("marketId") is not None or outcome.get("yesTokenId")]
    live_default_candidates = [outcome for outcome in default_candidates if not _outcome_is_terminal(outcome)]
    if live_default_candidates:
        default_candidates = live_default_candidates
    default_outcome = max(default_candidates, key=_default_outcome_score, default=None)
    if default_outcome is None and top_outcomes:
        default_outcome = top_outcomes[0]
    if default_outcome is None and outcomes:
        default_outcome = outcomes[0]
    gamma_event_volume_24h = _float_value(
        _first_present(event.get("volume24hr"), event.get("volume_24hr"), event.get("volume24h"))
    )
    event_volume_24h = gamma_event_volume_24h
    if event_volume_24h is None:
        outcome_volumes = [_float_value(outcome.get("volume24h")) for outcome in outcomes]
        summed_volume = sum(value for value in outcome_volumes if value is not None)
        event_volume_24h = summed_volume if summed_volume > 0 else None
    event_trade_count_24h = sum(
        int(float(value))
        for value in (_float_value(outcome.get("tradeCount24h")) for outcome in outcomes)
        if value is not None and value > 0
    ) or None

    return {
        "groupId": f"event:{event.get('id') or event.get('slug')}",
        "eventId": event.get("id"),
        "title": event_title,
        "slug": event.get("slug"),
        "category": _category_for_event(event),
        "tags": _event_tags(event),
        "createdAt": event.get("startDate") or event.get("createdAt") or event.get("created_at"),
        "endDate": event.get("endDate") or event.get("end_date"),
        "volume24h": event_volume_24h,
        "tradeCount24h": event_trade_count_24h,
        "volumeScope": "event",
        "volumeSource": "gamma-event" if gamma_event_volume_24h is not None else "derived-markets",
        "outcomeCount": len(outcomes),
        "lastActivityAt": last_activity_at,
        "defaultOutcomeKey": default_outcome.get("outcomeKey") if default_outcome else None,
        "defaultMarketId": default_outcome.get("marketId") if default_outcome else None,
        "outcomes": outcomes,
        "topOutcomes": [
            {
                "outcomeKey": outcome.get("outcomeKey"),
                "label": outcome.get("label"),
                "yesPrice": outcome.get("yesPrice"),
                "noPrice": outcome.get("noPrice"),
                "change24h": outcome.get("change24h"),
                "volume24h": outcome.get("volume24h"),
                "tradeCount24h": outcome.get("tradeCount24h"),
                "volumeScope": outcome.get("volumeScope") or "market",
                "volumeSource": outcome.get("volumeSource"),
                "marketId": outcome.get("marketId"),
                "localMarketId": outcome.get("localMarketId"),
                "gammaMarketId": outcome.get("gammaMarketId"),
                "conditionId": outcome.get("conditionId"),
                "slug": outcome.get("slug"),
                "yesTokenId": outcome.get("yesTokenId"),
                "noTokenId": outcome.get("noTokenId"),
            }
            for outcome in top_outcomes[:5]
        ],
    }


def _matches_query(group: Dict[str, Any], query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        [
            str(group.get("title") or ""),
            str(group.get("slug") or ""),
            str(group.get("category") or ""),
            " ".join(str(tag) for tag in group.get("tags") or []),
            " ".join(str(outcome.get("label") or "") for outcome in group.get("outcomes") or []),
        ]
    ).lower()
    return query.lower() in haystack


def _event_identity(event: Dict[str, Any]) -> str:
    return str(event.get("id") or event.get("slug") or "").strip()


def _merge_unique_events(*event_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for events in event_lists:
        for event in events:
            identity = _event_identity(event)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            merged.append(event)
    return merged


def _group_created_ts(group: Dict[str, Any]) -> float:
    return _parse_timestamp(group.get("createdAt"))


def _group_last_activity_ts(group: Dict[str, Any]) -> float:
    return _parse_timestamp(group.get("lastActivityAt")) or _group_created_ts(group)


def _group_has_ready_signal(group: Dict[str, Any]) -> bool:
    if (_float_value(group.get("volume24h")) or 0.0) > 0:
        return True
    if (_float_value(group.get("tradeCount24h")) or 0.0) > 0:
        return True
    return any(
        (_float_value((outcome or {}).get("volume24h")) or 0.0) > 0
        or (_float_value((outcome or {}).get("tradeCount24h")) or 0.0) > 0
        for outcome in (group.get("outcomes") or [])
    )


def _group_has_live_price_signal(group: Dict[str, Any]) -> bool:
    for outcome in group.get("outcomes") or []:
        price = _float_value((outcome or {}).get("yesPrice"))
        if price is None:
            continue
        if TERMINAL_PROBABILITY_LOW < price < TERMINAL_PROBABILITY_HIGH and (outcome or {}).get("yesTokenId"):
            return True
    return False


def _outcome_activity(outcome: Dict[str, Any]) -> Tuple[float, float]:
    volume = _float_value((outcome or {}).get("volume24h")) or 0.0
    trades = _float_value((outcome or {}).get("tradeCount24h")) or 0.0
    return volume, trades


def _outcome_has_activity(outcome: Dict[str, Any]) -> bool:
    volume, trades = _outcome_activity(outcome)
    return trades >= ACTIVE_DEFAULT_MIN_TRADES_24H or volume >= ACTIVE_DEFAULT_MIN_VOLUME_24H


def _outcome_is_focusable(outcome: Dict[str, Any], *, require_activity: bool) -> bool:
    if not isinstance(outcome, dict):
        return False
    if outcome.get("marketId") is None and not outcome.get("yesTokenId"):
        return False
    if _outcome_is_terminal(outcome):
        return False
    if require_activity and not _outcome_has_activity(outcome):
        return False
    price = _outcome_probability(outcome)
    if price is not None and abs(price - 0.5) < 0.0001 and not _outcome_has_activity(outcome):
        return False
    return True


def _group_default_outcome(group: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    default_key = str(group.get("defaultOutcomeKey") or "").strip()
    default_market_id = _float_value(group.get("defaultMarketId"))
    for outcome in _group_outcome_candidates(group):
        if default_key and str(outcome.get("outcomeKey") or "").strip() == default_key:
            return outcome
        market_id = _outcome_market_id(outcome)
        if default_market_id is not None and market_id == int(default_market_id):
            return outcome
    return None


def _group_has_focusable_default(group: Dict[str, Any], *, require_activity: bool = True) -> bool:
    selected = _group_default_outcome(group)
    if selected is None or not _outcome_is_focusable(selected, require_activity=False):
        return False
    if not require_activity or _outcome_has_activity(selected):
        return True
    outcome_volume = 0.0
    outcome_trades = 0.0
    has_outcome_activity_fields = False
    for outcome in _group_outcome_candidates(group):
        volume, trades = _outcome_activity(outcome)
        if volume > 0 or trades > 0:
            has_outcome_activity_fields = True
        if _outcome_is_terminal(outcome):
            continue
        outcome_volume += volume
        outcome_trades += trades
    if outcome_trades >= ACTIVE_DEFAULT_MIN_TRADES_24H or outcome_volume >= ACTIVE_DEFAULT_MIN_VOLUME_24H:
        return True
    if has_outcome_activity_fields:
        return False
    group_volume = _float_value(group.get("volume24h")) or 0.0
    group_trades = _float_value(group.get("tradeCount24h")) or 0.0
    return group_trades >= ACTIVE_DEFAULT_MIN_TRADES_24H or group_volume >= ACTIVE_DEFAULT_MIN_VOLUME_24H


def _active_group_sort_key(group: Dict[str, Any], *, now_ts: float) -> Tuple[int, int, float, float, float, float]:
    created_ts = _group_created_ts(group)
    raw_last_activity_ts = _parse_timestamp(group.get("lastActivityAt"))
    last_activity_ts = raw_last_activity_ts or created_ts
    volume = _float_value(group.get("volume24h")) or 0.0
    trade_count = _float_value(group.get("tradeCount24h")) or 0.0
    price = _float_value(group.get("latestBlockClosePrice"))
    ready_signal = _group_has_ready_signal(group)
    live_price_signal = _group_has_live_price_signal(group)
    multi_penalty = 0 if int(group.get("outcomeCount") or 0) > 2 else 1
    recent_threshold = now_ts - ACTIVE_GROUP_STALE_SECONDS
    fresh_threshold = now_ts - (12 * 3600)
    if volume > 0 and last_activity_ts >= recent_threshold:
        bucket = 0
        recency = last_activity_ts
    elif volume > 0:
        bucket = 1
        recency = last_activity_ts
    elif trade_count > 0 and ready_signal:
        bucket = 2
        recency = last_activity_ts
    elif live_price_signal and created_ts >= fresh_threshold:
        bucket = 3
        recency = created_ts
    else:
        bucket = 4
        recency = max(created_ts, last_activity_ts)
    if price is not None and abs(price - 0.5) < 0.0001 and trade_count <= 0 and volume < 25:
        bucket += 2
    return (bucket, multi_penalty, -volume, -trade_count, -recency, -created_ts)


def _group_category_bucket(group: Dict[str, Any]) -> str:
    category = str(group.get("category") or "").strip().lower()
    tags = " ".join(str(tag or "").strip().lower() for tag in _as_list(group.get("tags")))
    title = str(group.get("title") or "").strip().lower()
    slug = str(group.get("slug") or "").strip().lower()
    text = " ".join((category, tags, title, slug))
    if "orderfilled-placeholder" in text or title.startswith("trade indexer placeholder market"):
        return "placeholder"
    if any(token in text for token in ("politic", "election", "trump", "biden", "congress", "iran", "ceasefire", "war", "president")):
        return "politics"
    if any(token in text for token in ("crypto", "bitcoin", "ethereum", "solana", "xrp", "token", "btc", "eth")):
        return "crypto"
    if any(token in text for token in ("finance", "business", "econom", "fed", "rate", "inflation", "ipo", "valuation", "stock", "macro")):
        return "macro"
    if any(token in text for token in ("tech", "ai", "openai", "spacex", "tesla", "apple", "google", "nvidia")):
        return "tech"
    if any(token in text for token in ("weather", "temperature", "hurricane", "rain", "snow")):
        return "weather"
    if any(token in text for token in ("sports", "tennis", "soccer", "nba", "nfl", "mlb", "nhl", "fifa", "formula1", "ufc", "wta", "atp", "itf")):
        return "sports"
    if any(token in text for token in ("esports", "gaming", "counter-strike", "league of legends", "dota", "valorant", "rainbow six", "lol:")):
        return "games"
    if any(token in text for token in ("pop-culture", "awards", "movie", "music", "celebrity", "oscars", "grammy")):
        return "culture"
    return category or "market"


def _interleave_group_categories(groups: List[Dict[str, Any]], page_size: int) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for group in groups:
        bucket = _group_category_bucket(group)
        if bucket == "placeholder":
            continue
        if bucket not in buckets:
            buckets[bucket] = []
            order.append(bucket)
        buckets[bucket].append(group)
    if len(order) <= 1:
        return [group for group in groups if _group_category_bucket(group) != "placeholder"]
    selected: List[Dict[str, Any]] = []
    seen: set[str] = set()
    limit = max(1, int(page_size))
    while len(selected) < limit:
        added = False
        for bucket in order:
            bucket_rows = buckets.get(bucket) or []
            while bucket_rows:
                group = bucket_rows.pop(0)
                key = str(group.get("eventId") or group.get("groupId") or group.get("slug") or "")
                if key and key in seen:
                    continue
                selected.append(group)
                if key:
                    seen.add(key)
                added = True
                break
            if len(selected) >= limit:
                break
        if not added:
            break
    for group in groups:
        key = str(group.get("eventId") or group.get("groupId") or group.get("slug") or "")
        if _group_category_bucket(group) == "placeholder":
            continue
        if key and key in seen:
            continue
        selected.append(group)
        if key:
            seen.add(key)
    return selected


def _limit_group_category_dominance(groups: List[Dict[str, Any]], page_size: int) -> List[Dict[str, Any]]:
    """Preserve activity rank while keeping the first screen diverse across categories."""
    if not groups:
        return groups
    page_size = max(1, int(page_size))
    first_screen_limit = min(page_size, 24)
    category_limit = max(2, min(4, max(1, first_screen_limit // 6)))
    selected: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    seen: set[str] = set()
    bucket_counts: Dict[str, int] = {}
    for group in groups:
        bucket = _group_category_bucket(group)
        if bucket == "placeholder":
            continue
        key = str(group.get("eventId") or group.get("groupId") or group.get("slug") or "")
        if key and key in seen:
            continue
        if len(selected) < first_screen_limit and bucket_counts.get(bucket, 0) >= category_limit:
            deferred.append(group)
            continue
        selected.append(group)
        if key:
            seen.add(key)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    for group in deferred:
        key = str(group.get("eventId") or group.get("groupId") or group.get("slug") or "")
        if key and key in seen:
            continue
        selected.append(group)
        if key:
            seen.add(key)
    return selected


def _serving_row_identity(row: Dict[str, Any]) -> str:
    return str(row.get("serving_key") or row.get("group_id") or row.get("event_id") or row.get("event_slug") or "")


def _serving_select_sql(where_sql: str, order_sql: str) -> str:
    order_clause = f"ORDER BY {order_sql}" if str(order_sql or "").strip() else ""
    return f"""
        SELECT
          serving_key, group_id, event_id, event_slug, event_title, title, category, tags,
          created_at, end_date, volume_24h, trade_count_24h, last_activity_at, outcome_count,
          default_market_id, default_condition_id, default_gamma_market_id, default_outcome_key,
          top_outcomes, outcomes, completion_status, is_trading_closed, active_rank, source, updated_at
        FROM event_market_serving
        WHERE {where_sql}
        {order_clause}
        LIMIT ? OFFSET ?
        """


def _serving_active_rows(
    dependencies: MarketGroupDependencies,
    *,
    where_sql: str,
    order_sql: str,
    params: List[Any],
    page_size: int,
    offset: int,
) -> List[Dict[str, Any]]:
    query_limit = max(page_size, min(500, max(page_size * ACTIVE_GROUP_LOOKAHEAD_MULTIPLIER, page_size + 120))) if offset == 0 else page_size
    try:
        rows = dependencies.query_all(
            _serving_select_sql(where_sql, order_sql),
            [*params, query_limit, offset],
        )
    except Exception:
        logger = getattr(dependencies.application, "logger", None)
        if logger:
            logger.exception("market group active base query failed; retrying compact active-rank query")
        rows = dependencies.query_all(
            _serving_select_sql(where_sql, "active_rank DESC NULLS LAST, last_activity_at DESC NULLS LAST, volume_24h DESC"),
            [*params, query_limit, offset],
        )
    if offset > 0:
        return rows
    return rows


def _serving_table_ready(
    dependencies: MarketGroupDependencies,
) -> bool:
    if dependencies.get_backend is not None:
        try:
            if str(dependencies.get_backend()).lower() not in {
                "postgres",
                "postgresql",
            }:
                return False
        except Exception:
            return False
    if dependencies.table_exists is None:
        return False
    try:
        if not dependencies.table_exists("event_market_serving"):
            return False
        rows = dependencies.query_all(
            "SELECT 1 FROM event_market_serving LIMIT 1",
            (),
        )
        return bool(rows)
    except Exception:
        logger = getattr(dependencies.application, "logger", None)
        if logger:
            logger.exception("event_market_serving readiness check failed")
        return False


def _serving_json_list(value: Any) -> List[Any]:
    items = _as_list(value)
    if len(items) == 1 and isinstance(items[0], str):
        text = items[0].strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, list) else []
            except Exception:
                return []
    return items


def _serving_group_from_row(
    dependencies: MarketGroupDependencies,
    row: Dict[str, Any],
) -> Dict[str, Any]:
    outcomes = [item for item in _serving_json_list(row.get("outcomes")) if isinstance(item, dict)]
    top_outcomes = [item for item in _serving_json_list(row.get("top_outcomes")) if isinstance(item, dict)]
    if not top_outcomes:
        top_outcomes = sorted(
            [outcome for outcome in outcomes if _float_value(outcome.get("yesPrice")) is not None],
            key=lambda outcome: _float_value(outcome.get("yesPrice")) or 0.0,
            reverse=True,
        )[:5]
    return {
        "groupId": row.get("group_id") or f"event:{row.get('event_id') or row.get('serving_key')}",
        "eventId": row.get("event_id") or row.get("serving_key"),
        "title": row.get("title") or row.get("event_title") or "Untitled event",
        "slug": row.get("event_slug"),
        "category": row.get("category") or "market",
        "tags": _serving_json_list(row.get("tags")),
        "createdAt": row.get("created_at"),
        "endDate": row.get("end_date"),
        "volume24h": _float_value(row.get("volume_24h")) or 0.0,
        "tradeCount24h": _positive_count_or_none(row.get("trade_count_24h")),
        "volumeScope": "event",
        "volumeSource": row.get("source") or "postgres-serving",
        "outcomeCount": int(row.get("outcome_count") or len(outcomes)),
        "lastActivityAt": row.get("last_activity_at"),
        "defaultOutcomeKey": row.get("default_outcome_key"),
        "defaultMarketId": row.get("default_market_id"),
        "outcomes": outcomes,
        "topOutcomes": top_outcomes,
        "completionStatus": row.get("completion_status") or "OPEN",
        "isTradingClosed": bool(row.get("is_trading_closed")),
        "sourceMode": "postgres-serving",
        "generatedAt": dependencies.utc_now_iso(),
    }


def _outcome_market_id(outcome: Dict[str, Any]) -> Optional[int]:
    market_id = _float_value((outcome or {}).get("marketId") or (outcome or {}).get("localMarketId"))
    if market_id is None:
        return None
    return int(market_id)


def _group_market_ids(group: Dict[str, Any]) -> List[int]:
    ids: List[int] = []
    default_market_id = _float_value(group.get("defaultMarketId"))
    if default_market_id is not None:
        ids.append(int(default_market_id))
    for outcome in [*(group.get("outcomes") or []), *(group.get("topOutcomes") or [])]:
        if not isinstance(outcome, dict):
            continue
        market_id = _outcome_market_id(outcome)
        if market_id is not None:
            ids.append(market_id)
    return list(dict.fromkeys(ids))


def _apply_orderfilled_trade_counts(
    dependencies: MarketGroupDependencies,
    items: List[Dict[str, Any]],
    *,
    default_only: bool = False,
) -> None:
    if default_only:
        market_ids = [
            int(market_id)
            for item in items
            for market_id in [_float_value(item.get("defaultMarketId"))]
            if market_id is not None
        ]
    else:
        market_ids = [market_id for item in items for market_id in _group_market_ids(item)]
    try:
        stats = clickhouse_orderfilled_service.get_market_stats(
            dependencies.source,  # type: ignore[arg-type]
            market_ids,
            hours=24,
        )
    except Exception:
        logger = getattr(dependencies.application, "logger", None)
        if logger:
            logger.exception("market group OrderFilled trade-count lookup failed")
        return
    if not stats:
        return
    for group in items:
        group_counts: Dict[int, int] = {}
        for collection_name in ("outcomes", "topOutcomes"):
            for outcome in group.get(collection_name) or []:
                if not isinstance(outcome, dict):
                    continue
                market_id = _outcome_market_id(outcome)
                if default_only and market_id not in stats:
                    continue
                stat = stats.get(market_id) if market_id is not None else None
                trade_count = _positive_count_or_none((stat or {}).get("trade_count_24h"))
                outcome["tradeCount24h"] = trade_count
                if market_id is not None and trade_count is not None:
                    group_counts[market_id] = trade_count
        if not default_only:
            group["tradeCount24h"] = sum(group_counts.values()) or None


def _latest_block_close_by_market_id(
    context: Mapping[str, Any] | MarketGroupDependencies,
    market_ids: Iterable[int],
) -> Dict[int, Dict[str, Any]]:
    if isinstance(context, MarketGroupDependencies):
        table_exists = context.table_exists
        query_all = context.query_all
        application = context.application
    else:
        table_exists = resolve_optional_service_callable(
            context,
            "table_exists",
        )
        query_all = _service_callable(context, "query_all")
        application = resolve_service_value(context, "app")
    ids = [int(market_id) for market_id in dict.fromkeys(market_ids) if market_id is not None]
    if not ids:
        return {}
    if not callable(table_exists):
        return {}
    try:
        if not table_exists("quant.market_token_block_close"):
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = query_all(
            f"""
            SELECT DISTINCT ON (market_id)
                market_id,
                block_number,
                close_price,
                yes_probability_close
            FROM quant.market_token_block_close
            WHERE market_id IN ({placeholders})
              AND token_side = 'YES'
              AND NOT (
                  jsonb_exists(COALESCE(anomaly_flags, '[]'::jsonb), 'extreme_price_trade_present')
                  AND COALESCE(trade_count, 0) <= 3
                  AND COALESCE(volume, 0) <= 250
                  AND (close_price >= 0.95 OR close_price <= 0.002)
            )
            ORDER BY market_id, block_number DESC
            """,
            ids,
        )
    except Exception:
        logger = getattr(application, "logger", None)
        if logger:
            logger.exception("market group latest block-close lookup failed")
        return {}
    latest: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        market_id = _float_value(row.get("market_id"))
        if market_id is None:
            continue
        price = _float_value(row.get("yes_probability_close"))
        if price is None:
            price = _float_value(row.get("close_price"))
        if price is None:
            continue
        latest[int(market_id)] = {
            "price": _clamp_probability(price),
            "blockNumber": row.get("block_number"),
        }
    return latest


def _apply_latest_block_close_prices(
    context: Mapping[str, Any] | MarketGroupDependencies,
    items: List[Dict[str, Any]],
    *,
    default_only: bool = False,
) -> None:
    if default_only:
        market_ids = [
            int(market_id)
            for item in items
            for market_id in [_float_value(item.get("defaultMarketId"))]
            if market_id is not None
        ]
    else:
        market_ids = [market_id for item in items for market_id in _group_market_ids(item)]
    latest_by_market_id = _latest_block_close_by_market_id(
        context,
        market_ids,
    )
    if not latest_by_market_id:
        return
    for group in items:
        default_market_id = _float_value(group.get("defaultMarketId"))
        default_price: Optional[float] = None
        for outcome in [*(group.get("outcomes") or []), *(group.get("topOutcomes") or [])]:
            if not isinstance(outcome, dict):
                continue
            market_id = _outcome_market_id(outcome)
            latest = latest_by_market_id.get(market_id) if market_id is not None else None
            if not latest:
                continue
            yes_price = latest["price"]
            outcome["blockCloseYesPrice"] = yes_price
            outcome["blockCloseBlockNumber"] = latest.get("blockNumber")
            outcome["yesPrice"] = yes_price
            outcome["noPrice"] = _clamp_probability(1.0 - yes_price)
            if default_market_id is not None and market_id == int(default_market_id):
                default_price = yes_price
        if default_price is not None:
            group["latestBlockClosePrice"] = default_price


def _group_has_terminal_probability(group: Dict[str, Any]) -> bool:
    if _is_terminal_probability(group.get("latestBlockClosePrice")):
        return True
    for outcome in [*(group.get("outcomes") or []), *(group.get("topOutcomes") or [])]:
        if not isinstance(outcome, dict):
            continue
        if (
            _is_terminal_probability(outcome.get("blockCloseYesPrice"))
            or _is_terminal_probability(outcome.get("yesPrice"))
            or _is_terminal_probability(outcome.get("noPrice"))
        ):
            return True
    return False


def _group_outcome_candidates(group: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for outcome in [*(group.get("outcomes") or []), *(group.get("topOutcomes") or [])]:
        if not isinstance(outcome, dict):
            continue
        if outcome.get("marketId") is None and not outcome.get("yesTokenId"):
            continue
        key = str(outcome.get("marketId") or outcome.get("outcomeKey") or outcome.get("gammaMarketId") or len(candidates))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(outcome)
    return candidates


def _default_outcome_runtime_score(outcome: Dict[str, Any]) -> Tuple[float, float, float, float]:
    price = _outcome_probability(outcome)
    volume = _float_value(outcome.get("volume24h")) or 0.0
    trades = _float_value(outcome.get("tradeCount24h")) or 0.0
    has_block_close = outcome.get("blockCloseYesPrice") not in (None, "")
    distance_from_mid = 0.0 if price is None else min(1.0, abs(price - 0.5) * 2.0)
    score = min(70.0, volume ** 0.35) + min(70.0, trades * 3.0) + distance_from_mid * 24.0
    if has_block_close:
        score += 28.0
    if outcome.get("marketId") is not None:
        score += 12.0
    if outcome.get("yesTokenId"):
        score += 8.0
    if price is not None and abs(price - 0.5) < 0.0001 and trades <= 0 and volume < 25:
        score -= 45.0
    return (score, volume, trades, distance_from_mid)


def _retarget_group_default_outcome(group: Dict[str, Any]) -> bool:
    candidates = [
        outcome
        for outcome in _group_outcome_candidates(group)
        if _outcome_is_focusable(outcome, require_activity=False)
    ]
    if not candidates:
        return False
    selected = max(candidates, key=_default_outcome_runtime_score)
    group["defaultOutcomeKey"] = selected.get("outcomeKey")
    group["defaultMarketId"] = selected.get("marketId") or selected.get("localMarketId")
    selected_price = _outcome_probability(selected)
    if selected_price is not None:
        group["latestBlockClosePrice"] = selected_price
    return True


def _filter_active_focus_groups(groups: List[Dict[str, Any]], *, require_activity: bool) -> List[Dict[str, Any]]:
    return [
        group
        for group in groups
        if _group_has_focusable_default(group, require_activity=require_activity)
    ]


def _serving_market_groups_payload(
    dependencies: MarketGroupDependencies,
    *,
    query: str,
    page: int,
    page_size: int,
    sort: str,
) -> Optional[Dict[str, Any]]:
    if not _serving_table_ready(dependencies):
        return None
    where = [
        "outcome_count > 0",
        "LOWER(COALESCE(category, '')) NOT LIKE '%%orderfilled-placeholder%%'",
        "LOWER(COALESCE(event_slug, '')) NOT LIKE '%%trade-indexer-placeholder%%'",
        "LOWER(COALESCE(title, '')) NOT LIKE 'trade indexer placeholder market%%'",
        "LOWER(COALESCE(CAST(tags AS TEXT), '')) NOT LIKE '%%orderfilled-placeholder%%'",
    ]
    params: List[Any] = []
    if sort == "active":
        now_iso = dependencies.utc_now_iso()
        where.append("is_trading_closed = FALSE")
        where.append("completion_status NOT IN ('SETTLED', 'CANCELLED', 'CLOSED_UNRESOLVED')")
        where.append("(end_date IS NULL OR end_date >= ?)")
        params.append(now_iso)
        fresh_cutoff = datetime.fromtimestamp(
            _parse_timestamp(now_iso) - ACTIVE_GROUP_STALE_SECONDS,
            tz=timezone.utc,
        ).isoformat()
        where.append("last_activity_at >= ?")
        params.append(fresh_cutoff)
    if query:
        like = f"%{query}%"
        where.append("(title ILIKE ? OR COALESCE(event_slug, '') ILIKE ? OR COALESCE(category, '') ILIKE ? OR tags::text ILIKE ?)")
        params.extend([like, like, like, like])
    where_sql = " AND ".join(where)
    active_order_sql = "volume_24h DESC, trade_count_24h DESC, last_activity_at DESC NULLS LAST, active_rank DESC"
    order_sql = {
        "active": active_order_sql,
        "new": "created_at DESC NULLS LAST, last_activity_at DESC NULLS LAST, volume_24h DESC",
        "volume": "volume_24h DESC, last_activity_at DESC NULLS LAST, active_rank DESC",
    }.get(sort, "active_rank DESC, volume_24h DESC, last_activity_at DESC NULLS LAST")
    offset = (page - 1) * page_size
    if sort == "active" and not query:
        rows = _serving_active_rows(
            dependencies,
            where_sql=where_sql,
            order_sql=order_sql,
            params=params,
            page_size=page_size,
            offset=offset,
        )
    else:
        rows = dependencies.query_all(
            _serving_select_sql(where_sql, order_sql),
            [*params, page_size, offset],
        )
    total: int
    fast_active = sort == "active" and not query
    if fast_active:
        total = offset + min(len(rows), page_size)
    else:
        total_row = dependencies.query_all(
            f"SELECT COUNT(*) AS total FROM event_market_serving WHERE {where_sql}",
            params,
        )
        total = int((total_row[0] or {}).get("total") or 0) if total_row else 0
    items = [_serving_group_from_row(dependencies, row) for row in rows]
    if sort == "active":
        items = [item for item in items if _retarget_group_default_outcome(item)]
    if sort == "active" and not query:
        now_ts = _parse_timestamp(dependencies.utc_now_iso())
        items.sort(key=lambda group: _active_group_sort_key(group, now_ts=now_ts))
        items = _limit_group_category_dominance(items, page_size)
        items = items[: max(page_size * 2, page_size)]
    _apply_latest_block_close_prices(dependencies, items, default_only=True)
    if sort == "active":
        items = [item for item in items if _retarget_group_default_outcome(item)]
        if not query:
            items = _filter_active_focus_groups(items, require_activity=True)
    if sort == "active" and not query:
        now_ts = _parse_timestamp(dependencies.utc_now_iso())
        items.sort(key=lambda group: _active_group_sort_key(group, now_ts=now_ts))
        items = _limit_group_category_dominance(items, page_size)
        total = max(total, offset + min(len(items), page_size) + (1 if len(rows) > page_size else 0))
    items = items[:page_size]
    _apply_orderfilled_trade_counts(dependencies, items, default_only=True)
    if sort == "active" and not query and not items:
        return None
    return {
        "items": items,
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": max(1, (total + page_size - 1) // page_size),
            "hasMore": (len(rows) > page_size) if fast_active else offset + len(items) < total,
        },
        "generatedAt": dependencies.utc_now_iso(),
        "sourceMode": "postgres-serving",
    }


def _serving_market_group_detail(
    dependencies: MarketGroupDependencies,
    identifier: str,
) -> Optional[Dict[str, Any]]:
    if not _serving_table_ready(dependencies):
        return None
    raw = str(identifier or "").strip()
    normalized = raw.removeprefix("event:")
    candidates = list(dict.fromkeys([raw, normalized, f"event:{normalized}", f"slug:{normalized}"]))
    placeholders = ",".join("?" for _ in candidates)
    rows = dependencies.query_all(
        f"""
        SELECT
          serving_key, group_id, event_id, event_slug, event_title, title, category, tags,
          created_at, end_date, volume_24h, trade_count_24h, last_activity_at, outcome_count,
          default_market_id, default_condition_id, default_gamma_market_id, default_outcome_key,
          top_outcomes, outcomes, completion_status, is_trading_closed, active_rank, source, updated_at
        FROM event_market_serving
        WHERE serving_key IN ({placeholders})
           OR group_id IN ({placeholders})
           OR event_id IN ({placeholders})
           OR event_slug IN ({placeholders})
        ORDER BY active_rank DESC, volume_24h DESC
        LIMIT 1
        """,
        [*candidates, *candidates, *candidates, *candidates],
    )
    if not rows:
        return None
    group = _serving_group_from_row(dependencies, rows[0])
    _apply_orderfilled_trade_counts(dependencies, [group])
    group["status"] = "ok"
    return group


def _empty_market_groups_payload(
    dependencies: MarketGroupDependencies,
    *,
    page: int,
    page_size: int,
    status: str = "degraded",
) -> Dict[str, Any]:
    return {
        "items": [],
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": 0,
            "totalPages": 1,
            "hasMore": False,
        },
        "generatedAt": dependencies.utc_now_iso(),
        "status": status,
    }


def get_market_groups_payload(
    ctx: Mapping[str, Any],
    *,
    query: str = "",
    page: int = 1,
    page_size: int = 80,
    sort: str = "active",
) -> Dict[str, Any]:
    return _get_market_groups_payload(
        MarketGroupDependencies.from_context(ctx),
        query=query,
        page=page,
        page_size=page_size,
        sort=sort,
    )


def _get_market_groups_payload(
    dependencies: MarketGroupDependencies,
    *,
    query: str = "",
    page: int = 1,
    page_size: int = 80,
    sort: str = "active",
) -> Dict[str, Any]:
    page = max(1, int(page))
    page_size = min(200, max(1, int(page_size)))
    sort = str(sort or "active").strip().lower()
    if sort not in {"active", "new", "volume"}:
        sort = "active"
    query = str(query or "").strip()

    cache_key = json.dumps({"q": query, "page": page, "pageSize": page_size, "sort": sort, "v": 34}, sort_keys=True)

    def _builder() -> Dict[str, Any]:
        serving_payload = _serving_market_groups_payload(
            dependencies,
            query=query,
            page=page,
            page_size=page_size,
            sort=sort,
        )
        if serving_payload is not None:
            return serving_payload

        fetch_target = max(100, min(1000, (page * page_size * 3)))
        if sort == "active":
            recent_events: List[Dict[str, Any]] = []
            volume_events: List[Dict[str, Any]] = []
            try:
                recent_events = _fetch_gamma_events(dependencies, target_events=fetch_target, order="startDate")
            except Exception:
                dependencies.application.logger.exception(
                    "market-group list recent gamma fetch failed page=%s page_size=%s",
                    page,
                    page_size,
                )
            try:
                volume_events = _fetch_gamma_events(dependencies, target_events=fetch_target, order="volume24hr")
            except Exception:
                dependencies.application.logger.exception(
                    "market-group list volume gamma fetch failed page=%s page_size=%s",
                    page,
                    page_size,
                )
            events = _merge_unique_events(recent_events, volume_events)
            if not events:
                return _empty_market_groups_payload(dependencies, page=page, page_size=page_size)
        else:
            try:
                gamma_order = "startDate" if sort == "new" else "volume24hr"
                events = _fetch_gamma_events(dependencies, target_events=fetch_target, order=gamma_order)
            except Exception:
                dependencies.application.logger.exception(
                    "market-group list gamma fetch failed sort=%s page=%s page_size=%s",
                    sort,
                    page,
                    page_size,
                )
                return _empty_market_groups_payload(dependencies, page=page, page_size=page_size)
        now_iso = dependencies.utc_now_iso()
        now_ts = _parse_timestamp(now_iso)
        candidate_events = [
            event
            for event in events
            if isinstance(event, dict)
            and not _is_event_ended(event, now_iso)
            and not _is_noisy_event(event)
            and (
                not query
                or _matches_query(
                    {
                        "title": event.get("title"),
                        "slug": event.get("slug"),
                        "category": _category_for_event(event),
                        "tags": _event_tags(event),
                        "outcomes": [
                            {"label": _label_for_market(str(event.get("title") or ""), market)}
                            for market in (event.get("markets") or [])
                            if isinstance(market, dict)
                        ],
                    },
                    query,
                )
            )
        ]

        if sort == "new":
            candidate_events.sort(
                key=lambda event: _parse_timestamp(event.get("startDate") or event.get("createdAt") or event.get("created_at")),
                reverse=True,
            )
        elif sort == "volume":
            candidate_events.sort(
                key=lambda event: _float_value(event.get("volume24hr") or event.get("volume_24hr") or event.get("volume24h")) or 0.0,
                reverse=True,
            )

        offset = (page - 1) * page_size
        lookup_events = candidate_events[: offset + max(page_size * 3, page_size + 20)]
        lookups = _local_market_lookup(dependencies, lookup_events)
        groups = [
            group
            for event in lookup_events
            for group in [_normalize_group(dependencies, event, lookups)]
            if group is not None and _matches_query(group, query)
        ]
        if sort == "active":
            groups.sort(key=lambda group: _active_group_sort_key(group, now_ts=now_ts))
            _apply_latest_block_close_prices(dependencies, groups)
            groups = [group for group in groups if _retarget_group_default_outcome(group)]
            if not query:
                groups = _filter_active_focus_groups(groups, require_activity=True)
            groups.sort(key=lambda group: _active_group_sort_key(group, now_ts=now_ts))

        visible = groups[offset: offset + page_size]
        has_more = len(groups) > offset + page_size or len(candidate_events) > len(lookup_events)
        payload = {
            "items": visible,
            "pagination": {
                "page": page,
                "pageSize": page_size,
                "total": max(len(groups), len(candidate_events)),
                "totalPages": max(1, (max(len(groups), len(candidate_events)) + page_size - 1) // page_size),
                "hasMore": has_more,
            },
            "generatedAt": dependencies.utc_now_iso(),
        }
        if not any((outcome.get("localMarketId") or outcome.get("marketId")) for group in visible for outcome in group.get("outcomes") or []):
            payload["status"] = "degraded"
            payload["sourceMode"] = "gamma-fallback"
        return payload

    return dependencies.get_snapshot_payload(
        MARKET_GROUPS_LIST_NAMESPACE,
        cache_key,
        _builder,
        ttl_seconds=MARKET_GROUPS_LIST_TTL_SECONDS,
    )


def get_market_group_detail_payload(
    ctx: Mapping[str, Any],
    event_id: str,
) -> Optional[Dict[str, Any]]:
    return _get_market_group_detail_payload(
        MarketGroupDependencies.from_context(ctx),
        event_id,
    )


def _get_market_group_detail_payload(
    dependencies: MarketGroupDependencies,
    event_id: str,
) -> Optional[Dict[str, Any]]:
    identifier = str(event_id or "").strip()
    if not identifier:
        return None
    cache_key = json.dumps({"eventId": identifier, "v": 8}, sort_keys=True)

    def _builder() -> Optional[Dict[str, Any]]:
        serving_payload = _serving_market_group_detail(dependencies, identifier)
        if serving_payload is not None:
            return serving_payload

        try:
            event = _fetch_gamma_event_by_id(dependencies, identifier)
        except Exception:
            dependencies.application.logger.exception(
                "market-group detail gamma fetch failed event_id=%s",
                identifier,
            )
            return {
                "groupId": f"event:{identifier}",
                "eventId": identifier,
                "title": "Market group unavailable",
                "outcomes": [],
                "topOutcomes": [],
                "generatedAt": dependencies.utc_now_iso(),
                "status": "degraded",
            }
        if not isinstance(event, dict):
            return None
        lookups = _local_market_lookup(dependencies, [event])
        group = _normalize_group(dependencies, event, lookups)
        if group is None:
            return None
        group["generatedAt"] = dependencies.utc_now_iso()
        group["status"] = "ok"
        return group

    payload = dependencies.get_snapshot_payload(
        MARKET_GROUPS_DETAIL_NAMESPACE,
        cache_key,
        _builder,
        ttl_seconds=MARKET_GROUPS_DETAIL_TTL_SECONDS,
    )
    return payload if isinstance(payload, dict) else None


def get_market_group_chart_payload(
    ctx: Mapping[str, Any],
    event_id: str,
    *,
    range_name: str = "1d",
) -> Optional[Dict[str, Any]]:
    return _get_market_group_chart_payload(
        MarketGroupDependencies.from_context(ctx),
        event_id,
        range_name=range_name,
    )


def _get_market_group_chart_payload(
    dependencies: MarketGroupDependencies,
    event_id: str,
    *,
    range_name: str = "1d",
) -> Optional[Dict[str, Any]]:
    identifier = str(event_id or "").strip()
    normalized_range = str(range_name or "1d").strip().lower()
    if normalized_range not in CHART_RANGE_INTERVALS:
        normalized_range = "1d"
    cache_key = json.dumps({"eventId": identifier, "range": normalized_range, "v": 9}, sort_keys=True)

    def _builder() -> Optional[Dict[str, Any]]:
        detail = _get_market_group_detail_payload(dependencies, identifier)
        if not isinstance(detail, dict):
            return None
        if detail.get("status") == "degraded":
            return {
                "eventId": detail.get("eventId"),
                "groupId": detail.get("groupId"),
                "title": detail.get("title"),
                "defaultOutcomeKey": detail.get("defaultOutcomeKey"),
                "range": normalized_range,
                "interval": CHART_RANGE_INTERVALS.get(normalized_range, "15m"),
                "series": [],
                "generatedAt": dependencies.utc_now_iso(),
                "status": "degraded",
            }
        sorted_outcomes = sorted(
            [outcome for outcome in (detail.get("outcomes") or []) if isinstance(outcome, dict)],
            key=lambda outcome: float(outcome.get("yesPrice") or 0.0),
            reverse=True,
        )
        interval = CHART_RANGE_INTERVALS.get(normalized_range, "15m")
        series = []
        for index, outcome in enumerate(sorted_outcomes):
            yes_token_id = str(outcome.get("yesTokenId") or "").strip()
            if not yes_token_id:
                continue
            pseudo_market = {
                "id": outcome.get("marketId") or f"{identifier}:{outcome.get('outcomeKey')}",
                "yes_token_id": yes_token_id,
            }
            points = dependencies.get_market_clob_price_series(
                pseudo_market,
                range_name=normalized_range,
                interval=interval,
            )
            normalized_points = []
            for point in points or []:
                yes_price = _float_value(point.get("yesPrice"))
                timestamp = point.get("timestamp")
                if yes_price is None or not timestamp:
                    continue
                normalized_points.append({"timestamp": timestamp, "price": yes_price})
            if len(normalized_points) < 2:
                continue
            series.append(
                {
                    "outcomeKey": outcome.get("outcomeKey"),
                    "label": outcome.get("label"),
                    "marketId": outcome.get("marketId"),
                    "color": SERIES_PALETTE[index % len(SERIES_PALETTE)],
                    "points": normalized_points,
                }
            )

        return {
            "eventId": detail.get("eventId"),
            "groupId": detail.get("groupId"),
            "title": detail.get("title"),
            "defaultOutcomeKey": detail.get("defaultOutcomeKey"),
            "range": normalized_range,
            "interval": interval,
            "series": series,
            "historyStatus": "ok" if series else "pending",
            "priceSource": "clob-history" if series else "missing",
            "generatedAt": dependencies.utc_now_iso(),
        }

    payload = dependencies.get_snapshot_payload(
        MARKET_GROUPS_CHART_NAMESPACE,
        cache_key,
        _builder,
        ttl_seconds=CHART_RANGE_TTLS.get(normalized_range, 60),
    )
    return payload if isinstance(payload, dict) else None
