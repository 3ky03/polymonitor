from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import food_retail_basket_service


class FakeLogger:
    def exception(self, *args, **kwargs) -> None:
        return None


def fred_csv(series_id: str, values: list[float]) -> str:
    months = [
        "2025-03-01",
        "2025-04-01",
        "2025-05-01",
        "2025-06-01",
        "2025-07-01",
        "2025-08-01",
        "2025-09-01",
        "2025-10-01",
        "2025-11-01",
        "2025-12-01",
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
        "2026-04-01",
    ]
    rows = ["observation_date," + series_id]
    rows.extend(f"{month},{value}" for month, value in zip(months, values))
    return "\n".join(rows)


def make_ctx(*, fail: bool = False):
    requested_urls = []
    series_values = {
        "CPIUFDSL": [320, 321, 322, 322.5, 323, 323.4, 323.9, 324.2, 324.8, 325.2, 325.6, 326.0, 326.5, 327.2],
        "CUSR0000SAF11": [300, 300.4, 301, 301.4, 302, 302.6, 303, 303.7, 304.2, 304.8, 305.4, 306.2, 307, 308.4],
        "CUSR0000SAF112": [280, 280.2, 280.7, 281, 281.3, 281.9, 282.4, 283.1, 283.8, 284.5, 285.0, 285.8, 286.4, 287.6],
        "CUSR0000SAF113": [260, 260.5, 260.9, 261.2, 261.6, 262, 262.5, 262.8, 263.0, 263.3, 263.7, 264.1, 264.6, 264.9],
        "CUSR0000SEFJ": [240, 238, 235, 233, 234, 236, 237, 239, 241, 244, 247, 250, 255, 263],
    }

    def http_text_get(url: str, **kwargs):
        requested_urls.append(url)
        if fail:
            raise RuntimeError("fred failed")
        series_id = parse_qs(urlsplit(url).query)["id"][0]
        return fred_csv(series_id, series_values[series_id])

    return {
        "SETTINGS": SimpleNamespace(
            food_basket_fred_csv_url_template="https://fred.example/graph.csv?id={series_id}",
            food_basket_source_url="https://fred.example/",
            food_basket_ttl_seconds=21600,
        ),
        "app": SimpleNamespace(logger=FakeLogger()),
        "utc_now_iso": lambda: "2026-05-11T00:00:00Z",
        "http_text_get": http_text_get,
        "SNAPSHOT_STORE": None,
        "get_cached_json": lambda *args: None,
        "set_cached_json": lambda *args: None,
        "requested_urls": requested_urls,
    }


def test_food_retail_basket_builds_official_component_snapshot():
    ctx = make_ctx()
    payload = food_retail_basket_service.get_food_retail_basket_snapshot(ctx, limit=4)

    assert payload["status"] == "ok"
    assert payload["cacheMode"] == "live-build"
    assert payload["summary"]["coverage"] == 5
    assert payload["summary"]["signal"] in {"FOOD PRESSURE RISING", "FOOD STABLE", "FOOD DISINFLATION"}
    assert len(payload["items"]) == 4
    assert payload["items"][0]["source"] == "FRED / BLS CPI"
    assert payload["items"][0]["momPct"] > 0
    assert all("cosd=" in url for url in ctx["requested_urls"])


def test_food_retail_basket_warms_when_all_sources_fail():
    payload = food_retail_basket_service.get_food_retail_basket_snapshot(make_ctx(fail=True))

    assert payload["status"] == "warming"
    assert payload["items"] == []
    assert payload["sources"]["food"] == "error"
