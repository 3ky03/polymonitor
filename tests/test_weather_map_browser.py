from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "webpage"


def _wait_for(url: str, *, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise AssertionError(f"server did not become ready: {last_error}")


@pytest.mark.skipif(os.environ.get("POLYDATA_RUN_BROWSER_TESTS") != "1", reason="set POLYDATA_RUN_BROWSER_TESTS=1 to run browser screenshot checks")
def test_weather_deck_map_browser_screenshot_is_not_blank(tmp_path):
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome:
        pytest.skip("Chrome/Chromium is not installed")

    harness = WEB_ROOT / ".tmp-weather-map-browser-test.html"
    screenshot = tmp_path / "weather-map.png"
    profile = tmp_path / "chrome-profile"
    payload = [
        {"cityId": "new-york", "city": "New York", "lat": 40.7128, "lon": -74.006, "unit": "F", "currentTemp": 69.4, "forecastHigh": 88.7, "condition": "Partly cloudy", "topBin": {"label": "88F or higher", "midPriceYes": 0.42}, "quoteCoverage": "4/7", "eventSlug": "new-york-temperature"},
        {"cityId": "chicago", "city": "Chicago", "lat": 41.8781, "lon": -87.6298, "unit": "F", "currentTemp": 67.8, "forecastHigh": 87.1, "condition": "Clear", "quoteCoverage": "0/0"},
        {"cityId": "london", "city": "London", "lat": 51.5072, "lon": -0.1276, "unit": "C", "currentTemp": 16.0, "forecastHigh": 21.0, "condition": "Cloudy", "topBin": {"label": "21C", "midPriceYes": 0.35}, "quoteCoverage": "3/6", "eventSlug": "london-temperature"},
        {"cityId": "beijing", "city": "Beijing", "lat": 39.9042, "lon": 116.4074, "unit": "C", "currentTemp": 29.0, "forecastHigh": 34.0, "condition": "Fair", "quoteCoverage": "0/0"},
        {"cityId": "sydney", "city": "Sydney", "lat": -33.8688, "lon": 151.2093, "unit": "C", "currentTemp": 18.0, "forecastHigh": 24.0, "condition": "Fair", "quoteCoverage": "0/0"},
    ]
    harness.write_text(
        f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="stylesheet" href="/node_modules/maplibre-gl/dist/maplibre-gl.css" />
    <link rel="stylesheet" href="/src/styles/base-layer.css" />
    <style>body {{ margin: 0; background: #030405; }} #app {{ padding: 20px; height: 920px; box-sizing: border-box; }}</style>
  </head>
  <body>
    <div id="app"></div>
    <script type="module">
      import {{ h, render }} from '/node_modules/.vite/deps/preact.js';
      import WeatherDeckMap from '/src/components/WeatherDeckMap.tsx';
      render(h(WeatherDeckMap, {{ items: {payload!r}, selectedCityId: 'new-york', height: 860 }}), document.getElementById('app'));
    </script>
  </body>
</html>
""",
        encoding="utf-8",
    )

    server = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "3199"],
        cwd=WEB_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for("http://127.0.0.1:3199/.tmp-weather-map-browser-test.html")
        result = subprocess.run(
            [
                chrome,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--enable-unsafe-swiftshader",
                f"--user-data-dir={profile}",
                "--window-size=1600,960",
                "--virtual-time-budget=12000",
                f"--screenshot={screenshot}",
                "http://127.0.0.1:3199/.tmp-weather-map-browser-test.html",
            ],
            cwd=WEB_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stdout
        assert screenshot.exists(), result.stdout
        assert screenshot.stat().st_size > 25_000, "weather map screenshot is too small; likely blank/black"
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        harness.unlink(missing_ok=True)
