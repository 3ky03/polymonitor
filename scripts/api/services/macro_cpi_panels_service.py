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


SNAPSHOT_NAMESPACE_PREFIX = "snapshot:macro:"
DEFAULT_ITEM_LIMIT = 8
MAX_ITEM_LIMIT = 60
DEFAULT_TTL_SECONDS = 21600
FRED_SOURCE = "FRED CSV / public macro series"
CACHE_KEY = "panel-v2"
FRED_CSV_LOOKBACK_YEARS = 4
MACRO_CPI_PANEL_IDS = (
    "supply-tariff-import-watch",
    "shelter-rent-oer-pressure",
    "labor-wage-services-pressure",
    "growth-demand-recession-tracker",
    "fed-rates-polymarket-gap",
)


PANEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "supply-tariff-import-watch": {
        "source": "FRED CSV / Federal Register trade policy",
        "sourceUrl": "https://fred.stlouisfed.org/",
        "signalHot": "SUPPLY / TARIFF PRESSURE",
        "signalCool": "SUPPLY CHAIN EASING",
        "signalNeutral": "IMPORT WATCH MIXED",
        "linkedMarketCategories": ["cpi", "fed", "growth"],
        "series": [
            {"key": "ppi_all", "seriesId": "PPIACO", "label": "Producer prices all commodities", "group": "Upstream", "icon": "source", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "cpi_commodities", "seriesId": "CUSR0000SAC", "label": "CPI commodities", "group": "Goods CPI", "icon": "cpi", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "durables", "seriesId": "CUSR0000SAD", "label": "CPI durables", "group": "Goods CPI", "icon": "market", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "nondurables", "seriesId": "CUSR0000SAN", "label": "CPI nondurables", "group": "Goods CPI", "icon": "basket", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "commodities_less_food_energy", "seriesId": "CUSR0000SACL1E", "label": "Commodities ex food/energy", "group": "Core goods", "icon": "source", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "new_vehicles", "seriesId": "CUSR0000SETA01", "label": "New vehicles CPI", "group": "Vehicles", "icon": "market", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "used_cars", "seriesId": "CUSR0000SETA02", "label": "Used cars and trucks CPI", "group": "Vehicles", "icon": "market", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "apparel", "seriesId": "CPIAPPSL", "label": "Apparel CPI", "group": "Goods CPI", "icon": "source", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "imports", "seriesId": "IMPGS", "label": "Imports of goods & services", "group": "Imports", "icon": "market", "unit": "bil", "metric": "pct", "toneUp": "cool"},
            {"key": "export_import", "seriesId": "EXPGS", "label": "Exports of goods & services", "group": "Trade", "icon": "growth", "unit": "bil", "metric": "pct", "toneUp": "cool"},
        ],
        "federalRegisterQuery": "tariff import duty trade",
    },
    "shelter-rent-oer-pressure": {
        "source": "FRED CSV / BLS shelter CPI",
        "sourceUrl": "https://fred.stlouisfed.org/",
        "signalHot": "SHELTER CPI STICKY",
        "signalCool": "SHELTER DISINFLATION",
        "signalNeutral": "SHELTER WATCH",
        "linkedMarketCategories": ["cpi", "fed"],
        "series": [
            {"key": "rent", "seriesId": "CUSR0000SEHA", "label": "Rent of primary residence", "group": "Rent", "icon": "home", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "oer", "seriesId": "CUSR0000SEHC", "label": "Owners equivalent rent", "group": "OER", "icon": "home", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "shelter", "seriesId": "CUSR0000SAH1", "label": "Shelter CPI", "group": "Shelter", "icon": "cpi", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "home_prices", "seriesId": "CSUSHPINSA", "label": "Case-Shiller home prices", "group": "Housing", "icon": "market", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "rent_nsa", "seriesId": "CUUR0000SEHA", "label": "Rent of residence NSA", "group": "Rent", "icon": "home", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "oer_nsa", "seriesId": "CUUR0000SEHC", "label": "Owners equivalent rent NSA", "group": "OER", "icon": "home", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "us_house_price", "seriesId": "USSTHPI", "label": "US house price index", "group": "Housing", "icon": "market", "unit": "idx", "metric": "pct", "toneUp": "hot"},
        ],
    },
    "labor-wage-services-pressure": {
        "source": "FRED CSV / BLS labor indicators",
        "sourceUrl": "https://fred.stlouisfed.org/",
        "signalHot": "WAGE / SERVICES HOT",
        "signalCool": "LABOR COOLING",
        "signalNeutral": "LABOR MIXED",
        "linkedMarketCategories": ["labor", "cpi", "fed"],
        "series": [
            {"key": "payrolls", "seriesId": "PAYEMS", "label": "Nonfarm payrolls", "group": "Jobs", "icon": "labor", "unit": "k", "metric": "delta", "toneUp": "hot"},
            {"key": "unrate", "seriesId": "UNRATE", "label": "Unemployment rate", "group": "Slack", "icon": "labor", "unit": "%", "metric": "level", "toneUp": "cool"},
            {"key": "wages", "seriesId": "CES0500000003", "label": "Avg hourly earnings", "group": "Wages", "icon": "fed", "unit": "$", "metric": "pct", "toneUp": "hot"},
            {"key": "claims", "seriesId": "ICSA", "label": "Initial jobless claims", "group": "Claims", "icon": "source", "unit": "k", "metric": "delta", "toneUp": "cool"},
            {"key": "continuing_claims", "seriesId": "CCSA", "label": "Continuing jobless claims", "group": "Claims", "icon": "source", "unit": "k", "metric": "delta", "toneUp": "cool"},
            {"key": "openings", "seriesId": "JTSJOL", "label": "Job openings", "group": "JOLTS", "icon": "market", "unit": "k", "metric": "delta", "toneUp": "hot"},
            {"key": "quits_rate", "seriesId": "JTSQUR", "label": "Quits rate", "group": "JOLTS", "icon": "market", "unit": "%", "metric": "level", "toneUp": "hot"},
            {"key": "participation", "seriesId": "CIVPART", "label": "Labor force participation", "group": "Slack", "icon": "labor", "unit": "%", "metric": "level", "toneUp": "cool"},
            {"key": "employment_ratio", "seriesId": "EMRATIO", "label": "Employment-population ratio", "group": "Slack", "icon": "labor", "unit": "%", "metric": "level", "toneUp": "hot"},
            {"key": "u6", "seriesId": "U6RATE", "label": "U-6 unemployment rate", "group": "Slack", "icon": "labor", "unit": "%", "metric": "level", "toneUp": "cool"},
            {"key": "weekly_hours", "seriesId": "AWHAETP", "label": "Avg weekly hours", "group": "Wages", "icon": "fed", "unit": "hrs", "metric": "delta", "toneUp": "hot"},
            {"key": "nonfarm_weekly_hours", "seriesId": "AWHNONAG", "label": "Nonfarm weekly hours", "group": "Wages", "icon": "fed", "unit": "hrs", "metric": "delta", "toneUp": "hot"},
            {"key": "services_cpi", "seriesId": "CUSR0000SAS", "label": "Services CPI", "group": "Services CPI", "icon": "cpi", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "medical_services", "seriesId": "CUSR0000SAM2", "label": "Medical care services CPI", "group": "Services CPI", "icon": "cpi", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "transport_services", "seriesId": "CUSR0000SAS4", "label": "Transportation services CPI", "group": "Services CPI", "icon": "cpi", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "professional_services", "seriesId": "CUSR0000SEMC", "label": "Professional services CPI", "group": "Services CPI", "icon": "cpi", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "hospital_services", "seriesId": "CUSR0000SEMD", "label": "Hospital services CPI", "group": "Services CPI", "icon": "cpi", "unit": "idx", "metric": "pct", "toneUp": "hot"},
        ],
    },
    "growth-demand-recession-tracker": {
        "source": "FRED CSV / growth and curve signals",
        "sourceUrl": "https://fred.stlouisfed.org/",
        "signalHot": "DEMAND STILL FIRM",
        "signalCool": "RECESSION PRESSURE",
        "signalNeutral": "GROWTH MIXED",
        "linkedMarketCategories": ["growth", "fed", "cpi"],
        "series": [
            {"key": "retail", "seriesId": "RSAFS", "label": "Retail sales", "group": "Demand", "icon": "basket", "unit": "mil", "metric": "pct", "toneUp": "hot"},
            {"key": "pce", "seriesId": "PCE", "label": "Personal consumption", "group": "Demand", "icon": "cpi", "unit": "bil", "metric": "pct", "toneUp": "hot"},
            {"key": "industrial", "seriesId": "INDPRO", "label": "Industrial production", "group": "Output", "icon": "growth", "unit": "idx", "metric": "pct", "toneUp": "hot"},
            {"key": "gdp", "seriesId": "GDPC1", "label": "Real GDP", "group": "GDP", "icon": "growth", "unit": "bil", "metric": "pct", "toneUp": "hot"},
            {"key": "curve", "seriesId": "T10Y2Y", "label": "10Y minus 2Y Treasury", "group": "Curve", "icon": "rates", "unit": "pp", "metric": "level", "toneUp": "hot"},
            {"key": "real_pce", "seriesId": "PCEC96", "label": "Real PCE", "group": "Demand", "icon": "cpi", "unit": "bil", "metric": "pct", "toneUp": "hot"},
            {"key": "real_income", "seriesId": "DSPIC96", "label": "Real disposable income", "group": "Income", "icon": "growth", "unit": "bil", "metric": "pct", "toneUp": "hot"},
            {"key": "housing_starts", "seriesId": "HOUST", "label": "Housing starts", "group": "Housing", "icon": "home", "unit": "k", "metric": "pct", "toneUp": "hot"},
            {"key": "building_permits", "seriesId": "PERMIT", "label": "Building permits", "group": "Housing", "icon": "home", "unit": "k", "metric": "pct", "toneUp": "hot"},
            {"key": "consumer_sentiment", "seriesId": "UMCSENT", "label": "Consumer sentiment", "group": "Demand", "icon": "market", "unit": "idx", "metric": "delta", "toneUp": "hot"},
        ],
    },
    "fed-rates-polymarket-gap": {
        "source": "FRED CSV / Fed and Treasury rates",
        "sourceUrl": "https://fred.stlouisfed.org/",
        "signalHot": "RATES HAWKISH GAP",
        "signalCool": "CUT PATH EASING",
        "signalNeutral": "FED GAP WATCH",
        "linkedMarketCategories": ["fed", "cpi", "growth"],
        "series": [
            {"key": "dff", "seriesId": "DFF", "label": "Effective Fed funds", "group": "Fed", "icon": "fed", "unit": "%", "metric": "level", "toneUp": "hot"},
            {"key": "sofr", "seriesId": "SOFR", "label": "SOFR", "group": "Money", "icon": "rates", "unit": "%", "metric": "level", "toneUp": "hot"},
            {"key": "two_year", "seriesId": "DGS2", "label": "2Y Treasury", "group": "Front-end", "icon": "rates", "unit": "%", "metric": "level", "toneUp": "hot"},
            {"key": "three_month", "seriesId": "DGS3MO", "label": "3M Treasury", "group": "Front-end", "icon": "rates", "unit": "%", "metric": "level", "toneUp": "hot"},
            {"key": "five_year", "seriesId": "DGS5", "label": "5Y Treasury", "group": "Curve", "icon": "rates", "unit": "%", "metric": "level", "toneUp": "hot"},
            {"key": "ten_year", "seriesId": "DGS10", "label": "10Y Treasury", "group": "Long-end", "icon": "market", "unit": "%", "metric": "level", "toneUp": "hot"},
            {"key": "thirty_year", "seriesId": "DGS30", "label": "30Y Treasury", "group": "Long-end", "icon": "market", "unit": "%", "metric": "level", "toneUp": "hot"},
            {"key": "curve", "seriesId": "T10Y2Y", "label": "10Y / 2Y curve", "group": "Curve", "icon": "growth", "unit": "pp", "metric": "level", "toneUp": "cool"},
            {"key": "target_upper", "seriesId": "DFEDTARU", "label": "Fed target upper bound", "group": "Fed", "icon": "fed", "unit": "%", "metric": "level", "toneUp": "hot"},
            {"key": "target_lower", "seriesId": "DFEDTARL", "label": "Fed target lower bound", "group": "Fed", "icon": "fed", "unit": "%", "metric": "level", "toneUp": "hot"},
        ],
    },
}


@dataclass(frozen=True)
class MacroCpiPanelsDependencies:
    settings: Any
    application: Any
    http_text_get: Callable[..., str]
    http_json_get: Callable[..., Any]
    utc_now_iso: Callable[..., Any] | None
    snapshot_store: Any
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> MacroCpiPanelsDependencies:
        return cls(
            settings=resolve_service_value(context, "SETTINGS"),
            application=resolve_optional_service_value(context, "app"),
            http_text_get=resolve_service_callable(
                context,
                "http_text_get",
            ),
            http_json_get=resolve_service_callable(
                context,
                "http_json_get",
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


MacroCpiPanelsContext = Mapping[str, Any] | MacroCpiPanelsDependencies


def _dependencies(
    context: MacroCpiPanelsContext,
) -> MacroCpiPanelsDependencies:
    if isinstance(context, MacroCpiPanelsDependencies):
        return context
    return MacroCpiPanelsDependencies.from_context(context)


def _utc_now_iso(dependencies: MacroCpiPanelsDependencies) -> str:
    if dependencies.utc_now_iso is not None:
        return str(dependencies.utc_now_iso())
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _float(value: Any) -> Optional[float]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None


def _snapshot_namespace(panel_id: str) -> str:
    return f"{SNAPSHOT_NAMESPACE_PREFIX}{panel_id}"


def ttl_seconds(ctx: MacroCpiPanelsContext) -> int:
    dependencies = _dependencies(ctx)
    return max(
        1800,
        int(
            getattr(
                dependencies.settings,
                "macro_cpi_panel_ttl_seconds",
                DEFAULT_TTL_SECONDS,
            )
            or DEFAULT_TTL_SECONDS
        ),
    )


def _fred_url(
    dependencies: MacroCpiPanelsDependencies,
    series_id: str,
) -> str:
    settings = dependencies.settings
    template = getattr(
        settings,
        "finance_fred_csv_url_template",
        getattr(settings, "food_basket_fred_csv_url_template", "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"),
    )
    url = str(template).format(series_id=series_id)
    lookback_years = int(getattr(settings, "fred_csv_lookback_years", FRED_CSV_LOOKBACK_YEARS) or FRED_CSV_LOOKBACK_YEARS)
    start_date = f"{max(1900, datetime.now(timezone.utc).year - lookback_years)}-01-01"
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("cosd", start_date)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _fred_row_date(row: Dict[str, Any]) -> str:
    return str(row.get("observation_date") or row.get("DATE") or row.get("date") or "").strip()


def _fetch_fred_series(
    ctx: MacroCpiPanelsContext,
    spec: Dict[str, Any],
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    series_id = spec["seriesId"]
    url = _fred_url(dependencies, series_id)
    text = dependencies.http_text_get(
        url,
        timeout=15,
        headers={"User-Agent": "polydata-macro-cpi-panels/1.0"},
    )
    rows: List[Dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(str(text or "")))
    for row in reader:
        value = _float(row.get(series_id))
        date = _fred_row_date(row)
        if value is None or not date:
            continue
        rows.append({"date": date, "value": value})
    rows.sort(key=lambda item: item["date"])
    if len(rows) < 2:
        raise ValueError(f"not enough observations for {series_id}")
    latest = rows[-1]
    prev = rows[-2]
    year_ago = rows[-13] if len(rows) >= 13 else rows[0]
    change = latest["value"] - prev["value"]
    change_pct = None
    if prev["value"]:
        change_pct = (latest["value"] / prev["value"] - 1.0) * 100.0
    yoy_pct = None
    if year_ago["value"]:
        yoy_pct = (latest["value"] / year_ago["value"] - 1.0) * 100.0
    tone = _series_tone(spec, change, change_pct, latest["value"])
    return {
        "key": spec["key"],
        "seriesId": series_id,
        "label": spec["label"],
        "group": spec.get("group") or spec["key"],
        "icon": spec.get("icon") or "source",
        "metric": spec.get("metric") or "level",
        "unit": spec.get("unit"),
        "date": latest["date"],
        "value": round(latest["value"], 3),
        "change": round(change, 3),
        "changePct": round(change_pct, 2) if change_pct is not None else None,
        "yoyPct": round(yoy_pct, 2) if yoy_pct is not None else None,
        "tone": tone,
        "source": FRED_SOURCE,
        "sourceUrl": url,
    }


def _series_tone(spec: Dict[str, Any], change: float, change_pct: Optional[float], latest: float) -> str:
    metric = str(spec.get("metric") or "level")
    driver = change if metric in {"delta", "level"} else (change_pct if change_pct is not None else change)
    if metric == "level" and str(spec.get("key")) in {"gscpi", "curve"}:
        driver = latest
    if abs(driver) < 0.05:
        return "neutral"
    up_tone = str(spec.get("toneUp") or "hot")
    if driver > 0:
        return up_tone if up_tone in {"hot", "cool", "watch", "neutral"} else "hot"
    return "cool" if up_tone == "hot" else "hot"


def _fetch_federal_register_items(
    ctx: MacroCpiPanelsContext,
    panel_id: str,
    config: Dict[str, Any],
    limit: int,
) -> List[Dict[str, Any]]:
    dependencies = _dependencies(ctx)
    query = config.get("federalRegisterQuery")
    if not query:
        return []
    url = getattr(
        dependencies.settings,
        "geo_shock_federal_register_api_url",
        "https://www.federalregister.gov/api/v1/documents.json",
    )
    payload = dependencies.http_json_get(
        url,
        params={"conditions[term]": query, "order": "newest", "per_page": min(5, max(1, limit))},
        timeout=12,
        headers={"Accept": "application/json", "User-Agent": "polydata-macro-cpi-panels/1.0"},
    )
    docs = (payload or {}).get("results") if isinstance(payload, dict) else []
    items: List[Dict[str, Any]] = []
    for index, doc in enumerate(docs or []):
        if not isinstance(doc, dict):
            continue
        title = str(doc.get("title") or "Federal Register trade policy").strip()
        items.append(
            {
                "key": f"federal-register-{index}",
                "seriesId": None,
                "label": title,
                "group": "Policy",
                "icon": "policy",
                "metric": "event",
                "unit": None,
                "date": doc.get("publication_date"),
                "value": None,
                "change": None,
                "changePct": None,
                "yoyPct": None,
                "tone": "watch",
                "source": "Federal Register",
                "sourceUrl": doc.get("html_url") or doc.get("pdf_url") or url,
            }
        )
    return items


def _summary(panel_id: str, config: Dict[str, Any], items: List[Dict[str, Any]], sources: Dict[str, str]) -> Dict[str, Any]:
    scored = [item for item in items if item.get("tone") in {"hot", "cool", "watch", "neutral"}]
    hot = sum(1 for item in scored if item.get("tone") == "hot")
    cool = sum(1 for item in scored if item.get("tone") == "cool")
    watch = sum(1 for item in scored if item.get("tone") == "watch")
    if hot > cool:
        signal = config["signalHot"]
        bias = "hot"
    elif cool > hot:
        signal = config["signalCool"]
        bias = "cool"
    elif watch:
        signal = config["signalNeutral"]
        bias = "watch"
    else:
        signal = config["signalNeutral"]
        bias = "neutral"
    top = None
    numeric_items = [item for item in items if _float(item.get("changePct")) is not None or _float(item.get("change")) is not None]
    if numeric_items:
        top = max(numeric_items, key=lambda item: abs(_float(item.get("changePct")) if _float(item.get("changePct")) is not None else _float(item.get("change")) or 0.0))
    return {
        "signal": signal,
        "bias": bias,
        "hotCount": hot,
        "coolCount": cool,
        "watchCount": watch,
        "coverage": sum(1 for value in sources.values() if value == "ok"),
        "sourceCount": len(sources),
        "topMover": top,
        "linkedMarketCategories": config.get("linkedMarketCategories") or [],
        "panelId": panel_id,
    }


def build_macro_cpi_panel_payload(
    ctx: MacroCpiPanelsContext,
    panel_id: str,
    limit: int = DEFAULT_ITEM_LIMIT,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    config = PANEL_CONFIGS[panel_id]
    items: List[Dict[str, Any]] = []
    sources: Dict[str, str] = {}
    for spec in config.get("series") or []:
        key = str(spec.get("key") or spec.get("seriesId"))
        try:
            items.append(_fetch_fred_series(ctx, spec))
            sources[key] = "ok"
        except Exception as exc:
            sources[key] = "error"
            logger = getattr(dependencies.application, "logger", None)
            if logger is not None:
                logger.exception("macro cpi panel source failed panel=%s source=%s error=%s", panel_id, key, exc)
    if config.get("federalRegisterQuery"):
        try:
            policy_items = _fetch_federal_register_items(ctx, panel_id, config, limit)
            items = items[:3] + policy_items + items[3:]
            sources["federal_register"] = "ok" if policy_items else "empty"
        except Exception as exc:
            sources["federal_register"] = "error"
            logger = getattr(dependencies.application, "logger", None)
            if logger is not None:
                logger.exception("macro cpi panel federal register failed panel=%s error=%s", panel_id, exc)
    status = "ok" if sources and all(value in {"ok", "empty"} for value in sources.values()) else ("degraded" if items else "warming")
    limited_items = items[: max(1, min(int(limit or DEFAULT_ITEM_LIMIT), MAX_ITEM_LIMIT))]
    return {
        "generatedAt": _utc_now_iso(dependencies),
        "source": config.get("source") or FRED_SOURCE,
        "sourceUrl": config.get("sourceUrl") or "https://fred.stlouisfed.org/",
        "status": status,
        "sources": sources,
        "summary": _summary(panel_id, config, limited_items, sources),
        "items": limited_items,
    }


def _empty(
    dependencies: MacroCpiPanelsDependencies,
    panel_id: str,
    status: str = "warming",
) -> Dict[str, Any]:
    config = PANEL_CONFIGS[panel_id]
    return {
        "generatedAt": _utc_now_iso(dependencies),
        "source": config.get("source") or FRED_SOURCE,
        "sourceUrl": config.get("sourceUrl") or "https://fred.stlouisfed.org/",
        "status": status,
        "sources": {},
        "summary": _summary(panel_id, config, [], {}),
        "items": [],
    }


def normalize_macro_cpi_panel_payload(
    payload: Any,
    *,
    ctx: MacroCpiPanelsContext,
    panel_id: str,
    limit: int = DEFAULT_ITEM_LIMIT,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    if not isinstance(payload, dict):
        return _empty(dependencies, panel_id, "invalid")
    result = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    config = PANEL_CONFIGS[panel_id]
    items = [item for item in (result.get("items") or []) if isinstance(item, dict)]
    result["items"] = items[: max(1, min(int(limit or DEFAULT_ITEM_LIMIT), MAX_ITEM_LIMIT))]
    result["summary"] = result.get("summary") if isinstance(result.get("summary"), dict) else _summary(panel_id, config, result["items"], result.get("sources") or {})
    result["generatedAt"] = str(
        result.get("generatedAt")
        or _utc_now_iso(dependencies)
    )
    result["status"] = str(result.get("status") or ("ok" if result["items"] else "warming"))
    result["source"] = str(result.get("source") or config.get("source") or FRED_SOURCE)
    result["sourceUrl"] = str(result.get("sourceUrl") or config.get("sourceUrl") or "https://fred.stlouisfed.org/")
    result["sources"] = result.get("sources") if isinstance(result.get("sources"), dict) else {}
    return result


def _with_mode(payload: Dict[str, Any], mode: str) -> Dict[str, Any]:
    return {**payload, "cacheMode": mode}


def _read_seeded(
    dependencies: MacroCpiPanelsDependencies,
    panel_id: str,
) -> Optional[Dict[str, Any]]:
    namespace = _snapshot_namespace(panel_id)
    if dependencies.get_cached_json is not None:
        payload = dependencies.get_cached_json(namespace, CACHE_KEY)
        if isinstance(payload, dict):
            return _with_mode(payload, "redis-seed")
    store = dependencies.snapshot_store
    if store is not None:
        payload = store.get(namespace, CACHE_KEY)
        if isinstance(payload, dict):
            return _with_mode(payload, "sqlite-seed")
        stale = store.get_stale(namespace, CACHE_KEY)
        if isinstance(stale, dict):
            return _with_mode(stale, "stale-seed")
    return None


def _store_live(
    dependencies: MacroCpiPanelsDependencies,
    panel_id: str,
    payload: Dict[str, Any],
    *,
    ttl: int,
) -> None:
    namespace = _snapshot_namespace(panel_id)
    if dependencies.snapshot_store is not None:
        dependencies.snapshot_store.set(
            namespace,
            CACHE_KEY,
            payload,
            ttl,
        )
    if dependencies.set_cached_json is not None:
        dependencies.set_cached_json(
            namespace,
            CACHE_KEY,
            payload,
            ttl,
        )


def get_macro_cpi_panel_snapshot(
    ctx: MacroCpiPanelsContext,
    panel_id: str,
    limit: int = DEFAULT_ITEM_LIMIT,
    *,
    allow_live_build: bool = False,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    ttl = ttl_seconds(dependencies)
    seeded = _read_seeded(dependencies, panel_id)
    if seeded is not None:
        return normalize_macro_cpi_panel_payload(
            seeded,
            ctx=dependencies,
            panel_id=panel_id,
            limit=limit,
        )
    if not allow_live_build:
        return normalize_macro_cpi_panel_payload(
            _with_mode(
                _empty(dependencies, panel_id, "warming"),
                "seed-miss",
            ),
            ctx=dependencies,
            panel_id=panel_id,
            limit=limit,
        )
    payload = _with_mode(build_macro_cpi_panel_payload(ctx, panel_id, limit=limit), "live-build")
    if payload.get("items"):
        _store_live(
            dependencies,
            panel_id,
            payload,
            ttl=ttl,
        )
    return normalize_macro_cpi_panel_payload(
        payload,
        ctx=dependencies,
        panel_id=panel_id,
        limit=limit,
    )


def get_supply_tariff_import_watch_snapshot(
    ctx: MacroCpiPanelsContext,
    limit: int = DEFAULT_ITEM_LIMIT,
    *,
    allow_live_build: bool = False,
) -> Dict[str, Any]:
    return get_macro_cpi_panel_snapshot(ctx, "supply-tariff-import-watch", limit=limit, allow_live_build=allow_live_build)


def get_shelter_rent_oer_pressure_snapshot(
    ctx: MacroCpiPanelsContext,
    limit: int = DEFAULT_ITEM_LIMIT,
    *,
    allow_live_build: bool = False,
) -> Dict[str, Any]:
    return get_macro_cpi_panel_snapshot(ctx, "shelter-rent-oer-pressure", limit=limit, allow_live_build=allow_live_build)


def get_labor_wage_services_pressure_snapshot(
    ctx: MacroCpiPanelsContext,
    limit: int = DEFAULT_ITEM_LIMIT,
    *,
    allow_live_build: bool = False,
) -> Dict[str, Any]:
    return get_macro_cpi_panel_snapshot(ctx, "labor-wage-services-pressure", limit=limit, allow_live_build=allow_live_build)


def get_growth_demand_recession_tracker_snapshot(
    ctx: MacroCpiPanelsContext,
    limit: int = DEFAULT_ITEM_LIMIT,
    *,
    allow_live_build: bool = False,
) -> Dict[str, Any]:
    return get_macro_cpi_panel_snapshot(ctx, "growth-demand-recession-tracker", limit=limit, allow_live_build=allow_live_build)


def get_fed_rates_polymarket_gap_snapshot(
    ctx: MacroCpiPanelsContext,
    limit: int = DEFAULT_ITEM_LIMIT,
    *,
    allow_live_build: bool = False,
) -> Dict[str, Any]:
    return get_macro_cpi_panel_snapshot(ctx, "fed-rates-polymarket-gap", limit=limit, allow_live_build=allow_live_build)
