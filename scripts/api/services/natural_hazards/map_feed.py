from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from math import ceil
from typing import Any, Iterable, Mapping

from .contracts import SCHEMA_VERSION
from .dedupe import canonical_event_identity, latest_revision
from .service import NaturalHazardDependencies, get_natural_hazard_source_result
from .snapshots import cached_source_result
from .source_health import SOURCE_COVERAGE
from .providers import firms


MAP_SCHEMA_VERSION = "natural-hazards-map.v1"
DETAIL_SCHEMA_VERSION = "natural-hazard-detail.v1"
MAP_SOURCE_KEYS = ("usgs", "usgs-volcano-cap", "nhc", "eonet", "gdacs", "nws", "firms", "climate-anomaly")

_RENDER_PROPERTY_KEYS = {
    "observationType",
    "geometrySource",
    "resolvedZoneCount",
    "unresolvedZoneCount",
    "forecast",
    "observed",
    "status",
    "geometries",
    "canonicalEventId",
    "mergeReason",
    "sourceProvenance",
}

_METRIC_KEYS_BY_KIND = {
    "earthquake": {"magnitude", "depthKm", "significance", "pagerAlert", "tsunami"},
    "tropical-cyclone": {"maximumWind", "pressureHpa", "categoryLabel", "advisoryNumber"},
    "weather-alert": {"urgency", "certainty", "providerSeverity"},
    "wildfire": {"detectionCount", "fireRadiativePowerMw", "sensor", "satellite", "confidenceLabel"},
    "climate-anomaly": {
        "variable", "value", "anomaly", "unit", "baselinePeriod", "calculationVersion",
        "timeWindow", "spatialResolution", "provider",
    },
    "volcano-or-other": {"statusLabel"},
}


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    if not rendered:
        return None
    return rendered if len(rendered) <= limit else f"{rendered[: max(0, limit - 1)].rstrip()}…"


def _point(value: Any, precision: int) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        lon = round(float(value[0]), precision)
        lat = round(float(value[1]), precision)
    except (TypeError, ValueError):
        return None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return [lon, lat]


def _dedupe_points(points: Iterable[list[float]]) -> list[list[float]]:
    deduped: list[list[float]] = []
    for point in points:
        if not deduped or point != deduped[-1]:
            deduped.append(point)
    return deduped


def _simplify_line(coordinates: Any, *, max_points: int, precision: int) -> list[list[float]]:
    if not isinstance(coordinates, list):
        return []
    valid = [point for item in coordinates if (point := _point(item, precision)) is not None]
    if len(valid) <= max_points:
        return _dedupe_points(valid)
    stride = max(1, ceil((len(valid) - 1) / max(1, max_points - 1)))
    sampled = valid[::stride]
    if sampled[-1] != valid[-1]:
        sampled.append(valid[-1])
    return _dedupe_points(sampled)


def _simplify_ring(coordinates: Any, *, max_points: int, precision: int) -> list[list[float]]:
    line = _simplify_line(coordinates, max_points=max_points, precision=precision)
    if len(line) < 3:
        return []
    if line[0] != line[-1]:
        line.append(line[0])
    return line if len(line) >= 4 else []


def _ring_area_score(ring: list[list[float]]) -> float:
    if not ring:
        return 0.0
    longitudes = [point[0] for point in ring]
    latitudes = [point[1] for point in ring]
    return max(0.0, max(longitudes) - min(longitudes)) * max(0.0, max(latitudes) - min(latitudes))


def simplify_geometry(geometry: Any, *, zoom: float = 2.0) -> dict[str, Any] | None:
    """Return a bounded map geometry without mutating the evidence record.

    Global feeds intentionally trade sub-kilometre polygon detail for a small,
    valid shape. The complete source geometry remains available from the detail
    endpoint after selection.
    """

    if not isinstance(geometry, Mapping):
        return None
    kind = str(geometry.get("type") or "")
    coordinates = geometry.get("coordinates")
    if zoom < 3:
        precision, line_budget, ring_budget, hole_budget = 2, 64, 64, 0
        polygon_budget, total_point_budget = 8, 256
    elif zoom < 5:
        precision, line_budget, ring_budget, hole_budget = 3, 128, 128, 0
        polygon_budget, total_point_budget = 24, 1_024
    else:
        precision, line_budget, ring_budget, hole_budget = 4, 192, 192, 1
        polygon_budget, total_point_budget = 32, 2_048

    if kind == "Point":
        point = _point(coordinates, precision)
        return {"type": kind, "coordinates": point} if point else None
    if kind == "LineString":
        line = _simplify_line(coordinates, max_points=line_budget, precision=precision)
        return {"type": kind, "coordinates": line} if len(line) >= 2 else None
    if kind == "Polygon" and isinstance(coordinates, list):
        rings = [
            ring
            for item in coordinates[: 1 + hole_budget]
            if (ring := _simplify_ring(item, max_points=ring_budget, precision=precision))
        ]
        return {"type": kind, "coordinates": rings} if rings else None
    if kind == "MultiPolygon" and isinstance(coordinates, list):
        candidates: list[tuple[float, list[list[list[float]]]]] = []
        for polygon in coordinates:
            if not isinstance(polygon, list):
                continue
            rings = [
                ring
                for item in polygon[: 1 + hole_budget]
                if (ring := _simplify_ring(item, max_points=ring_budget, precision=precision))
            ]
            if rings:
                candidates.append((_ring_area_score(rings[0]), rings))
        candidates.sort(key=lambda item: item[0], reverse=True)
        polygons: list[list[list[list[float]]]] = []
        point_count = 0
        for _, rings in candidates[:polygon_budget]:
            ring_points = sum(len(ring) for ring in rings)
            if polygons and point_count + ring_points > total_point_budget:
                continue
            polygons.append(rings)
            point_count += ring_points
            if point_count >= total_point_budget:
                break
        return {"type": kind, "coordinates": polygons} if polygons else None
    return None


def compact_hazard_event(event: Mapping[str, Any], *, zoom: float = 2.0) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "id",
        "category",
        "title",
        "severity",
        "occurredAt",
        "updatedAt",
        "expiresAt",
        "locationPrecision",
        "countryCode",
        "regionCode",
        "locationLabel",
        "confidence",
        "hazardKind",
        "lifecycle",
        "effectiveAt",
        "onsetAt",
        "endedAt",
    ):
        value = event.get(key)
        if value is not None:
            compact[key] = value

    summary = _text(event.get("summary"), 240)
    if summary:
        compact["summary"] = summary
    compact["geometry"] = simplify_geometry(event.get("geometry"), zoom=zoom)
    coverage = event.get("coverage") if isinstance(event.get("coverage"), Mapping) else {
        "scope": "provider-area",
        "label": "Provider coverage was not declared.",
        "isComplete": False,
        "gaps": ["Coverage metadata unavailable."],
    }
    compact["coverage"] = {
        "scope": str(coverage.get("scope") or "provider-area"),
        "label": _text(coverage.get("label"), 180) or "Provider coverage was not declared.",
        "isComplete": bool(coverage.get("isComplete")),
        "gaps": [
            text
            for value in list(coverage.get("gaps") or [])[:2]
            if (text := _text(value, 160))
        ],
    }

    evidence = event.get("severityEvidence") if isinstance(event.get("severityEvidence"), Mapping) else {}
    compact["severityEvidence"] = {
        "provider": str(evidence.get("provider") or "unknown"),
        "mappingVersion": str(evidence.get("mappingVersion") or "unknown"),
        "reason": _text(evidence.get("reason"), 180) or "See the full disaster report.",
    }
    if evidence.get("rawLevel") is not None:
        compact["severityEvidence"]["rawLevel"] = _text(evidence.get("rawLevel"), 80)

    revision = event.get("revision") if isinstance(event.get("revision"), Mapping) else {}
    compact["revision"] = {
        key: deepcopy(value)
        for key in ("nativeEventId", "advisoryId", "revisionAt", "replaces", "cancelled")
        if (value := revision.get(key)) is not None
    }
    metrics = event.get("metrics") if isinstance(event.get("metrics"), Mapping) else {}
    metric_kind = str(metrics.get("kind") or "volcano-or-other")
    compact["metrics"] = {
        "kind": metric_kind,
        **{
            key: deepcopy(value)
            for key in _METRIC_KEYS_BY_KIND.get(metric_kind, set())
            if (value := metrics.get(key)) is not None
        },
    }
    compact["sources"] = []
    for source in event.get("sources") or []:
        if not isinstance(source, Mapping):
            continue
        compact_source = {
            key: source.get(key)
            for key in ("provider", "nativeId", "observedAt", "ingestedAt", "freshness", "status")
            if source.get(key) is not None
        }
        if compact_source.get("provider"):
            compact["sources"].append(compact_source)

    properties = event.get("properties") if isinstance(event.get("properties"), Mapping) else {}
    compact["properties"] = {
        key: deepcopy(value)
        for key in _RENDER_PROPERTY_KEYS - {"geometries"}
        if (value := properties.get(key)) is not None
    }
    canonical_id, merge_reason = canonical_event_identity(event)
    if canonical_id and canonical_id != str(event.get("id") or ""):
        compact["id"] = canonical_id
        compact["properties"]["canonicalEventId"] = canonical_id
        compact["properties"]["mergeReason"] = merge_reason
        compact["properties"].setdefault("sourceProvenance", [
            {
                "provider": str(source.get("provider") or "unknown"),
                "nativeEventId": str(
                    revision.get("nativeEventId")
                    or source.get("nativeId")
                    or event.get("id")
                    or ""
                ),
                "revisionAt": str(
                    revision.get("revisionAt")
                    or event.get("updatedAt")
                    or ""
                ),
            }
            for source in event.get("sources") or []
            if isinstance(source, Mapping)
        ])
    named_geometries = properties.get("geometries") if isinstance(properties.get("geometries"), Mapping) else {}
    simplified_named = {
        str(name): simplified
        for name, geometry in named_geometries.items()
        if (simplified := simplify_geometry(geometry, zoom=zoom)) is not None
    }
    if simplified_named:
        compact["properties"]["geometries"] = simplified_named
    compact["properties"]["geometryMode"] = "simplified"
    compact["properties"]["geometryZoom"] = zoom
    compact["properties"]["detailAvailable"] = True
    compact["limitations"] = [
        text
        for value in list(event.get("limitations") or [])[:2]
        if (text := _text(value, 160))
    ]
    compact["relatedMarketIds"] = []
    return compact


def _empty_source(key: str, error_code: str) -> dict[str, Any]:
    return {
        "key": key,
        "status": "error",
        "coverage": SOURCE_COVERAGE.get(key, {
            "scope": "provider-area",
            "label": "Provider coverage unavailable.",
            "isComplete": False,
            "gaps": ["Provider is not configured."],
        }),
        "events": [],
        "fetchedAt": None,
        "dataUpdatedAt": None,
        "staleAfter": None,
        "lastSuccessAt": None,
        "errorCode": error_code,
    }


def get_natural_hazard_map_snapshot(
    context: Mapping[str, Any],
    *,
    source: str,
    limit: int = 1200,
    zoom: float = 2.0,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    key = str(source or "").strip().lower()
    if key not in MAP_SOURCE_KEYS:
        raise ValueError("unsupported-natural-hazard-source")
    bounded_limit = max(1, min(1200, int(limit)))
    if key == "firms" and zoom >= 5 and bbox is not None:
        dependencies = NaturalHazardDependencies.from_context(context)
        if not dependencies.firms_map_key or dependencies.http_text_get is None:
            result = _empty_source(key, "configuration-required")
        else:
            try:
                viewport_result = firms.fetch_viewport(
                    dependencies.http_text_get,
                    map_key=dependencies.firms_map_key,
                    bbox=bbox,
                    base_url=dependencies.firms_base_url,
                    source=dependencies.firms_source,
                    limit=bounded_limit,
                )
                updated = viewport_result.get("data_updated_at") or _generated_at()
                result = {
                    "key": key,
                    "status": "ok",
                    "coverage": SOURCE_COVERAGE[key],
                    "events": viewport_result.get("events") or [],
                    "fetchedAt": _generated_at(),
                    "dataUpdatedAt": updated,
                    "staleAfter": None,
                    "lastSuccessAt": updated,
                    "errorCode": None,
                }
            except Exception as exc:
                result = _empty_source(key, f"firms-viewport-{exc.__class__.__name__}")
    else:
        result = get_natural_hazard_source_result(
            context,
            source=key,
            limit=bounded_limit,
        )
    events = [
        compact_hazard_event(event, zoom=zoom)
        for event in list(result.get("events") or [])[:bounded_limit]
        if isinstance(event, Mapping)
        and not bool((event.get("revision") or {}).get("cancelled"))
        and str(event.get("lifecycle") or "").lower() not in {"cancelled", "expired", "ended"}
    ]
    source_state = {name: deepcopy(value) for name, value in result.items() if name != "events"}
    errors = [] if source_state.get("status") == "ok" else [{
        "source": key,
        "code": source_state.get("errorCode"),
    }]
    generated_at = str(source_state.get("dataUpdatedAt") or source_state.get("fetchedAt") or _generated_at())
    return {
        "schemaVersion": MAP_SCHEMA_VERSION,
        "generatedAt": generated_at,
        "events": events,
        "sources": [source_state],
        "isPartial": bool(errors),
        "errors": errors,
        "counts": {
            "events": len(events),
            "byHazardKind": {
                hazard_kind: sum(1 for event in events if event.get("hazardKind") == hazard_kind)
                for hazard_kind in sorted({str(event.get("hazardKind") or "") for event in events})
                if hazard_kind
            },
        },
        "meta": {
            "source": key,
            "geometryMode": "simplified",
            "geometryZoom": zoom,
            "bbox": list(bbox) if bbox is not None else None,
            "detailEndpoint": "/runtime/world/natural-hazards/events/{eventId}",
            "fullSchemaVersion": SCHEMA_VERSION,
        },
    }


def get_natural_hazard_event_detail(
    context: Mapping[str, Any],
    *,
    event_id: str,
) -> dict[str, Any] | None:
    wanted = str(event_id or "").strip()
    if not wanted:
        return None
    dependencies = NaturalHazardDependencies.from_context(context)
    cached_events: list[dict[str, Any]] = []
    for key in MAP_SOURCE_KEYS:
        result = cached_source_result(dependencies.snapshot_store, key)
        if not result:
            continue
        for event in result.get("events") or []:
            if isinstance(event, Mapping):
                cached_events.append(deepcopy(dict(event)))
    for event in latest_revision(cached_events):
        if str(event.get("id") or "") != wanted:
            continue
        revision = event.get("revision") if isinstance(event.get("revision"), Mapping) else {}
        generated_at = str(
            event.get("updatedAt")
            or revision.get("revisionAt")
            or _generated_at()
        )
        return {
            "schemaVersion": DETAIL_SCHEMA_VERSION,
            "generatedAt": generated_at,
            "event": event,
        }
    return None
