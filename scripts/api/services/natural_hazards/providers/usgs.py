from __future__ import annotations

from typing import Any, Dict

from ..contracts import ProviderResult
from ..normalize import compact_text, finite_number, iso_from_epoch_ms, valid_point
from ..severity import usgs_severity


PROVIDER_KEY = "usgs"
DEFAULT_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson"
SOURCE_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php"


def fetch(http_json_get, *, url: str = DEFAULT_URL, limit: int = 500) -> ProviderResult:
    payload = http_json_get(
        url,
        timeout=8,
        headers={
            "Accept": "application/geo+json, application/json",
            "User-Agent": "polymonitor-world-event-map/1.0 (https://polymonitor.club)",
        },
    )
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise ValueError("usgs-schema-features")
    events: list[Dict[str, Any]] = []
    for feature in features[: max(1, limit)]:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        native_id = str(feature.get("id") or "").strip()
        geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
        point = valid_point(geometry.get("coordinates"))
        magnitude = finite_number(properties.get("mag"))
        if not native_id or point is None or magnitude is None:
            continue
        depth = finite_number((geometry.get("coordinates") or [None, None, None])[2])
        occurred_at = iso_from_epoch_ms(properties.get("time"))
        updated_at = iso_from_epoch_ms(properties.get("updated"))
        severity, evidence = usgs_severity(properties)
        place = compact_text(properties.get("place"), 180) or "Earthquake"
        source_url = str(properties.get("url") or SOURCE_URL)
        events.append(
            {
                "id": f"earthquake:usgs:{native_id}",
                "category": "natural-hazard",
                "title": compact_text(properties.get("title"), 220) or f"M {magnitude:.1f} - {place}",
                "summary": f"USGS reviewed earthquake observation near {place}.",
                "severity": severity,
                "occurredAt": occurred_at,
                "updatedAt": updated_at,
                "geometry": {"type": "Point", "coordinates": point},
                "locationPrecision": "exact",
                "locationLabel": place,
                "sources": [
                    {
                        "provider": "USGS",
                        "url": source_url,
                        "nativeId": native_id,
                        "observedAt": occurred_at,
                        "ingestedAt": updated_at,
                        "freshness": "live",
                        "status": "ok",
                    }
                ],
                "limitations": [
                    "Magnitude, depth, location and PAGER products may be revised by USGS.",
                    "Small-event completeness varies by regional seismic network coverage.",
                ],
                "relatedMarketIds": [],
                "properties": {
                    "mapEntity": "hazard-event",
                    "providerStatus": properties.get("status"),
                    "felt": properties.get("felt"),
                    "mmi": properties.get("mmi"),
                    "detailUrl": properties.get("detail"),
                },
                "hazardKind": "earthquake",
                "lifecycle": "observed",
                "coverage": {
                    "scope": "global",
                    "label": "Global earthquakes reported by USGS networks",
                    "isComplete": False,
                    "gaps": ["Small-event completeness varies by region."],
                },
                "severityEvidence": evidence,
                "revision": {
                    "nativeEventId": native_id,
                    "revisionAt": updated_at,
                    "replaces": [],
                    "cancelled": False,
                },
                "metrics": {
                    "kind": "earthquake",
                    "magnitude": magnitude,
                    "depthKm": depth,
                    "significance": finite_number(properties.get("sig")),
                    "pagerAlert": properties.get("alert"),
                    "tsunami": bool(properties.get("tsunami")),
                },
            }
        )
    generated = payload.get("metadata", {}).get("generated") if isinstance(payload.get("metadata"), dict) else None
    return {"events": events, "data_updated_at": iso_from_epoch_ms(generated)}
