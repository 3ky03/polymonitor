#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段二 - 任务 B: Trades Indexer

功能：扫描指定区块范围内的 OrderFilled 事件，解码交易，写入数据库
支持断点续传、动态市场发现、指数退避重试。
"""

import json
import os
import sys
import time
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Iterable, List, Dict, Optional, Set, Tuple
from decimal import Decimal

# 保证 scripts 根目录在 path 中，以便 from db / config / market 可导入
_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

try:
    from web3 import Web3
except ImportError as e:
    print("Error: web3 not installed or wrong Python env.", file=sys.stderr)
    print("  Run: pip install web3>=6.0.0", file=sys.stderr)
    print("  If using conda, ensure the intended environment is activated.", file=sys.stderr)
    print(f"  Detail: {e}", file=sys.stderr)
    sys.exit(1)
try:
    # 兼容 Web3.py v6.11+ 和 v7 版本
    from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware
except ImportError:
    try:
        # 兼容旧版 Web3.py
        from web3.middleware import geth_poa_middleware
    except ImportError:
        try:
            from web3.middleware.geth_poa import geth_poa_middleware
        except ImportError:
            geth_poa_middleware = None
            print("Warning: POA middleware not found; continuing without POA middleware.", file=sys.stderr)

from db import add_db_cli_args, configure_db_from_args, describe_db_target, get_backend, get_connection, init_schema, dict_from_row, DEFAULT_DB_PATH
from db.db import table_exists
from db.trade_v2 import (
    LEGACY_TRADES_TABLE,
    TRADE_V2_READ_VIEW,
    convert_trade_row_to_v2,
    ensure_trade_v2_schema,
    get_trade_write_mode,
    insert_trades_v2_batch,
    sql_identifier,
)
from market.market_discovery import (
    batch_upsert_markets,
    fetch_and_upsert_markets_for_token_ids,
    fetch_and_upsert_markets_for_token_ids_via_onchain,
)
from trade.trade_decoder import (
    decode_order_filled_log,
    get_order_filled_event_decoders,
    get_order_filled_topics,
    CTF_EXCHANGE_ADDRESS,
    NEG_RISK_EXCHANGE_ADDRESS,
    POLYMARKET_EXCHANGE_2026_ADDRESS,
    POLYMARKET_EXCHANGE_2026_ALT_ADDRESS,
)
from trade.orderfilled_raw import (
    ensure_orderfilled_raw_schema,
    insert_orderfilled_raw_batch,
    orderfilled_raw_row,
    upsert_orderfilled_sync_window,
)
from trade.clickhouse_orderfilled_writer import (
    add_clickhouse_orderfilled_cli_args,
    count_orderfilled_rows as count_clickhouse_orderfilled_rows,
    existing_orderfilled_keys as prefetch_clickhouse_orderfilled_keys,
    insert_orderfilled_fact_rows,
    settings_from_args as clickhouse_settings_from_args,
)
from trade.rpc_utils import build_web3 as build_shared_web3
from config import get_rpc_url

USDC_DIVISOR = 10**6
SYNC_STATE_KEY = "trade_sync"
BATCH_BLOCKS = 5000
MAX_RETRIES = 5
RETRY_DELAY_BASE = 2
MAX_WORKERS = 20
TOKEN_ID_BACKFILL_MAX_PAGES = 0
TRADE_INSERT_BATCH_SIZE = 2000
SQLITE_IN_MAX_VARS = 900
_THREAD_LOCAL = threading.local()
RPC_CONNECT_RETRIES = 3
RPC_CONNECT_RETRY_DELAY_SECONDS = 10
RPC_RECOVERY_SLEEP_BASE_SECONDS = 30
RPC_RECOVERY_SLEEP_MAX_SECONDS = 300
DB_WRITE_MAX_RETRIES = 6
DB_WRITE_RETRY_BASE_SECONDS = 1
LOG_PROCESS_CHUNK_SIZE = 5000
WATCH_ERROR_BACKOFF_BASE_SECONDS = 30
WATCH_ERROR_BACKOFF_MAX_SECONDS = 300
MARKET_BACKFILL_MODE = "api"
MARKET_LOOKUP_BACKEND = os.environ.get("POLYDATA_ORDERFILLED_MARKET_LOOKUP_BACKEND", "auto").strip().lower()
ENABLE_SLOW_CLOB_JSON_MARKET_LOOKUP = os.environ.get(
    "POLYDATA_ENABLE_SLOW_CLOB_JSON_MARKET_LOOKUP",
    "0",
).strip().lower() in {"1", "true", "yes", "y"}

def iter_block_windows(from_block: int, to_block: int, window_blocks: int):
    current = from_block
    while current <= to_block:
        end = min(current + window_blocks - 1, to_block)
        yield current, end
        current = end + 1


def iter_chunks(items: List[Any], chunk_size: int) -> Iterable[Tuple[int, List[Any]]]:
    for start in range(0, len(items), chunk_size):
        yield start, items[start:start + chunk_size]


def _build_web3(rpc_url: str) -> Web3:
    return build_shared_web3(
        rpc_url,
        timeout_seconds=60,
        connect_retries=RPC_CONNECT_RETRIES,
        connect_retry_delay_seconds=RPC_CONNECT_RETRY_DELAY_SECONDS,
    )


def _get_thread_local_web3(rpc_url: str) -> Web3:
    cache = getattr(_THREAD_LOCAL, "web3_cache", None)
    if cache is None:
        cache = {}
        _THREAD_LOCAL.web3_cache = cache
    w3 = cache.get(rpc_url)
    if w3 is None:
        w3 = _build_web3(rpc_url)
        cache[rpc_url] = w3
    return w3


def _invalidate_thread_local_web3(rpc_url: str) -> None:
    cache = getattr(_THREAD_LOCAL, "web3_cache", None)
    if not cache:
        return
    w3 = cache.pop(rpc_url, None)
    provider = getattr(w3, "provider", None) if w3 is not None else None
    if provider is not None:
        try:
            session = getattr(provider, "_request_session_manager", None)
            if session is not None and hasattr(session, "cache_and_return_session"):
                pass
        except Exception:
            pass


def _format_rpc_error(exc: Exception, max_len: int = 240) -> str:
    text = " ".join(str(exc).split())
    markers = (
        " because of Unterminated string",
        " because of Expecting value",
        " because of JSONDecodeError",
    )
    for marker in markers:
        idx = text.find(marker)
        if idx > 0:
            text = text[idx + 12 :]
            break
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


def _is_transient_rpc_error(exc: Exception) -> bool:
    msg = str(exc).lower()
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
        )
    )


def _sleep_for_rpc_recovery(stage: str, attempt: int, exc: Exception) -> None:
    delay = min(RPC_RECOVERY_SLEEP_BASE_SECONDS * (2 ** max(0, attempt - 1)), RPC_RECOVERY_SLEEP_MAX_SECONDS)
    print(
        f"[trade] {stage} failed due to transient RPC/network issue: {_format_rpc_error(exc)}. "
        f"Sleeping {delay}s before retry...",
        file=sys.stderr,
    )
    time.sleep(delay)


def _extract_db_error_code(exc: Exception) -> Optional[int]:
    args = getattr(exc, "args", None)
    if not args:
        return None
    first = args[0]
    return first if isinstance(first, int) else None


def _is_retryable_db_write_error(exc: Exception) -> bool:
    code = _extract_db_error_code(exc)
    if code in (1205, 1213, 2006, 2013):
        return True
    msg = str(exc).lower()
    return any(
        marker in msg
        for marker in (
            "lock wait timeout exceeded",
            "deadlock found",
            "lost connection to mysql server during query",
            "mysql server has gone away",
        )
    )


def _sleep_for_db_write_retry(stage: str, attempt: int, exc: Exception) -> None:
    delay = min(DB_WRITE_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)), 30)
    print(
        f"[trade] {stage} hit retryable DB write error: {_format_rpc_error(exc)}. "
        f"Sleeping {delay}s before retry...",
        file=sys.stderr,
    )
    time.sleep(delay)


def _compute_watch_error_backoff(interval_seconds: int, consecutive_failures: int) -> int:
    base = max(WATCH_ERROR_BACKOFF_BASE_SECONDS, int(interval_seconds))
    backoff = base * (2 ** max(0, consecutive_failures - 1))
    return min(backoff, WATCH_ERROR_BACKOFF_MAX_SECONDS)


def _should_split_range(exc: Exception, from_block: int, to_block: int) -> bool:
    if from_block >= to_block:
        return False
    msg = str(exc).lower()
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
        )
    )


def get_order_filled_topic(w3: Web3) -> bytes:
    return get_order_filled_topics(w3)[0]


def get_order_filled_topic_filter(w3: Web3) -> List[bytes]:
    return list(get_order_filled_topics(w3))


def fetch_logs_with_retry(
    rpc_url: str,
    from_block: int,
    to_block: int,
) -> List[Dict]:
    """带指数退避的 getLogs"""
    addresses = [
        Web3.to_checksum_address(CTF_EXCHANGE_ADDRESS),
        Web3.to_checksum_address(NEG_RISK_EXCHANGE_ADDRESS),
        Web3.to_checksum_address(POLYMARKET_EXCHANGE_2026_ADDRESS),
        Web3.to_checksum_address(POLYMARKET_EXCHANGE_2026_ALT_ADDRESS),
    ]
    recovery_attempt = 0

    while True:
        last_err: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            try:
                w3 = _get_thread_local_web3(rpc_url)
                topics = get_order_filled_topic_filter(w3)
                logs = w3.eth.get_logs(
                    {
                        "address": addresses,
                        "topics": [topics],
                        "fromBlock": from_block,
                        "toBlock": to_block,
                    }
                )
                return [dict(log) for log in logs]
            except Exception as e:
                last_err = e
                _invalidate_thread_local_web3(rpc_url)
                delay = RETRY_DELAY_BASE ** (attempt + 1)
                print(
                    f"getLogs failed blocks {from_block}-{to_block} "
                    f"(attempt {attempt+1}/{MAX_RETRIES}): {_format_rpc_error(e)}, retry in {delay}s",
                    file=sys.stderr,
                )
                time.sleep(delay)

        if last_err is not None and _should_split_range(last_err, from_block, to_block):
            mid = (from_block + to_block) // 2
            print(
                f"getLogs failed repeatedly for blocks {from_block}-{to_block}; split into {from_block}-{mid} and {mid + 1}-{to_block}",
                file=sys.stderr,
            )
            left = fetch_logs_with_retry(rpc_url, from_block, mid)
            right = fetch_logs_with_retry(rpc_url, mid + 1, to_block)
            return left + right

        if last_err is not None and _is_transient_rpc_error(last_err):
            recovery_attempt += 1
            _sleep_for_rpc_recovery(f"getLogs blocks {from_block}-{to_block}", recovery_attempt, last_err)
            continue

        if last_err is not None:
            raise RuntimeError(
                f"getLogs failed for blocks {from_block}-{to_block}: {_format_rpc_error(last_err)}"
            ) from last_err

        return []


def fetch_logs_parallel_with_retry(
    rpc_url: str,
    from_block: int,
    to_block: int,
    batch_blocks: int = BATCH_BLOCKS,
    max_workers: int = MAX_WORKERS,
) -> List[Dict]:
    """并发批量抓取 OrderFilled 日志，风格对齐 oracle 抓取器。"""
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
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_range = {executor.submit(fetch_logs_with_retry, rpc_url, r[0], r[1]): r for r in ranges}
        for future in as_completed(future_to_range):
            res = future.result()
            if res:
                logs.extend(res)
            completed_tasks += 1
            if completed_tasks % max(1, total_tasks // 20) == 0 or completed_tasks == total_tasks:
                progress = (completed_tasks / total_tasks) * 100
                print(
                    f"  ---> 抓取进度: {completed_tasks}/{total_tasks} 批次 ({progress:.1f}%) | 累计日志: {len(logs)} 条",
                    file=sys.stderr,
                )

    logs.sort(key=lambda x: (x.get("blockNumber", 0), x.get("logIndex", 0)))
    return logs


def build_trade_key(tx_hash: Any, log_index: Any) -> Tuple[str, int]:
    if hasattr(tx_hash, "hex"):
        tx_hash_text = tx_hash.hex()
    else:
        tx_hash_text = str(tx_hash)
    return tx_hash_text, int(log_index)


def build_trade_key_from_log(log: Dict) -> Tuple[str, int]:
    return build_trade_key(log.get("transactionHash"), log.get("logIndex", 0))


def prefetch_existing_trade_keys(
    conn,
    from_block: int,
    to_block: int,
) -> Set[Tuple[str, int]]:
    trade_write_mode = get_trade_write_mode()
    source = TRADE_V2_READ_VIEW if trade_write_mode == "v2" else LEGACY_TRADES_TABLE
    source_sql = sql_identifier(source)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT tx_hash, log_index
        FROM {source_sql}
        WHERE block_number >= ? AND block_number <= ?
        """,
        (from_block, to_block),
    )
    return {
        build_trade_key(row["tx_hash"], row["log_index"])
        for row in cursor.fetchall()
    }


def count_existing_trade_rows(conn, from_block: int, to_block: int) -> int:
    trade_write_mode = get_trade_write_mode()
    source = TRADE_V2_READ_VIEW if trade_write_mode == "v2" else LEGACY_TRADES_TABLE
    source_sql = sql_identifier(source)
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM {source_sql}
        WHERE block_number >= ? AND block_number <= ?
        """,
        (from_block, to_block),
    )
    row = cursor.fetchone()
    if row is None:
        return 0
    if hasattr(row, "get"):
        return int(row.get("c") or 0)
    return int(row[0] or 0)


def resolve_market_lookup_backend(value: str) -> str:
    mode = str(value or "auto").strip().lower()
    current = get_backend()
    if mode in {"", "auto"}:
        return "postgres" if current == "mysql" else current
    if mode == "current":
        return current
    if mode in {"mysql", "postgres", "postgresql", "sqlite"}:
        return "postgres" if mode == "postgresql" else mode
    raise ValueError(f"Unsupported market_lookup_backend={value!r}")


def _json_text_for_mysql(value: Any) -> str:
    if value in (None, ""):
        return "[]"
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return value
        return json.dumps(parsed, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _market_key_text(market: Dict[str, Any], key: str) -> str:
    return str(market.get(key) or "").strip()


def ensure_mysql_markets_for_lookup_results(
    write_conn,
    markets: Iterable[Dict[str, Any]],
    *,
    lookup_backend: str,
) -> int:
    """Mirror lookup-backend market rows into MySQL before writing trades_v2.

    During the Postgres market migration the OrderFilled writer can still target
    MySQL trades_v2, whose market_id has a foreign key to MySQL markets.id.  If
    lookup uses Postgres, copy the minimal market row into MySQL with the same
    id, or remap to an already-existing MySQL row with the same condition/slug.
    """
    if get_backend() != "mysql" or lookup_backend == get_backend():
        return 0

    unique: Dict[int, Dict[str, Any]] = {}
    for market in markets:
        try:
            market_id = int(market.get("id") or 0)
        except Exception:
            market_id = 0
        if market_id > 0:
            unique.setdefault(market_id, market)
    if not unique:
        return 0

    cursor = write_conn.cursor()
    mirrored = 0
    for desired_id, market in unique.items():
        local_id = None
        cursor.execute("SELECT id FROM markets WHERE id = ? LIMIT 1", (desired_id,))
        row = cursor.fetchone()
        if row:
            local_id = int(_row_value(row, "id", row[0]) or 0)

        condition_id = _market_key_text(market, "condition_id") or _placeholder_condition_id(
            _market_key_text(market, "yes_token_id") or str(desired_id)
        )
        slug = _market_key_text(market, "slug") or f"postgres-market-{desired_id}"

        if local_id is None and condition_id:
            cursor.execute("SELECT id FROM markets WHERE condition_id = ? LIMIT 1", (condition_id,))
            row = cursor.fetchone()
            if row:
                local_id = int(_row_value(row, "id", row[0]) or 0)

        if local_id is None and slug:
            cursor.execute("SELECT id FROM markets WHERE slug = ? LIMIT 1", (slug,))
            row = cursor.fetchone()
            if row:
                local_id = int(_row_value(row, "id", row[0]) or 0)

        if local_id is not None and local_id > 0:
            market["id"] = local_id
            continue

        yes_token_id = _market_key_text(market, "yes_token_id")
        no_token_id = _market_key_text(market, "no_token_id")
        clob_tokens = market.get("clob_token_ids")
        if not yes_token_id and isinstance(clob_tokens, (list, tuple)) and clob_tokens:
            yes_token_id = str(clob_tokens[0])
        if not no_token_id:
            no_token_id = f"unknown-complement-{yes_token_id[:32] if yes_token_id else desired_id}"

        cursor.execute(
            """
            INSERT INTO markets (
                id, gamma_market_id, slug, condition_id, question_id, oracle,
                yes_token_id, no_token_id, title, description, enable_neg_risk,
                end_date, created_at, category, tags, clob_token_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE
                gamma_market_id = COALESCE(NULLIF(VALUES(gamma_market_id), ''), markets.gamma_market_id),
                question_id = COALESCE(NULLIF(VALUES(question_id), ''), markets.question_id),
                oracle = COALESCE(NULLIF(VALUES(oracle), ''), markets.oracle),
                yes_token_id = COALESCE(NULLIF(VALUES(yes_token_id), ''), markets.yes_token_id),
                no_token_id = COALESCE(NULLIF(VALUES(no_token_id), ''), markets.no_token_id),
                title = COALESCE(NULLIF(VALUES(title), ''), markets.title),
                description = COALESCE(NULLIF(VALUES(description), ''), markets.description),
                category = COALESCE(NULLIF(VALUES(category), ''), markets.category),
                tags = COALESCE(NULLIF(markets.tags, '[]'), VALUES(tags)),
                clob_token_ids = COALESCE(NULLIF(markets.clob_token_ids, '[]'), VALUES(clob_token_ids))
            """,
            (
                desired_id,
                _market_key_text(market, "gamma_market_id"),
                slug,
                condition_id,
                _market_key_text(market, "question_id") or None,
                _market_key_text(market, "oracle") or None,
                yes_token_id,
                no_token_id,
                market.get("title") or f"Postgres mirrored market {desired_id}",
                market.get("description") or "Mirrored from PostgreSQL market lookup for MySQL trades_v2 FK.",
                int(bool(market.get("enable_neg_risk") or 0)),
                market.get("end_date"),
                market.get("created_at") or datetime.now(timezone.utc).isoformat(),
                market.get("category") or "postgres-market-mirror",
                _json_text_for_mysql(market.get("tags")),
                _json_text_for_mysql(clob_tokens if clob_tokens not in (None, "") else [yes_token_id]),
            ),
        )
        market["id"] = desired_id
        mirrored += 1

    if mirrored:
        write_conn.commit()
    return mirrored


def _has_market_tokens_table(conn) -> bool:
    try:
        return table_exists(conn, "market_tokens")
    except Exception:
        return False


def find_market_by_token_id(conn, token_id: str, *, lookup_backend: Optional[str] = None) -> Optional[Dict]:
    """根据 OrderFilled tokenId 查找本地 market。返回的 id 是 local markets.id。"""
    token_text = str(token_id)
    cursor = conn.cursor()

    if _has_market_tokens_table(conn):
        cursor.execute(
            """
            SELECT m.*
            FROM market_tokens mt
            JOIN markets m ON m.id = mt.market_id
            WHERE mt.token_id = ?
            LIMIT 1
            """,
            (token_text,),
        )
        row = cursor.fetchone()
        if row:
            return dict_from_row(row)

    cursor.execute(
        "SELECT * FROM markets WHERE yes_token_id = ? OR no_token_id = ? LIMIT 1",
        (token_text, token_text),
    )
    row = cursor.fetchone()
    if row:
        return dict_from_row(row)

    if not ENABLE_SLOW_CLOB_JSON_MARKET_LOOKUP:
        return None

    backend = lookup_backend or get_backend()
    if backend in {"postgres", "postgresql"}:
        cursor.execute(
            "SELECT * FROM markets WHERE clob_token_ids @> ?::jsonb LIMIT 1",
            (json.dumps([token_text]),),
        )
    elif backend == "mysql":
        cursor.execute(
            "SELECT * FROM markets WHERE JSON_CONTAINS(clob_token_ids, JSON_QUOTE(?)) LIMIT 1",
            (token_text,),
        )
    else:
        cursor.execute(
            "SELECT * FROM markets WHERE clob_token_ids LIKE ? LIMIT 1",
            (f"%{token_text}%",),
        )
    row = cursor.fetchone()
    return dict_from_row(row) if row else None


def prefetch_market_cache_for_token_ids(
    conn,
    token_ids: Iterable[str],
    market_cache: Dict[str, Dict],
    *,
    lookup_backend: Optional[str] = None,
) -> None:
    unresolved = [str(token_id) for token_id in token_ids if token_id and token_id not in market_cache]
    if not unresolved:
        return

    cursor = conn.cursor()

    for chunk_start in range(0, len(unresolved), SQLITE_IN_MAX_VARS):
        chunk = unresolved[chunk_start:chunk_start + SQLITE_IN_MAX_VARS]
        placeholders = ",".join("?" for _ in chunk)

        if _has_market_tokens_table(conn):
            cursor.execute(
                f"""
                SELECT m.*, mt.token_id AS matched_token_id
                FROM market_tokens mt
                JOIN markets m ON m.id = mt.market_id
                WHERE mt.token_id IN ({placeholders})
                """,
                chunk,
            )
            for row in cursor.fetchall():
                market = dict_from_row(row)
                matched_token_id = str(market.get("matched_token_id") or "")
                if matched_token_id:
                    market_cache[matched_token_id] = market

        cursor.execute(
            f"SELECT * FROM markets WHERE yes_token_id IN ({placeholders})",
            chunk,
        )
        for row in cursor.fetchall():
            market = dict_from_row(row)
            market_cache[str(market["yes_token_id"])] = market

        cursor.execute(
            f"SELECT * FROM markets WHERE no_token_id IN ({placeholders})",
            chunk,
        )
        for row in cursor.fetchall():
            market = dict_from_row(row)
            market_cache[str(market["no_token_id"])] = market

    # 只有少量未命中的 token 才走 JSON_CONTAINS 慢路径。
    still_missing = [token_id for token_id in unresolved if token_id not in market_cache]
    for token_id in still_missing:
        market = find_market_by_token_id(conn, token_id, lookup_backend=lookup_backend)
        if market:
            market_cache[token_id] = market


def _placeholder_condition_id(token_id: str) -> str:
    digest = hashlib.sha256(f"polydata-trade-indexer-placeholder:{token_id}".encode()).hexdigest()
    return "0x" + digest


def create_placeholder_markets_for_token_ids(
    conn,
    token_ids: Iterable[str],
    *,
    lookup_backend: Optional[str] = None,
) -> int:
    tokens = sorted({str(token_id or "").strip() for token_id in token_ids if str(token_id or "").strip()})
    if not tokens:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    markets = []
    for token_id in tokens:
        condition_id = _placeholder_condition_id(token_id)
        markets.append(
            {
                "gamma_market_id": "",
                "slug": f"trade-indexer-placeholder-{condition_id[2:18]}",
                "condition_id": condition_id,
                "question_id": None,
                "oracle": None,
                "yes_token_id": token_id,
                "no_token_id": f"unknown-complement-{token_id[:32]}",
                "title": f"Trade indexer placeholder market {token_id[:16]}",
                "description": (
                    "Synthetic market mapping created by trades_indexer because official/Gamma/on-chain "
                    "metadata was unavailable. It preserves chain-complete OrderFilled rows for BUY/SELL "
                    "cashflow and can be remapped if official metadata appears later."
                ),
                "enable_neg_risk": 0,
                "end_date": None,
                "created_at": now,
                "category": "orderfilled-placeholder",
                "tags": ["orderfilled-placeholder", "trade-indexer"],
                "clob_token_ids": [token_id],
            }
        )
    backend = lookup_backend or get_backend()
    if backend in {"postgres", "postgresql"} and backend != get_backend():
        inserted = 0
        cursor = conn.cursor()
        for market in markets:
            cursor.execute(
                """
                INSERT INTO markets (
                    gamma_market_id, slug, condition_id, question_id, oracle,
                    yes_token_id, no_token_id, title, description, enable_neg_risk,
                    end_date, created_at, category, tags, clob_token_ids
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?::jsonb)
                ON CONFLICT (condition_id) DO UPDATE SET
                    slug = COALESCE(NULLIF(EXCLUDED.slug, ''), markets.slug),
                    yes_token_id = COALESCE(NULLIF(EXCLUDED.yes_token_id, ''), markets.yes_token_id),
                    no_token_id = COALESCE(NULLIF(EXCLUDED.no_token_id, ''), markets.no_token_id),
                    title = COALESCE(NULLIF(EXCLUDED.title, ''), markets.title),
                    description = COALESCE(NULLIF(EXCLUDED.description, ''), markets.description),
                    category = COALESCE(NULLIF(EXCLUDED.category, ''), markets.category),
                    tags = CASE WHEN markets.tags IS NULL OR markets.tags = '[]'::jsonb THEN EXCLUDED.tags ELSE markets.tags END,
                    clob_token_ids = CASE
                        WHEN markets.clob_token_ids IS NULL OR markets.clob_token_ids = '[]'::jsonb THEN EXCLUDED.clob_token_ids
                        ELSE markets.clob_token_ids
                    END
                """,
                (
                    market["gamma_market_id"],
                    market["slug"],
                    market["condition_id"],
                    market["question_id"],
                    market["oracle"],
                    market["yes_token_id"],
                    market["no_token_id"],
                    market["title"],
                    market["description"],
                    bool(market["enable_neg_risk"]),
                    market["end_date"],
                    market["created_at"],
                    market["category"],
                    json.dumps(market["tags"]),
                    json.dumps(market["clob_token_ids"]),
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    return batch_upsert_markets(conn, markets)


def backfill_market_cache_for_token_ids(
    conn,
    token_ids: Iterable[str],
    *,
    db_path: str,
    market_cache: Dict[str, Dict],
    market_backfill_mode: str,
    market_backfill_max_pages: int,
    create_placeholder_markets: bool,
    lookup_backend: Optional[str] = None,
) -> None:
    tokens = sorted({str(token_id or "").strip() for token_id in token_ids if str(token_id or "").strip()})
    if not tokens:
        return
    prefetch_market_cache_for_token_ids(conn, tokens, market_cache, lookup_backend=lookup_backend)
    missing = [token_id for token_id in tokens if token_id not in market_cache]
    if not missing:
        return

    mode = str(market_backfill_mode or "none").strip().lower()
    upserted = 0
    if mode != "none":
        if lookup_backend and lookup_backend != get_backend():
            print(
                f"Skip dynamic market backfill for {len(missing)} token(s): "
                f"write backend={get_backend()} lookup backend={lookup_backend}. "
                "Use existing PostgreSQL market_tokens or placeholder markets.",
                file=sys.stderr,
            )
        elif mode == "onchain":
            conn.commit()
            upserted = fetch_and_upsert_markets_for_token_ids_via_onchain(missing, db_path=db_path)
        elif mode == "api":
            conn.commit()
            upserted = fetch_and_upsert_markets_for_token_ids(
                missing,
                db_path=db_path,
                max_pages=max(0, int(market_backfill_max_pages)),
                requests_delay=0.0,
            )
        else:
            raise ValueError(f"Unsupported market_backfill_mode={market_backfill_mode}")
        if upserted:
            prefetch_market_cache_for_token_ids(conn, missing, market_cache, lookup_backend=lookup_backend)

    missing = [token_id for token_id in tokens if token_id not in market_cache]
    if missing and create_placeholder_markets:
        created = create_placeholder_markets_for_token_ids(conn, missing, lookup_backend=lookup_backend)
        if created:
            prefetch_market_cache_for_token_ids(conn, missing, market_cache, lookup_backend=lookup_backend)


def resolve_market_by_token_id(
    conn,
    token_id: str,
    db_path: str,
    backfill_attempted: set,
    enable_market_backfill: bool = True,
    market_backfill_mode: str = MARKET_BACKFILL_MODE,
    market_backfill_max_pages: int = TOKEN_ID_BACKFILL_MAX_PAGES,
    create_placeholder_markets: bool = False,
    market_cache: Optional[Dict] = None,
    lookup_backend: Optional[str] = None,
) -> Optional[Dict]:
    if market_cache is not None and token_id in market_cache:
        return market_cache[token_id]

    market = find_market_by_token_id(conn, token_id, lookup_backend=lookup_backend)
    if market:
        if market_cache is not None:
            market_cache[token_id] = market
        return market

    if token_id in backfill_attempted or not enable_market_backfill:
        return None

    backfill_attempted.add(token_id)
    try:
        conn.commit()
        mode = str(market_backfill_mode or "none").strip().lower()
        if mode == "onchain":
            inserted = fetch_and_upsert_markets_for_token_ids_via_onchain([token_id], db_path=db_path)
        elif mode == "api":
            inserted = fetch_and_upsert_markets_for_token_ids(
                [token_id],
                db_path=db_path,
                max_pages=max(0, int(market_backfill_max_pages)),
                requests_delay=0.0,
            )
        elif mode == "none":
            inserted = 0
        else:
            raise ValueError(f"Unsupported market_backfill_mode={market_backfill_mode}")
        if inserted:
            print(
                f"Backfilled {inserted} market(s) for tokenId {token_id[:30]}...",
                file=sys.stderr,
            )
            market = find_market_by_token_id(conn, token_id, lookup_backend=lookup_backend)
            if market and market_cache is not None:
                market_cache[token_id] = market
            return market
        if create_placeholder_markets:
            created = create_placeholder_markets_for_token_ids(conn, [token_id], lookup_backend=lookup_backend)
            if created:
                market = find_market_by_token_id(conn, token_id, lookup_backend=lookup_backend)
                if market and market_cache is not None:
                    market_cache[token_id] = market
                return market
    except Exception as e:
        print(f"Failed to backfill market for tokenId {token_id[:30]}...: {e}", file=sys.stderr)

    return None


def decode_and_enrich(log: Dict, w3: Web3, event_decoder: Any, block_ts_cache: Dict[int, str]) -> Optional[Dict]:
    """解码日志并补充 block_number、size。

    不再通过 eth_getBlockByNumber 补链上 block timestamp；同步吞吐优先，
    前端/分析按 block_number + log_index 排序即可。
    """
    decoded = decode_order_filled_log(log, w3=w3, event_decoder=event_decoder)
    if not decoded:
        return None
    
    block_num = log.get("blockNumber")
    if block_num is not None:
        decoded["block_number"] = block_num
        decoded["timestamp"] = ""
    
    # size = 成交的头寸代币数量（实际单位，除以1e6）
    tid = str(decoded.get("tokenId", ""))
    maker_asset = str(decoded.get("makerAssetId", ""))
    taker_asset = str(decoded.get("takerAssetId", ""))
    if tid == maker_asset:
        token_amount = int(decoded.get("makerAmountFilled", 0) or 0)
    else:
        token_amount = int(decoded.get("takerAmountFilled", 0) or 0)
    decoded["size"] = str(Decimal(token_amount) / Decimal(USDC_DIVISOR))
    
    return decoded


def build_trade_insert_row(trade: Dict, market_id: int, outcome: str, condition_id: str = "") -> Dict[str, Any]:
    return {
        "tx_hash": trade["txHash"],
        "log_index": trade["logIndex"],
        "market_id": market_id,
        "condition_id": condition_id,
        "maker": str(trade["maker"]),
        "taker": str(trade["taker"]),
        "price": trade["price"],
        "size": trade["size"],
        "side": trade["side"],
        "outcome": outcome,
        "token_id": trade["tokenId"],
        "block_number": trade.get("block_number"),
        "timestamp": trade.get("timestamp"),
        "order_hash": trade.get("orderHash"),
        "maker_asset_id": trade.get("makerAssetId"),
        "taker_asset_id": trade.get("takerAssetId"),
        "maker_amount": int(trade["makerAmountFilled"]) if trade.get("makerAmountFilled") is not None else None,
        "taker_amount": int(trade["takerAmountFilled"]) if trade.get("takerAmountFilled") is not None else None,
        "fee": int(trade["fee"]) if trade.get("fee") is not None else None,
        "contract": trade.get("contract") or trade.get("exchange"),
        "created_at": None,
    }


def insert_trades_batch(conn, trade_rows: List[Dict[str, Any]]) -> int:
    """批量插入交易记录，唯一键冲突时忽略（幂等）"""
    if not trade_rows:
        return 0

    if get_trade_write_mode() == "v2":
        core_rows = [convert_trade_row_to_v2(row) for row in trade_rows]
        return insert_trades_v2_batch(conn, core_rows)

    for attempt in range(1, DB_WRITE_MAX_RETRIES + 1):
        cursor = conn.cursor()
        before_changes = conn.total_changes
        try:
            cursor.executemany(
                """
                INSERT OR IGNORE INTO trades (
                    tx_hash, log_index, market_id, maker, taker, price, size,
                    side, outcome, token_id, block_number, timestamp,
                    order_hash, maker_asset_id, taker_asset_id,
                    maker_amount, taker_amount, fee, contract
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["tx_hash"],
                        row["log_index"],
                        row["market_id"],
                        row["maker"],
                        row["taker"],
                        row["price"],
                        row["size"],
                        row["side"],
                        row["outcome"],
                        row["token_id"],
                        row["block_number"],
                        row["timestamp"],
                        row["order_hash"],
                        row["maker_asset_id"],
                        row["taker_asset_id"],
                        row["maker_amount"],
                        row["taker_amount"],
                        row["fee"],
                        row["contract"],
                    )
                    for row in trade_rows
                ],
            )
            conn.commit()
            return conn.total_changes - before_changes
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            if attempt < DB_WRITE_MAX_RETRIES and _is_retryable_db_write_error(e):
                _sleep_for_db_write_retry(f"insert_trades_batch[{len(trade_rows)} rows]", attempt, e)
                continue
            raise RuntimeError(
                f"Insert trades batch failed for {len(trade_rows)} rows: {e}"
            ) from e


def insert_trade_sinks(conn, trade_rows: List[Dict[str, Any]], clickhouse_settings, clickhouse_write_mode: str) -> int:
    """Write a decoded trade batch to the configured sink(s).

    ``none`` keeps the existing MySQL/SQLite behavior. ``dual`` writes both the
    configured DB and ClickHouse. ``clickhouse`` writes ClickHouse as the primary
    trade sink while the configured DB is still used for sync metadata.
    """

    if not trade_rows:
        return 0
    mode = (clickhouse_write_mode or "none").strip().lower()
    write_db = mode in {"none", "dual"}
    write_clickhouse = mode in {"dual", "clickhouse"}
    inserted = 0
    if write_db:
        inserted = insert_trades_batch(conn, trade_rows)
    if write_clickhouse:
        ch_inserted = insert_orderfilled_fact_rows(clickhouse_settings, trade_rows, skip_existing=True)
        if not write_db:
            inserted = ch_inserted
    return inserted


def update_sync_state(conn, block_number: int, sync_state_key: str = SYNC_STATE_KEY) -> None:
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO sync_state (key, value, last_block, updated_at) VALUES (?, ?, ?, ?)",
        (
            sync_state_key,
            str(block_number),
            block_number,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        ),
    )


def run_indexer(
    from_block: int,
    to_block: int,
    rpc_url: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
    output_json: Optional[str] = None,
    batch_blocks: int = BATCH_BLOCKS,
    max_workers: int = MAX_WORKERS,
    sync_state_key: str = SYNC_STATE_KEY,
    update_sync_state_on_commit: bool = True,
    enable_market_backfill: bool = True,
    market_backfill_mode: str = MARKET_BACKFILL_MODE,
    market_backfill_max_pages: int = TOKEN_ID_BACKFILL_MAX_PAGES,
    create_placeholder_markets: bool = False,
    audit_sync_windows: bool = True,
    fail_on_incomplete_window: bool = True,
    allow_incomplete_sync_state: bool = False,
    write_orderfilled_raw: bool = False,
    market_lookup_backend: str = MARKET_LOOKUP_BACKEND,
    clickhouse_write_mode: str = "none",
    clickhouse_settings=None,
) -> Tuple[int, int]:
    """
    扫描 from_block 到 to_block 的 OrderFilled 日志，解码并写入数据库或 JSON 文件

    Args:
        output_json: 若指定，则写入该 JSON 文件而非数据库（测试模式）

    Returns:
        (处理日志数, 新插入交易数或输出的交易数)
    """
    if rpc_url is None:
        rpc_url = get_rpc_url()
    test_mode = output_json is not None
    init_schema(db_path=db_path)

    w3 = _build_web3(rpc_url)
    event_decoder = get_order_filled_event_decoders(w3)

    conn = get_connection(db_path)
    if not test_mode and get_trade_write_mode() == "v2":
        ensure_trade_v2_schema(conn)
    if not test_mode and write_orderfilled_raw:
        ensure_orderfilled_raw_schema(conn)
    lookup_backend = resolve_market_lookup_backend(market_lookup_backend)
    market_conn = conn
    close_market_conn = False
    if not test_mode and lookup_backend != get_backend():
        market_conn = get_connection(backend=lookup_backend)
        close_market_conn = True
    if not test_mode:
        print(
            f"Market lookup target: {lookup_backend} ({'separate connection' if close_market_conn else 'write connection'})",
            file=sys.stderr,
            flush=True,
        )
    inserted = 0
    raw_inserted = 0
    processed = 0
    unknown_tokens = set()
    backfill_attempted = set()
    market_cache: Dict[str, Dict] = {}
    trades_out: List[Dict] = []  # 测试模式收集
    window_blocks = max(batch_blocks, batch_blocks * max_workers)
    total_blocks = max(0, to_block - from_block + 1)
    completed_blocks = 0

    try:
        if clickhouse_write_mode != "none" and clickhouse_settings is None:
            raise RuntimeError("clickhouse_settings is required when ClickHouse writing is enabled")
        print(
            f"Streaming logs blocks {from_block}-{to_block} with window={window_blocks}, batch={batch_blocks}, workers={max_workers}...",
            file=sys.stderr,
            flush=True,
        )

        for window_index, (window_start, window_end) in enumerate(
            iter_block_windows(from_block, to_block, window_blocks),
            start=1,
        ):
            block_ts_cache: Dict[int, str] = {}
            pending_trade_rows: List[Dict[str, Any]] = []
            skipped_existing = 0
            window_inserted = 0
            window_unresolved_market_rows = 0
            window_unknown_tokens: Set[str] = set()
            print(
                f"Window {window_index}: fetch logs blocks {window_start}-{window_end}...",
                file=sys.stderr,
                flush=True,
            )
            logs = fetch_logs_parallel_with_retry(
                rpc_url,
                window_start,
                window_end,
                batch_blocks=batch_blocks,
                max_workers=max_workers,
            )

            existing_trade_keys = set()
            if logs and not test_mode:
                if clickhouse_write_mode == "clickhouse":
                    existing_trade_keys = prefetch_clickhouse_orderfilled_keys(
                        clickhouse_settings,
                        window_start,
                        window_end,
                    )
                else:
                    existing_trade_keys = prefetch_existing_trade_keys(conn, window_start, window_end)
                if existing_trade_keys:
                    print(
                        f"  ... 当前窗口命中已存在交易键 {len(existing_trade_keys)} 条；交易聚合表跳过重复写...",
                        file=sys.stderr,
                    )

            print(f"  ... 开始解析并写入当前窗口 {len(logs)} 条日志...", file=sys.stderr)
            progress_step = max(1, len(logs) // 100)
            next_progress_mark = progress_step
            for chunk_start, log_chunk in iter_chunks(logs, LOG_PROCESS_CHUNK_SIZE):
                decoded_chunk: List[Dict] = []
                token_ids_in_chunk: Set[str] = set()

                for log in log_chunk:
                    processed += 1
                    skip_trade_insert = (
                        not test_mode
                        and bool(existing_trade_keys)
                        and build_trade_key_from_log(log) in existing_trade_keys
                    )
                    if skip_trade_insert:
                        skipped_existing += 1
                    decoded = decode_and_enrich(log, w3, event_decoder, block_ts_cache)
                    if not decoded:
                        continue
                    if log.get("topics"):
                        topic0 = log["topics"][0]
                        decoded["event_topic"] = topic0.hex() if hasattr(topic0, "hex") else str(topic0)
                    if skip_trade_insert:
                        decoded["_skip_trade_insert"] = True
                    decoded_chunk.append(decoded)
                    if not test_mode and not skip_trade_insert:
                        token_ids_in_chunk.add(str(decoded["tokenId"]))

                if not test_mode and write_orderfilled_raw and decoded_chunk:
                    raw_rows = [
                        orderfilled_raw_row(
                            {key: value for key, value in decoded.items() if not key.startswith("_")},
                            event_topic=decoded.get("event_topic") or "",
                        )
                        for decoded in decoded_chunk
                    ]
                    raw_inserted += insert_orderfilled_raw_batch(conn, raw_rows)

                if not test_mode and token_ids_in_chunk:
                    if enable_market_backfill:
                        backfill_market_cache_for_token_ids(
                            market_conn,
                            token_ids_in_chunk,
                            db_path=db_path,
                            market_cache=market_cache,
                            market_backfill_mode=market_backfill_mode,
                            market_backfill_max_pages=market_backfill_max_pages,
                            create_placeholder_markets=create_placeholder_markets,
                            lookup_backend=lookup_backend,
                        )
                    else:
                        prefetch_market_cache_for_token_ids(
                            market_conn,
                            token_ids_in_chunk,
                            market_cache,
                            lookup_backend=lookup_backend,
                        )

                    if market_cache and lookup_backend != get_backend() and get_backend() == "mysql":
                        mirror_count = ensure_mysql_markets_for_lookup_results(
                            conn,
                            (market_cache[token_id] for token_id in token_ids_in_chunk if token_id in market_cache),
                            lookup_backend=lookup_backend,
                        )
                        if mirror_count:
                            print(
                                f"  ... mirrored {mirror_count} PostgreSQL market row(s) into MySQL for trades_v2 FK...",
                                file=sys.stderr,
                                flush=True,
                            )

                for decoded in decoded_chunk:
                    if decoded.get("_skip_trade_insert"):
                        continue
                    token_id = str(decoded["tokenId"])
                    market = None
                    if not test_mode:
                        market = market_cache.get(token_id)
                        if market is None:
                            market = resolve_market_by_token_id(
                                market_conn,
                                token_id,
                                db_path,
                                backfill_attempted,
                                enable_market_backfill=enable_market_backfill,
                                market_backfill_mode=market_backfill_mode,
                                market_backfill_max_pages=market_backfill_max_pages,
                                create_placeholder_markets=create_placeholder_markets,
                                market_cache=market_cache,
                                lookup_backend=lookup_backend,
                            )
                            if market and lookup_backend != get_backend() and get_backend() == "mysql":
                                ensure_mysql_markets_for_lookup_results(
                                    conn,
                                    [market],
                                    lookup_backend=lookup_backend,
                                )

                        if not market and token_id not in unknown_tokens:
                            unknown_tokens.add(token_id)
                            window_unknown_tokens.add(token_id)
                            print(
                                f"Unknown tokenId, skip for now: {token_id} "
                                f"(window will be marked incomplete and sync_state will not advance)",
                                file=sys.stderr,
                            )

                    if test_mode:
                        row = {
                            "tx_hash": decoded.get("txHash"),
                            "log_index": decoded.get("logIndex"),
                            "block_number": decoded.get("block_number"),
                            "timestamp": decoded.get("timestamp"),
                            "maker": decoded.get("maker"),
                            "taker": decoded.get("taker"),
                            "price": decoded.get("price"),
                            "size": decoded.get("size"),
                            "side": decoded.get("side"),
                            "token_id": token_id,
                            "market_id": 0,
                            "market_slug": None,
                            "outcome": None,
                            "order_hash": decoded.get("orderHash"),
                            "maker_asset_id": decoded.get("makerAssetId"),
                            "taker_asset_id": decoded.get("takerAssetId"),
                            "maker_amount": decoded.get("makerAmountFilled"),
                            "taker_amount": decoded.get("takerAmountFilled"),
                            "fee": decoded.get("fee"),
                            "contract": decoded.get("contract") or decoded.get("exchange"),
                        }
                        trades_out.append(row)
                        inserted += 1
                    else:
                        if market:
                            outcome = "YES" if str(market["yes_token_id"]) == token_id else "NO"
                            m_id = market["id"]
                        else:
                            window_unresolved_market_rows += 1
                            continue

                        pending_trade_rows.append(
                            build_trade_insert_row(
                                decoded,
                                m_id,
                                outcome,
                                condition_id=str(market.get("condition_id") or ""),
                            )
                        )
                        if len(pending_trade_rows) >= TRADE_INSERT_BATCH_SIZE:
                            batch_inserted = insert_trade_sinks(
                                conn,
                                pending_trade_rows,
                                clickhouse_settings,
                                clickhouse_write_mode,
                            )
                            inserted += batch_inserted
                            window_inserted += batch_inserted
                            pending_trade_rows.clear()

                chunk_end = chunk_start + len(log_chunk)
                should_report = chunk_end >= next_progress_mark or chunk_end == len(logs)
                if should_report:
                    if pending_trade_rows and not test_mode:
                        batch_inserted = insert_trade_sinks(
                            conn,
                            pending_trade_rows,
                            clickhouse_settings,
                            clickhouse_write_mode,
                        )
                        inserted += batch_inserted
                        window_inserted += batch_inserted
                        pending_trade_rows.clear()
                    progress = (chunk_end / len(logs)) * 100 if logs else 100.0
                    print(
                        f"  ---> 窗口处理进度: {chunk_end}/{len(logs)} ({progress:.1f}%) | raw_inserted={raw_inserted} | inserted={inserted} | unknown_tokens={len(unknown_tokens)}",
                        file=sys.stderr,
                    )
                    while next_progress_mark <= chunk_end:
                        next_progress_mark += progress_step

            if pending_trade_rows and not test_mode:
                batch_inserted = insert_trade_sinks(
                    conn,
                    pending_trade_rows,
                    clickhouse_settings,
                    clickhouse_write_mode,
                )
                inserted += batch_inserted
                window_inserted += batch_inserted
                pending_trade_rows.clear()

            completed_blocks += window_end - window_start + 1
            overall_progress = (completed_blocks / total_blocks) * 100 if total_blocks else 100.0
            window_status = "complete"
            window_db_count = len(logs)
            window_last_error = None
            if not test_mode:
                if clickhouse_write_mode == "clickhouse":
                    window_db_count = count_clickhouse_orderfilled_rows(clickhouse_settings, window_start, window_end)
                else:
                    window_db_count = count_existing_trade_rows(conn, window_start, window_end)
                if window_db_count < len(logs) or window_unresolved_market_rows:
                    window_status = "incomplete"
                    window_last_error = (
                        f"db_missing={max(0, len(logs) - window_db_count)} "
                        f"unresolved_market_rows={window_unresolved_market_rows} "
                        f"unresolved_tokens={len(window_unknown_tokens)}"
                    )
                if audit_sync_windows:
                    upsert_orderfilled_sync_window(
                        conn,
                        from_block=window_start,
                        to_block=window_end,
                        chain_log_count=len(logs),
                        db_log_count=window_db_count,
                        repaired_count=window_inserted,
                        status=window_status,
                        last_error=window_last_error,
                    )
                if update_sync_state_on_commit and (window_status == "complete" or allow_incomplete_sync_state):
                    update_sync_state(conn, window_end, sync_state_key=sync_state_key)
                conn.commit()

                if window_status != "complete" and fail_on_incomplete_window:
                    raise RuntimeError(
                        f"OrderFilled window incomplete blocks {window_start}-{window_end}: {window_last_error}"
                    )

            print(
                f"Window {window_index} done: blocks {window_start}-{window_end} | logs={len(logs)} | db_count={window_db_count} | status={window_status} | skipped_existing={skipped_existing} | processed={processed} | raw_inserted={raw_inserted} | inserted={inserted} | overall_blocks={overall_progress:.1f}%",
                file=sys.stderr,
                flush=True,
            )

        if test_mode:
            payload = {
                "block_range": [from_block, to_block],
                "summary": {"processed": processed, "trades_with_market": inserted},
                "trades": trades_out,
            }
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"Test mode: wrote {len(trades_out)} trades to {output_json}", file=sys.stderr)
    finally:
        if close_market_conn:
            market_conn.close()
        conn.close()

    return processed, inserted


def get_last_synced_block(
    db_path: str = DEFAULT_DB_PATH,
    sync_state_key: str = SYNC_STATE_KEY,
) -> Optional[int]:
    """获取上次同步到的区块高度"""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT last_block FROM sync_state WHERE key = ?",
        (sync_state_key,),
    )
    row = cursor.fetchone()
    conn.close()
    return int(row[0]) if row and row[0] is not None else None


def _resolve_sync_range(args, rpc_url: str) -> Tuple[int, int]:
    w3 = _build_web3(rpc_url)

    to_block = args.to_block
    if to_block is None:
        latest = w3.eth.block_number
        to_block = max(0, latest - max(0, args.confirmations))

    from_block = args.from_block
    if args.continue_sync:
        last = get_last_synced_block(args.sqlite_path, sync_state_key=args.sync_state_key)
        from_block = (last + 1) if last is not None else 0
    if from_block is None:
        from_block = max(0, to_block - args.batch)

    return from_block, to_block


def _run_once(args) -> Tuple[int, int]:
    rpc_url = args.rpc or get_rpc_url()
    from_block, to_block = _resolve_sync_range(args, rpc_url)
    out_json = args.test
    if out_json:
        print(f"Test mode: blocks {from_block} to {to_block} -> {out_json}", file=sys.stderr)
    else:
        print(f"Indexing blocks {from_block} to {to_block} (RPC: {rpc_url[:50]}...)", file=sys.stderr)
        print(f"Database target: {describe_db_target()}", file=sys.stderr)

    processed, inserted = run_indexer(
        from_block, to_block, rpc_url, args.sqlite_path,
        output_json=out_json,
        batch_blocks=args.batch,
        max_workers=args.max_workers,
        sync_state_key=args.sync_state_key,
        update_sync_state_on_commit=not args.no_sync_state_update,
        enable_market_backfill=not args.disable_market_backfill,
        market_backfill_mode=args.market_backfill_mode,
        market_backfill_max_pages=args.market_backfill_max_pages,
        create_placeholder_markets=args.create_placeholder_markets,
        audit_sync_windows=not args.disable_sync_window_audit,
        fail_on_incomplete_window=not args.allow_incomplete_window,
        allow_incomplete_sync_state=args.allow_incomplete_sync_state,
        write_orderfilled_raw=args.write_orderfilled_raw,
        market_lookup_backend=args.market_lookup_backend,
        clickhouse_write_mode=args.clickhouse_write_mode,
        clickhouse_settings=clickhouse_settings_from_args(args),
    )
    if out_json:
        print(f"Processed {processed} logs, wrote {inserted} trades to {out_json}.", file=sys.stderr)
    else:
        print(f"Processed {processed} logs, inserted {inserted} trades.", file=sys.stderr)
    return processed, inserted


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Polymarket Trades Indexer")
    parser.add_argument("--from-block", type=int, help="起始区块")
    parser.add_argument("--to-block", type=int, help="结束区块")
    parser.add_argument("--rpc", default=None, help="RPC URL（默认从 NODE_URL/POLYMARKET_RPC_URL 或 config 读取）")
    add_db_cli_args(parser)
    parser.add_argument("--continue-sync", action="store_true", help="从上次进度继续")
    parser.add_argument("--watch", action="store_true", help="守护进程模式：循环执行 trade 同步")
    parser.add_argument("--interval", type=int, default=30, help="--watch 模式下每轮等待秒数")
    parser.add_argument("--confirmations", type=int, default=20, help="自动追最新区块时保留的确认块数")
    parser.add_argument("--batch", type=int, default=BATCH_BLOCKS, help="每批区块数")
    parser.add_argument("--max-workers", type=int, default=MAX_WORKERS, help="并发线程数")
    add_clickhouse_orderfilled_cli_args(parser)
    parser.add_argument("--sync-state-key", default=SYNC_STATE_KEY, help="sync_state 中用于读写进度的 key")
    parser.add_argument("--no-sync-state-update", action="store_true", help="写入 trades 但不更新 sync_state，用于历史补齐任务")
    parser.add_argument("--disable-market-backfill", action="store_true", help="仅使用现有 markets 映射，不在交易索引过程中动态回填缺失 market")
    parser.add_argument(
        "--market-backfill-mode",
        choices=["api", "onchain", "none"],
        default=MARKET_BACKFILL_MODE,
        help="缺失 token->market 时的回填方式；api 会先直接按 token 查官方接口，再走链上 registry/少量事件页。",
    )
    parser.add_argument(
        "--market-backfill-max-pages",
        type=int,
        default=TOKEN_ID_BACKFILL_MAX_PAGES,
        help="market-backfill-mode=api 时，直接 token 查询和链上 registry 都失败后，最多扫多少页 /events。",
    )
    parser.add_argument(
        "--create-placeholder-markets",
        action="store_true",
        help="官方/链上 market 元数据仍缺失时，为 token 创建带 orderfilled-placeholder 标签的最小 market，保证 BUY/SELL 不漏写。",
    )
    parser.add_argument(
        "--disable-sync-window-audit",
        action="store_true",
        help="不写 orderfilled_sync_windows 审计窗口；默认会记录每个窗口是否完整。",
    )
    parser.add_argument(
        "--allow-incomplete-window",
        action="store_true",
        help="遇到 chain logs 与 trades_v2 数量不一致时不中断；默认中断，防止 sync_state 越过缺口。",
    )
    parser.add_argument(
        "--allow-incomplete-sync-state",
        action="store_true",
        help="即使窗口 incomplete 也推进 sync_state；仅用于手动兼容旧行为，不建议常规同步使用。",
    )
    parser.add_argument(
        "--write-orderfilled-raw",
        action="store_true",
        help="同时写入 orderfilled_raw 审计表；默认关闭，常规同步只写 trades_v2。",
    )
    parser.add_argument(
        "--market-lookup-backend",
        choices=["auto", "current", "mysql", "postgres", "postgresql", "sqlite"],
        default=MARKET_LOOKUP_BACKEND,
        help=(
            "OrderFilled token->market 映射读取库。auto 表示写 MySQL 时查 PostgreSQL，"
            "其他情况查当前写入库。"
        ),
    )
    parser.add_argument(
        "--test",
        metavar="JSON_FILE",
        nargs="?",
        const="trades_test.json",
        default=None,
        help="测试模式：不写数据库，将解码后的交易输出到 JSON 文件（默认 trades_test.json）",
    )

    args = parser.parse_args()
    configure_db_from_args(args)
    if args.watch:
        run_index = 0
        consecutive_failures = 0
        try:
            while True:
                run_index += 1
                print(
                    f"\n[trade] Run #{run_index} at {datetime.now(timezone.utc).isoformat()}",
                    file=sys.stderr,
                )
                try:
                    _run_once(args)
                    consecutive_failures = 0
                    print(f"[trade] Sleeping {args.interval}s", file=sys.stderr)
                    time.sleep(args.interval)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    consecutive_failures += 1
                    backoff_seconds = _compute_watch_error_backoff(args.interval, consecutive_failures)
                    print(f"[trade] Run #{run_index} failed: {exc}", file=sys.stderr)
                    print(
                        f"[trade] Entering recovery sleep for {backoff_seconds}s "
                        f"(consecutive_failures={consecutive_failures}) before retrying.",
                        file=sys.stderr,
                    )
                    time.sleep(backoff_seconds)
        except KeyboardInterrupt:
            print("\n[trade] Interrupted by user. Exiting.", file=sys.stderr)
    else:
        _run_once(args)


if __name__ == "__main__":
    main()
