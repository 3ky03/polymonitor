from __future__ import annotations

from typing import Any, Dict

from ..contracts import ProviderResult
from ..normalize import compact_text, finite_number, iso_timestamp, unique_strings, valid_point
from ..severity import eonet_severity


PROVIDER_KEY = "eonet"
DEFAULT_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"
SOURCE_URL = "https://eonet.gsfc.nasa.gov/"
CATEGORY_IDS = "severeStorms,floods,volcanoes,wildfires,tempExtremes,drought,dustHaze,snow"


def _hazard_kind(category_id: str, title: str) -> str | None:
    lowered = title.lower()
    if category_id == "severeStorms":
        if any(term in lowered for term in ("hurricane", "typhoon", "cyclone", "tropical storm")):
            return "tropical-cyclone"
        if "tornado" in lowered:
            return "tornado"
        return "severe-storm"
    return {
        "floods": "flood",
        "volcanoes": "volcano",
        "wildfires": "wildfire",
        "tempExtremes": "extreme-heat" if "heat" in lowered else "extreme-cold" if "cold" in lowered else "other-weather-anomaly",
        "drought": "other-weather-anomaly",
        "dustHaze": "other-weather-anomaly",
        "snow": "severe-storm",
    }.get(category_id)


def _geometry(
    observations: list[Dict[str, Any]],
    hazard_kind: str,
) -> tuple[Dict[str, Any] | None, str | None]:
    points: list[tuple[str, list[float]]] = []
    latest_at: str | None = None
    latest_area: tuple[str, Dict[str, Any]] | None = None
    for observation in observations:
        observed_at = iso_timestamp(observation.get("date"))
        if observed_at and (latest_at is None or observed_at > latest_at):
            latest_at = observed_at
        geometry_type = str(observation.get("type") or "")
        coordinates = observation.get("coordinates")
        if geometry_type == "Point":
            point = valid_point(coordinates)
            timestamp = observed_at or ""
            if point and not any(existing == point for _, existing in points):
                points.append((timestamp, point))
        elif geometry_type in {"Polygon", "MultiPolygon"} and isinstance(coordinates, list):
            timestamp = observed_at or ""
            if latest_area is None or timestamp >= latest_area[0]:
                latest_area = (timestamp, {"type": geometry_type, "coordinates": coordinates})
    if latest_area:
        return latest_area[1], latest_at
    points.sort(key=lambda item: item[0])
    if hazard_kind == "tropical-cyclone" and len(points) >= 2:
        return {"type": "LineString", "coordinates": [point for _, point in points]}, latest_at
    if points:
        return {"type": "Point", "coordinates": points[-1][1]}, latest_at
    return None, latest_at


def fetch(http_json_get, *, url: str = DEFAULT_URL, limit: int = 300) -> ProviderResult:
    payload = http_json_get(
        url,
        params={"status": "open", "days": 120, "limit": max(1, limit), "category": CATEGORY_IDS},
        timeout=8,
        headers={
            "Accept": "application/json",
            "User-Agent": "polymonitor-world-event-map/1.0 (https://polymonitor.club)",
        },
    )
    raw_events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(raw_events, list):
        raise ValueError("eonet-schema-events")
    events: list[Dict[str, Any]] = []
    newest: str | None = None
    for raw in raw_events[: max(1, limit)]:
        if not isinstance(raw, dict):
            continue
        native_id = str(raw.get("id") or "").strip()
        title = compact_text(raw.get("title"), 220) or ""
        categories = raw.get("categories") if isinstance(raw.get("categories"), list) else []
        category_id = str((categories[0] if categories else {}).get("id") or "")
        hazard_kind = _hazard_kind(category_id, title)
        observations = [item for item in (raw.get("geometry") or []) if isinstance(item, dict)]
        geometry, updated_at = _geometry(observations, hazard_kind or "")
        if not native_id or not title or hazard_kind is None:
            continue
        if updated_at and (newest is None or updated_at > newest):
            newest = updated_at
        latest = observations[-1] if observations else {}
        magnitude_value = finite_number(latest.get("magnitudeValue"))
        magnitude_unit = compact_text(latest.get("magnitudeUnit"), 40)
        severity, evidence = eonet_severity(hazard_kind, magnitude_value, magnitude_unit)
        raw_sources = raw.get("sources") if isinstance(raw.get("sources"), list) else []
        source_urls = unique_strings(item.get("url") for item in raw_sources if isinstance(item, dict))
        provider_ids = unique_strings(item.get("id") for item in raw_sources if isinstance(item, dict))
        metric: Dict[str, Any]
        if hazard_kind == "tropical-cyclone":
            metric = {
                "kind": "tropical-cyclone",
                "maximumWind": (
                    {"value": magnitude_value, "unit": "kt"}
                    if magnitude_value is not None and str(magnitude_unit or "").lower() in {"kts", "kt"}
                    else None
                ),
                "categoryLabel": magnitude_unit,
            }
        elif hazard_kind == "wildfire":
            metric = {
                "kind": "wildfire",
                "detectionCount": None,
                "fireRadiativePowerMw": None,
                "sensor": None,
                "satellite": None,
                "confidenceLabel": "named-event",
            }
        elif hazard_kind in {"severe-storm", "tornado", "flood", "extreme-heat", "extreme-cold", "tsunami"}:
            metric = {
                "kind": "weather-alert",
                "providerSeverity": "EONET discovery",
            }
        else:
            metric = {"kind": "volcano-or-other", "statusLabel": f"EONET open {category_id}"}
        limitations = [
            "NASA EONET is a global discovery and cross-validation source, not a local official warning service.",
        ]
        if geometry is None:
            limitations.append("EONET did not provide renderable geometry for this event.")
        events.append(
            {
                "id": f"{hazard_kind}:eonet:{native_id}",
                "category": "natural-hazard",
                "title": title,
                "summary": compact_text(raw.get("description"), 500),
                "severity": severity,
                "occurredAt": updated_at,
                "updatedAt": updated_at,
                "geometry": geometry,
                "locationPrecision": "exact" if geometry else "unknown",
                "locationLabel": compact_text(raw.get("description"), 180),
                "sources": [
                    {
                        "provider": "NASA EONET",
                        "url": source_urls[0] if source_urls else str(raw.get("link") or SOURCE_URL),
                        "nativeId": native_id,
                        "observedAt": updated_at,
                        "freshness": "fresh",
                        "status": "ok",
                    }
                ],
                "limitations": limitations,
                "relatedMarketIds": [],
                "properties": {
                    "mapEntity": "hazard-event",
                    "eonetCategory": category_id,
                    "providerSourceIds": provider_ids,
                    "magnitudeValue": magnitude_value,
                    "magnitudeUnit": magnitude_unit,
                    "observationCount": len(observations),
                },
                "hazardKind": hazard_kind,
                "lifecycle": "active",
                "coverage": {
                    "scope": "global",
                    "label": "NASA EONET open-event discovery",
                    "isComplete": False,
                    "gaps": ["Not equivalent to local official warning coverage."],
                },
                "severityEvidence": evidence,
                "revision": {
                    "nativeEventId": native_id,
                    "revisionAt": updated_at,
                    "replaces": [],
                    "cancelled": False,
                },
                "metrics": metric,
            }
        )
    return {"events": events, "data_updated_at": newest}
