from __future__ import annotations

import io
import zipfile
from typing import Any, Dict
from xml.etree import ElementTree

from ..contracts import ProviderResult, SEVERITY_MAPPING_VERSION
from ..normalize import compact_text, finite_number, iso_timestamp


PROVIDER_KEY = "nhc"
DEFAULT_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
SOURCE_URL = "https://www.nhc.noaa.gov/gis/"
MAX_GEOMETRY_POINTS = 320


def _coordinates(text: str | None) -> list[list[float]]:
    points: list[list[float]] = []
    for token in str(text or "").replace("\n", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except (TypeError, ValueError):
            continue
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            points.append([round(lon, 4), round(lat, 4)])
    if len(points) > MAX_GEOMETRY_POINTS:
        stride = max(1, (len(points) - 1) // (MAX_GEOMETRY_POINTS - 1))
        reduced = points[::stride]
        if reduced[-1] != points[-1]:
            reduced.append(points[-1])
        points = reduced
    return points


def _split_dateline(points: list[list[float]]) -> Dict[str, Any] | None:
    if len(points) < 2:
        return None
    segments: list[list[list[float]]] = [[points[0]]]
    for point in points[1:]:
        if abs(point[0] - segments[-1][-1][0]) > 180:
            segments.append([point])
        else:
            segments[-1].append(point)
    valid = [segment for segment in segments if len(segment) >= 2]
    if not valid:
        return None
    if len(valid) == 1:
        return {"type": "LineString", "coordinates": valid[0]}
    return {"type": "MultiLineString", "coordinates": valid}


def _clip_ring_vertical(
    ring: list[list[float]],
    boundary: float,
    keep_greater: bool,
) -> list[list[float]]:
    if not ring:
        return []
    output: list[list[float]] = []

    def inside(point: list[float]) -> bool:
        return point[0] >= boundary if keep_greater else point[0] <= boundary

    previous = ring[-1]
    previous_inside = inside(previous)
    for current in ring:
        current_inside = inside(current)
        if current_inside != previous_inside:
            delta = current[0] - previous[0]
            if delta:
                fraction = (boundary - previous[0]) / delta
                output.append([boundary, previous[1] + (current[1] - previous[1]) * fraction])
        if current_inside:
            output.append(current)
        previous = current
        previous_inside = current_inside
    return output


def _split_dateline_polygon(ring: list[list[float]]) -> Dict[str, Any] | None:
    """Clip an NHC cone into world-copy-safe polygons without drawing across the globe."""
    if len(ring) < 4:
        return None
    unwrapped: list[list[float]] = [[ring[0][0], ring[0][1]]]
    for raw_lon, lat in ring[1:]:
        lon = raw_lon
        previous_lon = unwrapped[-1][0]
        while lon - previous_lon > 180:
            lon -= 360
        while lon - previous_lon < -180:
            lon += 360
        unwrapped.append([lon, lat])
    min_lon = min(point[0] for point in unwrapped)
    max_lon = max(point[0] for point in unwrapped)
    first_band = int((min_lon + 180) // 360)
    last_band = int((max_lon + 180) // 360)
    polygons: list[list[list[list[float]]]] = []
    for band in range(first_band, last_band + 1):
        west = -180 + band * 360
        east = 180 + band * 360
        clipped = _clip_ring_vertical(unwrapped, west, True)
        clipped = _clip_ring_vertical(clipped, east, False)
        if len(clipped) < 3:
            continue
        normalized = [
            [round(point[0] - band * 360, 4), round(point[1], 4)]
            for point in clipped
        ]
        if normalized[0] != normalized[-1]:
            normalized.append(normalized[0])
        if len(normalized) >= 4:
            polygons.append([normalized])
    if not polygons:
        return None
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _kmz_geometry(http_bytes_get, url: str, kind: str) -> Dict[str, Any] | None:
    if not url.lower().startswith("https://www.nhc.noaa.gov/"):
        return None
    content = http_bytes_get(
        url,
        timeout=5,
        headers={"User-Agent": "polymonitor-world-event-map/1.0 (https://polymonitor.club)"},
    )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        name = next((candidate for candidate in archive.namelist() if candidate.lower().endswith(".kml")), None)
        if not name:
            return None
        root = ElementTree.fromstring(archive.read(name))
    if kind == "line":
        candidates = [_coordinates(node.text) for node in root.findall(".//{*}LineString/{*}coordinates")]
        line = max((candidate for candidate in candidates if len(candidate) >= 2), key=len, default=None)
        if line is None:
            # NHC best-track KMZ encodes successive official fixes as Point
            # placemarks rather than one LineString. Document order is the
            # track order; join only those source-native positions.
            point_fixes = [
                point
                for node in root.findall(".//{*}Point/{*}coordinates")
                for point in _coordinates(node.text)
            ]
            line = point_fixes if len(point_fixes) >= 2 else None
        return _split_dateline(line) if line else None
    candidates = [_coordinates(node.text) for node in root.findall(".//{*}outerBoundaryIs//{*}coordinates")]
    ring = max((candidate for candidate in candidates if len(candidate) >= 3), key=len, default=None)
    if not ring:
        return None
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return _split_dateline_polygon(ring)


def _severity(wind_knots: float) -> tuple[str, Dict[str, Any]]:
    if wind_knots >= 96:
        severity = "critical"
    elif wind_knots >= 64:
        severity = "warning"
    elif wind_knots >= 34:
        severity = "watch"
    else:
        severity = "info"
    return severity, {
        "provider": "NOAA National Hurricane Center",
        "rawLevel": f"maximum sustained wind={wind_knots:g} kt",
        "mappingVersion": SEVERITY_MAPPING_VERSION,
        "reason": "Display severity is derived from the official maximum sustained wind in the current NHC advisory.",
    }


def fetch(
    http_json_get,
    *,
    http_bytes_get=None,
    url: str = DEFAULT_URL,
    limit: int = 40,
) -> ProviderResult:
    payload = http_json_get(
        url,
        timeout=8,
        headers={
            "Accept": "application/json",
            "User-Agent": "polymonitor-world-event-map/1.0 (https://polymonitor.club)",
        },
    )
    storms = payload.get("activeStorms") if isinstance(payload, dict) else None
    if not isinstance(storms, list):
        raise ValueError("nhc-schema-active-storms")
    events: list[Dict[str, Any]] = []
    for storm in storms[: max(1, limit)]:
        if not isinstance(storm, dict):
            continue
        native_id = str(storm.get("id") or "").strip().lower()
        lon = finite_number(storm.get("longitudeNumeric"))
        lat = finite_number(storm.get("latitudeNumeric"))
        wind = finite_number(storm.get("intensity"))
        if not native_id or lon is None or lat is None or wind is None:
            continue
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        updated_at = iso_timestamp(storm.get("lastUpdate"))
        advisory = storm.get("publicAdvisory") if isinstance(storm.get("publicAdvisory"), dict) else {}
        advisory_number = str(advisory.get("advNum") or "").strip() or None
        geometries: Dict[str, Any] = {
            "observedPosition": {"type": "Point", "coordinates": [lon, lat]},
        }
        geometry_errors: list[str] = []
        if http_bytes_get is not None:
            for property_name, source_name, kind in (
                ("observedTrack", "bestTrackGIS", "line"),
                ("forecastTrack", "forecastTrack", "line"),
                ("forecastCone", "trackCone", "polygon"),
            ):
                descriptor = storm.get(source_name) if isinstance(storm.get(source_name), dict) else {}
                kmz_url = str(descriptor.get("kmzFile") or "").strip()
                if not kmz_url:
                    continue
                try:
                    geometry = _kmz_geometry(http_bytes_get, kmz_url, kind)
                except Exception:
                    geometry = None
                if geometry:
                    geometries[property_name] = geometry
                else:
                    geometry_errors.append(property_name)
        severity, evidence = _severity(wind)
        name = compact_text(storm.get("name"), 80) or native_id.upper()
        classification = compact_text(storm.get("classification"), 32) or "TC"
        pressure = finite_number(storm.get("pressure"))
        events.append({
            "id": f"tropical-cyclone:nhc:{native_id}",
            "category": "natural-hazard",
            "title": f"{classification} {name} · NHC Advisory {advisory_number or 'current'}",
            "summary": f"Official NHC position and forecast products for {name}; {wind:g} kt maximum sustained wind.",
            "severity": severity,
            "occurredAt": updated_at,
            "updatedAt": updated_at,
            "geometry": geometries["observedPosition"],
            "locationPrecision": "exact",
            "locationLabel": f"{abs(lat):.1f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.1f}°{'E' if lon >= 0 else 'W'}",
            "sources": [{
                "provider": "NOAA National Hurricane Center",
                "url": str(advisory.get("url") or SOURCE_URL),
                "nativeId": native_id,
                "observedAt": updated_at,
                "freshness": "live",
                "status": "ok",
            }],
            "limitations": [
                "The forecast cone describes probable center-track uncertainty, not the full hazardous-weather footprint.",
                "NHC GIS forecast products are official operational guidance but may be revised by each advisory.",
                *([f"Named forecast geometry unavailable in this refresh: {', '.join(geometry_errors)}."] if geometry_errors else []),
            ],
            "relatedMarketIds": [],
            "properties": {
                "mapEntity": "hazard-event",
                "geometrySource": "nhc-current-storms-and-gis",
                "geometries": geometries,
                "canonicalEventId": f"tropical-cyclone:nhc:{native_id}",
                "mergeReason": "official NHC storm identifier",
                "sourceProvenance": [{"provider": "NOAA National Hurricane Center", "nativeEventId": native_id}],
                "movementDirectionDegrees": finite_number(storm.get("movementDir")),
                "movementSpeedKnots": finite_number(storm.get("movementSpeed")),
                "classification": classification,
            },
            "hazardKind": "tropical-cyclone",
            "lifecycle": "active",
            "coverage": {
                "scope": "provider-area",
                "label": "Active Atlantic, eastern Pacific and central Pacific tropical cyclones published by NHC",
                "isComplete": False,
                "gaps": ["Other national meteorological agencies cover tropical cyclones outside NHC responsibility areas."],
            },
            "severityEvidence": evidence,
            "revision": {
                "nativeEventId": native_id,
                "advisoryId": advisory_number,
                "revisionAt": updated_at,
                "replaces": [],
                "cancelled": False,
            },
            "metrics": {
                "kind": "tropical-cyclone",
                "maximumWind": {"value": wind, "unit": "kt"},
                "pressureHpa": pressure,
                "categoryLabel": classification,
                "advisoryNumber": advisory_number,
            },
        })
    newest = max((event.get("updatedAt") or "" for event in events), default=None)
    return {"events": events, "data_updated_at": newest}
