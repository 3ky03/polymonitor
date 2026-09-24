from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
    resolve_service_value,
)


FOOD_BASKET_SNAPSHOT_NAMESPACE = "snapshot:macro:food-retail-basket-pressure"
FOOD_BASKET_CACHE_KEY = "panel-v1"
DEFAULT_ITEM_LIMIT = 8
FRED_CSV_LOOKBACK_YEARS = 4
SERIES = (
    {"key": "food", "seriesId": "CPIUFDSL", "label": "Food CPI", "weight": 1.0},
    {"key": "home", "seriesId": "CUSR0000SAF11", "label": "Food at home", "weight": 1.3},
    {"key": "away", "seriesId": "CUSR0000SEFV", "label": "Food away from home", "weight": 1.1},
    {"key": "cereals", "seriesId": "CUSR0000SAF111", "label": "Cereals / bakery", "weight": 0.8},
    {"key": "meat_eggs", "seriesId": "CUSR0000SAF112", "label": "Meat / eggs", "weight": 1.1},
    {"key": "fruit_veg", "seriesId": "CUSR0000SAF113", "label": "Fruit / veg", "weight": 0.9},
    {"key": "beverages", "seriesId": "CUSR0000SAF116", "label": "Nonalcoholic beverages", "weight": 0.6},
    {"key": "eggs", "seriesId": "CUSR0000SEFJ", "label": "Eggs", "weight": 0.6},
)


@dataclass(frozen=True)
class FoodRetailBasketDependencies:
    settings: Any
    application: Any
    http_text_get: Callable[..., str]
    utc_now_iso: Callable[..., Any] | None
    snapshot_store: Any
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> FoodRetailBasketDependencies:
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
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
        )


FoodRetailBasketContext = Mapping[str, Any] | FoodRetailBasketDependencies


def _dependencies(
    context: FoodRetailBasketContext,
) -> FoodRetailBasketDependencies:
    if isinstance(context, FoodRetailBasketDependencies):
        return context
    return FoodRetailBasketDependencies.from_context(context)


def _utc_now_iso(dependencies: FoodRetailBasketDependencies) -> str:
    if dependencies.utc_now_iso is not None:
        return str(dependencies.utc_now_iso())
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _float(value: Any) -> Optional[float]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None


def _fred_url(
    dependencies: FoodRetailBasketDependencies,
    series_id: str,
) -> str:
    template = getattr(
        dependencies.settings,
        "food_basket_fred_csv_url_template",
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}",
    )
    url = str(template).format(series_id=series_id)
    lookback_years = int(
        getattr(
            dependencies.settings,
            "fred_csv_lookback_years",
            FRED_CSV_LOOKBACK_YEARS,
        )
        or FRED_CSV_LOOKBACK_YEARS
    )
    start_date = f"{max(1900, datetime.now(timezone.utc).year - lookback_years)}-01-01"
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("cosd", start_date)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _fred_row_date(row: Dict[str, Any]) -> str:
    return str(row.get("observation_date") or row.get("DATE") or row.get("date") or "").strip()


def _fetch_series(
    ctx: FoodRetailBasketContext,
    spec: Dict[str, Any],
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    url = _fred_url(dependencies, spec["seriesId"])
    text = dependencies.http_text_get(
        url,
        timeout=15,
        headers={"User-Agent": "polydata-food-basket/1.0"},
    )
    rows: List[Dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(str(text or "")))
    value_col = spec["seriesId"]
    for row in reader:
        value = _float(row.get(value_col))
        date = _fred_row_date(row)
        if value is None or not date:
            continue
        rows.append({"date": date, "value": value})
    rows.sort(key=lambda item: item["date"])
    if len(rows) < 13:
        raise ValueError(f"not enough observations for {spec['seriesId']}")
    latest = rows[-1]
    prev = rows[-2]
    year_ago = rows[-13]
    three_ago = rows[-4] if len(rows) >= 4 else prev
    mom = (latest["value"] / prev["value"] - 1.0) * 100.0
    yoy = (latest["value"] / year_ago["value"] - 1.0) * 100.0
    three_month = (latest["value"] / three_ago["value"] - 1.0) * 100.0
    return {
        "key": spec["key"],
        "seriesId": spec["seriesId"],
        "label": spec["label"],
        "date": latest["date"],
        "value": round(latest["value"], 3),
        "momPct": round(mom, 2),
        "yoyPct": round(yoy, 2),
        "threeMonthPct": round(three_month, 2),
        "source": "FRED / BLS CPI",
        "sourceUrl": url,
    }


def _summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        return {"signal": "FOOD WARMING", "bias": "unknown", "pressureScore": None, "topMover": None, "coverage": 0}
    weights = {spec["key"]: spec["weight"] for spec in SERIES}
    score = sum((_float(item.get("momPct")) or 0.0) * weights.get(str(item.get("key")), 1.0) for item in items) / max(1.0, sum(weights.get(str(item.get("key")), 1.0) for item in items))
    top = max(items, key=lambda item: abs(_float(item.get("momPct")) or 0.0))
    if score >= 0.35:
        signal = "FOOD PRESSURE RISING"
        bias = "hot"
    elif score <= -0.2:
        signal = "FOOD DISINFLATION"
        bias = "cool"
    else:
        signal = "FOOD STABLE"
        bias = "neutral"
    return {"signal": signal, "bias": bias, "pressureScore": round(score, 2), "topMover": top, "coverage": len(items)}


def build_food_retail_basket_payload(
    ctx: FoodRetailBasketContext,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    items: List[Dict[str, Any]] = []
    sources: Dict[str, str] = {}
    for spec in SERIES:
        try:
            items.append(_fetch_series(ctx, spec))
            sources[spec["key"]] = "ok"
        except Exception as exc:
            sources[spec["key"]] = "error"
            logger = getattr(dependencies.application, "logger", None)
            if logger is not None:
                logger.exception("food basket source failed source=%s error=%s", spec["key"], exc)
    status = "ok" if len(items) >= 5 else ("degraded" if items else "warming")
    return {
        "generatedAt": _utc_now_iso(dependencies),
        "source": "FRED CSV / BLS CPI food components",
        "sourceUrl": dependencies.settings.food_basket_source_url,
        "status": status,
        "sources": sources,
        "summary": _summary(items),
        "items": items,
    }


def _empty(
    dependencies: FoodRetailBasketDependencies,
    status: str = "warming",
) -> Dict[str, Any]:
    return {
        "generatedAt": _utc_now_iso(dependencies),
        "source": "FRED CSV / BLS CPI food components",
        "sourceUrl": getattr(
            dependencies.settings,
            "food_basket_source_url",
            "",
        ),
        "status": status,
        "sources": {},
        "summary": _summary([]),
        "items": [],
    }


def normalize_food_retail_basket_payload(
    payload: Any,
    *,
    ctx: FoodRetailBasketContext,
    limit: int = DEFAULT_ITEM_LIMIT,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    if not isinstance(payload, dict):
        return _empty(dependencies, "invalid")
    result = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    items = [item for item in (result.get("items") or []) if isinstance(item, dict)]
    result["items"] = items[: max(1, min(int(limit or DEFAULT_ITEM_LIMIT), 24))]
    result["summary"] = result.get("summary") if isinstance(result.get("summary"), dict) else _summary(items)
    result["generatedAt"] = str(
        result.get("generatedAt")
        or _utc_now_iso(dependencies)
    )
    result["status"] = str(result.get("status") or ("ok" if items else "warming"))
    result["source"] = str(result.get("source") or "FRED CSV / BLS CPI food components")
    result["sourceUrl"] = str(
        result.get("sourceUrl")
        or getattr(
            dependencies.settings,
            "food_basket_source_url",
            "",
        )
    )
    return result


def _with_mode(payload: Dict[str, Any], mode: str) -> Dict[str, Any]:
    return {**payload, "cacheMode": mode}


def _read_seeded(
    dependencies: FoodRetailBasketDependencies,
) -> Optional[Dict[str, Any]]:
    if dependencies.get_cached_json is not None:
        payload = dependencies.get_cached_json(
            FOOD_BASKET_SNAPSHOT_NAMESPACE,
            FOOD_BASKET_CACHE_KEY,
        )
        if isinstance(payload, dict):
            return _with_mode(payload, "redis-seed")
    store = dependencies.snapshot_store
    if store is not None:
        payload = store.get(FOOD_BASKET_SNAPSHOT_NAMESPACE, FOOD_BASKET_CACHE_KEY)
        if isinstance(payload, dict):
            return _with_mode(payload, "sqlite-seed")
        stale = store.get_stale(FOOD_BASKET_SNAPSHOT_NAMESPACE, FOOD_BASKET_CACHE_KEY)
        if isinstance(stale, dict):
            return _with_mode(stale, "stale-seed")
    return None


def _store_live(
    dependencies: FoodRetailBasketDependencies,
    payload: Dict[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    if dependencies.snapshot_store is not None:
        dependencies.snapshot_store.set(
            FOOD_BASKET_SNAPSHOT_NAMESPACE,
            FOOD_BASKET_CACHE_KEY,
            payload,
            ttl_seconds,
        )
    if dependencies.set_cached_json is not None:
        dependencies.set_cached_json(
            FOOD_BASKET_SNAPSHOT_NAMESPACE,
            FOOD_BASKET_CACHE_KEY,
            payload,
            ttl_seconds,
        )


def get_food_retail_basket_snapshot(
    ctx: FoodRetailBasketContext,
    limit: int = DEFAULT_ITEM_LIMIT,
    *,
    allow_live_build: bool = True,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    ttl = max(
        1800,
        int(
            getattr(
                dependencies.settings,
                "food_basket_ttl_seconds",
                21600,
            )
            or 21600
        ),
    )
    seeded = _read_seeded(dependencies)
    if seeded is not None:
        return normalize_food_retail_basket_payload(
            seeded,
            ctx=dependencies,
            limit=limit,
        )
    if not allow_live_build:
        return normalize_food_retail_basket_payload(
            {
                **_empty(dependencies),
                "cacheMode": "seed-miss",
            },
            ctx=dependencies,
            limit=limit,
        )
    payload = _with_mode(build_food_retail_basket_payload(ctx), "live-build")
    if payload.get("items"):
        _store_live(dependencies, payload, ttl_seconds=ttl)
    return normalize_food_retail_basket_payload(
        payload,
        ctx=dependencies,
        limit=limit,
    )
