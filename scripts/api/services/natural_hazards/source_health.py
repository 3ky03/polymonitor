from __future__ import annotations

from typing import Any, Dict

from .contracts import coverage


SOURCE_COVERAGE: dict[str, Dict[str, Any]] = {
    "usgs": coverage(
        scope="global",
        label="Global earthquakes reported by the USGS real-time feed",
        complete=False,
        gaps=["Small-event completeness varies by region and network coverage."],
    ),
    "usgs-volcano-cap": coverage(
        scope="provider-area",
        label="Elevated volcanoes monitored by USGS volcano observatories",
        complete=False,
        gaps=["No official coverage is implied outside United States volcano observatory responsibility areas."],
    ),
    "eonet": coverage(
        scope="global",
        label="Global open natural-event discovery from NASA EONET",
        complete=False,
        gaps=["Discovery coverage is not equivalent to local official warnings."],
    ),
    "gdacs": coverage(
        scope="global",
        label="International disaster alerts reported by GDACS",
        complete=False,
        gaps=[
            "Low-severity green alerts are excluded from the operational map.",
            "International discovery coverage does not replace local official warnings.",
        ],
    ),
    "nws": coverage(
        scope="provider-area",
        label="United States and NWS responsibility areas",
        complete=False,
        gaps=["No official CAP coverage is implied outside NWS responsibility areas."],
    ),
    "nhc": coverage(
        scope="provider-area",
        label="Active Atlantic, eastern Pacific and central Pacific tropical cyclones published by NOAA NHC",
        complete=False,
        gaps=["Tropical cyclones outside NHC responsibility areas require other official meteorological agencies."],
    ),
    "firms": coverage(
        scope="global",
        label="NASA FIRMS satellite fire detections",
        complete=False,
        gaps=[
            "Thermal detections are not confirmed wildfire perimeters.",
            "Cloud, smoke, sensor coverage and overpass timing can create gaps.",
            "Global detections are spatially aggregated and display-capped.",
        ],
    ),
    "climate-anomaly": coverage(
        scope="global",
        label="NOAA NCEI Climate at a Glance global 5 degree monthly temperature anomaly grid",
        complete=False,
        gaps=[
            "Monthly observations are not active weather warnings or incident impact forecasts.",
            "Publication follows the observation month and individual grid cells may be unavailable.",
        ],
    ),
}


def unavailable_source(key: str, error_code: str) -> Dict[str, Any]:
    return {
        "key": key,
        "status": "degraded",
        "coverage": SOURCE_COVERAGE[key],
        "fetchedAt": None,
        "dataUpdatedAt": None,
        "staleAfter": None,
        "lastSuccessAt": None,
        "errorCode": error_code,
    }
