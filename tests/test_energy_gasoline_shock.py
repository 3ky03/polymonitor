from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import energy_gasoline_shock_service


class FakeLogger:
    def exception(self, *args, **kwargs):
        return None


def make_ctx():
    rows = {
        "wti": {"key": "wti", "label": "WTI crude", "unit": "$/bbl", "cadence": "daily", "date": "2026-05-10", "value": 64.2, "change1": -0.4, "changeWeek": -1.1},
        "gas": {"key": "gasoline", "label": "US gasoline", "unit": "$/gal", "cadence": "weekly", "date": "2026-05-06", "value": 3.61, "change1": 0.04, "changeWeek": 0.04},
        "diesel": {"key": "diesel", "label": "US diesel", "unit": "$/gal", "cadence": "weekly", "date": "2026-05-06", "value": 3.94, "change1": 0.02, "changeWeek": 0.02},
    }
    return {
        "SETTINGS": SimpleNamespace(
            energy_shock_wti_xls_url="wti",
            energy_shock_gasoline_xls_url="gas",
            energy_shock_diesel_xls_url="diesel",
            energy_shock_source_url="https://eia.example",
            energy_shock_ttl_seconds=21600,
        ),
        "app": SimpleNamespace(logger=FakeLogger()),
        "utc_now_iso": lambda: "2026-05-11T00:00:00Z",
        "xlrd": object(),
        "http_bytes_get": lambda *args, **kwargs: b"",
        "SNAPSHOT_STORE": None,
        "get_cached_json": lambda *args: None,
        "set_cached_json": lambda *args: None,
        "_rows": rows,
    }


def test_energy_payload_signal_from_items(monkeypatch):
    ctx = make_ctx()

    def fake_parse(ctx, *, url, key, label, unit, cadence):
        return ctx["_rows"][url]

    monkeypatch.setattr(energy_gasoline_shock_service, "_parse_eia_xls", fake_parse)
    payload = energy_gasoline_shock_service.get_energy_gasoline_shock_snapshot(ctx)

    assert payload["status"] == "ok"
    assert payload["sources"] == {"wti": "ok", "gasoline": "ok", "diesel": "ok"}
    assert payload["summary"]["signal"] in {"ENERGY NEUTRAL", "HEADLINE CPI HOTTER", "HEADLINE COOLING"}
    assert len(payload["items"]) == 3


def test_energy_payload_degrades_when_sources_fail():
    ctx = make_ctx()
    payload = energy_gasoline_shock_service.get_energy_gasoline_shock_snapshot(ctx)

    assert payload["status"] == "warming"
    assert payload["items"] == []
    assert payload["sources"]["wti"] == "error"
