#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段二 - 任务 A: Market Discovery Service

功能：从 Gamma API 发现新市场，校验链上参数，存储到数据库
定期运行以捕获新上线的市场，为 Trades Indexer 提供市场清单。
"""

import json
import os
import re
import sys
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Optional, Tuple, Any

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("Error: requests not installed. pip install requests")
    sys.exit(1)

# 保证 scripts 根目录在 path 中
import sys
from pathlib import Path
_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

try:
    from web3 import Web3
except ImportError:
    Web3 = None

try:
    from psycopg.types.json import Jsonb
except ImportError:
    Jsonb = None

# 引入阶段一模块
from db import (
    DEFAULT_DB_PATH,
    add_db_cli_args,
    configure_db_from_args,
    describe_db_target,
    get_backend,
    get_db,
    get_connection,
    get_table_columns,
    init_schema,
    table_exists,
)
from config import get_rpc_url
from data_sources import POLYMARKET_CLOB_API_BASE
from trade.trade_decoder import CTF_EXCHANGE_ADDRESS, NEG_RISK_EXCHANGE_ADDRESS
from trade.orderfilled_raw import ORDERFILLED_RAW_TABLE, normalize_hex
try:
    from .market_decoder import (
        calculate_market_tokens,
        GAMMA_API_BASE,
        USDC_E_ADDRESS,
    )
    from .market_identity import gamma_market_url, normalize_gamma_market_id
except ImportError:
    # 直接运行脚本时无父包，将 market 目录加入 path
    _market_dir = Path(__file__).resolve().parent
    if str(_market_dir) not in sys.path:
        sys.path.insert(0, str(_market_dir))
    from market_decoder import (
        calculate_market_tokens,
        GAMMA_API_BASE,
        USDC_E_ADDRESS,
    )
    from market_identity import gamma_market_url, normalize_gamma_market_id

# Gamma API
GAMMA_MARKETS_URL = f"{GAMMA_API_BASE}/markets"
GAMMA_MARKETS_KEYSET_URL = f"{GAMMA_API_BASE}/markets/keyset"
GAMMA_EVENTS_URL = f"{GAMMA_API_BASE}/events"
CLOB_API_BASE = POLYMARKET_CLOB_API_BASE
TOKEN_REGISTERED_EVENT_SIGNATURE = "TokenRegistered(uint256,uint256,bytes32)"
SYNC_KEY_CHAIN_REGISTRY_CTF = "market_chain_registry_ctf"
SYNC_KEY_CHAIN_REGISTRY_NEG_RISK = "market_chain_registry_neg_risk"
DEFAULT_CHAIN_BATCH_BLOCKS = 50000
DEFAULT_CHAIN_REQUESTS_DELAY = 0.02
CHAIN_REGISTRY_EVENTS = (
    ("ctf", CTF_EXCHANGE_ADDRESS, SYNC_KEY_CHAIN_REGISTRY_CTF),
    ("neg_risk", NEG_RISK_EXCHANGE_ADDRESS, SYNC_KEY_CHAIN_REGISTRY_NEG_RISK),
)
GENERIC_CATEGORY_TAG_SLUGS = {"all", "featured", "hide-from-new", "recurring"}


def _attach_event_meta_to_market(m: Dict, ev: Dict) -> None:
    """把 event 的 negRisk、slug、category、tags 挂到 market 上，供 normalize_market_from_gamma 使用。"""
    m["_event_id"] = ev.get("id")
    m["_event_neg_risk"] = ev.get("negRisk", len(ev.get("markets", [])) > 1)
    m["_event_slug"] = ev.get("slug", ev.get("ticker", ""))
    m["_event_title"] = ev.get("title") or ev.get("name")
    m["_event_category"] = ev.get("category")
    m["_event_subcategory"] = ev.get("subcategory")
    m["_event_categories"] = ev.get("categories")
    m["_event_tags"] = ev.get("tags")
    if not (m.get("createdAt") or m.get("created_at")):
        event_created_at = ev.get("startDate") or ev.get("start_date") or ev.get("createdAt") or ev.get("created_at")
        if event_created_at:
            m["createdAt"] = event_created_at
    if not (m.get("endDate") or m.get("end_date")):
        event_end_date = ev.get("endDate") or ev.get("end_date")
        if event_end_date:
            m["endDate"] = event_end_date


def _attach_embedded_event_meta_to_market(m: Dict) -> None:
    """从 GET /markets 返回的 embedded event 中提取 event 元数据。"""
    events = m.get("events")
    if not isinstance(events, list) or not events:
        return
    ev = events[0]
    if not isinstance(ev, dict):
        return
    _attach_event_meta_to_market(m, ev)


def _ensure_0x(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.startswith("0x") else f"0x{text}"


def _normalize_tag_slug(tag: Any) -> str:
    if isinstance(tag, dict):
        value = tag.get("slug") or tag.get("label") or ""
    else:
        value = tag
    return str(value or "").strip().lower()


def _normalize_tags_payload(tags_raw: Any) -> List[str]:
    if isinstance(tags_raw, str):
        try:
            parsed = json.loads(tags_raw)
            if isinstance(parsed, list):
                tags_raw = parsed
            elif parsed:
                tags_raw = [parsed]
        except Exception:
            tags_raw = [tags_raw]
    if not isinstance(tags_raw, list):
        tags_raw = [tags_raw] if tags_raw else []

    tags: List[str] = []
    for item in tags_raw:
        slug = _normalize_tag_slug(item)
        if slug and slug not in tags:
            tags.append(slug)
    return tags


def _derive_category_from_tags(tags: List[str]) -> str:
    for tag in tags:
        if tag and tag not in GENERIC_CATEGORY_TAG_SLUGS:
            return tag
    return tags[0] if tags else ""


def _topic_to_int(topic: Any) -> int:
    if hasattr(topic, "hex"):
        return int(topic.hex(), 16)
    text = str(topic)
    if text.startswith("0x"):
        text = text[2:]
    return int(text, 16)


def _topic_to_hex(topic: Any) -> str:
    if hasattr(topic, "hex"):
        return _ensure_0x(topic.hex())
    return _ensure_0x(str(topic))


def _token_registered_topic0() -> Optional[bytes]:
    if Web3 is None:
        return None
    return Web3.keccak(text=TOKEN_REGISTERED_EVENT_SIGNATURE)


def _build_web3(rpc_url: Optional[str] = None):
    if Web3 is None:
        raise RuntimeError("web3 is not installed; on-chain registry supplement is unavailable")
    endpoint = rpc_url or get_rpc_url()
    w3 = Web3(Web3.HTTPProvider(endpoint, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to RPC: {endpoint}")
    return w3


def _get_sync_last_block(conn, key: str) -> Optional[int]:
    cur = conn.cursor()
    cur.execute("SELECT last_block FROM sync_state WHERE key = ?", (key,))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return int(row[0])


def _save_sync_last_block(conn, key: str, block_number: int) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO sync_state (key, value, last_block, updated_at) VALUES (?, ?, ?, ?)",
        (key, str(block_number), int(block_number), ts),
    )
    conn.commit()


def _iter_block_ranges(from_block: int, to_block: int, batch_blocks: int):
    current = from_block
    while current <= to_block:
        end = min(current + batch_blocks - 1, to_block)
        yield current, end
        current = end + 1


def _fetch_logs_for_range(w3, address: str, topic0: bytes, from_block: int, to_block: int) -> List[Dict]:
    last_err: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            logs = w3.eth.get_logs(
                {
                    "address": Web3.to_checksum_address(address),
                    "topics": [topic0],
                    "fromBlock": from_block,
                    "toBlock": to_block,
                }
            )
            return [dict(log) for log in logs]
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY ** (attempt + 1))
    if last_err is not None:
        raise last_err
    return []


def _decode_token_registered_log(log: Dict) -> Optional[Dict[str, str]]:
    topics = log.get("topics") or []
    if len(topics) < 4:
        return None
    return {
        "token0": str(_topic_to_int(topics[1])),
        "token1": str(_topic_to_int(topics[2])),
        "condition_id": _topic_to_hex(topics[3]),
    }


def fetch_market_by_condition_id_from_clob(condition_id: str) -> Optional[Dict]:
    cid = _ensure_0x(condition_id)
    if not cid:
        return None
    if str(os.environ.get("POLYDATA_MARKET_BACKFILL_SKIP_HTTP") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    try:
        resp = _get_session().get(f"{CLOB_API_BASE}/markets/{cid}", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = _response_json(resp, f"GET {CLOB_API_BASE}/markets/{cid}")
        resp.close()
    except Exception as e:
        _invalidate_session(str(e))
        return None
    if not isinstance(data, dict):
        return None
    slug = data.get("market_slug") or data.get("slug")
    # CLOB market payload often lacks Gamma's canonical market id even when the slug exists.
    # Hydrate from Gamma by slug first so downstream upserts can keep gamma_market_id/questionID.
    if slug:
        gamma_market = fetch_market_by_slug(str(slug).strip())
        if isinstance(gamma_market, dict):
            merged = dict(data)
            for key, value in gamma_market.items():
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                if isinstance(value, (list, dict)) and not value:
                    continue
                merged[key] = value
            data = merged
    data["id"] = data.get("id") or data.get("market_id")
    data["conditionId"] = data.get("condition_id") or cid
    data["questionId"] = data.get("question_id")
    if data.get("question_id") and not data.get("questionID"):
        data["questionID"] = data.get("question_id")
    data["slug"] = data.get("market_slug") or data.get("slug") or f"market-{cid[2:18]}"
    data["createdAt"] = data.get("createdAt") or data.get("created_at")
    data["endDate"] = data.get("endDate") or data.get("end_date") or data.get("end_date_iso")
    data["negRisk"] = data.get("negRisk") if data.get("negRisk") is not None else data.get("neg_risk")
    return data


def _normalize_clob_token_id_list(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return _normalize_clob_token_id_list(parsed)
        except Exception:
            numeric_tokens = re.findall(r"\d{20,}", text)
            return list(dict.fromkeys(numeric_tokens))
    if isinstance(value, dict):
        token = (
            value.get("tokenId")
            or value.get("token_id")
            or value.get("asset_id")
            or value.get("t")
        )
        return [str(token)] if token not in (None, "") else []
    if isinstance(value, list):
        if value and all(isinstance(item, str) and len(item) == 1 for item in value):
            reparsed = _normalize_clob_token_id_list("".join(value))
            if reparsed:
                return reparsed
        tokens: List[str] = []
        for item in value:
            if isinstance(item, dict):
                tokens.extend(_normalize_clob_token_id_list(item))
            elif item not in (None, ""):
                tokens.append(str(item))
        return list(dict.fromkeys(token for token in tokens if token))
    return [str(value)]


def _fetch_tags_endpoint(url: str) -> List[str]:
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        resp.close()
    except Exception:
        return []
    return _normalize_tags_payload(data)


def _fetch_market_tags_by_gamma_market_id(gamma_market_id: Any) -> List[str]:
    gamma_id = normalize_gamma_market_id(gamma_market_id)
    if not gamma_id:
        return []
    return _fetch_tags_endpoint(gamma_market_url(GAMMA_API_BASE, gamma_id, "tags"))


def _fetch_event_tags_by_id(event_id: Any) -> List[str]:
    event_id_text = str(event_id or "").strip()
    if not event_id_text:
        return []
    return _fetch_tags_endpoint(f"{GAMMA_EVENTS_URL}/{event_id_text}/tags")


def _fetch_clob_tags_by_condition_id(condition_id: Any) -> List[str]:
    cid = _ensure_0x(str(condition_id or "").strip())
    if not cid:
        return []
    try:
        resp = requests.get(f"{CLOB_API_BASE}/markets/{cid}", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        resp.close()
    except Exception:
        return []
    return _normalize_tags_payload((data or {}).get("tags") if isinstance(data, dict) else None)


def _needs_taxonomy_hydration(m: Dict) -> bool:
    gamma_market_id = str(m.get("id") or m.get("gamma_market_id") or "").strip()
    tags = _normalize_tags_payload(
        m.get("_event_tags")
        or m.get("tags")
        or (((m.get("events") or [{}])[0]).get("tags") if isinstance(m.get("events"), list) and m.get("events") else None)
    )
    return not gamma_market_id or not tags


def _hydrate_market_taxonomy_and_ids(m: Dict) -> Dict:
    gamma_market_id = str(m.get("id") or m.get("gamma_market_id") or "").strip()
    if gamma_market_id and not m.get("id"):
        m["id"] = gamma_market_id

    existing_tags = _normalize_tags_payload(
        m.get("_event_tags")
        or m.get("tags")
        or (((m.get("events") or [{}])[0]).get("tags") if isinstance(m.get("events"), list) and m.get("events") else None)
    )
    event_id = m.get("_event_id")
    if not event_id and isinstance(m.get("events"), list) and m.get("events"):
        event_id = (m.get("events") or [{}])[0].get("id")

    if not existing_tags and gamma_market_id:
        existing_tags = _fetch_market_tags_by_gamma_market_id(gamma_market_id)
    if not existing_tags and event_id:
        existing_tags = _fetch_event_tags_by_id(event_id)
    if not existing_tags:
        existing_tags = _fetch_clob_tags_by_condition_id(m.get("conditionId") or m.get("condition_id"))

    if existing_tags:
        m["_event_tags"] = existing_tags
        if not m.get("tags"):
            m["tags"] = list(existing_tags)

    if not (m.get("_event_category") or m.get("category")):
        derived_category = _derive_category_from_tags(existing_tags)
        if derived_category:
            m["_event_category"] = derived_category
    return m


def _normalize_market_record(m: Dict) -> Optional[Dict]:
    _hydrate_market_taxonomy_and_ids(m)
    return normalize_market_from_gamma(m)


def _token_lookup_candidates(token_id: Any) -> List[str]:
    text = str(token_id or "").strip()
    if not text:
        return []
    candidates = [text]
    lowered = text.lower()
    body = lowered[2:] if lowered.startswith("0x") else lowered
    if body and not body.isdigit():
        try:
            candidates.append(str(int(body, 16)))
        except Exception:
            pass
    deduped: List[str] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = str(item or "").strip()
        if normalized and normalized not in seen:
            deduped.append(normalized)
            seen.add(normalized)
    return deduped


def _fetch_market_by_token_from_clob_once(token: str) -> Optional[Dict]:
    token = str(token or "").strip()
    if not token:
        return None
    if str(os.environ.get("POLYDATA_MARKET_BACKFILL_SKIP_HTTP") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    try:
        resp = _get_session().get(f"{CLOB_API_BASE}/markets-by-token/{token}", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        token_market = _response_json(resp, f"GET {CLOB_API_BASE}/markets-by-token/{token}")
        resp.close()
    except Exception as e:
        _invalidate_session(str(e))
        print(f"fetch_market_by_token_from_clob({token[:30]}...): {e}", file=sys.stderr)
        return None
    if not isinstance(token_market, dict):
        return None
    condition_id = token_market.get("condition_id") or token_market.get("conditionId")
    if not condition_id:
        return None
    market = fetch_market_by_condition_id_from_clob(str(condition_id))
    if not isinstance(market, dict):
        return None
    primary = token_market.get("primary_token_id") or token_market.get("primaryTokenId")
    secondary = token_market.get("secondary_token_id") or token_market.get("secondaryTokenId")
    merged_tokens: List[str] = _normalize_clob_token_id_list(
        market.get("clobTokenIds") or market.get("clob_token_ids")
    )
    for item in (primary, secondary):
        if item is not None:
            merged_tokens.append(str(item))
    if merged_tokens:
        market["clobTokenIds"] = list(dict.fromkeys(token for token in merged_tokens if token))
    return market


def fetch_market_by_token_from_clob(token_id: str) -> Optional[Dict]:
    for token in _token_lookup_candidates(token_id):
        market = _fetch_market_by_token_from_clob_once(token)
        if market:
            return market
    return None


def fetch_and_upsert_markets_by_token_ids_via_clob_token_lookup(
    token_ids: List[str],
    db_path: str,
) -> int:
    if not token_ids:
        return 0
    seen_cid: set = set()
    norms: List[Dict] = []
    normalized_tokens: List[str] = []
    for token_id in token_ids:
        normalized_tokens.extend(_token_lookup_candidates(token_id))
    for token_id in dict.fromkeys(normalized_tokens):
        raw = fetch_market_by_token_from_clob(token_id)
        if not raw:
            continue
        cid = raw.get("conditionId") or raw.get("condition_id")
        if cid and cid in seen_cid:
            continue
        if cid:
            seen_cid.add(cid)
        raw.setdefault("_event_neg_risk", raw.get("negRisk", False))
        raw.setdefault("_event_slug", raw.get("slug", ""))
        norm = _normalize_market_record(raw)
        if norm:
            norms.append(norm)
    if not norms:
        return 0
    conn = get_connection(db_path)
    try:
        return batch_upsert_markets(conn, norms)
    finally:
        conn.close()


def _build_minimal_market_from_registry(
    condition_id: str,
    token0: str,
    token1: str,
    exchange_name: str,
) -> Dict:
    token_ids = sorted([str(token0), str(token1)])
    cid = _ensure_0x(condition_id)
    recovered_at = datetime.now(timezone.utc).isoformat()
    return {
        "condition_id": cid,
        "question_id": None,
        "oracle": None,
        "slug": f"onchain-{exchange_name}-{cid[2:18]}",
        "title": f"On-chain recovered market {cid[:18]}",
        "description": "Recovered from Polymarket exchange registry because Gamma/CLOB discovery missed this market.",
        "yes_token_id": token_ids[0],
        "no_token_id": token_ids[1],
        "clob_token_ids": token_ids,
        "enable_neg_risk": 1 if exchange_name == "neg_risk" else 0,
        "created_at": recovered_at,
        "end_date": None,
        "category": "",
        "tags": ["onchain-registry"],
        "gamma_market_id": "",
    }


def _real_market_filter_sql(alias: str = "markets") -> str:
    return (
        f"COALESCE({alias}.category, '') <> 'orderfilled-placeholder' "
        f"AND COALESCE({alias}.slug, '') NOT LIKE 'trade-indexer-placeholder-%%'"
    )


def _recompute_resolved_token_ids(db_path: str, *, real_only: bool = False) -> set:
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        where = "WHERE yes_token_id IS NOT NULL AND no_token_id IS NOT NULL"
        if real_only:
            where += f" AND {_real_market_filter_sql('markets')}"
        cur.execute(f"SELECT yes_token_id, no_token_id FROM markets {where}")
        resolved = set()
        for row in cur.fetchall():
            if row[0]:
                resolved.add(str(row[0]))
            if row[1]:
                resolved.add(str(row[1]))
        return resolved
    finally:
        conn.close()


def _resolved_token_ids_for(token_ids: List[str], db_path: str, *, real_only: bool = False) -> set:
    tokens: List[str] = []
    for token_id in token_ids:
        tokens.extend(_token_lookup_candidates(token_id))
    tokens = list(dict.fromkeys(tokens))
    if not tokens:
        return set()
    resolved: set = set()
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        for start in range(0, len(tokens), 500):
            chunk = tokens[start : start + 500]
            placeholders = ",".join(["?"] * len(chunk))
            cur.execute(
                f"""
                SELECT yes_token_id, no_token_id
                FROM markets
                WHERE (
                    yes_token_id IN ({placeholders})
                    OR no_token_id IN ({placeholders})
                    OR EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements_text(clob_token_ids) AS token(value)
                        WHERE token.value IN ({placeholders})
                    )
                )
                  {"AND " + _real_market_filter_sql("markets") if real_only else ""}
                """,
                tuple(chunk + chunk + chunk),
            )
            for row in cur.fetchall():
                if row[0]:
                    resolved.add(str(row[0]))
                if row[1]:
                    resolved.add(str(row[1]))
    finally:
        conn.close()
    return resolved


def fetch_and_upsert_markets_for_token_ids_via_onchain(
    token_ids: List[str],
    db_path: str,
    rpc_url: Optional[str] = None,
) -> int:
    if not token_ids:
        return 0
    try:
        w3 = _build_web3(rpc_url)
    except Exception as e:
        print(f"fetch_and_upsert_markets_for_token_ids_via_onchain: {e}", file=sys.stderr)
        return 0

    abi = [
        {
            "inputs": [{"internalType": "uint256", "name": "token", "type": "uint256"}],
            "name": "getConditionId",
            "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [{"internalType": "uint256", "name": "token", "type": "uint256"}],
            "name": "getComplement",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function",
        },
    ]

    seen_conditions: set = set()
    norms: List[Dict] = []
    normalized_tokens: List[str] = []
    for token_id in token_ids:
        normalized_tokens.extend(_token_lookup_candidates(token_id))
    for token_id in dict.fromkeys(normalized_tokens):
        try:
            token_int = int(token_id)
        except ValueError:
            continue
        for exchange_name, exchange_address, _sync_key in CHAIN_REGISTRY_EVENTS:
            contract = w3.eth.contract(address=Web3.to_checksum_address(exchange_address), abi=abi)
            try:
                condition_bytes = contract.functions.getConditionId(token_int).call()
                complement = str(contract.functions.getComplement(token_int).call())
            except Exception:
                continue
            condition_id = _ensure_0x(condition_bytes.hex() if hasattr(condition_bytes, "hex") else str(condition_bytes))
            if not condition_id or condition_id in seen_conditions:
                continue
            raw = fetch_market_by_condition_id_from_clob(condition_id)
            if raw:
                norm = _normalize_market_record(raw)
            else:
                norm = _build_minimal_market_from_registry(condition_id, token_id, complement, exchange_name)
            if norm:
                seen_conditions.add(condition_id)
                norms.append(norm)
            break

    if not norms:
        return 0

    conn = get_connection(db_path)
    try:
        return batch_upsert_markets(conn, norms)
    finally:
        conn.close()


def supplement_missing_markets_from_onchain_registry(
    db_path: str,
    rpc_url: Optional[str] = None,
    batch_blocks: int = DEFAULT_CHAIN_BATCH_BLOCKS,
    requests_delay: float = DEFAULT_CHAIN_REQUESTS_DELAY,
) -> int:
    try:
        w3 = _build_web3(rpc_url)
    except Exception as e:
        print(f"[onchain] registry supplement disabled: {e}", file=sys.stderr)
        return 0

    topic0 = _token_registered_topic0()
    if topic0 is None:
        return 0

    latest_block = int(w3.eth.block_number)
    conn = get_connection(db_path)
    try:
        seen_conditions = _load_existing_condition_ids(conn)
        total_written = 0
        for exchange_name, exchange_address, sync_key in CHAIN_REGISTRY_EVENTS:
            start_block = (_get_sync_last_block(conn, sync_key) or -1) + 1
            if start_block > latest_block:
                continue
            print(
                f"[onchain] scanning {exchange_name} registry blocks {start_block}-{latest_block} (batch={batch_blocks}) ...",
                file=sys.stderr,
            )
            for range_start, range_end in _iter_block_ranges(start_block, latest_block, batch_blocks):
                try:
                    logs = _fetch_logs_for_range(w3, exchange_address, topic0, range_start, range_end)
                except Exception as e:
                    print(
                        f"[onchain] {exchange_name} registry logs failed for blocks {range_start}-{range_end}: {e}",
                        file=sys.stderr,
                    )
                    _save_sync_last_block(conn, sync_key, range_start - 1)
                    return total_written

                norms: List[Dict] = []
                for log in logs:
                    decoded = _decode_token_registered_log(log)
                    if not decoded:
                        continue
                    condition_id = decoded["condition_id"]
                    if condition_id in seen_conditions:
                        continue
                    raw = fetch_market_by_condition_id_from_clob(condition_id)
                    if raw:
                        norm = _normalize_market_record(raw)
                    else:
                        norm = _build_minimal_market_from_registry(
                            condition_id,
                            decoded["token0"],
                            decoded["token1"],
                            exchange_name,
                        )
                    if not norm:
                        continue
                    seen_conditions.add(condition_id)
                    norms.append(norm)

                if norms:
                    total_written += batch_upsert_markets(conn, norms)
                    print(
                        f"[onchain] {exchange_name} wrote {len(norms)} missing markets from blocks {range_start}-{range_end} (total {total_written})",
                        file=sys.stderr,
                    )

                _save_sync_last_block(conn, sync_key, range_end)
                if requests_delay > 0:
                    time.sleep(requests_delay)
        return total_written
    finally:
        conn.close()

# 默认请求间隔（秒）
DEFAULT_REQUESTS_DELAY = 0.7
# 重试次数与指数退避基数
MAX_RETRIES = max(1, int(os.environ.get("POLYDATA_MARKET_HTTP_RETRIES", "5")))
RETRY_BASE_DELAY = max(1, int(os.environ.get("POLYDATA_MARKET_HTTP_RETRY_BASE_DELAY", "2")))
# 每 N 次请求后主动关闭并重建 Session，清除累积的僵死连接
SESSION_RECYCLE_EVERY = 200
# 请求超时：(连接超时秒, 读取超时秒)；分离两阶段，避免慢响应时无限等待。
REQUEST_TIMEOUT = (
    int(os.environ.get("POLYDATA_MARKET_HTTP_CONNECT_TIMEOUT", "10")),
    int(os.environ.get("POLYDATA_MARKET_HTTP_READ_TIMEOUT", "45")),
)

SYNC_KEY_CLOSED_EVENTS_OFFSET = "closed_events_offset"
# 记录最后一次 market discovery 的完成时间（ISO 字符串），用于增量同步起点
SYNC_KEY_LAST_DISCOVERY_AT = "last_market_discovery_at"


def _create_session() -> requests.Session:
    """创建可复用连接的 Session，减轻长运行时的连接池耗尽与 SSL 断连"""
    session = requests.Session()
    # 默认不要继承 shell 里的 HTTP(S)_PROXY/ALL_PROXY，避免 market backfill
    # 在本机分析/修复任务里悄悄绕到 clash 或其他代理。
    # 如需显式走代理，再设置 POLYDATA_MARKET_HTTP_TRUST_ENV_PROXY=1。
    session.trust_env = str(os.environ.get("POLYDATA_MARKET_HTTP_TRUST_ENV_PROXY", "0")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    session.headers.update({"Connection": "close", "User-Agent": "polydata-market-discovery/1.0"})
    retries = Retry(
        total=MAX_RETRIES,
        backoff_factor=RETRY_BASE_DELAY,
        # 包含 429 以自动处理限速；urllib3 会尊重 Retry-After 头
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    # 顺序爬取只需极少连接；过大的连接池在长时间运行中反而会堆积僵死连接
    adapter = HTTPAdapter(max_retries=retries, pool_connections=2, pool_maxsize=4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# 模块级 Session，复用 TCP 连接，避免每次 requests.get 重新握手
_HTTP_SESSION: Optional[requests.Session] = None
# 当前 Session 已使用的请求次数，超过阈值后主动回收
_HTTP_SESSION_REQUESTS: int = 0


def _get_session(recycle_every: int = SESSION_RECYCLE_EVERY) -> requests.Session:
    """返回可用的 Session；超过 recycle_every 次请求后自动关闭旧 Session 并新建，
    防止长时间爬取时僵死连接在池里堆积导致卡顿。"""
    global _HTTP_SESSION, _HTTP_SESSION_REQUESTS
    if _HTTP_SESSION is None or _HTTP_SESSION_REQUESTS >= recycle_every:
        if _HTTP_SESSION is not None:
            try:
                _HTTP_SESSION.close()
            except Exception:
                pass
            print(
                f"  [session] Recycled after {_HTTP_SESSION_REQUESTS} requests (threshold={recycle_every})",
                file=sys.stderr,
            )
        _HTTP_SESSION = _create_session()
        _HTTP_SESSION_REQUESTS = 0
    return _HTTP_SESSION


def _invalidate_session(reason: Optional[str] = None) -> None:
    """丢弃当前共享 Session，避免损坏的 keep-alive 连接被继续复用。"""
    global _HTTP_SESSION, _HTTP_SESSION_REQUESTS
    if _HTTP_SESSION is not None:
        try:
            _HTTP_SESSION.close()
        except Exception:
            pass
    _HTTP_SESSION = None
    _HTTP_SESSION_REQUESTS = 0
    if reason:
        print(f"  [session] Invalidated: {reason}", file=sys.stderr)


def _response_json(resp: requests.Response, context: str) -> Any:
    """统一 JSON 解析，遇到截断/脏响应时带上上下文。"""
    try:
        return resp.json()
    except Exception as e:
        try:
            body_prefix = resp.text[:300].replace("\n", "\\n")
        except Exception:
            body_prefix = "<unavailable>"
        raise RuntimeError(f"{context}: invalid JSON response: {e}; body_prefix={body_prefix!r}") from e


def _fetch_with_retry(
    url: str,
    params: Dict,
    max_retries: int = MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY,
    session_recycle_every: int = SESSION_RECYCLE_EVERY,
) -> Any:
    """带指数退避的 API 请求，失败时重试。

    - 每次请求递增全局计数，触发阈值后自动回收 Session
    - 使用 (connect, read) 分离超时，避免慢响应无限阻塞
    - 显式关闭响应以确保连接归还到池
    """
    global _HTTP_SESSION_REQUESTS
    last_err = None
    for attempt in range(max_retries):
        session = _get_session(recycle_every=session_recycle_every)
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            _HTTP_SESSION_REQUESTS += 1
            resp.raise_for_status()
            data = _response_json(resp, f"GET {url}")
            resp.close()
            return data
        except Exception as e:
            last_err = e
            _invalidate_session(str(e))
            try:
                resp.close()  # type: ignore[union-attr]
            except Exception:
                pass
            if attempt < max_retries - 1:
                delay = base_delay ** (attempt + 1)
                print(f"  Retry {attempt + 1}/{max_retries} in {delay}s: {e}", file=sys.stderr)
                time.sleep(delay)
    raise last_err


def _load_existing_condition_ids(conn) -> set:
    """从 DB 加载已有市场的 condition_id，用于去重"""
    cur = conn.cursor()
    cur.execute("SELECT condition_id FROM markets")
    return {row[0] for row in cur.fetchall()}


def _get_closed_events_offset(conn) -> Optional[int]:
    """获取上次 closed events 同步到的 offset，0 或空表示从头开始"""
    cur = conn.cursor()
    cur.execute("SELECT value FROM sync_state WHERE key = ?", (SYNC_KEY_CLOSED_EVENTS_OFFSET,))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    val = int(row[0])
    return val if val > 0 else None  # 0 表示已完成，下次从头


def _save_closed_events_offset(conn, offset: int) -> None:
    """保存 closed events 同步进度"""
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO sync_state (key, value, updated_at) VALUES (?, ?, ?)",
        (SYNC_KEY_CLOSED_EVENTS_OFFSET, str(offset), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _get_latest_market_created_at(conn) -> Optional[datetime]:
    """从 markets 表读取最新的 created_at，作为增量同步起点。
    若表为空返回 None（触发全量拉取）。"""
    cur = conn.cursor()
    cur.execute("SELECT MAX(created_at) FROM markets WHERE created_at IS NOT NULL")
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return _parse_iso_date(row[0])


def _save_last_discovery_at(conn, dt: datetime, sync_state_key: str = SYNC_KEY_LAST_DISCOVERY_AT) -> None:
    """将本次同步完成时间写入 sync_state，供下次增量同步读取。"""
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO sync_state (key, value, updated_at) VALUES (?, ?, ?)",
        (sync_state_key, dt.isoformat(), dt.isoformat()),
    )
    conn.commit()


def _get_last_discovery_at(conn, sync_state_key: str = SYNC_KEY_LAST_DISCOVERY_AT) -> Optional[datetime]:
    """从 sync_state 读取上次成功完成的时间戳。"""
    cur = conn.cursor()
    cur.execute("SELECT value FROM sync_state WHERE key = ?", (sync_state_key,))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return _parse_iso_date(row[0])


def resolve_incremental_since_date(
    db_path: str,
    sync_state_key: str = SYNC_KEY_LAST_DISCOVERY_AT,
    fallback_sync_state_keys: Optional[List[str]] = None,
    fallback_to_latest_created_at: bool = True,
) -> Optional[datetime]:
    """推断增量同步的起始日期，优先取 sync_state 记录的上次完成时间，
    若无记录则回退到 DB 中最新市场的 created_at，两者均无则返回 None（全量）。"""
    init_schema(db_path=db_path)
    conn = get_connection(db_path)
    try:
        since = _get_last_discovery_at(conn, sync_state_key=sync_state_key)
        if since is not None:
            return since
        for fallback_key in fallback_sync_state_keys or []:
            since = _get_last_discovery_at(conn, sync_state_key=fallback_key)
            if since is not None:
                return since
        if fallback_to_latest_created_at:
            return _get_latest_market_created_at(conn)
        return None
    finally:
        conn.close()


def fetch_all_markets(
    limit: int = 500,
    active_only: bool = False,
    closed_only: bool = False,
    db_path: Optional[str] = None,
    batch_size: int = 500,
    requests_delay: float = DEFAULT_REQUESTS_DELAY,
    progress_every_pages: int = 10,
) -> List[Dict]:
    """
    从 Gamma API 获取市场列表。

    重要：改用 GET /events 而非 GET /markets 作为主入口。
    原因：GET /markets 的响应里 tags 字段永远为 null，category 在新市场也通常为 null。
    category 和 tags 只存在于 GET /events 返回的顶层 event 对象上，
    必须通过 _attach_event_meta_to_market(m, ev) 把 event 的字段挂到 market 上。

    Args:
        limit: 最大获取数量
        active_only: 是否仅获取活跃市场
        closed_only: 是否仅获取已结束市场（closed=true）
        db_path: 若指定则边抓边写入数据库（避免全量时无输出+内存爆）
        batch_size: 与 db_path 联用的批量写入大小
        requests_delay: 每次请求间隔（秒）
        progress_every_pages: 每多少页打印一次进度

    Returns:
        市场列表（streaming-to-DB 模式下返回空列表，total_written 不为零）
    """
    if not active_only and not closed_only:
        ancient = datetime(1970, 1, 1, tzinfo=timezone.utc)
        markets, _written, _max_created_at = fetch_markets_since_date(
            since_date=ancient,
            include_active=True,
            include_closed=True,
            db_path=db_path,
            batch_size=batch_size,
            requests_delay=requests_delay,
        )
        return markets[:limit]

    # closed_only 走 events?closed=true，已有专用函数
    if closed_only:
        return fetch_closed_markets(limit=limit)

    # 使用 GET /events（而非 GET /markets）以获取 tags 字段
    # GET /markets 响应里 tags 永远为 null，只有 GET /events 的顶层 event 有 tags
    params: Dict[str, Any] = {
        "limit": 100,
        "ascending": "false",
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
    }

    all_markets: List[Dict] = []
    offset = 0
    pages = 0
    buffer: List[Dict] = []
    total_written = 0
    conn = None
    seen_ids: set = set()

    if db_path:
        init_schema(db_path=db_path)
        conn = get_connection(db_path)
        seen_ids = _load_existing_condition_ids(conn)
        if seen_ids:
            print(
                f"  Loaded {len(seen_ids)} existing markets from DB (will skip duplicates)",
                file=sys.stderr,
            )

    collected = 0
    while collected < limit:
        params["offset"] = offset
        try:
            time.sleep(requests_delay)
            data = _fetch_with_retry(GAMMA_EVENTS_URL, dict(params))
        except Exception as e:
            print(f"Error fetching events: {e}", file=sys.stderr)
            break

        if not isinstance(data, list):
            data = data.get("events", []) if isinstance(data, dict) else []

        if not data:
            break
        pages += 1

        for ev in data:
            for m in ev.get("markets", []):
                cid = m.get("conditionId")
                if not cid:
                    continue
                # 把 event 的 category/tags 挂到 market 上
                _attach_event_meta_to_market(m, ev)
                if conn is not None:
                    if cid in seen_ids:
                        continue
                    seen_ids.add(cid)
                    buffer.append(m)
                    collected += 1
                    if len(buffer) >= batch_size:
                        buffer, total_written = _flush_buffer_to_db(
                            buffer, conn, total_written, batch_size
                        )
                else:
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        all_markets.append(m)
                        collected += 1

        offset += len(data)
        if len(data) < 100:
            break

        if progress_every_pages and pages % progress_every_pages == 0:
            if conn is not None:
                print(
                    f"  ... fetched events pages={pages}, offset={offset}, written={total_written}, buffered={len(buffer)}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  ... fetched events pages={pages}, offset={offset}, collected={len(all_markets)}",
                    file=sys.stderr,
                )

    if conn is not None:
        if buffer:
            _, total_written = _flush_buffer_to_db(buffer, conn, total_written, batch_size)
        try:
            conn.close()
        except Exception:
            pass
        print(
            f"  Done streaming markets to DB. Total written={total_written}, pages={pages}, offset={offset}",
            file=sys.stderr,
        )
        return []

    return all_markets[:limit]


def _parse_iso_date(s: Optional[str]) -> Optional[datetime]:
    """解析 ISO 日期字符串为 datetime，失败返回 None"""
    if not s:
        return None
    try:
        clean = str(s).split(".")[0].split("+")[0].replace("Z", "").strip()
        return datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _is_postgres_target() -> bool:
    return get_backend() in {"postgres", "postgresql"}


def _json_for_db(value: Any) -> Any:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = []
    else:
        parsed = value
    if parsed is None:
        parsed = []
    if not isinstance(parsed, list):
        parsed = [parsed]
    if _is_postgres_target() and Jsonb is not None:
        return Jsonb(parsed)
    return json.dumps(parsed, ensure_ascii=False)


def _datetime_for_db(value: Any) -> Any:
    if not _is_postgres_target():
        return value
    if isinstance(value, datetime):
        return value
    return _parse_iso_date(str(value)) if value is not None else None


def _bool_for_db(value: Any) -> Any:
    if isinstance(value, bool):
        flag = value
    elif isinstance(value, (int, float)):
        flag = value != 0
    elif isinstance(value, str):
        flag = value.strip().lower() in {"1", "true", "t", "yes", "y"}
    else:
        flag = bool(value)
    return flag if _is_postgres_target() else int(flag)


def _decimal_text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return format(Decimal(str(value)), "f")
    except (InvalidOperation, ValueError, TypeError):
        return None


def _probability_text(value: Any) -> Optional[str]:
    text = _decimal_text(value)
    if text is None:
        return None
    try:
        probability = Decimal(text)
    except InvalidOperation:
        return None
    if probability < 0 or probability > 1:
        return None
    return format(probability, "f")


def _gamma_outcome_prices(market: Dict) -> List[Any]:
    raw = market.get("outcomePrices") or market.get("outcome_prices")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    return raw if isinstance(raw, list) else []


def _gamma_yes_no_prices(market: Dict) -> Tuple[Optional[str], Optional[str]]:
    prices = _gamma_outcome_prices(market)
    yes_price = _probability_text(prices[0]) if prices else None
    no_price = _probability_text(prices[1]) if len(prices) > 1 else None
    if yes_price is None:
        yes_price = _probability_text(
            market.get("lastTradePrice")
            or market.get("last_trade_price")
            or market.get("bestAsk")
            or market.get("bestBid")
        )
    if no_price is None and yes_price is not None:
        no_price = format(Decimal("1") - Decimal(yes_price), "f")
    return yes_price, no_price


def _gamma_price_24h_ago(yes_price: Optional[str], market: Dict) -> Optional[str]:
    if yes_price is None:
        return None
    change = _decimal_text(market.get("oneDayPriceChange") or market.get("one_day_price_change"))
    if change is None:
        return None
    try:
        price = Decimal(yes_price) - Decimal(change)
    except InvalidOperation:
        return None
    if price < 0 or price > 1:
        return None
    return format(price, "f")


def _gamma_volume_24h(market: Dict) -> str:
    return _decimal_text(market.get("volume24hr") or market.get("volume_24hr") or market.get("volume24h")) or "0"


def _upsert_market_serving_from_gamma(conn, markets: List[Dict]) -> int:
    if not markets:
        return 0
    try:
        if not table_exists(conn, "market_latest_prices") or not table_exists(conn, "market_list_serving"):
            return 0
    except Exception:
        return 0

    by_condition = {
        str(market.get("condition_id") or "").strip(): market
        for market in markets
        if str(market.get("condition_id") or "").strip()
    }
    if not by_condition:
        return 0

    conditions = sorted(by_condition)
    market_id_by_condition: Dict[str, int] = {}
    for start in range(0, len(conditions), 500):
        chunk = conditions[start:start + 500]
        placeholders = ",".join(["?"] * len(chunk))
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, condition_id FROM markets WHERE condition_id IN ({placeholders})",
            tuple(chunk),
        )
        for row in cur.fetchall():
            market_id_by_condition[str(row[1])] = int(row[0])

    latest_rows: List[Tuple[Any, ...]] = []
    serving_rows: List[Tuple[Any, ...]] = []
    for condition_id, market in by_condition.items():
        market_id = market_id_by_condition.get(condition_id)
        if market_id is None:
            continue
        latest_yes = market.get("_gamma_latest_yes_price")
        latest_no = market.get("_gamma_latest_no_price")
        latest_at = _datetime_for_db(market.get("_gamma_latest_trade_at"))
        volume_24h = market.get("_gamma_volume_24h") or "0"
        price_24h_ago = market.get("_gamma_price_24h_ago")
        if latest_yes is not None or latest_no is not None:
            latest_rows.append(
                (
                    market_id,
                    latest_at,
                    None,
                    None,
                    latest_yes,
                    latest_at,
                    None,
                    None,
                    latest_yes,
                    latest_at,
                    None,
                    None,
                    latest_no,
                )
            )
        serving_rows.append(
            (
                market_id,
                latest_yes,
                latest_at,
                price_24h_ago,
                0,
                volume_24h,
                latest_at if Decimal(str(volume_24h or "0")) > 0 else None,
            )
        )

    if latest_rows:
        conn.executemany(
            """
            INSERT INTO market_latest_prices (
                market_id,
                latest_trade_at,
                latest_trade_block,
                latest_trade_log_index,
                latest_price,
                latest_yes_trade_at,
                latest_yes_trade_block,
                latest_yes_trade_log_index,
                latest_yes_price,
                latest_no_trade_at,
                latest_no_trade_block,
                latest_no_trade_log_index,
                latest_no_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market_id) DO UPDATE SET
                latest_trade_at = COALESCE(excluded.latest_trade_at, market_latest_prices.latest_trade_at),
                latest_price = COALESCE(excluded.latest_price, market_latest_prices.latest_price),
                latest_yes_trade_at = COALESCE(excluded.latest_yes_trade_at, market_latest_prices.latest_yes_trade_at),
                latest_yes_price = COALESCE(excluded.latest_yes_price, market_latest_prices.latest_yes_price),
                latest_no_trade_at = COALESCE(excluded.latest_no_trade_at, market_latest_prices.latest_no_trade_at),
                latest_no_price = COALESCE(excluded.latest_no_price, market_latest_prices.latest_no_price),
                updated_at = CURRENT_TIMESTAMP
            """,
            latest_rows,
        )
    if serving_rows:
        conn.executemany(
            """
            INSERT INTO market_list_serving (
                market_id,
                latest_price,
                latest_trade_at,
                price_24h_ago,
                trade_count_24h,
                volume_24h,
                last_trade_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market_id) DO UPDATE SET
                latest_price = COALESCE(excluded.latest_price, market_list_serving.latest_price),
                latest_trade_at = COALESCE(excluded.latest_trade_at, market_list_serving.latest_trade_at),
                price_24h_ago = COALESCE(excluded.price_24h_ago, market_list_serving.price_24h_ago),
                trade_count_24h = CASE
                    WHEN excluded.trade_count_24h > market_list_serving.trade_count_24h THEN excluded.trade_count_24h
                    ELSE market_list_serving.trade_count_24h
                END,
                volume_24h = CASE
                    WHEN excluded.volume_24h > market_list_serving.volume_24h THEN excluded.volume_24h
                    ELSE market_list_serving.volume_24h
                END,
                last_trade_at = COALESCE(excluded.last_trade_at, market_list_serving.last_trade_at),
                updated_at = CURRENT_TIMESTAMP
            """,
            serving_rows,
        )
    conn.commit()
    return len(serving_rows)


def _get_market_created_at(market: Dict) -> Optional[datetime]:
    """统一读取 market 级 createdAt，用于增量断点推进。"""
    return _parse_iso_date(market.get("createdAt") or market.get("created_at"))


def _max_created_at_from_markets(markets: List[Dict]) -> Optional[datetime]:
    latest: Optional[datetime] = None
    for market in markets:
        created = _get_market_created_at(market)
        if created is not None and (latest is None or created > latest):
            latest = created
    return latest


def _flush_buffer_to_db(
    buffer: List[Dict],
    conn,
    total_written: int,
    batch_size: int,
) -> Tuple[List[Dict], int]:
    """将 buffer 规范化后批量写入 DB，清空 buffer。返回 (空 buffer, 新的 total_written)"""
    if not buffer:
        return buffer, total_written
    hydrate_targets = [item for item in buffer if _needs_taxonomy_hydration(item)]
    if hydrate_targets:
        with ThreadPoolExecutor(max_workers=min(12, len(hydrate_targets))) as ex:
            futures = [ex.submit(_hydrate_market_taxonomy_and_ids, item) for item in hydrate_targets]
            for future in as_completed(futures):
                future.result()
    norms = []
    for m in buffer:
        n = normalize_market_from_gamma(m)
        if n:
            norms.append(n)
    if norms:
        batch_upsert_markets(conn, norms)
        _upsert_market_serving_from_gamma(conn, norms)
        total_written += len(norms)
        print(f"  ... wrote {len(norms)} markets to DB (total {total_written})", file=sys.stderr)
    return [], total_written


def fetch_markets_since_date(
    since_date: datetime,
    include_active: bool = True,
    include_closed: bool = True,
    db_path: Optional[str] = None,
    batch_size: int = 500,
    requests_delay: float = DEFAULT_REQUESTS_DELAY,
    max_empty_closed_pages: int = 5,
) -> Tuple[List[Dict], int, Optional[datetime]]:
    """
    从指定起始日期向后爬取市场，按 market.createdAt 排序，不设 limit。
    同时拉取活跃和已关闭市场。若指定 db_path，则每 batch_size 个写入 DB 一次（去重由 ON CONFLICT 保证）。

    Args:
        since_date: 起始日期（含），只保留 created_at >= since_date 的市场
        include_active: 是否包含活跃市场
        include_closed: 是否包含已关闭市场
        db_path: 若指定，每 batch_size 个市场写入数据库
        batch_size: 每批写入数量，默认 500
        max_empty_closed_pages: 保留参数兼容旧调用；当前 market 级 createdAt 排序下不再使用

    Returns:
        (市场列表用于 JSON 输出，总写入数，实际观察到的最大 market.createdAt)。
        当 db_path 指定时列表为空，仅返回计数与断点时间。
    """
    if not include_active and not include_closed:
        return [], 0, None

    buffer: List[Dict] = []
    all_markets: List[Dict] = []
    seen_ids: set = set()
    total_written = 0
    latest_seen_created_at: Optional[datetime] = None
    conn = None
    if db_path:
        init_schema(db_path=db_path)
        conn = get_connection(db_path)
        # 加载已有 condition_id，避免与已入库数据重复
        existing = _load_existing_condition_ids(conn)
        seen_ids.update(existing)
        if existing:
            print(f"  Loaded {len(existing)} existing markets from DB (will skip duplicates)", file=sys.stderr)

    def _add_market(m: Dict) -> None:
        nonlocal buffer, total_written, latest_seen_created_at
        cid = m.get("conditionId")
        created = _get_market_created_at(m)
        if created is not None and (latest_seen_created_at is None or created > latest_seen_created_at):
            latest_seen_created_at = created
        if not cid or cid in seen_ids:
            return
        if created is None or created < since_date:
            return
        seen_ids.add(cid)
        if conn is not None:
            buffer.append(m)
            if len(buffer) >= batch_size:
                buffer, total_written = _flush_buffer_to_db(buffer, conn, total_written, batch_size)
        else:
            all_markets.append(m)

    def _scan_keyset_pages() -> None:
        cursor: Optional[str] = None
        pages = 0
        while True:
            params: Dict[str, Any] = {
                "limit": 100,
                "order": "createdAt",
                "ascending": "false",
            }
            if cursor:
                # Gamma returns next_cursor in the response; the next request must pass it as after_cursor.
                params["after_cursor"] = cursor
            time.sleep(requests_delay)
            data = _fetch_with_retry(GAMMA_MARKETS_KEYSET_URL, params)
            if not isinstance(data, dict):
                raise RuntimeError(f"GET {GAMMA_MARKETS_KEYSET_URL}: expected object response")

            batch = data.get("markets") or []
            if not isinstance(batch, list):
                raise RuntimeError(f"GET {GAMMA_MARKETS_KEYSET_URL}: expected markets list")
            if not batch:
                break

            pages += 1
            page_has_recent_market = False
            page_has_unknown_created_at = False

            for market in batch:
                if not isinstance(market, dict):
                    continue
                is_closed = bool(market.get("closed"))
                if (is_closed and not include_closed) or (not is_closed and not include_active):
                    continue
                _attach_embedded_event_meta_to_market(market)
                created = _get_market_created_at(market)
                if created is None:
                    page_has_unknown_created_at = True
                elif created >= since_date:
                    page_has_recent_market = True
                _add_market(market)

            if pages % 25 == 0:
                collected = total_written + len(buffer) if conn else len(all_markets)
                oldest = _get_market_created_at(batch[-1]) if batch else None
                print(
                    f"  ... keyset pages={pages}, collected={collected}, oldest_page_created_at={oldest}",
                    file=sys.stderr,
                )

            cursor = data.get("next_cursor") or data.get("nextCursor") or data.get("cursor")
            if not cursor:
                break

            # keyset is descending by createdAt; once an entire page is older than since_date,
            # the remaining cursor pages are older too.
            if not page_has_recent_market and not page_has_unknown_created_at:
                break

    def _scan_market_pages(label: str, params: Dict[str, str]) -> None:
        offset = 0
        while True:
            params["offset"] = offset
            time.sleep(requests_delay)
            try:
                batch = _fetch_with_retry(GAMMA_MARKETS_URL, dict(params))
            except Exception as e:
                raise RuntimeError(f"Error fetching {label} markets (offset={offset}): {e}") from e

            if not isinstance(batch, list):
                batch = batch.get("markets", []) if isinstance(batch, dict) else []

            if not batch:
                break

            page_has_recent_market = False
            page_has_unknown_created_at = False

            for market in batch:
                _attach_embedded_event_meta_to_market(market)
                created = _get_market_created_at(market)
                if created is None:
                    page_has_unknown_created_at = True
                elif created >= since_date:
                    page_has_recent_market = True
                _add_market(market)

            offset += len(batch)
            batch_len = len(batch)
            collected = total_written + len(buffer) if conn else len(all_markets)
            if offset % 500 == 0 and offset > 0:
                print(
                    f"  ... fetched {offset} from /markets({label}), collected {collected}",
                    file=sys.stderr,
                )

            # /markets 已经按 market.createdAt 倒序排列；当整页都早于 since_date 时可安全停止。
            if not page_has_recent_market and not page_has_unknown_created_at:
                break
            if batch_len < 100:
                break
            del batch

    def _scan_event_pages(label: str, params: Dict[str, Any]) -> None:
        """Scan Gamma events and flatten embedded markets.

        Some Gamma markets, especially bucketed crypto/weather event markets, are
        present under /events but do not resolve through /markets?slug. The
        /markets keyset remains the fast path; this pass closes the event-only
        coverage gap while reusing the same condition_id dedupe and DB writer.
        """
        offset = 0
        pages = 0
        while True:
            params["offset"] = offset
            time.sleep(requests_delay)
            try:
                events = _fetch_with_retry(GAMMA_EVENTS_URL, dict(params))
            except Exception as e:
                raise RuntimeError(f"Error fetching {label} events (offset={offset}): {e}") from e

            if isinstance(events, dict):
                events = events.get("events") or events.get("data") or []
            if not isinstance(events, list) or not events:
                break

            pages += 1
            page_has_recent_market = False
            page_has_unknown_created_at = False
            event_markets = 0

            for ev in events:
                if not isinstance(ev, dict):
                    continue
                markets = ev.get("markets") or []
                if not isinstance(markets, list):
                    continue
                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    is_closed = bool(market.get("closed"))
                    if (is_closed and not include_closed) or (not is_closed and not include_active):
                        continue
                    _attach_event_meta_to_market(market, ev)
                    created = _get_market_created_at(market)
                    if created is None:
                        page_has_unknown_created_at = True
                    elif created >= since_date:
                        page_has_recent_market = True
                    _add_market(market)
                    event_markets += 1

            offset += len(events)
            if pages % 25 == 0:
                collected = total_written + len(buffer) if conn else len(all_markets)
                oldest_event_created = _parse_iso_date(events[-1].get("createdAt")) if events else None
                print(
                    f"  ... event pages({label})={pages}, offset={offset}, "
                    f"event_markets={event_markets}, collected={collected}, "
                    f"oldest_event_created_at={oldest_event_created}",
                    file=sys.stderr,
                )

            if not page_has_recent_market and not page_has_unknown_created_at:
                break
            if len(events) < 100:
                break
            del events

    try:
        if include_active and include_closed:
            _scan_keyset_pages()
        elif include_active:
            _scan_market_pages(
                "active",
                {
                    "limit": 100,
                    "order": "createdAt",
                    "ascending": "false",
                    "active": "true",
                    "closed": "false",
                },
            )

        if include_active:
            _scan_event_pages(
                "active",
                {
                    "limit": 100,
                    "order": "createdAt",
                    "ascending": "false",
                    "closed": "false",
                },
            )

        if include_closed and not include_active:
            _scan_market_pages(
                "closed",
                {
                    "limit": 100,
                    "order": "createdAt",
                    "ascending": "false",
                    "closed": "true",
                },
            )

        if include_closed:
            _scan_event_pages(
                "closed",
                {
                    "limit": 100,
                    "order": "createdAt",
                    "ascending": "false",
                    "closed": "true",
                },
            )

        # 写入剩余 buffer
        if conn and buffer:
            buffer, total_written = _flush_buffer_to_db(buffer, conn, total_written, batch_size)

        if conn:
            conn.close()
            conn = None

        if all_markets:
            def _sort_key(m: Dict) -> datetime:
                d = _get_market_created_at(m)
                return d or datetime.min.replace(tzinfo=timezone.utc)

            all_markets.sort(key=_sort_key)
            return all_markets, total_written, latest_seen_created_at
        return [], total_written, latest_seen_created_at
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _run_closed_events_only(
    db_path: str,
    batch_size: int = 500,
    start_offset: Optional[int] = None,
    requests_delay: float = DEFAULT_REQUESTS_DELAY,
    cooldown_every: Optional[int] = None,
    cooldown_seconds: float = 60,
    max_fetches: Optional[int] = None,
    since_date: Optional[datetime] = None,
    session_recycle_every: int = SESSION_RECYCLE_EVERY,
) -> int:
    """仅补充拉取 closed events，支持断点续传、周期性冷却与分批上限。可选 since_date 过滤 created_at。"""
    init_schema(db_path=db_path)
    conn = get_connection(db_path)
    seen_ids = set(_load_existing_condition_ids(conn))
    print(f"  Loaded {len(seen_ids)} existing markets from DB (will skip duplicates)", file=sys.stderr)

    buffer: List[Dict] = []
    total_written = 0
    ev_offset = start_offset if start_offset is not None else _get_closed_events_offset(conn)
    ev_offset = ev_offset if ev_offset is not None and ev_offset > 0 else 0
    if ev_offset > 0:
        print(f"  Resuming closed events from offset {ev_offset}", file=sys.stderr)
    else:
        print(f"  Starting closed events fetch from offset 0", file=sys.stderr)
    if since_date is not None:
        print(f"  Filter: only markets with created_at >= {since_date.date()}", file=sys.stderr)

    def _add_market(m: Dict) -> None:
        nonlocal buffer, total_written
        cid = m.get("conditionId")
        if not cid or cid in seen_ids:
            return
        if since_date is not None:
            created = _parse_iso_date(m.get("createdAt") or m.get("created_at"))
            if created is not None and created < since_date:
                return  # 跳过早于 since_date 的市场
        seen_ids.add(cid)
        buffer.append(m)
        if len(buffer) >= batch_size:
            buffer, total_written = _flush_buffer_to_db(buffer, conn, total_written, batch_size)

    ev_params = {
        "limit": 100,
        "closed": "true",
        "order": "volume",
        "ascending": "false",
    }

    fetch_count = 0

    try:
        while True:
            if max_fetches is not None and fetch_count >= max_fetches:
                _save_closed_events_offset(conn, ev_offset)
                print(
                    f"  Reached --max-fetches {max_fetches}. Saved offset {ev_offset}. Re-run to resume.",
                    file=sys.stderr,
                )
                break

            ev_params["offset"] = ev_offset
            time.sleep(requests_delay)
            try:
                events = _fetch_with_retry(
                    GAMMA_EVENTS_URL, dict(ev_params),
                    session_recycle_every=session_recycle_every,
                )
            except Exception as e:
                print(f"Error fetching closed events (offset={ev_offset}): {e}", file=sys.stderr)
                _save_closed_events_offset(conn, ev_offset)
                print(f"  Saved progress at offset {ev_offset}. Re-run with --closed-events-only to resume.", file=sys.stderr)
                return total_written

            fetch_count += 1

            if cooldown_every is not None and cooldown_seconds > 0 and fetch_count % cooldown_every == 0 and fetch_count > 0:
                print(
                    f"  Cooldown: sleeping {cooldown_seconds}s after {fetch_count} fetches...",
                    file=sys.stderr,
                )
                time.sleep(cooldown_seconds)

            if not events or not isinstance(events, list):
                _save_closed_events_offset(conn, 0)
                break

            for ev in events:
                for m in ev.get("markets", []):
                    _attach_event_meta_to_market(m, ev)
                    _add_market(m)

            ev_offset += len(events)
            _save_closed_events_offset(conn, ev_offset)
            if len(events) < 100:
                _save_closed_events_offset(conn, 0)
                break
            if ev_offset % 500 == 0 and ev_offset > 0:
                print(f"  ... fetched {ev_offset} closed events, total {total_written + len(buffer)}", file=sys.stderr)
            del events

        if buffer:
            _, total_written = _flush_buffer_to_db(buffer, conn, total_written, batch_size)
        print(f"Done. Wrote {total_written} new closed-event markets.", file=sys.stderr)
        return total_written
    finally:
        conn.close()


def fetch_closed_markets(limit: int = 500) -> List[Dict]:
    """
    通过 /events?closed=true 获取已结束市场（/markets 不支持 closed 参数）

    Args:
        limit: 最大获取市场数量

    Returns:
        市场列表（从 events 中扁平化 markets）
    """
    url = GAMMA_EVENTS_URL
    params = {
        "limit": 100,
        "closed": "true",
        "order": "volume",  # closed_time 在 closed=true 时易 422，改用 volume
        "ascending": "false",
    }
    all_markets: List[Dict] = []
    offset = 0

    while len(all_markets) < limit:
        params["offset"] = offset
        try:
            events = _fetch_with_retry(url, dict(params))
        except Exception as e:
            print(f"Error fetching closed events: {e}", file=sys.stderr)
            break

        if not events or not isinstance(events, list):
            break

        for ev in events:
            for m in ev.get("markets", []):
                _attach_event_meta_to_market(m, ev)
                all_markets.append(m)
            if len(all_markets) >= limit:
                break

        offset += len(events)
        if len(events) < 100:
            break

    return all_markets[:limit]


def fetch_markets_by_event_slug(event_slug: str) -> List[Dict]:
    """
    按事件 slug 获取该事件下的所有市场
    
    Args:
        event_slug: 事件 slug
    
    Returns:
        市场列表
    """
    url = GAMMA_EVENTS_URL
    params = {"slug": event_slug}
    try:
        resp = _get_session().get(url, params=params, timeout=30)
        resp.raise_for_status()
        events = _response_json(resp, f"GET {url}")
        resp.close()
    except Exception as e:
        _invalidate_session(str(e))
        print(f"Error fetching event: {e}", file=sys.stderr)
        return []
    
    if not events or not isinstance(events, list):
        return []
    
    event = events[0]
    markets = event.get("markets", [])
    for m in markets:
        _attach_event_meta_to_market(m, event)

    return markets


def fetch_markets_by_clob_token_ids(token_ids: List[str]) -> List[Dict]:
    """
    按 token_id 从 Gamma API 一键查询市场（GET /markets 支持 query 参数 clob_token_ids）。
    OpenAPI: clob_token_ids 为 string 数组，返回包含任一该 token 的市场列表。
    用于缺失市场补齐，无需分页扫 /events。
    """
    if not token_ids:
        return []
    token_ids = [str(t).strip() for t in token_ids if t and str(t).strip()]
    if not token_ids:
        return []
    def _request(ids: List[str]) -> List[Dict]:
        # GET /markets 支持 clob_token_ids 数组，requests 会序列化为 clob_token_ids=id1&clob_token_ids=id2
        params: Dict[str, Any] = {
            "limit": min(100, max(len(ids) * 2, 10)),
        }
        session = _get_session()
        resp = session.get(
            GAMMA_MARKETS_URL,
            params={"clob_token_ids": ids, **params},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = _response_json(resp, f"GET {GAMMA_MARKETS_URL}")
        resp.close()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "markets" in data:
            return data.get("markets") or []
        return []

    try:
        data = _request(token_ids[:CLOB_TOKEN_IDS_BATCH])
    except Exception as e:
        _invalidate_session(str(e))
        print(f"fetch_markets_by_clob_token_ids batch failed: {e}", file=sys.stderr)
        data = []
        if len(token_ids) > 1:
            for token_id in token_ids[:CLOB_TOKEN_IDS_BATCH]:
                try:
                    data.extend(_request([token_id]))
                except Exception as item_error:
                    _invalidate_session(str(item_error))
                    print(
                        f"fetch_markets_by_clob_token_ids item failed token={str(token_id)[:30]}...: {item_error}",
                        file=sys.stderr,
                    )
    if not data and token_ids:
            print(
                f"fetch_markets_by_clob_token_ids: GET /markets returned 0 markets for {len(token_ids)} token(s), will fall back to event-scan.",
                file=sys.stderr,
            )
    return data


def _enrich_market_with_event_by_slug(m: Dict) -> None:
    """兼容旧调用：现在优先使用 tags 端点和 CLOB 做轻量补全，不再依赖 event slug 猜测。"""
    _hydrate_market_taxonomy_and_ids(m)


def fetch_market_by_slug(slug: str) -> Optional[Dict]:
    """
    按市场 slug 从 Gamma API 一键查询单个市场（文档：Fetch by Slug）。
    用于交易导入时用 market_slug 直接拉取缺失市场，无需分页扫描。
    若返回的 market 无 category/tags，会再请求 events 按 slug 补全。
    """
    if not slug or not str(slug).strip():
        return None
    slug = str(slug).strip()
    url = f"{GAMMA_MARKETS_URL}/slug/{slug}"
    try:
        resp = _get_session().get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = _response_json(resp, f"GET {url}")
        resp.close()
    except Exception as e:
        _invalidate_session(str(e))
        print(f"fetch_market_by_slug({slug!r}): {e}", file=sys.stderr)
        return None
    if not data or not isinstance(data, dict):
        return None
    _hydrate_market_taxonomy_and_ids(data)
    return data


def fetch_and_upsert_markets_for_slugs(
    slugs: List[str],
    db_path: str,
) -> int:
    """
    根据 market_slug 列表从 Gamma 一键查询并写入 DB。
    每个 slug 一次请求 GET /markets/slug/{slug}，无分页、不卡顿。
    """
    if not slugs:
        return 0
    unique_slugs = list(dict.fromkeys(s for s in (str(x).strip() for x in slugs) if s))
    if not unique_slugs:
        return 0
    conn = get_connection(db_path)
    try:
        norms: List[Dict] = []
        for slug in unique_slugs:
            slug = str(slug).strip()
            raw = fetch_market_by_slug(slug)
            if not raw:
                continue
            raw["_event_neg_risk"] = raw.get("negRisk", False)
            raw["_event_slug"] = slug
            n = _normalize_market_record(raw)
            if n:
                norms.append(n)
        if not norms:
            return 0
        return batch_upsert_markets(conn, norms)
    finally:
        conn.close()


# 每批最多传多少个 token_id 给 GET /markets?clob_token_ids=...（API 承受能力）。
# Gamma 在代理/长连接环境下偶发 SSL EOF，默认小批量能降低 live indexer 被单个长 URL 卡住的概率。
CLOB_TOKEN_IDS_BATCH = max(1, int(os.environ.get("POLYDATA_CLOB_TOKEN_IDS_BATCH", "6")))
DATA_API_BASE_URL = os.environ.get("POLYMARKET_DATA_API_BASE_URL", "https://data-api.polymarket.com")
COMBO_DATA_API_BASE_URL = os.environ.get("POLYMARKET_COMBO_DATA_API_BASE_URL", DATA_API_BASE_URL)
ORDERFILLED_EXCHANGE_ADDRESSES = {
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",
    "0xe111180000d2663c0091e4f400237545b87b996b",
    "0xe2222d279d744050d28e00520010520000310f59",
    "0xe3333700ca9d93003f00f0f71f8515005f6c00aa",
}

ORDERFILLED_ACTIVITY_SAMPLE_LIMIT = max(1, int(os.environ.get("POLYDATA_ORDERFILLED_ACTIVITY_SAMPLE_LIMIT", "2")))
ORDERFILLED_COMBO_ACTIVITY_LIMIT = min(
    500,
    max(1, int(os.environ.get("POLYDATA_ORDERFILLED_COMBO_ACTIVITY_LIMIT", "500"))),
)
ORDERFILLED_COMBO_ACTIVITY_MAX_PAGES = max(
    1,
    int(os.environ.get("POLYDATA_ORDERFILLED_COMBO_ACTIVITY_MAX_PAGES", "2")),
)


def _combo_category_from_title(title: str) -> str:
    text = str(title or "").lower()
    if any(term in text for term in ("nba", "nfl", "mlb", "nhl", "soccer", "tennis", "ufc", "wnba", "spread:", " o/u ", " vs. ")):
        return "sports"
    if any(term in text for term in ("bitcoin", "btc", "ethereum", "eth", "solana", "crypto")):
        return "crypto"
    if any(term in text for term in ("trump", "election", "senate", "president")):
        return "politics"
    return "combo"


def _combo_tokens_from_activity(activity: Dict[str, Any], token_id: str) -> Tuple[str, str]:
    # Data API combo activity exposes conditionId, but that is the market
    # condition id, not the ERC1155 CTF position id. The traded asset is one of
    # the adjacent position ids, so derive the pair from the observed token.
    token_int = int(str(token_id))
    base_int = token_int if token_int % 2 == 0 else token_int - 1
    return str(base_int), str(base_int + 1)


def _adjacent_combo_token_ids(token_id: str) -> set[str]:
    try:
        yes_token, no_token = _combo_tokens_from_activity({}, token_id)
    except Exception:
        return {str(token_id or "").strip()}
    return {yes_token, no_token}


def _combo_title_from_legs(activity: Dict[str, Any]) -> str:
    titles: List[str] = []
    for leg in activity.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        market = leg.get("market") if isinstance(leg.get("market"), dict) else {}
        title = str(market.get("title") or "").strip()
        outcome = str(market.get("outcome") or leg.get("leg_outcome_label") or "").strip()
        if title and outcome and outcome.lower() not in title.lower():
            title = f"{title}: {outcome}"
        if title:
            titles.append(title)
    return " AND ".join(titles)


def _combo_tags_from_legs(activity: Dict[str, Any]) -> List[str]:
    tags: List[str] = ["combo", "data-api-combo-activity"]
    for leg in activity.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        market = leg.get("market") if isinstance(leg.get("market"), dict) else {}
        for value in (market.get("category"), market.get("subcategory")):
            text = str(value or "").strip().lower()
            if text and text not in tags:
                tags.append(text)
        for value in market.get("tags") or []:
            text = str(value or "").strip().lower()
            if text and text not in tags:
                tags.append(text)
        event = market.get("event") if isinstance(market.get("event"), dict) else {}
        event_slug = str(event.get("event_slug") or "").strip().lower()
        if event_slug:
            if "fifwc" in event_slug and "soccer" not in tags:
                tags.append("soccer")
            if "sports" not in tags and any(item in event_slug for item in ("fifwc", "nba", "nfl", "mlb", "nhl", "ufc")):
                tags.append("sports")
    return tags


def _build_combo_market_from_combo_activity(activity: Dict[str, Any], token_id: str) -> Optional[Dict[str, Any]]:
    condition_id = str(activity.get("combo_condition_id") or activity.get("conditionId") or "").strip()
    if not condition_id:
        return None
    yes_token, no_token = _combo_tokens_from_activity(activity, token_id)
    title = _combo_title_from_legs(activity) or f"Combo market {condition_id[:18]}"
    tags = _combo_tags_from_legs(activity)
    event_slug = ""
    event_title = ""
    for leg in activity.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        market = leg.get("market") if isinstance(leg.get("market"), dict) else {}
        event = market.get("event") if isinstance(market.get("event"), dict) else {}
        event_slug = event_slug or str(event.get("event_slug") or "").strip()
        event_title = event_title or str(event.get("event_title") or "").strip()
    return {
        "gamma_market_id": "",
        "event_id": "",
        "event_slug": event_slug,
        "event_title": event_title,
        "slug": f"combo-{condition_id[2:18]}",
        "condition_id": condition_id,
        "question_id": None,
        "oracle": None,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "clob_token_ids": [yes_token, no_token],
        "title": title,
        "description": "Recovered from Polymarket combo activity for an OrderFilled combo asset.",
        "enable_neg_risk": False,
        "end_date": None,
        "created_at": activity.get("tx_dttm"),
        "category": _derive_category_from_tags(tags) or _combo_category_from_title(title),
        "tags": tags,
    }


def _non_exchange_participants(maker: Any, taker: Any) -> List[str]:
    participants: List[str] = []
    for address in (maker, taker):
        text = str(address or "").strip().lower()
        if text and text not in ORDERFILLED_EXCHANGE_ADDRESSES and text not in participants:
            participants.append(text)
    return participants


def _find_data_api_activity_trade(
    *,
    token_id: str,
    tx_hash: str,
    maker: str,
    taker: str,
    activity_timestamp: Optional[int] = None,
    timeout: float = 5.0,
) -> Optional[Dict[str, Any]]:
    token_text = str(token_id or "").strip()
    tx_text = normalize_hex(tx_hash)
    combo_pair = _adjacent_combo_token_ids(token_text)
    participants = _non_exchange_participants(maker, taker)
    if not token_text or not tx_text or not participants:
        return None

    for user in participants:
        params: Dict[str, Any] = {"user": user, "type": "TRADE", "limit": "100"}
        if activity_timestamp is not None and int(activity_timestamp) > 0:
            # Active wallets can have far more than 100 newer trades. Narrowing
            # by the on-chain block timestamp makes the exact tx lookup both
            # bounded and reliable; /activity officially supports start/end.
            timestamp = int(activity_timestamp)
            params.update(
                {
                    "limit": "500",
                    "start": str(max(0, timestamp - 600)),
                    "end": str(timestamp + 600),
                }
            )
        try:
            resp = _get_session().get(
                f"{DATA_API_BASE_URL.rstrip('/')}/activity",
                params=params,
                timeout=timeout,
            )
            resp.raise_for_status()
            payload = _response_json(resp, f"GET {DATA_API_BASE_URL}/activity")
            resp.close()
        except Exception as exc:
            print(
                f"Data API activity lookup failed for token {token_text[:30]}... "
                f"user {user[:12]}...: {exc}",
                file=sys.stderr,
            )
            continue
        if not isinstance(payload, list):
            continue
        tx_matches = 0
        asset_matches = 0
        for item in payload:
            if not isinstance(item, dict):
                continue
            item_tx_hash = normalize_hex(item.get("transactionHash"))
            asset = str(item.get("asset") or "").strip()
            if item_tx_hash == tx_text:
                tx_matches += 1
            if asset == token_text or (item.get("isCombo") and asset in combo_pair):
                asset_matches += 1
            if item_tx_hash != tx_text:
                continue
            if asset == token_text or (item.get("isCombo") and asset in combo_pair):
                return item
        if activity_timestamp is not None:
            print(
                f"Data API activity exact match missing for token {token_text[:30]}... "
                f"user {user[:12]}... rows={len(payload)} tx_matches={tx_matches} "
                f"asset_matches={asset_matches} timestamp={int(activity_timestamp)}",
                file=sys.stderr,
            )
    return None


def _find_data_api_combo_activity_trade(
    *,
    token_id: str,
    tx_hash: str,
    maker: str,
    taker: str,
    timeout: float = 10.0,
) -> Optional[Dict[str, Any]]:
    token_text = str(token_id or "").strip()
    tx_text = normalize_hex(tx_hash)
    combo_pair = _adjacent_combo_token_ids(token_text)
    participants = _non_exchange_participants(maker, taker)
    if not token_text or not tx_text or not participants:
        return None

    for user in participants:
        cursor: Optional[str] = None
        for page in range(ORDERFILLED_COMBO_ACTIVITY_MAX_PAGES):
            params: Dict[str, Any] = {
                "user": user,
                "limit": ORDERFILLED_COMBO_ACTIVITY_LIMIT,
            }
            if cursor:
                params["cursor"] = cursor
            elif page:
                params["offset"] = page * ORDERFILLED_COMBO_ACTIVITY_LIMIT
            try:
                resp = _get_session().get(
                    f"{COMBO_DATA_API_BASE_URL.rstrip('/')}/v1/activity/combos",
                    params=params,
                    timeout=timeout,
                )
                resp.raise_for_status()
                payload = _response_json(resp, f"GET {COMBO_DATA_API_BASE_URL}/v1/activity/combos")
                resp.close()
            except Exception:
                break
            if not isinstance(payload, dict):
                break
            activity = payload.get("activity") or []
            if not isinstance(activity, list):
                break
            for item in activity:
                if not isinstance(item, dict):
                    continue
                if normalize_hex(item.get("tx_hash")) != tx_text:
                    continue
                if str(item.get("combo_position_id") or "").strip() not in combo_pair:
                    continue
                return item
            pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
            cursor = str(pagination.get("next_cursor") or "").strip() or None
            if not cursor and not pagination.get("has_more"):
                break
    return None


def _upsert_market_from_activity_trade(
    *,
    token_id: str,
    tx_hash: str,
    maker: str,
    taker: str,
    db_path: str,
    activity_timestamp: Optional[int] = None,
    timeout: float = 5.0,
) -> int:
    activity = _find_data_api_activity_trade(
        token_id=token_id,
        tx_hash=tx_hash,
        maker=maker,
        taker=taker,
        activity_timestamp=activity_timestamp,
        timeout=timeout,
    )
    combo_activity: Optional[Dict[str, Any]] = None
    if not activity:
        combo_activity = _find_data_api_combo_activity_trade(
            token_id=token_id,
            tx_hash=tx_hash,
            maker=maker,
            taker=taker,
            timeout=max(timeout, 10.0),
        )
        if not combo_activity:
            return 0
        market = _build_combo_market_from_combo_activity(combo_activity, token_id)
        if not market:
            return 0
        conn = get_connection(db_path)
        try:
            return batch_upsert_markets(conn, [market])
        finally:
            conn.close()

    condition_id = str(activity.get("conditionId") or activity.get("condition_id") or "").strip()
    slug = str(activity.get("slug") or "").strip()
    is_combo_activity = bool(activity.get("isCombo"))
    raw: Optional[Dict[str, Any]] = None
    if condition_id and not is_combo_activity:
        raw = fetch_market_by_condition_id_from_clob(condition_id)
    if not raw and slug and not is_combo_activity:
        raw = fetch_market_by_slug(slug)
    if raw:
        if activity.get("eventSlug") and not raw.get("_event_slug"):
            raw["_event_slug"] = str(activity.get("eventSlug") or "").strip()
        raw["_event_neg_risk"] = raw.get("negRisk", False)
        norm = _normalize_market_record(raw)
        if norm:
            conn = get_connection(db_path)
            try:
                return batch_upsert_markets(conn, [norm])
            finally:
                conn.close()

    if not is_combo_activity or not condition_id:
        return 0

    yes_token, no_token = _combo_tokens_from_activity(activity, token_id)
    title = str(activity.get("title") or f"Combo market {condition_id[:18]}").strip()
    market = {
        "gamma_market_id": "",
        "event_id": "",
        "event_slug": str(activity.get("eventSlug") or "").strip(),
        "event_title": "",
        "slug": slug or f"combo-{condition_id[2:18]}",
        "condition_id": condition_id,
        "question_id": None,
        "oracle": None,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "clob_token_ids": [yes_token, no_token],
        "title": title,
        "description": "Recovered from Polymarket Data API activity for an OrderFilled asset.",
        "enable_neg_risk": False,
        "end_date": None,
        "created_at": None,
        "category": _combo_category_from_title(title),
        "tags": ["combo", "data-api-activity"],
    }
    conn = get_connection(db_path)
    try:
        return batch_upsert_markets(conn, [market])
    finally:
        conn.close()


def fetch_and_upsert_markets_by_token_ids_via_activity_fallback(
    token_ids: List[str],
    db_path: str,
    *,
    per_token_limit: int = ORDERFILLED_ACTIVITY_SAMPLE_LIMIT,
) -> int:
    if not token_ids or per_token_limit <= 0:
        return 0

    conn = get_connection(db_path)
    try:
        samples: List[tuple[str, str, str, str]] = []
        for token_id in dict.fromkeys(str(item).strip() for item in token_ids if str(item or "").strip()):
            cur = conn.execute(
                f"""
                SELECT token_id, tx_hash, maker, taker
                FROM {ORDERFILLED_RAW_TABLE}
                WHERE token_id = ?
                ORDER BY block_number DESC, log_index DESC
                LIMIT ?
                """,
                (token_id, int(per_token_limit)),
            )
            for row in cur.fetchall():
                samples.append(
                    (
                        str(row[0] or ""),
                        str(row[1] or ""),
                        str(row[2] or ""),
                        str(row[3] or ""),
                    )
                )
    finally:
        conn.close()

    upserted = 0
    tried: set[tuple[str, str]] = set()
    for token_id, tx_hash, maker, taker in samples:
        key = (token_id, tx_hash)
        if key in tried:
            continue
        tried.add(key)
        upserted += int(
            _upsert_market_from_activity_trade(
                token_id=token_id,
                tx_hash=tx_hash,
                maker=maker,
                taker=taker,
                db_path=db_path,
            )
            or 0
        )
    return upserted


def fetch_and_upsert_combo_market_from_activity_trade(
    *,
    token_id: str,
    tx_hash: str,
    maker: str,
    taker: str,
    db_path: str,
    timeout: float = 5.0,
) -> int:
    """Recover combo market metadata when CLOB /markets-by-token 404s.

    Polymarket combo fills emitted by the 2026 Exchange can use CTF position IDs
    that are present in Data API activity but not accepted by the CLOB token
    lookup endpoint. This fallback matches on wallet + tx_hash + asset.
    """
    return _upsert_market_from_activity_trade(
        token_id=token_id,
        tx_hash=tx_hash,
        maker=maker,
        taker=taker,
        db_path=db_path,
        timeout=timeout,
    )


def fetch_and_upsert_markets_by_token_ids_via_api(
    token_ids: List[str],
    db_path: str,
) -> int:
    """
    用 Gamma GET /markets?clob_token_ids=... 按 token_id 一键查询并写入 DB。
    分批请求（每批 CLOB_TOKEN_IDS_BATCH 个），避免漏掉大量缺失 token。
    """
    if not token_ids:
        return 0
    seen_cid: set = set()
    raw_list: List[Dict] = []
    normalized_tokens: List[str] = []
    for token_id in token_ids:
        normalized_tokens.extend(_token_lookup_candidates(token_id))
    normalized_tokens = list(dict.fromkeys(normalized_tokens))
    for i in range(0, len(normalized_tokens), CLOB_TOKEN_IDS_BATCH):
        chunk = normalized_tokens[i : i + CLOB_TOKEN_IDS_BATCH]
        batch = fetch_markets_by_clob_token_ids(chunk)
        for m in batch or []:
            cid = m.get("conditionId")
            if cid and cid not in seen_cid:
                seen_cid.add(cid)
                raw_list.append(m)
    if not raw_list:
        return 0
    conn = get_connection(db_path)
    try:
        norms: List[Dict] = []
        for m in raw_list:
            m.setdefault("_event_neg_risk", m.get("negRisk", False))
            m.setdefault("_event_slug", m.get("slug", ""))
            _enrich_market_with_event_by_slug(m)
            n = _normalize_market_record(m)
            if n:
                norms.append(n)
        if not norms:
            return 0
        return batch_upsert_markets(conn, norms)
    finally:
        conn.close()


def fetch_and_upsert_markets_for_token_ids(
    token_ids: List[str],
    db_path: str,
    max_pages: int = 20,
    requests_delay: float = 0.0,
) -> int:
    """
    根据缺失的 token_id 拉取对应市场（含已关闭/历史），并写入 DB。
    优先使用官方 CLOB GET /markets-by-token/{token_id}，因为这是只有 token_id
    但不知道 condition_id 时的专用解析入口；Gamma 查询作为补充。

    策略：1) CLOB GET /markets-by-token/{token_id} -> condition_id -> market
         2) Gamma GET /markets?clob_token_ids=id1&clob_token_ids=id2 补充
         3) on-chain registry 补充
         4) 若无结果再分页 /events?closed=true 与 /events?active=true&closed=false
    """
    if not token_ids:
        return 0
    # 1) 先走官方 CLOB token 反查。
    total_upserted = fetch_and_upsert_markets_by_token_ids_via_clob_token_lookup(token_ids, db_path)
    # 计算仍未解析的 token（API 可能只返回了部分）。
    # 注意：placeholder 不是“已解析”。如果这里把 placeholder 算作 resolved，
    # 后面的官方 CLOB /markets-by-token 反查会被短路，真实 market 永远补不回来。
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT yes_token_id, no_token_id
            FROM markets
            WHERE yes_token_id IS NOT NULL
              AND no_token_id IS NOT NULL
              AND {_real_market_filter_sql("markets")}
            """
        )
        resolved = set()
        for row in cur.fetchall():
            if row[0]:
                resolved.add(str(row[0]))
            if row[1]:
                resolved.add(str(row[1]))
    finally:
        conn.close()
    remaining = set(str(t) for t in token_ids) - resolved
    if not remaining:
        return total_upserted

    # 2) Gamma token 参数查询作为补充。部分新 sports/tennis token 当前会返回 0，
    # 所以不能让它成为唯一 source of truth。
    total_upserted += fetch_and_upsert_markets_by_token_ids_via_api(list(remaining), db_path)
    remaining = set(str(t) for t in token_ids) - _resolved_token_ids_for(token_ids, db_path, real_only=True)
    if not remaining:
        return total_upserted

    # 3) 用链上 registry + CLOB condition 接口做确定性回填，覆盖 Gamma/CLOB token lookup 漏掉的真实 market。
    # live OrderFilled 同步可通过 POLYDATA_MARKET_BACKFILL_SKIP_ONCHAIN=1 跳过该步骤，
    # 避免市场元数据修复消耗 RPC units 或拖慢实时交易写入。
    skip_onchain = str(os.environ.get("POLYDATA_MARKET_BACKFILL_SKIP_ONCHAIN") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not skip_onchain:
        total_upserted += fetch_and_upsert_markets_for_token_ids_via_onchain(list(remaining), db_path)
        remaining = set(str(t) for t in token_ids) - _resolved_token_ids_for(token_ids, db_path, real_only=True)
        if not remaining:
            return total_upserted

    # 4) 用真实 OrderFilled raw + Data API activity 做补齐。
    # 这一步专门修复“raw fill 已到，但 token/market registry 还没对上”的长尾缺口。
    total_upserted += fetch_and_upsert_markets_by_token_ids_via_activity_fallback(list(remaining), db_path)
    remaining = set(str(t) for t in token_ids) - _resolved_token_ids_for(token_ids, db_path, real_only=True)
    if not remaining:
        return total_upserted

    # 5) 仍未解析的再分页扫 /events
    seen_condition_ids: set = set()
    conn = get_connection(db_path)
    try:
        def _clob_ids(m: Dict) -> List[str]:
            cids = m.get("clobTokenIds", [])
            if isinstance(cids, str):
                try:
                    cids = json.loads(cids)
                except Exception:
                    cids = []
            if not cids and m.get("tokens"):
                cids = [t.get("tokenId") for t in m["tokens"] if t.get("tokenId")]
            return [str(x) for x in cids if x]

        def _fetch_events_once(stage: str, page: int, params: Dict) -> Optional[List[Dict]]:
            """
            仅用于按 token_id 补市场的轻量调用：
            - 不使用全局 _fetch_with_retry 的指数退避，避免导入流程长时间卡住
            - 单次请求失败直接返回 None，由调用方快速放弃 Gamma，交给本地 dataset 兜底
            """
            try:
                session = _get_session()
                resp = session.get(GAMMA_EVENTS_URL, params=params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = _response_json(resp, f"GET {GAMMA_EVENTS_URL}")
                resp.close()
            except Exception as e:
                _invalidate_session(str(e))
                print(
                    f"fetch_and_upsert_markets_for_token_ids[{stage}] page={page}: {e}",
                    file=sys.stderr,
                )
                return None

            # Gamma /events 通常直接返回 list；保守兼容 dict 包裹的情况
            if isinstance(data, list):
                events = data
            elif isinstance(data, dict):
                events = data.get("events") or []
            else:
                events = []

            if not events or not isinstance(events, list):
                return None
            return events

        for closed_first in (True, False):
            if not remaining:
                break
            stage = "closed" if closed_first else "active"
            params = {
                "limit": 100,
                "order": "volume",
                "ascending": "false",
            }
            if closed_first:
                params["closed"] = "true"
            else:
                params["active"] = "true"
                params["closed"] = "false"
            offset = 0
            for page in range(max_pages):
                params["offset"] = offset
                events = _fetch_events_once(stage, page + 1, dict(params))
                if not events:
                    break
                batch: List[Dict] = []
                for ev in events:
                    for m in ev.get("markets", []):
                        cids = _clob_ids(m)
                        if not (remaining & set(cids)):
                            continue
                        cid = m.get("conditionId")
                        if not cid or cid in seen_condition_ids:
                            continue
                        _attach_event_meta_to_market(m, ev)
                        norm = _normalize_market_record(m)
                        if norm:
                            seen_condition_ids.add(cid)
                            batch.append(norm)
                            remaining -= set(cids)
                if batch:
                    total_upserted += batch_upsert_markets(conn, batch)
                offset += len(events)
                if len(events) < 100:
                    break
                if not remaining:
                    break
        return total_upserted
    finally:
        conn.close()


def normalize_market_from_gamma(m: Dict) -> Optional[Dict]:
    """
    从 Gamma API 市场数据中提取并规范化字段

    策略说明：
    - 缺失 questionId/oracle 的市场静默过滤（Strategy B）
    - NegRisk 市场直接采信 Gamma 的 clobTokenIds（Strategy A）
    - 标准二元市场优先使用本地计算，与 API 校验
    """
    condition_id = m.get("conditionId") or m.get("condition_id")
    if not condition_id:
        return None
    # Gamma payload "id" is the official Gamma market id. It is not markets.id,
    # which is a local surrogate key used only for internal joins.
    gamma_market_id = m.get("id") or m.get("gamma_market_id")
    gamma_market_id = str(gamma_market_id).strip() if gamma_market_id is not None else ""

    # 对齐 trade → market 的关键是 yes/no token_id；question_id/oracle 缺失不应直接丢弃市场。
    # 否则会导致大量链上交易无法在本地 markets 表中找到匹配。
    question_id = m.get("questionID") or m.get("questionId") or m.get("question_id")
    oracle = m.get("oracle")
    if isinstance(oracle, dict):
        oracle = oracle.get("address")
    if not oracle:
        oracle = m.get("resolvedBy") or m.get("resolved_by")

    clob_token_ids = _normalize_clob_token_id_list(
        m.get("clobTokenIds", []) or m.get("clob_token_ids", [])
    )

    tokens = m.get("tokens", [])
    if tokens and not clob_token_ids:
        clob_token_ids = _normalize_clob_token_id_list(tokens)

    is_neg_risk = (
        m.get("_event_neg_risk", False)
        or m.get("negRisk", False)
        or m.get("neg_risk", False)
    )

    # Strategy A：只要有 clobTokenIds 就优先采用，避免 conditionId/oracle 不匹配产生的 warning
    # 原因：Gamma 的 resolvedBy 常非 conditionId 公式中的 oracle，导致本地计算 conditionId 不一致；
    # 链上交易实际使用 clobTokenIds，采信 API 可保证与 trades indexer 匹配正确
    if clob_token_ids and len(clob_token_ids) >= 2:
        yes_token = str(clob_token_ids[0])
        no_token = str(clob_token_ids[1])
    else:
        # 无 clobTokenIds 时只能通过本地计算得到 tokenId；计算需要 oracle + question_id
        if not question_id or not oracle:
            return None
        try:
            calculated = calculate_market_tokens(
                condition_id=condition_id,
                oracle=oracle,
                question_id=question_id,
                outcome_slot_count=2,
                collateral_token=USDC_E_ADDRESS,
            )
        except Exception:
            return None
        yes_token = str(calculated["yesTokenId"])
        no_token = str(calculated["noTokenId"])

    slug = m.get("slug") or m.get("market_slug") or f"market-{condition_id[:16]}"
    created_at = m.get("createdAt") or m.get("created_at")
    end_date = m.get("endDate") or m.get("end_date") or m.get("end_date_iso")

    # category: 优先用 event/market 上已有分类；缺失时从 tags 派生一级分类。
    category = (
        m.get("_event_category")
        or m.get("_event_subcategory")
        or m.get("category")
        or ""
    )
    if isinstance(category, dict):
        category = category.get("slug", "") or category.get("label", "") or str(category)
    if not category and m.get("_event_categories"):
        cats = m["_event_categories"]
        if isinstance(cats, list) and cats and isinstance(cats[0], dict):
            category = cats[0].get("slug", "") or cats[0].get("label", "") or ""
        elif isinstance(cats, list) and cats:
            category = str(cats[0])
    if not category and m.get("categories"):
        cats = m["categories"]
        if isinstance(cats, list) and cats and isinstance(cats[0], dict):
            category = cats[0].get("slug", "") or cats[0].get("label", "") or ""

    tags_raw = m.get("_event_tags") or m.get("tags")
    if tags_raw is None and m.get("events"):
        ev = m["events"][0] if m["events"] else {}
        tags_raw = ev.get("tags")
    tags = _normalize_tags_payload(tags_raw)
    if not category and tags:
        category = _derive_category_from_tags(tags)
    latest_yes_price, latest_no_price = _gamma_yes_no_prices(m)
    gamma_updated_at = m.get("updatedAt") or m.get("updated_at") or m.get("lastTradeTimestamp") or m.get("last_trade_timestamp")

    return {
        "gamma_market_id": gamma_market_id,
        "event_id": str(m.get("_event_id") or "").strip(),
        "event_slug": str(m.get("_event_slug") or "").strip(),
        "event_title": str(m.get("_event_title") or "").strip(),
        "slug": slug,
        "condition_id": _ensure_0x(condition_id),
        "question_id": question_id,  # may be None
        "oracle": oracle,  # may be None
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "clob_token_ids": clob_token_ids,  # 完整列表，支持多选市场
        "title": m.get("question") or m.get("title", ""),
        "description": m.get("description", ""),
        "enable_neg_risk": 1 if is_neg_risk else 0,
        "end_date": end_date,
        "created_at": created_at,
        "category": category,
        "tags": tags,
        "_gamma_latest_yes_price": latest_yes_price,
        "_gamma_latest_no_price": latest_no_price,
        "_gamma_latest_trade_at": gamma_updated_at or created_at,
        "_gamma_price_24h_ago": _gamma_price_24h_ago(latest_yes_price, m),
        "_gamma_volume_24h": _gamma_volume_24h(m),
    }


def _category_and_tags_from_event(ev: Dict) -> Tuple[str, str]:
    """从 event 字典解析 category 字符串和 tags JSON 字符串，供 DB 更新用。"""
    category = ev.get("category") or ev.get("subcategory") or ""
    if isinstance(category, dict):
        category = category.get("slug", "") or category.get("label", "") or ""
    cats = ev.get("categories")
    if not category and isinstance(cats, list) and cats and isinstance(cats[0], dict):
        category = cats[0].get("slug", "") or cats[0].get("label", "") or ""
    tags = _normalize_tags_payload(ev.get("tags"))
    return ((category or _derive_category_from_tags(tags)), json.dumps(tags, ensure_ascii=False))


def _event_has_category_or_tags(ev: Dict) -> bool:
    """检查 event 是否包含有效的 category 或 tags。"""
    if not ev:
        return False
    cat = ev.get("category") or ev.get("subcategory") or ev.get("categories")
    if cat:
        if isinstance(cat, list) and cat:
            return True
        if isinstance(cat, dict) and (cat.get("slug") or cat.get("label")):
            return True
        if isinstance(cat, str) and cat.strip():
            return True
    tags = ev.get("tags")
    return bool(isinstance(tags, list) and len(tags) > 0)


def _fetch_market_with_embedded_event(slug: str) -> Optional[Dict]:
    """GET /markets?slug= 获取 market，返回带 embedded_event 的 market 或 None。"""
    try:
        resp = _get_session().get(
            GAMMA_MARKETS_URL,
            params={"slug": slug},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        resp.close()
        if isinstance(data, list) and data:
            return data[0]
        if isinstance(data, dict) and data.get("slug"):
            return data
        return None
    except Exception:
        return None


def _fetch_event_by_id_or_slug(event_id: Optional[str], event_slug: Optional[str]) -> Optional[Dict]:
    """GET /events?id= 或 /events?slug= 获取完整 event（含 tags）。"""
    params: Dict[str, str] = {}
    if event_id:
        params["id"] = str(event_id)
    elif event_slug:
        params["slug"] = str(event_slug)
    else:
        return None
    try:
        resp = _get_session().get(GAMMA_EVENTS_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        events = resp.json()
        resp.close()
        if isinstance(events, list) and events:
            return events[0]
        return None
    except Exception:
        return None


# 并发 worker 用：独立 requests.get，避免共享 Session（非线程安全）
# 重试参数：应对瞬时超时/限流
_REFRESH_FETCH_RETRIES = 3
_REFRESH_FETCH_RETRY_DELAY = 0.3


def _fetch_market_by_slug_standalone(slug: str) -> Optional[Dict]:
    """线程安全：GET /markets?slug= 获取 market，失败时重试。"""
    for attempt in range(_REFRESH_FETCH_RETRIES):
        try:
            resp = requests.get(
                GAMMA_MARKETS_URL,
                params={"slug": slug},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            resp.close()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict) and data.get("slug"):
                return data
            return None
        except Exception:
            if attempt < _REFRESH_FETCH_RETRIES - 1:
                time.sleep(_REFRESH_FETCH_RETRY_DELAY)
            else:
                return None
    return None


def _fetch_event_by_id_or_slug_standalone(
    event_id: Optional[str], event_slug: Optional[str]
) -> Optional[Dict]:
    """线程安全：GET /events?id= 或 ?slug= 获取完整 event，失败时重试。"""
    params: Dict[str, str] = {}
    if event_id:
        params["id"] = str(event_id)
    elif event_slug:
        params["slug"] = str(event_slug)
    else:
        return None
    for attempt in range(_REFRESH_FETCH_RETRIES):
        try:
            resp = requests.get(GAMMA_EVENTS_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            events = resp.json()
            resp.close()
            if isinstance(events, list) and events:
                return events[0]
            return None
        except Exception:
            if attempt < _REFRESH_FETCH_RETRIES - 1:
                time.sleep(_REFRESH_FETCH_RETRY_DELAY)
            else:
                return None
    return None


def _fetch_from_clob_by_condition_id(condition_id: str) -> Optional[Tuple[str, str]]:
    """
    当 Gamma slug 查不到时，用 condition_id 查 CLOB API（GET /markets/{condition_id}）。
    CLOB 返回 tags 列表，可用于补全。返回 (category, tags_json) 或 None。
    """
    if not condition_id or not str(condition_id).strip().startswith("0x"):
        return None
    cid = str(condition_id).strip()
    for attempt in range(_REFRESH_FETCH_RETRIES):
        try:
            url = f"{CLOB_API_BASE}/markets/{cid}"
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            resp.close()
            if not isinstance(data, dict):
                return None
            tags = data.get("tags")
            if not isinstance(tags, list) or not tags:
                return None
            tags_str = [str(t) for t in tags]
            category = tags_str[0] if tags_str else ""
            return (category, json.dumps(tags_str, ensure_ascii=False))
        except Exception:
            if attempt < _REFRESH_FETCH_RETRIES - 1:
                time.sleep(_REFRESH_FETCH_RETRY_DELAY)
            else:
                return None
    return None


def _fetch_category_tags_for_market(slug: str, condition_id: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """
    单条 market 的 API 拉取逻辑（供线程池调用）。
    1) 先 GET /markets?slug= 查 Gamma
    2) 若查不到且 condition_id 存在，用 CLOB GET /markets/{condition_id} 兜底（slug 可能已变更）
    返回 (category, tags_json) 或 None（API 无数据则跳过）。
    """
    slug = (slug or "").strip()
    market = _fetch_market_by_slug_standalone(slug) if slug else None
    cid = (condition_id or "").strip() or None
    if not cid and market:
        cid = (market.get("conditionId") or "").strip() or None
    if not market and cid:
        return _fetch_from_clob_by_condition_id(cid)
    if not market:
        return None
    evs = market.get("events") or []
    ev = evs[0] if evs else None
    if not ev:
        if cid:
            return _fetch_from_clob_by_condition_id(cid)
        return None
    if _event_has_category_or_tags(ev):
        return _category_and_tags_from_event(ev)
    event_id = ev.get("id")
    event_slug = ev.get("slug") or ev.get("ticker")
    full_ev = _fetch_event_by_id_or_slug_standalone(
        str(event_id) if event_id else None,
        str(event_slug) if event_slug else None,
    )
    if full_ev and _event_has_category_or_tags(full_ev):
        return _category_and_tags_from_event(full_ev)
    # Gamma GET /events 返回空或 event 无 tags 时，用 CLOB 兜底（event 可能已从 Gamma 下线）
    if cid:
        return _fetch_from_clob_by_condition_id(cid)
    return None


REFRESH_MAX_WORKERS = 10  # 并发数；失败由重试逻辑兜底


def refresh_category_tags_in_db(
    db_path: str,
    limit: Optional[int] = None,
    requests_delay: float = 0.5,
    max_workers: int = REFRESH_MAX_WORKERS,
) -> int:
    """
    为 DB 中 category 或 tags 为空的 market 补全并写回。仅使用 API 获取数据。

    使用 ThreadPoolExecutor 并发请求，单次请求失败会自动重试 3 次。

    查询链路：
    1. Step A: GET /markets?slug={market_slug} 获取 market 及 embedded event
    2. Step B: 从 market 提取 event_id、event_slug
    3. Step C: 若 embedded_event 已有 category/tags，直接使用
    4. Step D: 否则 GET /events?id={event_id} 或 ?slug={event_slug} 获取完整 event

    API 无数据时跳过该记录（不更新），下次 refresh 会重试。
    返回更新的行数。
    """
    init_schema(db_path=db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, slug, condition_id, title FROM markets
            WHERE (category IS NULL OR TRIM(COALESCE(category, '')) = '')
               OR (tags IS NULL OR TRIM(COALESCE(tags, '')) = '' OR tags = '[]')
            ORDER BY id
            """
            + (" LIMIT " + str(int(limit)) if limit is not None and limit > 0 else ""),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("No markets with empty category/tags.", file=sys.stderr)
        return 0

    print(f"Refreshing category/tags for {len(rows)} market(s) (workers={max_workers}) ...", file=sys.stderr)
    updated = 0
    progress_every = 50

    def _worker(item: Tuple) -> Tuple[int, Optional[Tuple[str, str]]]:
        market_id, slug, condition_id, _title = item
        result = _fetch_category_tags_for_market((slug or "").strip(), condition_id=(condition_id or "").strip() or None)
        return (market_id, result)

    def _norm_result(category: str, tags_json: str) -> str:
        """有 tags 无 category 时用第一个 tag 补 category。"""
        if not category and tags_json != "[]":
            try:
                tags_list = json.loads(tags_json)
                return tags_list[0] if tags_list else ""
            except Exception:
                pass
        return category

    batch: List[Tuple[str, str, int]] = []
    failed_rows: List[Tuple] = []  # (market_id, slug, condition_id, title)
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_row = {ex.submit(_worker, row): row for row in rows}
        for future in as_completed(future_to_row):
            try:
                market_id, result = future.result()
                row = future_to_row[future]
                if result:
                    category, tags_json = result
                    category = _norm_result(category, tags_json) or category
                    batch.append((category, tags_json, market_id))
                else:
                    failed_rows.append(row)
            except Exception as e:
                row = future_to_row.get(future)
                if row:
                    failed_rows.append(row)
                print(f"  Worker error for market {row[0] if row else '?'}: {e}", file=sys.stderr)
            completed += 1
            if completed % progress_every == 0:
                print(f"  ... fetched {completed}/{len(rows)}, updated {len(batch)}", file=sys.stderr)

    # 第二轮：对首轮未拿到结果的记录顺序重试一次，减少漏写
    if failed_rows:
        print(f"  Retrying {len(failed_rows)} failed record(s) sequentially (incl. CLOB by condition_id) ...", file=sys.stderr)
        for market_id, slug, condition_id, _title in failed_rows:
            result = _fetch_category_tags_for_market((slug or "").strip(), condition_id=(condition_id or "").strip() or None)
            if result:
                category, tags_json = result
                category = _norm_result(category, tags_json) or category
                batch.append((category, tags_json, market_id))

    if batch:
        conn = get_connection(db_path)
        try:
            try:
                conn.ping(reconnect=True)
            except Exception:
                pass
            cursor = conn.cursor()
            cursor.executemany(
                "UPDATE markets SET category = ?, tags = ? WHERE id = ?",
                batch,
            )
            conn.commit()
        finally:
            conn.close()

    updated = len(batch)
    print(f"Refreshed category/tags for {updated} market(s).", file=sys.stderr)
    return updated


def batch_upsert_markets(conn, markets: List[Dict]) -> int:
    """
    批量插入或更新市场记录，condition_id 唯一，自动去重。
    使用 executemany 一次提交，减轻 DB IO 压力。
    category/tags 使用 COALESCE 保护：只在当前为空时才用新值覆盖，
    避免 API 抓到的精准标签被后续无分类数据清空。
    Returns:
        成功写入的数量
    """
    if not markets:
        return 0
    rows = []
    legacy_rows = []
    for market in markets:
        cids = market.get("clob_token_ids", []) or []
        base_row = (
            market.get("gamma_market_id", "") or "",
            market.get("slug"),
            market.get("condition_id"),
            market.get("question_id"),
            market.get("oracle"),
            market.get("yes_token_id"),
            market.get("no_token_id"),
            market.get("title", ""),
            market.get("description", ""),
            _bool_for_db(market.get("enable_neg_risk", 0)),
            _datetime_for_db(market.get("end_date")),
            _datetime_for_db(market.get("created_at")),
            market.get("category", "") or "",
            _json_for_db(market.get("tags", []) or []),
            _json_for_db(cids),
        )
        legacy_rows.append(base_row)
        rows.append((
            market.get("gamma_market_id", "") or "",
            market.get("event_id", "") or "",
            market.get("event_slug", "") or "",
            market.get("event_title", "") or "",
            *base_row[1:],
        ))
    cursor = conn.cursor()
    if _is_postgres_target():
        cursor.executemany(
            """
            INSERT INTO markets (
                gamma_market_id, event_id, event_slug, event_title,
                slug, condition_id, question_id, oracle,
                yes_token_id, no_token_id, title, description,
                enable_neg_risk, end_date, created_at, category, tags, clob_token_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(condition_id) DO UPDATE SET
                gamma_market_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.gamma_market_id,'')), ''), markets.gamma_market_id),
                event_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.event_id,'')), ''), markets.event_id),
                event_slug=COALESCE(NULLIF(TRIM(COALESCE(excluded.event_slug,'')), ''), markets.event_slug),
                event_title=COALESCE(NULLIF(TRIM(COALESCE(excluded.event_title,'')), ''), markets.event_title),
                slug=COALESCE(NULLIF(TRIM(COALESCE(excluded.slug,'')), ''), markets.slug),
                question_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.question_id,'')), ''), markets.question_id),
                oracle=COALESCE(NULLIF(TRIM(COALESCE(excluded.oracle,'')), ''), markets.oracle),
                yes_token_id=excluded.yes_token_id,
                no_token_id=excluded.no_token_id,
                title=excluded.title,
                description=excluded.description,
                enable_neg_risk=excluded.enable_neg_risk,
                end_date=COALESCE(excluded.end_date, markets.end_date),
                created_at=COALESCE(excluded.created_at, markets.created_at),
                category=COALESCE(NULLIF(TRIM(COALESCE(markets.category,'')), ''), excluded.category),
                tags=CASE
                    WHEN markets.tags IS NULL OR markets.tags = '[]'::jsonb THEN excluded.tags
                    ELSE markets.tags
                END,
                clob_token_ids=CASE
                    WHEN excluded.clob_token_ids IS NULL OR excluded.clob_token_ids = '[]'::jsonb THEN markets.clob_token_ids
                    ELSE excluded.clob_token_ids
                END
            """,
            rows,
        )
    else:
        cursor.executemany(
            """
            INSERT INTO markets (
                gamma_market_id, slug, condition_id, question_id, oracle,
                yes_token_id, no_token_id, title, description,
                enable_neg_risk, end_date, created_at, category, tags, clob_token_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(condition_id) DO UPDATE SET
                gamma_market_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.gamma_market_id,'')), ''), markets.gamma_market_id),
                slug=COALESCE(NULLIF(TRIM(COALESCE(excluded.slug,'')), ''), markets.slug),
                question_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.question_id,'')), ''), markets.question_id),
                oracle=COALESCE(NULLIF(TRIM(COALESCE(excluded.oracle,'')), ''), markets.oracle),
                yes_token_id=excluded.yes_token_id,
                no_token_id=excluded.no_token_id,
                title=excluded.title,
                description=excluded.description,
                enable_neg_risk=excluded.enable_neg_risk,
                end_date=COALESCE(NULLIF(TRIM(COALESCE(excluded.end_date,'')), ''), markets.end_date),
                created_at=COALESCE(NULLIF(TRIM(COALESCE(excluded.created_at,'')), ''), markets.created_at),
                category=COALESCE(NULLIF(TRIM(COALESCE(markets.category,'')), ''), excluded.category),
                tags=COALESCE(NULLIF(TRIM(COALESCE(markets.tags,'[]')), '[]'), excluded.tags),
                clob_token_ids=COALESCE(NULLIF(TRIM(COALESCE(markets.clob_token_ids,'[]')), '[]'), excluded.clob_token_ids)
            """,
            legacy_rows,
        )
    conn.commit()
    upsert_market_tokens_for_conditions(
        conn,
        [
            str(market.get("condition_id") or "").strip()
            for market in markets
            if str(market.get("condition_id") or "").strip()
        ],
    )
    return len(markets)


def upsert_market_tokens_for_conditions(conn, condition_ids: List[str]) -> int:
    """Keep market_tokens in step with markets for newly discovered markets.

    Existing migrated rows use their historical positive ids. Runtime-created token rows
    use deterministic negative ids derived from the local market id, avoiding collisions
    while preserving token_id as the natural unique key.
    """
    condition_ids = sorted({_ensure_0x(cid) for cid in condition_ids if str(cid or "").strip()})
    if not condition_ids:
        return 0
    try:
        if not table_exists(conn, "market_tokens"):
            return 0
    except Exception:
        return 0

    rows_by_condition: Dict[str, Tuple[Any, ...]] = {}
    chunk_size = 500
    for start in range(0, len(condition_ids), chunk_size):
        chunk = condition_ids[start : start + chunk_size]
        placeholders = ",".join(["?"] * len(chunk))
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT id, condition_id, yes_token_id, no_token_id, end_date, created_at
            FROM markets
            WHERE condition_id IN ({placeholders})
            """,
            tuple(chunk),
        )
        for row in cur.fetchall():
            rows_by_condition[str(row[1])] = tuple(row)

    columns = set(get_table_columns(conn, "market_tokens"))
    has_raw_columns = {"raw_end_date", "raw_created_at", "raw_updated_at"}.issubset(columns)
    token_rows: List[Tuple[Any, ...]] = []
    now = datetime.now(timezone.utc)
    for market_id, condition_id, yes_token_id, no_token_id, end_date, created_at in rows_by_condition.values():
        local_id = int(market_id)
        if yes_token_id:
            base = (-((local_id * 2) - 1), local_id, condition_id, str(yes_token_id), "YES", 0, True, end_date, created_at, now)
            token_rows.append(base[:8] + (None, base[8], None, base[9], None) if has_raw_columns else base)
        if no_token_id:
            base = (-(local_id * 2), local_id, condition_id, str(no_token_id), "NO", 1, True, end_date, created_at, now)
            token_rows.append(base[:8] + (None, base[8], None, base[9], None) if has_raw_columns else base)

    if not token_rows:
        return 0

    cur = conn.cursor()
    negative_ids = [int(row[0]) for row in token_rows if int(row[0]) < 0]
    if negative_ids:
        for start in range(0, len(negative_ids), chunk_size):
            chunk = negative_ids[start : start + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cur.execute(f"DELETE FROM market_tokens WHERE id IN ({placeholders})", tuple(chunk))
    if has_raw_columns:
        cur.executemany(
            """
            INSERT INTO market_tokens (
                id, market_id, condition_id, token_id, outcome, outcome_index, active,
                end_date, raw_end_date, created_at, raw_created_at, updated_at, raw_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_id) DO UPDATE SET
                market_id=excluded.market_id,
                condition_id=excluded.condition_id,
                outcome=excluded.outcome,
                outcome_index=excluded.outcome_index,
                active=excluded.active,
                end_date=COALESCE(excluded.end_date, market_tokens.end_date),
                created_at=COALESCE(excluded.created_at, market_tokens.created_at),
                updated_at=excluded.updated_at
            """,
            token_rows,
        )
    else:
        cur.executemany(
            """
            INSERT INTO market_tokens (
                id, market_id, condition_id, token_id, outcome, outcome_index, active,
                end_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_id) DO UPDATE SET
                market_id=excluded.market_id,
                condition_id=excluded.condition_id,
                outcome=excluded.outcome,
                outcome_index=excluded.outcome_index,
                active=excluded.active,
                end_date=COALESCE(excluded.end_date, market_tokens.end_date),
                created_at=COALESCE(excluded.created_at, market_tokens.created_at),
                updated_at=excluded.updated_at
            """,
            token_rows,
        )
    conn.commit()
    return len(token_rows)


def upsert_market(conn, market: Dict) -> int:
    """
    插入或更新市场记录，返回 market_id
    不存储 collateral_token、status、updated_at；存储 category、tags、clob_token_ids
    category/tags 使用 COALESCE 保护，避免覆盖已有精准标签。
    """
    cids = market.get("clob_token_ids", []) or []
    cursor = conn.cursor()
    legacy_row = (
        market.get("gamma_market_id", "") or "",
        market.get("slug"),
        market.get("condition_id"),
        market.get("question_id"),
        market.get("oracle"),
        market.get("yes_token_id"),
        market.get("no_token_id"),
        market.get("title", ""),
        market.get("description", ""),
        _bool_for_db(market.get("enable_neg_risk", 0)),
        _datetime_for_db(market.get("end_date")),
        _datetime_for_db(market.get("created_at")),
        market.get("category", "") or "",
        _json_for_db(market.get("tags", []) or []),
        _json_for_db(cids),
    )
    row = (
        market.get("gamma_market_id", "") or "",
        market.get("event_id", "") or "",
        market.get("event_slug", "") or "",
        market.get("event_title", "") or "",
        *legacy_row[1:],
    )
    if _is_postgres_target():
        cursor.execute(
            """
            INSERT INTO markets (
                gamma_market_id, event_id, event_slug, event_title,
                slug, condition_id, question_id, oracle,
                yes_token_id, no_token_id, title, description,
                enable_neg_risk, end_date, created_at, category, tags, clob_token_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(condition_id) DO UPDATE SET
                gamma_market_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.gamma_market_id,'')), ''), markets.gamma_market_id),
                event_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.event_id,'')), ''), markets.event_id),
                event_slug=COALESCE(NULLIF(TRIM(COALESCE(excluded.event_slug,'')), ''), markets.event_slug),
                event_title=COALESCE(NULLIF(TRIM(COALESCE(excluded.event_title,'')), ''), markets.event_title),
                slug=COALESCE(NULLIF(TRIM(COALESCE(excluded.slug,'')), ''), markets.slug),
                question_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.question_id,'')), ''), markets.question_id),
                oracle=COALESCE(NULLIF(TRIM(COALESCE(excluded.oracle,'')), ''), markets.oracle),
                yes_token_id=excluded.yes_token_id,
                no_token_id=excluded.no_token_id,
                title=excluded.title,
                description=excluded.description,
                enable_neg_risk=excluded.enable_neg_risk,
                end_date=COALESCE(excluded.end_date, markets.end_date),
                created_at=COALESCE(excluded.created_at, markets.created_at),
                category=COALESCE(NULLIF(TRIM(COALESCE(markets.category,'')), ''), excluded.category),
                tags=CASE
                    WHEN markets.tags IS NULL OR markets.tags = '[]'::jsonb THEN excluded.tags
                    ELSE markets.tags
                END,
                clob_token_ids=CASE
                    WHEN excluded.clob_token_ids IS NULL OR excluded.clob_token_ids = '[]'::jsonb THEN markets.clob_token_ids
                    ELSE excluded.clob_token_ids
                END
            """,
            row,
        )
    else:
        cursor.execute(
            """
            INSERT INTO markets (
                gamma_market_id, slug, condition_id, question_id, oracle,
                yes_token_id, no_token_id, title, description,
                enable_neg_risk, end_date, created_at, category, tags, clob_token_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(condition_id) DO UPDATE SET
                gamma_market_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.gamma_market_id,'')), ''), markets.gamma_market_id),
                slug=COALESCE(NULLIF(TRIM(COALESCE(excluded.slug,'')), ''), markets.slug),
                question_id=COALESCE(NULLIF(TRIM(COALESCE(excluded.question_id,'')), ''), markets.question_id),
                oracle=COALESCE(NULLIF(TRIM(COALESCE(excluded.oracle,'')), ''), markets.oracle),
                yes_token_id=excluded.yes_token_id,
                no_token_id=excluded.no_token_id,
                title=excluded.title,
                description=excluded.description,
                enable_neg_risk=excluded.enable_neg_risk,
                end_date=COALESCE(NULLIF(TRIM(COALESCE(excluded.end_date,'')), ''), markets.end_date),
                created_at=COALESCE(NULLIF(TRIM(COALESCE(excluded.created_at,'')), ''), markets.created_at),
                category=COALESCE(NULLIF(TRIM(COALESCE(markets.category,'')), ''), excluded.category),
                tags=COALESCE(NULLIF(TRIM(COALESCE(markets.tags,'[]')), '[]'), excluded.tags),
                clob_token_ids=COALESCE(NULLIF(TRIM(COALESCE(markets.clob_token_ids,'[]')), '[]'), excluded.clob_token_ids)
            """,
            legacy_row,
        )
    conn.commit()
    cursor.execute("SELECT id FROM markets WHERE condition_id = ?", (market["condition_id"],))
    row = cursor.fetchone()
    upsert_market_tokens_for_conditions(conn, [market["condition_id"]])
    return row[0] if row else 0


def estimate_full_markets(
    active_only: bool = False, closed_only: bool = False, max_pages: Optional[int] = None
) -> Dict:
    """
    全量分页拉取 Gamma API，统计市场总数及经 normalize 后保留数量，
    并估算存入 SQLite 后的 DB 大小。

    Args:
        active_only: 是否只统计活跃市场
        closed_only: 是否只统计已结束市场
        max_pages: 最多拉取页数，None 表示不限

    Returns:
        {"total_raw": int, "total_normalized": int, "db_size_mb": float, "pages": int, "extrapolated": bool}
    """
    params: Dict = {
        "limit": 100,
        "ascending": "false",
    }
    if active_only:
        params["active"] = "true"
        params["closed"] = "false"
        params["order"] = "volume24hr"
    elif closed_only:
        params["closed"] = "true"
        params["order"] = "volume"  # closed_time 易 422
    else:
        params["order"] = "volume24hr"

    # closed_only 必须用 events 接口
    api_url = GAMMA_EVENTS_URL if closed_only else GAMMA_MARKETS_URL

    total_raw = 0
    total_normalized = 0
    pages = 0
    sample_sizes: List[int] = []  # 用于估算单条记录大小
    extrapolated = False

    offset = 0
    while True:
        if max_pages is not None and pages >= max_pages:
            extrapolated = True
            break
        params["offset"] = offset
        try:
            data = _fetch_with_retry(api_url, dict(params))
        except Exception as e:
            print(f"Error during estimate: {e}", file=sys.stderr)
            break

        events_list: List[Dict] = []
        if closed_only:
            # events 返回事件列表，需扁平化 markets
            events_list = data if isinstance(data, list) else []
            batch = []
            for ev in events_list:
                for m in ev.get("markets", []):
                    _attach_event_meta_to_market(m, ev)
                    batch.append(m)
        else:
            if isinstance(data, list):
                batch = data
            elif isinstance(data, dict) and "markets" in data:
                batch = data["markets"]
            else:
                batch = []

        if not batch:
            break

        total_raw += len(batch)
        pages += 1

        for m in batch:
            norm = _normalize_market_record(m)
            if norm:
                total_normalized += 1
                # 仅采样前若干条用于大小估算
                if len(sample_sizes) < 50:
                    sample_sizes.append(
                        sum(len(str(v) or "") for v in norm.values()) + 200
                    )

        # 修复：使用单一 page_size 推进 offset，避免原来 offset += len(batch) 与 offset += page_size 双重累加
        page_size = len(events_list) if closed_only else len(batch)
        offset += page_size
        if pages % 10 == 0 and pages > 0:
            print(f"  ... {pages} pages, {total_raw} raw, {total_normalized} normalized", file=sys.stderr)
        if page_size < 100:
            break

    avg_bytes = int(sum(sample_sizes) / len(sample_sizes)) if sample_sizes else 1200
    db_bytes = total_normalized * avg_bytes
    db_size_mb = round(db_bytes / (1024 * 1024), 2)

    return {
        "total_raw": total_raw,
        "total_normalized": total_normalized,
        "db_size_mb": db_size_mb,
        "pages": pages,
        "extrapolated": extrapolated,
    }


def run_market_discovery(
    db_path: Optional[str] = None,
    output_json: Optional[str] = None,
    limit: int = 500,
    active_only: bool = False,
    closed_only: bool = False,
    event_slugs: Optional[List[str]] = None,
    since_date: Optional[datetime] = None,
    batch_size: int = 500,
    closed_events_only: bool = False,
    closed_events_start_offset: Optional[int] = None,
    requests_delay: float = DEFAULT_REQUESTS_DELAY,
    cooldown_every: Optional[int] = None,
    cooldown_seconds: float = 60,
    max_fetches: Optional[int] = None,
    session_recycle_every: int = SESSION_RECYCLE_EVERY,
    sync_state_key: str = SYNC_KEY_LAST_DISCOVERY_AT,
    update_sync_state: bool = True,
    onchain_registry_supplement: bool = True,
) -> int:
    """
    执行市场发现流程

    - 若指定 since_date：从该日期起按时间向后爬取，不限 limit，拉取活跃+已关闭
    - 若指定 db_path：写入数据库；since_date 时每 batch_size 个市场写入一次（去重）
    - 否则：保存为 JSON 文件（output_json 路径，默认 market_discovery.json）

    Returns:
        成功处理的市场数量
    """
    markets_to_process: List[Dict] = []
    pre_written = 0
    discovery_watermark: Optional[datetime] = None

    if closed_events_only:
        if not db_path:
            print("Error: --closed-events-only requires database output mode.", file=sys.stderr)
            return 0
        return _run_closed_events_only(
            db_path=db_path,
            batch_size=batch_size,
            start_offset=closed_events_start_offset,
            requests_delay=requests_delay,
            cooldown_every=cooldown_every,
            cooldown_seconds=cooldown_seconds,
            max_fetches=max_fetches,
            since_date=since_date,
            session_recycle_every=session_recycle_every,
        )

    if since_date is not None:
        markets_to_process, pre_written, discovery_watermark = fetch_markets_since_date(
            since_date=since_date,
            include_active=True,
            include_closed=True,
            db_path=db_path,
            batch_size=batch_size,
            requests_delay=requests_delay,
        )
        total = pre_written + len(markets_to_process)
        print(f"Fetched {total} markets since {since_date.date()}", file=sys.stderr)
        # 若已分批写入 DB，继续走后续 sync_state 保存逻辑。
        # 没有 JSON 输出时 markets_to_process 通常为空，无需再次 upsert。
    else:
        if db_path and not active_only and not closed_only and not event_slugs:
            ancient = datetime(1970, 1, 1, tzinfo=timezone.utc)
            markets_to_process, pre_written, discovery_watermark = fetch_markets_since_date(
                since_date=ancient,
                include_active=True,
                include_closed=True,
                db_path=db_path,
                batch_size=batch_size,
                requests_delay=requests_delay,
            )
            print("Fetched full market history from Gamma events (active + closed).", file=sys.stderr)
        else:
            if event_slugs:
                for slug in event_slugs:
                    ms = fetch_markets_by_event_slug(slug)
                    markets_to_process.extend(ms)

            if not event_slugs or limit > len(markets_to_process):
                ms = fetch_all_markets(
                    limit=limit,
                    active_only=active_only,
                    closed_only=closed_only,
                    db_path=db_path,
                    batch_size=batch_size,
                    requests_delay=requests_delay,
                )
                seen = {m.get("conditionId") or m.get("condition_id") for m in markets_to_process}
                for m in ms:
                    condition_id = m.get("conditionId") or m.get("condition_id")
                    if condition_id not in seen:
                        markets_to_process.append(m)

    # 如果 fetch_all_markets 以 streaming-to-DB 模式运行，则 markets_to_process 可能为空，
    # 此时无需再做一次性 norms/upsert（避免重复写入和占用内存）
    norms: List[Dict] = []
    if markets_to_process:
        for m in markets_to_process:
            norm = _normalize_market_record(m)
            if norm:
                norms.append(norm)
        if discovery_watermark is None:
            discovery_watermark = _max_created_at_from_markets(markets_to_process)

    if db_path and pre_written == 0 and norms:
        init_schema(db_path=db_path)
        with get_db(db_path) as conn:
            batch_upsert_markets(conn, norms)
            _upsert_market_serving_from_gamma(conn, norms)
    elif not db_path or output_json:
        out_path = output_json or "market_discovery.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(norms, f, ensure_ascii=False, indent=2)
        print(f"Results saved to: {out_path}", file=sys.stderr)

    # 记录本次实际扫描到的最大 market.createdAt，供下次增量同步读取。
    # 手工 --since-date 回补默认不推进主断点，避免污染正常增量游标。
    if db_path and update_sync_state:
        try:
            if discovery_watermark is None:
                discovery_watermark = _max_created_at_from_markets(norms)
            if discovery_watermark is None and since_date is not None:
                discovery_watermark = since_date
            if discovery_watermark is not None:
                with get_db(db_path) as conn:
                    _save_last_discovery_at(conn, discovery_watermark, sync_state_key=sync_state_key)
        except Exception as e:
            print(f"[sync-state] failed to save {sync_state_key}: {e}", file=sys.stderr)
    if db_path and onchain_registry_supplement:
        pre_written += supplement_missing_markets_from_onchain_registry(
            db_path=db_path,
            batch_blocks=DEFAULT_CHAIN_BATCH_BLOCKS,
            requests_delay=DEFAULT_CHAIN_REQUESTS_DELAY,
        )

    return pre_written + len(norms)


def _get_current_max_market_id(db_path: Optional[str]) -> int:
    init_schema(db_path=db_path)
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM markets").fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        conn.close()


def _run_post_discovery_canonical_repair(
    db_path: Optional[str],
    *,
    start_id: int,
    max_id: int,
    batch_size: int,
    workers: int,
    limit: Optional[int],
    run_retry: bool,
    dry_run: bool = False,
) -> None:
    if max_id <= start_id:
        print(
            f"[post-repair] No new market id range to repair: ({start_id}, {max_id}]",
            file=sys.stderr,
        )
        return

    from market.backfill_market_canonical_fields import (
        DEFAULT_SYNC_STATE_KEY as CANONICAL_BACKFILL_STATE_KEY,
        run_backfill,
    )

    print(
        f"[post-repair] Canonical backfill for market id range ({start_id}, {max_id}]",
        file=sys.stderr,
    )
    backfill_stats = run_backfill(
        db_path,
        batch_size=batch_size,
        workers=workers,
        limit=limit,
        start_id=start_id,
        max_id=max_id,
        resume=False,
        sync_state_key=CANONICAL_BACKFILL_STATE_KEY,
        dry_run=dry_run,
    )
    print(f"[post-repair] canonical backfill stats: {backfill_stats}", file=sys.stderr)

    if not run_retry:
        return

    from market.retry_market_fetch_failed import run_retry as run_retry_fetch_failed

    print(
        f"[post-repair] Retrying unresolved canonical fields for market id range ({start_id}, {max_id}]",
        file=sys.stderr,
    )
    retry_stats = run_retry_fetch_failed(
        db_path,
        batch_size=batch_size,
        workers=workers,
        limit=limit,
        start_id=start_id,
        max_id=max_id,
        dry_run=dry_run,
    )
    print(f"[post-repair] retry fetch-failed stats: {retry_stats}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Polymarket Market Discovery Service")
    add_db_cli_args(parser)
    parser.add_argument("--no-db", action="store_true", help="不写数据库，改为输出 JSON 文件")
    parser.add_argument("--output", "-o", help="JSON 输出路径（仅在 --no-db 时生效，默认 market_discovery.json）")
    parser.add_argument("--limit", type=int, default=500, help="最多获取市场数（仅在未使用 --since-date 时生效）")
    parser.add_argument("--active-only", action="store_true", help="仅获取活跃市场（与 --since-date 互斥时被忽略）")
    parser.add_argument("--closed-only", action="store_true", help="仅获取已结束市场（与 --since-date 互斥时被忽略）")
    parser.add_argument(
        "--since-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="从该日期起向后爬取市场，按时间排序，不设 limit；同时拉取活跃和已关闭市场。例：2024-09-01",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="与 --since-date 和数据库输出联用：每批写入数据库的市场数量（默认 500）",
    )
    parser.add_argument(
        "--closed-events-only",
        action="store_true",
        help="仅补充拉取 closed events，用于断点续传；需启用数据库输出",
    )
    parser.add_argument(
        "--closed-events-start-offset",
        type=int,
        default=None,
        help="与 --closed-events-only 联用：从指定 offset 开始（用于手动指定断点）",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        metavar="SECONDS",
        help="每次 API 请求前的间隔（秒），默认 0.7；加大可减轻服务器压力",
    )
    parser.add_argument(
        "--cooldown-every",
        type=int,
        default=None,
        metavar="N",
        help="与 --closed-events-only 联用：每 N 次请求后冷却一次",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=60,
        metavar="SECONDS",
        help="与 --cooldown-every 联用：冷却时长（秒），默认 60",
    )
    parser.add_argument(
        "--max-fetches",
        type=int,
        default=None,
        metavar="N",
        help="与 --closed-events-only 联用：最多请求 N 次后停止并保存进度，可多次运行分批完成",
    )
    parser.add_argument(
        "--session-recycle-every",
        type=int,
        default=SESSION_RECYCLE_EVERY,
        metavar="N",
        help=(
            f"每 N 次 HTTP 请求后自动关闭并重建 Session，清除僵死连接，防止连接池耗尽（默认 {SESSION_RECYCLE_EVERY}）；"
            "爬取量极大时可适当调小（如 100）"
        ),
    )
    parser.add_argument(
        "--event-slugs",
        nargs="*",
        help="指定事件 slug 列表，如 fed-decision-in-january",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help=(
            "增量模式：自动从数据库推断起始日期（优先读取上次同步完成时间，"
            "否则取 DB 中最新市场的 created_at），只拉取新市场。"
            "需启用数据库输出；首次运行若 DB 为空则自动降级为全量同步。"
            "与 --since-date 互斥（--incremental 优先）。"
        ),
    )
    parser.add_argument(
        "--incremental-fallback-latest-created-at",
        action="store_true",
        help=(
            "当 sync_state 没有 last_market_discovery_at 时，允许回退到 markets.created_at 最大值。"
            "默认关闭，避免迁移/回补缺口时把断点错误推进到最新数据。"
        ),
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help=(
            "守护进程模式：持续循环运行市场发现，每次完成后等待 --interval 秒再次执行。"
            "与 --incremental 联用可实现真正的增量定时同步。"
            "按 Ctrl+C 退出。"
        ),
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        metavar="SECONDS",
        help="与 --watch 联用：两次同步之间的等待秒数（默认 3600，即 1 小时）",
    )
    parser.add_argument(
        "--watch-error-interval",
        type=int,
        default=int(os.environ.get("POLYDATA_MARKET_SYNC_ERROR_INTERVAL_SECONDS", "300")),
        metavar="SECONDS",
        help="与 --watch 联用：单轮同步因网络/数据库等异常失败后的退避秒数（默认 300）",
    )
    parser.add_argument(
        "--estimate",
        action="store_true",
        help="全量分页拉取并估算市场总数与 DB 大小，不写入文件",
    )
    parser.add_argument(
        "--estimate-max-pages",
        type=int,
        default=None,
        help="与 --estimate 联用：最多拉取页数，用于快速抽样（默认不限）",
    )
    parser.add_argument(
        "--refresh-category-tags",
        action="store_true",
        help="为 DB 中 category/tags 为空的 market 按 slug 请求 Gamma 事件补全并写回；需启用数据库输出",
    )
    parser.add_argument(
        "--refresh-category-tags-limit",
        type=int,
        default=None,
        metavar="N",
        help="与 --refresh-category-tags 联用：仅处理前 N 条空记录（默认全部）",
    )
    parser.add_argument(
        "--no-refresh-category-tags",
        action="store_true",
        help="兼容旧参数：当前默认已不在 discovery 结束后自动全表补分类，本选项可忽略",
    )
    parser.add_argument(
        "--post-refresh-category-tags",
        action="store_true",
        help="在 discovery 结束后额外执行一次全表 category/tags 修复；默认关闭，通常只在 repair 时使用",
    )
    parser.add_argument(
        "--skip-onchain-registry-supplement",
        action="store_true",
        help="跳过 discovery 后的链上 TokenRegistered 补市场扫描；PostgreSQL 增量同步 smoke test 时建议使用",
    )
    parser.add_argument(
        "--post-canonical-repair",
        dest="post_canonical_repair",
        action="store_true",
        default=True,
        help="数据库写入后自动对本次新增 market 运行 canonical backfill；默认开启",
    )
    parser.add_argument(
        "--no-post-canonical-repair",
        dest="post_canonical_repair",
        action="store_false",
        help="禁用 discovery 后的 canonical backfill",
    )
    parser.add_argument(
        "--post-retry-fetch-failed",
        dest="post_retry_fetch_failed",
        action="store_true",
        default=True,
        help="canonical backfill 后自动运行第二轮 retry；默认开启",
    )
    parser.add_argument(
        "--no-post-retry-fetch-failed",
        dest="post_retry_fetch_failed",
        action="store_false",
        help="禁用 discovery 后的 retry_market_fetch_failed",
    )
    parser.add_argument(
        "--post-repair-start-id",
        type=int,
        default=None,
        help="post repair 起始 market id；默认只修复本次 discovery 新增 id，可传 0 修复全库",
    )
    parser.add_argument(
        "--post-repair-limit",
        type=int,
        default=None,
        help="post repair 最多扫描多少条候选；默认不限制",
    )
    parser.add_argument(
        "--post-repair-batch-size",
        type=int,
        default=200,
        help="post repair 每批扫描多少条候选 market，默认 200",
    )
    parser.add_argument(
        "--post-repair-workers",
        type=int,
        default=8,
        help="post repair 并发请求数，默认 8",
    )

    args = parser.parse_args()
    configure_db_from_args(args)
    db_path = None if args.no_db else args.sqlite_path

    if getattr(args, "refresh_category_tags", False):
        if not db_path:
            print("Error: --refresh-category-tags requires database output mode.", file=sys.stderr)
            sys.exit(1)
        refresh_category_tags_in_db(
            db_path=db_path,
            limit=args.refresh_category_tags_limit,
            requests_delay=(args.delay if args.delay is not None else DEFAULT_REQUESTS_DELAY),
        )
        return

    if args.estimate:
        print("Estimating full market count and DB size...", file=sys.stderr)
        result = estimate_full_markets(
            active_only=args.active_only,
            closed_only=args.closed_only,
            max_pages=args.estimate_max_pages,
        )
        ext = " (抽样外推)" if result.get("extrapolated") else ""
        print(
            f"Total raw markets (API): {result['total_raw']}\n"
            f"Total normalized (stored): {result['total_normalized']}\n"
            f"API pages: {result['pages']}\n"
            f"Estimated DB size: ~{result['db_size_mb']} MB{ext}",
            file=sys.stderr,
        )
        return

    if args.active_only and args.closed_only:
        print("Error: --active-only and --closed-only cannot be used together.", file=sys.stderr)
        sys.exit(1)

    if args.incremental and not db_path:
        print("Error: --incremental requires database output mode.", file=sys.stderr)
        sys.exit(1)

    if args.watch and not db_path:
        print("Error: --watch requires database output mode.", file=sys.stderr)
        sys.exit(1)

    # 解析手动指定的 --since-date（与 --incremental 互斥时，--incremental 优先）
    manual_since_date = None
    if args.since_date:
        try:
            manual_since_date = datetime.strptime(args.since_date.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Error: Invalid --since-date format. Use YYYY-MM-DD, e.g. 2024-09-01", file=sys.stderr)
            sys.exit(1)

    requests_delay = (args.delay if args.delay is not None else DEFAULT_REQUESTS_DELAY)

    def _run_once() -> int:
        """执行一次市场发现，返回写入数量。支持 --incremental 自动推断起点。"""
        since_date = manual_since_date
        update_sync_state = bool(args.incremental or args.watch)

        if args.incremental:
            since_date = resolve_incremental_since_date(
                db_path,
                fallback_to_latest_created_at=bool(args.incremental_fallback_latest_created_at),
            )
            if since_date is not None:
                print(
                    f"[incremental] Resuming from last sync: {since_date.isoformat()}",
                    file=sys.stderr,
                )
            else:
                print(
                    "[incremental] No previous sync found in DB — running full discovery.",
                    file=sys.stderr,
                )
        elif (
            args.watch
            and db_path
            and manual_since_date is None
            and not args.event_slugs
            and not args.active_only
            and not args.closed_only
            and not args.closed_events_only
        ):
            since_date = resolve_incremental_since_date(
                db_path,
                fallback_to_latest_created_at=bool(args.incremental_fallback_latest_created_at),
            )
            if since_date is not None:
                print(
                    f"[watch-auto-incremental] Resuming from last sync: {since_date.isoformat()}",
                    file=sys.stderr,
                )
            else:
                print(
                    "[watch-auto-incremental] No previous sync found in DB — running full discovery.",
                    file=sys.stderr,
                )

        # 如果启用了数据库输出且未使用 since-date / incremental / event-slugs，
        # 则默认做“全量发现”以最大化覆盖链上交易所需的 tokenId。
        # （此前默认 --limit=500 容易导致 markets 覆盖不足，从而 trade 导入时出现大量 missing market）
        limit = args.limit
        if (
            db_path
            and since_date is None
            and not args.incremental
            and not args.event_slugs
            and not args.active_only
            and not args.closed_only
            and limit == 500
        ):
            limit = 10_000_000
            print(
                "[discovery] --db provided with default --limit=500. "
                "Upgrading to full discovery (limit=10_000_000) to maximize market coverage.",
                file=sys.stderr,
            )

        if args.closed_events_only:
            msg = "Running Closed Events Supplement Only"
            if since_date is not None:
                msg += f" (created_at >= {since_date.date()})"
            print(msg + "...", file=sys.stderr)
        else:
            if since_date is not None:
                print(
                    f"Fetching markets since {since_date.date()} (active + closed, chronological)...",
                    file=sys.stderr,
                )
            else:
                print("Running Market Discovery (full)...", file=sys.stderr)

        return run_market_discovery(
            db_path=db_path,
            output_json=args.output,
            limit=limit,
            active_only=args.active_only,
            closed_only=args.closed_only,
            event_slugs=args.event_slugs,
            since_date=since_date,
            batch_size=args.batch_size,
            closed_events_only=args.closed_events_only,
            closed_events_start_offset=args.closed_events_start_offset,
            requests_delay=requests_delay,
            cooldown_every=args.cooldown_every,
            cooldown_seconds=args.cooldown_seconds,
            max_fetches=args.max_fetches,
            session_recycle_every=args.session_recycle_every,
            update_sync_state=update_sync_state,
            onchain_registry_supplement=not args.skip_onchain_registry_supplement,
        )

    if args.watch:
        print(
            f"[watch] Starting daemon mode. Interval: {args.interval}s. Press Ctrl+C to stop.",
            file=sys.stderr,
        )
        print(f"[watch] Database target: {describe_db_target()}", file=sys.stderr)
        run_index = 0
        try:
            while True:
                sleep_seconds = max(1, int(args.interval))
                try:
                    run_index += 1
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    print(f"\n[watch] Run #{run_index} at {ts}", file=sys.stderr)
                    pre_repair_max_id = _get_current_max_market_id(db_path) if db_path else 0
                    count = _run_once()
                    if db_path:
                        print(f"[watch] Done. Stored/updated {count} markets.", file=sys.stderr)
                        if args.post_canonical_repair:
                            post_repair_max_id = _get_current_max_market_id(db_path)
                            _run_post_discovery_canonical_repair(
                                db_path,
                                start_id=args.post_repair_start_id if args.post_repair_start_id is not None else pre_repair_max_id,
                                max_id=post_repair_max_id,
                                batch_size=args.post_repair_batch_size,
                                workers=args.post_repair_workers,
                                limit=args.post_repair_limit,
                                run_retry=args.post_retry_fetch_failed,
                            )
                        if getattr(args, "post_refresh_category_tags", False):
                            print("[watch] Refreshing category/tags for existing markets with empty data...", file=sys.stderr)
                            refresh_category_tags_in_db(
                                db_path=db_path,
                                limit=args.refresh_category_tags_limit,
                                requests_delay=requests_delay,
                            )
                    else:
                        print(f"[watch] Done. Saved {count} markets to JSON.", file=sys.stderr)
                except Exception as e:
                    sleep_seconds = max(60, int(args.watch_error_interval))
                    _invalidate_session(str(e))
                    print(
                        f"[watch] ERROR run failed: {type(e).__name__}: {e}; "
                        f"sleeping {sleep_seconds}s before retry",
                        file=sys.stderr,
                    )
                next_ts = datetime.now(timezone.utc)
                print(
                    f"[watch] Sleeping {sleep_seconds}s until next run "
                    f"(~{(next_ts.replace(second=0, microsecond=0)).strftime('%H:%M')} + {sleep_seconds//60}m)...",
                    file=sys.stderr,
                )
                time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            print("\n[watch] Interrupted by user. Exiting.", file=sys.stderr)
    else:
        pre_repair_max_id = _get_current_max_market_id(db_path) if db_path else 0
        count = _run_once()
        if db_path:
            print(f"Database target: {describe_db_target()}", file=sys.stderr)
            print(f"Done. Stored/updated {count} markets to database.", file=sys.stderr)
            if args.post_canonical_repair:
                post_repair_max_id = _get_current_max_market_id(db_path)
                _run_post_discovery_canonical_repair(
                    db_path,
                    start_id=args.post_repair_start_id if args.post_repair_start_id is not None else pre_repair_max_id,
                    max_id=post_repair_max_id,
                    batch_size=args.post_repair_batch_size,
                    workers=args.post_repair_workers,
                    limit=args.post_repair_limit,
                    run_retry=args.post_retry_fetch_failed,
                )
            if getattr(args, "post_refresh_category_tags", False):
                print("Refreshing category/tags for existing markets with empty data...", file=sys.stderr)
                refresh_category_tags_in_db(
                    db_path=db_path,
                    limit=args.refresh_category_tags_limit,
                    requests_delay=requests_delay,
                )
        else:
            print(f"Done. Saved {count} markets to JSON.", file=sys.stderr)


if __name__ == "__main__":
    main()
