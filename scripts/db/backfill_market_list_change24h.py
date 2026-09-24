#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backfill market_list_serving.price_24h_ago in resumable batches.

This job keeps 24h change data off the request path by processing
market_list_serving incrementally and storing progress in sync_state.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import add_db_cli_args, configure_db_from_args, describe_db_target, dict_from_row, get_connection, init_schema
from db.sync_trade_analytics import _refresh_market_list_price_24h_ago, _threshold_datetime_24h

MARKET_LIST_CHANGE24H_BACKFILL_SYNC_KEY = "market_list_change24h_backfill"
DEFAULT_BATCH_SIZE = 10
DEFAULT_WATCH_INTERVAL_SECONDS = 15


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(value: Optional[str]) -> datetime:
    text = str(value or "").strip()
    if not text:
        return _threshold_datetime_24h()
    normalized = text.replace(" UTC", "Z").replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_state(conn, sync_state_key: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT value, last_block, updated_at FROM sync_state WHERE `key` = ?",
        (sync_state_key,),
    ).fetchone()
    if not row:
        return {}
    payload = dict_from_row(row)
    try:
        value_payload = json.loads(payload.get("value") or "{}")
    except json.JSONDecodeError:
        value_payload = {}
    return {
        "threshold": value_payload.get("threshold"),
        "last_market_id": int(value_payload.get("last_market_id") or payload.get("last_block") or 0),
        "started_at": value_payload.get("started_at"),
        "completed_at": value_payload.get("completed_at"),
        "updated_at": payload.get("updated_at"),
    }


def _save_state(conn, sync_state_key: str, state: Dict[str, Any]) -> None:
    value = json.dumps(
        {
            "threshold": state.get("threshold"),
            "last_market_id": int(state.get("last_market_id") or 0),
            "started_at": state.get("started_at"),
            "completed_at": state.get("completed_at"),
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO sync_state (`key`, value, last_block, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            sync_state_key,
            value,
            int(state.get("last_market_id") or 0),
            _utc_now_iso(),
        ),
    )


def _start_new_cycle() -> Dict[str, Any]:
    return {
        "threshold": _threshold_datetime_24h().isoformat().replace("+00:00", "Z"),
        "last_market_id": 0,
        "started_at": _utc_now_iso(),
        "completed_at": None,
    }


def run_backfill(
    db_path: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: Optional[int] = None,
    sync_state_key: str = MARKET_LIST_CHANGE24H_BACKFILL_SYNC_KEY,
    force_new_cycle: bool = False,
    verbose: bool = True,
) -> Dict[str, Any]:
    init_schema(db_path=db_path)
    conn = get_connection(db_path)
    batches = 0
    updated_markets = 0
    try:
        state = _load_state(conn, sync_state_key)
        if force_new_cycle or not state or state.get("completed_at"):
            state = _start_new_cycle()

        threshold_dt = _parse_iso_datetime(state.get("threshold"))
        while True:
            if max_batches is not None and batches >= max_batches:
                break
            rows = conn.execute(
                """
                SELECT market_id
                FROM market_list_serving
                WHERE market_id > ?
                ORDER BY market_id ASC
                LIMIT ?
                """,
                (int(state.get("last_market_id") or 0), int(batch_size)),
            ).fetchall()
            market_ids = [
                int(dict_from_row(row)["market_id"])
                for row in rows
                if dict_from_row(row).get("market_id") is not None
            ]
            if not market_ids:
                state["last_market_id"] = 0
                state["completed_at"] = _utc_now_iso()
                _save_state(conn, sync_state_key, state)
                conn.commit()
                return {
                    "batches": batches,
                    "updated_markets": updated_markets,
                    "completed": True,
                    "threshold": state.get("threshold"),
                    "last_market_id": 0,
                }

            batch_updated = _refresh_market_list_price_24h_ago(conn, market_ids, threshold_dt)
            updated_markets += batch_updated
            batches += 1
            state["last_market_id"] = market_ids[-1]
            state["completed_at"] = None
            _save_state(conn, sync_state_key, state)
            conn.commit()

            if verbose:
                print(
                    f"[market-change24h-backfill] batch={batches} updated={batch_updated} "
                    f"last_market_id={state['last_market_id']} threshold={state['threshold']}",
                    file=sys.stderr,
                )

        return {
            "batches": batches,
            "updated_markets": updated_markets,
            "completed": False,
            "threshold": state.get("threshold"),
            "last_market_id": int(state.get("last_market_id") or 0),
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill market_list_serving.price_24h_ago in resumable batches")
    add_db_cli_args(parser)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="每批回填多少个 market_id")
    parser.add_argument("--max-batches", type=int, default=None, help="最多执行多少批；默认直到本轮完成")
    parser.add_argument("--sync-state-key", default=MARKET_LIST_CHANGE24H_BACKFILL_SYNC_KEY, help="sync_state 中记录回填进度的 key")
    parser.add_argument("--force-new-cycle", action="store_true", help="忽略未完成进度，重新开始当前回填轮次")
    parser.add_argument("--watch", action="store_true", help="持续运行；每轮完成后等待并开启下一轮")
    parser.add_argument("--interval", type=int, default=DEFAULT_WATCH_INTERVAL_SECONDS, help="--watch 模式下两轮之间的等待秒数")

    args = parser.parse_args()
    configure_db_from_args(args)
    db_path = args.sqlite_path
    interval_seconds = max(1, int(args.interval))

    print(f"[market-change24h-backfill] target={describe_db_target()}", file=sys.stderr)
    try:
        force_new_cycle = bool(args.force_new_cycle)
        while True:
            started_at = _utc_now_iso()
            print(f"[market-change24h-backfill] cycle-start started_at={started_at}", file=sys.stderr)
            result = run_backfill(
                db_path=db_path,
                batch_size=max(1, int(args.batch_size)),
                max_batches=args.max_batches,
                sync_state_key=args.sync_state_key,
                force_new_cycle=force_new_cycle,
                verbose=True,
            )
            force_new_cycle = False
            print(
                f"[market-change24h-backfill] cycle-done batches={result['batches']} "
                f"updated_markets={result['updated_markets']} completed={result['completed']} "
                f"last_market_id={result['last_market_id']}",
                file=sys.stderr,
            )
            if not args.watch:
                break
            if result["completed"]:
                force_new_cycle = True
            print(
                f"[market-change24h-backfill] sleeping interval_seconds={interval_seconds}",
                file=sys.stderr,
            )
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("[market-change24h-backfill] interrupted, exiting", file=sys.stderr)


if __name__ == "__main__":
    main()
