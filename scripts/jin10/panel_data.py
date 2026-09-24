#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch Jin10 flash items and write a panel-friendly JSON artifact."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any, Dict

try:
    from jin10.flash_client import fetch_jin10_panel_payload
except ImportError:
    from flash_client import fetch_jin10_panel_payload


DEFAULT_PANEL_PATH = (Path(__file__).resolve().parents[2] / "data" / "runtime" / "jin10" / "panel.json").resolve()


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    target = ensure_parent(path)
    tmp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(target)


def write_panel_payload(output_path: Path | str = DEFAULT_PANEL_PATH, *, limit: int = 24) -> Dict[str, Any]:
    target = Path(output_path).expanduser().resolve()
    payload = fetch_jin10_panel_payload(limit=limit)
    atomic_write_json(target, payload)
    payload["sourcePath"] = str(target)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Jin10 flash data for the dashboard panel")
    parser.add_argument("--output", default=str(DEFAULT_PANEL_PATH), help="Where to write the panel JSON artifact")
    parser.add_argument("--limit", type=int, default=24, help="Maximum number of panel cards to keep")
    args = parser.parse_args()
    payload = write_panel_payload(args.output, limit=max(4, args.limit))
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
