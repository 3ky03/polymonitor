#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed World Cup core snapshots: schedule, odds, and panel projections."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict

_scripts_root = Path(__file__).resolve().parents[1]
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from api.services import worldcup_dashboard_service
from runtime.worldcup_seed_common import WorldCupSeedWatcher, env_int, record_count

DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60
SERVICE_NAME = "polydata-worldcup-core-seed.service"
SEED_META_KEY = "worldcup-core"


class WorldCupCoreWatcher(WorldCupSeedWatcher):
    def __init__(self, *, interval_seconds: int) -> None:
        super().__init__(
            mode="core",
            service_name=SERVICE_NAME,
            seed_meta_key=SEED_META_KEY,
            interval_seconds=interval_seconds,
        )

    def run_once(self) -> Dict[str, Any]:
        ctx = self.service_context()
        try:
            core = worldcup_dashboard_service.build_worldcup_core_payload(ctx, include_live_market_links=True)
            core = worldcup_dashboard_service.store_worldcup_core_snapshot(ctx, core)
            live = worldcup_dashboard_service.get_worldcup_live_snapshot(ctx)
            dashboard = worldcup_dashboard_service.merge_worldcup_core_live_payload(core, live)
            dashboard = worldcup_dashboard_service.store_worldcup_dashboard_snapshot(ctx, dashboard)
        except Exception as exc:
            self.store_seed_meta(
                status="error",
                record_count_value=0,
                source_states={"worldcupCore": "error"},
                error_summary=str(exc),
                preserve_last_success=True,
            )
            raise
        count = record_count(core)
        states = core.get("providerStates") if isinstance(core.get("providerStates"), dict) else {}
        status = "ok" if len(core.get("matches") or []) >= 64 else "degraded"
        self.store_seed_meta(
            status=status,
            record_count_value=count,
            source_states=states,
            error_summary=None if status == "ok" else "World Cup core snapshot is degraded",
        )
        return {
            "status": status,
            "mode": "core",
            "recordCount": count,
            "dashboardGeneratedAt": dashboard.get("generatedAt"),
            "summary": core.get("summary"),
            "providerStates": states,
        }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed World Cup core snapshots into Redis and SQLite")
    parser.add_argument("--watch", action="store_true", help="Run continuously instead of once")
    parser.add_argument(
        "--interval",
        type=int,
        default=env_int("POLYDATA_WORLDCUP_CORE_WATCH_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS),
        help="Seconds between core refresh runs in watch mode",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    watcher = WorldCupCoreWatcher(interval_seconds=args.interval)
    watcher.redis_client.ping()
    print(
        f"[worldcup-core] redis_key={watcher.redis_prefix}{worldcup_dashboard_service.WORLDCUP_CORE_NAMESPACE}:{worldcup_dashboard_service.WORLDCUP_CORE_CACHE_KEY} sqlite={watcher.settings.snapshot_sqlite_path}",
        file=sys.stderr,
    )
    if not args.watch:
        watcher.print_result(watcher.run_once())
        return 0

    while True:
        try:
            watcher.print_result(watcher.run_once())
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[worldcup-core] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(watcher.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
