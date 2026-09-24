#!/usr/bin/env python3
"""Sync Polygon block timestamps into ClickHouse.

The OrderFilled fact table intentionally does not store per-row block_time.
This script keeps a lightweight block_number -> block_time dimension table that
can be filled on demand for the date ranges used by research/backtests.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTAINER = "polydata_clickhouse_orderfilled"
DEFAULT_DATABASE = "poly_orderfilled"
DEFAULT_USER = "poly_user"
DEFAULT_PASSWORD = ""


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_rpc_url(args: argparse.Namespace) -> str:
    if args.rpc_url:
        return args.rpc_url
    load_dotenv(PROJECT_ROOT / ".env")
    rpc_url = os.environ.get("POLYMARKET_RPC_URL") or os.environ.get("NODE_URL")
    if not rpc_url:
        raise SystemExit("RPC URL missing. Pass --rpc-url or set POLYMARKET_RPC_URL/NODE_URL.")
    return rpc_url


@dataclass(frozen=True)
class ClickHouse:
    container: str
    database: str
    user: str
    password: str


def ch_cmd(ch: ClickHouse, query: str) -> list[str]:
    return [
        "docker",
        "exec",
        "-i",
        ch.container,
        "clickhouse-client",
        "--user",
        ch.user,
        "--password",
        ch.password,
        "--database",
        ch.database,
        "--query",
        query,
    ]


def ch_scalar(ch: ClickHouse, query: str) -> str:
    cmd = ch_cmd(ch, query)
    cmd.remove("-i")
    return subprocess.check_output(cmd, text=True).strip()


def ch_insert_tsv(ch: ClickHouse, query: str, rows: list[str]) -> None:
    if not rows:
        return
    subprocess.run(ch_cmd(ch, query), input="\n".join(rows) + "\n", text=True, check=True)


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def tsv_escape(value: object) -> str:
    if value is None:
        return r"\N"
    text = str(value)
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


class RpcClient:
    def __init__(self, rpc_url: str, timeout: int, trust_env_proxy: bool):
        self.rpc_url = rpc_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = trust_env_proxy
        self._request_id = 0

    def call(self, method: str, params: list) -> object:
        self._request_id += 1
        response = self.session.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise RuntimeError(payload["error"])
        return payload["result"]

    def latest_block(self) -> int:
        return int(str(self.call("eth_blockNumber", [])), 16)

    def block(self, block_number: int) -> dict:
        result = self.call("eth_getBlockByNumber", [hex(block_number), False])
        if not result:
            raise RuntimeError(f"missing block {block_number}")
        return result  # type: ignore[return-value]

    def block_timestamp(self, block_number: int) -> int:
        block = self.block(block_number)
        return int(str(block["timestamp"]), 16)


def lower_bound_block(rpc: RpcClient, target_timestamp: int, latest: int) -> int:
    low = 0
    high = latest
    while low < high:
        mid = (low + high) // 2
        if rpc.block_timestamp(mid) < target_timestamp:
            low = mid + 1
        else:
            high = mid
    return low


def resolve_date_range(args: argparse.Namespace, rpc: RpcClient) -> dict:
    tz = ZoneInfo(args.timezone)
    start_local = datetime.fromisoformat(args.date).replace(tzinfo=tz)
    end_local = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if start_local != end_local:
        raise SystemExit("--date must be YYYY-MM-DD, not a datetime")
    end_local = end_local.replace(day=end_local.day) + args.day_delta
    start_ts = int(start_local.timestamp())
    end_ts = int(end_local.timestamp())
    latest = rpc.latest_block()
    start_block = lower_bound_block(rpc, start_ts, latest)
    end_exclusive = lower_bound_block(rpc, end_ts, latest)
    return {
        "date": args.date,
        "timezone": args.timezone,
        "start_utc": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(end_ts, timezone.utc).isoformat(),
        "start_block": start_block,
        "end_exclusive_block": end_exclusive,
        "end_inclusive_block": end_exclusive - 1,
        "latest_block": latest,
    }


def max_synced_block(ch: ClickHouse) -> int:
    value = ch_scalar(ch, "SELECT toString(ifNull(max(block_number), 0)) FROM block_timestamps")
    return int(value or 0)


def parse_existing_blocks(ch: ClickHouse, from_block: int, to_block: int) -> set[int]:
    output = ch_scalar(
        ch,
        f"""
        SELECT block_number
        FROM block_timestamps
        WHERE block_number BETWEEN {int(from_block)} AND {int(to_block)}
        FORMAT TabSeparated
        """,
    )
    return {int(line) for line in output.splitlines() if line.strip()}


def fetch_block_row(rpc_url: str, timeout: int, trust_env_proxy: bool, block_number: int) -> tuple[int, str, str]:
    rpc = RpcClient(rpc_url, timeout=timeout, trust_env_proxy=trust_env_proxy)
    block = rpc.block(block_number)
    ts = int(str(block["timestamp"]), 16)
    block_hash = str(block.get("hash") or "").lower().removeprefix("0x")
    ts_text = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return block_number, ts_text, block_hash


def chunked(values: list[int], size: int) -> Iterable[list[int]]:
    for idx in range(0, len(values), size):
        yield values[idx : idx + size]


def backfill_range(args: argparse.Namespace, ch: ClickHouse, rpc_url: str, from_block: int, to_block: int) -> None:
    if to_block < from_block:
        print("[block-timestamps] empty range", file=sys.stderr)
        return
    total = to_block - from_block + 1
    if total > args.max_blocks and not args.allow_large_range:
        raise SystemExit(
            f"Refusing to fetch {total} blocks. Pass --allow-large-range or raise --max-blocks."
        )
    existing = parse_existing_blocks(ch, from_block, to_block) if args.skip_existing else set()
    blocks = [block for block in range(from_block, to_block + 1, args.step) if block not in existing]
    print(
        f"[block-timestamps] range={from_block}-{to_block} total={total} step={args.step} "
        f"existing={len(existing)} fetch={len(blocks)} workers={args.workers}",
        file=sys.stderr,
        flush=True,
    )
    if not blocks:
        return

    inserted = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(fetch_block_row, rpc_url, args.rpc_timeout, not args.no_proxy, block): block
            for block in blocks
        }
        pending_rows: list[str] = []
        for future in as_completed(future_map):
            block_number = future_map[future]
            try:
                row_block, block_time, block_hash = future.result()
            except Exception as exc:
                print(f"[block-timestamps] block={block_number} failed: {exc}", file=sys.stderr, flush=True)
                if args.fail_on_error:
                    raise
                continue
            pending_rows.append(
                "\t".join(
                    [
                        tsv_escape(row_block),
                        tsv_escape(block_time),
                        tsv_escape(block_hash),
                        tsv_escape(args.source),
                    ]
                )
            )
            if len(pending_rows) >= args.insert_batch:
                ch_insert_tsv(
                    ch,
                    "INSERT INTO block_timestamps (block_number, block_time, block_hash, source) FORMAT TabSeparated",
                    pending_rows,
                )
                inserted += len(pending_rows)
                pending_rows.clear()
                elapsed = max(0.001, time.time() - started)
                print(
                    f"[block-timestamps] inserted={inserted}/{len(blocks)} rate={inserted/elapsed:.1f}/s",
                    file=sys.stderr,
                    flush=True,
                )
        if pending_rows:
            ch_insert_tsv(
                ch,
                "INSERT INTO block_timestamps (block_number, block_time, block_hash, source) FORMAT TabSeparated",
                pending_rows,
            )
            inserted += len(pending_rows)
    elapsed = max(0.001, time.time() - started)
    print(f"[block-timestamps] done inserted={inserted} elapsed={elapsed:.1f}s rate={inserted/elapsed:.1f}/s")


def continue_sync_once(args: argparse.Namespace, ch: ClickHouse, rpc_url: str, rpc: RpcClient) -> dict:
    latest_confirmed = max(0, rpc.latest_block() - max(0, args.confirmations))
    max_existing = max_synced_block(ch)
    if args.from_block is not None:
        from_block = args.from_block
        start_reason = "explicit_from_block"
    elif max_existing > 0:
        from_block = max_existing + 1
        start_reason = "max_existing_plus_one"
    else:
        from_block = max(0, latest_confirmed - max(0, args.bootstrap_blocks) + 1)
        start_reason = "bootstrap_tail"

    if args.to_block is not None:
        to_block = min(args.to_block, latest_confirmed)
    else:
        to_block = latest_confirmed

    planned_blocks = max(0, to_block - from_block + 1)
    if (
        planned_blocks > args.max_catchup_blocks
        and not args.allow_large_range
        and args.from_block is None
    ):
        from_block = max(0, to_block - args.max_catchup_blocks + 1)
        planned_blocks = max(0, to_block - from_block + 1)
        start_reason = f"tail_capped_{args.max_catchup_blocks}"

    status = {
        "latest_confirmed": latest_confirmed,
        "max_existing": max_existing,
        "from_block": from_block,
        "to_block": to_block,
        "planned_blocks": planned_blocks,
        "start_reason": start_reason,
    }
    print(f"[block-timestamps] continue-sync {json.dumps(status, ensure_ascii=False)}", file=sys.stderr, flush=True)
    if planned_blocks <= 0:
        return status
    backfill_range(args, ch, rpc_url, from_block, to_block)
    return status


def day_delta_one(args: argparse.Namespace):
    from datetime import timedelta

    args.day_delta = timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync block_number -> block_time into ClickHouse")
    parser.add_argument("command", choices=("resolve-date", "backfill-range", "backfill-day", "continue-sync"))
    parser.add_argument("--date", help="Date in --timezone, e.g. 2026-06-02")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--from-block", type=int)
    parser.add_argument("--to-block", type=int)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--insert-batch", type=int, default=1000)
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--allow-large-range", action="store_true")
    parser.add_argument("--max-blocks", type=int, default=100000)
    parser.add_argument("--fail-on-error", action="store_true")
    parser.add_argument("--source", default="rpc")
    parser.add_argument("--confirmations", type=int, default=20)
    parser.add_argument("--bootstrap-blocks", type=int, default=2000)
    parser.add_argument("--max-catchup-blocks", type=int, default=5000)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--rpc-url")
    parser.add_argument("--rpc-timeout", type=int, default=20)
    parser.add_argument("--no-proxy", action="store_true", help="Ignore HTTP(S)_PROXY/ALL_PROXY for RPC calls")
    parser.add_argument("--clickhouse-container", default=DEFAULT_CONTAINER)
    parser.add_argument("--clickhouse-database", default=DEFAULT_DATABASE)
    parser.add_argument("--clickhouse-user", default=DEFAULT_USER)
    parser.add_argument("--clickhouse-password", default=os.environ.get("CLICKHOUSE_PASSWORD", DEFAULT_PASSWORD))
    args = parser.parse_args()
    day_delta_one(args)
    if not args.clickhouse_password:
        raise SystemExit("--clickhouse-password or CLICKHOUSE_PASSWORD is required")

    ch = ClickHouse(args.clickhouse_container, args.clickhouse_database, args.clickhouse_user, args.clickhouse_password)
    rpc_url = get_rpc_url(args)
    rpc = RpcClient(rpc_url, timeout=args.rpc_timeout, trust_env_proxy=not args.no_proxy)

    if args.command == "resolve-date":
        if not args.date:
            raise SystemExit("--date is required")
        print(json.dumps(resolve_date_range(args, rpc), ensure_ascii=False, indent=2))
        return

    if args.command == "backfill-day":
        if not args.date:
            raise SystemExit("--date is required")
        resolved = resolve_date_range(args, rpc)
        print(json.dumps(resolved, ensure_ascii=False), file=sys.stderr)
        backfill_range(args, ch, rpc_url, int(resolved["start_block"]), int(resolved["end_inclusive_block"]))
        return

    if args.command == "continue-sync":
        while True:
            continue_sync_once(args, ch, rpc_url, rpc)
            if not args.watch:
                return
            print(f"[block-timestamps] sleeping {args.interval_seconds}s", file=sys.stderr, flush=True)
            time.sleep(args.interval_seconds)

    if args.command == "backfill-range":
        if args.from_block is None or args.to_block is None:
            raise SystemExit("--from-block and --to-block are required")
        backfill_range(args, ch, rpc_url, args.from_block, args.to_block)


if __name__ == "__main__":
    main()
