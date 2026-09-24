from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import macro_cpi_panels_service


class FakeLogger:
    def exception(self, *args, **kwargs) -> None:
        return None


class FakeApp:
    logger = FakeLogger()


def fred_csv(series_id: str, values: list[float]) -> str:
    rows = ["observation_date," + series_id]
    for index, value in enumerate(values, start=1):
        rows.append(f"2025-{index:02d}-01,{value}")
    return "\n".join(rows)


def make_context():
    cached = {}
    requested_urls = []

    def http_text_get(url, **kwargs):
        requested_urls.append(url)
        series_id = parse_qs(urlsplit(url).query)["id"][0]
        return fred_csv(series_id, [100 + i for i in range(13)])

    def http_json_get(url, **kwargs):
        return {
            "results": [
                {
                    "title": "Tariff notice on imported goods",
                    "publication_date": "2026-05-01",
                    "html_url": "https://federalregister.example/doc",
                }
            ]
        }

    return {
        "SETTINGS": SimpleNamespace(
            food_basket_fred_csv_url_template="https://fred.example/graph.csv?id={series_id}",
            geo_shock_federal_register_api_url="https://federalregister.example/api",
            macro_cpi_panel_ttl_seconds=3600,
        ),
        "app": FakeApp(),
        "utc_now_iso": lambda: "2026-05-12T00:00:00Z",
        "http_text_get": http_text_get,
        "http_json_get": http_json_get,
        "SNAPSHOT_STORE": None,
        "get_cached_json": lambda namespace, key: cached.get((namespace, key)),
        "set_cached_json": lambda namespace, key, payload, ttl: cached.__setitem__((namespace, key), payload),
        "requested_urls": requested_urls,
    }


def test_macro_cpi_panel_builds_fred_and_policy_items():
    ctx = make_context()
    payload = macro_cpi_panels_service.get_supply_tariff_import_watch_snapshot(ctx, limit=8, allow_live_build=True)

    assert payload["status"] == "ok"
    assert payload["cacheMode"] == "live-build"
    assert payload["summary"]["coverage"] >= 3
    assert any(item["source"] == "Federal Register" for item in payload["items"])
    assert payload["sources"]["ppi_all"] == "ok"
    assert all("cosd=" in url for url in ctx["requested_urls"])


def test_all_remaining_macro_panels_return_normalized_payloads():
    ctx = make_context()
    for panel_id in (
        "shelter-rent-oer-pressure",
        "labor-wage-services-pressure",
        "growth-demand-recession-tracker",
        "fed-rates-polymarket-gap",
    ):
        payload = macro_cpi_panels_service.get_macro_cpi_panel_snapshot(ctx, panel_id, limit=5, allow_live_build=True)
        assert payload["items"]
        assert payload["summary"]["panelId"] == panel_id
        assert len(payload["items"]) <= 5


def test_macro_cpi_panel_api_is_seed_only_on_miss():
    payload = macro_cpi_panels_service.get_supply_tariff_import_watch_snapshot(make_context(), limit=8)

    assert payload["status"] == "warming"
    assert payload["cacheMode"] == "seed-miss"
    assert payload["items"] == []
