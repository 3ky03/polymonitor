from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable


LINKER_SCHEMA_VERSION = "hazard-market-links.v1"
LINKER_VERSION = "hazard-weather-market-linker.v1"
DEFAULT_LIMIT = 8

_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")

_DIRECT_FAMILIES: dict[str, set[str]] = {
    "tornado": {"tornado"},
    "volcano": {"volcano"},
    "temperature-anomaly": {"highest_temperature", "lowest_temperature"},
    "precipitation-anomaly": {"precipitation"},
}

_CONTEXTUAL_FAMILIES: dict[str, set[str]] = {
    "severe-storm": {"weather_binary", "precipitation"},
    "tropical-cyclone": {"hurricane"},
    "flood": {"precipitation"},
    "extreme-heat": {"highest_temperature"},
    "extreme-cold": {"lowest_temperature"},
    "other-weather-anomaly": {"global_climate"},
}

_DIRECT_RADIUS_KM: dict[str, float] = {
    "severe-storm": 125.0,
    "tornado": 75.0,
    "tropical-cyclone": 250.0,
    "flood": 100.0,
    "extreme-heat": 125.0,
    "extreme-cold": 125.0,
    "earthquake": 125.0,
    "volcano": 125.0,
    "tsunami": 250.0,
    "wildfire": 100.0,
    "fire-detection": 50.0,
    "temperature-anomaly": 125.0,
    "precipitation-anomaly": 125.0,
    "other-weather-anomaly": 250.0,
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _target_date(market: Dict[str, Any]) -> str | None:
    for value in (
        market.get("targetDate"),
        market.get("eventTitle"),
        market.get("eventSlug"),
        market.get("marketUrl"),
    ):
        match = _ISO_DATE_RE.search(str(value or ""))
        if match:
            try:
                return datetime.fromisoformat(match.group(1)).date().isoformat()
            except ValueError:
                continue
    return None


def _event_window(event: Dict[str, Any]) -> tuple[datetime, datetime] | None:
    start = next(
        (
            parsed
            for value in (
                event.get("onsetAt"),
                event.get("effectiveAt"),
                event.get("occurredAt"),
                event.get("updatedAt"),
            )
            for parsed in [_parse_datetime(value)]
            if parsed is not None
        ),
        None,
    )
    if start is None:
        return None
    end = next(
        (
            parsed
            for value in (event.get("expiresAt"), event.get("endedAt"))
            for parsed in [_parse_datetime(value)]
            if parsed is not None
        ),
        start,
    )
    if end < start:
        end = start
    return start, end


def _time_evidence(event: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    target_date = _target_date(market)
    event_window = _event_window(event)
    if target_date is None:
        return {
            "passed": False,
            "reason": "Market settlement date is not available as a structured or ISO-dated field.",
            "targetDate": None,
        }
    if event_window is None:
        return {
            "passed": False,
            "reason": "Hazard has no valid effective, onset, occurrence or update timestamp.",
            "targetDate": target_date,
        }
    day_start = datetime.fromisoformat(target_date).replace(tzinfo=timezone.utc)
    day_end = day_start.replace(hour=23, minute=59, second=59, microsecond=999999)
    event_start, event_end = event_window
    passed = event_start <= day_end and event_end >= day_start
    return {
        "passed": passed,
        "reason": (
            f"Market target day {target_date} overlaps hazard window "
            f"{event_start.isoformat()} to {event_end.isoformat()}."
            if passed
            else f"Market target day {target_date} does not overlap the hazard validity window."
        ),
        "targetDate": target_date,
        "eventStart": event_start.isoformat().replace("+00:00", "Z"),
        "eventEnd": event_end.isoformat().replace("+00:00", "Z"),
    }


def _haversine_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    lon1, lat1 = first
    lon2, lat2 = second
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    delta_lat = lat2_r - lat1_r
    delta_lon = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(delta_lon / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(value)))


def _point_in_ring(point: tuple[float, float], ring: Iterable[Any]) -> bool:
    x, y = point
    vertices = [
        (float(item[0]), float(item[1]))
        for item in ring
        if isinstance(item, (list, tuple)) and len(item) >= 2
    ]
    if len(vertices) < 3:
        return False
    inside = False
    previous = vertices[-1]
    for current in vertices:
        x1, y1 = current
        x2, y2 = previous
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _point_in_polygon(point: tuple[float, float], polygon: Any) -> bool:
    if not isinstance(polygon, list) or not polygon:
        return False
    if not _point_in_ring(point, polygon[0]):
        return False
    return not any(_point_in_ring(point, hole) for hole in polygon[1:])


def _geometry_points(geometry: Dict[str, Any]) -> list[tuple[float, float]]:
    geometry_type = str(geometry.get("type") or "")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point" and isinstance(coordinates, list) and len(coordinates) >= 2:
        return [(float(coordinates[0]), float(coordinates[1]))]
    if geometry_type == "LineString" and isinstance(coordinates, list):
        return [
            (float(item[0]), float(item[1]))
            for item in coordinates
            if isinstance(item, list) and len(item) >= 2
        ]
    return []


def _space_evidence(
    event: Dict[str, Any],
    *,
    city: str,
    lon: Any,
    lat: Any,
) -> Dict[str, Any]:
    try:
        city_point = (float(lon), float(lat))
    except (TypeError, ValueError):
        return {"passed": False, "reason": "Market target has no valid city or region coordinates."}
    geometry = event.get("geometry") if isinstance(event.get("geometry"), dict) else {}
    geometry_type = str(geometry.get("type") or "")
    coordinates = geometry.get("coordinates")
    contained = False
    if geometry_type == "Polygon":
        contained = _point_in_polygon(city_point, coordinates)
    elif geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        contained = any(_point_in_polygon(city_point, polygon) for polygon in coordinates)
    if contained:
        return {
            "passed": True,
            "level": "direct",
            "distanceKm": 0.0,
            "reason": f"{city} lies inside the provider-native hazard polygon.",
        }
    event_points = _geometry_points(geometry)
    if not event_points:
        return {
            "passed": False,
            "reason": "Hazard has no point, track or polygon suitable for spatial evidence.",
        }
    distance = min(_haversine_km(point, city_point) for point in event_points)
    hazard_kind = str(event.get("hazardKind") or "")
    direct_radius = _DIRECT_RADIUS_KM.get(hazard_kind, 100.0)
    contextual_radius = direct_radius * 2
    if distance <= direct_radius:
        level = "direct"
    elif distance <= contextual_radius:
        level = "contextual"
    else:
        return {
            "passed": False,
            "distanceKm": round(distance, 1),
            "reason": (
                f"{city} is {distance:.1f} km from the nearest provider geometry, "
                f"outside the {contextual_radius:.0f} km evidence radius."
            ),
        }
    return {
        "passed": True,
        "level": level,
        "distanceKm": round(distance, 1),
        "reason": (
            f"{city} is {distance:.1f} km from the nearest provider geometry "
            f"({level} spatial evidence)."
        ),
    }


def _type_evidence(event: Dict[str, Any], family: str) -> Dict[str, Any]:
    hazard_kind = str(event.get("hazardKind") or "")
    if family in _DIRECT_FAMILIES.get(hazard_kind, set()):
        return {
            "passed": True,
            "level": "direct",
            "reason": f"{hazard_kind} is directly compatible with {family}.",
        }
    if family in _CONTEXTUAL_FAMILIES.get(hazard_kind, set()):
        return {
            "passed": True,
            "level": "contextual",
            "reason": (
                f"{hazard_kind} and {family} share a weather context, but the "
                "market does not directly measure the hazard itself."
            ),
        }
    return {
        "passed": False,
        "reason": f"{hazard_kind or 'unknown hazard'} is not compatible with {family or 'unknown market family'}.",
    }


def _metric_evidence(event: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    family = str(market.get("metricType") or market.get("marketFamily") or "").strip()
    metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
    hazard_kind = str(event.get("hazardKind") or "")
    if metrics.get("kind") == "climate-anomaly" and family in {
        "highest_temperature",
        "lowest_temperature",
        "precipitation",
    }:
        unit = str(metrics.get("unit") or "").strip()
        anomaly = metrics.get("anomaly")
        if (
            family == "highest_temperature"
            and isinstance(anomaly, (int, float))
            and float(anomaly) < 0
        ) or (
            family == "lowest_temperature"
            and isinstance(anomaly, (int, float))
            and float(anomaly) > 0
        ):
            return {
                "passed": False,
                "reason": (
                    f"Anomaly direction {float(anomaly):g} {unit or 'units'} is incompatible "
                    f"with {family}."
                ),
            }
        return {
            "passed": bool(unit),
            "level": "direct" if unit else "contextual",
            "reason": (
                f"Hazard supplies a numeric {metrics.get('variable')} value and anomaly in {unit}."
                if unit
                else "Climate anomaly has no comparable unit."
            ),
            "hazardValue": metrics.get("value"),
            "hazardAnomaly": metrics.get("anomaly"),
            "unit": unit or None,
        }
    if hazard_kind in {"extreme-heat", "extreme-cold"} and family in {
        "highest_temperature",
        "lowest_temperature",
    }:
        return {
            "passed": True,
            "level": "contextual",
            "reason": (
                "The official alert and market both concern temperature, but "
                "the alert payload does not expose a comparable settlement threshold."
            ),
        }
    if hazard_kind == "flood" and family == "precipitation":
        return {
            "passed": True,
            "level": "contextual",
            "reason": (
                "Precipitation can provide context for a flood, but a rainfall "
                "settlement metric does not directly measure flood occurrence or impact."
            ),
        }
    if hazard_kind == "severe-storm" and family in {"weather_binary", "precipitation"}:
        return {
            "passed": True,
            "level": "contextual",
            "reason": "The market metric is weather-related but not a structured storm-intensity metric.",
        }
    if hazard_kind == "tropical-cyclone" and family == "hurricane":
        return {
            "passed": True,
            "level": "contextual",
            "reason": (
                "Both records concern a tropical cyclone, but the market payload "
                "does not expose structured landfall, wind or storm-identity settlement fields."
            ),
        }
    if hazard_kind in {"tornado", "volcano"} and family == hazard_kind:
        return {
            "passed": True,
            "level": "direct",
            "reason": f"Both event and market measure {hazard_kind} occurrence.",
        }
    return {
        "passed": False,
        "reason": "No comparable structured settlement metric is available.",
    }


def _leading_outcome(market: Dict[str, Any]) -> Dict[str, Any] | None:
    top = market.get("topBin") if isinstance(market.get("topBin"), dict) else None
    if not top:
        bins = [item for item in (market.get("bins") or []) if isinstance(item, dict)]
        top = max(
            bins,
            key=lambda item: float(item.get("midPriceYes") or -1),
            default=None,
        )
    return top


def _link_candidate(
    event: Dict[str, Any],
    city_row: Dict[str, Any],
    market: Dict[str, Any],
) -> tuple[Dict[str, Any] | None, Dict[str, Any]]:
    family = str(market.get("marketFamily") or market.get("metricType") or "").strip()
    evidence = {
        "type": _type_evidence(event, family),
        "space": _space_evidence(
            event,
            city=str(city_row.get("city") or city_row.get("region") or "market target"),
            lon=city_row.get("lon"),
            lat=city_row.get("lat"),
        ),
        "time": _time_evidence(event, market),
        "metric": _metric_evidence(event, market),
    }
    if not all(item.get("passed") for item in evidence.values()):
        return None, evidence
    level = (
        "direct"
        if all(item.get("level", "direct") == "direct" for item in evidence.values())
        else "contextual"
    )
    top = _leading_outcome(market)
    bid = top.get("bestBidYes") if top else None
    ask = top.get("bestAskYes") if top else None
    spread = (
        round(float(ask) - float(bid), 4)
        if ask is not None and bid is not None
        else None
    )
    score = 1.0 if level == "direct" else 0.7
    if evidence["space"].get("level") == "contextual":
        score -= 0.1
    return {
        "marketId": top.get("marketId") if top else None,
        "eventSlug": market.get("eventSlug"),
        "title": market.get("eventTitle") or (top or {}).get("label"),
        "url": market.get("marketUrl"),
        "marketFamily": family,
        "relationship": level,
        "matchScore": round(score, 2),
        "matchReasons": evidence,
        "matchedAt": _iso_now(),
        "linkerVersion": LINKER_VERSION,
        "target": {
            "cityId": city_row.get("cityId"),
            "city": city_row.get("city"),
            "country": city_row.get("country"),
            "lat": city_row.get("lat"),
            "lon": city_row.get("lon"),
            "date": evidence["time"].get("targetDate"),
        },
        "quote": {
            "leadingOutcome": (top or {}).get("label"),
            "probability": (top or {}).get("midPriceYes"),
            "bestBid": bid,
            "bestAsk": ask,
            "spread": spread,
            "bookStatus": (top or {}).get("bookStatus"),
            "priceSource": (top or {}).get("priceSource"),
            "updatedAt": market.get("updatedAt"),
        },
        "oracle": {
            "status": "unknown",
            "reason": "Weather map payload does not expose an oracle lifecycle record.",
        },
    }, evidence


def link_weather_markets(
    event: Dict[str, Any],
    weather_map_payload: Dict[str, Any],
    *,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    links: list[Dict[str, Any]] = []
    rejected = 0
    candidate_count = 0
    seen: set[str] = set()
    for city_row in weather_map_payload.get("items") or []:
        if not isinstance(city_row, dict):
            continue
        markets = city_row.get("markets") or []
        for market in markets:
            if not isinstance(market, dict) or market.get("eventStatus") == "inactive":
                continue
            identity = str(market.get("eventSlug") or market.get("marketUrl") or "")
            if not identity or identity in seen:
                continue
            seen.add(identity)
            candidate_count += 1
            linked, _evidence = _link_candidate(event, city_row, market)
            if linked is None:
                rejected += 1
                continue
            links.append(linked)
    links.sort(
        key=lambda item: (
            item.get("relationship") == "direct",
            float(item.get("matchScore") or 0),
            str((item.get("quote") or {}).get("updatedAt") or ""),
        ),
        reverse=True,
    )
    bounded_limit = max(1, min(25, int(limit)))
    limitations = [
        "Only structured weather-market targets available in the current weather map snapshot are evaluated.",
        "Title similarity alone never creates a link.",
        "Oracle lifecycle is reported as unknown until a dedicated oracle join is available.",
    ]
    if not weather_map_payload.get("items"):
        limitations.insert(
            0,
            "No cached structured weather-market snapshot was available; no live provider refresh was attempted.",
        )
    return {
        "schemaVersion": LINKER_SCHEMA_VERSION,
        "generatedAt": _iso_now(),
        "eventId": event.get("id"),
        "linkerVersion": LINKER_VERSION,
        "markets": links[:bounded_limit],
        "counts": {
            "candidates": candidate_count,
            "matched": len(links),
            "returned": min(len(links), bounded_limit),
            "rejected": rejected,
        },
        "limitations": limitations,
    }


def related_weather_markets_snapshot(
    *,
    event_id: str,
    natural_hazards_payload: Dict[str, Any],
    weather_map_payload: Dict[str, Any],
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any] | None:
    event = next(
        (
            item
            for item in natural_hazards_payload.get("events") or []
            if isinstance(item, dict) and str(item.get("id") or "") == str(event_id)
        ),
        None,
    )
    if event is None:
        return None
    return link_weather_markets(event, weather_map_payload, limit=limit)
