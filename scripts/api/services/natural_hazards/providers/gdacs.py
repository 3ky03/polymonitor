from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from ..contracts import ProviderResult, SEVERITY_MAPPING_VERSION
from ..normalize import compact_text, iso_timestamp, valid_point


PROVIDER_KEY = "gdacs"
DEFAULT_URL = "https://www.gdacs.org/xml/gdacs.geojson"
SOURCE_URL = "https://www.gdacs.org/"

HAZARD_KINDS = {
    "FL": "flood",
    "TC": "tropical-cyclone",
    "VO": "volcano",
    "WF": "wildfire",
    "DR": "other-weather-anomaly",
}


def _severity(raw_level: Any) -> tuple[str, Dict[str, str]]:
    level = str(raw_level or "").strip().lower()
    severity = "critical" if level == "red" else "warning" if level == "orange" else "watch"
    return severity, {
        "provider": "GDACS",
        "rawLevel": level or "unknown",
        "mappingVersion": SEVERITY_MAPPING_VERSION,
        "reason": f"GDACS alert level={level or 'unknown'}.",
    }


def _gdacs_timestamp(value: Any) -> str | None:
    normalized = iso_timestamp(value)
    if normalized:
        return normalized
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%d %b %Y %H:%M:%S", "%d %b %Y %H:%M"):
        try:
            parsed = datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return None


def _metric(hazard_kind: str, properties: Dict[str, Any]) -> Dict[str, Any]:
    severity_text = compact_text((properties.get("severitydata") or {}).get("severitytext"), 180) \
        if isinstance(properties.get("severitydata"), dict) else None
    if hazard_kind == "tropical-cyclone":
        return {"kind": "tropical-cyclone", "categoryLabel": severity_text or "GDACS tropical cyclone"}
    if hazard_kind in {"flood"}:
        return {"kind": "weather-alert", "providerSeverity": severity_text or properties.get("alertlevel")}
    if hazard_kind == "wildfire":
        return {"kind": "wildfire", "confidenceLabel": "GDACS alert"}
    return {"kind": "volcano-or-other", "statusLabel": severity_text or f"GDACS {properties.get('eventtype') or 'event'}"}


def fetch(http_json_get, *, url: str = DEFAULT_URL, limit: int = 160) -> ProviderResult:
    payload = http_json_get(
        url,
        timeout=8,
        headers={
            "Accept": "*/*",
            "User-Agent": "polymonitor-world-event-map/1.0 (https://polymonitor.club)",
        },
    )
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise ValueError("gdacs-schema-features")
    events: list[Dict[str, Any]] = []
    seen: set[str] = set()
    newest: str | None = None
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        event_type = str(properties.get("eventtype") or "").strip().upper()
        hazard_kind = HAZARD_KINDS.get(event_type)
        native_id = str(properties.get("eventid") or "").strip()
        point = valid_point((feature.get("geometry") or {}).get("coordinates") if isinstance(feature.get("geometry"), dict) else None)
        if not hazard_kind or not native_id or point is None:
            continue
        identity = f"{event_type}:{native_id}"
        if identity in seen:
            continue
        seen.add(identity)
        raw_level = properties.get("alertlevel")
        if str(raw_level or "").strip().lower() == "green":
            continue
        updated_at = _gdacs_timestamp(properties.get("todate") or properties.get("fromdate"))
        occurred_at = _gdacs_timestamp(properties.get("fromdate")) or updated_at
        if updated_at and (newest is None or updated_at > newest):
            newest = updated_at
        severity, evidence = _severity(raw_level)
        title = compact_text(properties.get("name") or properties.get("eventname"), 220) or f"GDACS {event_type} {native_id}"
        report = properties.get("url") if isinstance(properties.get("url"), dict) else {}
        source_url = str(report.get("report") or properties.get("link") or properties.get("url") or SOURCE_URL)
        events.append({
            "id": f"{hazard_kind}:gdacs:{native_id}",
            "category": "natural-hazard",
            "title": title,
            "summary": compact_text(properties.get("description"), 700),
            "severity": severity,
            "occurredAt": occurred_at,
            "updatedAt": updated_at,
            "geometry": {"type": "Point", "coordinates": point},
            "locationPrecision": "region",
            "locationLabel": compact_text(properties.get("country"), 180),
            "sources": [{
                "provider": "GDACS",
                "url": source_url,
                "nativeId": native_id,
                "observedAt": updated_at,
                "freshness": "fresh",
                "status": "ok",
            }],
            "limitations": [
                "GDACS is an international disaster alert and discovery source, not a complete local warning service.",
                "The map point is the provider-supplied event location, not the full impact footprint.",
            ],
            "relatedMarketIds": [],
            "properties": {
                "mapEntity": "hazard-event",
                "gdacsEventType": event_type,
                "gdacsAlertLevel": raw_level,
                "gdacsEpisodeId": properties.get("episodeid"),
            },
            "hazardKind": hazard_kind,
            "lifecycle": "active",
            "coverage": {
                "scope": "global",
                "label": "GDACS international disaster alerts",
                "isComplete": False,
                "gaps": ["Low-severity green alerts are excluded to keep the operational map readable."],
            },
            "severityEvidence": evidence,
            "revision": {
                "nativeEventId": native_id,
                "advisoryId": str(properties.get("episodeid") or native_id),
                "revisionAt": updated_at,
                "replaces": [],
                "cancelled": False,
            },
            "metrics": _metric(hazard_kind, properties),
        })
        if len(events) >= max(1, limit):
            break
    return {"events": events, "data_updated_at": newest}
