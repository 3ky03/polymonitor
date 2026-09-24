#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional CLI helper to write the current F1 runtime payload to disk."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

from f1.runtime_feed import DEFAULT_PANEL_PATH, sync_f1_panel


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch BWENews runtime data and write a panel JSON artifact.")
    parser.add_argument("--output", default=str(DEFAULT_PANEL_PATH), help="Output JSON path for the panel payload.")
    parser.add_argument("--year", type=int, default=None, help="Season year to fetch. Defaults to current UTC year.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of cards to persist.")
    parser.add_argument("--watch", action="store_true", help="Continuously refresh the panel payload.")
    parser.add_argument("--interval", type=float, default=180.0, help="Refresh interval in seconds when --watch is enabled.")
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve()

    def _run_once() -> None:
        payload = sync_f1_panel(output_path, requests_lib=requests, year=args.year, limit=args.limit)
        summary = {
            "generatedAt": payload.get("generatedAt"),
            "season": payload.get("season"),
            "cards": len(payload.get("cards") or []),
            "focusMeeting": ((payload.get("focusMeeting") or {}).get("meetingName")),
            "output": str(output_path),
        }
        print(json.dumps(summary, ensure_ascii=False))

    if not args.watch:
        _run_once()
        return 0

    interval_seconds = max(15.0, float(args.interval or 180.0))
    while True:
        try:
            _run_once()
        except Exception as exc:  # pragma: no cover - operational guard
            print(json.dumps({"error": str(exc), "output": str(output_path)}, ensure_ascii=False))
        time.sleep(interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
