#!/usr/bin/env python3
"""Refresh natural-hazard provider snapshots outside browser API requests."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

_scripts_root = Path(__file__).resolve().parents[1]
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

import requests

from api.config import load_api_settings
from api.services import natural_hazards
from runtime.snapshot_store import SnapshotStore


DEFAULT_INTERVAL_SECONDS = 90


class _Logger:
    def info(self, message: str, *args: Any, **_kwargs: Any) -> None:
        print(f"[natural-hazards] {message % args if args else message}", file=sys.stderr)

    def warning(self, message: str, *args: Any, **_kwargs: Any) -> None:
        print(f"[natural-hazards] WARN {message % args if args else message}", file=sys.stderr)

    def exception(self, message: str, *args: Any, **_kwargs: Any) -> None:
        print(f"[natural-hazards] ERROR {message % args if args else message}", file=sys.stderr)


class _App:
    logger = _Logger()


class NaturalHazardsWatcher:
    def __init__(self, *, settings: Any, snapshot_sqlite_path: str, interval_seconds: int) -> None:
        self.settings = settings
        self.interval_seconds = max(60, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.session = requests.Session()
        # Production collectors must not inherit an operator's workstation or
        # shell proxy settings.
        self.session.trust_env = False

    def _http_json_get(
        self,
        url: str,
        *,
        params: Dict[str, Any] | None = None,
        timeout: int = 12,
        headers: Dict[str, str] | None = None,
    ) -> Any:
        response = self.session.get(url, params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.json()

    def _http_text_get(
        self,
        url: str,
        *,
        params: Dict[str, Any] | None = None,
        timeout: int = 12,
        headers: Dict[str, str] | None = None,
    ) -> str:
        response = self.session.get(url, params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.text

    def _http_bytes_get(
        self,
        url: str,
        *,
        params: Dict[str, Any] | None = None,
        timeout: int = 12,
        headers: Dict[str, str] | None = None,
    ) -> bytes:
        response = self.session.get(url, params=params, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.content

    def context(self) -> Dict[str, Any]:
        return {
            "SETTINGS": self.settings,
            "SNAPSHOT_STORE": self.snapshot_store,
            "app": _App(),
            "http_json_get": self._http_json_get,
            "http_text_get": self._http_text_get,
            "http_bytes_get": self._http_bytes_get,
        }

    def run_once(self) -> Dict[str, Any]:
        payload = natural_hazards.get_natural_hazards_snapshot(
            self.context(),
            limit=natural_hazards.DEFAULT_EVENT_LIMIT,
            allow_provider_fetch=True,
        )
        return {
            "status": "degraded" if payload.get("isPartial") else "ok",
            "eventCount": int((payload.get("counts") or {}).get("events") or 0),
            "sources": {
                str(source.get("key") or "unknown"): str(source.get("status") or "error")
                for source in payload.get("sources") or []
                if isinstance(source, dict)
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get(
            "POLYDATA_NATURAL_HAZARDS_WATCH_INTERVAL_SECONDS",
            DEFAULT_INTERVAL_SECONDS,
        )),
    )
    args = parser.parse_args()
    settings = load_api_settings()
    watcher = NaturalHazardsWatcher(
        settings=settings,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        interval_seconds=args.interval,
    )
    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[natural-hazards] ERROR {exc}", file=sys.stderr)
        if not args.watch:
            return 0
        time.sleep(watcher.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
