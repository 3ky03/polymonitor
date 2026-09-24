from __future__ import annotations

import csv
import io
import math
import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping
from urllib.parse import quote

from ..contracts import ProviderResult, SEVERITY_MAPPING_VERSION
from ..normalize import finite_number


PROVIDER_KEY = "firms"
DEFAULT_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
DEFAULT_SOURCE = "VIIRS_NOAA20_NRT"
SOURCE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/"
GRID_DEGREES = 0.5
MAX_AGGREGATES = 250
MAX_VIEWPORT_DETECTIONS = 1_200


def _observed_at(row: Dict[str, str]) -> str | None:
    date = str(row.get("acq_date") or "").strip()
    time_text = str(row.get("acq_time") or "").strip().zfill(4)
    if not date or len(time_text) != 4 or not time_text.isdigit():
        return None
    try:
        parsed = datetime.strptime(f"{date} {time_text}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _severity(detection_count: int, total_frp: float) -> tuple[str, Dict[str, str]]:
    if detection_count >= 100 or total_frp >= 1000:
        severity = "warning"
    elif detection_count >= 20 or total_frp >= 200:
        severity = "watch"
    else:
        severity = "info"
    return severity, {
        "provider": "NASA FIRMS",
        "rawLevel": f"detections={detection_count},frp={total_frp:.1f}MW",
        "mappingVersion": SEVERITY_MAPPING_VERSION,
        "reason": (
            "Satellite thermal-anomaly display priority is based on aggregate detection count "
            "and fire radiative power; it is not a confirmed wildfire severity."
        ),
    }


def _cluster_rows(rows: Iterable[Mapping[str, str]], source: str, limit: int) -> list[Dict[str, Any]]:
    buckets: dict[tuple[str, int, int], Dict[str, Any]] = defaultdict(lambda: {
        "count": 0,
        "lonTotal": 0.0,
        "latTotal": 0.0,
        "frpTotal": 0.0,
        "latestAt": "",
        "confidences": set(),
        "satellites": set(),
        "instruments": set(),
    })
    for row in rows:
        lon = finite_number(row.get("longitude"))
        lat = finite_number(row.get("latitude"))
        observed_at = _observed_at(row)
        if lon is None or lat is None or observed_at is None:
            continue
        if lon < -180 or lon > 180 or lat < -90 or lat > 90:
            continue
        day = observed_at[:10]
        cell_x = math.floor((lon + 180) / GRID_DEGREES)
        cell_y = math.floor((lat + 90) / GRID_DEGREES)
        bucket = buckets[(day, cell_x, cell_y)]
        bucket["count"] += 1
        bucket["lonTotal"] += lon
        bucket["latTotal"] += lat
        bucket["frpTotal"] += finite_number(row.get("frp")) or 0.0
        bucket["latestAt"] = max(str(bucket["latestAt"]), observed_at)
        for field, target in (
            ("confidence", "confidences"),
            ("satellite", "satellites"),
            ("instrument", "instruments"),
        ):
            text = str(row.get(field) or "").strip()
            if text:
                bucket[target].add(text)

    aggregates: list[Dict[str, Any]] = []
    for (day, cell_x, cell_y), bucket in buckets.items():
        count = int(bucket["count"])
        total_frp = float(bucket["frpTotal"])
        latest_at = str(bucket["latestAt"])
        lon = float(bucket["lonTotal"]) / count
        lat = float(bucket["latTotal"]) / count
        west = cell_x * GRID_DEGREES - 180
        south = cell_y * GRID_DEGREES - 90
        east = min(180.0, west + GRID_DEGREES)
        north = min(90.0, south + GRID_DEGREES)
        native_id = f"{source}:{day}:{cell_x}:{cell_y}"
        severity, evidence = _severity(count, total_frp)
        satellites = sorted(bucket["satellites"])
        instruments = sorted(bucket["instruments"])
        confidences = sorted(bucket["confidences"])
        aggregates.append({
            "id": f"fire-detection:firms:{native_id}",
            "category": "natural-hazard",
            "title": f"Satellite thermal anomaly cluster · {count} detections",
            "summary": (
                f"NASA FIRMS detected {count} thermal anomalies in a {GRID_DEGREES:g}° grid cell. "
                "This observation is not a confirmed wildfire perimeter."
            ),
            "severity": severity,
            "occurredAt": latest_at,
            "updatedAt": latest_at,
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "locationPrecision": "region",
            "locationLabel": f"Grid bbox [{west:.1f}, {south:.1f}, {east:.1f}, {north:.1f}]",
            "sources": [{
                "provider": "NASA FIRMS",
                "url": SOURCE_URL,
                "nativeId": native_id,
                "observedAt": latest_at,
                "freshness": "live",
                "status": "ok",
            }],
            "limitations": [
                "A satellite thermal anomaly is not a confirmed wildfire or fire perimeter.",
                "Cloud, smoke, sensor coverage and overpass timing can create gaps.",
                f"Global detections are aggregated into {GRID_DEGREES:g}° daily cells and capped by display priority.",
            ],
            "relatedMarketIds": [],
            "properties": {
                "mapEntity": "hazard-observation",
                "observationType": "satellite-thermal-anomaly",
                "bounds": [west, south, east, north],
                "gridDegrees": GRID_DEGREES,
                "sourceProduct": source,
                "rawDetectionDisclosure": "zoom-dependent-not-in-v1-response",
            },
            "hazardKind": "fire-detection",
            "lifecycle": "observed",
            "coverage": {
                "scope": "global",
                "label": f"NASA FIRMS {source} near-real-time thermal detections",
                "isComplete": False,
                "gaps": ["Sensor coverage, clouds and acquisition timing affect detection completeness."],
            },
            "severityEvidence": evidence,
            "revision": {
                "nativeEventId": native_id,
                "revisionAt": latest_at,
                "replaces": [],
                "cancelled": False,
            },
            "metrics": {
                "kind": "wildfire",
                "detectionCount": count,
                "fireRadiativePowerMw": round(total_frp, 2),
                "sensor": ",".join(instruments) or None,
                "satellite": ",".join(satellites) or None,
                "confidenceLabel": ",".join(confidences) or None,
            },
        })
    aggregates.sort(
        key=lambda event: (
            int((event.get("metrics") or {}).get("detectionCount") or 0),
            float((event.get("metrics") or {}).get("fireRadiativePowerMw") or 0),
            str(event.get("updatedAt") or ""),
        ),
        reverse=True,
    )
    return aggregates[: max(1, min(MAX_AGGREGATES, limit))]


def fetch(
    http_text_get,
    *,
    map_key: str,
    base_url: str = DEFAULT_BASE_URL,
    source: str = DEFAULT_SOURCE,
    limit: int = MAX_AGGREGATES,
) -> ProviderResult:
    clean_key = str(map_key or "").strip()
    if not clean_key:
        raise ValueError("firms-map-key-required")
    clean_source = str(source or DEFAULT_SOURCE).strip()
    url = f"{base_url.rstrip('/')}/{quote(clean_key, safe='')}/{quote(clean_source, safe='')}/world/1"
    text = http_text_get(
        url,
        timeout=8,
        headers={
            "Accept": "text/csv",
            "User-Agent": "polymonitor-world-event-map/1.0 (https://polymonitor.club)",
        },
    )
    reader = csv.DictReader(io.StringIO(str(text or "")))
    required = {"latitude", "longitude", "acq_date", "acq_time"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError("firms-schema-columns")
    events = _cluster_rows(reader, clean_source, limit)
    newest = max((event["updatedAt"] for event in events), default=None)
    return {"events": events, "data_updated_at": newest}


def _raw_detection_events(rows: Iterable[Mapping[str, str]], source: str, limit: int) -> list[Dict[str, Any]]:
    events: list[Dict[str, Any]] = []
    for row in rows:
        lon = finite_number(row.get("longitude"))
        lat = finite_number(row.get("latitude"))
        observed_at = _observed_at(dict(row))
        if lon is None or lat is None or observed_at is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        frp = finite_number(row.get("frp")) or 0.0
        confidence = str(row.get("confidence") or "").strip() or None
        satellite = str(row.get("satellite") or "").strip() or None
        instrument = str(row.get("instrument") or "").strip() or None
        identity = "|".join((source, observed_at, f"{lat:.5f}", f"{lon:.5f}", satellite or "", instrument or ""))
        native_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        severity = "watch" if frp >= 100 else "info"
        events.append({
            "id": f"fire-detection:firms-raw:{native_id}",
            "category": "natural-hazard",
            "title": f"Satellite thermal detection · {frp:.1f} MW FRP",
            "summary": "A source-native NASA FIRMS thermal detection in the requested viewport; it is not a confirmed wildfire perimeter.",
            "severity": severity,
            "occurredAt": observed_at,
            "updatedAt": observed_at,
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "locationPrecision": "exact",
            "locationLabel": f"{lat:.4f}, {lon:.4f}",
            "sources": [{
                "provider": "NASA FIRMS",
                "url": SOURCE_URL,
                "nativeId": native_id,
                "observedAt": observed_at,
                "freshness": "live",
                "status": "ok",
            }],
            "limitations": [
                "A satellite thermal anomaly is not a confirmed wildfire or fire perimeter.",
                "Cloud, smoke, sensor coverage and overpass timing can create gaps.",
                "Raw detections are requested only for the current high-zoom viewport and are display-capped.",
            ],
            "relatedMarketIds": [],
            "properties": {
                "mapEntity": "hazard-observation",
                "observationType": "satellite-thermal-detection",
                "sourceProduct": source,
                "rawDetection": True,
                "canonicalEventId": f"fire-detection:firms-raw:{native_id}",
                "mergeReason": "source-native FIRMS observation identifier",
                "sourceProvenance": [{"provider": "NASA FIRMS", "nativeEventId": native_id}],
            },
            "hazardKind": "fire-detection",
            "lifecycle": "observed",
            "coverage": {
                "scope": "viewport",
                "label": f"NASA FIRMS {source} detections in the requested map viewport",
                "isComplete": False,
                "gaps": ["Sensor coverage, clouds and acquisition timing affect detection completeness."],
            },
            "severityEvidence": {
                "provider": "NASA FIRMS",
                "rawLevel": f"frp={frp:.1f}MW,confidence={confidence or 'not-reported'}",
                "mappingVersion": SEVERITY_MAPPING_VERSION,
                "reason": "Raw detection priority uses reported FRP only; it does not assert incident severity.",
            },
            "revision": {"nativeEventId": native_id, "revisionAt": observed_at, "replaces": [], "cancelled": False},
            "metrics": {
                "kind": "wildfire",
                "detectionCount": 1,
                "fireRadiativePowerMw": round(frp, 2),
                "sensor": instrument,
                "satellite": satellite,
                "confidenceLabel": confidence,
            },
        })
        if len(events) >= max(1, min(MAX_VIEWPORT_DETECTIONS, limit)):
            break
    events.sort(key=lambda event: str(event.get("updatedAt") or ""), reverse=True)
    return events


def fetch_viewport(
    http_text_get,
    *,
    map_key: str,
    bbox: tuple[float, float, float, float],
    base_url: str = DEFAULT_BASE_URL,
    source: str = DEFAULT_SOURCE,
    limit: int = MAX_VIEWPORT_DETECTIONS,
) -> ProviderResult:
    clean_key = str(map_key or "").strip()
    if not clean_key:
        raise ValueError("firms-map-key-required")
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("firms-invalid-viewport-bbox")
    clean_source = str(source or DEFAULT_SOURCE).strip()
    area = f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"
    url = f"{base_url.rstrip('/')}/{quote(clean_key, safe='')}/{quote(clean_source, safe='')}/{area}/1"
    text = http_text_get(url, timeout=8, headers={
        "Accept": "text/csv",
        "User-Agent": "polymonitor-world-event-map/1.0 (https://polymonitor.club)",
    })
    reader = csv.DictReader(io.StringIO(str(text or "")))
    required = {"latitude", "longitude", "acq_date", "acq_time"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError("firms-schema-columns")
    events = _raw_detection_events(reader, clean_source, limit)
    newest = max((event["updatedAt"] for event in events), default=None)
    return {"events": events, "data_updated_at": newest}
