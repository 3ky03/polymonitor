from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timezone
from typing import Any, Dict

from ..contracts import ProviderResult, SEVERITY_MAPPING_VERSION
from ..normalize import finite_number


PROVIDER_KEY = "climate-anomaly"
DEFAULT_URL_TEMPLATE = "https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/global/mapping/tavg-{year_month}/data.json?raw=1"
SOURCE_URL = "https://www.ncei.noaa.gov/access/monitoring/climate-at-a-glance/global/mapping"
BASELINE_PERIOD = "1991-2020"
CALCULATION_VERSION = "NOAA-NCEI-CAG-global-mapping.v1"
MIN_ABSOLUTE_ANOMALY_C = 2.0
MAX_EVENTS = 280


def _months_back(now: datetime, count: int = 4) -> list[str]:
    result: list[str] = []
    year, month = now.year, now.month
    for _ in range(count):
        result.append(f"{year:04d}{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return result


def _month_end(year_month: str) -> str:
    year, month = int(year_month[:4]), int(year_month[4:])
    return datetime(year, month, monthrange(year, month)[1], 23, 59, 59, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def fetch(
    http_json_get,
    *,
    url_template: str = DEFAULT_URL_TEMPLATE,
    limit: int = MAX_EVENTS,
    now: datetime | None = None,
) -> ProviderResult:
    reference = now or datetime.now(timezone.utc)
    payload: Dict[str, Any] | None = None
    year_month = ""
    last_error: Exception | None = None
    for candidate in _months_back(reference):
        try:
            raw = http_json_get(
                url_template.format(year_month=candidate),
                timeout=10,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "polymonitor-world-event-map/1.0 (https://polymonitor.club)",
                },
            )
        except Exception as exc:
            last_error = exc
            continue
        if isinstance(raw, dict) and raw:
            payload = raw
            year_month = candidate
            break
    if payload is None:
        raise ValueError(f"ncei-global-mapping-unavailable:{last_error.__class__.__name__ if last_error else 'empty'}")

    observed_at = _month_end(year_month)
    candidates: list[tuple[float, Dict[str, Any]]] = []
    for grid_id, value in payload.items():
        if not isinstance(value, dict):
            continue
        coordinates = value.get("coordinates") if isinstance(value.get("coordinates"), dict) else {}
        lat = finite_number(coordinates.get("latitude"))
        lon = finite_number(coordinates.get("longitude"))
        anomaly = finite_number(value.get("anomaly"))
        if lat is None or lon is None or anomaly is None or abs(anomaly) < MIN_ABSOLUTE_ANOMALY_C:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        absolute = abs(anomaly)
        severity = "critical" if absolute >= 4 else "warning" if absolute >= 3 else "watch"
        west, east = max(-180.0, lon - 2.5), min(180.0, lon + 2.5)
        south, north = max(-90.0, lat - 2.5), min(90.0, lat + 2.5)
        direction = "warm" if anomaly > 0 else "cold"
        event = {
            "id": f"temperature-anomaly:ncei:{year_month}:{grid_id}",
            "category": "natural-hazard",
            "title": f"Monthly {direction} temperature anomaly · {anomaly:+.2f} °C",
            "summary": f"NOAA NCEI reports a {anomaly:+.2f} °C monthly surface-temperature anomaly for this 5° grid cell relative to {BASELINE_PERIOD}.",
            "severity": severity,
            "occurredAt": observed_at,
            "updatedAt": observed_at,
            "geometry": {"type": "Polygon", "coordinates": [[
                [west, south], [east, south], [east, north], [west, north], [west, south],
            ]]},
            "locationPrecision": "region",
            "locationLabel": str(grid_id),
            "sources": [{
                "provider": "NOAA NCEI Climate at a Glance",
                "url": SOURCE_URL,
                "nativeId": f"tavg-{year_month}:{grid_id}",
                "observedAt": observed_at,
                "freshness": "monthly",
                "status": "ok",
            }],
            "limitations": [
                "A monthly gridded climate anomaly is an observation, not an active weather warning or local impact forecast.",
                "The 5° grid is unsuitable for street-level or incident-level attribution.",
            ],
            "relatedMarketIds": [],
            "properties": {
                "mapEntity": "hazard-observation",
                "observationType": "monthly-temperature-anomaly",
                "geometrySource": "ncei-5-degree-grid",
                "observed": True,
                "canonicalEventId": f"temperature-anomaly:ncei:{year_month}:{grid_id}",
                "mergeReason": "official NCEI month and grid-cell identifier",
                "sourceProvenance": [{"provider": "NOAA NCEI Climate at a Glance", "nativeEventId": f"tavg-{year_month}:{grid_id}"}],
            },
            "hazardKind": "temperature-anomaly",
            "lifecycle": "observed",
            "coverage": {
                "scope": "global",
                "label": "NOAA NCEI Climate at a Glance global 5° temperature anomaly grid",
                "isComplete": False,
                "gaps": ["Monthly products are published after the observation month and may contain unavailable grid cells."],
            },
            "severityEvidence": {
                "provider": "NOAA NCEI Climate at a Glance",
                "rawLevel": f"anomaly={anomaly:+.2f}°C",
                "mappingVersion": SEVERITY_MAPPING_VERSION,
                "reason": "Map priority is derived from absolute monthly temperature departure; it does not assert disaster impact.",
            },
            "revision": {
                "nativeEventId": f"tavg-{year_month}:{grid_id}",
                "revisionAt": observed_at,
                "replaces": [],
                "cancelled": False,
            },
            "metrics": {
                "kind": "climate-anomaly",
                "variable": "surface-temperature",
                "value": anomaly,
                "anomaly": anomaly,
                "unit": "°C",
                "baselinePeriod": BASELINE_PERIOD,
                "calculationVersion": CALCULATION_VERSION,
                "timeWindow": year_month,
                "spatialResolution": "5-degree-grid",
                "provider": "NOAA NCEI Climate at a Glance",
            },
        }
        candidates.append((absolute, event))
    candidates.sort(key=lambda item: item[0], reverse=True)
    bounded = [event for _, event in candidates[: max(1, min(MAX_EVENTS, limit))]]
    return {"events": bounded, "data_updated_at": observed_at}
