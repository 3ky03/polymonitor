from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import macro_cpi_registry_service


def _ctx():
    return {"utc_now_iso": lambda: "2026-05-12T00:00:00Z"}


def test_cpi_release_command_center_composes_calendar_and_nowcast(monkeypatch):
    monkeypatch.setattr(
        macro_cpi_registry_service.cpi_release_calendar_service,
        "get_cpi_release_calendar_snapshot",
        lambda ctx, limit=20, **kwargs: {
            "status": "ok",
            "sources": {"bls": "ok"},
            "items": [
                {
                    "id": "cpi-next",
                    "kind": "CPI",
                    "title": "Consumer Price Index",
                    "releaseAt": "2026-05-12T12:30:00Z",
                    "releaseTimeEt": "08:30",
                    "referencePeriod": "April 2026",
                    "source": "BLS",
                    "marketRelevance": "inflation settlement trigger",
                    "hoursToEvent": 6,
                }
            ],
        },
    )
    monkeypatch.setattr(
        macro_cpi_registry_service,
        "_inflation_nowcast_seeded_snapshot",
        lambda ctx: {
            "status": "ok",
            "source": "Cleveland Fed",
            "monthOverMonth": {"CPI": 0.41, "Core CPI": 0.22},
            "yearOverYear": {"CPI": 3.4},
        },
    )

    payload = macro_cpi_registry_service.get_cpi_release_command_center_snapshot(_ctx(), limit=10)

    assert payload["status"] == "ok"
    assert payload["cacheMode"] == "composed-seed"
    assert payload["summary"]["rowCount"] == 4
    assert payload["summary"]["hotCount"] >= 2
    assert [item["group"] for item in payload["items"]][:2] == ["CPI", "NOWCAST"]


def test_components_registry_composes_energy_food_and_shelter(monkeypatch):
    monkeypatch.setattr(
        macro_cpi_registry_service.energy_gasoline_shock_service,
        "get_energy_gasoline_shock_snapshot",
        lambda ctx, limit=12, **kwargs: {
            "status": "ok",
            "sources": {"eia": "redis-seed"},
            "items": [{"key": "wti", "label": "WTI crude", "value": 109.76, "unit": "$", "changeWeek": 9.87, "source": "EIA"}],
        },
    )
    monkeypatch.setattr(
        macro_cpi_registry_service.food_retail_basket_service,
        "get_food_retail_basket_snapshot",
        lambda ctx, limit=12, **kwargs: {
            "status": "ok",
            "sources": {"fred": "redis-seed"},
            "items": [{"key": "food-home", "label": "Food at home", "value": 330.1, "momPct": -0.3, "source": "FRED / BLS"}],
        },
    )
    monkeypatch.setattr(
        macro_cpi_registry_service.macro_cpi_panels_service,
        "get_shelter_rent_oer_pressure_snapshot",
        lambda ctx, limit=12: {
            "status": "ok",
            "sources": {"fred": "redis-seed"},
            "items": [{"seriesId": "CUSR0000SEHA", "group": "rent", "label": "Rent of primary residence", "value": 442.7, "changePct": 0.19, "tone": "hot"}],
        },
    )

    payload = macro_cpi_registry_service.get_cpi_components_pressure_registry_snapshot(_ctx(), limit=10)

    assert payload["status"] == "ok"
    assert payload["summary"]["rowCount"] == 3
    assert {item["group"] for item in payload["items"]} == {"ENERGY", "FOOD", "RENT"}
    assert payload["summary"]["topMover"]["label"] == "WTI crude"


def test_registry_panels_return_capped_rows(monkeypatch):
    many_rows = [
        {"seriesId": f"S{i}", "group": "goods", "label": f"Series {i}", "value": i, "changePct": i / 10, "tone": "watch"}
        for i in range(70)
    ]
    monkeypatch.setattr(
        macro_cpi_registry_service.macro_cpi_panels_service,
        "get_supply_tariff_import_watch_snapshot",
        lambda ctx, limit=30: {"status": "ok", "sources": {"fred": "redis-seed"}, "items": many_rows},
    )

    payload = macro_cpi_registry_service.get_goods_tariff_supply_watch_snapshot(_ctx(), limit=999)

    assert payload["status"] == "ok"
    assert len(payload["items"]) == macro_cpi_registry_service.MAX_ITEM_LIMIT
    assert payload["summary"]["rowCount"] == macro_cpi_registry_service.MAX_ITEM_LIMIT


def test_registry_calendar_and_component_sources_are_seed_only(monkeypatch):
    def fail_live_calendar(ctx, limit=20, *, allow_live_build=True):
        assert allow_live_build is False
        return {"status": "warming", "cacheMode": "seed-miss", "sources": {}, "items": []}

    def fail_live_component(ctx, limit=12, *, allow_live_build=True):
        assert allow_live_build is False
        return {"status": "warming", "cacheMode": "seed-miss", "sources": {}, "items": []}

    monkeypatch.setattr(
        macro_cpi_registry_service.cpi_release_calendar_service,
        "get_cpi_release_calendar_snapshot",
        fail_live_calendar,
    )
    monkeypatch.setattr(
        macro_cpi_registry_service.energy_gasoline_shock_service,
        "get_energy_gasoline_shock_snapshot",
        fail_live_component,
    )
    monkeypatch.setattr(
        macro_cpi_registry_service.food_retail_basket_service,
        "get_food_retail_basket_snapshot",
        fail_live_component,
    )
    monkeypatch.setattr(
        macro_cpi_registry_service.runtime_service,
        "get_inflation_nowcast_snapshot",
        lambda ctx: {"status": "warming", "items": []},
    )
    monkeypatch.setattr(
        macro_cpi_registry_service.macro_cpi_panels_service,
        "get_shelter_rent_oer_pressure_snapshot",
        lambda ctx, limit=12: {"status": "warming", "cacheMode": "seed-miss", "sources": {}, "items": []},
    )

    release = macro_cpi_registry_service.get_cpi_release_command_center_snapshot(_ctx(), limit=10)
    components = macro_cpi_registry_service.get_cpi_components_pressure_registry_snapshot(_ctx(), limit=10)

    assert release["status"] == "warming"
    assert components["status"] == "warming"
