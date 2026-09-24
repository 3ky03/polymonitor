#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量回填 UMA Oracle 历史数据。

设计目标：
1. 从头到尾补齐 oracle_events
2. 使用独立 sync_state key，不影响现有实时增量同步
3. 按区块窗口分批执行，失败后可 resume
4. 依赖 oracle_events(tx_hash, log_index) 唯一约束，重复事件自动覆盖/跳过
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Iterator, Optional, Tuple

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from config import get_rpc_url
from db import add_db_cli_args, configure_db_from_args, describe_db_target
from oracle.fetch_uma_oracle_chain import (
    ADAPTER_FULL_HISTORY_START_BLOCK,
    BATCH_BLOCKS_ADAPTER,
    BATCH_BLOCKS_ORACLE,
    CONTINUE_SYNC_REWIND_BLOCKS,
    _build_web3,
    _call_with_retries,
    DEFAULT_ADAPTER_ADDRESSES,
    DEFAULT_NEG_RISK_OPERATOR_ADDRESSES,
    DEFAULT_ORACLE_ADDRESSES,
    _format_rpc_error,
    _is_transient_rpc_error,
    get_last_oracle_synced_block,
    resolve_auto_start_block,
    run,
)
from oracle.fetch_updown_oracle_chain import (
    DEFAULT_UPDOWN_SYNC_STATE_KEY,
    resolve_updown_start_block,
    run_updown_oracle_backfill,
)


DEFAULT_BACKFILL_SYNC_STATE_KEY = "oracle_backfill_full"
DEFAULT_CHUNK_BLOCKS = 500_000
DEFAULT_OVERLAP_BLOCKS = 500
CHUNK_MAX_RETRIES = 8


def _resolve_latest_block(rpc_url: str, confirmations: int) -> int:
    w3 = _build_web3(rpc_url)
    latest = int(_call_with_retries("eth_blockNumber", lambda: w3.eth.block_number))
    return max(0, latest - max(0, int(confirmations)))


def _iter_ranges(start_block: int, end_block: int, chunk_blocks: int, overlap_blocks: int) -> Iterator[Tuple[int, int]]:
    if chunk_blocks <= 0:
        raise ValueError("chunk_blocks must be > 0")
    if overlap_blocks < 0:
        raise ValueError("overlap_blocks must be >= 0")
    if overlap_blocks >= chunk_blocks:
        raise ValueError("overlap_blocks must be smaller than chunk_blocks")

    current = int(start_block)
    while current <= end_block:
        window_end = min(current + chunk_blocks - 1, end_block)
        yield current, window_end
        if window_end >= end_block:
            break
        current = window_end + 1 - overlap_blocks


def _resolve_start_block(
    db_path: Optional[str],
    requested_start: Optional[int],
    *,
    rpc_url: str,
    end_block: int,
    resume: bool,
    sync_state_key: str,
    overlap_blocks: int,
) -> int:
    if requested_start is None:
        w3 = _build_web3(rpc_url)
        start_block = resolve_auto_start_block(
            w3,
            db_path or "",
            end_block,
            DEFAULT_ADAPTER_ADDRESSES,
            DEFAULT_ORACLE_ADDRESSES,
            DEFAULT_NEG_RISK_OPERATOR_ADDRESSES,
        )
    else:
        start_block = max(0, int(requested_start))
    if not resume:
        return start_block
    last = get_last_oracle_synced_block(db_path or "", sync_state_key=sync_state_key)
    if last is None:
        return start_block
    return max(start_block, max(0, int(last) + 1 - max(0, int(overlap_blocks))))


def _compute_chunk_retry_delay(attempt: int) -> int:
    return min(30 * (2 ** max(0, attempt - 1)), 300)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill full UMA oracle history into oracle_events with chunked resume support"
    )
    parser.add_argument("--rpc", default=None, help="RPC URL")
    parser.add_argument(
        "--start-block",
        type=int,
        default=None,
        help="回填起始区块；默认自动取 max(最早 market.created_at 对应区块, 已配置 oracle/adapter/operator 最早部署区块)",
    )
    parser.add_argument("--end-block", type=int, default=None, help="回填结束区块；默认 latest-confirmations")
    parser.add_argument("--confirmations", type=int, default=20, help="自动取最新区块时保留确认块数")
    parser.add_argument(
        "--chunk-blocks",
        type=int,
        default=DEFAULT_CHUNK_BLOCKS,
        help=f"每轮外层回填窗口大小（default: {DEFAULT_CHUNK_BLOCKS}）",
    )
    parser.add_argument(
        "--overlap-blocks",
        type=int,
        default=DEFAULT_OVERLAP_BLOCKS,
        help=f"相邻窗口回看多少区块，重复事件会自动去重（default: {DEFAULT_OVERLAP_BLOCKS}）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从 sync_state 的 backfill 进度继续",
    )
    parser.add_argument(
        "--sync-state-key",
        default=DEFAULT_BACKFILL_SYNC_STATE_KEY,
        help=f"回填进度 sync_state key（default: {DEFAULT_BACKFILL_SYNC_STATE_KEY}）",
    )
    parser.add_argument(
        "--only-updown",
        action="store_true",
        help="只回补 updown 专用 resolver/CTF 结算链路",
    )
    parser.add_argument(
        "--batch-adapter",
        type=int,
        default=BATCH_BLOCKS_ADAPTER,
        help=f"传给 fetch_uma_oracle_chain 的 adapter 内层批次（default: {BATCH_BLOCKS_ADAPTER}）",
    )
    parser.add_argument(
        "--batch-oracle",
        type=int,
        default=BATCH_BLOCKS_ORACLE,
        help=f"传给 fetch_uma_oracle_chain 的 oracle 内层批次（default: {BATCH_BLOCKS_ORACLE}）",
    )
    parser.add_argument("--max-workers", type=int, default=8, help="每个窗口内部抓链并发数")
    add_db_cli_args(parser)
    args = parser.parse_args()

    configure_db_from_args(args)
    db_path = args.sqlite_path
    rpc_url = args.rpc or get_rpc_url()
    effective_sync_state_key = args.sync_state_key
    if args.only_updown and effective_sync_state_key == DEFAULT_BACKFILL_SYNC_STATE_KEY:
        effective_sync_state_key = DEFAULT_UPDOWN_SYNC_STATE_KEY

    effective_end = int(args.end_block) if args.end_block is not None else _resolve_latest_block(rpc_url, args.confirmations)
    if args.only_updown and args.start_block is None:
        w3 = _build_web3(rpc_url)
        base_start = resolve_updown_start_block(w3, db_path, effective_end)
        if args.resume:
            last = get_last_oracle_synced_block(db_path or "", sync_state_key=effective_sync_state_key)
            if last is not None:
                effective_start = max(base_start, max(0, int(last) + 1 - max(0, int(args.overlap_blocks))))
            else:
                effective_start = base_start
        else:
            effective_start = base_start
    else:
        effective_start = _resolve_start_block(
            db_path,
            args.start_block,
            rpc_url=rpc_url,
            end_block=effective_end,
            resume=args.resume,
            sync_state_key=effective_sync_state_key,
            overlap_blocks=args.overlap_blocks,
        )

    if effective_start > effective_end:
        print(
            f"Nothing to do: start_block={effective_start} is greater than end_block={effective_end}",
            file=sys.stderr,
        )
        return

    print(f"Database target: {describe_db_target()}", file=sys.stderr)
    print(
        f"[oracle-backfill] range={effective_start}-{effective_end} "
        f"chunk_blocks={args.chunk_blocks} overlap={args.overlap_blocks} "
        f"sync_state_key={effective_sync_state_key}"
        + (" mode=updown" if args.only_updown else " mode=uma"),
        file=sys.stderr,
    )

    total_ranges = 0
    for range_index, (window_start, window_end) in enumerate(
        _iter_ranges(effective_start, effective_end, args.chunk_blocks, args.overlap_blocks),
        start=1,
    ):
        total_ranges += 1
        adapter_start = ADAPTER_FULL_HISTORY_START_BLOCK if range_index == 1 and effective_start == 0 else window_start
        print(
            f"\n[oracle-backfill] chunk #{range_index}: oracle={window_start}-{window_end}, "
            f"adapter={adapter_start}-{window_end}",
            file=sys.stderr,
        )
        chunk_attempt = 0
        while True:
            try:
                if args.only_updown:
                    run_updown_oracle_backfill(
                        rpc_url=rpc_url,
                        from_block=window_start,
                        to_block=window_end,
                        db_path=db_path,
                        batch_blocks=args.batch_oracle,
                        max_workers=args.max_workers,
                        sync_state_key=effective_sync_state_key,
                        include_legacy_ctf=True,
                    )
                else:
                    run(
                        rpc_url=rpc_url,
                        adapter_start_block=adapter_start,
                        oracle_start_block=window_start,
                        end_block=window_end,
                        db_path=db_path,
                        batch_adapter=args.batch_adapter,
                        batch_oracle=args.batch_oracle,
                        max_workers=args.max_workers,
                        sync_state_key=effective_sync_state_key,
                        continue_sync=False,
                        continue_sync_rewind_blocks=CONTINUE_SYNC_REWIND_BLOCKS,
                    )
                break
            except Exception as exc:
                if not _is_transient_rpc_error(exc):
                    raise
                chunk_attempt += 1
                if chunk_attempt >= CHUNK_MAX_RETRIES:
                    raise RuntimeError(
                        f"Chunk {range_index} failed after {CHUNK_MAX_RETRIES} transient retries: "
                        f"{_format_rpc_error(exc)}"
                    ) from exc
                delay = _compute_chunk_retry_delay(chunk_attempt)
                print(
                    f"[oracle-backfill] chunk #{range_index} transient RPC failure "
                    f"({chunk_attempt}/{CHUNK_MAX_RETRIES}): {_format_rpc_error(exc)}",
                    file=sys.stderr,
                )
                print(
                    f"[oracle-backfill] sleeping {delay}s, then retry chunk #{range_index}...",
                    file=sys.stderr,
                )
                time.sleep(delay)

    print(f"\n[oracle-backfill] Completed {total_ranges} chunk(s).", file=sys.stderr)


if __name__ == "__main__":
    main()
