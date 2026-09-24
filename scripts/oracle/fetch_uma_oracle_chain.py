#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UMA Oracle 链上数据拉取（多 adapter + 多 oracle 版本并集）

通过 RPC (web3.py) 获取链上数据，解决 ancillaryData 与 Polymarket question_id 脱节问题：
1. 并集抓取多个 UmaCtfAdapter 的 QuestionInitialized
2. 并集抓取多个 UMA Oracle 地址上的 RequestPrice/ProposePrice/DisputePrice/Settle
3. 用 ancillaryData 构建本地 question_id 映射
4. 用数据库桥接到市场
5. 输出为 parquet/json
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from datetime import datetime, timezone
from hexbytes import HexBytes

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

try:
    from web3 import Web3
except ImportError:
    print("Error: web3 not installed. pip install web3", file=sys.stderr)
    sys.exit(1)

try:
    from web3.middleware import ExtraDataToPOAMiddleware
except ImportError:
    try:
        from web3.middleware import geth_poa_middleware as ExtraDataToPOAMiddleware
    except ImportError:
        ExtraDataToPOAMiddleware = None

from config import get_rpc_url
from db import (
    add_db_cli_args,
    configure_db_from_args,
    DEFAULT_DB_PATH,
    create_index_if_not_exists,
    describe_db_target,
    get_connection,
    get_table_columns,
    init_schema,
    is_postgres_backend,
)
from trade.rpc_utils import build_web3 as build_shared_web3

# 合约地址（Polygon）
DEFAULT_ORACLE_ADDRESSES = [
    "0x2C0367a9DB231dDeBd88a94b4f6461a6e47C58B1",  # OptimisticOracleV2 (older Polymarket deployment)
    "0xeE3Afe347D5C74317041E2618C49534dAf887c24",  # OptimisticOracleV2
]
DEFAULT_ADAPTER_ADDRESSES = [
    "0xCB1822859cEF82Cd2Eb4E6276C7916e692995130",  # UmaCtf Adapter V1
    "0x65070BE91477460D8A7AeEb94ef92fe056C2f2A7",  # UmaCtf Adapter (older OO deployment)
    "0x69c47De9D4D3Dad79590d61b9e05918E03775f24",  # UmaCtf Adapter (neg-risk / older OO deployment)
    "0xb21182d0494521Cf45DbbeEbb5A3ACAAb6d22093",  # UmaCtf Adapter (Polygon mainnet)
    "0x2F5e3684cb1F318ec51b00Edba38d79Ac2c0aA9d",  # UmaCtf Adapter V3
    "0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74",  # UmaCtf Adapter V2
    "0x157Ce2d672854c848c9b79C49a8Cc6cc89176a49",  # 文档中的 UmaCtfAdapter v3.0
]
DEFAULT_NEG_RISK_OPERATOR_ADDRESSES = [
    "0x661992aebf6BecF7BA5abB66f6b0Bf62Aa7a2E93",  # NegRiskOperator -> oracle 0x69c47...
    "0x71523d0f655B41E805Cec45b17163f528B59B820",  # NegRiskOperator -> oracle 0x2F5e...
]
UMA_ORACLE_ADDRESS = DEFAULT_ORACLE_ADDRESSES[0]
UMA_ADAPTER_ADDRESS = DEFAULT_ADAPTER_ADDRESSES[0]

EVENT_SIGNATURES = {
    "request": "RequestPrice(address,bytes32,uint256,bytes,address,uint256,uint256)",
    "propose": "ProposePrice(address,address,bytes32,uint256,bytes,int256,uint256,address)",
    "dispute": "DisputePrice(address,address,address,bytes32,uint256,bytes,int256)",
    "settle": "Settle(address,address,address,bytes32,uint256,bytes,int256,uint256)",
    "question_initialized": "QuestionInitialized(bytes32,uint256,address,bytes,address,uint256,uint256)",
    "neg_risk_question_prepared": "QuestionPrepared(bytes32,bytes32,bytes32,uint256,bytes)",
}

# Growth 套餐 250 RPS，步长 2000 + 多线程可大幅提速
BATCH_BLOCKS_ORACLE = 2000
BATCH_BLOCKS_ADAPTER = 2000
MAX_RETRIES = 5
RETRY_DELAY_BASE = 2
RPC_CONNECT_RETRIES = 3
RPC_CONNECT_RETRY_DELAY_SECONDS = 10
RPC_RECOVERY_SLEEP_BASE_SECONDS = 30
RPC_RECOVERY_SLEEP_MAX_SECONDS = 300
MAX_LOGS: Optional[int] = None
PARQUET_WRITE_BATCH = 5000
ORACLE_SYNC_STATE_KEY = "oracle_sync"
ORACLE_LIVE_SYNC_STATE_KEY = "oracle_sync_live"
ORACLE_UPDOWN_LIVE_SYNC_STATE_KEY = "oracle_updown_sync_live"
ADAPTER_FULL_HISTORY_START_BLOCK = 0
CONTINUE_SYNC_REWIND_BLOCKS = 500
CONTINUE_SYNC_MAX_BLOCKS = 200_000
WATCH_ERROR_BACKOFF_BASE_SECONDS = 30
WATCH_ERROR_BACKOFF_MAX_SECONDS = 300

POLYMARKET_DB = os.environ.get("POLYMARKET_DB", DEFAULT_DB_PATH)
ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


def _topic0(sig: str) -> str:
    return "0x" + Web3.keccak(text=sig).hex()


def _ancillary_to_utf8(data: bytes) -> str:
    """将 ancillaryData 完整解码为 utf-8，不截断"""
    if not data:
        return ""
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _ancillary_to_hex(data: bytes) -> str:
    """bytes -> 0x 开头的 hex 字符串，用于 SQLite 存储/查询"""
    if not data:
        return "0x"
    h = data.hex()
    return "0x" + h if not h.startswith("0x") else h


def _normalize_address(value: Any) -> str:
    if not value:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if not s.startswith("0x"):
        s = "0x" + s
    try:
        return Web3.to_checksum_address(s)
    except Exception:
        return s.lower()


def _parse_address_list(raw: Optional[str], defaults: List[str]) -> List[str]:
    if not raw:
        return [_normalize_address(x) for x in defaults]
    parts = [p.strip() for p in raw.split(",")]
    out: List[str] = []
    for p in parts:
        if not p:
            continue
        addr = _normalize_address(p)
        if addr and addr not in out:
            out.append(addr)
    return out


def _hex_to_bytes(s: str) -> bytes:
    if not s or s == "0x":
        return b""
    s = s.strip()
    if not s.startswith("0x"):
        s = "0x" + s
    try:
        return bytes.fromhex(s[2:])
    except Exception:
        return b""


def _parse_question_raw(raw: str) -> Tuple[str, str, str]:
    """从 question_raw_text 提取 description, p1, p2"""
    desc, p1_val, p2_val = "", "", ""
    if not raw:
        return desc, p1_val, p2_val
    try:
        m = re.search(r"(?s)description:\s*(.*?)(?:\s*market_id:|$)", raw)
        if m:
            desc = m.group(1).strip()
        m1 = re.search(r"p1:\s*([0-9]+)", raw)
        if m1:
            p1_val = m1.group(1)
        m2 = re.search(r"p2:\s*([0-9]+)", raw)
        if m2:
            p2_val = m2.group(1)
    except Exception:
        pass
    return desc, p1_val, p2_val


def _normalize_title(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _extract_market_id(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    m = re.search(r"market_id:\s*([0-9]+)", raw, re.IGNORECASE)
    return m.group(1) if m else ""


def _extract_title(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    m = re.search(r"title:\s*(.*?)(?:,\s*description:|$)", raw, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _parse_any_datetime(value: str) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.000 UTC",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    return None


def _format_rpc_error(exc: Exception) -> str:
    msg = str(exc).strip()
    return msg or exc.__class__.__name__


def _call_with_retries(stage: str, func, max_retries: int = MAX_RETRIES):
    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries or not _is_transient_rpc_error(exc):
                raise
            delay = RETRY_DELAY_BASE ** attempt
            print(
                f"[oracle] {stage} failed ({attempt}/{max_retries}): {_format_rpc_error(exc)}. "
                f"Retrying in {delay}s...",
                file=sys.stderr,
            )
            time.sleep(delay)
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{stage} failed without exception")


def _build_web3(rpc_url: str) -> Web3:
    return build_shared_web3(
        rpc_url,
        timeout_seconds=60,
        connect_retries=RPC_CONNECT_RETRIES,
        connect_retry_delay_seconds=RPC_CONNECT_RETRY_DELAY_SECONDS,
    )


def _compute_watch_error_backoff(interval_seconds: int, consecutive_failures: int) -> int:
    base = max(WATCH_ERROR_BACKOFF_BASE_SECONDS, int(interval_seconds))
    backoff = base * (2 ** max(0, consecutive_failures - 1))
    return min(backoff, WATCH_ERROR_BACKOFF_MAX_SECONDS)


def _is_transient_rpc_error(exc: Exception) -> bool:
    msg = _format_rpc_error(exc).lower()
    return any(
        keyword in msg
        for keyword in (
            "cannot connect to rpc",
            "connection aborted",
            "connection reset",
            "remote disconnected",
            "temporarily unavailable",
            "timed out",
            "read timed out",
            "max retries exceeded",
            "503",
            "502",
            "504",
            "429",
            "network",
            "header not found",
        )
    )


def _sleep_for_rpc_recovery(stage: str, attempt: int, exc: Exception) -> None:
    delay = min(
        RPC_RECOVERY_SLEEP_BASE_SECONDS * (2 ** max(0, attempt - 1)),
        RPC_RECOVERY_SLEEP_MAX_SECONDS,
    )
    print(
        f"[oracle] {stage} failed due to transient RPC/network issue: {_format_rpc_error(exc)}. "
        f"Sleeping {delay}s before retry...",
        file=sys.stderr,
    )
    time.sleep(delay)


def _should_split_range(exc: Exception, from_block: int, to_block: int) -> bool:
    if from_block >= to_block:
        return False
    msg = _format_rpc_error(exc).lower()
    return any(
        keyword in msg
        for keyword in (
            "unterminated string",
            "json",
            "response ended",
            "expecting value",
            "413",
            "payload",
            "too large",
            "timed out",
            "read timed out",
            "query returned more than",
            "limit exceeded",
        )
    )


def _connect_db_readonly(db_path: str):
    return get_connection(db_path, readonly=True)


def _db_target_available(db_path: str) -> bool:
    if is_postgres_backend():
        return True
    return Path(db_path).exists()


def _table_has_column(conn, table: str, column: str) -> bool:
    return column in set(get_table_columns(conn, table))


def _get_earliest_market_created_at(db_path: str) -> Optional[str]:
    conn = _connect_db_readonly(db_path)
    try:
        cur = conn.execute(
            "SELECT MIN(created_at) FROM markets WHERE created_at IS NOT NULL AND TRIM(CAST(created_at AS TEXT)) != ''"
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else None
    finally:
        conn.close()


def get_last_oracle_synced_block(
    db_path: str,
    sync_state_key: str = ORACLE_SYNC_STATE_KEY,
) -> Optional[int]:
    conn = _connect_db_readonly(db_path)
    try:
        cur = conn.execute(
            "SELECT last_block FROM sync_state WHERE key = ?",
            (sync_state_key,),
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None
    finally:
        conn.close()


def save_oracle_synced_block(
    db_path: str,
    last_block: int,
    sync_state_key: str = ORACLE_SYNC_STATE_KEY,
) -> None:
    conn = _connect_sqlite_write(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO sync_state (key, value, last_block, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                sync_state_key,
                str(last_block),
                int(last_block),
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def save_oracle_live_state(
    db_path: str,
    *,
    status: str,
    phase: str,
    from_block: Optional[int] = None,
    to_block: Optional[int] = None,
    progress_block: Optional[int] = None,
    logs_scanned: Optional[int] = None,
    records_written: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    payload = {
        "status": status,
        "phase": phase,
        "fromBlock": from_block,
        "toBlock": to_block,
        "progressBlock": progress_block,
        "logsScanned": logs_scanned,
        "recordsWritten": records_written,
        "error": error,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    live_block = progress_block if progress_block is not None else from_block
    if live_block is None:
        live_block = to_block or 0
    conn = _connect_sqlite_write(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO sync_state (key, value, last_block, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                ORACLE_LIVE_SYNC_STATE_KEY,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                int(live_block or 0),
                payload["updatedAt"],
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"[oracle] save live sync state failed: {_format_rpc_error(exc)}", file=sys.stderr)
    finally:
        conn.close()


def _select_market_by_title(
    candidates: List[Dict[str, Any]], event_time: str
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    用标题桥接 DB。
    - 只有一个候选时直接采用
    - 重复标题时按 end_date 与 event_time 的接近程度消歧
    """
    if not candidates:
        return None, ""
    if len(candidates) == 1:
        return candidates[0], "by_title"

    event_dt = _parse_any_datetime(event_time)
    if event_dt is None:
        return None, ""

    scored: List[Tuple[float, float, Dict[str, Any]]] = []
    for candidate in candidates:
        end_dt = _parse_any_datetime(candidate.get("end_date", ""))
        created_dt = _parse_any_datetime(candidate.get("created_at", ""))
        end_diff = abs((end_dt - event_dt).total_seconds()) if end_dt else float("inf")
        created_diff = abs((created_dt - event_dt).total_seconds()) if created_dt else float("inf")
        scored.append((end_diff, created_diff, candidate))

    scored.sort(key=lambda item: (item[0], item[1]))
    best_end_diff, _, best_candidate = scored[0]
    # 超过 30 天仍然无法可靠消歧，则放弃自动匹配
    if best_end_diff > 30 * 24 * 3600:
        return None, ""
    return best_candidate, "by_title_nearest_date"


def _load_market_bridge_indices(db_path: str) -> Dict[str, Any]:
    """
    从 markets 表构建 Oracle -> DB 的桥接索引。
    优先使用标题 + 日期消歧，最后再尝试 question_id。
    """
    conn = _connect_db_readonly(db_path)
    try:
        has_gamma_market_id = _table_has_column(conn, "markets", "gamma_market_id")
        cur = conn.execute(
            f"""
            SELECT
                id AS market_id,
                {"gamma_market_id" if has_gamma_market_id else "'' AS gamma_market_id"},
                slug,
                title,
                question_id,
                condition_id,
                created_at,
                end_date
            FROM markets
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    by_gamma_market_id: Dict[str, Dict[str, Any]] = {}
    by_question_id: Dict[str, Dict[str, Any]] = {}
    by_condition_id: Dict[str, Dict[str, Any]] = {}
    title_buckets: Dict[str, List[Dict[str, Any]]] = {}

    for market_id, gamma_market_id, slug, title, question_id, condition_id, created_at, end_date in rows:
        row = {
            "market_id": market_id,
            "gamma_market_id": str(gamma_market_id or "").strip(),
            "slug": slug or "",
            "title": title or "",
            "question_id": question_id or "",
            "condition_id": condition_id or "",
            "created_at": created_at or "",
            "end_date": end_date or "",
        }
        if row["gamma_market_id"]:
            by_gamma_market_id[row["gamma_market_id"]] = row
        qid = str(question_id or "").strip().lower()
        if qid:
            by_question_id[qid] = row
        cid = str(condition_id or "").strip().lower()
        if cid:
            by_condition_id[cid] = row
        norm_title = _normalize_title(title or "")
        if norm_title:
            title_buckets.setdefault(norm_title, []).append(row)

    return {
        "by_gamma_market_id": by_gamma_market_id,
        "by_question_id": by_question_id,
        "by_condition_id": by_condition_id,
        "by_title_buckets": title_buckets,
    }


def _parse_date_to_timestamp(s: str) -> int:
    s = (s or "").strip()
    if not s:
        raise ValueError("日期不能为空")
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {s}")


def _block_at_timestamp(w3: Web3, target_ts: int, high: Optional[int] = None) -> int:
    raise RuntimeError(
        "Date/timestamp-to-block conversion is disabled. Pass explicit "
        "--oracle-start-block/--adapter-start-block/--end-block instead."
    )


def _address_has_code_at_block(w3: Web3, address: str, block_number: int) -> bool:
    try:
        code = _call_with_retries(
            f"eth_getCode({address}@{block_number})",
            lambda: w3.eth.get_code(Web3.to_checksum_address(address), block_identifier=block_number),
        )
        return bool(code and code != b"" and code != HexBytes("0x"))
    except Exception:
        return False


def _find_contract_deployment_block(
    w3: Web3,
    address: str,
    *,
    high: Optional[int] = None,
) -> Optional[int]:
    if high is None:
        high = int(_call_with_retries("eth_blockNumber", lambda: w3.eth.block_number))
    checksum_address = Web3.to_checksum_address(address)
    if not _address_has_code_at_block(w3, checksum_address, high):
        return None
    low = 0
    result = high
    while low <= high:
        mid = (low + high) // 2
        if _address_has_code_at_block(w3, checksum_address, mid):
            result = mid
            high = mid - 1
        else:
            low = mid + 1
    return result


def _resolve_contract_family_start_block(
    w3: Web3,
    adapter_addresses: List[str],
    oracle_addresses: List[str],
    neg_risk_operator_addresses: List[str],
    *,
    high: Optional[int] = None,
) -> Optional[int]:
    addresses = []
    for raw in adapter_addresses + oracle_addresses + neg_risk_operator_addresses:
        address = _normalize_address(raw)
        if address and address not in addresses:
            addresses.append(address)
    earliest: Optional[int] = None
    for address in addresses:
        deployment_block = _find_contract_deployment_block(w3, address, high=high)
        if deployment_block is None:
            continue
        if earliest is None or deployment_block < earliest:
            earliest = deployment_block
    return earliest


def resolve_auto_start_block(
    w3: Web3,
    db_path: str,
    end_block: int,
    adapter_addresses: List[str],
    oracle_addresses: List[str],
    neg_risk_operator_addresses: List[str],
) -> int:
    start_candidates: List[int] = []
    # Do not infer a start block from market.created_at. That requires chain
    # timestamp binary search via eth_getBlockByNumber and can unexpectedly
    # consume RPC/proxy traffic on service restarts.
    contract_start_block = _resolve_contract_family_start_block(
        w3,
        adapter_addresses,
        oracle_addresses,
        neg_risk_operator_addresses,
        high=end_block,
    )
    if contract_start_block is not None:
        start_candidates.append(contract_start_block)
        print(
            f"[oracle] earliest configured oracle/adapter/operator deployment block={contract_start_block}",
            file=sys.stderr,
        )
    if not start_candidates:
        return max(0, end_block - 500_000)
    resolved = max(start_candidates)
    print(f"[oracle] auto-selected start block={resolved}", file=sys.stderr)
    return resolved


def _block_ts(w3: Web3, block_number: int, cache: Optional[Dict[int, str]] = None) -> str:
    """Return no event_time without issuing per-block timestamp RPC calls.

    Oracle rows keep block_number/log_index/tx_hash as the canonical chain
    ordering. Hydrating block timestamps requires eth_getBlockByNumber per
    unique block and is intentionally disabled for live/backfill sync traffic.
    """
    if cache is not None:
        cache.setdefault(block_number, "")
    return ""


def fetch_logs_with_retry(
    w3: Web3,
    from_block: int,
    to_block: int,
    address: str,
    topics: List[str],
    batch_blocks: int = BATCH_BLOCKS_ORACLE,
    max_logs: Optional[int] = None,
    max_workers: int = 30,
    progress_callback: ProgressCallback = None,
    progress_label: str = "logs",
) -> List[Dict]:
    limit = max_logs if max_logs is not None else MAX_LOGS

    def _fetch_single_range(start_b: int, end_b: int) -> List[Dict]:
        recovery_attempt = 0
        while True:
            last_err: Optional[Exception] = None
            for attempt in range(MAX_RETRIES):
                try:
                    batch = w3.eth.get_logs(
                        {
                            "address": Web3.to_checksum_address(address),
                            "fromBlock": start_b,
                            "toBlock": end_b,
                            "topics": [topics],
                        }
                    )
                    return [dict(l) for l in batch]
                except Exception as e:
                    last_err = e
                    delay = RETRY_DELAY_BASE ** (attempt + 1)
                    print(
                        f"get_logs failed blocks {start_b}-{end_b} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES}): {_format_rpc_error(e)}, retry in {delay}s",
                        file=sys.stderr,
                    )
                    time.sleep(delay)

            if last_err is not None and _should_split_range(last_err, start_b, end_b):
                mid = (start_b + end_b) // 2
                print(
                    f"get_logs failed repeatedly for blocks {start_b}-{end_b}; "
                    f"split into {start_b}-{mid} and {mid + 1}-{end_b}",
                    file=sys.stderr,
                )
                left = _fetch_single_range(start_b, mid)
                right = _fetch_single_range(mid + 1, end_b)
                return left + right

            if last_err is not None and _is_transient_rpc_error(last_err):
                recovery_attempt += 1
                delay = min(RETRY_DELAY_BASE ** min(recovery_attempt, 6), 60)
                print(
                    f"get_logs transient failure blocks {start_b}-{end_b}: {_format_rpc_error(last_err)}. "
                    f"Sleeping {delay}s before retrying whole range...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue

            raise RuntimeError(
                f"Failed to fetch logs for blocks {start_b}-{end_b} on {address}: "
                f"{_format_rpc_error(last_err or RuntimeError('unknown error'))}"
            )

    ranges = []
    current = from_block
    while current <= to_block:
        end = min(current + batch_blocks - 1, to_block)
        ranges.append((current, end))
        current = end + 1

    total_tasks = len(ranges)
    print(
        f"  ... 任务已切分为 {total_tasks} 个并发批次 (每批 {batch_blocks} 块)，启动 {max_workers} 个工作线程...",
        file=sys.stderr,
    )

    logs: List[Dict] = []
    completed_tasks = 0
    completed_range_ends: List[int] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_range = {executor.submit(_fetch_single_range, r[0], r[1]): r for r in ranges}
        for future in as_completed(future_to_range):
            _, range_end = future_to_range[future]
            res = future.result()
            if res:
                logs.extend(res)
            completed_tasks += 1
            completed_range_ends.append(int(range_end))
            if completed_tasks % max(1, total_tasks // 20) == 0 or completed_tasks == total_tasks:
                progress = (completed_tasks / total_tasks) * 100
                progress_block = max(completed_range_ends, default=from_block)
                print(
                    f"  ---> 抓取进度: {completed_tasks}/{total_tasks} 批次 ({progress:.1f}%) | 累计日志: {len(logs)} 条",
                    file=sys.stderr,
                )
                if progress_callback:
                    progress_callback(
                        {
                            "label": progress_label,
                            "address": address,
                            "fromBlock": from_block,
                            "toBlock": to_block,
                            "progressBlock": min(progress_block, to_block),
                            "completedTasks": completed_tasks,
                            "totalTasks": total_tasks,
                            "logsScanned": len(logs),
                        }
                    )

    logs.sort(key=lambda x: (x.get("blockNumber", 0), x.get("logIndex", 0)))
    return logs[:limit] if limit is not None else logs


def fetch_logs_many_addresses(
    w3: Web3,
    from_block: int,
    to_block: int,
    addresses: List[str],
    topics: List[str],
    batch_blocks: int,
    max_logs: Optional[int] = None,
    max_workers: int = 30,
    label: str = "logs",
    progress_callback: ProgressCallback = None,
) -> List[Dict]:
    all_logs: List[Dict] = []
    seen: set[Tuple[str, int, int, str]] = set()
    print(
        f"Fetching {label} from {len(addresses)} address(es): {', '.join(addresses)}",
        file=sys.stderr,
    )
    for idx, address in enumerate(addresses, start=1):
        print(
            f"  [{idx}/{len(addresses)}] address={address} block={from_block}-{to_block}",
            file=sys.stderr,
        )
        logs = fetch_logs_with_retry(
            w3,
            from_block,
            to_block,
            address,
            topics,
            batch_blocks=batch_blocks,
            max_logs=max_logs,
            max_workers=max_workers,
            progress_callback=progress_callback,
            progress_label=label,
        )
        for log in logs:
            tx_hash = _ensure_0x(log.get("transactionHash") or "")
            block_number = int(log.get("blockNumber", 0) or 0)
            log_index = int(log.get("logIndex", 0) or 0)
            addr = _normalize_address(log.get("address") or address).lower()
            key = (tx_hash.lower(), block_number, log_index, addr)
            if key in seen:
                continue
            seen.add(key)
            log["_source_address"] = addr
            all_logs.append(log)
    all_logs.sort(
        key=lambda x: (
            int(x.get("blockNumber", 0) or 0),
            int(x.get("logIndex", 0) or 0),
            str(x.get("_source_address") or x.get("address") or ""),
        )
    )
    if max_logs is not None:
        return all_logs[:max_logs]
    return all_logs


# ===================== Step 1: 初始化 SQLite 映射表 =====================
def init_uma_adapter_mapping(conn) -> None:
    init_schema(conn=conn)
    if is_postgres_backend():
        # The migrated PostgreSQL oracle.uma_adapter_mapping table uses an id
        # primary key plus a generated ancillary_data_hash unique key. Runtime
        # live sync appends new rows, so id needs a default sequence and the
        # upsert must target the generated hash constraint rather than the
        # unindexed TEXT column.
        conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        conn.execute("CREATE SEQUENCE IF NOT EXISTS oracle.uma_adapter_mapping_id_seq")
        conn.execute(
            """
            ALTER TABLE oracle.uma_adapter_mapping
            ALTER COLUMN id SET DEFAULT nextval('oracle.uma_adapter_mapping_id_seq')
            """
        )
        conn.execute(
            """
            SELECT setval(
                'oracle.uma_adapter_mapping_id_seq',
                GREATEST(COALESCE((SELECT MAX(id) FROM oracle.uma_adapter_mapping), 0), 1),
                true
            )
            """
        )
    conn.commit()


# ===================== Step 2: 增量同步适配器字典 =====================
def _decode_question_initialized(w3: Web3, log: Dict) -> Optional[Tuple[str, str, str]]:
    """返回 (ancillary_hex, question_id_hex, source_adapter)"""
    abi = {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "questionID", "type": "bytes32"},
            {"indexed": True, "name": "requestTimestamp", "type": "uint256"},
            {"indexed": True, "name": "creator", "type": "address"},
            {"indexed": False, "name": "ancillaryData", "type": "bytes"},
            {"indexed": False, "name": "rewardToken", "type": "address"},
            {"indexed": False, "name": "reward", "type": "uint256"},
            {"indexed": False, "name": "proposalBond", "type": "uint256"},
        ],
        "name": "QuestionInitialized",
        "type": "event",
    }
    try:
        contract = w3.eth.contract(abi=[abi])
        decoded = contract.events.QuestionInitialized().process_log(log)
        args = decoded["args"]
        ad = args.get("ancillaryData") or b""
        qid = args.get("questionID")
        if qid is None:
            return None
        qid_hex = qid.hex() if hasattr(qid, "hex") else str(qid)
        if not qid_hex.startswith("0x"):
            qid_hex = "0x" + qid_hex
        source_adapter = str(log.get("_source_address") or log.get("address") or "").lower()
        return (_ancillary_to_hex(ad), qid_hex[:66], source_adapter)
    except Exception as e:
        print(f"Decode QuestionInitialized error: {e}", file=sys.stderr)
        return None


def sync_adapter_mapping(
    w3: Web3,
    conn,
    adapter_start_block: int,
    end_block: int,
    batch_blocks: int = BATCH_BLOCKS_ADAPTER,
    max_workers: int = 30,
) -> int:
    """增量同步 QuestionInitialized 到 uma_adapter_mapping，返回插入条数"""
    topic0 = _topic0(EVENT_SIGNATURES["question_initialized"])
    print(f"Sync QuestionInitialized {adapter_start_block}-{end_block} (batch={batch_blocks})...", file=sys.stderr)
    logs = fetch_logs_with_retry(
        w3, adapter_start_block, end_block,
        Web3.to_checksum_address(UMA_ADAPTER_ADDRESS),
        [topic0],
        batch_blocks=batch_blocks,
        max_logs=MAX_LOGS,
        max_workers=max_workers,
    )
    inserted = 0
    for log in logs:
        res = _decode_question_initialized(w3, log)
        if res:
            ancillary_hex, question_id, source_adapter = res
            try:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO uma_adapter_mapping (ancillary_data, question_id, source_adapter)
                    VALUES (?, ?, ?)
                    """,
                    (ancillary_hex, question_id, source_adapter),
                )
                if cur.rowcount > 0:
                    inserted += 1
            except Exception as e:
                print(f"INSERT error: {e}", file=sys.stderr)
    conn.commit()
    cur = conn.execute("SELECT COUNT(*) FROM uma_adapter_mapping")
    total = cur.fetchone()[0]
    print(f"  新增 {inserted} 条，映射表共 {total} 条", file=sys.stderr)
    return inserted


def load_adapter_mapping(conn) -> Tuple[Dict[str, str], Dict[str, str]]:
    """加载 ancillary_data (hex) -> question_id/source_adapter 映射"""
    cur = conn.execute("SELECT ancillary_data, question_id, source_adapter FROM uma_adapter_mapping")
    question_map: Dict[str, str] = {}
    source_map: Dict[str, str] = {}
    for row in cur.fetchall():
        key = row[0].lower() if row[0] else ""
        if not key:
            continue
        question_map[key] = row[1] or ""
        source_map[key] = (row[2] or "").lower()
    return question_map, source_map


def save_adapter_mapping_entries(
    conn,
    mapping: Dict[str, str],
    mapping_source: Optional[Dict[str, str]] = None,
) -> int:
    if not mapping and not mapping_source:
        return 0
    before = conn.total_changes
    rows = []
    for key, question_id in mapping.items():
        if not key or not question_id:
            continue
        rows.append((key.lower(), question_id, (mapping_source or {}).get(key, "")))
    conflict_target = "ancillary_data_hash" if is_postgres_backend() else "ancillary_data"
    conn.executemany(
        f"""
        INSERT INTO uma_adapter_mapping (ancillary_data, question_id, source_adapter)
        VALUES (?, ?, ?)
        ON CONFLICT({conflict_target}) DO UPDATE SET
            question_id=excluded.question_id,
            source_adapter=COALESCE(NULLIF(TRIM(COALESCE(excluded.source_adapter,'')), ''), uma_adapter_mapping.source_adapter)
        """,
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def _decode_neg_risk_question_prepared(
    w3: Web3,
    log: Dict,
) -> Optional[Tuple[str, str, str, str]]:
    """返回 (request_id, question_id, market_id, source_operator)"""
    abi = {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "marketId", "type": "bytes32"},
            {"indexed": True, "name": "questionId", "type": "bytes32"},
            {"indexed": True, "name": "requestId", "type": "bytes32"},
            {"indexed": False, "name": "index", "type": "uint256"},
            {"indexed": False, "name": "data", "type": "bytes"},
        ],
        "name": "QuestionPrepared",
        "type": "event",
    }
    try:
        contract = w3.eth.contract(abi=[abi])
        decoded = contract.events.QuestionPrepared().process_log(log)
        args = decoded["args"]
        request_id = _ensure_0x(args.get("requestId") or "")[:66].lower()
        question_id = _ensure_0x(args.get("questionId") or "")[:66].lower()
        market_id = _ensure_0x(args.get("marketId") or "")[:66].lower()
        source_operator = str(log.get("_source_address") or log.get("address") or "").lower()
        if not request_id or request_id == "0x":
            return None
        return request_id, question_id, market_id, source_operator
    except Exception as e:
        print(f"Decode NegRisk QuestionPrepared error: {e}", file=sys.stderr)
        return None


def load_neg_risk_request_mapping(conn) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """加载 neg-risk request_id -> question_id/market_id/source_operator 映射"""
    cur = conn.execute(
        "SELECT request_id, question_id, market_id, source_operator FROM neg_risk_request_mapping"
    )
    question_map: Dict[str, str] = {}
    market_map: Dict[str, str] = {}
    operator_map: Dict[str, str] = {}
    for row in cur.fetchall():
        request_id = str(row[0] or "").strip().lower()
        if not request_id:
            continue
        question_map[request_id] = str(row[1] or "").strip().lower()
        market_map[request_id] = str(row[2] or "").strip().lower()
        operator_map[request_id] = str(row[3] or "").strip().lower()
    return question_map, market_map, operator_map


def save_neg_risk_request_mapping_entries(
    conn,
    request_question_map: Dict[str, str],
    request_market_map: Optional[Dict[str, str]] = None,
    request_operator_map: Optional[Dict[str, str]] = None,
) -> int:
    if not request_question_map:
        return 0
    before = conn.total_changes
    rows = []
    for request_id, question_id in request_question_map.items():
        request_id_norm = str(request_id or "").strip().lower()
        question_id_norm = str(question_id or "").strip().lower()
        if not request_id_norm or not question_id_norm:
            continue
        rows.append(
            (
                request_id_norm,
                question_id_norm,
                str((request_market_map or {}).get(request_id, "") or "").strip().lower(),
                str((request_operator_map or {}).get(request_id, "") or "").strip().lower(),
            )
        )
    conn.executemany(
        """
        INSERT INTO neg_risk_request_mapping (request_id, question_id, market_id, source_operator)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(request_id) DO UPDATE SET
            question_id=excluded.question_id,
            market_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.market_id,'')), ''), neg_risk_request_mapping.market_id),
            source_operator=COALESCE(NULLIF(TRIM(COALESCE(excluded.source_operator,'')), ''), neg_risk_request_mapping.source_operator)
        """,
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def build_neg_risk_request_mapping(
    w3: Web3,
    start_block: int,
    end_block: int,
    operator_addresses: List[str],
    batch_blocks: int = BATCH_BLOCKS_ADAPTER,
    max_workers: int = 30,
    progress_callback: ProgressCallback = None,
) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """在内存中构建 request_id -> question_id/market_id/source_operator 映射。"""
    if not operator_addresses:
        return {}, {}, {}
    topic0 = _topic0(EVENT_SIGNATURES["neg_risk_question_prepared"])
    logs = fetch_logs_many_addresses(
        w3,
        start_block,
        end_block,
        operator_addresses,
        [topic0],
        batch_blocks=batch_blocks,
        max_logs=MAX_LOGS,
        max_workers=max_workers,
        label="NegRisk QuestionPrepared",
        progress_callback=progress_callback,
    )
    request_question_map: Dict[str, str] = {}
    request_market_map: Dict[str, str] = {}
    request_operator_map: Dict[str, str] = {}
    conflict_count = 0
    for log in logs:
        decoded = _decode_neg_risk_question_prepared(w3, log)
        if not decoded:
            continue
        request_id, question_id, market_id, source_operator = decoded
        old_question_id = request_question_map.get(request_id)
        if old_question_id and old_question_id != question_id:
            conflict_count += 1
        request_question_map[request_id] = question_id
        request_market_map[request_id] = market_id
        request_operator_map[request_id] = source_operator
    print(
        f"  Neg-risk request 映射条数: {len(request_question_map)}"
        + (f" | request_id 冲突覆盖: {conflict_count}" if conflict_count else ""),
        file=sys.stderr,
    )
    return request_question_map, request_market_map, request_operator_map


def _connect_sqlite_write(db_path: str):
    init_schema(db_path=db_path)
    return get_connection(db_path)


def ensure_oracle_events_table(conn) -> None:
    init_schema(conn=conn)
    if is_postgres_backend():
        conn.execute("CREATE SEQUENCE IF NOT EXISTS oracle.oracle_events_id_seq")
        conn.execute(
            """
            ALTER TABLE oracle.oracle_events
            ALTER COLUMN id SET DEFAULT nextval('oracle.oracle_events_id_seq')
            """
        )
        conn.execute(
            """
            SELECT setval(
                'oracle.oracle_events_id_seq',
                GREATEST(COALESCE((SELECT MAX(id) FROM oracle.oracle_events), 0), 1),
                true
            )
            """
        )
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_market_id", ["market_id"])
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_external_market_id", ["external_market_id"])
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_question_id", ["question_id"])
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_condition_id", ["condition_id"])
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_market_block_id", ["market_id", "block_number", "id"])
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_external_market_block_id", ["external_market_id", "block_number", "id"])
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_question_block_id", ["question_id", "block_number", "id"])
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_condition_block_id", ["condition_id", "block_number", "id"])
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_block", ["block_number"])
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_status", ["event_status"])
    create_index_if_not_exists(conn, "oracle_events", "idx_oracle_events_matched_by", ["matched_by"])
    conn.commit()


class _OracleDbWriter:
    def __init__(self, db_path: str, batch_size: int = 1000) -> None:
        self.conn = _connect_sqlite_write(db_path)
        ensure_oracle_events_table(self.conn)
        self.batch_size = batch_size
        self.total = 0
        self._buffer: List[Dict[str, Any]] = []

    def write(self, record: Dict[str, Any]) -> None:
        self.total += 1
        self._buffer.append(record)
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        self.conn.executemany(
            """
            INSERT INTO oracle_events (
                tx_hash, log_index, block_number, event_time, event_status,
                external_market_id, market_id, market_title, source_adapter, source_oracle,
                adapter_question_id, matched_by, question_id, condition_id, string_raw,
                p1, p2, proposed_price, settled_price, settlement_recipient, payout,
                requester, proposer, disputer, request_transaction, proposal_transaction,
                settlement_transaction
            ) VALUES (
                :tx_hash, :log_index, :block_number, :event_time, :event_status,
                :external_market_id, :market_id, :market_title, :source_adapter, :source_oracle,
                :adapter_question_id, :matched_by, :question_id, :condition_id, :string_raw,
                :p1, :p2, :proposed_price, :settled_price, :settlement_recipient, :payout,
                :requester, :proposer, :disputer, :request_transaction, :proposal_transaction,
                :settlement_transaction
            )
            ON CONFLICT(tx_hash, log_index) DO UPDATE SET
                block_number=excluded.block_number,
                event_time=excluded.event_time,
                event_status=excluded.event_status,
                external_market_id=excluded.external_market_id,
                market_id=excluded.market_id,
                market_title=excluded.market_title,
                source_adapter=excluded.source_adapter,
                source_oracle=excluded.source_oracle,
                adapter_question_id=excluded.adapter_question_id,
                matched_by=excluded.matched_by,
                question_id=excluded.question_id,
                condition_id=excluded.condition_id,
                string_raw=excluded.string_raw,
                p1=excluded.p1,
                p2=excluded.p2,
                proposed_price=excluded.proposed_price,
                settled_price=excluded.settled_price,
                settlement_recipient=excluded.settlement_recipient,
                payout=excluded.payout,
                requester=excluded.requester,
                proposer=excluded.proposer,
                disputer=excluded.disputer,
                request_transaction=excluded.request_transaction,
                proposal_transaction=excluded.proposal_transaction,
                settlement_transaction=excluded.settlement_transaction
            """,
            self._buffer,
        )
        self.conn.commit()
        self._buffer.clear()

    def close(self) -> None:
        self.flush()
        self.conn.close()


def build_adapter_mapping(
    w3: Web3,
    adapter_start_block: int,
    end_block: int,
    adapter_addresses: List[str],
    batch_blocks: int = BATCH_BLOCKS_ADAPTER,
    max_workers: int = 30,
    progress_callback: ProgressCallback = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """仅在内存中构建 ancillary_data (hex) -> question_id 映射，不写数据库。"""
    topic0 = _topic0(EVENT_SIGNATURES["question_initialized"])
    logs = fetch_logs_many_addresses(
        w3,
        adapter_start_block,
        end_block,
        adapter_addresses,
        [topic0],
        batch_blocks=batch_blocks,
        max_logs=MAX_LOGS,
        max_workers=max_workers,
        label="QuestionInitialized",
        progress_callback=progress_callback,
    )
    mapping: Dict[str, str] = {}
    mapping_source: Dict[str, str] = {}
    conflict_count = 0
    for log in logs:
        res = _decode_question_initialized(w3, log)
        if not res:
            continue
        ancillary_hex, question_id, source_adapter = res
        key = (ancillary_hex or "").lower()
        if key and question_id:
            old_qid = mapping.get(key)
            if old_qid and old_qid != question_id:
                conflict_count += 1
            mapping[key] = question_id
            mapping_source[key] = source_adapter
    print(
        f"  内存映射条数: {len(mapping)}"
        + (f" | ancillary 冲突覆盖: {conflict_count}" if conflict_count else ""),
        file=sys.stderr,
    )
    return mapping, mapping_source


class _RecordWriter:
    def __init__(self, out: str, parquet_batch_size: int = PARQUET_WRITE_BATCH) -> None:
        self.out_path = Path(out)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.suffix = self.out_path.suffix.lower()
        self.parquet_batch_size = parquet_batch_size
        self.total = 0
        self._buffer: List[Dict[str, Any]] = []
        self._json_records: List[Dict[str, Any]] = []
        self._parquet_writer = None
        self._pa = None
        self._pq = None

        if self.suffix == ".parquet":
            import pyarrow as pa
            import pyarrow.parquet as pq

            self._pa = pa
            self._pq = pq

    def write(self, record: Dict[str, Any]) -> None:
        self.total += 1
        if self.suffix == ".parquet":
            self._buffer.append(record)
            if len(self._buffer) >= self.parquet_batch_size:
                self._flush_parquet()
            return
        self._json_records.append(record)

    def _flush_parquet(self) -> None:
        if not self._buffer:
            return
        table = self._pa.Table.from_pylist(self._buffer)
        if self._parquet_writer is None:
            self._parquet_writer = self._pq.ParquetWriter(
                self.out_path,
                table.schema,
                compression="snappy",
            )
        self._parquet_writer.write_table(table)
        self._buffer.clear()

    def close(self) -> None:
        if self.suffix == ".parquet":
            self._flush_parquet()
            if self._parquet_writer is not None:
                self._parquet_writer.close()
            return

        with open(self.out_path, "w", encoding="utf-8") as f:
            json.dump(
                {"source": "UMA Optimistic Oracle (chain)", "records": self._json_records},
                f,
                ensure_ascii=False,
                indent=2,
            )


# ===================== Step 3 & 4 & 5: Oracle 抓取 + 字典合并 + 输出 =====================
def decode_request_price(log: Dict, w3: Web3, block_ts_cache: Dict[int, str]) -> Optional[Dict]:
    abi = {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "requester", "type": "address"},
            {"indexed": False, "name": "identifier", "type": "bytes32"},
            {"indexed": False, "name": "timestamp", "type": "uint256"},
            {"indexed": False, "name": "ancillaryData", "type": "bytes"},
            {"indexed": False, "name": "currency", "type": "address"},
            {"indexed": False, "name": "reward", "type": "uint256"},
            {"indexed": False, "name": "finalFee", "type": "uint256"},
        ],
        "name": "RequestPrice",
        "type": "event",
    }
    try:
        decoded = w3.eth.contract(abi=[abi]).events.RequestPrice().process_log(log)
        args = decoded["args"]
        ad = args.get("ancillaryData") or b""
        raw = _ancillary_to_utf8(ad)
        desc, p1, p2 = _parse_question_raw(raw)
        tx = log["transactionHash"].hex() if hasattr(log["transactionHash"], "hex") else log["transactionHash"]
        return {
            "block_number": log["blockNumber"],
            "log_index": int(log.get("logIndex", 0) or 0),
            "event_time": _block_ts(w3, log["blockNumber"], block_ts_cache),
            "tx_hash": tx,
            "source_oracle": str(log.get("_source_address") or log.get("address") or "").lower(),
            "label": "request",
            "identifier": args.get("identifier"),
            "timestamp": args.get("timestamp"),
            "ancillaryData": ad,
            "ancillary_hex": _ancillary_to_hex(ad),
            "ancillary_raw": raw,
            # RequestPrice.requester is often the adapter contract; prefer tx sender later.
            "requester": None,
            "proposer": None,
            "disputer": None,
            "settlement_recipient": None,
            "proposedPrice": None,
            "price": None,
            "payout": None,
            "description": desc, "p1": p1, "p2": p2,
            "request_tx": tx, "proposal_tx": None, "settlement_tx": None,
        }
    except Exception as e:
        print(f"Decode RequestPrice error: {e}", file=sys.stderr)
        return None


def decode_propose_price(log: Dict, w3: Web3, block_ts_cache: Dict[int, str]) -> Optional[Dict]:
    abi = {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "requester", "type": "address"},
            {"indexed": True, "name": "proposer", "type": "address"},
            {"indexed": False, "name": "identifier", "type": "bytes32"},
            {"indexed": False, "name": "timestamp", "type": "uint256"},
            {"indexed": False, "name": "ancillaryData", "type": "bytes"},
            {"indexed": False, "name": "proposedPrice", "type": "int256"},
            {"indexed": False, "name": "expirationTimestamp", "type": "uint256"},
            {"indexed": False, "name": "currency", "type": "address"},
        ],
        "name": "ProposePrice",
        "type": "event",
    }
    try:
        decoded = w3.eth.contract(abi=[abi]).events.ProposePrice().process_log(log)
        args = decoded["args"]
        ad = args.get("ancillaryData") or b""
        raw = _ancillary_to_utf8(ad)
        desc, p1, p2 = _parse_question_raw(raw)
        tx = log["transactionHash"].hex() if hasattr(log["transactionHash"], "hex") else log["transactionHash"]
        prop = args.get("proposedPrice")
        prop_val = int(prop) if prop is not None and hasattr(prop, "__int__") else None
        return {
            "block_number": log["blockNumber"],
            "log_index": int(log.get("logIndex", 0) or 0),
            "event_time": _block_ts(w3, log["blockNumber"], block_ts_cache),
            "tx_hash": tx,
            "source_oracle": str(log.get("_source_address") or log.get("address") or "").lower(),
            "label": "propose",
            "identifier": args.get("identifier"),
            "timestamp": args.get("timestamp"),
            "ancillaryData": ad,
            "ancillary_hex": _ancillary_to_hex(ad),
            "ancillary_raw": raw,
            "requester": args.get("requester"),
            "proposer": args.get("proposer"),
            "disputer": None,
            "settlement_recipient": None,
            "proposedPrice": prop_val,
            "price": None,
            "payout": None,
            "description": desc, "p1": p1, "p2": p2,
            "request_tx": None, "proposal_tx": tx, "settlement_tx": None,
        }
    except Exception as e:
        print(f"Decode ProposePrice error: {e}", file=sys.stderr)
        return None


def decode_dispute_price(log: Dict, w3: Web3, block_ts_cache: Dict[int, str]) -> Optional[Dict]:
    abi = {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "requester", "type": "address"},
            {"indexed": True, "name": "proposer", "type": "address"},
            {"indexed": True, "name": "disputer", "type": "address"},
            {"indexed": False, "name": "identifier", "type": "bytes32"},
            {"indexed": False, "name": "timestamp", "type": "uint256"},
            {"indexed": False, "name": "ancillaryData", "type": "bytes"},
            {"indexed": False, "name": "proposedPrice", "type": "int256"},
        ],
        "name": "DisputePrice",
        "type": "event",
    }
    try:
        decoded = w3.eth.contract(abi=[abi]).events.DisputePrice().process_log(log)
        args = decoded["args"]
        ad = args.get("ancillaryData") or b""
        raw = _ancillary_to_utf8(ad)
        desc, p1, p2 = _parse_question_raw(raw)
        tx = log["transactionHash"].hex() if hasattr(log["transactionHash"], "hex") else log["transactionHash"]
        prop = args.get("proposedPrice")
        prop_val = int(prop) if prop is not None and hasattr(prop, "__int__") else None
        return {
            "block_number": log["blockNumber"],
            "log_index": int(log.get("logIndex", 0) or 0),
            "event_time": _block_ts(w3, log["blockNumber"], block_ts_cache),
            "tx_hash": tx,
            "source_oracle": str(log.get("_source_address") or log.get("address") or "").lower(),
            "label": "dispute",
            "identifier": args.get("identifier"),
            "timestamp": args.get("timestamp"),
            "ancillaryData": ad,
            "ancillary_hex": _ancillary_to_hex(ad),
            "ancillary_raw": raw,
            "requester": args.get("requester"),
            "proposer": args.get("proposer"),
            "disputer": args.get("disputer"),
            "settlement_recipient": None,
            "proposedPrice": prop_val,
            "price": None,
            "payout": None,
            "description": desc, "p1": p1, "p2": p2,
            "request_tx": None, "proposal_tx": None, "settlement_tx": None,
        }
    except Exception as e:
        print(f"Decode DisputePrice error: {e}", file=sys.stderr)
        return None


def decode_settle(log: Dict, w3: Web3, block_ts_cache: Dict[int, str]) -> Optional[Dict]:
    abi = {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "requester", "type": "address"},
            {"indexed": True, "name": "proposer", "type": "address"},
            {"indexed": True, "name": "disputer", "type": "address"},
            {"indexed": False, "name": "identifier", "type": "bytes32"},
            {"indexed": False, "name": "timestamp", "type": "uint256"},
            {"indexed": False, "name": "ancillaryData", "type": "bytes"},
            {"indexed": False, "name": "price", "type": "int256"},
            {"indexed": False, "name": "payout", "type": "uint256"},
        ],
        "name": "Settle",
        "type": "event",
    }
    try:
        decoded = w3.eth.contract(abi=[abi]).events.Settle().process_log(log)
        args = decoded["args"]
        ad = args.get("ancillaryData") or b""
        raw = _ancillary_to_utf8(ad)
        desc, p1, p2 = _parse_question_raw(raw)
        tx = log["transactionHash"].hex() if hasattr(log["transactionHash"], "hex") else log["transactionHash"]
        price = args.get("price")
        payout = args.get("payout")
        price_val = int(price) if price is not None and hasattr(price, "__int__") else None
        payout_val = int(payout) if payout is not None and hasattr(payout, "__int__") else None
        return {
            "block_number": log["blockNumber"],
            "log_index": int(log.get("logIndex", 0) or 0),
            "event_time": _block_ts(w3, log["blockNumber"], block_ts_cache),
            "tx_hash": tx,
            "source_oracle": str(log.get("_source_address") or log.get("address") or "").lower(),
            "label": "settle",
            "identifier": args.get("identifier"),
            "timestamp": args.get("timestamp"),
            "ancillaryData": ad,
            "ancillary_hex": _ancillary_to_hex(ad),
            "ancillary_raw": raw,
            "requester": args.get("requester"),
            "proposer": args.get("proposer"),
            "disputer": args.get("disputer"),
            "settlement_recipient": args.get("proposer"),
            "proposedPrice": None,
            "price": price_val,
            "payout": payout_val,
            "description": desc, "p1": p1, "p2": p2,
            "request_tx": None, "proposal_tx": None, "settlement_tx": tx,
        }
    except Exception as e:
        print(f"Decode Settle error: {e}", file=sys.stderr)
        return None


def _fill_requester_from_tx(w3: Web3, tx_hash: str) -> Optional[str]:
    try:
        tx = w3.eth.get_transaction(tx_hash)
        return tx.get("from") if tx else None
    except Exception:
        return None


def _ensure_0x(s: Any) -> str:
    if s is None or s == "":
        return ""
    h = s.hex() if hasattr(s, "hex") else str(s)
    return h if h.startswith("0x") else "0x" + h


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _key(e: Dict) -> Tuple[Any, Any, bytes]:
    return (
        e.get("source_oracle") or "",
        e.get("identifier"),
        e.get("timestamp"),
        e.get("ancillaryData") or b"",
    )


def run(
    rpc_url: Optional[str] = None,
    adapter_start_block: Optional[int] = None,
    oracle_start_block: Optional[int] = None,
    end_block: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    output_path: Optional[str] = None,
    limit: Optional[int] = None,
    db_path: Optional[str] = None,
    adapter_addresses_raw: Optional[str] = None,
    oracle_addresses_raw: Optional[str] = None,
    neg_risk_operator_addresses_raw: Optional[str] = None,
    continue_sync: bool = False,
    batch_adapter: int = BATCH_BLOCKS_ADAPTER,
    batch_oracle: int = BATCH_BLOCKS_ORACLE,
    max_workers: int = 30,
    sync_state_key: str = ORACLE_SYNC_STATE_KEY,
    continue_sync_rewind_blocks: int = CONTINUE_SYNC_REWIND_BLOCKS,
    continue_sync_max_blocks: int = 0,
    include_updown: bool = False,
    updown_sync_state_key: str = ORACLE_UPDOWN_LIVE_SYNC_STATE_KEY,
) -> str:
    rpc_url = rpc_url or get_rpc_url()
    w3 = _build_web3(rpc_url)

    db_path = db_path or os.environ.get("POLYMARKET_DB", POLYMARKET_DB)
    db_path = str(Path(db_path).expanduser().resolve())
    db_exists = _db_target_available(db_path)
    adapter_addresses = _parse_address_list(adapter_addresses_raw, DEFAULT_ADAPTER_ADDRESSES)
    oracle_addresses = _parse_address_list(oracle_addresses_raw, DEFAULT_ORACLE_ADDRESSES)
    neg_risk_operator_addresses = _parse_address_list(
        neg_risk_operator_addresses_raw,
        DEFAULT_NEG_RISK_OPERATOR_ADDRESSES,
    )

    if continue_sync and (start_date or end_date):
        raise ValueError("--continue-sync 与 --start/--end 不能同时使用")

    # 日期转区块
    if start_date or end_date:
        raise ValueError(
            "--start/--end date conversion is disabled because it requires "
            "eth_getBlockByNumber timestamp lookups. Use explicit block ranges."
        )
    if end_block is None:
        end_block = w3.eth.block_number
    if continue_sync and oracle_start_block is None:
        last = get_last_oracle_synced_block(db_path, sync_state_key=sync_state_key) if db_exists else None
        if last is not None:
            rewind = max(0, int(continue_sync_rewind_blocks))
            oracle_start_block = max(0, last + 1 - rewind)
            print(
                f"[continue-sync] Resuming oracle sync from block {oracle_start_block} "
                f"(last_block={last}, rewind={rewind})",
                file=sys.stderr,
            )
        else:
            print("[continue-sync] No previous oracle sync found — falling back to default start block logic.", file=sys.stderr)
    if oracle_start_block is None:
        oracle_start_block = resolve_auto_start_block(
            w3,
            db_path,
            end_block,
            adapter_addresses,
            oracle_addresses,
            neg_risk_operator_addresses,
        )
    if continue_sync and continue_sync_max_blocks and continue_sync_max_blocks > 0:
        capped_end_block = min(int(end_block), int(oracle_start_block) + int(continue_sync_max_blocks) - 1)
        if capped_end_block < int(end_block):
            print(
                f"[continue-sync] Capping this live run to block {capped_end_block} "
                f"(target={end_block}, max_blocks={continue_sync_max_blocks})",
                file=sys.stderr,
            )
            end_block = capped_end_block
    if adapter_start_block is None:
        if continue_sync:
            # In live mode the historical adapter / neg-risk dictionaries are
            # already persisted. Re-scanning from block 0 turns every restart
            # into a full-history job and prevents fresh oracle events from
            # being reached. A small rewind on the oracle checkpoint is enough
            # to catch new QuestionInitialized / QuestionPrepared rows.
            adapter_start_block = oracle_start_block
        else:
            adapter_start_block = ADAPTER_FULL_HISTORY_START_BLOCK

    def _live(phase: str, **extra: Any) -> None:
        save_oracle_live_state(
            db_path,
            status=str(extra.pop("status", "running")),
            phase=phase,
            from_block=oracle_start_block,
            to_block=end_block,
            **extra,
        )

    def _log_progress(payload: Dict[str, Any]) -> None:
        save_oracle_live_state(
            db_path,
            status="running",
            phase=f"fetch:{payload.get('label') or 'logs'}",
            from_block=payload.get("fromBlock"),
            to_block=payload.get("toBlock"),
            progress_block=payload.get("progressBlock"),
            logs_scanned=payload.get("logsScanned"),
        )

    _live("start", progress_block=oracle_start_block)

    # Step 1 & 2: 复用历史 SQLite 映射，并增量补充最近 QuestionInitialized
    adapter_map: Dict[str, str] = {}
    adapter_map_source: Dict[str, str] = {}
    neg_risk_request_map: Dict[str, str] = {}
    neg_risk_market_map: Dict[str, str] = {}
    neg_risk_operator_map: Dict[str, str] = {}
    if db_exists:
        conn_map = _connect_sqlite_write(db_path)
        try:
            init_uma_adapter_mapping(conn_map)
            adapter_map, adapter_map_source = load_adapter_mapping(conn_map)
            if adapter_map:
                print(f"Loaded persisted adapter mapping rows: {len(adapter_map)}", file=sys.stderr)
            (
                neg_risk_request_map,
                neg_risk_market_map,
                neg_risk_operator_map,
            ) = load_neg_risk_request_mapping(conn_map)
            if neg_risk_request_map:
                print(
                    f"Loaded persisted neg-risk request mapping rows: {len(neg_risk_request_map)}",
                    file=sys.stderr,
                )
        finally:
            conn_map.close()

    recent_adapter_map, recent_adapter_source = build_adapter_mapping(
        w3,
        adapter_start_block,
        end_block,
        adapter_addresses=adapter_addresses,
        batch_blocks=batch_adapter,
        max_workers=max_workers,
        progress_callback=_log_progress,
    )
    _live("adapter_mapping", progress_block=end_block, logs_scanned=len(recent_adapter_map))
    adapter_map.update(recent_adapter_map)
    adapter_map_source.update(recent_adapter_source)
    if db_exists and recent_adapter_map:
        conn_map = _connect_sqlite_write(db_path)
        try:
            init_uma_adapter_mapping(conn_map)
            changed = save_adapter_mapping_entries(conn_map, recent_adapter_map, recent_adapter_source)
            print(f"Persisted adapter mapping changes: {changed}", file=sys.stderr)
        finally:
            conn_map.close()

    recent_neg_risk_request_map, recent_neg_risk_market_map, recent_neg_risk_operator_map = build_neg_risk_request_mapping(
        w3,
        adapter_start_block,
        end_block,
        operator_addresses=neg_risk_operator_addresses,
        progress_callback=_log_progress,
        batch_blocks=batch_adapter,
        max_workers=max_workers,
    )
    _live("neg_risk_mapping", progress_block=end_block, logs_scanned=len(recent_neg_risk_request_map))
    neg_risk_request_map.update(recent_neg_risk_request_map)
    neg_risk_market_map.update(recent_neg_risk_market_map)
    neg_risk_operator_map.update(recent_neg_risk_operator_map)
    if db_exists and recent_neg_risk_request_map:
        conn_map = _connect_sqlite_write(db_path)
        try:
            init_uma_adapter_mapping(conn_map)
            changed = save_neg_risk_request_mapping_entries(
                conn_map,
                recent_neg_risk_request_map,
                recent_neg_risk_market_map,
                recent_neg_risk_operator_map,
            )
            print(f"Persisted neg-risk request mapping changes: {changed}", file=sys.stderr)
        finally:
            conn_map.close()

    market_bridge = None
    if db_exists:
        try:
            market_bridge = _load_market_bridge_indices(db_path)
            print(
                "Loaded DB bridge indexes: "
                f"gamma_market_id={len(market_bridge['by_gamma_market_id'])}, "
                f"condition_id={len(market_bridge['by_condition_id'])}, "
                f"title_keys={len(market_bridge['by_title_buckets'])}, "
                f"question_id={len(market_bridge['by_question_id'])}",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"Load DB bridge indexes failed: {e}", file=sys.stderr)

    # Step 3: 抓取 UMA Oracle 事件
    all_topics = [_topic0(EVENT_SIGNATURES[k]) for k in ("request", "propose", "dispute", "settle")]
    decoders = {
        _topic0(EVENT_SIGNATURES["request"]): decode_request_price,
        _topic0(EVENT_SIGNATURES["propose"]): decode_propose_price,
        _topic0(EVENT_SIGNATURES["dispute"]): decode_dispute_price,
        _topic0(EVENT_SIGNATURES["settle"]): decode_settle,
    }
    print(f"Fetching UMA Oracle logs {oracle_start_block}-{end_block} (batch={batch_oracle})...", file=sys.stderr)
    logs = fetch_logs_many_addresses(
        w3,
        oracle_start_block,
        end_block,
        oracle_addresses,
        all_topics,
        batch_blocks=batch_oracle,
        max_logs=MAX_LOGS,
        max_workers=max_workers,
        label="UMA Oracle events",
        progress_callback=_log_progress,
    )
    _live("oracle_logs_fetched", progress_block=end_block, logs_scanned=len(logs))

    block_ts_cache: Dict[int, str] = {}

    # ================= 解析日志：不再预取链上 block timestamp =================
    print(f"  ... 开始解析 {len(logs)} 条日志...", file=sys.stderr)
    rows: List[Dict] = []
    for log in logs:
        top = log.get("topics") or []
        topic0_raw = top[0] if top else None
        topic0 = topic0_raw.hex() if hasattr(topic0_raw, "hex") else (topic0_raw or "")
        if topic0 and not topic0.startswith("0x"):
            topic0 = "0x" + topic0
        if topic0 not in decoders:
            continue
        d = decoders[topic0](log, w3, block_ts_cache)
        if d:
            rows.append(d)

    # 不再为 Request 事件额外预取交易发送者。该字段只是辅助展示，
    # 但会对每笔 request 额外触发 eth_getTransactionByHash。
    tx_sender_cache: Dict[str, str] = {}

    def _fast_fill_requester(tx_hash: str) -> Optional[str]:
        return tx_sender_cache.get(tx_hash) or None

    # ================= 分组填充 (现在纯内存运算，0 RPC) =================
    by_key: Dict[Tuple, List[Dict]] = {}
    for r in rows:
        k = _key(r)
        if k not in by_key:
            by_key[k] = []
        by_key[k].append(r)

    for k, group in by_key.items():
        request_tx = proposal_tx = settlement_tx = requester = proposer = disputer = None
        settlement_recipient = None
        proposed_price = settled_price = payout = None
        for r in group:
            if r["label"] == "request":
                request_tx = r.get("tx_hash") or r.get("request_tx")
                requester = _fast_fill_requester(request_tx or "") or requester or r.get("requester")
            elif r["label"] == "propose":
                proposal_tx = r.get("tx_hash") or r.get("proposal_tx")
                proposer = proposer or r.get("proposer")
                if r.get("proposedPrice") is not None:
                    proposed_price = r["proposedPrice"]
            elif r["label"] == "dispute":
                disputer = disputer or r.get("disputer")
                if r.get("proposedPrice") is not None:
                    proposed_price = proposed_price or r["proposedPrice"]
            elif r["label"] == "settle":
                settlement_tx = r.get("tx_hash") or r.get("settlement_tx")
                settlement_recipient = settlement_recipient or r.get("settlement_recipient") or r.get("proposer")
                if r.get("price") is not None:
                    settled_price = r["price"]
                if r.get("payout") is not None:
                    payout = r["payout"]
        for r in group:
            r["request_tx"] = r.get("request_tx") or request_tx
            r["proposal_tx"] = r.get("proposal_tx") or proposal_tx
            r["settlement_tx"] = r.get("settlement_tx") or settlement_tx
            if requester:
                r["requester"] = requester
            else:
                r["requester"] = r.get("requester") or ""
            r["proposer"] = r.get("proposer") or proposer
            r["disputer"] = r.get("disputer") or disputer
            r["settlement_recipient"] = r.get("settlement_recipient") or settlement_recipient
            if r["label"] in ("propose", "dispute") and proposed_price is not None:
                r["proposedPrice"] = r.get("proposedPrice") if r.get("proposedPrice") is not None else proposed_price
            if r["label"] == "settle":
                r["price"] = r.get("price") if r.get("price") is not None else settled_price
                r["payout"] = r.get("payout") if r.get("payout") is not None else payout

    # Step 4 & 5: 字典映射合并 + 直接写库（可选调试导出）
    match_stats = {
        "by_gamma_market_id": 0,
        "by_title": 0,
        "by_title_nearest_date": 0,
        "by_condition_id": 0,
        "by_question_id": 0,
        "neg_risk_request_to_question": 0,
        "missing_neg_risk_request_mapping": 0,
        "unmatched": 0,
        "missing_adapter_mapping": 0,
        "fallback_identifier": 0,
        "fallback_ancillary_hash": 0,
    }
    sorted_rows = sorted(rows, key=lambda x: (-x["block_number"], x["tx_hash"]))
    db_writer = _OracleDbWriter(db_path)
    file_writer = _RecordWriter(output_path) if output_path else None
    for r in sorted_rows:
        ancillary_hex = r.get("ancillary_hex") or _ancillary_to_hex(r.get("ancillaryData") or b"")
        real_qid = adapter_map.get(ancillary_hex.lower()) or adapter_map.get(ancillary_hex) or ""
        source_adapter = adapter_map_source.get(ancillary_hex.lower()) or adapter_map_source.get(ancillary_hex) or ""
        translated_question_id = ""
        if real_qid:
            translated_question_id = (
                neg_risk_request_map.get(real_qid.lower())
                or neg_risk_request_map.get(real_qid)
                or ""
            )
            if translated_question_id:
                match_stats["neg_risk_request_to_question"] += 1
        lookup_question_id = translated_question_id or real_qid
        fallback_question_id = ""
        if not real_qid:
            match_stats["missing_adapter_mapping"] += 1
            iden = r.get("identifier")
            if iden is not None:
                h = iden.hex() if hasattr(iden, "hex") else str(iden)
                if not h.startswith("0x"):
                    h = "0x" + h
                fallback_question_id = h[:66]
                match_stats["fallback_identifier"] += 1
            else:
                ad = r.get("ancillaryData") or b""
                fallback_question_id = "0x" + Web3.keccak(primitive=ad).hex()[:64] if ad else ""
                if fallback_question_id:
                    match_stats["fallback_ancillary_hash"] += 1
        elif source_adapter and source_adapter.lower() in {
            "0x69c47de9d4d3dad79590d61b9e05918e03775f24",
            "0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d",
        } and not translated_question_id:
            match_stats["missing_neg_risk_request_mapping"] += 1

        raw = r.get("ancillary_raw") or ""
        ext_market_id = _extract_market_id(raw)
        ext_title = _extract_title(raw)
        ext_title_norm = _normalize_title(ext_title)

        matched_market: Optional[Dict[str, Any]] = None
        matched_by = ""
        if market_bridge:
            if ext_market_id and ext_market_id in market_bridge["by_gamma_market_id"]:
                matched_market = market_bridge["by_gamma_market_id"][ext_market_id]
                matched_by = "by_gamma_market_id"
                match_stats["by_gamma_market_id"] += 1
            elif ext_title_norm:
                matched_market, matched_by = _select_market_by_title(
                    market_bridge["by_title_buckets"].get(ext_title_norm, []),
                    r.get("event_time", ""),
                )
                if matched_by:
                    match_stats[matched_by] += 1
            if matched_market is None and lookup_question_id and lookup_question_id.lower() in market_bridge["by_condition_id"]:
                matched_market = market_bridge["by_condition_id"][lookup_question_id.lower()]
                matched_by = "by_condition_id"
                match_stats["by_condition_id"] += 1
            if matched_market is None and lookup_question_id and lookup_question_id.lower() in market_bridge["by_question_id"]:
                matched_market = market_bridge["by_question_id"][lookup_question_id.lower()]
                matched_by = "question_id"
                match_stats["by_question_id"] += 1
            if matched_market is None:
                match_stats["unmatched"] += 1

        if matched_market:
            question_id = matched_market.get("question_id") or translated_question_id or real_qid or ""
            condition_id = matched_market.get("condition_id") or ""
            market_id = matched_market.get("market_id") or ""
            market_title = matched_market.get("title") or ext_title
        else:
            question_id = translated_question_id or real_qid or ""
            condition_id = ""
            market_id = ""
            market_title = ext_title

        proposed_price_out = ""
        if r.get("label") in ("propose", "dispute") and r.get("proposedPrice") is not None:
            p = r["proposedPrice"]
            proposed_price_out = str(p / 1e18) if isinstance(p, (int, float)) else str(p)
        settled_price_out = ""
        if r.get("label") == "settle" and r.get("price") is not None:
            p = r["price"]
            settled_price_out = str(p / 1e18) if isinstance(p, (int, float)) else str(p)
        payout_out = str(r["payout"]) if r.get("payout") is not None else ""

        rec = {
            "block_number": r["block_number"],
            "log_index": int(r.get("log_index", 0) or 0),
            "event_time": r["event_time"],
            "tx_hash": _ensure_0x(r["tx_hash"]),
            "event_status": r["label"],
            "external_market_id": _to_text(ext_market_id),
            "market_id": int(market_id) if str(market_id).strip().isdigit() else None,
            "market_title": _to_text(market_title),
            "source_adapter": _to_text(source_adapter),
            "source_oracle": _to_text(r.get("source_oracle") or ""),
            "adapter_question_id": _to_text(real_qid),
            "matched_by": matched_by,
            "question_id": _to_text(question_id),
            "condition_id": _to_text(condition_id),
            "string_raw": _to_text(raw),
            "p1": _to_text(r.get("p1") or ""),
            "p2": _to_text(r.get("p2") or ""),
            "proposed_price": _to_text(proposed_price_out),
            "settled_price": _to_text(settled_price_out),
            "settlement_recipient": (r.get("settlement_recipient") or "").lower() if r.get("settlement_recipient") else "",
            "payout": _to_text(payout_out),
            "requester": (r.get("requester") or "").lower() if r.get("requester") else "",
            "proposer": (r.get("proposer") or "").lower() if r.get("proposer") else "",
            "disputer": (r.get("disputer") or "").lower() if r.get("disputer") else "",
            "request_transaction": _ensure_0x(r.get("request_tx") or ""),
            "proposal_transaction": _ensure_0x(r.get("proposal_tx") or ""),
            "settlement_transaction": _ensure_0x(r.get("settlement_tx") or ""),
        }
        db_writer.write(rec)
        if file_writer is not None:
            file_writer.write({**rec, "market_id": _to_text(rec["market_id"])})
        if db_writer.total % PARQUET_WRITE_BATCH == 0:
            print(f"  ---> 已写入数据库 {db_writer.total} 条 oracle 事件", file=sys.stderr)
            _live("write_oracle_events", progress_block=int(r.get("block_number") or oracle_start_block), records_written=db_writer.total)
        if limit is not None and db_writer.total >= limit:
            break

    db_writer.close()
    if file_writer is not None:
        file_writer.close()
    print(
        "DB bridge match stats: "
        f"gamma_market_id={match_stats['by_gamma_market_id']}, "
        f"title={match_stats['by_title']}, "
        f"title_nearest_date={match_stats['by_title_nearest_date']}, "
        f"condition_id={match_stats['by_condition_id']}, "
        f"question_id={match_stats['by_question_id']}, "
        f"neg_risk_request_to_question={match_stats['neg_risk_request_to_question']}, "
        f"missing_neg_risk_request_mapping={match_stats['missing_neg_risk_request_mapping']}, "
        f"unmatched={match_stats['unmatched']}, "
        f"missing_adapter_mapping={match_stats['missing_adapter_mapping']}, "
        f"fallback_identifier={match_stats['fallback_identifier']}, "
        f"fallback_ancillary_hash={match_stats['fallback_ancillary_hash']}",
        file=sys.stderr,
    )
    save_oracle_synced_block(db_path, end_block, sync_state_key=sync_state_key)
    if include_updown:
        try:
            from oracle.fetch_updown_oracle_chain import run_updown_oracle_backfill

            _live("updown_ctf", progress_block=oracle_start_block)
            updown_last = get_last_oracle_synced_block(db_path, sync_state_key=updown_sync_state_key)
            updown_start_block = oracle_start_block
            if updown_last is not None:
                rewind = max(0, int(continue_sync_rewind_blocks))
                updown_start_block = max(oracle_start_block, int(updown_last) + 1 - rewind)
            if updown_start_block <= end_block:
                updown_stats = run_updown_oracle_backfill(
                    rpc_url=rpc_url,
                    from_block=updown_start_block,
                    to_block=end_block,
                    db_path=db_path,
                    batch_blocks=batch_oracle,
                    max_workers=max_workers,
                    sync_state_key=updown_sync_state_key,
                    include_legacy_ctf=True,
                )
                _live(
                    "updown_ctf_complete",
                    progress_block=end_block,
                    logs_scanned=updown_stats.get("logs_scanned"),
                    records_written=updown_stats.get("matched_events"),
                )
            else:
                print(
                    f"[updown-oracle] Nothing to do: start_block={updown_start_block} > end_block={end_block}",
                    file=sys.stderr,
                )
        except Exception as exc:
            _live("updown_ctf_error", status="error", progress_block=end_block, error=_format_rpc_error(exc))
            raise
    _live("complete", status="idle", progress_block=end_block, logs_scanned=len(logs), records_written=db_writer.total)
    print(f"Saved oracle sync checkpoint: last_block={end_block}", file=sys.stderr)
    print(
        f"Wrote {db_writer.total} oracle event records into {describe_db_target()} / oracle_events",
        file=sys.stderr,
    )
    if file_writer is not None:
        print(f"Also exported oracle events to {output_path}", file=sys.stderr)
    return db_path


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="UMA Oracle 链上拉取：多 adapter + 多 oracle 地址并集抓取，直接写入 oracle_events"
    )
    parser.add_argument("--rpc", default=None, help="RPC URL")
    parser.add_argument("--adapter-start-block", type=int, default=None,
        help="QuestionInitialized 起始区块，建议早于 oracle-start-block")
    parser.add_argument("--oracle-start-block", type=int, default=None,
        help="Oracle 事件起始区块")
    parser.add_argument("--end-block", type=int, default=None, help="结束区块 (default: latest)")
    parser.add_argument("--start", default=None, help='起始日期 "YYYY-MM-DD"')
    parser.add_argument("--end", dest="end_date", default=None, help='结束日期 "YYYY-MM-DD"')
    parser.add_argument("--output", "-o", default=None, help="可选调试导出路径；不传则只写数据库")
    parser.add_argument("--limit", type=int, default=None, help="最大输出条数，默认不限")
    add_db_cli_args(parser)
    parser.add_argument("--watch", action="store_true", help="守护进程模式：循环执行 oracle 同步")
    parser.add_argument("--interval", type=int, default=30, help="--watch 模式下每轮等待秒数")
    parser.add_argument("--confirmations", type=int, default=20, help="自动追最新区块时保留的确认块数")
    parser.add_argument("--continue-sync", action="store_true", help="从 sync_state.oracle_sync 记录的上次区块继续同步")
    parser.add_argument(
        "--adapter-addresses",
        default=",".join(DEFAULT_ADAPTER_ADDRESSES),
        help="逗号分隔的 UmaCtfAdapter 地址列表；默认内置多版本地址并集",
    )
    parser.add_argument(
        "--oracle-addresses",
        default=",".join(DEFAULT_ORACLE_ADDRESSES),
        help="逗号分隔的 UMA Oracle 地址列表；默认内置多版本地址并集",
    )
    parser.add_argument(
        "--neg-risk-operator-addresses",
        default=",".join(DEFAULT_NEG_RISK_OPERATOR_ADDRESSES),
        help="逗号分隔的 NegRiskOperator 地址列表；用于 requestId -> questionId 桥接",
    )
    parser.add_argument("--batch-adapter", type=int, default=BATCH_BLOCKS_ADAPTER,
        help=f"Adapter 每批区块数 (default: {BATCH_BLOCKS_ADAPTER})")
    parser.add_argument("--batch-oracle", type=int, default=BATCH_BLOCKS_ORACLE,
        help=f"Oracle 每批区块数，RPC 限制时改小 (default: {BATCH_BLOCKS_ORACLE})")
    parser.add_argument("--max-workers", type=int, default=30,
        help="并发线程数，Growth 套餐 250 RPS 用 30；若遇 429 可降至 15 (default: 30)")
    parser.add_argument(
        "--continue-sync-rewind-blocks",
        type=int,
        default=CONTINUE_SYNC_REWIND_BLOCKS,
        help=f"--continue-sync 时自动回扫最近多少个块 (default: {CONTINUE_SYNC_REWIND_BLOCKS})",
    )
    parser.add_argument(
        "--continue-sync-max-blocks",
        type=int,
        default=0,
        help="--continue-sync 单轮最多补多少个块；0 表示不限制",
    )
    parser.add_argument(
        "--include-updown",
        action="store_true",
        help="同步 UMA 后追加扫描 CTF ConditionPreparation/ConditionResolution，补齐 crypto/updown 结算事件",
    )
    parser.add_argument(
        "--updown-sync-state-key",
        default=ORACLE_UPDOWN_LIVE_SYNC_STATE_KEY,
        help=f"updown CTF 同步进度 key (default: {ORACLE_UPDOWN_LIVE_SYNC_STATE_KEY})",
    )
    args = parser.parse_args()
    configure_db_from_args(args)
    db_path = args.sqlite_path

    def _run_once() -> None:
        effective_end_block = args.end_block
        if effective_end_block is None:
            rpc_url = args.rpc or get_rpc_url()
            w3 = _build_web3(rpc_url)
            effective_end_block = max(0, w3.eth.block_number - max(0, args.confirmations))

        print(f"Database target: {describe_db_target()}", file=sys.stderr)
        run(
            rpc_url=args.rpc,
            adapter_start_block=args.adapter_start_block,
            oracle_start_block=args.oracle_start_block,
            end_block=effective_end_block,
            start_date=args.start,
            end_date=args.end_date,
            output_path=args.output,
            limit=args.limit,
            db_path=db_path,
            adapter_addresses_raw=args.adapter_addresses,
            oracle_addresses_raw=args.oracle_addresses,
            neg_risk_operator_addresses_raw=args.neg_risk_operator_addresses,
            continue_sync=args.continue_sync,
            batch_adapter=args.batch_adapter,
            batch_oracle=args.batch_oracle,
            max_workers=args.max_workers,
            sync_state_key=ORACLE_SYNC_STATE_KEY,
            continue_sync_rewind_blocks=args.continue_sync_rewind_blocks,
            continue_sync_max_blocks=args.continue_sync_max_blocks,
            include_updown=args.include_updown,
            updown_sync_state_key=args.updown_sync_state_key,
        )

    if args.watch:
        run_index = 0
        consecutive_failures = 0
        try:
            while True:
                run_index += 1
                print(
                    f"\n[oracle] Run #{run_index} at {datetime.now(timezone.utc).isoformat()}",
                    file=sys.stderr,
                )
                try:
                    _run_once()
                    consecutive_failures = 0
                    print(f"[oracle] Sleeping {args.interval}s", file=sys.stderr)
                    time.sleep(args.interval)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    consecutive_failures += 1
                    backoff_seconds = _compute_watch_error_backoff(args.interval, consecutive_failures)
                    save_oracle_live_state(
                        db_path,
                        status="error",
                        phase="watch_error",
                        error=_format_rpc_error(exc),
                    )
                    print(f"[oracle] Run #{run_index} failed: {exc}", file=sys.stderr)
                    print(
                        f"[oracle] Entering recovery sleep for {backoff_seconds}s "
                        f"(consecutive_failures={consecutive_failures}) before retrying.",
                        file=sys.stderr,
                    )
                    time.sleep(backoff_seconds)
        except KeyboardInterrupt:
            print("\n[oracle] Interrupted by user. Exiting.", file=sys.stderr)
    else:
        _run_once()


if __name__ == "__main__":
    main()
