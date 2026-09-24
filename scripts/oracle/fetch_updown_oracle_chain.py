#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取 updown 市场专用 resolver/CTF 结算链路。

思路：
1. 不依赖 markets.oracle/resolvedBy，因为 updown 在 Gamma payload 中常为空。
2. 直接扫描 CTF 的 ConditionPreparation / ConditionResolution。
3. 用 condition_id / question_id 过滤为 updown 市场。
4. 将准备事件映射为 request，将结算事件映射为 settle，写入 oracle_events。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from web3 import Web3

from db import dict_from_row, get_connection
from oracle.fetch_uma_oracle_chain import (
    _OracleDbWriter,
    _block_at_timestamp,
    _build_web3,
    _call_with_retries,
    _db_target_available,
    _format_rpc_error,
    _parse_any_datetime,
    fetch_logs_many_addresses,
    get_last_oracle_synced_block,
    save_oracle_synced_block,
)


DEFAULT_UPDOWN_SYNC_STATE_KEY = "oracle_backfill_updown"
DEFAULT_CURRENT_CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
DEFAULT_LEGACY_CTF_ADDRESS = "0xC59b0e4De5F1248C1140964E0fF287B192407E0C"
CTF_EVENT_SIGNATURES = {
    "request": "ConditionPreparation(bytes32,address,bytes32,uint256)",
    "settle": "ConditionResolution(bytes32,address,bytes32,uint256,uint256[])",
}


def _topic0(sig: str) -> str:
    return "0x" + Web3.keccak(text=sig).hex()


def _normalize_address(value: str) -> str:
    return Web3.to_checksum_address(value)


def _lower_or_empty(value: Any) -> str:
    return str(value or "").strip().lower()


def _topic32(value: str) -> str:
    return "0x" + str(value).lower().removeprefix("0x").rjust(64, "0")


def _hex32(value: Any) -> str:
    if value is None:
        return ""
    try:
        out = Web3.to_hex(value)
    except Exception:
        out = str(value)
    if not out:
        return ""
    if not out.startswith("0x"):
        out = "0x" + out
    return out.lower()


def _connect_readonly(db_path: Optional[str]):
    return get_connection(db_path, readonly=True)


def _get_earliest_updown_market_created_at(db_path: Optional[str]) -> Optional[str]:
    conn = _connect_readonly(db_path)
    try:
        cur = conn.execute(
            """
            SELECT MIN(created_at)
            FROM markets
            WHERE slug LIKE ?
              AND created_at IS NOT NULL
              AND TRIM(CAST(created_at AS TEXT)) <> ''
            """,
            ("%-updown-%",),
        )
        row = cur.fetchone()
        if row is None:
            return None
        if isinstance(row, tuple):
            return row[0]
        return list(dict_from_row(row).values())[0]
    finally:
        conn.close()


def resolve_updown_start_block(
    w3: Web3,
    db_path: Optional[str],
    end_block: int,
) -> int:
    # Do not map market.created_at to a block. That binary search issues
    # eth_getBlockByNumber timestamp calls and can silently burn proxy/RPC
    # traffic when a service restarts without a checkpoint.
    return max(0, end_block - 500_000)


def _build_updown_market_indices(db_path: Optional[str]) -> Dict[str, Dict[str, Any]]:
    conn = _connect_readonly(db_path)
    try:
        cur = conn.execute(
            """
            SELECT id, gamma_market_id, slug, title, description, question_id, condition_id
            FROM markets
            WHERE slug LIKE ?
              AND question_id IS NOT NULL
              AND TRIM(question_id) <> ''
              AND condition_id IS NOT NULL
              AND TRIM(condition_id) <> ''
            """,
            ("%-updown-%",),
        )
        rows = [dict_from_row(row) for row in cur.fetchall()]
    finally:
        conn.close()

    by_condition_id: Dict[str, Dict[str, Any]] = {}
    by_question_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        condition_id = _lower_or_empty(row.get("condition_id"))
        question_id = _lower_or_empty(row.get("question_id"))
        if condition_id:
            by_condition_id[condition_id] = row
        if question_id:
            by_question_id[question_id] = row
    return {
        "by_condition_id": by_condition_id,
        "by_question_id": by_question_id,
    }


def _decode_ctf_condition_preparation(w3: Web3, log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    abi = {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "conditionId", "type": "bytes32"},
            {"indexed": True, "name": "oracle", "type": "address"},
            {"indexed": True, "name": "questionId", "type": "bytes32"},
            {"indexed": False, "name": "outcomeSlotCount", "type": "uint256"},
        ],
        "name": "ConditionPreparation",
        "type": "event",
    }
    try:
        event = w3.eth.contract(abi=[abi]).events.ConditionPreparation()
        decoded = event.process_log(log)
        args = decoded["args"]
        return {
            "label": "request",
            "condition_id": _hex32(args.get("conditionId")),
            "oracle": _lower_or_empty(args.get("oracle")),
            "question_id": _hex32(args.get("questionId")),
            "outcome_slot_count": int(args.get("outcomeSlotCount", 0) or 0),
        }
    except Exception as exc:
        print(f"[updown-oracle] decode ConditionPreparation failed: {_format_rpc_error(exc)}", file=sys.stderr)
        return None


def _decode_ctf_condition_resolution(w3: Web3, log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    abi = {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "conditionId", "type": "bytes32"},
            {"indexed": True, "name": "oracle", "type": "address"},
            {"indexed": True, "name": "questionId", "type": "bytes32"},
            {"indexed": False, "name": "outcomeSlotCount", "type": "uint256"},
            {"indexed": False, "name": "payoutNumerators", "type": "uint256[]"},
        ],
        "name": "ConditionResolution",
        "type": "event",
    }
    try:
        event = w3.eth.contract(abi=[abi]).events.ConditionResolution()
        decoded = event.process_log(log)
        args = decoded["args"]
        payouts = [int(x) for x in (args.get("payoutNumerators") or [])]
        return {
            "label": "settle",
            "condition_id": _hex32(args.get("conditionId")),
            "oracle": _lower_or_empty(args.get("oracle")),
            "question_id": _hex32(args.get("questionId")),
            "outcome_slot_count": int(args.get("outcomeSlotCount", 0) or 0),
            "payout_numerators": payouts,
        }
    except Exception as exc:
        print(f"[updown-oracle] decode ConditionResolution failed: {_format_rpc_error(exc)}", file=sys.stderr)
        return None


def _decode_ctf_event(w3: Web3, log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    topics = log.get("topics") or []
    topic0 = _hex32(topics[0]) if topics else ""
    if topic0 == _topic0(CTF_EVENT_SIGNATURES["request"]):
        return _decode_ctf_condition_preparation(w3, log)
    if topic0 == _topic0(CTF_EVENT_SIGNATURES["settle"]):
        return _decode_ctf_condition_resolution(w3, log)
    return None


def _build_string_raw(market: Dict[str, Any], decoded: Dict[str, Any]) -> str:
    parts = [f"title: {market.get('title') or market.get('slug') or ''}"]
    description = str(market.get("description") or "").strip()
    if description:
        parts.append(f"description: {description}")
    gamma_market_id = str(market.get("gamma_market_id") or "").strip()
    if gamma_market_id:
        parts.append(f"gamma_market_id: {gamma_market_id}")
    parts.append(f"updown_oracle: {decoded.get('oracle') or ''}")
    if decoded.get("label") == "settle":
        parts.append(f"payouts: {json.dumps(decoded.get('payout_numerators') or [])}")
    return ", ".join(parts)


def _build_record(
    market: Dict[str, Any],
    decoded: Dict[str, Any],
    log: Dict[str, Any],
    event_time: str,
    tx_context: Dict[str, Any],
    ctf_address: str,
) -> Dict[str, Any]:
    tx_hash = log["transactionHash"].hex() if hasattr(log["transactionHash"], "hex") else str(log["transactionHash"])
    event_status = decoded["label"]
    actor = tx_context.get("actor") or ""
    payout_numerators = decoded.get("payout_numerators") or []
    payout_out = json.dumps(payout_numerators, ensure_ascii=False) if payout_numerators else ""
    return {
        "block_number": int(log.get("blockNumber", 0) or 0),
        "log_index": int(log.get("logIndex", 0) or 0),
        "event_time": event_time,
        "tx_hash": tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash,
        "event_status": event_status,
        "external_market_id": str(market.get("gamma_market_id") or ""),
        "market_id": int(market["id"]) if str(market.get("id") or "").isdigit() else None,
        "market_title": str(market.get("title") or ""),
        "source_adapter": str(decoded.get("oracle") or ""),
        "source_oracle": str(ctf_address or ""),
        "adapter_question_id": str(decoded.get("question_id") or ""),
        "matched_by": "by_updown_condition_id",
        "question_id": str(market.get("question_id") or decoded.get("question_id") or ""),
        "condition_id": str(market.get("condition_id") or decoded.get("condition_id") or ""),
        "string_raw": _build_string_raw(market, decoded),
        "p1": "",
        "p2": "",
        "proposed_price": "",
        "settled_price": "",
        "settlement_recipient": "",
        "payout": payout_out,
        "requester": actor if event_status == "request" else "",
        "proposer": "",
        "disputer": "",
        "request_transaction": tx_hash if event_status == "request" else "",
        "proposal_transaction": "",
        "settlement_transaction": tx_hash if event_status == "settle" else "",
    }


def run_updown_oracle_backfill(
    *,
    rpc_url: str,
    from_block: int,
    to_block: int,
    db_path: Optional[str],
    batch_blocks: int,
    max_workers: int,
    sync_state_key: str = DEFAULT_UPDOWN_SYNC_STATE_KEY,
    include_legacy_ctf: bool = True,
) -> Dict[str, Any]:
    if not _db_target_available(db_path or ""):
        raise RuntimeError("Database target is not available")

    w3 = _build_web3(rpc_url)
    market_indices = _build_updown_market_indices(db_path)
    by_condition_id = market_indices["by_condition_id"]
    by_question_id = market_indices["by_question_id"]

    ctf_addresses = [_normalize_address(DEFAULT_CURRENT_CTF_ADDRESS)]
    if include_legacy_ctf:
        ctf_addresses.append(_normalize_address(DEFAULT_LEGACY_CTF_ADDRESS))

    topic0s = [_topic0(CTF_EVENT_SIGNATURES["request"]), _topic0(CTF_EVENT_SIGNATURES["settle"])]
    logs = fetch_logs_many_addresses(
        w3,
        from_block,
        to_block,
        ctf_addresses,
        topic0s,
        batch_blocks=batch_blocks,
        max_workers=max_workers,
        label="Updown CTF events",
    )

    writer = _OracleDbWriter(str(Path(db_path or "").expanduser().resolve()))
    stats = {
        "logs_scanned": len(logs),
        "matched_events": 0,
        "request_events": 0,
        "settle_events": 0,
        "discovered_oracles": {},
    }

    matched_entries: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], str]] = []
    for log in logs:
        decoded = _decode_ctf_event(w3, log)
        if not decoded:
            continue
        market = by_condition_id.get(decoded["condition_id"]) or by_question_id.get(decoded["question_id"])
        if market is None:
            continue
        tx_hash = log["transactionHash"].hex() if hasattr(log["transactionHash"], "hex") else str(log["transactionHash"])
        matched_entries.append((log, decoded, market, tx_hash))

    tx_context = {"tx_from": "", "tx_to": "", "actor": "", "log_addresses": []}

    for log, decoded, market, tx_hash in matched_entries:
        record = _build_record(
            market,
            decoded,
            log,
            "",
            tx_context,
            _lower_or_empty(log.get("_source_address") or log.get("address") or ""),
        )
        writer.write(record)
        stats["matched_events"] += 1
        if stats["matched_events"] % 5000 == 0:
            print(
                f"[updown-oracle] written matched events: {stats['matched_events']}",
                file=sys.stderr,
            )
        if decoded["label"] == "request":
            stats["request_events"] += 1
        elif decoded["label"] == "settle":
            stats["settle_events"] += 1
        oracle = record["source_adapter"]
        if oracle:
            stats["discovered_oracles"][oracle] = stats["discovered_oracles"].get(oracle, 0) + 1

    writer.close()
    save_oracle_synced_block(db_path or "", to_block, sync_state_key=sync_state_key)
    print(
        f"[updown-oracle] scanned_logs={stats['logs_scanned']} matched={stats['matched_events']} "
        f"request={stats['request_events']} settle={stats['settle_events']}",
        file=sys.stderr,
    )
    if stats["discovered_oracles"]:
        top = sorted(stats["discovered_oracles"].items(), key=lambda item: item[1], reverse=True)[:10]
        print(f"[updown-oracle] discovered_oracles={top}", file=sys.stderr)
    return stats
