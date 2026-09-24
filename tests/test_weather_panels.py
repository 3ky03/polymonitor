from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import global_weather_map_service, weather_news_service
from runtime import global_weather_map_watcher, weather_news_watcher
from runtime.snapshot_store import SnapshotStore
from weather.cities import WEATHER_CITIES


class FakeLogger:
    def exception(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class FakeStore:
    def __init__(self, payload=None, stale=None):
        self.payload = payload
        self.stale = stale
        self.set_calls = []

    def get(self, namespace, cache_key):
        return self.payload

    def get_stale(self, namespace, cache_key):
        return self.stale

    def set(self, namespace, cache_key, payload, ttl):
        self.set_calls.append((namespace, cache_key, payload, ttl))
        self.payload = payload


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def execute(self, query, params=None):
        return FakeCursor(self.rows)

    def close(self):
        self.closed = True


def test_weather_city_watchlist_matches_polyweather_reference():
    expected = [
        "New York",
        "Chicago",
        "Dallas",
        "Miami",
        "Austin",
        "Atlanta",
        "Houston",
        "Denver",
        "Mexico City",
        "Los Angeles",
        "Seattle",
        "Toronto",
        "London",
        "Paris",
        "Madrid",
        "Milan",
        "Munich",
        "Warsaw",
        "Amsterdam",
        "Tel Aviv",
        "Ankara",
        "Beijing",
        "Shanghai",
        "Shenzhen",
        "Singapore",
        "Tokyo",
        "Seoul",
        "Chengdu",
        "Chongqing",
        "Wuhan",
        "Buenos Aires",
        "Sao Paulo",
        "Wellington",
    ]

    assert [city["city"] for city in WEATHER_CITIES] == expected
    assert len(WEATHER_CITIES) == 33


def test_weather_city_loader_extends_db_market_universe():
    from weather.cities import load_weather_cities

    names = [city["city"] for city in load_weather_cities()]
    assert "Hong Kong" in names
    assert "San Francisco" in names
    assert len(names) >= 50


def make_settings(**kwargs):
    defaults = {
        "open_meteo_api_url": "https://open.example/forecast",
        "aviationweather_metar_api_url": "https://aviation.example/metar",
        "google_news_rss_url": "https://news.example/rss/search",
        "weather_source_url": "https://weather.example",
        "gamma_api_base": "https://gamma.example",
        "clob_api_base": "https://clob.example",
        "clob_timeout_seconds": 2,
        "global_weather_map_ttl_seconds": 300,
        "global_weather_market_days": 4,
        "weather_news_ttl_seconds": 900,
        "weather_news_limit": 40,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def make_ctx(http_json_get=None, http_text_get=None, store=None, cached=None):
    calls = {"json": 0, "text": 0, "set_cache": 0}

    def json_get(*args, **kwargs):
        calls["json"] += 1
        if http_json_get:
            return http_json_get(*args, **kwargs)
        raise RuntimeError("network disabled")

    def text_get(*args, **kwargs):
        calls["text"] += 1
        if http_text_get:
            return http_text_get(*args, **kwargs)
        raise RuntimeError("network disabled")

    ctx = {
        "SETTINGS": make_settings(),
        "app": SimpleNamespace(logger=FakeLogger()),
        "utc_now_iso": lambda: "2026-05-12T12:00:00Z",
        "http_json_get": json_get,
        "http_text_get": text_get,
        "SNAPSHOT_STORE": store,
        "get_cached_json": lambda namespace, cache_key: cached,
        "set_cached_json": lambda *args: calls.__setitem__("set_cache", calls["set_cache"] + 1),
        "_calls": calls,
    }
    return ctx


def test_global_weather_map_builds_weather_metar_and_market_payload(monkeypatch):
    monkeypatch.setattr(global_weather_map_service, "_clob_yes_quote", lambda ctx, market: {"bestBidYes": 0.31, "bestAskYes": 0.35})
    request_params = {}

    def http_json_get(url, *, params=None, **kwargs):
        if "open.example" in url:
            request_params["openMeteo"] = dict(params or {})
            return [
                {
                    "current": {
                        "temperature_2m": 22.0,
                        "weather_code": 2,
                        "precipitation": 1.2,
                        "wind_speed_10m": 24.4,
                        "wind_gusts_10m": 41.1,
                        "time": "2026-05-12T12:00",
                    },
                    "hourly": {
                        "time": ["2026-05-12T12:00", "2026-05-12T13:00"],
                        "temperature_2m": [22.0, 23.0],
                        "precipitation": [1.2, 2.1],
                        "precipitation_probability": [70, 85],
                        "wind_speed_10m": [24.4, 28.8],
                        "wind_gusts_10m": [41.1, 49.2],
                        "weather_code": [2, 61],
                    },
                    "daily": {
                        "time": ["2026-05-12"],
                        "temperature_2m_max": [27.0],
                        "temperature_2m_min": [16.0],
                        "precipitation_sum": [9.6],
                        "precipitation_probability_max": [85],
                        "wind_speed_10m_max": [31.2],
                        "wind_gusts_10m_max": [52.7],
                        "weather_code": [61],
                    },
                }
            ]
        if "aviation.example" in url:
            return [{"icaoId": "KNYC", "temp": 21, "reportTime": "2026-05-12T11:50:00Z"}]
        if "gamma.example" in url:
            return [
                {
                    "id": "evt-1",
                    "slug": "highest-temperature-in-new-york-on-may-12-2026",
                    "title": "Highest temperature in New York on May 12?",
                    "active": True,
                    "closed": False,
                    "markets": [
                        {"id": "m1", "question": "Highest temperature in New York on May 12? 80°F or higher", "slug": "ny-80", "outcomePrices": ["0.30", "0.70"], "active": True}
                    ],
                }
            ]
        if "clob.example" in url:
            return {"bids": [{"price": "0.31"}], "asks": [{"price": "0.35"}]}
        return []

    ctx = make_ctx(http_json_get=http_json_get)
    payload = global_weather_map_service.build_global_weather_map_payload(ctx, limit=1)

    assert payload["status"] == "ok"
    assert int(payload["summary"]["mappedCount"]) >= 1
    assert payload["summary"]["liveMarketCount"] == 1
    city = payload["items"][0]
    assert city["cityId"] == "new-york"
    assert city["currentTemp"] == 71.6
    assert city["metarTemp"] == 69.8
    assert city["currentWindSpeed"] == 24.4
    assert city["currentWindGust"] == 41.1
    assert city["currentPrecipitation"] == 1.2
    assert city["todayWindGust"] == 52.7
    assert city["todayPrecipitationProbability"] == 85
    assert city["forecastPrecipitationSum"] == 9.6
    assert city["windSpeedUnit"] == "km/h"
    assert city["precipitationUnit"] == "mm"
    assert city["hourly"][1]["windGust"] == 49.2
    assert city["daily"][0]["precipitationSum"] == 9.6
    assert city["quoteCoverage"] == "1/1"
    assert city["topBin"]["midPriceYes"] == 0.33
    assert request_params["openMeteo"]["current"] == "temperature_2m,weather_code,precipitation,wind_speed_10m,wind_gusts_10m"
    assert "precipitation_probability" in request_params["openMeteo"]["hourly"]
    assert "wind_gusts_10m_max" in request_params["openMeteo"]["daily"]


def test_global_weather_map_uses_wttr_real_intensity_when_open_meteo_errors(monkeypatch):
    monkeypatch.setattr(global_weather_map_service, "_clob_yes_quote", lambda ctx, market: {"bookStatus": "no-book"})

    def http_json_get(url, *, params=None, **kwargs):
        if "open.example" in url:
            return {"error": True, "reason": "Daily API request limit exceeded. Please try again tomorrow."}
        if "wttr.in" in url:
            return {
                "current_condition": [
                    {
                        "temp_F": "68",
                        "weatherCode": "116",
                        "precipMM": "1.4",
                        "windspeedKmph": "24",
                        "weatherDesc": [{"value": "Partly cloudy"}],
                    }
                ],
                "weather": [
                    {
                        "date": "2026-05-12",
                        "maxtempF": "79",
                        "mintempF": "63",
                        "hourly": [
                            {
                                "time": "0",
                                "tempF": "69",
                                "precipMM": "0.0",
                                "chanceofrain": "15",
                                "windspeedKmph": "18",
                                "WindGustKmph": "25",
                                "weatherCode": "116",
                            },
                            {
                                "time": "300",
                                "tempF": "70",
                                "precipMM": "1.2",
                                "chanceofrain": "60",
                                "windspeedKmph": "21",
                                "WindGustKmph": "31",
                                "weatherCode": "119",
                            },
                        ],
                    },
                    {
                        "date": "2026-05-13",
                        "maxtempF": "82",
                        "mintempF": "61",
                        "hourly": [
                            {
                                "time": "0",
                                "tempF": "71",
                                "precipMM": "2.7",
                                "chanceofrain": "75",
                                "windspeedKmph": "27",
                                "WindGustKmph": "39",
                                "weatherCode": "296",
                            }
                        ],
                    },
                ],
            }
        if "aviation.example" in url:
            return []
        if "gamma.example" in url:
            return []
        return []

    ctx = make_ctx(http_json_get=http_json_get)
    payload = global_weather_map_service.build_global_weather_map_payload(ctx, limit=1)

    city = payload["items"][0]
    assert city["currentTemp"] == 68.0
    assert city["currentWindSpeed"] == 24.0
    assert city["currentPrecipitation"] == 1.4
    assert city["todayWindGust"] == 31.0
    assert city["todayPrecipitationSum"] == 1.2
    assert city["todayPrecipitationProbability"] == 60.0
    assert city["forecastWindSpeedMax"] == 27.0
    assert city["forecastWindGustMax"] == 39.0
    assert city["forecastPrecipitationSum"] == 2.7
    assert city["forecastPrecipitationProbabilityMax"] == 75.0
    assert city["hourly"][1]["windGust"] == 31.0
    assert city["daily"][0]["precipitationProbabilityMax"] == 60.0
    assert city["windSpeedUnit"] == "km/h"
    assert city["precipitationUnit"] == "mm"
    assert city["sourceStates"]["openMeteo"] == "error"
    assert city["sourceStates"]["wttr"] == "ok"


def test_global_weather_map_uses_local_market_database_before_gamma(monkeypatch):
    monkeypatch.setattr(global_weather_map_service, "_clob_yes_quote", lambda ctx, market: {"bestBidYes": 0.44, "bestAskYes": 0.48})
    db_rows = [
        {
            "market_id": 501,
            "slug": "highest-temperature-in-new-york-on-may-12-2026-80forhigher",
            "title": "Will the highest temperature in New York City be 80°F or higher on May 12?",
            "description": "",
            "end_date": "2026-05-12T12:00:00Z",
            "created_at": "2026-05-12T00:00:00Z",
            "yes_token_id": "yes-token",
            "no_token_id": "no-token",
            "clob_token_ids": '["yes-token", "no-token"]',
            "latest_yes_price": None,
            "latest_trade_price": None,
            "serving_latest_price": None,
            "latest_trade_at": None,
            "serving_latest_trade_at": None,
            "is_trading_closed": 0,
            "is_resolved": 0,
            "gamma_closed": 0,
        }
    ]

    def http_json_get(url, *, params=None, **kwargs):
        if "open.example" in url:
            return [
                {
                    "current": {"temperature_2m": 22.0, "weather_code": 2, "time": "2026-05-12T12:00"},
                    "hourly": {"time": ["2026-05-12T12:00"], "temperature_2m": [22.0]},
                    "daily": {"time": ["2026-05-12"], "temperature_2m_max": [27.0], "temperature_2m_min": [16.0]},
                }
            ]
        if "aviation.example" in url:
            return []
        if "gamma.example" in url:
            raise AssertionError("Gamma should not be queried when local DB has a city market group")
        return []

    ctx = make_ctx(http_json_get=http_json_get)
    fake_conn = FakeConnection(db_rows)
    ctx["DB_PATH"] = "fake"
    ctx["get_connection"] = lambda *args, **kwargs: fake_conn
    payload = global_weather_map_service.build_global_weather_map_payload(ctx, limit=1)
    city = payload["items"][0]

    assert fake_conn.closed is True
    assert city["sourceStates"]["polymarket"] == "ok"
    assert city["marketSource"] == "psql-db"
    assert city["quoteCoverage"] == "1/1"
    assert city["topBin"]["label"] == "Will the highest temperature in New York City be 80°F or higher on May 12?"
    assert city["topBin"]["midPriceYes"] == 0.46
    assert city["bins"][0]["marketSlug"] == "highest-temperature-in-new-york-on-may-12-2026-80forhigher"


def test_global_weather_map_ignores_closed_db_weather_markets(monkeypatch):
    monkeypatch.setattr(
        global_weather_map_service,
        "_clob_yes_quote",
        lambda ctx, market: {"bestBidYes": None, "bestAskYes": None, "bookStatus": "no-book"},
    )
    db_rows = [
        {
            "market_id": 601,
            "slug": "highest-temperature-in-new-york-on-may-12-2026-80forhigher",
            "title": "Will the highest temperature in New York City be 80°F or higher on May 12?",
            "description": "",
            "end_date": "2026-05-12T12:00:00Z",
            "created_at": "2026-05-12T00:00:00Z",
            "yes_token_id": "closed-yes",
            "no_token_id": "closed-no",
            "clob_token_ids": '["closed-yes", "closed-no"]',
            "latest_yes_price": 0.99,
            "latest_trade_price": 0.99,
            "serving_latest_price": 0.99,
            "latest_trade_at": "2026-05-12T11:58:00Z",
            "serving_latest_trade_at": "2026-05-12T11:58:00Z",
            "is_trading_closed": 1,
            "is_resolved": 0,
            "gamma_closed": 0,
        },
        {
            "market_id": 602,
            "slug": "highest-temperature-in-new-york-on-may-12-2026-82forhigher",
            "title": "Will the highest temperature in New York City be 82°F or higher on May 12?",
            "description": "",
            "end_date": "2026-05-12T12:00:00Z",
            "created_at": "2026-05-12T00:00:00Z",
            "yes_token_id": "active-yes",
            "no_token_id": "active-no",
            "clob_token_ids": '["active-yes", "active-no"]',
            "latest_yes_price": 0.24,
            "latest_trade_price": 0.24,
            "serving_latest_price": 0.24,
            "latest_trade_at": "2026-05-12T11:58:00Z",
            "serving_latest_trade_at": "2026-05-12T11:58:00Z",
            "is_trading_closed": 0,
            "is_resolved": 0,
            "gamma_closed": 0,
        },
    ]

    def http_json_get(url, *, params=None, **kwargs):
        if "open.example" in url:
            return [
                {
                    "current": {"temperature_2m": 22.0, "weather_code": 2, "time": "2026-05-12T12:00"},
                    "hourly": {"time": ["2026-05-12T12:00"], "temperature_2m": [22.0]},
                    "daily": {"time": ["2026-05-12"], "temperature_2m_max": [27.0], "temperature_2m_min": [16.0]},
                }
            ]
        if "aviation.example" in url:
            return []
        return []

    ctx = make_ctx(http_json_get=http_json_get)
    ctx["DB_PATH"] = "fake"
    ctx["get_connection"] = lambda *args, **kwargs: FakeConnection(db_rows)
    payload = global_weather_map_service.build_global_weather_map_payload(ctx, limit=1)
    bins = payload["items"][0]["bins"]

    assert len(bins) == 1
    assert bins[0]["marketId"] == 602
    assert bins[0]["midPriceYes"] == 0.24
    assert bins[0]["marketStatus"] == "live"
    assert payload["items"][0]["marketSource"] == "psql-db"


def test_global_weather_map_indexes_low_temperature_and_precipitation(monkeypatch):
    monkeypatch.setattr(global_weather_map_service, "_clob_yes_quote", lambda ctx, market: {"bookStatus": "no-book"})
    db_rows = [
        {
            "market_id": 601,
            "slug": "lowest-temperature-in-nyc-on-may-12-2026-60-61f",
            "title": "Will the lowest temperature in New York City be between 60-61°F on May 12?",
            "description": "",
            "end_date": "2026-05-12T12:00:00Z",
            "created_at": "2026-05-12T00:00:00Z",
            "yes_token_id": "low-yes",
            "no_token_id": "low-no",
            "clob_token_ids": '["low-yes", "low-no"]',
            "latest_yes_price": 0.42,
            "latest_trade_price": None,
            "serving_latest_price": None,
            "latest_trade_at": None,
            "serving_latest_trade_at": None,
            "is_trading_closed": 0,
            "is_resolved": 0,
            "gamma_closed": 0,
        },
        {
            "market_id": 602,
            "slug": "will-nyc-have-between-2-and-3-inches-of-precipitation-in-may",
            "title": "Will NYC have between 2-3 inches of precipitation in May?",
            "description": "",
            "end_date": "2026-05-31T12:00:00Z",
            "created_at": "2026-05-12T00:00:00Z",
            "yes_token_id": "rain-yes",
            "no_token_id": "rain-no",
            "clob_token_ids": '["rain-yes", "rain-no"]',
            "latest_yes_price": 0.7,
            "latest_trade_price": None,
            "serving_latest_price": None,
            "latest_trade_at": None,
            "serving_latest_trade_at": None,
            "is_trading_closed": 0,
            "is_resolved": 0,
            "gamma_closed": 0,
        },
    ]

    def http_json_get(url, *, params=None, **kwargs):
        if "open.example" in url:
            return [
                {
                    "current": {"temperature_2m": 22.0, "weather_code": 2, "time": "2026-05-12T12:00"},
                    "hourly": {"time": ["2026-05-12T12:00"], "temperature_2m": [22.0]},
                    "daily": {"time": ["2026-05-12"], "temperature_2m_max": [27.0], "temperature_2m_min": [16.0]},
                }
            ]
        if "aviation.example" in url:
            return []
        return []

    ctx = make_ctx(http_json_get=http_json_get)
    ctx["DB_PATH"] = "fake"
    ctx["get_connection"] = lambda *args, **kwargs: FakeConnection(db_rows)
    payload = global_weather_map_service.build_global_weather_map_payload(ctx, limit=1)
    city = payload["items"][0]
    families = set(city["marketFamilies"])
    assert "lowest_temperature" in families
    assert "precipitation" in families
    precip = [market for market in city["markets"] if market["marketFamily"] == "precipitation"][0]
    assert precip["topBin"]["unit"] == "in"
    assert precip["topBin"]["midPriceYes"] == 0.7


def test_global_weather_map_filters_hurricanes_sports_false_positives(monkeypatch):
    monkeypatch.setattr(global_weather_map_service, "_clob_yes_quote", lambda ctx, market: {"bookStatus": "no-book"})
    db_rows = [
        {
            "market_id": 701,
            "slug": "will-carolina-hurricanes-win-the-2026-nhl-stanley-cup",
            "title": "Will the Carolina Hurricanes win the 2026 NHL Stanley Cup?",
            "description": "",
            "end_date": "2026-06-30T00:00:00Z",
            "created_at": "2026-05-12T00:00:00Z",
            "yes_token_id": "sports-yes",
            "no_token_id": "sports-no",
            "clob_token_ids": '["sports-yes", "sports-no"]',
            "latest_yes_price": 0.12,
            "latest_trade_price": None,
            "serving_latest_price": None,
            "latest_trade_at": None,
            "serving_latest_trade_at": None,
            "is_trading_closed": 0,
            "is_resolved": 0,
            "gamma_closed": 0,
        },
        {
            "market_id": 702,
            "slug": "will-a-hurricane-form-by-may-31",
            "title": "Will a hurricane form by May 31?",
            "description": "",
            "end_date": "2026-05-31T00:00:00Z",
            "created_at": "2026-05-12T00:00:00Z",
            "yes_token_id": "storm-yes",
            "no_token_id": "storm-no",
            "clob_token_ids": '["storm-yes", "storm-no"]',
            "latest_yes_price": 0.44,
            "latest_trade_price": None,
            "serving_latest_price": None,
            "latest_trade_at": None,
            "serving_latest_trade_at": None,
            "is_trading_closed": 0,
            "is_resolved": 0,
            "gamma_closed": 0,
        },
    ]

    def http_json_get(url, *, params=None, **kwargs):
        if "open.example" in url:
            return [
                {
                    "current": {"temperature_2m": 22.0, "weather_code": 2, "time": "2026-05-12T12:00"},
                    "hourly": {"time": ["2026-05-12T12:00"], "temperature_2m": [22.0]},
                    "daily": {"time": ["2026-05-12"], "temperature_2m_max": [27.0], "temperature_2m_min": [16.0]},
                }
            ]
        if "aviation.example" in url:
            return []
        return []

    ctx = make_ctx(http_json_get=http_json_get)
    ctx["DB_PATH"] = "fake"
    ctx["get_connection"] = lambda *args, **kwargs: FakeConnection(db_rows)
    payload = global_weather_map_service.build_global_weather_map_payload(ctx, limit=1)

    assert payload["summary"]["marketFamilyCounts"] == {"hurricane": 1}
    assert payload["unmappedMarkets"][0]["title"] == "Will a hurricane form by May 31?"
    assert all("Carolina Hurricanes" not in str(item) for item in payload["unmappedMarkets"])


def test_global_weather_map_prefers_clob_book_over_db_price(monkeypatch):
    calls = {"clob": 0}

    def clob_quote(ctx, market):
        calls["clob"] += 1
        return {"bestBidYes": 0.24, "bestAskYes": 0.30, "bookStatus": "ok", "yesTokenId": "yes-token"}

    monkeypatch.setattr(global_weather_map_service, "_clob_yes_quote", clob_quote)
    db_rows = [
        {
            "market_id": 502,
            "slug": "highest-temperature-in-new-york-on-may-12-2026-80forhigher",
            "title": "Will the highest temperature in New York City be 80°F or higher on May 12?",
            "description": "",
            "end_date": "2026-05-12T12:00:00Z",
            "created_at": "2026-05-12T00:00:00Z",
            "yes_token_id": "yes-token",
            "no_token_id": "no-token",
            "clob_token_ids": '["yes-token", "no-token"]',
            "latest_yes_price": 0.77,
            "latest_trade_price": 0.77,
            "serving_latest_price": 0.77,
            "latest_trade_at": "2026-05-12T11:58:00Z",
            "serving_latest_trade_at": "2026-05-12T11:58:00Z",
            "is_trading_closed": 0,
            "is_resolved": 0,
            "gamma_closed": 0,
        }
    ]

    def http_json_get(url, *, params=None, **kwargs):
        if "open.example" in url:
            return [
                {
                    "current": {"temperature_2m": 22.0, "weather_code": 2, "time": "2026-05-12T12:00"},
                    "hourly": {"time": ["2026-05-12T12:00"], "temperature_2m": [22.0]},
                    "daily": {"time": ["2026-05-12"], "temperature_2m_max": [27.0], "temperature_2m_min": [16.0]},
                }
            ]
        if "aviation.example" in url:
            return []
        if "gamma.example" in url:
            raise AssertionError("Gamma should not be queried when DB has a fallback price")
        return []

    ctx = make_ctx(http_json_get=http_json_get)
    ctx["DB_PATH"] = "fake"
    ctx["get_connection"] = lambda *args, **kwargs: FakeConnection(db_rows)
    payload = global_weather_map_service.build_global_weather_map_payload(ctx, limit=1)
    top_bin = payload["items"][0]["topBin"]

    assert calls["clob"] == 1
    assert top_bin["midPriceYes"] == 0.27
    assert top_bin["priceSource"] == "clob-book"
    assert top_bin["bookStatus"] == "ok"


def test_global_weather_map_keeps_db_price_when_clob_has_no_book(monkeypatch):
    monkeypatch.setattr(
        global_weather_map_service,
        "_clob_yes_quote",
        lambda ctx, market: {"bestBidYes": None, "bestAskYes": None, "bookStatus": "no-book", "yesTokenId": "yes-token"},
    )
    db_rows = [
        {
            "market_id": 503,
            "slug": "highest-temperature-in-new-york-on-may-12-2026-80forhigher",
            "title": "Will the highest temperature in New York City be 80°F or higher on May 12?",
            "description": "",
            "end_date": "2026-05-12T12:00:00Z",
            "created_at": "2026-05-12T00:00:00Z",
            "yes_token_id": "yes-token",
            "no_token_id": "no-token",
            "clob_token_ids": '["yes-token", "no-token"]',
            "latest_yes_price": 0.77,
            "latest_trade_price": 0.77,
            "serving_latest_price": 0.77,
            "latest_trade_at": "2026-05-12T11:58:00Z",
            "serving_latest_trade_at": "2026-05-12T11:58:00Z",
            "is_trading_closed": 0,
            "is_resolved": 0,
            "gamma_closed": 0,
        }
    ]

    def http_json_get(url, *, params=None, **kwargs):
        if "open.example" in url:
            return [
                {
                    "current": {"temperature_2m": 22.0, "weather_code": 2, "time": "2026-05-12T12:00"},
                    "hourly": {"time": ["2026-05-12T12:00"], "temperature_2m": [22.0]},
                    "daily": {"time": ["2026-05-12"], "temperature_2m_max": [27.0], "temperature_2m_min": [16.0]},
                }
            ]
        if "aviation.example" in url:
            return []
        return []

    ctx = make_ctx(http_json_get=http_json_get)
    ctx["DB_PATH"] = "fake"
    ctx["get_connection"] = lambda *args, **kwargs: FakeConnection(db_rows)
    payload = global_weather_map_service.build_global_weather_map_payload(ctx, limit=1)
    top_bin = payload["items"][0]["topBin"]

    assert top_bin["midPriceYes"] == 0.77
    assert top_bin["priceSource"] == "db-latest"
    assert top_bin["bookStatus"] == "no-book"


def test_global_weather_map_seeded_snapshot_does_not_live_fetch():
    seeded = {"generatedAt": "2026-05-12T00:00:00Z", "status": "ok", "items": [{"cityId": "seed", "city": "Seed"}], "summary": {"cityCount": 1, "mappedCount": 1}}
    ctx = make_ctx(cached=seeded)
    payload = global_weather_map_service.get_global_weather_map_snapshot(ctx, limit=1)

    assert payload["cacheMode"] == "redis-seed"
    assert payload["items"][0]["city"] == "Seed"
    assert ctx["_calls"]["json"] == 0


def test_global_weather_map_failure_returns_warming_payload():
    ctx = make_ctx()
    payload = global_weather_map_service.get_global_weather_map_snapshot(ctx, limit=1, allow_live_build=False)

    assert payload["status"] == "warming"
    assert payload["items"] == []
    assert payload["cacheMode"] == "seed-miss"


def test_global_weather_map_cold_start_returns_fast_and_schedules_refresh(monkeypatch):
    scheduled = {}

    def schedule(ctx, *, limit, ttl_seconds, reason):
        scheduled.update({"limit": limit, "ttl": ttl_seconds, "reason": reason})
        return True

    monkeypatch.setattr(global_weather_map_service, "_schedule_live_refresh", schedule)
    ctx = make_ctx()
    payload = global_weather_map_service.get_global_weather_map_snapshot(ctx, limit=2)

    assert payload["status"] == "warming"
    assert payload["items"] == []
    assert payload["cacheMode"] == "seed-miss-refreshing"
    assert scheduled == {"limit": 2, "ttl": 300, "reason": "seed-miss"}
    assert ctx["_calls"]["json"] == 0


def test_global_weather_map_watcher_context_can_read_market_database():
    watcher = global_weather_map_watcher.GlobalWeatherMapWatcher.__new__(global_weather_map_watcher.GlobalWeatherMapWatcher)
    watcher.settings = make_settings()
    watcher.snapshot_store = FakeStore()
    watcher._get_cached_json = lambda namespace, cache_key: None
    watcher._set_cached_json = lambda namespace, cache_key, payload, ttl: None
    watcher._http_json_get = lambda *args, **kwargs: {}

    ctx = watcher.context()

    assert callable(ctx["get_connection"])
    assert ctx["DB_PATH"]


def test_global_weather_map_market_discovery_is_city_tolerant(monkeypatch):
    monkeypatch.setattr(global_weather_map_service, "_clob_yes_quote", lambda ctx, market: {"bestBidYes": 0.41, "bestAskYes": 0.45})

    def http_json_get(url, *, params=None, **kwargs):
        if "open.example" in url:
            return [
                {
                    "current": {"temperature_2m": 22.0, "weather_code": 2, "time": "2026-05-12T12:00"},
                    "hourly": {"time": ["2026-05-12T12:00"], "temperature_2m": [22.0]},
                    "daily": {"time": ["2026-05-12"], "temperature_2m_max": [27.0], "temperature_2m_min": [16.0]},
                },
                {
                    "current": {"temperature_2m": 18.0, "weather_code": 1, "time": "2026-05-12T12:00"},
                    "hourly": {"time": ["2026-05-12T12:00"], "temperature_2m": [18.0]},
                    "daily": {"time": ["2026-05-12"], "temperature_2m_max": [24.0], "temperature_2m_min": [14.0]},
                },
            ]
        if "aviation.example" in url:
            return [{"icaoId": "KNYC", "temp": 21, "reportTime": "2026-05-12T11:50:00Z"}]
        if "gamma.example" in url:
            query = str((params or {}).get("q") or "")
            if "Chicago" in query:
                raise RuntimeError("gamma temporary miss")
            return [
                {
                    "id": "evt-1",
                    "slug": "highest-temperature-in-new-york-on-may-12-2026",
                    "title": "Highest temperature in New York on May 12?",
                    "active": True,
                    "closed": False,
                    "markets": [
                        {"id": "m1", "question": "Highest temperature in New York on May 12? 80°F or higher", "slug": "ny-80", "outcomePrices": ["0.40", "0.60"], "active": True}
                    ],
                }
            ]
        return []

    ctx = make_ctx(http_json_get=http_json_get)
    payload = global_weather_map_service.build_global_weather_map_payload(ctx, limit=2)
    by_city = {item["cityId"]: item for item in payload["items"]}

    assert int(payload["summary"]["mappedCount"]) >= 2
    assert payload["summary"]["liveMarketCount"] == 1
    assert payload["sources"]["gamma"] == "partial"
    assert by_city["new-york"]["sourceStates"]["polymarket"] == "ok"
    assert by_city["chicago"]["sourceStates"]["polymarket"] == "error"


def test_weather_news_builds_filters_dedupes_and_ranks():
    rss = """<?xml version="1.0"?><rss><channel>
      <item><title>New York weather warning: heavy rain</title><link>https://news.example/a</link><source>WX News</source><pubDate>Tue, 12 May 2026 10:00:00 GMT</pubDate><description>Storm warning and rain forecast.</description></item>
      <item><title>New York sports update</title><link>https://news.example/b</link><source>Sports</source><pubDate>Tue, 12 May 2026 09:00:00 GMT</pubDate><description>Baseball result.</description></item>
    </channel></rss>"""
    ctx = make_ctx(http_text_get=lambda *args, **kwargs: rss)
    payload = weather_news_service.build_weather_news_payload(ctx, limit=3)

    assert payload["status"] == "ok"
    assert payload["summary"]["articleCount"] == 1
    assert payload["items"][0]["severity"] == "warning"
    assert payload["items"][0]["city"] == "New York"


def test_weather_news_seeded_snapshot_does_not_live_fetch():
    seeded = {"generatedAt": "2026-05-12T00:00:00Z", "status": "ok", "items": [{"id": "seed", "title": "Seed weather"}], "summary": {"articleCount": 1}}
    ctx = make_ctx(cached=seeded)
    payload = weather_news_service.get_weather_news_snapshot(ctx, limit=1)

    assert payload["cacheMode"] == "redis-seed"
    assert payload["items"][0]["title"] == "Seed weather"
    assert ctx["_calls"]["text"] == 0


def test_weather_news_bad_xml_degrades_without_items():
    ctx = make_ctx(http_text_get=lambda *args, **kwargs: "<rss>")
    payload = weather_news_service.build_weather_news_payload(ctx, limit=3)

    assert payload["status"] == "degraded"
    assert payload["items"] == []
    assert any(value == "error" for value in payload["sources"].values())


def test_weather_news_filters_sports_storm_false_positives():
    rss = """<?xml version="1.0"?><rss><channel>
      <item><title>Eels v Storm: Moses riding high</title><link>https://news.example/sports</link><source>NRL.com</source><pubDate>Tue, 12 May 2026 10:00:00 GMT</pubDate><description>NRL team news and picks.</description></item>
      <item><title>Melbourne ambush: Storm snap losing streak</title><link>https://news.example/storm-team</link><source>Daily Telegraph Sydney</source><pubDate>Tue, 12 May 2026 09:45:00 GMT</pubDate><description>Emotional Bellamy return.</description></item>
      <item><title>Perth property bloodbath warning amid housing crash</title><link>https://news.example/property</link><source>PerthNow</source><pubDate>Tue, 12 May 2026 09:30:00 GMT</pubDate><description>Property market warning.</description></item>
      <item><title>Johannesburg severe storm disrupts flights</title><link>https://news.example/weather</link><source>Travel Desk</source><pubDate>Tue, 12 May 2026 09:00:00 GMT</pubDate><description>Severe storm and wind delays expected.</description></item>
      <item><title>Chicago weather: Tracking late storm chances</title><link>https://news.example/chicago</link><source>FOX 32 Chicago</source><pubDate>Tue, 12 May 2026 08:00:00 GMT</pubDate><description>Storm chance returns Tuesday.</description></item>
    </channel></rss>"""
    ctx = make_ctx(http_text_get=lambda *args, **kwargs: rss)
    payload = weather_news_service.build_weather_news_payload(ctx, limit=5)

    titles = [item["title"] for item in payload["items"]]
    assert "Johannesburg severe storm disrupts flights" in titles
    assert "Chicago weather: Tracking late storm chances" in titles
    assert all("Eels v Storm" not in title for title in titles)
    assert all("Storm snap losing streak" not in title for title in titles)
    assert all("property bloodbath" not in title for title in titles)


def test_snapshot_store_keeps_expired_payload_for_stale_fallback(tmp_path):
    payload = {"items": [{"id": "weather-seed"}], "status": "ok"}
    store = SnapshotStore(str(tmp_path / "snapshots.sqlite3"))
    store.set("snapshot:test", "weather", payload, 60)
    with sqlite3.connect(str(tmp_path / "snapshots.sqlite3")) as conn:
        conn.execute("UPDATE panel_snapshots SET expires_at = 0 WHERE namespace = ? AND cache_key = ?", ("snapshot:test", "weather"))
        conn.commit()

    assert store.get("snapshot:test", "weather") is None
    assert store.get_stale("snapshot:test", "weather") == payload


def test_global_weather_map_carries_forward_weather_series_when_open_meteo_fails(monkeypatch):
    previous_map = {
        "items": [
            {
                "cityId": "new-york",
                "city": "New York",
                "currentTemp": 74,
                "currentWindSpeed": 24,
                "currentWindGust": 41,
                "currentPrecipitation": 2.2,
                "condition": "Clear",
                "todayHigh": 91,
                "todayLow": 69,
                "todayWindSpeed": 32,
                "todayWindGust": 56,
                "todayPrecipitationSum": 11,
                "todayPrecipitationProbability": 82,
                "forecastHigh": 98,
                "forecastWindSpeedMax": 34,
                "forecastWindGustMax": 61,
                "forecastPrecipitationSum": 18,
                "forecastPrecipitationProbabilityMax": 88,
                "windSpeedUnit": "km/h",
                "precipitationUnit": "mm",
                "hourly": [{"time": "2026-05-22T00:00:00Z", "temp": 74, "windSpeed": 22, "windGust": 39, "precipitation": 1.4, "precipitationProbability": 75}],
                "daily": [{"date": "2026-05-22", "high": 91, "low": 69, "windGustMax": 56, "precipitationSum": 11, "precipitationProbabilityMax": 82}],
                "sourceStates": {"openMeteo": "ok", "metar": "ok", "polymarket": "ok"},
            }
        ],
        "summary": {"mappedCount": 1, "marketFamilyCounts": {"highest_temperature": 1}},
        "sources": {"openMeteo": "ok"},
        "status": "ok",
    }
    fresh_partial = {
        "items": [
            {
                "cityId": "new-york",
                "city": "New York",
                "metarTemp": 59,
                "quoteCoverage": "11/11",
                "eventSlug": "weather-new-york",
                "sourceStates": {"openMeteo": "error", "metar": "ok", "polymarket": "ok"},
            }
        ],
        "summary": {"mappedCount": 1, "marketFamilyCounts": {"highest_temperature": 1}},
        "sources": {"openMeteo": "error", "aviationWeather": "ok", "gamma": "ok", "clob": "ok"},
        "status": "degraded",
    }
    map_watcher = global_weather_map_watcher.GlobalWeatherMapWatcher.__new__(global_weather_map_watcher.GlobalWeatherMapWatcher)
    map_watcher.previous = lambda: previous_map
    map_watcher.context = lambda: {}
    stored = {}
    map_watcher.store_payload = lambda payload: stored.setdefault("payload", payload)
    map_watcher.store_meta = lambda **kwargs: stored.setdefault("meta", kwargs)
    monkeypatch.setattr(global_weather_map_watcher.global_weather_map_service, "build_global_weather_map_payload", lambda ctx: fresh_partial)

    result = map_watcher.run_once()

    assert result["status"] == "stored"
    item = stored["payload"]["items"][0]
    assert item["hourly"] == previous_map["items"][0]["hourly"]
    assert item["daily"] == previous_map["items"][0]["daily"]
    assert item["currentTemp"] == 74
    assert item["currentWindSpeed"] == 24
    assert item["currentWindGust"] == 41
    assert item["todayPrecipitationProbability"] == 82
    assert item["forecastWindGustMax"] == 61
    assert item["precipitationUnit"] == "mm"
    assert item["metarTemp"] == 59
    assert item["quoteCoverage"] == "11/11"
    assert item["sourceStates"]["openMeteo"] == "stale"
    assert stored["payload"]["sources"]["openMeteo"] == "stale"


def test_watchers_preserve_previous_on_empty_or_exception(monkeypatch):
    previous_map = {
        "items": [{"cityId": "new-york", "currentTemp": 72, "hourly": [{"temp": 72}]}],
        "summary": {"mappedCount": 1},
        "sources": {"openMeteo": "ok"},
        "status": "ok",
    }
    map_watcher = global_weather_map_watcher.GlobalWeatherMapWatcher.__new__(global_weather_map_watcher.GlobalWeatherMapWatcher)
    map_watcher.previous = lambda: previous_map
    map_watcher.context = lambda: {}
    stored = {}
    map_watcher.store_payload = lambda payload: stored.setdefault("map_payload", payload)
    map_watcher.store_meta = lambda **kwargs: stored.setdefault("map_meta", kwargs)
    monkeypatch.setattr(global_weather_map_watcher.global_weather_map_service, "build_global_weather_map_payload", lambda ctx: {"items": [], "sources": {"openMeteo": "empty"}, "status": "empty"})

    result = map_watcher.run_once()

    assert result["status"] == "preserved"
    assert stored["map_payload"] is previous_map
    assert stored["map_meta"]["preserve"] is True

    stored.clear()
    monkeypatch.setattr(global_weather_map_watcher.global_weather_map_service, "build_global_weather_map_payload", lambda ctx: {"items": [{"cityId": "new-york", "quoteCoverage": "11/11"}], "sources": {"openMeteo": "error"}, "summary": {"mappedCount": 0}, "status": "warming"})

    result = map_watcher.run_once()

    assert result["status"] == "preserved"
    assert stored["map_payload"] is previous_map
    assert stored["map_meta"]["preserve"] is True

    previous_news = {"items": [{"id": "n1"}], "sources": {"googleNews": "ok"}, "status": "ok"}
    news_watcher = weather_news_watcher.WeatherNewsWatcher.__new__(weather_news_watcher.WeatherNewsWatcher)
    news_watcher.previous = lambda: previous_news
    news_watcher.context = lambda: {}
    news_watcher.settings = make_settings()
    news_watcher.store_payload = lambda payload: stored.setdefault("news_payload", payload)
    news_watcher.store_meta = lambda **kwargs: stored.setdefault("news_meta", kwargs)
    monkeypatch.setattr(weather_news_watcher.weather_news_service, "build_weather_news_payload", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    result = news_watcher.run_once()

    assert result["status"] == "preserved"
    assert stored["news_payload"] is previous_news
    assert stored["news_meta"]["preserve"] is True
