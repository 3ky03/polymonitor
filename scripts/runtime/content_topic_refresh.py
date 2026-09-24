#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh high-level content topic pools into PostgreSQL/SQLite.

This worker is intentionally topic-first: it refreshes broad intelligence
streams such as NBA, CPI, oil, crypto, and elections, then stores those items in
content_items. Market-level panels can link against this pool without issuing a
fresh external search per market.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from data_sources import CONTENT_TOPIC_REGISTRY  # noqa: E402


def _topic_ids_from_args(value: str) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _selected_topic_ids(value: str) -> List[str]:
    requested = _topic_ids_from_args(value)
    if requested:
        return requested
    return [str(topic.get("id") or "").strip() for topic in CONTENT_TOPIC_REGISTRY if str(topic.get("id") or "").strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh high-level content topic pools")
    parser.add_argument(
        "--topics",
        default="",
        help="Comma-separated topic IDs. Defaults to every topic in CONTENT_TOPIC_REGISTRY.",
    )
    parser.add_argument("--limit-per-topic", type=int, default=24, help="Maximum items to keep per topic.")
    parser.add_argument("--list-topics", action="store_true", help="Print available topic IDs and exit.")
    parser.add_argument("--watch", action="store_true", help="Refresh repeatedly instead of exiting after one run.")
    parser.add_argument("--interval", type=int, default=900, help="Seconds between refreshes in watch mode.")
    args = parser.parse_args()

    if args.list_topics:
        for topic in CONTENT_TOPIC_REGISTRY:
            print(f"{topic.get('id')}\t{topic.get('label')}")
        return 0

    topic_ids = _selected_topic_ids(args.topics)
    from api.services import query_service  # noqa: E402
    from api_server import build_service_context  # noqa: E402

    while True:
        ctx = build_service_context()
        started_at = time.time()
        stored_by_topic = {}
        item_count = 0
        for topic_id in topic_ids:
            topic_started_at = time.time()
            try:
                payload = query_service.refresh_topic_content(
                    ctx,
                    topic_ids=[topic_id],
                    limit_per_topic=max(1, int(args.limit_per_topic or 24)),
                )
            except Exception as exc:
                payload = {
                    "sourceMode": "topic-registry",
                    "topicCount": 1,
                    "itemCount": 0,
                    "storedCount": 0,
                    "topics": {topic_id: 0},
                    "error": str(exc),
                }
            stored = int((payload.get("topics") or {}).get(topic_id) or 0)
            items = int(payload.get("itemCount") or 0)
            stored_by_topic[topic_id] = stored
            item_count += items
            print(
                json.dumps(
                    {
                        "event": "topic-refreshed",
                        "topic": topic_id,
                        "itemCount": items,
                        "storedCount": stored,
                        "durationMs": round((time.time() - topic_started_at) * 1000, 2),
                        "sourceMode": payload.get("sourceMode") or "topic-registry",
                        **({"error": payload["error"]} if payload.get("error") else {}),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
        print(
            json.dumps(
                {
                    "event": "topic-refresh-cycle",
                    "sourceMode": "topic-registry",
                    "topicCount": len(stored_by_topic),
                    "itemCount": item_count,
                    "storedCount": sum(stored_by_topic.values()),
                    "durationMs": round((time.time() - started_at) * 1000, 2),
                    "topics": stored_by_topic,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        if not args.watch:
            break
        time.sleep(max(60, int(args.interval or 900)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
