#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _json_get(base_url: str, path: str, timeout_seconds: float) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Accept": "application/json", "User-Agent": "polydata-focus-prewarm/1.0"},
    )
    with opener.open(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected payload for {path}")
    return payload


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 1)


def _read_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps({"updatedAt": int(time.time()), "items": items}, ensure_ascii=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _catalog_items(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    return [
        {
            "id": int(item["id"]),
            "yesTokenId": str(item.get("yesTokenId") or ""),
            "noTokenId": str(item.get("noTokenId") or ""),
            "title": str(item.get("title") or "")[:240],
        }
        for item in (payload.get("items") or [])[:limit]
        if isinstance(item, dict) and item.get("id") is not None
    ]


def prewarm(
    base_url: str,
    *,
    limit: int,
    workers: int,
    timeout_seconds: float,
    lob_limit: int,
    state_path: Path,
    catalog_refresh_seconds: int,
) -> dict[str, Any]:
    state = _read_state(state_path)
    state_items = [item for item in (state.get("items") or []) if isinstance(item, dict) and item.get("id") is not None]
    state_age = max(0, int(time.time()) - int(state.get("updatedAt") or 0))
    catalog_started = time.perf_counter()
    catalog_mode = "state-hit"
    items = state_items[:limit] if state_items and state_age < catalog_refresh_seconds else []
    if not items:
        query = urllib.parse.urlencode({"page": 1, "pageSize": limit, "status": "active"})
        try:
            catalog = _json_get(base_url, f"/markets?{query}", timeout_seconds)
            items = _catalog_items(catalog, limit)
            _write_state(state_path, items)
            state_age = 0
            catalog_mode = "refreshed"
        except Exception:
            if not state_items:
                raise
            items = state_items[:limit]
            catalog_mode = "stale-fallback"
    catalog_latency_ms = round((time.perf_counter() - catalog_started) * 1000, 1)

    def warm_one(index_and_item: tuple[int, dict[str, Any]]) -> dict[str, Any]:
        index, item = index_and_item
        market_id = int(item["id"])
        started = time.perf_counter()
        try:
            payload = _json_get(base_url, f"/markets/{market_id}/focus-tile", timeout_seconds)
            identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
            yes_token_id = str(identity.get("yesTokenId") or item.get("yesTokenId") or "").strip()
            no_token_id = str(identity.get("noTokenId") or item.get("noTokenId") or "").strip()
            lob_status = "not-requested"
            if index < lob_limit and yes_token_id:
                lob_query = urllib.parse.urlencode({
                    "noTokenId": no_token_id,
                    "marketId": market_id,
                })
                lob_payload = _json_get(
                    base_url,
                    f"/runtime/lob/token/{urllib.parse.quote(yes_token_id, safe='')}?{lob_query}",
                    timeout_seconds,
                )
                lob_status = str(lob_payload.get("bookStatus") or "unknown")
            return {
                "marketId": market_id,
                "status": str(payload.get("focusStatus") or "unknown"),
                "lobStatus": lob_status,
                "latencyMs": round((time.perf_counter() - started) * 1000, 1),
                "error": None,
            }
        except Exception as exc:
            return {
                "marketId": market_id,
                "status": "error",
                "lobStatus": "error" if index < lob_limit else "not-requested",
                "latencyMs": round((time.perf_counter() - started) * 1000, 1),
                "error": str(exc)[:180],
            }

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        for result in executor.map(warm_one, enumerate(items)):
            results.append(result)
    latencies = [float(item["latencyMs"]) for item in results if item.get("status") != "error"]
    counts: dict[str, int] = {}
    for item in results:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    lob_counts: dict[str, int] = {}
    for item in results[:lob_limit]:
        status = str(item.get("lobStatus") or "unknown")
        lob_counts[status] = lob_counts.get(status, 0) + 1
    return {
        "status": "ok" if results and counts.get("error", 0) < len(results) else "error",
        "source": "local-api-focus-tile-prewarm",
        "catalogMode": catalog_mode,
        "catalogAgeSeconds": state_age,
        "catalogLatencyMs": catalog_latency_ms,
        "requested": len(items),
        "counts": counts,
        "lobRequested": min(lob_limit, len(items)),
        "lobCounts": lob_counts,
        "latencyMs": {
            "median": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
            "max": round(max(latencies), 1) if latencies else None,
        },
        "errors": [item for item in results if item.get("error")][:5],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prewarm selection-critical market focus tiles through the local API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:18500")
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--lob-limit", type=int, default=80)
    parser.add_argument("--state-path", type=Path, default=Path("/tmp/polydata/market-focus-prewarm-state.json"))
    parser.add_argument("--catalog-refresh-seconds", type=int, default=300)
    args = parser.parse_args()
    try:
        payload = prewarm(
            args.base_url,
            limit=max(1, min(int(args.limit), 80)),
            workers=max(1, min(int(args.workers), 12)),
            timeout_seconds=max(0.5, min(float(args.timeout_seconds), 15.0)),
            lob_limit=max(0, min(int(args.lob_limit), 80)),
            state_path=args.state_path,
            catalog_refresh_seconds=max(60, min(int(args.catalog_refresh_seconds), 1800)),
        )
    except Exception as exc:
        payload = {"status": "error", "source": "local-api-focus-tile-prewarm", "error": str(exc)[:240]}
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
