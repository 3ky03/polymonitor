#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_scripts_root = Path(__file__).resolve().parents[1]
_project_root = _scripts_root.parent
for _path in (str(_project_root), str(_scripts_root)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    import websockets
except ImportError:  # pragma: no cover - dependency guard.
    websockets = None

from api.services import lob_service
from data_sources import POLYMARKET_CLOB_WS_URL
from runtime.local_orderbook_websocket_watcher import CoverageTarget, _event_sample, _iter_json_events


def _collect_coverage_tokens(ctx: dict[str, Any], *, limit: int, topics: str) -> list[str]:
    payload = lob_service.get_lob_coverage_targets_payload(ctx, limit=limit, topics=topics)
    if payload.get("_status") or payload.get("error"):
        raise RuntimeError(str(payload.get("detail") or payload.get("error") or "coverage target selection failed"))
    tokens: list[str] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        target = CoverageTarget.from_payload(item)
        if target is None:
            continue
        tokens.extend(token for token in target.token_ids if token)
    return tokens


async def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    if websockets is None:
        raise RuntimeError("websockets package is required. Install scripts/requirements.txt")
    token_ids = [token.strip() for token in (args.token_id or []) if token.strip()]
    if not token_ids and not args.all_markets:
        from api_server import build_service_context

        token_ids = _collect_coverage_tokens(build_service_context(), limit=args.limit, topics=args.topics)
    token_ids = token_ids[: max(0, int(args.max_tokens))]
    if not token_ids and not args.all_markets:
        raise RuntimeError("No token IDs selected for probe")

    counts: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    raw_message_count = 0
    pong_count = 0
    started_at = time.time()
    last_ping_at = 0.0
    deadline = time.monotonic() + max(1, int(args.seconds))
    subscribe_payload = {
        "type": "market",
        "assets_ids": [] if args.all_markets else token_ids,
        "custom_feature_enabled": True,
    }
    async with websockets.connect(args.ws_url, ping_interval=None, close_timeout=10, max_queue=1000) as websocket:
        await websocket.send(json.dumps(subscribe_payload, ensure_ascii=True))
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_ping_at >= max(1, int(args.ping_seconds)):
                await websocket.send("PING")
                last_ping_at = now
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            if raw == "PONG":
                pong_count += 1
                continue
            if raw in {"PING", ""}:
                continue
            raw_message_count += 1
            try:
                payload = json.loads(str(raw))
            except json.JSONDecodeError:
                counts["invalid_json"] = counts.get("invalid_json", 0) + 1
                continue
            for event in _iter_json_events(payload):
                event_type = str(event.get("event_type") or event.get("type") or "unknown").strip() or "unknown"
                counts[event_type] = counts.get(event_type, 0) + 1
                if len(samples) < max(0, int(args.samples)):
                    samples.append(_event_sample(event, event_type=event_type))
    return {
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        "seconds": int(time.time() - started_at),
        "wsUrl": args.ws_url,
        "allMarkets": bool(args.all_markets),
        "subscribedTokenCount": 0 if args.all_markets else len(token_ids),
        "rawMessageCount": raw_message_count,
        "pongCount": pong_count,
        "eventTypeCounts": dict(sorted(counts.items())),
        "samples": samples,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Polymarket CLOB websocket event types without writing to DB")
    parser.add_argument("--ws-url", default=os.environ.get("POLYDATA_CLOB_WS_URL") or POLYMARKET_CLOB_WS_URL)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--limit", type=int, default=40, help="Coverage market limit when token IDs are not provided")
    parser.add_argument("--topics", default=os.environ.get("POLYDATA_LOB_COVERAGE_TOPICS", "worldcup,crypto,politics"))
    parser.add_argument("--max-tokens", type=int, default=120)
    parser.add_argument("--token-id", action="append", default=[])
    parser.add_argument("--all-markets", action="store_true", help="Subscribe with empty assets_ids to probe all-market stream briefly")
    parser.add_argument("--ping-seconds", type=int, default=10)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    report = asyncio.run(run_probe(args))
    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        print("LOB websocket probe")
        print(f"seconds={report['seconds']} all_markets={report['allMarkets']} subscribed_tokens={report['subscribedTokenCount']}")
        print(f"raw_messages={report['rawMessageCount']} pongs={report['pongCount']}")
        print("event_type_counts=" + json.dumps(report["eventTypeCounts"], ensure_ascii=True, sort_keys=True))
        print("samples=" + json.dumps(report["samples"], ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
