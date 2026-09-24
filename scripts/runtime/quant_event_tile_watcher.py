#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed quant event chart tiles into Redis and SQLite snapshot storage."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_scripts_root = Path(__file__).resolve().parents[1]
_repo_root = _scripts_root.parent
for candidate in (_scripts_root, _repo_root):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:
    import redis
except ImportError:
    redis = None

from api.config import load_api_settings
from api.routes.quant import _apply_point_payload_format, _camel_row
from runtime.seed_meta import SeedMetaStore, build_seed_meta_payload, utc_now_iso
from runtime.snapshot_store import SnapshotStore

from quant.api.read_api import get_event_price_tile
from quant.core.db import PostgresSettings, postgres_connection


DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_MAX_EVENTS = 80
DEFAULT_TTL_SECONDS = 90
DEFAULT_SOURCE = "orderfilled_block_close"
DEFAULT_LIMIT = 2500
DEFAULT_MAX_OUTCOMES = 100
DEFAULT_TOP_N = 12
DEFAULT_MAX_POINTS = 600
DEFAULT_RANGE = "latest"
DEFAULT_RESOLUTION = "auto"
DEFAULT_POINT_FORMAT = "lite"
NAMESPACE = "quant-event-tile"
SEED_META_NAMESPACE = "seed-meta:quant"
SEED_META_CACHE_KEY = "event-price-tile"
SEED_META_SERVICE_NAME = "polydata-quant-event-tile-seed.service"
WARM_REQUESTS_KEY = "quant-event-tile-warm:events"


def _redis_key(prefix: str, namespace: str, cache_key: str) -> str:
    return f"{str(prefix or '')}{namespace}:{cache_key}"


def _cache_key(name: str, args: dict[str, str], *, version: int = 1) -> str:
    route_args = {key: [str(args[key])] for key in sorted(args.keys())}
    return json.dumps({"name": name, "v": version, "args": route_args}, sort_keys=True, ensure_ascii=True)


def _canonical_tile_range(value: str | None) -> str:
    normalized = str(value or DEFAULT_RANGE).strip().lower()
    return "full" if normalized in {"all", "full"} else "latest"


class QuantEventTileWatcher:
    def __init__(
        self,
        *,
        redis_url: str,
        redis_prefix: str,
        snapshot_sqlite_path: str,
        interval_seconds: int,
        ttl_seconds: int,
        max_events: int,
        price_source: str,
        limit: int,
        max_outcomes: int,
        top_n: int,
        max_points: int,
        tile_range: str,
        resolution: str,
        point_format: str,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required. Install scripts/requirements.txt")
        if not str(redis_url or "").strip():
            raise RuntimeError("POLYDATA_REDIS_URL is required for quant event tile watcher")
        self.redis_prefix = str(redis_prefix or "")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.snapshot_store = SnapshotStore(snapshot_sqlite_path)
        self.seed_meta_store = SeedMetaStore(
            redis_client=self.redis_client,
            redis_prefix=self.redis_prefix,
            snapshot_store=self.snapshot_store,
        )
        self.interval_seconds = max(10, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.ttl_seconds = max(30, int(ttl_seconds or DEFAULT_TTL_SECONDS))
        self.max_events = max(1, int(max_events or DEFAULT_MAX_EVENTS))
        self.price_source = str(price_source or DEFAULT_SOURCE).strip() or DEFAULT_SOURCE
        self.limit = max(250, int(limit or DEFAULT_LIMIT))
        self.max_outcomes = max(1, int(max_outcomes or DEFAULT_MAX_OUTCOMES))
        self.top_n = max(1, int(top_n or DEFAULT_TOP_N))
        self.max_points = max(50, int(max_points or DEFAULT_MAX_POINTS))
        self.tile_range = _canonical_tile_range(tile_range)
        self.resolution = str(resolution or DEFAULT_RESOLUTION).strip() or DEFAULT_RESOLUTION
        self.point_format = str(point_format or DEFAULT_POINT_FORMAT).strip().lower() or DEFAULT_POINT_FORMAT

    def query_args(self, event_slug: str) -> dict[str, str]:
        return {
            "event_slug": event_slug,
            "price_source": self.price_source,
            "limit": str(self.limit),
            "max_outcomes": str(self.max_outcomes),
            "top_n": str(self.top_n),
            "max_points": str(self.max_points),
            "range": self.tile_range,
            "resolution": self.resolution,
            "point_format": self.point_format,
        }

    def normalize_job_args(self, job: dict[str, Any]) -> dict[str, str]:
        event_slug = str(job.get("event_slug") or "").strip()
        return {
            "event_slug": event_slug,
            "price_source": str(job.get("price_source") or self.price_source).strip() or self.price_source,
            "limit": str(max(250, int(job.get("limit") or self.limit))),
            "max_outcomes": str(max(1, int(job.get("max_outcomes") or self.max_outcomes))),
            "top_n": str(max(1, int(job.get("top_n") or self.top_n))),
            "max_points": str(max(50, int(job.get("max_points") or self.max_points))),
            "range": _canonical_tile_range(str(job.get("range") or self.tile_range)),
            "resolution": str(job.get("resolution") or self.resolution).strip() or self.resolution,
            "point_format": str(job.get("point_format") or self.point_format).strip().lower() or self.point_format,
        }

    def cache_key(self, event_slug: str) -> str:
        return _cache_key("event-price-tile", self.query_args(event_slug), version=4)

    def cache_key_for_args(self, args: dict[str, str]) -> str:
        return _cache_key("event-price-tile", args, version=4)

    def redis_key(self, event_slug: str) -> str:
        return _redis_key(self.redis_prefix, NAMESPACE, self.cache_key(event_slug))

    def discover_events(self) -> list[dict[str, Any]]:
        rows_source = "block_rows" if self.price_source == "orderfilled_block_close" else "frontend_rows"
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH event_rollup AS (
                        SELECT
                            e.event_slug,
                            max(e.event_title) AS event_title,
                            max(e.status) AS status,
                            max(e.end_date) AS end_date,
                            count(*) AS members,
                            sum(COALESCE(m.block_rows, 0)) AS block_rows,
                            sum(COALESCE(m.frontend_rows, 0)) AS frontend_rows,
                            bool_or(COALESCE(m.active, FALSE)) AS active,
                            max(COALESCE(m.latest_block, 0)) AS latest_block,
                            max(m.latest_timestamp) AS latest_timestamp,
                            max(m.updated_at) AS updated_at
                        FROM quant.market_event_metadata e
                        JOIN quant.market_event_members m ON m.event_slug = e.event_slug
                        GROUP BY e.event_slug
                    )
                    SELECT *
                    FROM event_rollup
                    WHERE COALESCE({rows_source}, 0) > 0
                      AND (
                        active
                        OR end_date >= now() - interval '90 days'
                        OR updated_at >= now() - interval '14 days'
                      )
                    ORDER BY
                        active DESC,
                        latest_block DESC,
                        latest_timestamp DESC NULLS LAST,
                        COALESCE({rows_source}, 0) DESC,
                        event_slug ASC
                    LIMIT %s
                    """,
                    (self.max_events,),
                )
                return [dict(row) for row in cur.fetchall()]

    def build_payload(self, event_slug: str) -> dict[str, Any]:
        return self.build_payload_for_args(self.query_args(event_slug))

    def build_payload_for_args(self, args: dict[str, str]) -> dict[str, Any]:
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            payload = get_event_price_tile(
                conn,
                event_slug=args["event_slug"],
                price_source=args["price_source"],
                limit=int(args["limit"]),
                max_outcomes=int(args["max_outcomes"]),
                top_n=int(args["top_n"]),
                max_points=int(args["max_points"]),
                tile_range=args["range"],
                resolution=args["resolution"],
            )
        payload = _camel_row(_apply_point_payload_format(payload, args["point_format"]))
        payload["cacheMode"] = "seeded"
        payload["seededAt"] = utc_now_iso()
        payload["status"] = "ok"
        payload["cacheHit"] = True
        payload["cacheStatus"] = "hot"
        return payload

    def store_payload(self, event_slug: str, payload: dict[str, Any]) -> None:
        self.store_payload_for_args(self.query_args(event_slug), payload)

    def store_payload_for_args(self, args: dict[str, str], payload: dict[str, Any]) -> None:
        event_slug = args["event_slug"]
        cache_key = self.cache_key_for_args(args)
        serialized = json.dumps(payload, ensure_ascii=True, default=str)
        self.snapshot_store.set(NAMESPACE, cache_key, payload, self.ttl_seconds)
        self.redis_client.set(_redis_key(self.redis_prefix, NAMESPACE, cache_key), serialized, ex=self.ttl_seconds)
        points = [
            point
            for outcome in payload.get("outcomes") or []
            for series_name in ("points", "complementPoints")
            for point in outcome.get(series_name) or []
            if isinstance(point, (dict, list, tuple))
        ]

        def point_x(point: Any) -> int:
            if isinstance(point, dict):
                value = point.get("x") or point.get("blockNumber") or point.get("timestamp") or 0
            elif isinstance(point, (list, tuple)) and point:
                value = point[0]
            else:
                value = 0
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        x_values = [point_x(point) for point in points]
        with postgres_connection(PostgresSettings(), readonly=False) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS quant.quant_price_series_tiles (
                        tile_key TEXT PRIMARY KEY,
                        scope TEXT NOT NULL,
                        entity_slug TEXT NOT NULL,
                        price_source TEXT NOT NULL,
                        range_name TEXT NOT NULL,
                        resolution TEXT NOT NULL,
                        top_n INTEGER NOT NULL,
                        max_points INTEGER NOT NULL,
                        payload JSONB NOT NULL,
                        payload_bytes BIGINT NOT NULL DEFAULT 0,
                        row_count BIGINT NOT NULL DEFAULT 0,
                        data_min_x BIGINT,
                        data_max_x BIGINT,
                        expires_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    INSERT INTO quant.quant_price_series_tiles (
                        tile_key, scope, entity_slug, price_source, range_name, resolution,
                        top_n, max_points, payload, payload_bytes, row_count,
                        data_min_x, data_max_x, expires_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
                        now() + (%s::text || ' seconds')::interval
                    )
                    ON CONFLICT (tile_key) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        payload_bytes = EXCLUDED.payload_bytes,
                        row_count = EXCLUDED.row_count,
                        data_min_x = EXCLUDED.data_min_x,
                        data_max_x = EXCLUDED.data_max_x,
                        expires_at = EXCLUDED.expires_at,
                        updated_at = now()
                    """,
                    (
                        cache_key,
                        "event",
                        event_slug,
                        args["price_source"],
                        args["range"],
                        args["resolution"],
                        int(args["top_n"]),
                        int(args["max_points"]),
                        serialized,
                        len(serialized),
                        len(points),
                        min(x_values) if x_values else None,
                        max(x_values) if x_values else None,
                        self.ttl_seconds,
                    ),
                )

    def claim_warm_jobs(self) -> list[dict[str, str]]:
        key = f"{self.redis_prefix}{WARM_REQUESTS_KEY}"
        try:
            raw_jobs = list(self.redis_client.smembers(key) or [])
        except Exception as exc:
            print(f"[quant-event-tile] warm job read failed: {exc}", file=sys.stderr)
            return []
        jobs: list[dict[str, str]] = []
        for raw in raw_jobs[: max(1, self.max_events)]:
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    continue
                args = self.normalize_job_args(parsed)
                if args["event_slug"]:
                    jobs.append(args)
            except Exception:
                continue
            finally:
                try:
                    self.redis_client.srem(key, raw)
                except Exception:
                    pass
        deduped: dict[str, dict[str, str]] = {}
        for args in jobs:
            deduped[self.cache_key_for_args(args)] = args
        return list(deduped.values())

    def store_seed_meta(self, *, status: str, record_count: int, error_summary: str | None, metadata: dict[str, Any]) -> None:
        attempted_at = utc_now_iso()
        previous = self.seed_meta_store.load(SEED_META_NAMESPACE, SEED_META_CACHE_KEY) or {}
        last_success_at = previous.get("lastSuccessAt")
        if status in {"ok", "degraded"}:
            last_success_at = attempted_at
        payload = build_seed_meta_payload(
            panel_id="quant-event-price-tile",
            namespace=SEED_META_NAMESPACE,
            cache_key=SEED_META_CACHE_KEY,
            service_name=SEED_META_SERVICE_NAME,
            expected_interval_seconds=self.interval_seconds,
            status=status,
            last_attempt_at=attempted_at,
            last_success_at=last_success_at or attempted_at,
            record_count=record_count,
            source_states={"quantEventTiles": status},
            error_summary=error_summary,
            cache_mode="seeded",
            payload_status=status,
            metadata=metadata,
        )
        self.seed_meta_store.store(SEED_META_NAMESPACE, SEED_META_CACHE_KEY, payload)

    def run_once(self) -> dict[str, Any]:
        started_at = time.perf_counter()
        warm_jobs = self.claim_warm_jobs()
        events = self.discover_events()
        seeded = 0
        warmed = 0
        errors: list[str] = []
        total_bytes = 0
        for args in warm_jobs:
            event_slug = args["event_slug"]
            try:
                payload = self.build_payload_for_args(args)
                self.store_payload_for_args(args, payload)
                warmed += 1
                seeded += 1
                total_bytes += len(json.dumps(payload, ensure_ascii=True, default=str))
            except Exception as exc:
                errors.append(f"{event_slug}: {exc}")
        for event in events:
            event_slug = str(event.get("event_slug") or "").strip()
            if not event_slug:
                continue
            if any(job["event_slug"] == event_slug for job in warm_jobs):
                continue
            try:
                payload = self.build_payload(event_slug)
                self.store_payload(event_slug, payload)
                seeded += 1
                total_bytes += len(json.dumps(payload, ensure_ascii=True, default=str))
            except Exception as exc:
                errors.append(f"{event_slug}: {exc}")
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        status = "ok" if seeded and not errors else "degraded" if seeded else "error"
        self.store_seed_meta(
            status=status,
            record_count=seeded,
            error_summary="; ".join(errors[:5]) if errors else None,
            metadata={
                "candidateEvents": len(events),
                "warmJobs": len(warm_jobs),
                "warmedEvents": warmed,
                "seededEvents": seeded,
                "errors": len(errors),
                "durationMs": duration_ms,
                "payloadBytes": total_bytes,
                "priceSource": self.price_source,
                "topN": self.top_n,
                "maxPoints": self.max_points,
            },
        )
        return {
            "status": status,
            "candidateEvents": len(events),
            "warmJobs": len(warm_jobs),
            "warmedEvents": warmed,
            "seededEvents": seeded,
            "errors": len(errors),
            "durationMs": duration_ms,
            "payloadBytes": total_bytes,
        }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed quant event chart tiles into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_QUANT_EVENT_TILE_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    parser.add_argument("--ttl", type=int, default=int(os.environ.get("POLYDATA_QUANT_EVENT_TILE_TTL_SECONDS", DEFAULT_TTL_SECONDS)))
    parser.add_argument("--max-events", type=int, default=int(os.environ.get("POLYDATA_QUANT_EVENT_TILE_MAX_EVENTS", DEFAULT_MAX_EVENTS)))
    parser.add_argument("--price-source", default=os.environ.get("POLYDATA_QUANT_EVENT_TILE_PRICE_SOURCE", DEFAULT_SOURCE))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("POLYDATA_QUANT_EVENT_TILE_LIMIT", DEFAULT_LIMIT)))
    parser.add_argument("--max-outcomes", type=int, default=int(os.environ.get("POLYDATA_QUANT_EVENT_TILE_MAX_OUTCOMES", DEFAULT_MAX_OUTCOMES)))
    parser.add_argument("--top-n", type=int, default=int(os.environ.get("POLYDATA_QUANT_EVENT_TILE_TOP_N", DEFAULT_TOP_N)))
    parser.add_argument("--max-points", type=int, default=int(os.environ.get("POLYDATA_QUANT_EVENT_TILE_MAX_POINTS", DEFAULT_MAX_POINTS)))
    parser.add_argument("--range", default=os.environ.get("POLYDATA_QUANT_EVENT_TILE_RANGE", DEFAULT_RANGE))
    parser.add_argument("--resolution", default=os.environ.get("POLYDATA_QUANT_EVENT_TILE_RESOLUTION", DEFAULT_RESOLUTION))
    parser.add_argument("--point-format", default=os.environ.get("POLYDATA_QUANT_EVENT_TILE_POINT_FORMAT", DEFAULT_POINT_FORMAT))
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    settings = load_api_settings()
    watcher = QuantEventTileWatcher(
        redis_url=settings.redis_url,
        redis_prefix=settings.redis_prefix,
        snapshot_sqlite_path=settings.snapshot_sqlite_path,
        interval_seconds=args.interval,
        ttl_seconds=args.ttl,
        max_events=args.max_events,
        price_source=args.price_source,
        limit=args.limit,
        max_outcomes=args.max_outcomes,
        top_n=args.top_n,
        max_points=args.max_points,
        tile_range=getattr(args, "range"),
        resolution=args.resolution,
        point_format=args.point_format,
    )
    watcher.redis_client.ping()
    print(
        f"[quant-event-tile] source={watcher.price_source} max_events={watcher.max_events} ttl={watcher.ttl_seconds}",
        file=sys.stderr,
    )
    if not args.watch:
        print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        return 0
    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[quant-event-tile] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(watcher.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
