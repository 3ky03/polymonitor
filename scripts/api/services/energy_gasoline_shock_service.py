from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
    resolve_service_value,
)


ENERGY_SHOCK_SNAPSHOT_NAMESPACE = "snapshot:macro:energy-gasoline-shock"
ENERGY_SHOCK_CACHE_KEY = "panel-v1"
DEFAULT_ITEM_LIMIT = 6


@dataclass(frozen=True)
class EnergyGasolineShockDependencies:
    settings: Any
    application: Any
    http_bytes_get: Callable[..., bytes]
    xlrd: Any
    utc_now_iso: Callable[..., Any] | None
    snapshot_store: Any
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> EnergyGasolineShockDependencies:
        return cls(
            settings=resolve_service_value(context, "SETTINGS"),
            application=resolve_optional_service_value(context, "app"),
            http_bytes_get=resolve_service_callable(
                context,
                "http_bytes_get",
            ),
            xlrd=resolve_optional_service_value(context, "xlrd"),
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


EnergyGasolineShockContext = Mapping[str, Any] | EnergyGasolineShockDependencies


def _dependencies(
    context: EnergyGasolineShockContext,
) -> EnergyGasolineShockDependencies:
    if isinstance(context, EnergyGasolineShockDependencies):
        return context
    return EnergyGasolineShockDependencies.from_context(context)


def _utc_now_iso(dependencies: EnergyGasolineShockDependencies) -> str:
    if dependencies.utc_now_iso is not None:
        return str(dependencies.utc_now_iso())
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _parse_eia_xls(
    ctx: EnergyGasolineShockContext,
    *,
    url: str,
    key: str,
    label: str,
    unit: str,
    cadence: str,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    xlrd = dependencies.xlrd
    if xlrd is None:
        raise RuntimeError("xlrd is required for EIA XLS parsing")
    content = dependencies.http_bytes_get(
        url,
        timeout=18,
        headers={"User-Agent": "polydata-energy-shock/1.0"},
    )
    workbook = xlrd.open_workbook(file_contents=content)
    sheet = workbook.sheet_by_index(1 if workbook.nsheets > 1 else 0)
    rows: List[Dict[str, Any]] = []
    for row_index in range(3, sheet.nrows):
        raw_date = sheet.cell_value(row_index, 0)
        raw_value = _float(sheet.cell_value(row_index, 1))
        if raw_value is None:
            continue
        try:
            date_value = xlrd.xldate_as_datetime(raw_date, workbook.datemode).date().isoformat()
        except Exception:
            continue
        rows.append({"date": date_value, "value": raw_value})
    rows.sort(key=lambda row: row["date"])
    latest = rows[-1] if rows else None
    previous = rows[-2] if len(rows) >= 2 else None
    week_ago = rows[-6] if len(rows) >= 6 and cadence == "daily" else (rows[-2] if len(rows) >= 2 else None)
    change_1 = (latest["value"] - previous["value"]) if latest and previous else None
    change_w = (latest["value"] - week_ago["value"]) if latest and week_ago else None
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "cadence": cadence,
        "date": latest.get("date") if latest else None,
        "value": round(float(latest["value"]), 3) if latest else None,
        "change1": round(float(change_1), 3) if change_1 is not None else None,
        "changeWeek": round(float(change_w), 3) if change_w is not None else None,
        "source": "EIA public XLS",
        "sourceUrl": url,
    }


def _score_signal(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_key = {item.get("key"): item for item in items}
    gasoline = by_key.get("gasoline") or {}
    wti = by_key.get("wti") or {}
    diesel = by_key.get("diesel") or {}
    gas_w = _float(gasoline.get("changeWeek")) or 0.0
    wti_w = _float(wti.get("changeWeek")) or 0.0
    diesel_w = _float(diesel.get("changeWeek")) or 0.0
    impulse = gas_w * 0.035 + wti_w * 0.002 + diesel_w * 0.015
    if impulse >= 0.03:
        signal = "HEADLINE CPI HOTTER"
        bias = "hot"
    elif impulse <= -0.03:
        signal = "HEADLINE COOLING"
        bias = "cool"
    else:
        signal = "ENERGY NEUTRAL"
        bias = "neutral"
    return {
        "signal": signal,
        "bias": bias,
        "headlineImpulsePp": round(impulse, 3),
        "linkedMarkets": ["CPI headline", "oil", "gasoline", "Fed"],
    }


def build_energy_gasoline_shock_payload(
    ctx: EnergyGasolineShockContext,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    settings = dependencies.settings
    sources: Dict[str, str] = {}
    items: List[Dict[str, Any]] = []
    jobs = (
        ("wti", settings.energy_shock_wti_xls_url, "WTI crude", "$/bbl", "daily"),
        ("gasoline", settings.energy_shock_gasoline_xls_url, "US gasoline", "$/gal", "weekly"),
        ("diesel", settings.energy_shock_diesel_xls_url, "US diesel", "$/gal", "weekly"),
    )
    for key, url, label, unit, cadence in jobs:
        try:
            items.append(_parse_eia_xls(ctx, url=url, key=key, label=label, unit=unit, cadence=cadence))
            sources[key] = "ok"
        except Exception as exc:
            sources[key] = "error"
            logger = getattr(dependencies.application, "logger", None)
            if logger is not None:
                logger.exception("energy gasoline shock source failed source=%s error=%s", key, exc)
    summary = _score_signal(items)
    status = "ok" if len(items) == 3 else ("degraded" if items else "warming")
    return {
        "generatedAt": _utc_now_iso(dependencies),
        "source": "EIA public petroleum XLS",
        "sourceUrl": settings.energy_shock_source_url,
        "status": status,
        "sources": sources,
        "summary": summary,
        "items": items,
    }


def _empty_payload(
    dependencies: EnergyGasolineShockDependencies,
    *,
    status: str = "warming",
) -> Dict[str, Any]:
    return {
        "generatedAt": _utc_now_iso(dependencies),
        "source": "EIA public petroleum XLS",
        "sourceUrl": getattr(dependencies.settings, "energy_shock_source_url", ""),
        "status": status,
        "sources": {},
        "summary": {"signal": "ENERGY WARMING", "bias": "unknown", "headlineImpulsePp": None, "linkedMarkets": []},
        "items": [],
    }


def normalize_energy_gasoline_shock_payload(
    payload: Any,
    *,
    ctx: EnergyGasolineShockContext,
    limit: int = DEFAULT_ITEM_LIMIT,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    if not isinstance(payload, dict):
        return _empty_payload(dependencies, status="invalid")
    result = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    items = [item for item in (result.get("items") or []) if isinstance(item, dict)]
    result["items"] = items[: max(1, min(int(limit or DEFAULT_ITEM_LIMIT), 12))]
    result["summary"] = result.get("summary") if isinstance(result.get("summary"), dict) else _score_signal(items)
    result["generatedAt"] = str(result.get("generatedAt") or _utc_now_iso(dependencies))
    result["status"] = str(result.get("status") or ("ok" if items else "warming"))
    result["source"] = str(result.get("source") or "EIA public petroleum XLS")
    result["sourceUrl"] = str(
        result.get("sourceUrl")
        or getattr(dependencies.settings, "energy_shock_source_url", "")
    )
    return result


def _with_cache_mode(payload: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    return {**payload, "cacheMode": cache_mode}


def _read_seeded_snapshot(
    dependencies: EnergyGasolineShockDependencies,
) -> Optional[Dict[str, Any]]:
    if dependencies.get_cached_json is not None:
        payload = dependencies.get_cached_json(
            ENERGY_SHOCK_SNAPSHOT_NAMESPACE,
            ENERGY_SHOCK_CACHE_KEY,
        )
        if isinstance(payload, dict):
            return _with_cache_mode(payload, "redis-seed")
    store = dependencies.snapshot_store
    if store is None:
        return None
    payload = store.get(ENERGY_SHOCK_SNAPSHOT_NAMESPACE, ENERGY_SHOCK_CACHE_KEY)
    if isinstance(payload, dict):
        return _with_cache_mode(payload, "sqlite-seed")
    stale = store.get_stale(ENERGY_SHOCK_SNAPSHOT_NAMESPACE, ENERGY_SHOCK_CACHE_KEY)
    if isinstance(stale, dict):
        return _with_cache_mode(stale, "stale-seed")
    return None


def _store_live(
    dependencies: EnergyGasolineShockDependencies,
    payload: Dict[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    store = dependencies.snapshot_store
    if store is not None:
        store.set(ENERGY_SHOCK_SNAPSHOT_NAMESPACE, ENERGY_SHOCK_CACHE_KEY, payload, ttl_seconds)
    if dependencies.set_cached_json is not None:
        dependencies.set_cached_json(
            ENERGY_SHOCK_SNAPSHOT_NAMESPACE,
            ENERGY_SHOCK_CACHE_KEY,
            payload,
            ttl_seconds,
        )


def get_energy_gasoline_shock_snapshot(
    ctx: EnergyGasolineShockContext,
    limit: int = DEFAULT_ITEM_LIMIT,
    *,
    allow_live_build: bool = True,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    ttl_seconds = max(
        1800,
        int(
            getattr(
                dependencies.settings,
                "energy_shock_ttl_seconds",
                21600,
            )
            or 21600
        ),
    )
    seeded = _read_seeded_snapshot(dependencies)
    if seeded is not None:
        return normalize_energy_gasoline_shock_payload(
            seeded,
            ctx=dependencies,
            limit=limit,
        )
    if not allow_live_build:
        return normalize_energy_gasoline_shock_payload(
            {
                **_empty_payload(dependencies),
                "cacheMode": "seed-miss",
            },
            ctx=dependencies,
            limit=limit,
        )
    payload = _with_cache_mode(build_energy_gasoline_shock_payload(ctx), "live-build")
    if payload.get("items"):
        _store_live(dependencies, payload, ttl_seconds=ttl_seconds)
    return normalize_energy_gasoline_shock_payload(
        payload,
        ctx=dependencies,
        limit=limit,
    )
