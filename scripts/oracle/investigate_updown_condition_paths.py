#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调查 updown 市场的 condition 创建/结算路径。

当前目标：
1. 从 markets 中抽取一批已结束 updown 市场
2. 用 condition_id / question_id 反查 CTF 的 ConditionPreparation / ConditionResolution
3. 反查已知 UMA adapter 的 QuestionInitialized
4. 命中时补充 tx sender / receipt log 地址，帮助定位真实创建者与结算者
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from web3 import Web3

try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:
    try:
        from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware
    except ImportError:
        ExtraDataToPOAMiddleware = None

from config import get_rpc_url
from db import add_db_cli_args, configure_db_from_args, describe_db_target, get_connection, dict_from_row
from oracle.fetch_uma_oracle_chain import _build_web3


DEFAULT_CURRENT_CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
DEFAULT_LEGACY_CTF = "0xC59b0e4De5F1248C1140964E0fF287B192407E0C"
DEFAULT_ADAPTERS = [
    "0xCB1822859cEF82Cd2Eb4E6276C7916e692995130",
    "0x65070BE91477460D8A7AeEb94ef92fe056C2f2A7",
    "0x69c47De9D4D3Dad79590d61b9e05918E03775f24",
    "0xb21182d0494521Cf45DbbeEbb5A3ACAAb6d22093",
    "0x2F5e3684cb1F318ec51b00Edba38d79Ac2c0aA9d",
    "0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74",
    "0x157Ce2d672854c848c9b79C49a8Cc6cc89176a49",
]
DEFAULT_CHUNK_SIZE = 5_000

COND_PREP_SIG = "0x" + Web3.keccak(text="ConditionPreparation(bytes32,address,bytes32,uint256)").hex()
COND_RES_SIG = "0x" + Web3.keccak(text="ConditionResolution(bytes32,address,bytes32,uint256,uint256[])").hex()
QINIT_SIG = "0x" + Web3.keccak(
    text="QuestionInitialized(bytes32,uint256,address,bytes,address,uint256,uint256)"
).hex()


def _normalize_address(value: str) -> str:
    return Web3.to_checksum_address(value)


def _topic32(value: str) -> str:
    return "0x" + str(value).lower().removeprefix("0x").rjust(64, "0")


def _iter_ranges(from_block: int, to_block: int, chunk_size: int) -> Iterable[Tuple[int, int]]:
    current = int(from_block)
    while current <= to_block:
        end = min(current + chunk_size - 1, to_block)
        yield current, end
        current = end + 1


def _query_recent_ended_updown_markets(limit: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, slug, gamma_market_id, question_id, condition_id, end_date, created_at
            FROM markets
            WHERE slug LIKE ?
              AND gamma_market_id IS NOT NULL AND TRIM(gamma_market_id) <> ''
              AND question_id IS NOT NULL AND TRIM(question_id) <> ''
              AND end_date IS NOT NULL AND TRIM(end_date) <> ''
              AND STR_TO_DATE(REPLACE(REPLACE(end_date, 'T', ' '), 'Z', ''), '%%Y-%%m-%%d %%H:%%i:%%s') < UTC_TIMESTAMP()
            ORDER BY created_at DESC
            LIMIT ?
            """,
            ("%-updown-%", int(limit)),
        )
        return [dict_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _first_log_hit(
    w3: Web3,
    *,
    address: str,
    from_block: int,
    to_block: int,
    topics: List[Optional[str]],
    chunk_size: int,
) -> List[Dict[str, Any]]:
    for start, end in _iter_ranges(from_block, to_block, chunk_size):
        try:
            logs = w3.eth.get_logs(
                {
                    "address": address,
                    "fromBlock": start,
                    "toBlock": end,
                    "topics": topics,
                }
            )
        except Exception:
            continue
        if logs:
            return [dict(log) for log in logs]
    return []


def _summarize_receipt_context(w3: Web3, tx_hash: str) -> Dict[str, Any]:
    tx = w3.eth.get_transaction(tx_hash)
    receipt = w3.eth.get_transaction_receipt(tx_hash)
    log_addresses: List[str] = []
    for log in receipt.get("logs", []):
        address = str(log.get("address") or "").lower()
        if address and address not in log_addresses:
            log_addresses.append(address)
    return {
        "tx_hash": tx_hash,
        "tx_from": (tx.get("from") or "").lower() if tx else "",
        "tx_to": (tx.get("to") or "").lower() if tx and tx.get("to") else "",
        "block_number": int(receipt.get("blockNumber", 0) or 0),
        "log_addresses": log_addresses,
        "log_count": len(receipt.get("logs", [])),
    }


def _match_known_adapter(condition_id: str, question_id: str, adapters: List[str]) -> List[str]:
    matches: List[str] = []
    q_bytes = Web3.to_bytes(hexstr=question_id)
    for adapter in adapters:
        computed = Web3.solidity_keccak(["address", "bytes32", "uint256"], [adapter, q_bytes, 2]).hex()
        if computed.lower() == condition_id.lower():
            matches.append(adapter)
    return matches


def investigate_markets(
    w3: Web3,
    markets: List[Dict[str, Any]],
    *,
    from_block: int,
    to_block: int,
    chunk_size: int,
    ctf_addresses: List[str],
    adapter_addresses: List[str],
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    summary = {
        "sample_size": len(markets),
        "window": [from_block, to_block],
        "prepared_count": 0,
        "resolved_count": 0,
        "question_initialized_count": 0,
        "known_adapter_condition_id_match_count": 0,
    }

    for market in markets:
        question_id = str(market["question_id"])
        condition_id = str(market["condition_id"])
        qtopic = _topic32(question_id)
        ctopic = _topic32(condition_id)

        market_result: Dict[str, Any] = {
            "id": market["id"],
            "slug": market["slug"],
            "gamma_market_id": market["gamma_market_id"],
            "question_id": question_id,
            "condition_id": condition_id,
            "ctf_hits": [],
            "question_initialized_hits": [],
            "condition_id_matches_known_adapters": _match_known_adapter(condition_id, question_id, adapter_addresses),
        }
        if market_result["condition_id_matches_known_adapters"]:
            summary["known_adapter_condition_id_match_count"] += 1

        for ctf_name, ctf_address in ctf_addresses:
            prep_logs = _first_log_hit(
                w3,
                address=ctf_address,
                from_block=from_block,
                to_block=to_block,
                topics=[COND_PREP_SIG, ctopic, None, qtopic],
                chunk_size=chunk_size,
            )
            res_logs = _first_log_hit(
                w3,
                address=ctf_address,
                from_block=from_block,
                to_block=to_block,
                topics=[COND_RES_SIG, ctopic, None, qtopic],
                chunk_size=chunk_size,
            )
            if prep_logs or res_logs:
                ctf_hit: Dict[str, Any] = {
                    "ctf_name": ctf_name,
                    "ctf_address": ctf_address,
                    "prepare_hits": [],
                    "resolution_hits": [],
                }
                for log in prep_logs[:3]:
                    tx_hash = log["transactionHash"].hex()
                    ctf_hit["prepare_hits"].append(_summarize_receipt_context(w3, tx_hash))
                for log in res_logs[:3]:
                    tx_hash = log["transactionHash"].hex()
                    ctf_hit["resolution_hits"].append(_summarize_receipt_context(w3, tx_hash))
                market_result["ctf_hits"].append(ctf_hit)

        for adapter in adapter_addresses:
            qinit_logs = _first_log_hit(
                w3,
                address=adapter,
                from_block=from_block,
                to_block=to_block,
                topics=[QINIT_SIG, qtopic],
                chunk_size=chunk_size,
            )
            if qinit_logs:
                market_result["question_initialized_hits"].append(
                    {
                        "adapter": adapter,
                        "hits": [_summarize_receipt_context(w3, log["transactionHash"].hex()) for log in qinit_logs[:3]],
                    }
                )

        if any(hit["prepare_hits"] for hit in market_result["ctf_hits"]):
            summary["prepared_count"] += 1
        if any(hit["resolution_hits"] for hit in market_result["ctf_hits"]):
            summary["resolved_count"] += 1
        if market_result["question_initialized_hits"]:
            summary["question_initialized_count"] += 1

        results.append(market_result)

    return {
        "database_target": describe_db_target(),
        "summary": summary,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Investigate recent ended updown markets via condition_id on-chain traces")
    parser.add_argument("--rpc", default=None, help="RPC URL")
    parser.add_argument("--sample-size", type=int, default=20, help="抽取最近已结束 updown 市场数量")
    parser.add_argument("--from-block", type=int, required=True, help="链上调查起始区块")
    parser.add_argument("--to-block", type=int, required=True, help="链上调查结束区块")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="eth_getLogs 分片区块数")
    parser.add_argument("--include-legacy-ctf", action="store_true", help="同时扫描旧 CTF 地址")
    parser.add_argument("--output", default=None, help="可选 JSON 输出路径")
    add_db_cli_args(parser)
    args = parser.parse_args()

    configure_db_from_args(args)
    rpc_url = args.rpc or get_rpc_url()
    w3 = _build_web3(rpc_url)

    markets = _query_recent_ended_updown_markets(args.sample_size)
    ctf_addresses = [("current_ctf", _normalize_address(DEFAULT_CURRENT_CTF))]
    if args.include_legacy_ctf:
        ctf_addresses.append(("legacy_ctf", _normalize_address(DEFAULT_LEGACY_CTF)))
    adapter_addresses = [_normalize_address(address) for address in DEFAULT_ADAPTERS]

    report = investigate_markets(
        w3,
        markets,
        from_block=args.from_block,
        to_block=args.to_block,
        chunk_size=args.chunk_size,
        ctf_addresses=ctf_addresses,
        adapter_addresses=adapter_addresses,
    )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote report to {args.output}", file=sys.stderr)
    print(text)


if __name__ == "__main__":
    main()
