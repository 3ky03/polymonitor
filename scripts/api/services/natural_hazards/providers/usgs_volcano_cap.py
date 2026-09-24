from __future__ import annotations

from typing import Any, Dict

from ..contracts import ProviderResult, SEVERITY_MAPPING_VERSION
from ..normalize import compact_text, finite_number, iso_timestamp


PROVIDER_KEY = "usgs-volcano-cap"
DEFAULT_URL = "https://volcanoes.usgs.gov/hans-public/api/volcano/getCapElevated"
SOURCE_URL = "https://volcanoes.usgs.gov/hans-public/resources/"


def _severity(row: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    alert = str(row.get("alert_level") or "").strip().upper()
    color = str(row.get("color_code") or "").strip().upper()
    if alert == "WARNING" or color == "RED":
        severity = "critical"
    elif alert == "WATCH" or color == "ORANGE":
        severity = "warning"
    elif alert == "ADVISORY" or color == "YELLOW":
        severity = "watch"
    else:
        severity = "info"
    return severity, {
        "provider": "USGS Volcano Hazards Program HANS CAP",
        "rawLevel": f"alert={alert or 'not-reported'},aviation={color or 'not-reported'}",
        "mappingVersion": SEVERITY_MAPPING_VERSION,
        "reason": "Display severity maps official USGS Volcano Alert Level and Aviation Color Code without changing provider meaning.",
    }


def fetch(http_json_get, *, url: str = DEFAULT_URL, limit: int = 80) -> ProviderResult:
    payload = http_json_get(url, timeout=8, headers={
        "Accept": "application/json",
        "User-Agent": "polymonitor-world-event-map/1.0 (https://polymonitor.club)",
    })
    if not isinstance(payload, list):
        raise ValueError("usgs-volcano-cap-schema")
    events: list[Dict[str, Any]] = []
    for row in payload[: max(1, limit)]:
        if not isinstance(row, dict) or not bool(row.get("is_elevated_cap")):
            continue
        vnum = str(row.get("vnum") or "").strip()
        lon = finite_number(row.get("longitude"))
        lat = finite_number(row.get("latitude"))
        notice_id = str(row.get("notice_identifier") or row.get("guid") or "").strip()
        if not vnum or not notice_id or lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        updated_at = iso_timestamp(row.get("sent_date_cap"))
        expires_at = iso_timestamp(row.get("cap_expires"))
        if updated_at is None:
            continue
        name = compact_text(row.get("volcano_name_appended"), 100) or f"USGS volcano {vnum}"
        severity, evidence = _severity(row)
        alert = str(row.get("alert_level") or "").strip().upper() or "UNASSIGNED"
        color = str(row.get("color_code") or "").strip().upper() or "UNASSIGNED"
        previous = str(row.get("prev_guid") or "").strip()
        events.append({
            "id": f"volcano:usgs:{vnum}",
            "category": "natural-hazard",
            "title": f"{name} · {alert} / {color}",
            "summary": compact_text(row.get("synopsis"), 700),
            "severity": severity,
            "occurredAt": updated_at,
            "updatedAt": updated_at,
            "expiresAt": expires_at,
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "locationPrecision": "exact",
            "locationLabel": str(row.get("obs_fullname") or "USGS responsibility area"),
            "sources": [{
                "provider": "USGS Volcano Hazards Program HANS CAP",
                "url": str(row.get("notice_url") or SOURCE_URL),
                "nativeId": notice_id,
                "observedAt": updated_at,
                "freshness": "live",
                "status": "ok",
            }],
            "limitations": [
                "This official CAP feed covers elevated volcanoes monitored by United States volcano observatories; it is not global volcano coverage.",
                "A volcano alert level and aviation color code describe provider status, not a universal cross-country impact scale.",
            ],
            "relatedMarketIds": [],
            "properties": {
                "mapEntity": "hazard-event",
                "observationType": "official-cap-volcano-status",
                "canonicalEventId": f"volcano:usgs:{vnum}",
                "mergeReason": "official USGS Smithsonian volcano number",
                "sourceProvenance": [{"provider": "USGS HANS CAP", "nativeEventId": notice_id}],
                "status": {"alertLevel": alert, "aviationColorCode": color},
            },
            "hazardKind": "volcano",
            "lifecycle": "active",
            "coverage": {
                "scope": "provider-area",
                "label": "Elevated volcanoes monitored by USGS volcano observatories",
                "isComplete": False,
                "gaps": ["No official coverage is implied outside United States volcano observatory responsibility areas."],
            },
            "severityEvidence": evidence,
            "revision": {
                "nativeEventId": notice_id,
                "advisoryId": notice_id,
                "revisionAt": updated_at,
                "replaces": [previous] if previous else [],
                "cancelled": False,
            },
            "metrics": {"kind": "volcano-or-other", "statusLabel": f"{alert} / {color}"},
        })
    newest = max((event["updatedAt"] for event in events), default=None)
    return {"events": events, "data_updated_at": newest}
