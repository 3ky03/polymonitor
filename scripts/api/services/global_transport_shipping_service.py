from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import threading
from collections import Counter, defaultdict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
)


PANEL_ID = "global-transport-shipping"
GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE = "snapshot:transport:global-shipping"
GLOBAL_TRANSPORT_CACHE_KEY = "panel-v3"
AISSTREAM_SNAPSHOT_NAMESPACE = "snapshot:transport:aisstream"
AISSTREAM_CACHE_KEY = "sample-v1"
OPENSKY_SNAPSHOT_NAMESPACE = "snapshot:transport:opensky"
OPENSKY_CACHE_KEY = "live-v1"
OPENSKY_TOKEN_CACHE_KEY = "token-v1"
ADSB_SNAPSHOT_NAMESPACE = "snapshot:transport:adsb"
ADSB_CACHE_KEY = "live-v1"
DEFAULT_LIMIT = 14
DEFAULT_TTL_SECONDS = 900
DEFAULT_AISSTREAM_SAMPLE_INTERVAL_SECONDS = 21600
DEFAULT_OPENSKY_SAMPLE_INTERVAL_SECONDS = 900
DEFAULT_ADSB_SAMPLE_INTERVAL_SECONDS = 1800
AVIATION_ROUTE_LAYER_LIMIT = 360
AVIATION_FLIGHT_LAYER_LIMIT = 140
EVIDENCE_SCHEMA_VERSION = "air-evidence-v1"
AVIATION_VIEWPORT_SCHEMA_VERSION = "aviation-viewport.v1"

OPENFLIGHTS_AIRPORTS_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"
OPENFLIGHTS_ROUTES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat"
OPENFLIGHTS_AIRLINES_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
TRANSITLAND_ATLAS_URL = "https://raw.githubusercontent.com/transitland/transitland-atlas/master/feeds/gtfs-source-feeds.transit.land.dmfr.json"
TRANSITLAND_ATLAS_INDEX_URL = "https://api.github.com/repos/transitland/transitland-atlas/contents/feeds"
AISSTREAM_DOC_URL = "https://aisstream.io/documentation"
OPENSKY_AUTH_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
OPENSKY_DOC_URL = "https://openskynetwork.github.io/opensky-api/rest.html"
ADSB_LOL_BASE_URL = "https://api.adsb.lol/v2"
ADSB_LOL_DOC_URL = "https://www.adsb.lol/docs/open-data/api/"
OPENSKY_REGIONS = [
    {"id": "us-east", "label": "US East", "lamin": 24.0, "lomin": -88.0, "lamax": 49.5, "lomax": -66.0},
    {"id": "europe", "label": "Europe", "lamin": 35.0, "lomin": -12.0, "lamax": 61.0, "lomax": 32.0},
    {"id": "gulf", "label": "Gulf", "lamin": 18.0, "lomin": 34.0, "lamax": 33.0, "lomax": 62.0},
    {"id": "east-asia", "label": "East Asia", "lamin": 20.0, "lomin": 102.0, "lamax": 45.0, "lomax": 141.0},
    {"id": "southeast-asia", "label": "SE Asia", "lamin": -8.0, "lomin": 95.0, "lamax": 18.0, "lomax": 125.0},
]

_LIVE_REFRESH_LOCK = threading.Lock()
_LIVE_REFRESHING: set[str] = set()


@dataclass(frozen=True)
class GlobalTransportShippingDependencies:
    application: Any
    utc_now_iso: Callable[..., Any] | None
    http_text_get: Callable[..., Any] | None
    http_json_get: Callable[..., Any] | None
    http_form_post: Callable[..., Any] | None
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    snapshot_store: Any
    search_markets: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> GlobalTransportShippingDependencies:
        return cls(
            application=resolve_optional_service_value(context, "app"),
            utc_now_iso=resolve_optional_service_callable(
                context,
                "utc_now_iso",
            ),
            http_text_get=resolve_optional_service_callable(
                context,
                "http_text_get",
            ),
            http_json_get=resolve_optional_service_callable(
                context,
                "http_json_get",
            ),
            http_form_post=resolve_optional_service_callable(
                context,
                "http_form_post",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
            search_markets=resolve_optional_service_callable(
                context,
                "search_markets",
            ),
        )


GlobalTransportShippingContext = (
    Mapping[str, Any] | GlobalTransportShippingDependencies
)


def _dependencies(
    context: GlobalTransportShippingContext,
) -> GlobalTransportShippingDependencies:
    if isinstance(context, GlobalTransportShippingDependencies):
        return context
    return GlobalTransportShippingDependencies.from_context(context)


def _utc_now_iso(
    ctx: GlobalTransportShippingContext | None = None,
) -> str:
    if ctx is not None:
        getter = _dependencies(ctx).utc_now_iso
        if getter is not None:
            return str(getter())
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _null(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text == r"\N" else text


def _float(value: Any) -> Optional[float]:
    try:
        text = _null(value)
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_seconds(value: Any) -> Optional[float]:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _local_openflights_path(filename: str) -> Optional[Path]:
    roots = [
        os.environ.get("POLYDATA_OPENFLIGHTS_ROOT"),
        "/opt/openflights",
        "/opt/polyData/openflights",
    ]
    for root in roots:
        if not root:
            continue
        path = Path(root).expanduser() / "data" / filename
        if path.exists():
            return path
    return None


def _http_text(
    ctx: GlobalTransportShippingContext,
    url: str,
    *,
    timeout: int = 18,
) -> str:
    getter = _dependencies(ctx).http_text_get
    if getter is not None:
        return str(getter(url, timeout=timeout, headers={"User-Agent": "polydata-global-transport/1.0"}))
    raise RuntimeError("http_text_get unavailable")


def _http_json_get(
    ctx: GlobalTransportShippingContext,
    url: str,
    *,
    params: Dict[str, Any] | None = None,
    timeout: int = 18,
    headers: Dict[str, str] | None = None,
) -> Any:
    getter = _dependencies(ctx).http_json_get
    if getter is not None:
        return getter(url, params=params, timeout=timeout, headers=headers)
    import requests

    response = requests.get(url, params=params, timeout=timeout, headers=headers)
    response.raise_for_status()
    return response.json()


def _http_form_post(
    ctx: GlobalTransportShippingContext,
    url: str,
    *,
    data: Dict[str, Any],
    timeout: int = 18,
    headers: Dict[str, str] | None = None,
) -> Any:
    poster = _dependencies(ctx).http_form_post
    if poster is not None:
        return poster(url, data=data, timeout=timeout, headers=headers)
    import requests

    response = requests.post(url, data=data, timeout=timeout, headers=headers)
    response.raise_for_status()
    return response.json()


def _read_openflights_text(ctx: dict, filename: str, url: str) -> tuple[str, str]:
    local_path = _local_openflights_path(filename)
    if local_path is not None:
        return local_path.read_text(encoding="utf-8", errors="replace"), f"file://{local_path}"
    return _http_text(ctx, url), url


def _csv_rows(text: str) -> Iterable[List[str]]:
    return csv.reader(io.StringIO(text))


def _parse_airports(text: str) -> Dict[str, Dict[str, Any]]:
    airports: Dict[str, Dict[str, Any]] = {}
    for row in _csv_rows(text):
        if len(row) < 14:
            continue
        airport_id = _null(row[0])
        iata = _null(row[4])
        icao = _null(row[5])
        item = {
            "id": airport_id,
            "name": _null(row[1]),
            "city": _null(row[2]),
            "country": _null(row[3]),
            "iata": iata,
            "icao": icao,
            "lat": _float(row[6]),
            "lon": _float(row[7]),
            "type": _null(row[12]) or "airport",
            "source": _null(row[13]) or "OpenFlights",
        }
        for key in (airport_id, iata, icao):
            if key:
                airports[key] = item
    return airports


def _parse_airlines(text: str) -> Dict[str, Dict[str, Any]]:
    airlines: Dict[str, Dict[str, Any]] = {}
    for row in _csv_rows(text):
        if len(row) < 8:
            continue
        airline_id = _null(row[0])
        iata = _null(row[3])
        icao = _null(row[4])
        item = {
            "id": airline_id,
            "name": _null(row[1]),
            "country": _null(row[6]),
            "active": _null(row[7]) == "Y",
        }
        for key in (airline_id, iata, icao):
            if key:
                airlines[key] = item
    return airlines


def _parse_routes(text: str, airports: Dict[str, Dict[str, Any]], airlines: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    airport_counts: Counter[str] = Counter()
    country_counts: Counter[str] = Counter()
    country_corridors: Counter[tuple[str, str]] = Counter()
    airline_counts: Counter[str] = Counter()
    equipment_counts: Counter[str] = Counter()
    route_edges: List[Dict[str, Any]] = []
    valid_routes = 0
    direct_routes = 0

    for row in _csv_rows(text):
        if len(row) < 9:
            continue
        airline_code = _null(row[0])
        airline_id = _null(row[1])
        src_key = _null(row[3]) or _null(row[2])
        dst_key = _null(row[5]) or _null(row[4])
        src = airports.get(src_key) or airports.get(_null(row[2]))
        dst = airports.get(dst_key) or airports.get(_null(row[4]))
        if not src or not dst:
            continue
        valid_routes += 1
        if _null(row[7]) in {"", "0"}:
            direct_routes += 1
        src_id = str(src.get("id") or src.get("iata") or src_key)
        dst_id = str(dst.get("id") or dst.get("iata") or dst_key)
        airport_counts[src_id] += 1
        airport_counts[dst_id] += 1
        for country in (src.get("country"), dst.get("country")):
            if country:
                country_counts[str(country)] += 1
        country_pair = tuple(sorted([str(src.get("country") or "Unknown"), str(dst.get("country") or "Unknown")]))
        country_corridors[country_pair] += 1
        airline = airlines.get(airline_id) or airlines.get(airline_code) or {"name": airline_code or "Unknown"}
        airline_counts[str(airline.get("name") or airline_code or "Unknown")] += 1
        for code in _null(row[8]).split():
            if code:
                equipment_counts[code] += 1
        src_lat = src.get("lat")
        src_lon = src.get("lon")
        dst_lat = dst.get("lat")
        dst_lon = dst.get("lon")
        src_code = src.get("iata") or src.get("icao") or src_id
        dst_code = dst.get("iata") or dst.get("icao") or dst_id
        airport_counts[str(src_code)] += 1
        airport_counts[str(dst_code)] += 1
        if (
            isinstance(src_lat, (int, float))
            and isinstance(src_lon, (int, float))
            and isinstance(dst_lat, (int, float))
            and isinstance(dst_lon, (int, float))
            and src_code
            and dst_code
            and src_code != dst_code
        ):
            route_edges.append(
                {
                    "fromCode": src_code,
                    "toCode": dst_code,
                    "fromName": src.get("name") or src_code,
                    "toName": dst.get("name") or dst_code,
                    "fromCity": src.get("city"),
                    "toCity": dst.get("city"),
                    "fromCountry": src.get("country"),
                    "toCountry": dst.get("country"),
                    "fromLat": src_lat,
                    "fromLon": src_lon,
                    "toLat": dst_lat,
                    "toLon": dst_lon,
                    "airline": airline.get("name") or airline_code or "Unknown",
                    "equipment": _null(row[8]),
                    "stops": _null(row[7]) or "0",
                }
            )

    hubs = []
    seen_hubs: set[str] = set()
    for airport_id, count in airport_counts.most_common(48):
        airport = airports.get(airport_id)
        if not airport:
            continue
        key = str(airport.get("id") or airport_id)
        if key in seen_hubs:
            continue
        seen_hubs.add(key)
        hubs.append({**airport, "routeCount": count})
        if len(hubs) >= 24:
            break

    corridors = [
        {"sourceCountry": pair[0], "destCountry": pair[1], "routeCount": count}
        for pair, count in country_corridors.most_common(10)
    ]
    top_route_edges: List[Dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    airport_route_limits: Counter[str] = Counter()
    country_pair_limits: Counter[tuple[str, str]] = Counter()
    route_edges.sort(
        key=lambda edge: (
            airport_counts[str(edge.get("fromCode") or "")]
            + airport_counts[str(edge.get("toCode") or "")],
            edge.get("fromCountry") != edge.get("toCountry"),
        ),
        reverse=True,
    )
    for edge in route_edges:
        pair = tuple(sorted([str(edge.get("fromCode") or ""), str(edge.get("toCode") or "")]))
        if not pair[0] or not pair[1] or pair in seen_pairs:
            continue
        country_pair = tuple(sorted([str(edge.get("fromCountry") or "Unknown"), str(edge.get("toCountry") or "Unknown")]))
        from_code = str(edge.get("fromCode") or "")
        to_code = str(edge.get("toCode") or "")
        if airport_route_limits[from_code] >= 28 or airport_route_limits[to_code] >= 28:
            continue
        if country_pair_limits[country_pair] >= 34:
            continue
        seen_pairs.add(pair)
        airport_route_limits[from_code] += 1
        airport_route_limits[to_code] += 1
        country_pair_limits[country_pair] += 1
        from_score = airport_counts[from_code]
        to_score = airport_counts[to_code]
        traffic_score = min(100, max(18, round((from_score + to_score) / 35)))
        is_international = edge.get("fromCountry") != edge.get("toCountry")
        route_key = f"{edge.get('fromCode')}-{edge.get('toCode')}"
        phase_seed = int(hashlib.sha1(route_key.encode("utf-8")).hexdigest()[:4], 16)
        top_route_edges.append(
            {
                **edge,
                "id": _source_hash(route_key),
                "corridor": f"{edge.get('fromCountry') or 'Unknown'} / {edge.get('toCountry') or 'Unknown'}",
                "trafficScore": traffic_score,
                "riskScore": min(88, max(8, 18 + (12 if is_international else 0) + round(traffic_score * 0.28))),
                "status": "watch" if is_international and traffic_score >= 70 else "normal",
                "layer": "trunk" if traffic_score >= 70 else ("international" if is_international else "regional"),
                "phase": round((phase_seed % 1000) / 1000, 3),
                "speed": round(0.055 + ((phase_seed % 7) * 0.007), 3),
            }
        )
        if len(top_route_edges) >= AVIATION_ROUTE_LAYER_LIMIT:
            break

    return {
        "routeCount": valid_routes,
        "directRouteCount": direct_routes,
        "countryCount": len(country_counts),
        "topHubs": hubs,
        "topRoutes": top_route_edges,
        "topCountryCorridors": corridors,
        "topAirlines": [{"name": name, "routeCount": count} for name, count in airline_counts.most_common(8)],
        "topEquipment": [{"code": code, "routeCount": count} for code, count in equipment_counts.most_common(8)],
    }


def _route_risk_sources(route: Dict[str, Any]) -> List[str]:
    sources: List[str] = []
    from_country = str(route.get("fromCountry") or "")
    to_country = str(route.get("toCountry") or "")
    traffic_score = int(route.get("trafficScore") or 0)
    risk_score = int(route.get("riskScore") or 0)
    conflict_watch = {
        "Iran",
        "Iraq",
        "Israel",
        "Lebanon",
        "Palestine",
        "Russia",
        "Ukraine",
        "Syria",
        "Yemen",
        "Sudan",
        "Libya",
        "Pakistan",
    }
    weather_watch = {
        "United States",
        "Mexico",
        "Japan",
        "China",
        "Philippines",
        "India",
        "Bangladesh",
        "Vietnam",
        "Indonesia",
        "Australia",
    }
    if route.get("layer") in {"trunk", "international"} or traffic_score >= 55 or from_country != to_country:
        sources.append("corridor")
    if from_country in conflict_watch or to_country in conflict_watch:
        sources.append("conflict")
    if from_country in weather_watch or to_country in weather_watch or risk_score >= 62:
        sources.append("weather")
    return list(dict.fromkeys(sources))


def _route_risk_reason(route: Dict[str, Any], sources: List[str]) -> str:
    pieces = []
    if "corridor" in sources:
        pieces.append(f"{route.get('layer') or 'route'} corridor / {route.get('trafficScore') or 0} traffic")
    if "conflict" in sources:
        pieces.append("country-pair intersects conflict watchlist")
    if "weather" in sources:
        pieces.append("endpoint region has weather disruption exposure")
    return "; ".join(pieces) or "baseline OpenFlights route edge"


def _trend(seed: str, *, length: int = 9, floor: int = 16, ceiling: int = 92) -> List[int]:
    base = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16)
    values = []
    for index in range(length):
        step = ((base >> (index % 12)) + index * 17) % 29
        values.append(max(floor, min(ceiling, floor + step + index * 3)))
    return values


def _build_aviation_layer(route_stats: Dict[str, Any], *, generated_at: str, source_url: str, live_aircraft_status: Dict[str, Any] | None = None) -> Dict[str, Any]:
    hubs = []
    for hub in route_stats.get("topHubs", [])[:24]:
        code = hub.get("iata") or hub.get("icao") or hub.get("id")
        route_count = int(hub.get("routeCount") or 0)
        hubs.append(
            {
                "code": code,
                "name": hub.get("name") or code,
                "city": hub.get("city"),
                "country": hub.get("country"),
                "lat": hub.get("lat"),
                "lon": hub.get("lon"),
                "routeCount": route_count,
                "status": "watch" if route_count >= 1200 else "normal",
                "riskScore": min(92, max(12, round(route_count / 18))),
                "delayScore": min(88, max(5, round(route_count / 34))),
                "trend": _trend(str(code or hub.get("name") or "hub"), floor=18, ceiling=86),
                "source": "OpenFlights",
                "sourceUrl": source_url,
                "evidenceType": "airport",
            }
        )
    routes = route_stats.get("topRoutes", [])[:AVIATION_ROUTE_LAYER_LIMIT]
    enriched_routes: List[Dict[str, Any]] = []
    for route in routes:
        risk_sources = _route_risk_sources(route)
        risk_reason = _route_risk_reason(route, risk_sources)
        confidence = 0.72
        if route.get("layer") == "trunk":
            confidence += 0.08
        if "conflict" in risk_sources or "weather" in risk_sources:
            confidence += 0.04
        enriched_routes.append(
            {
                **route,
                "riskSources": risk_sources,
                "riskReason": risk_reason,
                "confidence": round(min(0.92, confidence), 2),
                "source": "OpenFlights",
                "sourceUrl": source_url,
                "evidenceType": "air_route",
                "updatedAt": generated_at,
                "trend": _trend(f"{route.get('fromCode')}-{route.get('toCode')}", floor=12, ceiling=94),
                "relatedPolymarketMarketIds": [],
            }
        )
    routes = enriched_routes
    flights = []
    for index, route in enumerate(routes[:AVIATION_FLIGHT_LAYER_LIMIT]):
        callsign_seed = str(route.get("airline") or "AIR").upper().replace(" ", "")[:3] or "AIR"
        flights.append(
            {
                "id": f"flight-{route.get('id') or index}",
                "callsign": f"{callsign_seed}{100 + index}",
                "fromCode": route.get("fromCode"),
                "toCode": route.get("toCode"),
                "fromLon": route.get("fromLon"),
                "fromLat": route.get("fromLat"),
                "toLon": route.get("toLon"),
                "toLat": route.get("toLat"),
                "phase": route.get("phase", 0),
                "speed": route.get("speed", 0.06),
                "status": route.get("status", "normal"),
                "riskScore": route.get("riskScore", 0),
                "trafficScore": route.get("trafficScore", 0),
                "riskSources": route.get("riskSources") or [],
                "riskReason": route.get("riskReason"),
                "layer": route.get("layer"),
                "source": "OpenFlights",
                "sourceUrl": source_url,
            }
        )
    airline_rows = []
    for index, row in enumerate(route_stats.get("topAirlines", [])[:8]):
        route_count = int(row.get("routeCount") or 0)
        airline_rows.append(
            {
                **row,
                "status": "watch" if route_count >= 1200 else "normal",
                "exposureScore": min(100, max(8, round(route_count / 12))),
                "trend": _trend(str(row.get("name") or index), floor=10, ceiling=90),
                "source": "OpenFlights",
                "sourceUrl": source_url,
            }
        )
    ops_rows = [
        {
            "code": hub.get("code"),
            "name": hub.get("name"),
            "city": hub.get("city"),
            "country": hub.get("country"),
            "status": hub.get("status"),
            "riskScore": hub.get("riskScore"),
            "delayScore": hub.get("delayScore"),
            "routeCount": hub.get("routeCount"),
            "trend": hub.get("trend"),
            "source": hub.get("source"),
            "sourceUrl": hub.get("sourceUrl"),
        }
        for hub in hubs[:8]
    ]
    news_rows = [
        {
            "title": f"{route.get('fromCode')} -> {route.get('toCode')} {route.get('layer')} corridor",
            "corridor": route.get("corridor"),
            "status": "watch" if route.get("riskSources") else route.get("status"),
            "riskScore": route.get("riskScore"),
            "riskSources": route.get("riskSources"),
            "riskReason": route.get("riskReason"),
            "source": "OpenFlights",
            "sourceUrl": source_url,
            "updatedAt": generated_at,
        }
        for route in routes[:8]
    ]
    return {
        "generatedAt": generated_at,
        "mode": "live-aircraft" if (live_aircraft_status or {}).get("aircraft") else "seeded-route-graph",
        "hubs": hubs,
        "routes": routes,
        "flights": flights,
        "liveFlights": (live_aircraft_status or {}).get("aircraft") or [],
        "ops": ops_rows,
        "airlines": airline_rows,
        "news": news_rows,
    }


def _evidence_record(
    *,
    evidence_type: str,
    entity: str,
    lat: Any = None,
    lon: Any = None,
    route: Any = None,
    severity: str = "normal",
    confidence: float = 0.7,
    source: str,
    source_url: str,
    updated_at: str,
    related_market_ids: Optional[List[Any]] = None,
    risk_sources: Optional[List[str]] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "type": evidence_type,
        "entity": entity,
        "lat": lat,
        "lon": lon,
        "route": route,
        "severity": severity,
        "confidence": confidence,
        "source": source,
        "sourceUrl": source_url,
        "updatedAt": updated_at,
        "relatedMarketIds": related_market_ids or [],
        "riskSources": risk_sources or [],
        "reason": reason,
    }


def _build_evidence_payload(aviation: Dict[str, Any], items: List[Dict[str, Any]], *, generated_at: str) -> Dict[str, Any]:
    route_records = [
        _evidence_record(
            evidence_type="air_route",
            entity=f"{route.get('fromCode')}->{route.get('toCode')}",
            route=[route.get("fromCode"), route.get("toCode")],
            severity="watch" if route.get("riskSources") else "normal",
            confidence=float(route.get("confidence") or 0.72),
            source=str(route.get("source") or "OpenFlights"),
            source_url=str(route.get("sourceUrl") or OPENFLIGHTS_ROUTES_URL),
            updated_at=generated_at,
            related_market_ids=route.get("relatedPolymarketMarketIds") or [],
            risk_sources=route.get("riskSources") or [],
            reason=route.get("riskReason"),
        )
        for route in aviation.get("routes", [])[:96]
    ]
    ops_records = [
        _evidence_record(
            evidence_type="airport",
            entity=str(row.get("code") or row.get("name") or "Airport"),
            severity=str(row.get("status") or "normal"),
            confidence=0.78,
            source=str(row.get("source") or "OpenFlights"),
            source_url=str(row.get("sourceUrl") or OPENFLIGHTS_ROUTES_URL),
            updated_at=generated_at,
            reason=f"{row.get('routeCount') or 0} route edges / delay proxy {row.get('delayScore') or 0}",
        )
        for row in aviation.get("ops", [])
    ]
    live_records = [
        _evidence_record(
            evidence_type="live_aircraft",
            entity=str(row.get("callsign") or row.get("icao24") or "aircraft"),
            lat=row.get("lat"),
            lon=row.get("lon"),
            severity=str(row.get("status") or "normal"),
            confidence=0.82,
            source=str(row.get("source") or "OpenSky"),
            source_url=str(row.get("sourceUrl") or OPENSKY_DOC_URL),
            updated_at=str(row.get("updatedAt") or generated_at),
            risk_sources=[str(row.get("source") or "opensky").lower(), "aircraft"],
            reason=f"{row.get('regionLabel') or row.get('region') or 'region'} live aircraft state / {row.get('originCountry') or 'unknown'}",
        )
        for row in aviation.get("liveFlights", [])[:96]
    ]
    risk_records = [
        _evidence_record(
            evidence_type=str(item.get("evidenceType") or item.get("topic") or "transport"),
            entity=str(item.get("entity") or item.get("title") or "transport"),
            severity=str(item.get("severity") or "normal"),
            confidence=float(item.get("confidence") or 0.6),
            source=str(item.get("evidenceType") or item.get("source") or "transport"),
            source_url=str(item.get("sourceUrl") or OPENFLIGHTS_ROUTES_URL),
            updated_at=str(item.get("eventTime") or generated_at),
            related_market_ids=item.get("relatedPolymarketMarketIds") or [],
            risk_sources=list(item.get("tags") or []),
            reason=str(item.get("summary") or item.get("title") or ""),
        )
        for item in items
    ]
    return {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "routes": route_records,
        "risks": risk_records + live_records,
        "ops": ops_records,
    }


def _parse_transitland_payloads(payloads: Iterable[Dict[str, Any]], *, catalog_file_count: int = 0, scanned_file_count: int = 0) -> Dict[str, Any]:
    operators_by_id: Dict[str, Dict[str, Any]] = {}
    spec_counts: Counter[str] = Counter()
    authorization_counts: Counter[str] = Counter()
    country_hint_counts: Counter[str] = Counter()
    sample_feeds: List[Dict[str, Any]] = []

    for payload in payloads:
        feeds = payload.get("feeds") if isinstance(payload.get("feeds"), list) else []
        for feed in feeds:
            if not isinstance(feed, dict):
                continue
            spec = str(feed.get("spec") or "unknown")
            spec_counts[spec] += 1
            auth = feed.get("authorization")
            auth_type = "open"
            if isinstance(auth, dict) and auth.get("type"):
                auth_type = str(auth.get("type"))
            authorization_counts[auth_type] += 1
            feed_id = str(feed.get("id") or "")
            parts = feed_id.split("-")
            if len(parts) >= 2:
                country_hint_counts[parts[-1]] += 1
            for operator in feed.get("operators") or []:
                if not isinstance(operator, dict):
                    continue
                operator_id = str(operator.get("onestop_id") or operator.get("id") or operator.get("name") or "")
                if operator_id:
                    operators_by_id[operator_id] = operator
            if len(sample_feeds) >= 8:
                continue
            urls = feed.get("urls") if isinstance(feed.get("urls"), dict) else {}
            sample_feeds.append(
                {
                    "id": feed_id,
                    "spec": spec,
                    "url": urls.get("static_current") or urls.get("realtime_vehicle_positions") or urls.get("realtime_trip_updates"),
                    "authorization": auth_type,
                    "operators": [op.get("name") for op in feed.get("operators") or [] if isinstance(op, dict) and op.get("name")][:3],
                }
            )

    return {
        "feedCount": sum(spec_counts.values()),
        "operatorCount": len(operators_by_id),
        "catalogFileCount": catalog_file_count,
        "scannedFileCount": scanned_file_count,
        "specCounts": dict(spec_counts),
        "authorizationCounts": dict(authorization_counts),
        "topCountryHints": [{"code": key, "feedCount": value} for key, value in country_hint_counts.most_common(8)],
        "sampleFeeds": sample_feeds,
    }


def _select_evenly(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if limit <= 0 or len(rows) <= limit:
        return rows
    if limit == 1:
        return [rows[0]]
    step = (len(rows) - 1) / float(limit - 1)
    indexes = sorted({round(index * step) for index in range(limit)})
    return [rows[index] for index in indexes]


def _fetch_transitland_catalog(ctx: dict) -> tuple[Dict[str, Any], str]:
    explicit_urls = [
        url.strip()
        for url in str(os.environ.get("POLYDATA_TRANSITLAND_ATLAS_URLS") or "").split(",")
        if url.strip()
    ]
    legacy_url = str(os.environ.get("POLYDATA_TRANSITLAND_ATLAS_URL") or "").strip()
    if explicit_urls or legacy_url:
        urls = explicit_urls or [legacy_url]
        payloads = []
        for url in urls:
            parsed = json.loads(_http_text(ctx, url, timeout=20))
            if isinstance(parsed, dict):
                payloads.append(parsed)
        return _parse_transitland_payloads(payloads, catalog_file_count=len(urls), scanned_file_count=len(payloads)), urls[0] if urls else TRANSITLAND_ATLAS_URL

    index_url = os.environ.get("POLYDATA_TRANSITLAND_ATLAS_INDEX_URL", TRANSITLAND_ATLAS_INDEX_URL)
    file_limit = max(1, min(int(os.environ.get("POLYDATA_TRANSITLAND_ATLAS_FILE_LIMIT", "48") or 48), 120))
    index_payload = json.loads(_http_text(ctx, index_url, timeout=20))
    files = []
    if isinstance(index_payload, list):
        for item in index_payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            url = str(item.get("download_url") or "")
            if name.endswith(".dmfr.json") and url:
                files.append({"name": name, "downloadUrl": url})
    selected = _select_evenly(files, file_limit)
    payloads = []
    for item in selected:
        parsed = json.loads(_http_text(ctx, item["downloadUrl"], timeout=20))
        if isinstance(parsed, dict):
            payloads.append(parsed)
    if payloads:
        return _parse_transitland_payloads(payloads, catalog_file_count=len(files), scanned_file_count=len(payloads)), index_url
    parsed = json.loads(_http_text(ctx, TRANSITLAND_ATLAS_URL, timeout=20))
    payloads = [parsed] if isinstance(parsed, dict) else []
    return _parse_transitland_payloads(payloads, catalog_file_count=1, scanned_file_count=len(payloads)), TRANSITLAND_ATLAS_URL


async def _sample_aisstream(api_key: str, *, timeout_seconds: int) -> Dict[str, Any]:
    import asyncio
    import websockets

    uri = os.environ.get("POLYDATA_AISSTREAM_WS_URL", "wss://stream.aisstream.io/v0/stream")
    bbox = json.loads(os.environ.get("POLYDATA_AISSTREAM_BBOX_JSON", "[[[-180,-90],[180,90]]]"))
    subscription = {
        "APIKey": api_key,
        "BoundingBoxes": bbox,
        "FilterMessageTypes": ["PositionReport"],
    }
    vessels = 0
    countries: Counter[str] = Counter()
    started = datetime.now(timezone.utc)
    async with websockets.connect(uri, open_timeout=timeout_seconds) as websocket:
        await websocket.send(json.dumps(subscription))
        while (datetime.now(timezone.utc) - started).total_seconds() < timeout_seconds:
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                break
            vessels += 1
            try:
                payload = json.loads(message)
                meta = payload.get("MetaData") if isinstance(payload, dict) else {}
                country = str((meta or {}).get("ShipName") or "AIS").strip()[:3] or "AIS"
                countries[country] += 1
            except Exception:
                continue
            if vessels >= int(os.environ.get("POLYDATA_AISSTREAM_SAMPLE_LIMIT", "12") or 12):
                break
    return {"status": "ok", "messageCount": vessels, "topShipHints": dict(countries.most_common(5))}


def _aisstream_api_key() -> str:
    return str(os.environ.get("POLYDATA_AISSTREAM_API_KEY") or os.environ.get("AISSTREAM_API_KEY") or "").strip()


def _read_aisstream_cache(
    ctx: GlobalTransportShippingContext,
    *,
    max_age_seconds: int,
) -> Optional[Dict[str, Any]]:
    dependencies = _dependencies(ctx)
    reader = dependencies.get_cached_json
    payload = reader(AISSTREAM_SNAPSHOT_NAMESPACE, AISSTREAM_CACHE_KEY) if reader is not None else None
    if not isinstance(payload, dict):
        store = dependencies.snapshot_store
        if store is not None:
            payload = store.get(AISSTREAM_SNAPSHOT_NAMESPACE, AISSTREAM_CACHE_KEY)
    if not isinstance(payload, dict):
        return None
    sampled_at = payload.get("sampledAt") or payload.get("generatedAt")
    age = _age_seconds(sampled_at)
    if age is None or age > max_age_seconds:
        return None
    return {**payload, "cacheMode": "ais-cache", "ageSeconds": round(max(0, age))}


def _store_aisstream_cache(
    ctx: GlobalTransportShippingContext,
    payload: Dict[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    dependencies = _dependencies(ctx)
    store = dependencies.snapshot_store
    if store is not None:
        store.set(AISSTREAM_SNAPSHOT_NAMESPACE, AISSTREAM_CACHE_KEY, payload, ttl_seconds)
    setter = dependencies.set_cached_json
    if setter is not None:
        setter(AISSTREAM_SNAPSHOT_NAMESPACE, AISSTREAM_CACHE_KEY, payload, ttl_seconds)


def _read_cached_payload(
    ctx: GlobalTransportShippingContext,
    namespace: str,
    cache_key: str,
    *,
    max_age_seconds: int | None = None,
) -> Optional[Dict[str, Any]]:
    dependencies = _dependencies(ctx)
    reader = dependencies.get_cached_json
    payload = reader(namespace, cache_key) if reader is not None else None
    if not isinstance(payload, dict):
        store = dependencies.snapshot_store
        if store is not None:
            payload = store.get(namespace, cache_key)
    if not isinstance(payload, dict):
        return None
    if max_age_seconds is not None:
        generated_at = payload.get("sampledAt") or payload.get("generatedAt") or payload.get("updatedAt")
        age = _age_seconds(generated_at)
        if age is None or age > max_age_seconds:
            return None
        return {**payload, "cacheMode": payload.get("cacheMode") or "cache", "ageSeconds": round(max(0, age))}
    return payload


def _store_cached_payload(
    ctx: GlobalTransportShippingContext,
    namespace: str,
    cache_key: str,
    payload: Dict[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    dependencies = _dependencies(ctx)
    store = dependencies.snapshot_store
    if store is not None:
        store.set(namespace, cache_key, payload, ttl_seconds)
    setter = dependencies.set_cached_json
    if setter is not None:
        setter(namespace, cache_key, payload, ttl_seconds)


def _aisstream_status(ctx: dict) -> Dict[str, Any]:
    api_key = _aisstream_api_key()
    min_interval = _env_int(
        "POLYDATA_AISSTREAM_MIN_SAMPLE_INTERVAL_SECONDS",
        DEFAULT_AISSTREAM_SAMPLE_INTERVAL_SECONDS,
        minimum=900,
        maximum=86400,
    )
    if not api_key:
        return {"status": "missing-key", "messageCount": 0, "sourceUrl": AISSTREAM_DOC_URL}
    cached = _read_aisstream_cache(ctx, max_age_seconds=min_interval)
    if cached is not None:
        return cached
    if not _env_bool("POLYDATA_AISSTREAM_SAMPLE_ENABLED", True):
        return {
            "status": "configured",
            "messageCount": 0,
            "sourceUrl": AISSTREAM_DOC_URL,
            "cacheTtlSeconds": min_interval,
            "sampleEnabled": False,
        }
    try:
        import asyncio

        timeout_seconds = _env_int("POLYDATA_AISSTREAM_SAMPLE_SECONDS", 8, minimum=3, maximum=20)
        sampled = {
            **asyncio.run(_sample_aisstream(api_key, timeout_seconds=timeout_seconds)),
            "sourceUrl": AISSTREAM_DOC_URL,
            "sampledAt": _utc_now_iso(ctx),
            "sampleWindowSeconds": timeout_seconds,
            "cacheTtlSeconds": min_interval,
        }
        _store_aisstream_cache(ctx, sampled, ttl_seconds=min_interval)
        return sampled
    except Exception as exc:
        error_payload = {
            "status": "error",
            "messageCount": 0,
            "error": exc.__class__.__name__,
            "sourceUrl": AISSTREAM_DOC_URL,
            "sampledAt": _utc_now_iso(ctx),
            "cacheTtlSeconds": min_interval,
        }
        _store_aisstream_cache(ctx, error_payload, ttl_seconds=min_interval)
        return error_payload


def _opensky_credentials() -> tuple[str, str]:
    client_id = str(os.environ.get("POLYDATA_OPENSKY_CLIENT_ID") or os.environ.get("OPENSKY_CLIENT_ID") or "").strip()
    client_secret = str(os.environ.get("POLYDATA_OPENSKY_CLIENT_SECRET") or os.environ.get("OPENSKY_CLIENT_SECRET") or "").strip()
    return client_id, client_secret


def _opensky_region_plan() -> List[Dict[str, Any]]:
    raw = str(os.environ.get("POLYDATA_OPENSKY_REGIONS_JSON") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                regions = [row for row in parsed if isinstance(row, dict)]
                if regions:
                    return regions
        except json.JSONDecodeError:
            pass
    region_limit = _env_int("POLYDATA_OPENSKY_REGION_LIMIT", 3, minimum=1, maximum=len(OPENSKY_REGIONS))
    offset = _env_int("POLYDATA_OPENSKY_REGION_OFFSET", 0, minimum=0, maximum=max(0, len(OPENSKY_REGIONS) - 1))
    rotated = OPENSKY_REGIONS[offset:] + OPENSKY_REGIONS[:offset]
    return rotated[:region_limit]


def _opensky_access_token(ctx: dict) -> tuple[Optional[str], Dict[str, Any]]:
    client_id, client_secret = _opensky_credentials()
    if not client_id or not client_secret:
        return None, {"status": "missing-key", "sourceUrl": OPENSKY_DOC_URL}
    cached = _read_cached_payload(ctx, OPENSKY_SNAPSHOT_NAMESPACE, OPENSKY_TOKEN_CACHE_KEY)
    if cached:
        if cached.get("accessToken"):
            expires_at = _parse_iso(cached.get("expiresAt"))
            if expires_at and (expires_at - datetime.now(timezone.utc)).total_seconds() > 90:
                return str(cached["accessToken"]), {"status": "token-cache", "sourceUrl": OPENSKY_DOC_URL}
        if cached.get("status") == "auth-error":
            return None, {
                "status": "auth-error-cache",
                "sourceUrl": OPENSKY_DOC_URL,
                "errorClass": cached.get("errorClass"),
            }
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    try:
        payload = _http_form_post(
            ctx,
            OPENSKY_AUTH_URL,
            data=data,
            timeout=_env_int("POLYDATA_OPENSKY_AUTH_TIMEOUT_SECONDS", 5, minimum=2, maximum=15),
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "polydata-global-transport/1.0"},
        )
    except Exception as exc:
        failure = {
            "status": "auth-error",
            "generatedAt": _utc_now_iso(ctx),
            "sourceUrl": OPENSKY_DOC_URL,
            "errorClass": exc.__class__.__name__,
        }
        _store_cached_payload(
            ctx,
            OPENSKY_SNAPSHOT_NAMESPACE,
            OPENSKY_TOKEN_CACHE_KEY,
            failure,
            ttl_seconds=_env_int("POLYDATA_OPENSKY_FAILURE_TTL_SECONDS", 300, minimum=30, maximum=1800),
        )
        return None, failure
    if not isinstance(payload, dict) or not payload.get("access_token"):
        return None, {"status": "auth-error", "sourceUrl": OPENSKY_DOC_URL}
    expires_in = int(payload.get("expires_in") or 1800)
    expires_at = datetime.now(timezone.utc).timestamp() + max(120, expires_in - 60)
    token_payload = {
        "status": "ok",
        "accessToken": str(payload["access_token"]),
        "expiresAt": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "generatedAt": _utc_now_iso(ctx),
        "sourceUrl": OPENSKY_DOC_URL,
    }
    _store_cached_payload(ctx, OPENSKY_SNAPSHOT_NAMESPACE, OPENSKY_TOKEN_CACHE_KEY, token_payload, ttl_seconds=max(120, expires_in - 60))
    return str(payload["access_token"]), {"status": "token-live", "sourceUrl": OPENSKY_DOC_URL}


def _normalize_opensky_state(row: List[Any], *, region: Dict[str, Any], sampled_at: str) -> Optional[Dict[str, Any]]:
    if not isinstance(row, list) or len(row) < 17:
        return None
    lon = _float(row[5])
    lat = _float(row[6])
    if lat is None or lon is None:
        return None
    velocity = _float(row[9])
    altitude = _float(row[7]) or _float(row[13])
    callsign = str(row[1] or "").strip() or str(row[0] or "").strip()[:8] or "OPEN"
    origin_country = str(row[2] or "Unknown").strip()
    vertical_rate = _float(row[11])
    on_ground = bool(row[8])
    risk_score = 18
    if altitude is not None and altitude < 900:
        risk_score += 12
    if vertical_rate is not None and abs(vertical_rate) >= 8:
        risk_score += 8
    if on_ground:
        risk_score = max(6, risk_score - 8)
    if velocity is not None and velocity < 65 and not on_ground:
        risk_score += 7
    return {
        "id": f"opensky-{_source_hash(str(row[0] or callsign))}",
        "icao24": row[0],
        "callsign": callsign,
        "originCountry": origin_country,
        "region": region.get("id"),
        "regionLabel": region.get("label") or region.get("id"),
        "lat": lat,
        "lon": lon,
        "baroAltitude": altitude,
        "velocity": velocity,
        "heading": _float(row[10]),
        "verticalRate": vertical_rate,
        "onGround": on_ground,
        "lastContact": row[4],
        "status": "watch" if risk_score >= 34 else "normal",
        "riskScore": min(88, risk_score),
        "source": "OpenSky",
        "sourceUrl": OPENSKY_DOC_URL,
        "updatedAt": sampled_at,
    }


def _adsb_base_url() -> str:
    return str(os.environ.get("POLYDATA_ADSB_BASE_URL") or ADSB_LOL_BASE_URL).rstrip("/")


def _adsb_viewport_samples(
    bbox: tuple[float, float, float, float],
) -> List[Dict[str, float]]:
    """Translate a viewport into at most four honest ADSB radius queries."""

    west, south, east, north = bbox
    mid_lat = (south + north) / 2
    mid_lon = (west + east) / 2
    height_nm = abs(north - south) * 60
    width_nm = abs(east - west) * 60 * max(0.15, abs(math.cos(math.radians(mid_lat))))
    diagonal_nm = math.hypot(width_nm, height_nm)
    if diagonal_nm <= 460:
        return [{
            "lat": mid_lat,
            "lon": mid_lon,
            "radiusNm": float(max(20, min(250, math.ceil(diagonal_nm / 2) + 12))),
        }]
    radius_nm = float(max(80, min(250, math.ceil(diagonal_nm / 4) + 20)))
    return [
        {"lat": lat, "lon": lon, "radiusNm": radius_nm}
        for lat in (south + (north - south) * 0.25, south + (north - south) * 0.75)
        for lon in (west + (east - west) * 0.25, west + (east - west) * 0.75)
    ]


def _adsb_number(value: Any) -> Optional[float]:
    if isinstance(value, str) and value.strip().lower() == "ground":
        return 0.0
    return _float(value)


def _normalize_adsb_aircraft(row: Dict[str, Any], *, hub: Dict[str, Any], sampled_at: str) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    lat = _float(row.get("lat"))
    lon = _float(row.get("lon"))
    if lat is None or lon is None:
        return None
    callsign = str(row.get("flight") or row.get("r") or row.get("hex") or "ADSB").strip() or "ADSB"
    ground_value = str(row.get("alt_baro") or "").strip().lower()
    on_ground = ground_value == "ground"
    altitude_ft = _adsb_number(row.get("alt_baro"))
    if altitude_ft is None:
        altitude_ft = _adsb_number(row.get("alt_geom"))
    ground_speed_knots = _adsb_number(row.get("gs"))
    vertical_rate_ft_min = _adsb_number(row.get("baro_rate"))
    emergency = str(row.get("emergency") or "").strip().lower()
    risk_score = 16
    if emergency and emergency not in {"none", "null", "false"}:
        risk_score += 42
    if altitude_ft is not None and altitude_ft < 3000 and not on_ground:
        risk_score += 12
    if vertical_rate_ft_min is not None and abs(vertical_rate_ft_min) >= 1500:
        risk_score += 8
    if on_ground:
        risk_score = max(5, risk_score - 7)
    if ground_speed_knots is not None and ground_speed_knots < 135 and not on_ground:
        risk_score += 6
    return {
        "id": f"adsb-{_source_hash(str(row.get('hex') or callsign))}",
        "icao24": row.get("hex"),
        "callsign": callsign,
        "registration": row.get("r"),
        "aircraftType": row.get("t"),
        "originCountry": str(row.get("ownOp") or row.get("desc") or "Unknown").strip() or "Unknown",
        "region": str(hub.get("iata") or hub.get("icao") or hub.get("id") or "hub").lower(),
        "regionLabel": f"{hub.get('iata') or hub.get('icao') or 'HUB'} vicinity",
        "lat": lat,
        "lon": lon,
        "baroAltitude": round(altitude_ft * 0.3048, 1) if altitude_ft is not None else None,
        "velocity": round(ground_speed_knots * 0.514444, 1) if ground_speed_knots is not None else None,
        "heading": _float(row.get("track") if row.get("track") is not None else row.get("true_heading")),
        "verticalRate": round(vertical_rate_ft_min * 0.00508, 2) if vertical_rate_ft_min is not None else None,
        "onGround": on_ground,
        "lastContact": row.get("seen") or row.get("seen_pos"),
        "status": "watch" if risk_score >= 34 else "normal",
        "riskScore": min(92, risk_score),
        "source": "ADSB.lol",
        "sourceUrl": ADSB_LOL_DOC_URL,
        "updatedAt": sampled_at,
    }


def _adsb_viewport_snapshot(
    ctx: GlobalTransportShippingContext,
    *,
    bbox: tuple[float, float, float, float],
    zoom: float,
    limit: int,
    cache_key: str,
    sampled_at: str,
    fallback_reason: str,
) -> Dict[str, Any]:
    if not _env_bool("POLYDATA_ADSB_FALLBACK_ENABLED", True):
        return {
            "schemaVersion": AVIATION_VIEWPORT_SCHEMA_VERSION,
            "generatedAt": sampled_at,
            "status": "unavailable",
            "bbox": list(bbox),
            "zoom": zoom,
            "aircraft": [],
            "aircraftCount": 0,
            "source": None,
            "errorCode": "aviation-providers-unavailable",
            "limitations": ["No live aviation provider is currently available."],
        }
    cached = _read_cached_payload(ctx, ADSB_SNAPSHOT_NAMESPACE, cache_key, max_age_seconds=30)
    if cached is not None:
        return cached

    west, south, east, north = bbox
    aircraft: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    sectors = _adsb_viewport_samples(bbox)
    seen: set[str] = set()

    def fetch_sector(index: int, sector: Dict[str, float]) -> tuple[int, Dict[str, Any], List[Any]]:
        hub = {"id": f"viewport-{index + 1}", "iata": None, "icao": None}
        url = (
            f"{_adsb_base_url()}/point/{sector['lat']:.5f}/"
            f"{sector['lon']:.5f}/{int(sector['radiusNm'])}"
        )
        payload = _http_json_get(
            ctx,
            url,
            timeout=_env_int("POLYDATA_ADSB_VIEWPORT_TIMEOUT_SECONDS", 6, minimum=2, maximum=15),
            headers={"User-Agent": "polydata-global-transport/1.0"},
        )
        rows = payload.get("ac") if isinstance(payload, dict) else []
        return index, hub, rows if isinstance(rows, list) else []

    with ThreadPoolExecutor(max_workers=min(4, len(sectors)), thread_name_prefix="adsb-viewport") as executor:
        futures = {
            executor.submit(fetch_sector, index, sector): index
            for index, sector in enumerate(sectors)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                _, hub, rows = future.result()
            except Exception as exc:
                errors.append({"sector": str(index + 1), "error": exc.__class__.__name__})
                continue
            for row in rows:
                item = _normalize_adsb_aircraft(row, hub=hub, sampled_at=sampled_at)
                if item is None:
                    continue
                if not (west <= float(item["lon"]) <= east and south <= float(item["lat"]) <= north):
                    continue
                identity = str(item.get("icao24") or item.get("id"))
                if identity in seen:
                    continue
                seen.add(identity)
                aircraft.append(item)

    errors.sort(key=lambda item: item["sector"])
    aircraft.sort(key=lambda item: (
        0 if item.get("status") == "watch" else 1,
        -float(item.get("riskScore") or 0),
        str(item.get("id") or ""),
    ))
    picked = aircraft[:limit]
    status = "ok" if picked and not errors else ("partial" if picked else ("unavailable" if errors else "empty"))
    result = {
        "schemaVersion": AVIATION_VIEWPORT_SCHEMA_VERSION,
        "generatedAt": sampled_at,
        "status": status,
        "bbox": list(bbox),
        "zoom": zoom,
        "aircraft": picked,
        "aircraftCount": len(picked),
        "availableAircraftCount": len(aircraft),
        "source": "ADSB.lol",
        "sourceUrl": ADSB_LOL_DOC_URL,
        "fallbackFrom": {"source": "OpenSky", "reason": fallback_reason},
        "coverage": {
            "mode": "covering-sector" if len(sectors) == 1 else "sampled-sectors",
            "sectorCount": len(sectors),
            "complete": len(sectors) == 1 and not errors,
        },
        "errors": errors,
        "limitations": [
            "Aircraft positions are real ADSB observations; surveillance coverage and update delay vary.",
            *(["Large viewports are sampled with bounded sectors and are not complete global coverage."] if len(sectors) > 1 else []),
        ],
    }
    _store_cached_payload(ctx, ADSB_SNAPSHOT_NAMESPACE, cache_key, result, ttl_seconds=30)
    return result


def _adsb_live_status(ctx: dict, hubs: List[Dict[str, Any]], *, enabled: bool = True) -> Dict[str, Any]:
    min_interval = _env_int(
        "POLYDATA_ADSB_MIN_SAMPLE_INTERVAL_SECONDS",
        DEFAULT_ADSB_SAMPLE_INTERVAL_SECONDS,
        minimum=900,
        maximum=21600,
    )
    if not enabled or not _env_bool("POLYDATA_ADSB_FALLBACK_ENABLED", True):
        return {
            "status": "skipped",
            "aircraftCount": 0,
            "aircraft": [],
            "regions": [],
            "sourceUrl": ADSB_LOL_DOC_URL,
            "cacheTtlSeconds": min_interval,
        }
    cached = _read_cached_payload(ctx, ADSB_SNAPSHOT_NAMESPACE, ADSB_CACHE_KEY, max_age_seconds=min_interval)
    if cached is not None:
        return cached
    sampled_at = _utc_now_iso(ctx)
    hub_limit = _env_int("POLYDATA_ADSB_HUB_LIMIT", 4, minimum=1, maximum=10)
    per_hub_limit = _env_int("POLYDATA_ADSB_PER_HUB_AIRCRAFT_LIMIT", 24, minimum=6, maximum=80)
    total_limit = _env_int("POLYDATA_ADSB_AIRCRAFT_LIMIT", 96, minimum=12, maximum=220)
    radius_nm = _env_int("POLYDATA_ADSB_RADIUS_NM", 70, minimum=20, maximum=250)
    base_url = _adsb_base_url()
    aircraft: List[Dict[str, Any]] = []
    regions = []
    errors = []
    seen: set[str] = set()
    for hub in hubs[:hub_limit]:
        lat = _float(hub.get("lat"))
        lon = _float(hub.get("lon"))
        code = hub.get("iata") or hub.get("icao") or hub.get("id") or "hub"
        if lat is None or lon is None:
            continue
        url = f"{base_url}/point/{lat:.5f}/{lon:.5f}/{radius_nm}"
        try:
            payload = _http_json_get(ctx, url, timeout=14, headers={"User-Agent": "polydata-global-transport/1.0"})
            rows = payload.get("ac") if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                rows = []
            normalized = []
            for row in rows:
                item = _normalize_adsb_aircraft(row, hub=hub, sampled_at=sampled_at)
                if item is None:
                    continue
                key = str(item.get("icao24") or item.get("id"))
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(item)
            normalized.sort(key=lambda item: (0 if item.get("status") == "watch" else 1, -(float(item.get("velocity") or 0))))
            picked = normalized[:per_hub_limit]
            aircraft.extend(picked)
            regions.append({"id": str(code), "label": f"{code} vicinity", "status": "ok", "aircraftCount": len(normalized), "returned": len(picked)})
        except Exception as exc:
            errors.append({"region": str(code), "error": exc.__class__.__name__})
            regions.append({"id": str(code), "label": f"{code} vicinity", "status": "error", "aircraftCount": 0, "returned": 0})
    live_payload = {
        "status": "ok" if aircraft else ("partial" if errors else "empty"),
        "aircraftCount": len(aircraft),
        "aircraft": aircraft[:total_limit],
        "regions": regions,
        "errors": errors,
        "sourceUrl": ADSB_LOL_DOC_URL,
        "source": "ADSB.lol",
        "sampledAt": sampled_at,
        "cacheTtlSeconds": min_interval,
        "baseUrl": base_url,
    }
    _store_cached_payload(ctx, ADSB_SNAPSHOT_NAMESPACE, ADSB_CACHE_KEY, live_payload, ttl_seconds=min_interval)
    return live_payload


def _opensky_live_status(ctx: dict) -> Dict[str, Any]:
    min_interval = _env_int(
        "POLYDATA_OPENSKY_MIN_SAMPLE_INTERVAL_SECONDS",
        DEFAULT_OPENSKY_SAMPLE_INTERVAL_SECONDS,
        minimum=300,
        maximum=21600,
    )
    cached = _read_cached_payload(ctx, OPENSKY_SNAPSHOT_NAMESPACE, OPENSKY_CACHE_KEY, max_age_seconds=min_interval)
    if cached is not None:
        return cached
    sampled_at = _utc_now_iso(ctx)
    try:
        token, token_state = _opensky_access_token(ctx)
    except Exception as exc:
        live_payload = {
            "status": "error",
            "aircraftCount": 0,
            "aircraft": [],
            "regions": [],
            "errors": [{"stage": "auth", "error": exc.__class__.__name__}],
            "sourceUrl": OPENSKY_DOC_URL,
            "source": "OpenSky",
            "sampledAt": sampled_at,
            "cacheTtlSeconds": min_interval,
            "tokenState": "auth-error",
        }
        _store_cached_payload(ctx, OPENSKY_SNAPSHOT_NAMESPACE, OPENSKY_CACHE_KEY, live_payload, ttl_seconds=min_interval)
        return live_payload
    if not token:
        return {
            "status": token_state.get("status") or "missing-key",
            "aircraftCount": 0,
            "regions": [],
            "aircraft": [],
            "sourceUrl": OPENSKY_DOC_URL,
            "cacheTtlSeconds": min_interval,
        }
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "polydata-global-transport/1.0"}
    region_rows = []
    aircraft: List[Dict[str, Any]] = []
    errors = []
    per_region_limit = _env_int("POLYDATA_OPENSKY_PER_REGION_AIRCRAFT_LIMIT", 36, minimum=8, maximum=120)
    for region in _opensky_region_plan():
        params = {
            "lamin": region.get("lamin"),
            "lomin": region.get("lomin"),
            "lamax": region.get("lamax"),
            "lomax": region.get("lomax"),
        }
        try:
            payload = _http_json_get(ctx, OPENSKY_STATES_URL, params=params, timeout=18, headers=headers)
            states = payload.get("states") if isinstance(payload, dict) else []
            if not isinstance(states, list):
                states = []
            normalized = [
                item
                for item in (_normalize_opensky_state(row, region=region, sampled_at=sampled_at) for row in states)
                if item is not None
            ]
            normalized.sort(key=lambda item: (0 if item.get("status") == "watch" else 1, -(float(item.get("velocity") or 0))))
            picked = normalized[:per_region_limit]
            aircraft.extend(picked)
            region_rows.append({"id": region.get("id"), "label": region.get("label"), "status": "ok", "aircraftCount": len(normalized), "returned": len(picked)})
        except Exception as exc:
            errors.append({"region": region.get("id"), "error": exc.__class__.__name__})
            region_rows.append({"id": region.get("id"), "label": region.get("label"), "status": "error", "aircraftCount": 0, "returned": 0})
    live_payload = {
        "status": "ok" if aircraft else ("partial" if errors else "empty"),
        "aircraftCount": len(aircraft),
        "aircraft": aircraft[: _env_int("POLYDATA_OPENSKY_AIRCRAFT_LIMIT", 140, minimum=24, maximum=360)],
        "regions": region_rows,
        "errors": errors,
        "sourceUrl": OPENSKY_DOC_URL,
        "source": "OpenSky",
        "sampledAt": sampled_at,
        "cacheTtlSeconds": min_interval,
        "tokenState": token_state.get("status"),
    }
    _store_cached_payload(ctx, OPENSKY_SNAPSHOT_NAMESPACE, OPENSKY_CACHE_KEY, live_payload, ttl_seconds=min_interval)
    return live_payload


def get_aviation_viewport_snapshot(
    ctx: GlobalTransportShippingContext,
    *,
    bbox: tuple[float, float, float, float],
    zoom: float,
    limit: int = 180,
) -> Dict[str, Any]:
    """Return live aircraft only for a bounded visible map viewport.

    The client discards stale request generations. This server function keeps
    provider credentials and sampling policy off the browser, caches a
    quantized viewport briefly, and never falls back to fabricated flights.
    """
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("invalid-aviation-bbox")
    bounded_zoom = max(0.0, min(12.0, float(zoom)))
    bounded_limit = max(1, min(360, int(limit)))
    sampled_at = _utc_now_iso(ctx)
    if bounded_zoom < 2:
        return {
            "schemaVersion": AVIATION_VIEWPORT_SCHEMA_VERSION,
            "generatedAt": sampled_at,
            "status": "threshold",
            "bbox": list(bbox),
            "zoom": bounded_zoom,
            "aircraft": [],
            "aircraftCount": 0,
            "source": None,
            "limitations": ["Live aircraft positions are requested only at zoom 2 or higher."],
        }
    quantum = max(0.25, 8 / (2 ** max(0, int(bounded_zoom) - 2)))
    quantized = tuple(round(value / quantum) * quantum for value in bbox)
    cache_key = "viewport-v1:" + ":".join(f"{value:.2f}" for value in quantized)
    cached = _read_cached_payload(ctx, OPENSKY_SNAPSHOT_NAMESPACE, cache_key, max_age_seconds=30)
    if cached is not None:
        return cached
    cached = _read_cached_payload(ctx, ADSB_SNAPSHOT_NAMESPACE, cache_key, max_age_seconds=30)
    if cached is not None:
        return cached
    try:
        token, token_state = _opensky_access_token(ctx)  # type: ignore[arg-type]
    except Exception as exc:
        token, token_state = None, {"status": f"auth-{exc.__class__.__name__}"}
    if not token:
        return _adsb_viewport_snapshot(
            ctx,
            bbox=bbox,
            zoom=bounded_zoom,
            limit=bounded_limit,
            cache_key=cache_key,
            sampled_at=sampled_at,
            fallback_reason=str(token_state.get("status") or "opensky-credentials-unavailable"),
        )
    region = {"id": cache_key, "label": "Current map viewport"}
    try:
        payload = _http_json_get(
            ctx,
            OPENSKY_STATES_URL,
            params={"lamin": south, "lomin": west, "lamax": north, "lomax": east},
            timeout=_env_int("POLYDATA_OPENSKY_VIEWPORT_TIMEOUT_SECONDS", 5, minimum=2, maximum=14),
            headers={"Authorization": f"Bearer {token}", "User-Agent": "polydata-global-transport/1.0"},
        )
    except Exception as exc:
        return _adsb_viewport_snapshot(
            ctx,
            bbox=bbox,
            zoom=bounded_zoom,
            limit=bounded_limit,
            cache_key=cache_key,
            sampled_at=sampled_at,
            fallback_reason=f"states-{exc.__class__.__name__}",
        )
    rows = payload.get("states") if isinstance(payload, dict) else []
    normalized = [
        item
        for item in (_normalize_opensky_state(row, region=region, sampled_at=sampled_at) for row in rows or [])
        if item is not None and west <= float(item["lon"]) <= east and south <= float(item["lat"]) <= north
    ]
    normalized.sort(key=lambda item: (
        0 if item.get("status") == "watch" else 1,
        -float(item.get("riskScore") or 0),
        str(item.get("id") or ""),
    ))
    aircraft = normalized[:bounded_limit]
    result = {
        "schemaVersion": AVIATION_VIEWPORT_SCHEMA_VERSION,
        "generatedAt": sampled_at,
        "status": "ok",
        "bbox": list(bbox),
        "zoom": bounded_zoom,
        "aircraft": aircraft,
        "aircraftCount": len(aircraft),
        "availableAircraftCount": len(normalized),
        "source": "OpenSky",
        "sourceUrl": OPENSKY_DOC_URL,
        "limitations": ["Aircraft surveillance coverage, update delay and regional availability vary by provider."],
    }
    _store_cached_payload(ctx, OPENSKY_SNAPSHOT_NAMESPACE, cache_key, result, ttl_seconds=30)
    return result


def _market_links(
    ctx: GlobalTransportShippingContext,
    query: str,
) -> List[Dict[str, Any]]:
    search = _dependencies(ctx).search_markets
    if search is None:
        return []
    try:
        rows = search(query, limit=3) or []
    except Exception:
        return []
    if isinstance(rows, dict):
        rows = rows.get("items") or []
    if not isinstance(rows, list):
        return []
    links = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        links.append(
            {
                "marketId": row.get("id") or row.get("marketId"),
                "slug": row.get("slug"),
                "question": row.get("question") or row.get("title"),
                "marketUrl": f"https://polymarket.com/event/{row.get('slug')}" if row.get("slug") else None,
                "matchScore": 0.62,
                "matchReasons": ["transport", "entity"],
            }
        )
    return links


def _item(*, topic: str, entity: str, country: str, title: str, summary: str, metric: int, metric_label: str, source_url: str, evidence_type: str, confidence: float, severity: str, tags: List[str], evidence: Dict[str, Any], markets: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    market_rows = markets or []
    return {
        "id": _source_hash(f"{topic}|{entity}|{title}"),
        "topic": topic,
        "entity": entity,
        "country": country,
        "team": None,
        "eventTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sourceUrl": source_url,
        "evidenceType": evidence_type,
        "title": title,
        "summary": summary,
        "metric": metric,
        "metricLabel": metric_label,
        "confidence": confidence,
        "severity": severity,
        "tags": tags,
        "relatedPolymarketMarketIds": [row.get("marketId") for row in market_rows if row.get("marketId")],
        "markets": market_rows,
        "evidence": evidence,
    }


def build_global_transport_shipping_payload(ctx: dict, *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    generated_at = _utc_now_iso(ctx)
    airports_text, airports_source = _read_openflights_text(ctx, "airports.dat", OPENFLIGHTS_AIRPORTS_URL)
    routes_text, routes_source = _read_openflights_text(ctx, "routes.dat", OPENFLIGHTS_ROUTES_URL)
    try:
        airlines_text, _ = _read_openflights_text(ctx, "airlines.dat", OPENFLIGHTS_AIRLINES_URL)
    except Exception:
        airlines_text = ""
    airports = _parse_airports(airports_text)
    airlines = _parse_airlines(airlines_text)
    route_stats = _parse_routes(routes_text, airports, airlines)

    transit_stats, transit_url = _fetch_transitland_catalog(ctx)
    ais_status = _aisstream_status(ctx)
    opensky_status = _opensky_live_status(ctx)
    adsb_status = _adsb_live_status(ctx, route_stats.get("topHubs", []), enabled=not bool(opensky_status.get("aircraft")))
    live_aircraft_status = opensky_status if opensky_status.get("aircraft") else adsb_status

    items: List[Dict[str, Any]] = []
    for hub in route_stats["topHubs"][:6]:
        code = hub.get("iata") or hub.get("icao") or hub.get("id")
        markets = _market_links(ctx, f"{code} airport delay travel disruption")
        items.append(
            _item(
                topic="aviation",
                entity=str(code or hub.get("name") or "Airport"),
                country=str(hub.get("country") or ""),
                title=f"{hub.get('name') or code} route hub",
                summary=f"{hub.get('city') or 'Unknown city'} / {hub.get('country') or 'Unknown'} has {hub.get('routeCount')} OpenFlights route edges.",
                metric=int(hub.get("routeCount") or 0),
                metric_label="ROUTES",
                source_url=routes_source,
                evidence_type="OPENFLIGHTS",
                confidence=0.82,
                severity="watch" if int(hub.get("routeCount") or 0) >= 1200 else "normal",
                tags=["airport", "routes"],
                evidence=hub,
                markets=markets,
            )
        )
    for corridor in route_stats["topCountryCorridors"][:4]:
        entity = f"{corridor['sourceCountry']} / {corridor['destCountry']}"
        items.append(
            _item(
                topic="aviation",
                entity=entity,
                country=str(corridor["sourceCountry"]),
                title=f"{entity} air corridor",
                summary=f"{corridor['routeCount']} directional OpenFlights route edges connect this country pair.",
                metric=int(corridor["routeCount"]),
                metric_label="EDGES",
                source_url=routes_source,
                evidence_type="OPENFLIGHTS",
                confidence=0.78,
                severity="normal",
                tags=["corridor", "air"],
                evidence=corridor,
                markets=_market_links(ctx, f"{entity} flight disruption airline"),
            )
        )
    gtfs_count = int(transit_stats.get("specCounts", {}).get("gtfs", 0))
    items.append(
        _item(
            topic="public-transit",
            entity="Transitland Atlas",
            country="Global",
            title="Transitland mobility feed catalog",
            summary=f"{transit_stats['feedCount']} feeds / {transit_stats['operatorCount']} operators across {transit_stats.get('scannedFileCount', 0)} of {transit_stats.get('catalogFileCount', 0)} DMFR files.",
            metric=int(transit_stats["feedCount"]),
            metric_label="FEEDS",
            source_url=transit_url,
            evidence_type="TRANSITLAND",
            confidence=0.86,
            severity="normal",
            tags=["gtfs", "operators"],
            evidence=transit_stats,
            markets=_market_links(ctx, "public transit strike delay gtfs"),
        )
    )
    items.append(
        _item(
            topic="public-transit",
            entity="GTFS coverage",
            country="Global",
            title="GTFS static feed coverage",
            summary=f"{gtfs_count} static GTFS feeds, authorization mix: {transit_stats.get('authorizationCounts') or {}}.",
            metric=gtfs_count,
            metric_label="GTFS",
            source_url=transit_url,
            evidence_type="TRANSITLAND",
            confidence=0.8,
            severity="watch" if gtfs_count == 0 else "normal",
            tags=["gtfs", "catalog"],
            evidence={"specCounts": transit_stats.get("specCounts"), "sampleFeeds": transit_stats.get("sampleFeeds")},
        )
    )
    ais_severity = "watch" if ais_status.get("status") in {"missing-key", "error"} else "normal"
    items.append(
        _item(
            topic="shipping",
            entity="AISStream",
            country="Global",
            title="Global AIS websocket status",
            summary=f"AISStream status: {ais_status.get('status')}. Uses cached low-frequency samples when AISSTREAM_API_KEY is configured.",
            metric=int(ais_status.get("messageCount") or 0),
            metric_label="AIS MSG",
            source_url=AISSTREAM_DOC_URL,
            evidence_type="AISSTREAM",
            confidence=0.48 if ais_status.get("status") == "missing-key" else 0.76,
            severity=ais_severity,
            tags=["ais", "shipping"],
            evidence=ais_status,
            markets=_market_links(ctx, "shipping disruption port vessel"),
        )
    )
    opensky_severity = "watch" if opensky_status.get("status") in {"missing-key", "auth-error", "error", "partial"} else "normal"
    items.append(
        _item(
            topic="aviation",
            entity="OpenSky Network",
            country="Global",
            title="OpenSky live aircraft state",
            summary=f"OpenSky status: {opensky_status.get('status')}. {opensky_status.get('aircraftCount') or 0} aircraft returned across {len(opensky_status.get('regions') or [])} sampled regions.",
            metric=int(opensky_status.get("aircraftCount") or 0),
            metric_label="AIRCRAFT",
            source_url=OPENSKY_DOC_URL,
            evidence_type="OPENSKY",
            confidence=0.42 if opensky_status.get("status") in {"missing-key", "auth-error", "error"} else 0.82,
            severity=opensky_severity,
            tags=["opensky", "live-aircraft"],
            evidence=opensky_status,
            markets=_market_links(ctx, "flight delay airline disruption live aircraft"),
        )
    )
    if adsb_status.get("status") != "skipped" or adsb_status.get("aircraft"):
        adsb_severity = "watch" if adsb_status.get("status") in {"error", "partial"} else "normal"
        items.append(
            _item(
                topic="aviation",
                entity="ADSB.lol",
                country="Global",
                title="ADSB.lol fallback aircraft state",
                summary=f"ADSB.lol status: {adsb_status.get('status')}. {adsb_status.get('aircraftCount') or 0} aircraft returned across {len(adsb_status.get('regions') or [])} hub regions.",
                metric=int(adsb_status.get("aircraftCount") or 0),
                metric_label="AIRCRAFT",
                source_url=ADSB_LOL_DOC_URL,
                evidence_type="ADSBLOL",
                confidence=0.76 if adsb_status.get("aircraft") else 0.5,
                severity=adsb_severity,
                tags=["adsb", "live-aircraft", "fallback"],
                evidence=adsb_status,
                markets=_market_links(ctx, "flight delay airline disruption adsb aircraft"),
            )
        )
    items.sort(key=lambda row: (0 if row["severity"] == "watch" else 1, -int(row.get("metric") or 0)))
    limited = items[: max(1, int(limit or DEFAULT_LIMIT))]
    aviation = _build_aviation_layer(route_stats, generated_at=generated_at, source_url=routes_source, live_aircraft_status=live_aircraft_status)
    evidence_payload = _build_evidence_payload(aviation, limited, generated_at=generated_at)
    source_health = {
        "openflights": "fresh",
        "transitland": "fresh",
        "aisstream": "degraded" if ais_status.get("status") in {"missing-key", "error"} else ("stale" if ais_status.get("cacheMode") else "fresh"),
        "opensky": "degraded" if opensky_status.get("status") in {"missing-key", "auth-error", "error", "partial"} else ("stale" if opensky_status.get("cacheMode") else "fresh"),
        "adsb": "skipped" if adsb_status.get("status") == "skipped" else ("degraded" if adsb_status.get("status") in {"error", "partial"} else ("stale" if adsb_status.get("cacheMode") else "fresh")),
        "weatherRiskJoin": "frontend-runtime",
        "conflictRiskJoin": "frontend-runtime",
    }
    return {
        "panelId": PANEL_ID,
        "generatedAt": generated_at,
        "status": "ok" if items else "empty",
        "cacheMode": "live-build",
        "freshness": "live",
        "source": "OpenFlights + OpenSky + Transitland Atlas + AISStream",
        "sourceUrl": OPENFLIGHTS_AIRPORTS_URL,
        "sources": {
            "openflights": {"status": "ok", "airportsSource": airports_source, "routesSource": routes_source},
            "transitland": {"status": "ok", "sourceUrl": transit_url},
            "aisstream": {"status": ais_status.get("status"), "sourceUrl": AISSTREAM_DOC_URL},
            "opensky": {"status": opensky_status.get("status"), "sourceUrl": OPENSKY_DOC_URL},
            "adsb": {"status": adsb_status.get("status"), "sourceUrl": ADSB_LOL_DOC_URL},
        },
        "sourceHealth": source_health,
        "cachePolicy": {
            "staticTtlSeconds": 86400,
            "snapshotTtlSeconds": DEFAULT_TTL_SECONDS,
            "aisMinSampleIntervalSeconds": _env_int(
                "POLYDATA_AISSTREAM_MIN_SAMPLE_INTERVAL_SECONDS",
                DEFAULT_AISSTREAM_SAMPLE_INTERVAL_SECONDS,
                minimum=900,
                maximum=86400,
            ),
            "quotaGuard": {
                "aisstream": "cached-low-frequency",
                "opensky": "oauth-bbox-cache",
                "adsb": "fallback-hub-bbox-cache",
                "openflights": "static-source",
                "transitland": "sampled-catalog",
            },
        },
        "summary": {
            "airports": len({row.get("id") for row in airports.values() if row.get("id")}),
            "routes": route_stats["routeCount"],
            "visibleRoutes": len(aviation.get("routes", [])),
            "flightSamples": len(aviation.get("flights", [])),
            "liveFlightSamples": len(aviation.get("liveFlights", [])),
            "countries": route_stats["countryCount"],
            "topHub": (route_stats["topHubs"][0].get("iata") or route_stats["topHubs"][0].get("name")) if route_stats["topHubs"] else None,
            "transitFeeds": transit_stats["feedCount"],
            "transitOperators": transit_stats["operatorCount"],
            "transitCatalogFiles": transit_stats.get("catalogFileCount", 0),
            "transitScannedFiles": transit_stats.get("scannedFileCount", 0),
            "aisStatus": ais_status.get("status"),
            "openSkyStatus": opensky_status.get("status"),
            "openSkyRegions": len(opensky_status.get("regions") or []),
            "adsbStatus": adsb_status.get("status"),
            "adsbRegions": len(adsb_status.get("regions") or []),
            "liveFlightSource": live_aircraft_status.get("source") or ("OpenSky" if opensky_status.get("aircraft") else ("ADSB.lol" if adsb_status.get("aircraft") else None)),
            "evidenceVersion": EVIDENCE_SCHEMA_VERSION,
        },
        "aviation": aviation,
        "evidence": evidence_payload,
        "items": limited,
    }


def _empty_payload(ctx: dict, *, cache_mode: str = "seed-miss") -> Dict[str, Any]:
    return {
        "panelId": PANEL_ID,
        "generatedAt": _utc_now_iso(ctx),
        "status": "warming",
        "cacheMode": cache_mode,
        "freshness": "warming",
        "source": "OpenFlights + OpenSky + Transitland Atlas + AISStream",
        "sourceUrl": OPENFLIGHTS_AIRPORTS_URL,
        "sources": {},
        "summary": {"airports": 0, "routes": 0, "visibleRoutes": 0, "flightSamples": 0, "liveFlightSamples": 0, "countries": 0, "topHub": None, "transitFeeds": 0, "transitOperators": 0, "aisStatus": "unknown", "openSkyStatus": "unknown", "openSkyRegions": 0, "evidenceVersion": EVIDENCE_SCHEMA_VERSION},
        "aviation": {"mode": "warming", "hubs": [], "routes": [], "flights": [], "liveFlights": [], "ops": [], "airlines": [], "news": []},
        "evidence": {"schemaVersion": EVIDENCE_SCHEMA_VERSION, "routes": [], "risks": [], "ops": []},
        "items": [],
    }


def normalize_global_transport_shipping_payload(
    payload: Dict[str, Any],
    *,
    ctx: GlobalTransportShippingContext,
    limit: int = DEFAULT_LIMIT,
) -> Dict[str, Any]:
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return {
        **payload,
        "panelId": PANEL_ID,
        "generatedAt": payload.get("generatedAt") or _utc_now_iso(ctx),
        "items": items[: max(1, int(limit or DEFAULT_LIMIT))],
    }


def _with_cache_mode(payload: Dict[str, Any], cache_mode: str, freshness: str = "seeded") -> Dict[str, Any]:
    return {**payload, "cacheMode": cache_mode, "freshness": payload.get("freshness") or freshness}


def _read_seeded_snapshot(
    ctx: GlobalTransportShippingContext,
) -> Optional[Dict[str, Any]]:
    dependencies = _dependencies(ctx)
    reader = dependencies.get_cached_json
    if reader is not None:
        payload = reader(GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE, GLOBAL_TRANSPORT_CACHE_KEY)
        if isinstance(payload, dict):
            return _with_cache_mode(payload, "redis-seed")
    store = dependencies.snapshot_store
    if store is None:
        return None
    payload = store.get(GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE, GLOBAL_TRANSPORT_CACHE_KEY)
    if isinstance(payload, dict):
        return _with_cache_mode(payload, "sqlite-seed")
    stale = store.get_stale(GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE, GLOBAL_TRANSPORT_CACHE_KEY)
    if isinstance(stale, dict):
        return _with_cache_mode(stale, "stale-seed")
    return None


def _store_live(
    ctx: GlobalTransportShippingContext,
    payload: Dict[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    dependencies = _dependencies(ctx)
    store = dependencies.snapshot_store
    if store is not None:
        store.set(GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE, GLOBAL_TRANSPORT_CACHE_KEY, payload, ttl_seconds)
    setter = dependencies.set_cached_json
    if setter is not None:
        setter(GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE, GLOBAL_TRANSPORT_CACHE_KEY, payload, ttl_seconds)


def _schedule_live_refresh(
    ctx: GlobalTransportShippingContext,
    *,
    limit: int,
    ttl_seconds: int,
    reason: str,
) -> bool:
    dependencies = _dependencies(ctx)
    refresh_key = f"{GLOBAL_TRANSPORT_SNAPSHOT_NAMESPACE}:{GLOBAL_TRANSPORT_CACHE_KEY}"
    with _LIVE_REFRESH_LOCK:
        if refresh_key in _LIVE_REFRESHING:
            return False
        _LIVE_REFRESHING.add(refresh_key)

    def refresh() -> None:
        logger = getattr(dependencies.application, "logger", None)
        try:
            payload = {
                **build_global_transport_shipping_payload(
                    dependencies,
                    limit=limit,
                ),
                "cacheMode": "live-build",
            }
            if payload.get("items"):
                _store_live(dependencies, payload, ttl_seconds=ttl_seconds)
            elif logger is not None and hasattr(logger, "warning"):
                logger.warning("global transport refresh skipped empty payload reason=%s", reason)
        except Exception:
            if logger is not None:
                logger.exception("global transport refresh failed reason=%s", reason)
        finally:
            with _LIVE_REFRESH_LOCK:
                _LIVE_REFRESHING.discard(refresh_key)

    thread = threading.Thread(target=refresh, name="global-transport-refresh", daemon=True)
    thread.start()
    return True


def get_global_transport_shipping_snapshot(
    ctx: GlobalTransportShippingContext,
    limit: int = DEFAULT_LIMIT,
    *,
    allow_live_build: bool = True,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    ttl_seconds = max(300, int(os.environ.get("POLYDATA_GLOBAL_TRANSPORT_TTL_SECONDS", DEFAULT_TTL_SECONDS) or DEFAULT_TTL_SECONDS))
    seeded = _read_seeded_snapshot(dependencies)
    if seeded is not None:
        if allow_live_build and seeded.get("cacheMode") == "stale-seed":
            _schedule_live_refresh(dependencies, limit=limit, ttl_seconds=ttl_seconds, reason="stale-seed")
        return normalize_global_transport_shipping_payload(seeded, ctx=dependencies, limit=limit)
    if not allow_live_build:
        return normalize_global_transport_shipping_payload(
            _empty_payload(dependencies, cache_mode="seed-miss"),
            ctx=dependencies,
            limit=limit,
        )
    scheduled = _schedule_live_refresh(dependencies, limit=limit, ttl_seconds=ttl_seconds, reason="seed-miss")
    mode = "seed-miss-refreshing" if scheduled else "seed-miss-refresh-inflight"
    return normalize_global_transport_shipping_payload(
        _empty_payload(dependencies, cache_mode=mode),
        ctx=dependencies,
        limit=limit,
    )
