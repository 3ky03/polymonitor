#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
for path in (str(SCRIPTS_ROOT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

# This job seeds only Agent snapshots. API startup prewarm fans out to many
# unrelated external sources, so keep it off even if the shared env enables it.
os.environ["POLYDATA_SNAPSHOT_PREWARM"] = "0"

from agent.market_wide.snapshot import DEFAULT_LENSES, seed_market_wide_snapshots, snapshot_response
from api_server import get_route_context, initialize_runtime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed market-wide Agent insight snapshots.")
    parser.add_argument("--lens", action="append", choices=DEFAULT_LENSES, help="Lens to seed. Repeatable. Defaults to all lenses.")
    parser.add_argument("--fallback-only", action="store_true", help="Write deterministic snapshots without calling external Agent APIs.")
    parser.add_argument("--force", action="store_true", help="Ignore the minimum live interval and refresh the selected snapshot(s).")
    parser.add_argument("--json", action="store_true", help="Print machine-readable seed summary.")
    parser.add_argument("--init-schema", action="store_true", help="Initialize database schema before seeding.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    initialize_runtime(skip_init_schema=not args.init_schema, log_startup=False)
    lenses = tuple(args.lens or DEFAULT_LENSES)
    snapshots = seed_market_wide_snapshots(get_route_context(), lenses, live=not args.fallback_only, force=args.force)
    summary = [
        {
            "lens": snapshot.get("lens"),
            "status": snapshot_response(snapshot).get("status"),
            "cacheStatus": snapshot_response(snapshot).get("cacheStatus"),
            "liveAttempted": snapshot.get("liveAttempted"),
            "skipped": bool(snapshot.get("skipped")),
            "skipReason": snapshot.get("skipReason"),
            "generatedAt": snapshot.get("generatedAt"),
            "expiresAt": snapshot.get("expiresAt"),
            "budget": snapshot.get("budget"),
        }
        for snapshot in snapshots
    ]
    if args.json:
        print(json.dumps({"seeded": summary}, ensure_ascii=True, indent=2, sort_keys=True))
    else:
        for item in summary:
            print(
                "seeded lens={lens} status={status} cache={cacheStatus} live={liveAttempted} generatedAt={generatedAt}".format(
                    **item
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
