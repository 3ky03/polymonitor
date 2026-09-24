from __future__ import annotations

from typing import Any


OPEN_METEO_WEATHER_CODES = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Cloudy",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm hail",
    99: "Thunderstorm hail",
}


def describe_weather_code(value: Any) -> str:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return "Unknown"
    return OPEN_METEO_WEATHER_CODES.get(code, f"Code {code}")

