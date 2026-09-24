from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import f1_runtime_service
from runtime.snapshot_store import SnapshotStore


class FakeLogger:
    def exception(self, *args, **kwargs) -> None:
        return None


class FakeApp:
    logger = FakeLogger()


class FakeResponse:
    def __init__(self, *, status_code: int = 200, json_payload=None, text: str = "", headers=None):
        self.status_code = status_code
        self._json_payload = json_payload
        self.text = text
        self.headers = headers or {}
        self.content = text.encode("utf-8") if text else b"json"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._json_payload


class FakeRequests:
    def __init__(self) -> None:
        self.calls = []

    def get(self, url, params=None, timeout=0, headers=None):
        self.calls.append((url, params))
        if "fixture-f1-rss" in url:
            return FakeResponse(
                text="""
                <rss><channel>
                  <item>
                    <title>Binance EN: Binance Futures Will Launch USDⓈ-Margined OPGUSDT Perpetual Contract&lt;br/&gt;Binance 合约将上线 OPGUSDT 永续合约&lt;br/&gt;&lt;br/&gt;————————————&lt;br/&gt;2026-04-22 14:00:00&lt;br/&gt;source: fixture-binance-announcement</title>
                    <link>fixture-bwenews-item-99999</link>
                    <pubDate>Wed, 22 Apr 2026 08:00:00 GMT</pubDate>
                  </item>
                </channel></rss>
                """
            )
        return FakeResponse(json_payload=[])


class F1RuntimeServiceTestCase(unittest.TestCase):
    def make_context(self) -> dict:
        snapshot_dir = tempfile.TemporaryDirectory()
        self.addCleanup(snapshot_dir.cleanup)
        snapshot_store = SnapshotStore(str(Path(snapshot_dir.name) / "f1.sqlite3"))
        return {
            "SPORTS_RUNTIME_TTL_SECONDS": 60,
            "SETTINGS": SimpleNamespace(
                f1_bwenews_rss_url="fixture-f1-rss",
                f1_bwenews_source_url="fixture-bwenews-source",
            ),
            "utc_now_iso": lambda: "2026-04-23T00:00:00Z",
            "app": FakeApp(),
            "requests": FakeRequests(),
            "SNAPSHOT_STORE": snapshot_store,
            "get_cached_json": lambda namespace, cache_key: None,
            "set_cached_json": lambda namespace, cache_key, payload, ttl_seconds: None,
        }

    def test_get_f1_panel_snapshot_builds_live_payload_with_news(self):
        payload = f1_runtime_service.get_f1_panel_snapshot(self.make_context(), limit=8)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["source"], "bwenews-rss")
        self.assertEqual(payload["sourceUrl"], "fixture-bwenews-source")
        self.assertIsNone(payload["focusMeeting"])
        self.assertTrue(any(item.get("kind") == "news" for item in payload["cards"]))
        self.assertEqual(payload["cards"][0]["title"], "Binance EN: Binance Futures Will Launch USDⓈ-Margined OPGUSDT Perpetual Contract")
        self.assertEqual(payload["cards"][0]["url"], "fixture-bwenews-item-99999")
        self.assertIn("Binance 合约将上线 OPGUSDT 永续合约", payload["cards"][0]["summary"])

    def test_get_f1_panel_snapshot_returns_empty_when_requests_unavailable(self):
        ctx = self.make_context()
        ctx["requests"] = None

        payload = f1_runtime_service.get_f1_panel_snapshot(ctx, limit=8)

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["cards"], [])


if __name__ == "__main__":
    unittest.main()
