#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket market resolution fast sync.

目标：
1. 从 Gamma API 批量抓取已关闭 market
2. 将 local markets.id -> settlement_code 写入本地数据库
3. 提供尽可能快的本地结算查询层，供 PnL/历史曲线复用

表设计尽量轻量但查询友好：
    market_resolution_fast(
        market_id BIGINT PRIMARY KEY,  # local markets.id, not Gamma market id
        condition_id VARCHAR,
        slug VARCHAR,
        settlement_code TINYINT NOT NULL,
        closed_time DATETIME,
        updated_at DATETIME
    )

settlement_code 约定：
    1 = YES wins
    2 = NO wins
    3 = CANCELLED / void / 0.5-0.5
    0 = UNKNOWN / ambiguous

用法示例：
    # 全量同步
    python sync_market_resolution_fast.py

    # 只跑前 1 页做 smoke test
    python sync_market_resolution_fast.py --page-size 100 --max-pages 1

    # 增量同步（使用 sync_state 中保存的 Gamma updatedAt 水位）
    python sync_market_resolution_fast.py --mode incremental
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("Error: requests is not installed. Please install it with: pip install requests", file=sys.stderr)
    sys.exit(1)

# 保证 scripts 根目录在 path 中，便于 import db
_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import (
    add_db_cli_args,
    configure_db_from_args,
    describe_db_target,
    get_connection,
    init_schema,
)
from db.db import create_index_if_not_exists, get_table_columns, table_exists
from data_sources import POLYMARKET_ACTIVITY_API_URL, POLYMARKET_GAMMA_API_BASE


GAMMA_API_BASE = POLYMARKET_GAMMA_API_BASE
GAMMA_MARKETS_URL = f"{GAMMA_API_BASE}/markets"
GAMMA_EVENTS_URL = f"{GAMMA_API_BASE}/events"
ACTIVITY_API_URL = POLYMARKET_ACTIVITY_API_URL

DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES: Optional[int] = None
DEFAULT_MIN_PAGE_SIZE = 25
DEFAULT_REQUEST_TIMEOUT = (10, 45)
DEFAULT_PAGE_FETCH_ATTEMPTS = 2
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_FACTOR = 1.5
DEFAULT_SESSION_RECYCLE_EVERY = 200
DEFAULT_REQUESTS_DELAY = 0.1
DEFAULT_BULK_BAD_WINDOWS_LIMIT = 3
DEFAULT_FULL_MODE_BULK_MAX_PAGES = 120
DEFAULT_GAMMA_500_PAUSE_SECONDS = 15.0
DEFAULT_GAMMA_500_MAX_PAUSE_RETRIES = 6
DEFAULT_TARGETED_BATCH_SIZE = 200
DEFAULT_TARGETED_REQUESTS_DELAY = 0.05
DEFAULT_UNKNOWN_REVIEW_MAX_MARKETS: Optional[int] = None
DEFAULT_UNKNOWN_REVIEW_MAX_ADDRESSES = 25
DEFAULT_UNKNOWN_REVIEW_REQUESTS_DELAY = 0.05
SYNC_STATE_KEY_LAST_GAMMA_UPDATED_AT = "market_resolution_fast_last_gamma_updated_at"


def _create_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=0,
        connect=0,
        read=0,
        status=0,
        backoff_factor=DEFAULT_BACKOFF_FACTOR,
        status_forcelist=[],
        allowed_methods=["GET"],
        respect_retry_after_header=False,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=4, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_HTTP_SESSION: Optional[requests.Session] = None
_HTTP_SESSION_REQUESTS: int = 0


def _get_session(recycle_every: int = DEFAULT_SESSION_RECYCLE_EVERY) -> requests.Session:
    global _HTTP_SESSION, _HTTP_SESSION_REQUESTS
    if _HTTP_SESSION is None or _HTTP_SESSION_REQUESTS >= recycle_every:
        if _HTTP_SESSION is not None:
            try:
                _HTTP_SESSION.close()
            except Exception:
                pass
        _HTTP_SESSION = _create_session()
        _HTTP_SESSION_REQUESTS = 0
    return _HTTP_SESSION


def _invalidate_session() -> None:
    global _HTTP_SESSION, _HTTP_SESSION_REQUESTS
    if _HTTP_SESSION is not None:
        try:
            _HTTP_SESSION.close()
        except Exception:
            pass
    _HTTP_SESSION = None
    _HTTP_SESSION_REQUESTS = 0


def _response_json(resp: requests.Response, context: str) -> Any:
    try:
        return resp.json()
    except Exception as e:
        try:
            prefix = resp.text[:300].replace("\n", "\\n")
        except Exception:
            prefix = "<unavailable>"
        raise RuntimeError(f"{context}: invalid JSON response: {e}; body_prefix={prefix!r}") from e


def _exception_http_status(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    try:
        return int(response.status_code)
    except Exception:
        return None


def _is_gamma_server_http_error(exc: Exception) -> bool:
    status = _exception_http_status(exc)
    return status in (500, 502, 503, 504, 520, 521, 522, 523, 524)


def _fetch_closed_events_page(
    *,
    offset: int,
    limit: int,
    session_recycle_every: int = DEFAULT_SESSION_RECYCLE_EVERY,
) -> List[Dict[str, Any]]:
    params = {
        "limit": min(int(limit), 100),
        "offset": int(offset),
        "closed": "true",
        "order": "updatedAt",
        "ascending": "false",
    }
    last_err: Optional[Exception] = None
    for attempt in range(DEFAULT_PAGE_FETCH_ATTEMPTS):
        session = _get_session(recycle_every=session_recycle_every)
        resp = None
        try:
            resp = session.get(GAMMA_EVENTS_URL, params=params, timeout=DEFAULT_REQUEST_TIMEOUT)
            global _HTTP_SESSION_REQUESTS
            _HTTP_SESSION_REQUESTS += 1
            if resp.status_code in (500, 502, 503, 504, 520, 521, 522, 523, 524):
                raise requests.HTTPError(
                    f"Gamma events returned HTTP {resp.status_code} for offset={offset}, limit={params['limit']}",
                    response=resp,
                )
            resp.raise_for_status()
            data = _response_json(resp, f"GET {GAMMA_EVENTS_URL}")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("events", "data", "results", "items"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
            return []
        except Exception as e:
            last_err = e
            _invalidate_session()
            if attempt < DEFAULT_PAGE_FETCH_ATTEMPTS - 1:
                delay = DEFAULT_BACKOFF_FACTOR ** (attempt + 1)
                print(f"[gamma] retry {attempt + 1}/{DEFAULT_PAGE_FETCH_ATTEMPTS} in {delay:.1f}s: {e}", file=sys.stderr)
                time.sleep(delay)
        finally:
            try:
                if resp is not None:
                    resp.close()
            except Exception:
                pass
    if last_err is not None:
        raise last_err
    return []


def _fetch_closed_events_page_adaptive(
    *,
    offset: int,
    limit: int,
    min_limit: int = DEFAULT_MIN_PAGE_SIZE,
    session_recycle_every: int = DEFAULT_SESSION_RECYCLE_EVERY,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Gamma 在部分 offset 上会对大页请求返回 500。
    对 HTTP 500 一类服务端错误，保持原 limit 原地等待后重试；
    对非 5xx 异常，再采用自适应降页。
    """
    page_limit = max(int(limit), 1)
    floor = max(int(min_limit), 1)
    last_err: Optional[Exception] = None
    gamma_500_pause_retries = 0

    while page_limit >= floor:
        try:
            page = _fetch_closed_events_page(
                offset=offset,
                limit=page_limit,
                session_recycle_every=session_recycle_every,
            )
            return page, page_limit
        except Exception as e:
            last_err = e
            if _is_gamma_server_http_error(e):
                gamma_500_pause_retries += 1
                if gamma_500_pause_retries > DEFAULT_GAMMA_500_MAX_PAUSE_RETRIES:
                    break
                delay = DEFAULT_GAMMA_500_PAUSE_SECONDS * gamma_500_pause_retries
                print(
                    f"[gamma-events] offset={offset} limit={page_limit} got transient HTTP "
                    f"{_exception_http_status(e)}; wait {delay:.1f}s and retry same page_size",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            if page_limit == floor:
                break
            next_limit = max(floor, page_limit // 2)
            if next_limit == page_limit:
                next_limit = floor
            print(
                f"[gamma-events] offset={offset} limit={page_limit} failed; fallback to smaller page_size={next_limit}: {e}",
                file=sys.stderr,
            )
            page_limit = next_limit

    if last_err is not None:
        raise last_err
    return [], page_limit


def _recover_bad_offset_window(
    conn,
    *,
    offset: int,
    window_size: int,
    session_recycle_every: int = DEFAULT_SESSION_RECYCLE_EVERY,
) -> Tuple[int, int, int]:
    """
    当某个 event offset 区间在最小页大小下仍然失败时，退化成 limit=1 逐个 event 尝试。
    返回 (market_rows_seen, rows_written, event_rows_skipped)。
    """
    rows_seen = 0
    rows_written = 0
    rows_skipped = 0
    for rel_offset in range(offset, offset + window_size):
        try:
            events = _fetch_closed_events_page(
                offset=rel_offset,
                limit=1,
                session_recycle_every=session_recycle_every,
            )
        except Exception as e:
            rows_skipped += 1
            print(f"[gamma-events] skip bad offset={rel_offset}: {e}", file=sys.stderr)
            continue
        page = _flatten_markets_from_events(events)
        if not page:
            rows_skipped += 1
            continue
        gamma_rows, matched_rows, written_rows, _, _, _ = _sync_page(conn, page)
        rows_seen += gamma_rows
        rows_written += written_rows
        if matched_rows == 0:
            rows_skipped += 1
    return rows_seen, rows_written, rows_skipped


def _flatten_markets_from_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    page: List[Dict[str, Any]] = []
    for event in events or []:
        markets = event.get("markets") or []
        if not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict):
                continue
            page.append(market)
    return page


def _ensure_0x(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("0x"):
        return text.lower()
    return f"0x{text}".lower()


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        clean = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _format_db_datetime(value: Any) -> Optional[str]:
    dt = _parse_iso_datetime(value)
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


def _gamma_closed_time(raw: Dict[str, Any]) -> Optional[str]:
    for key in ("closedTime", "closed_time", "endDate", "end_date", "updatedAt", "updated_at"):
        value = raw.get(key)
        formatted = _format_db_datetime(value)
        if formatted:
            return formatted
    return None


def _parse_outcome_prices(raw: Any) -> Optional[Tuple[float, float]]:
    if raw is None:
        return None
    values: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            values = json.loads(text)
        except Exception:
            return None
    if not isinstance(values, (list, tuple)) or len(values) < 2:
        return None
    try:
        a = float(values[0])
        b = float(values[1])
    except Exception:
        return None
    return (a, b)


def _settlement_code_from_outcome_prices(raw: Any) -> int:
    parsed = _parse_outcome_prices(raw)
    if not parsed:
        return 0
    yes, no = parsed
    eps = 0.01
    if abs(yes - 1.0) <= eps and abs(no - 0.0) <= eps:
        return 1
    if abs(yes - 0.0) <= eps and abs(no - 1.0) <= eps:
        return 2
    if abs(yes - 0.5) <= eps and abs(no - 0.5) <= eps:
        return 3
    if abs((yes + no) - 1.0) <= 0.02:
        if yes > no:
            return 1
        if no > yes:
            return 2
    return 0


def _classify_outcome_prices_issue(raw: Any) -> str:
    parsed = _parse_outcome_prices(raw)
    if parsed is None:
        if raw is None:
            return "missing_outcome_prices"
        text = str(raw).strip()
        return "parse_error" if text else "missing_outcome_prices"

    yes, no = parsed
    eps = 0.01
    if abs(yes) <= eps and abs(no) <= eps:
        return "zero_zero"
    if abs(yes - 1.0) <= eps and abs(no) <= eps:
        return "standard_yes"
    if abs(yes) <= eps and abs(no - 1.0) <= eps:
        return "standard_no"
    if abs(yes - 0.5) <= eps and abs(no - 0.5) <= eps:
        return "standard_cancel"
    if abs((yes + no) - 1.0) <= 0.02:
        return "nonstandard_decimal"
    return "abnormal_outcome_prices"


def _close_enough(value: float, expected: float, *, relative_tol: float = 0.02, absolute_floor: float = 1e-6) -> bool:
    scale = max(abs(float(expected)) * relative_tol, absolute_floor)
    return abs(float(value) - float(expected)) <= scale


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _fetch_user_market_redeems(
    user: str,
    condition_id: str,
    *,
    session_recycle_every: int = DEFAULT_SESSION_RECYCLE_EVERY,
) -> List[Dict[str, Any]]:
    user = str(user or "").strip().lower()
    condition_id = _ensure_0x(condition_id)
    if not user or not condition_id:
        return []

    params = {
        "user": user,
        "market": condition_id,
        "type": "REDEEM",
    }
    last_err: Optional[Exception] = None
    for attempt in range(DEFAULT_PAGE_FETCH_ATTEMPTS):
        session = _get_session(recycle_every=session_recycle_every)
        resp = None
        try:
            resp = session.get(ACTIVITY_API_URL, params=params, timeout=DEFAULT_REQUEST_TIMEOUT)
            global _HTTP_SESSION_REQUESTS
            _HTTP_SESSION_REQUESTS += 1
            if resp.status_code in (429, 500, 502, 503, 504, 520, 521, 522, 523, 524):
                raise requests.HTTPError(
                    f"Activity returned HTTP {resp.status_code} for user={user} market={condition_id}",
                    response=resp,
                )
            resp.raise_for_status()
            data = _response_json(resp, f"GET {ACTIVITY_API_URL}")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for key in ("activity", "data", "results", "items"):
                    if key in data and isinstance(data[key], list):
                        return data[key]
            return []
        except Exception as e:
            last_err = e
            _invalidate_session()
            if attempt < DEFAULT_PAGE_FETCH_ATTEMPTS - 1:
                delay = DEFAULT_BACKOFF_FACTOR ** (attempt + 1)
                print(
                    f"[activity] retry {attempt + 1}/{DEFAULT_PAGE_FETCH_ATTEMPTS} in {delay:.1f}s: {e}",
                    file=sys.stderr,
                )
                time.sleep(delay)
        finally:
            try:
                if resp is not None:
                    resp.close()
            except Exception:
                pass
    if last_err is not None:
        raise last_err
    return []


def _next_unknown_markets_batch(
    conn,
    *,
    batch_size: int,
) -> List[Dict[str, Any]]:
    cursor = conn.cursor()
    now_expr = "NOW()" if hasattr(conn, "_raw_conn") else "datetime('now')"
    cursor.execute(
        f"""
        SELECT
            m.id AS market_id,
            COALESCE(NULLIF(r.condition_id, ''), m.condition_id) AS condition_id,
            COALESCE(NULLIF(r.slug, ''), m.slug) AS slug,
            r.closed_time AS closed_time,
            r.updated_at AS updated_at,
            m.end_date AS end_date
        FROM market_resolution_fast r
        JOIN markets m ON m.id = r.market_id
        WHERE r.settlement_code = 0
          AND m.end_date IS NOT NULL
          AND m.end_date <= {now_expr}
        ORDER BY m.end_date ASC, m.id ASC
        LIMIT ?
        """,
        (int(batch_size),),
    )
    rows = cursor.fetchall()
    result: List[Dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "market_id": int(row["market_id"] if hasattr(row, "keys") else row[0]),
                "condition_id": _ensure_0x(row["condition_id"] if hasattr(row, "keys") else row[1]),
                "slug": str((row["slug"] if hasattr(row, "keys") else row[2]) or "").strip().lower(),
                "closed_time": row["closed_time"] if hasattr(row, "keys") else row[3],
                "updated_at": row["updated_at"] if hasattr(row, "keys") else row[4],
                "end_date": row["end_date"] if hasattr(row, "keys") else row[5],
            }
        )
    return result


def _load_unknown_markets_for_review(
    conn,
    *,
    max_markets: Optional[int],
) -> List[Dict[str, Any]]:
    limit = 1000000 if max_markets is None else max(0, int(max_markets))
    if limit == 0:
        return []
    return _next_unknown_markets_batch(conn, batch_size=limit)


def _load_market_participant_nets(
    conn,
    *,
    market_id: int,
    limit: int,
) -> List[Dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            ta.address,
            SUM(
                CASE
                    WHEN ta.outcome = 'YES' AND ta.side_for_address = 'BUY' THEN ta.size
                    WHEN ta.outcome = 'YES' AND ta.side_for_address = 'SELL' THEN -ta.size
                    ELSE 0
                END
            ) AS yes_net_shares,
            SUM(
                CASE
                    WHEN ta.outcome = 'NO' AND ta.side_for_address = 'BUY' THEN ta.size
                    WHEN ta.outcome = 'NO' AND ta.side_for_address = 'SELL' THEN -ta.size
                    ELSE 0
                END
            ) AS no_net_shares,
            SUM(COALESCE(ta.notional, 0)) AS gross_notional,
            COUNT(*) AS trade_count
        FROM trade_addresses ta
        WHERE ta.market_id = ?
          AND ta.address IS NOT NULL
          AND TRIM(ta.address) <> ''
        GROUP BY ta.address
        HAVING yes_net_shares > 0 OR no_net_shares > 0
        ORDER BY gross_notional DESC, trade_count DESC, ta.address ASC
        LIMIT ?
        """,
        (int(market_id), int(limit)),
    )
    rows = cursor.fetchall()
    result: List[Dict[str, Any]] = []
    for row in rows:
        address = str(row["address"] if hasattr(row, "keys") else row[0]).strip().lower()
        result.append(
            {
                "address": address,
                "yes_net_shares": _safe_float(row["yes_net_shares"] if hasattr(row, "keys") else row[1]),
                "no_net_shares": _safe_float(row["no_net_shares"] if hasattr(row, "keys") else row[2]),
                "gross_notional": _safe_float(row["gross_notional"] if hasattr(row, "keys") else row[3]),
                "trade_count": int((row["trade_count"] if hasattr(row, "keys") else row[4]) or 0),
            }
        )
    return result


def _infer_redeem_resolution_vote(
    *,
    redeemed_usdc: float,
    yes_net_shares: float,
    no_net_shares: float,
) -> Tuple[int, str]:
    yes_net_shares = max(0.0, float(yes_net_shares))
    no_net_shares = max(0.0, float(no_net_shares))
    redeemed_usdc = max(0.0, float(redeemed_usdc))

    if redeemed_usdc <= 0:
        return 0, "zero_redeem"

    if yes_net_shares > 0 and _close_enough(redeemed_usdc, yes_net_shares):
        return 1, "redeem_matches_yes"
    if no_net_shares > 0 and _close_enough(redeemed_usdc, no_net_shares):
        return 2, "redeem_matches_no"

    if yes_net_shares > 0 and no_net_shares <= 0 and _close_enough(redeemed_usdc, 0.5 * yes_net_shares):
        return 3, "redeem_matches_cancel_yes_half"
    if no_net_shares > 0 and yes_net_shares <= 0 and _close_enough(redeemed_usdc, 0.5 * no_net_shares):
        return 3, "redeem_matches_cancel_no_half"

    total_half = 0.5 * (yes_net_shares + no_net_shares)
    if total_half > 0 and _close_enough(redeemed_usdc, total_half):
        return 3, "redeem_matches_cancel_split"

    return 0, "abnormal_redeem_payout"


def _pick_redeem_ratio_sample(record: Dict[str, Any]) -> Optional[float]:
    ratios: List[float] = []
    for key in ("yes_ratio", "no_ratio"):
        value = record.get(key)
        if value is None:
            continue
        ratio = _safe_float(value)
        if ratio > 0:
            ratios.append(ratio)
    if not ratios:
        return None
    bounded = [ratio for ratio in ratios if ratio <= 1.2]
    if bounded:
        return min(bounded)
    return min(ratios)


def _classify_unresolved_issue(
    *,
    raw_issue: str,
    redeem_hit_count: int,
    vote_codes: Sequence[int],
    abnormal_samples: Sequence[Dict[str, Any]],
    fetch_errors: Sequence[str],
) -> Tuple[str, str, Dict[str, Any]]:
    if vote_codes:
        return (
            "conflicting_redeem_votes",
            "不同地址的 redeem 复核给出了冲突结论",
            {},
        )

    if abnormal_samples:
        ratios = [ratio for ratio in (_pick_redeem_ratio_sample(item) for item in abnormal_samples) if ratio is not None]
        extra: Dict[str, Any] = {}
        if ratios:
            ratios_sorted = sorted(ratios)
            median_ratio = ratios_sorted[len(ratios_sorted) // 2]
            extra["ratio_min"] = min(ratios_sorted)
            extra["ratio_max"] = max(ratios_sorted)
            extra["ratio_median"] = median_ratio
            if all(abs(ratio - 0.925) <= 0.01 for ratio in ratios_sorted):
                return (
                    "payout_ratio_0_925",
                    "查到 redeem，且 payout 比例稳定在 0.925 左右，属于非标准 payout market",
                    extra,
                )
            if all(0 < ratio < 0.98 for ratio in ratios_sorted):
                return (
                    "partial_redeem",
                    "查到 redeem，但回收金额系统性低于标准 1x payout，更像部分赎回或非标准回收",
                    extra,
                )
            return (
                "abnormal_redeem_payout_other",
                "查到 redeem，但金额与 YES/NO/取消三种标准 payout 都不匹配",
                extra,
            )
        return (
            "abnormal_redeem_payout_other",
            "查到 redeem，但金额与 YES/NO/取消三种标准 payout 都不匹配",
            {},
        )

    if redeem_hit_count == 0:
        if raw_issue == "missing_outcome_prices":
            return ("missing_outcome_prices", "Gamma 缺少 outcomePrices，且抽样地址未查到 redeem", {})
        if raw_issue == "parse_error":
            return ("parse_error", "Gamma 返回了无法解析的 outcomePrices，且抽样地址未查到 redeem", {})
        if raw_issue == "nonstandard_decimal":
            return ("nonstandard_decimal", "Gamma 返回了非标准小数结算，但 redeem 线索不足以确认方向", {})
        if raw_issue == "abnormal_outcome_prices":
            return ("abnormal_outcome_prices", "Gamma 返回了异常 outcomePrices，且抽样地址未查到 redeem", {})
        return ("no_redeem_activity_found", "抽样地址未查到该 market 的 REDEEM 活动", {})

    if fetch_errors:
        return ("activity_fetch_error", "activity endpoint 取数失败", {})

    if raw_issue == "missing_outcome_prices":
        return ("missing_outcome_prices", "Gamma 缺少 outcomePrices，且现有 redeem 线索不足以推出唯一结论", {})
    if raw_issue == "parse_error":
        return ("parse_error", "Gamma 返回了无法解析的 outcomePrices，且现有 redeem 线索不足以推出唯一结论", {})
    if raw_issue == "nonstandard_decimal":
        return ("nonstandard_decimal", "Gamma 返回了非标准小数结算，且现有 redeem 线索不足以推出唯一结论", {})

    return ("review_inconclusive", "存在 redeem 线索，但不足以推出唯一结论", {})


def _review_unknown_markets_via_activity(
    conn,
    *,
    max_markets: Optional[int],
    max_addresses_per_market: int,
    requests_delay: float,
    repair_list_json: Optional[str] = None,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "candidate_markets": 0,
        "reviewed_markets": 0,
        "resolved_markets": 0,
        "resolved_counts": {"1": 0, "2": 0, "3": 0},
        "repair_counts": {},
        "repair_candidates": [],
        "resolved_samples": [],
    }

    markets = _load_unknown_markets_for_review(conn, max_markets=max_markets)
    summary["candidate_markets"] = len(markets)

    for market in markets:
        summary["reviewed_markets"] += 1
        market_id = int(market["market_id"])
        slug = str(market["slug"] or "")
        condition_id = _ensure_0x(market["condition_id"])

        raw_market: Optional[Dict[str, Any]] = None
        raw_issue = "missing_outcome_prices"
        raw_outcome_prices: Any = None
        try:
            raw_market = _fetch_market_by_slug_resolution(slug) if slug else None
        except Exception as e:
            raw_issue = f"gamma_fetch_error:{e}"
        if raw_market:
            raw_outcome_prices = raw_market.get("outcomePrices") or raw_market.get("outcome_prices")
            raw_issue = _classify_outcome_prices_issue(raw_outcome_prices)

        participants = _load_market_participant_nets(
            conn,
            market_id=market_id,
            limit=max(1, int(max_addresses_per_market)),
        )
        if not participants:
            issue = "no_local_participants"
            summary["repair_counts"][issue] = summary["repair_counts"].get(issue, 0) + 1
            summary["repair_candidates"].append(
                {
                    "market_id": market_id,
                    "slug": slug,
                    "condition_id": condition_id,
                    "issue_type": issue,
                    "raw_issue_type": raw_issue,
                    "raw_outcome_prices": raw_outcome_prices,
                    "notes": "trade_addresses 中没有可用于净头寸复核的地址样本",
                }
            )
            continue

        votes: List[Dict[str, Any]] = []
        abnormal_samples: List[Dict[str, Any]] = []
        fetch_errors: List[str] = []
        redeem_hit_count = 0

        for participant in participants:
            address = str(participant["address"] or "").strip().lower()
            if not address:
                continue
            try:
                redeems = _fetch_user_market_redeems(address, condition_id)
            except Exception as e:
                fetch_errors.append(f"{address}:{e}")
                if requests_delay > 0:
                    time.sleep(requests_delay)
                continue

            if not redeems:
                if requests_delay > 0:
                    time.sleep(requests_delay)
                continue

            redeem_hit_count += 1
            redeemed_usdc = sum(_safe_float(item.get("usdcSize") or item.get("size")) for item in redeems)
            vote_code, vote_reason = _infer_redeem_resolution_vote(
                redeemed_usdc=redeemed_usdc,
                yes_net_shares=float(participant["yes_net_shares"]),
                no_net_shares=float(participant["no_net_shares"]),
            )
            yes_ratio = (
                redeemed_usdc / float(participant["yes_net_shares"])
                if float(participant["yes_net_shares"]) > 0
                else None
            )
            no_ratio = (
                redeemed_usdc / float(participant["no_net_shares"])
                if float(participant["no_net_shares"]) > 0
                else None
            )
            record = {
                "address": address,
                "redeem_events": len(redeems),
                "redeemed_usdc": redeemed_usdc,
                "yes_net_shares": float(participant["yes_net_shares"]),
                "no_net_shares": float(participant["no_net_shares"]),
                "yes_ratio": yes_ratio,
                "no_ratio": no_ratio,
                "vote_code": vote_code,
                "vote_reason": vote_reason,
            }
            if vote_code in (1, 2, 3):
                votes.append(record)
            elif len(abnormal_samples) < 5:
                abnormal_samples.append(record)
            if requests_delay > 0:
                time.sleep(requests_delay)

        vote_codes = sorted({int(item["vote_code"]) for item in votes if int(item["vote_code"]) in (1, 2, 3)})
        if len(vote_codes) == 1:
            resolved_code = vote_codes[0]
            _upsert_rows(
                conn,
                [
                    (
                        market_id,
                        condition_id,
                        slug,
                        resolved_code,
                        str(market["closed_time"]) if market.get("closed_time") is not None else None,
                        str(market["updated_at"]) if market.get("updated_at") is not None else None,
                    )
                ],
            )
            conn.commit()
            summary["resolved_markets"] += 1
            summary["resolved_counts"][str(resolved_code)] += 1
            if len(summary["resolved_samples"]) < 10:
                summary["resolved_samples"].append(
                    {
                        "market_id": market_id,
                        "slug": slug,
                        "condition_id": condition_id,
                        "resolved_code": resolved_code,
                        "raw_issue_type": raw_issue,
                        "raw_outcome_prices": raw_outcome_prices,
                        "supporting_votes": votes[:3],
                    }
                )
            continue

        issue, note, extra_issue_fields = _classify_unresolved_issue(
            raw_issue=raw_issue,
            redeem_hit_count=redeem_hit_count,
            vote_codes=vote_codes,
            abnormal_samples=abnormal_samples,
            fetch_errors=fetch_errors,
        )

        summary["repair_counts"][issue] = summary["repair_counts"].get(issue, 0) + 1
        candidate = {
            "market_id": market_id,
            "slug": slug,
            "condition_id": condition_id,
            "issue_type": issue,
            "raw_issue_type": raw_issue,
            "raw_outcome_prices": raw_outcome_prices,
            "redeem_hits": redeem_hit_count,
            "abnormal_samples": abnormal_samples,
            "vote_samples": votes[:5],
            "fetch_errors": fetch_errors[:5],
            "notes": note,
        }
        candidate.update(extra_issue_fields)
        summary["repair_candidates"].append(candidate)

    if repair_list_json:
        out_path = Path(repair_list_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(summary["repair_candidates"], f, ensure_ascii=False, indent=2)

    return summary


def _get_sync_state(conn, key: str) -> Optional[datetime]:
    cur = conn.cursor()
    cur.execute("SELECT value FROM sync_state WHERE key = ?", (key,))
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return _parse_iso_datetime(row[0])


def _set_sync_state(conn, key: str, value: datetime) -> None:
    ts = value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sync_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value.astimezone(timezone.utc).isoformat(), ts),
    )


def _ensure_schema(conn) -> None:
    if not table_exists(conn, "market_resolution_fast"):
        if hasattr(conn, "_raw_conn"):
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_resolution_fast (
                    market_id BIGINT NOT NULL PRIMARY KEY,
                    condition_id VARCHAR(255),
                    slug VARCHAR(255),
                    settlement_code TINYINT NOT NULL,
                    closed_time DATETIME(6),
                    updated_at DATETIME(6)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_resolution_fast (
                    market_id INTEGER PRIMARY KEY,
                    condition_id TEXT,
                    slug TEXT,
                    settlement_code INTEGER NOT NULL,
                    closed_time TEXT,
                    updated_at TEXT
                )
                """
            )

    # 兼容已有的旧表结构：补列，不重建
    existing_cols = set(get_table_columns(conn, "market_resolution_fast"))
    for column_name, column_type in (
        ("condition_id", "VARCHAR(255)" if hasattr(conn, "_raw_conn") else "TEXT"),
        ("slug", "VARCHAR(255)" if hasattr(conn, "_raw_conn") else "TEXT"),
        ("closed_time", "DATETIME(6)" if hasattr(conn, "_raw_conn") else "TEXT"),
        ("updated_at", "DATETIME(6)" if hasattr(conn, "_raw_conn") else "TEXT"),
    ):
        if column_name not in existing_cols:
            conn.execute(f"ALTER TABLE market_resolution_fast ADD COLUMN {column_name} {column_type}")

    # 常用查询索引：按 market_id、condition_id、slug、settlement_code、closed_time
    create_index_if_not_exists(conn, "market_resolution_fast", "idx_mrf_condition_id", ["condition_id"])
    create_index_if_not_exists(conn, "market_resolution_fast", "idx_mrf_slug", ["slug"])
    create_index_if_not_exists(conn, "market_resolution_fast", "idx_mrf_settlement_code", ["settlement_code"])
    create_index_if_not_exists(conn, "market_resolution_fast", "idx_mrf_closed_time", ["closed_time"])

    # 把已有旧行补齐 condition_id / slug，避免迁移后仍然只能按 market_id 查
    try:
        if hasattr(conn, "_raw_conn"):
            conn.execute(
                """
                UPDATE market_resolution_fast mrf
                JOIN markets m ON m.id = mrf.market_id
                SET
                    mrf.condition_id = COALESCE(NULLIF(mrf.condition_id, ''), m.condition_id),
                    mrf.slug = COALESCE(NULLIF(mrf.slug, ''), m.slug)
                WHERE mrf.condition_id IS NULL OR mrf.condition_id = '' OR mrf.slug IS NULL OR mrf.slug = ''
                """
            )
        else:
            conn.execute(
                """
                UPDATE market_resolution_fast
                SET
                    condition_id = COALESCE(condition_id, (
                        SELECT condition_id FROM markets WHERE markets.id = market_resolution_fast.market_id
                    )),
                    slug = COALESCE(slug, (
                        SELECT slug FROM markets WHERE markets.id = market_resolution_fast.market_id
                    ))
                WHERE condition_id IS NULL OR condition_id = '' OR slug IS NULL OR slug = ''
                """
            )
    except Exception:
        # 旧表迁移时如果 join/子查询失败，不影响后续增量写入
        pass


def _chunked(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _load_market_mapping(
    conn,
    *,
    condition_ids: List[str],
    slugs: List[str],
) -> Dict[str, int]:
    """
    返回 gamma 条目 key -> local market_id 的映射。
    以 condition_id 为主，slug 为辅。
    """
    mapping: Dict[str, int] = {}
    cursor = conn.cursor()

    if condition_ids:
        for chunk in _chunked(condition_ids, 500):
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"SELECT id, condition_id FROM markets WHERE condition_id IN ({placeholders})",
                tuple(chunk),
            )
            for row in cursor.fetchall():
                market_id = int(row[0])
                condition_id = str(row[1]).lower()
                mapping[condition_id] = market_id

    missing_slugs = [slug for slug in slugs if slug and slug.lower() not in mapping]
    if missing_slugs:
        for chunk in _chunked(missing_slugs, 500):
            placeholders = ",".join(["?"] * len(chunk))
            cursor.execute(
                f"SELECT id, slug FROM markets WHERE slug IN ({placeholders})",
                tuple(chunk),
            )
            for row in cursor.fetchall():
                market_id = int(row[0])
                slug = str(row[1]).lower()
                mapping[slug] = market_id

    return mapping


def _upsert_rows(conn, rows: List[Tuple[int, str, str, int, Optional[str], Optional[str]]]) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO market_resolution_fast (
            market_id, condition_id, slug, settlement_code, closed_time, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_id) DO UPDATE SET
            condition_id = excluded.condition_id,
            slug = excluded.slug,
            settlement_code = excluded.settlement_code,
            closed_time = excluded.closed_time,
            updated_at = excluded.updated_at
        """,
        rows,
    )
    _refresh_market_status_snapshot(conn, [int(row[0]) for row in rows])
    return len(rows)


def _refresh_market_status_snapshot(conn, market_ids: Sequence[int]) -> int:
    if not market_ids or not table_exists(conn, "market_status_snapshot"):
        return 0
    from db.sync_trade_analytics import _upsert_market_status_snapshot

    return _upsert_market_status_snapshot(conn, sorted({int(market_id) for market_id in market_ids if market_id}))


def _fetch_market_by_slug_resolution(
    slug: str,
    *,
    session_recycle_every: int = DEFAULT_SESSION_RECYCLE_EVERY,
) -> Optional[Dict[str, Any]]:
    global _HTTP_SESSION_REQUESTS
    slug = str(slug or "").strip()
    if not slug:
        return None

    last_err: Optional[Exception] = None
    url = f"{GAMMA_MARKETS_URL}/slug/{slug}"
    for attempt in range(DEFAULT_PAGE_FETCH_ATTEMPTS):
        session = _get_session(recycle_every=session_recycle_every)
        resp = None
        try:
            resp = session.get(url, timeout=DEFAULT_REQUEST_TIMEOUT)
            global _HTTP_SESSION_REQUESTS
            _HTTP_SESSION_REQUESTS += 1
            if resp.status_code in (500, 502, 503, 504, 520, 521, 522, 523, 524):
                raise requests.HTTPError(
                    f"Gamma market-by-slug returned HTTP {resp.status_code} for slug={slug}",
                    response=resp,
                )
            resp.raise_for_status()
            data = _response_json(resp, f"GET {url}")
            if isinstance(data, dict) and data:
                return data
        except Exception as e:
            last_err = e
            status = _exception_http_status(e)
            if status == 404:
                # A fair number of archived markets are not available via /markets/slug/{slug}.
                # Treat this as a normal miss and fall through to the query-based fallback quietly.
                break
            _invalidate_session()
            if attempt < DEFAULT_PAGE_FETCH_ATTEMPTS - 1:
                delay = DEFAULT_BACKOFF_FACTOR ** (attempt + 1)
                print(f"[gamma-slug] retry {attempt + 1}/{DEFAULT_PAGE_FETCH_ATTEMPTS} in {delay:.1f}s: {e}", file=sys.stderr)
                time.sleep(delay)
        finally:
            try:
                if resp is not None:
                    resp.close()
            except Exception:
                pass

    # Fallback: some archived markets are easier to retrieve from /markets?slug=...&closed=true&archived=true
    params = {
        "slug": slug,
        "closed": "true",
        "archived": "true",
        "limit": 10,
    }
    for attempt in range(DEFAULT_PAGE_FETCH_ATTEMPTS):
        session = _get_session(recycle_every=session_recycle_every)
        resp = None
        try:
            resp = session.get(GAMMA_MARKETS_URL, params=params, timeout=DEFAULT_REQUEST_TIMEOUT)
            _HTTP_SESSION_REQUESTS += 1
            if resp.status_code in (500, 502, 503, 504, 520, 521, 522, 523, 524):
                raise requests.HTTPError(
                    f"Gamma markets-by-slug-query returned HTTP {resp.status_code} for slug={slug}",
                    response=resp,
                )
            resp.raise_for_status()
            data = _response_json(resp, f"GET {GAMMA_MARKETS_URL}")
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                for key in ("markets", "data", "results", "items"):
                    if key in data and isinstance(data[key], list) and data[key]:
                        return data[key][0]
        except Exception as e:
            last_err = e
            status = _exception_http_status(e)
            if status == 404:
                return None
            _invalidate_session()
            if attempt < DEFAULT_PAGE_FETCH_ATTEMPTS - 1:
                delay = DEFAULT_BACKOFF_FACTOR ** (attempt + 1)
                print(f"[gamma-slug] fallback retry {attempt + 1}/{DEFAULT_PAGE_FETCH_ATTEMPTS} in {delay:.1f}s: {e}", file=sys.stderr)
                time.sleep(delay)
        finally:
            try:
                if resp is not None:
                    resp.close()
            except Exception:
                pass

    if last_err is not None:
        status = _exception_http_status(last_err)
        if status == 404:
            return None
        raise last_err
    return None


def _next_missing_ended_markets_batch(
    conn,
    *,
    batch_size: int,
) -> List[Tuple[int, str, str]]:
    cursor = conn.cursor()
    now_expr = "NOW()" if hasattr(conn, "_raw_conn") else "datetime('now')"
    cursor.execute(
        f"""
        SELECT m.id, m.condition_id, m.slug
        FROM markets m
        LEFT JOIN market_resolution_fast r ON r.market_id = m.id
        WHERE m.end_date IS NOT NULL
          AND m.end_date <= {now_expr}
          AND m.slug IS NOT NULL
          AND TRIM(m.slug) <> ''
          AND r.market_id IS NULL
        ORDER BY m.end_date ASC, m.id ASC
        LIMIT ?
        """,
        (int(batch_size),),
    )
    rows = cursor.fetchall()
    return [(int(row[0]), str(row[1] or ""), str(row[2] or "")) for row in rows]


def _targeted_fill_missing_markets(
    conn,
    *,
    max_markets: Optional[int],
    batch_size: int,
    requests_delay: float,
) -> Dict[str, Any]:
    summary = {
        "candidate_rows": 0,
        "attempted": 0,
        "written": 0,
        "written_unknown_markers": 0,
        "not_found": 0,
        "unknown": 0,
        "errors": 0,
        "samples_failed": [],
    }
    processed = 0

    while True:
        remaining = None if max_markets is None else max(0, int(max_markets) - processed)
        if remaining == 0:
            break
        current_batch_size = batch_size if remaining is None else min(batch_size, remaining)
        batch = _next_missing_ended_markets_batch(
            conn,
            batch_size=current_batch_size,
        )
        if not batch:
            break

        summary["candidate_rows"] += len(batch)
        upsert_rows: List[Tuple[int, str, str, int, Optional[str], Optional[str]]] = []

        for market_id, condition_id, slug in batch:
            processed += 1
            summary["attempted"] += 1
            try:
                raw = _fetch_market_by_slug_resolution(slug)
            except Exception as e:
                summary["errors"] += 1
                if len(summary["samples_failed"]) < 10:
                    summary["samples_failed"].append(
                        {"market_id": market_id, "slug": slug, "reason": str(e)}
                    )
                if requests_delay > 0:
                    time.sleep(requests_delay)
                continue

            if not raw:
                summary["not_found"] += 1
                if len(summary["samples_failed"]) < 10:
                    summary["samples_failed"].append(
                        {"market_id": market_id, "slug": slug, "reason": "not_found"}
                    )
                if requests_delay > 0:
                    time.sleep(requests_delay)
                continue

            settlement_code = _settlement_code_from_outcome_prices(
                raw.get("outcomePrices") or raw.get("outcome_prices")
            )
            if settlement_code == 0:
                summary["unknown"] += 1
                summary["written_unknown_markers"] += 1
                upsert_rows.append(
                    (
                        market_id,
                        _ensure_0x(raw.get("conditionId") or raw.get("condition_id") or condition_id),
                        str(raw.get("slug") or slug).strip().lower(),
                        0,
                        _gamma_closed_time(raw),
                        _format_db_datetime(raw.get("updatedAt") or raw.get("updated_at")),
                    )
                )
                if len(summary["samples_failed"]) < 10:
                    summary["samples_failed"].append(
                        {
                            "market_id": market_id,
                            "slug": slug,
                            "reason": "unknown_outcome_prices",
                            "outcome_prices": raw.get("outcomePrices") or raw.get("outcome_prices"),
                        }
                    )
                if requests_delay > 0:
                    time.sleep(requests_delay)
                continue

            upsert_rows.append(
                (
                    market_id,
                    _ensure_0x(raw.get("conditionId") or raw.get("condition_id") or condition_id),
                    str(raw.get("slug") or slug).strip().lower(),
                    settlement_code,
                    _gamma_closed_time(raw),
                    _format_db_datetime(raw.get("updatedAt") or raw.get("updated_at")),
                )
            )
            if requests_delay > 0:
                time.sleep(requests_delay)

        if upsert_rows:
            summary["written"] += _upsert_rows(conn, upsert_rows)
            conn.commit()

        print(
            f"[targeted-fill] processed={processed} attempted={summary['attempted']} written={summary['written']} "
            f"not_found={summary['not_found']} unknown={summary['unknown']} errors={summary['errors']}",
            file=sys.stderr,
        )

    return summary


def _sync_page(
    conn,
    page: List[Dict[str, Any]],
) -> Tuple[int, int, int, Dict[int, int], Optional[datetime], int]:
    """
    返回：
        (gamma_rows, matched_rows, written_rows, settlement_counts, max_gamma_updated_at, unknown_rows)
    """
    if not page:
        return (0, 0, 0, {}, None, 0)

    condition_ids: List[str] = []
    slugs: List[str] = []
    normalized: List[Dict[str, Any]] = []
    settlement_counts: Dict[int, int] = {}
    max_updated_at: Optional[datetime] = None
    unknown_rows = 0

    for item in page:
        condition_id = _ensure_0x(item.get("conditionId") or item.get("condition_id"))
        slug = str(item.get("slug") or item.get("market_slug") or "").strip().lower()
        settlement_code = _settlement_code_from_outcome_prices(item.get("outcomePrices") or item.get("outcome_prices"))
        updated_at = _parse_iso_datetime(item.get("updatedAt") or item.get("updated_at"))

        normalized.append(
            {
                "condition_id": condition_id,
                "slug": slug,
                "settlement_code": settlement_code,
                "closed_time": _gamma_closed_time(item),
                "updated_at_str": _format_db_datetime(item.get("updatedAt") or item.get("updated_at")),
                "updated_at": updated_at,
            }
        )
        if condition_id:
            condition_ids.append(condition_id)
        if slug:
            slugs.append(slug)
        settlement_counts[settlement_code] = settlement_counts.get(settlement_code, 0) + 1
        if settlement_code == 0:
            unknown_rows += 1
        if updated_at and (max_updated_at is None or updated_at > max_updated_at):
            max_updated_at = updated_at

    mapping = _load_market_mapping(conn, condition_ids=condition_ids, slugs=slugs)

    upsert_rows: List[Tuple[int, str, str, int, Optional[str], Optional[str]]] = []
    matched_rows = 0
    for item in normalized:
        market_id = None
        if item["condition_id"] and item["condition_id"] in mapping:
            market_id = mapping[item["condition_id"]]
        elif item["slug"] and item["slug"] in mapping:
            market_id = mapping[item["slug"]]

        if market_id is None:
            continue

        matched_rows += 1
        upsert_rows.append(
            (
                int(market_id),
                item["condition_id"],
                item["slug"],
                int(item["settlement_code"]),
                item["closed_time"],
                item["updated_at_str"],
            )
        )

    written_rows = _upsert_rows(conn, upsert_rows)
    return (len(page), matched_rows, written_rows, settlement_counts, max_updated_at, unknown_rows)


def sync_market_resolution_fast(
    *,
    mode: str = "full",
    page_size: int = DEFAULT_PAGE_SIZE,
    min_page_size: int = DEFAULT_MIN_PAGE_SIZE,
    max_pages: Optional[int] = None,
    requests_delay: float = DEFAULT_REQUESTS_DELAY,
    bulk_bad_windows_limit: int = DEFAULT_BULK_BAD_WINDOWS_LIMIT,
    skip_bulk: bool = False,
    targeted_max_markets: Optional[int] = None,
    targeted_batch_size: int = DEFAULT_TARGETED_BATCH_SIZE,
    targeted_requests_delay: float = DEFAULT_TARGETED_REQUESTS_DELAY,
    skip_targeted_fill: bool = False,
    skip_unknown_review: bool = False,
    unknown_review_max_markets: Optional[int] = DEFAULT_UNKNOWN_REVIEW_MAX_MARKETS,
    unknown_review_max_addresses: int = DEFAULT_UNKNOWN_REVIEW_MAX_ADDRESSES,
    unknown_review_requests_delay: float = DEFAULT_UNKNOWN_REVIEW_REQUESTS_DELAY,
    db_path: Optional[str] = None,
    output_json: Optional[str] = None,
    repair_list_json: Optional[str] = None,
) -> Dict[str, Any]:
    init_schema(db_path=db_path or "")
    conn = get_connection(db_path=db_path or None)
    summary: Dict[str, Any] = {
        "mode": mode,
        "db_target": describe_db_target(),
        "table": "market_resolution_fast",
        "page_size": page_size,
        "min_page_size": min_page_size,
        "max_pages": max_pages,
        "requests_delay": requests_delay,
        "bulk_bad_windows_limit": bulk_bad_windows_limit,
        "skip_bulk": skip_bulk,
        "targeted_max_markets": targeted_max_markets,
        "targeted_batch_size": targeted_batch_size,
        "targeted_requests_delay": targeted_requests_delay,
        "skip_targeted_fill": skip_targeted_fill,
        "skip_unknown_review": skip_unknown_review,
        "unknown_review_max_markets": unknown_review_max_markets,
        "unknown_review_max_addresses": unknown_review_max_addresses,
        "unknown_review_requests_delay": unknown_review_requests_delay,
        "pages_fetched": 0,
        "gamma_rows": 0,
        "matched_rows": 0,
        "written_rows": 0,
        "unknown_rows": 0,
        "settlement_counts": {},
        "watermark_before": None,
        "watermark_after": None,
        "stop_reason": None,
        "samples_unmatched": [],
    }

    try:
        _ensure_schema(conn)
        watermark = _get_sync_state(conn, SYNC_STATE_KEY_LAST_GAMMA_UPDATED_AT) if mode == "incremental" else None
        summary["watermark_before"] = watermark.isoformat() if watermark else None
        if watermark is not None:
            # 为避免 updatedAt 同秒重复造成漏抓，给 watermark 留 1 秒重叠
            watermark = watermark - timedelta(seconds=1)

        effective_max_pages = max_pages
        if not skip_bulk and effective_max_pages is None and mode == "full":
            effective_max_pages = DEFAULT_FULL_MODE_BULK_MAX_PAGES
        summary["effective_max_pages"] = effective_max_pages

        offset = 0
        pages_fetched = 0
        current_page_size = max(1, int(page_size))
        settlement_counts: Dict[int, int] = {}
        max_seen_updated_at: Optional[datetime] = None
        unmatched_samples: List[Dict[str, Any]] = []
        recovered_windows_count = 0

        while not skip_bulk:
            if effective_max_pages is not None and pages_fetched >= effective_max_pages:
                summary["stop_reason"] = "max_pages"
                break

            try:
                events, effective_page_size = _fetch_closed_events_page_adaptive(
                    offset=offset,
                    limit=current_page_size,
                    min_limit=min_page_size,
                )
            except Exception as e:
                if _is_gamma_server_http_error(e):
                    delay = DEFAULT_GAMMA_500_PAUSE_SECONDS
                    print(
                        f"[gamma-events] offset={offset} limit={current_page_size} still sees transient HTTP "
                        f"{_exception_http_status(e)} after in-place retries; sleep {delay:.1f}s and retry same offset",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue
                print(
                    f"[gamma-events] offset={offset} still failing at min_page_size={min_page_size}; "
                    f"recovering one-by-one: {e}",
                    file=sys.stderr,
                )
                seen, written, skipped = _recover_bad_offset_window(
                    conn,
                    offset=offset,
                    window_size=min_page_size,
                )
                summary["gamma_rows"] += seen
                summary["written_rows"] += written
                summary["unknown_rows"] += 0
                recovered_windows_count += 1
                summary.setdefault("recovered_windows", []).append(
                    {
                        "offset": offset,
                        "window_size": min_page_size,
                        "rows_seen": seen,
                        "rows_written": written,
                        "rows_skipped": skipped,
                    }
                )
                pages_fetched += 1
                offset += min_page_size
                if bulk_bad_windows_limit > 0 and recovered_windows_count >= bulk_bad_windows_limit:
                    summary["stop_reason"] = "switched_to_targeted_fill_after_bad_windows"
                    break
                if requests_delay > 0:
                    time.sleep(requests_delay)
                continue
            pages_fetched += 1
            if not events:
                summary["stop_reason"] = "empty_page"
                break
            page = _flatten_markets_from_events(events)
            if not page:
                summary["stop_reason"] = "empty_markets_page"
                break
            current_page_size = effective_page_size

            gamma_rows, matched_rows, written_rows, page_counts, page_max_updated_at, unknown_rows = _sync_page(conn, page)
            summary["gamma_rows"] += gamma_rows
            summary["matched_rows"] += matched_rows
            summary["written_rows"] += written_rows
            summary["unknown_rows"] += unknown_rows

            for code, count in page_counts.items():
                settlement_counts[code] = settlement_counts.get(code, 0) + count

            if page_max_updated_at and (max_seen_updated_at is None or page_max_updated_at > max_seen_updated_at):
                max_seen_updated_at = page_max_updated_at

            # 记录少量未匹配样本，便于人工核对
            if len(unmatched_samples) < 10:
                condition_ids = [_ensure_0x(item.get("conditionId") or item.get("condition_id")) for item in page]
                slugs = [str(item.get("slug") or item.get("market_slug") or "").strip().lower() for item in page]
                mapping = _load_market_mapping(conn, condition_ids=condition_ids, slugs=slugs)
                for item in page:
                    condition_id = _ensure_0x(item.get("conditionId") or item.get("condition_id"))
                    slug = str(item.get("slug") or item.get("market_slug") or "").strip().lower()
                    if (condition_id and condition_id in mapping) or (slug and slug in mapping):
                        continue
                    unmatched_samples.append(
                        {
                            "condition_id": condition_id,
                            "slug": slug,
                            "updated_at": item.get("updatedAt") or item.get("updated_at"),
                            "outcome_prices": item.get("outcomePrices") or item.get("outcome_prices"),
                        }
                    )
                    if len(unmatched_samples) >= 10:
                        break

            print(
                f"[page {pages_fetched}] gamma={gamma_rows} matched={matched_rows} written={written_rows} "
                f"unknown={unknown_rows} offset={offset} effective_event_page_size={effective_page_size}",
                file=sys.stderr,
            )

            if watermark is not None:
                if page_max_updated_at is None:
                    summary["stop_reason"] = "no_updated_at"
                    break
                if page_max_updated_at <= watermark:
                    summary["stop_reason"] = "incremental_watermark_reached"
                    break

            if len(events) < effective_page_size:
                summary["stop_reason"] = "last_page"
                break

            offset += len(events)
            if requests_delay > 0:
                time.sleep(requests_delay)

        if not skip_targeted_fill:
            targeted_summary = _targeted_fill_missing_markets(
                conn,
                max_markets=targeted_max_markets,
                batch_size=targeted_batch_size,
                requests_delay=targeted_requests_delay,
            )
            summary["targeted_fill"] = targeted_summary
            summary["written_rows"] += int(targeted_summary.get("written", 0))

        if not skip_unknown_review:
            unknown_review_summary = _review_unknown_markets_via_activity(
                conn,
                max_markets=unknown_review_max_markets,
                max_addresses_per_market=unknown_review_max_addresses,
                requests_delay=unknown_review_requests_delay,
                repair_list_json=repair_list_json,
            )
            summary["unknown_activity_review"] = unknown_review_summary
            summary["written_rows"] += int(unknown_review_summary.get("resolved_markets", 0))

        if max_seen_updated_at is not None:
            _set_sync_state(conn, SYNC_STATE_KEY_LAST_GAMMA_UPDATED_AT, max_seen_updated_at)
            summary["watermark_after"] = max_seen_updated_at.isoformat()

        summary["pages_fetched"] = pages_fetched
        summary["settlement_counts"] = {str(k): v for k, v in sorted(settlement_counts.items())}
        summary["samples_unmatched"] = unmatched_samples
        conn.commit()
    finally:
        conn.close()

    if output_json:
        out_path = Path(output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bulk sync market_resolution_fast from Gamma closed events")
    parser.add_argument(
        "--mode",
        choices=["full", "incremental"],
        default="full",
        help="full: 从 Gamma 全量扫描 closed events；incremental: 基于 sync_state 水位增量扫描",
    )
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Gamma events API 单页大小，默认 100")
    parser.add_argument(
        "--min-page-size",
        type=int,
        default=DEFAULT_MIN_PAGE_SIZE,
        help="当大页请求失败时允许自动降到的最小页大小，默认 100",
    )
    parser.add_argument("--max-pages", type=int, default=None, help="仅处理前 N 页，适合 smoke test")
    parser.add_argument(
        "--requests-delay",
        type=float,
        default=DEFAULT_REQUESTS_DELAY,
        help="每页请求之间的等待秒数，默认 0.1",
    )
    parser.add_argument(
        "--bulk-bad-windows-limit",
        type=int,
        default=DEFAULT_BULK_BAD_WINDOWS_LIMIT,
        help="bulk 模式下遇到多少个坏分页窗口后切换到 targeted fill，默认 3",
    )
    parser.add_argument(
        "--skip-bulk",
        action="store_true",
        help="跳过 closed events bulk，只做基于本地 markets 缺失的 targeted fill",
    )
    parser.add_argument(
        "--targeted-max-markets",
        type=int,
        default=None,
        help="targeted fill 最多处理多少个本地缺失的已结束市场；默认不设上限",
    )
    parser.add_argument(
        "--targeted-batch-size",
        type=int,
        default=DEFAULT_TARGETED_BATCH_SIZE,
        help="targeted fill 每批扫描多少个本地缺失市场，默认 200",
    )
    parser.add_argument(
        "--targeted-requests-delay",
        type=float,
        default=DEFAULT_TARGETED_REQUESTS_DELAY,
        help="targeted fill 每个 slug 请求之间的等待秒数，默认 0.05",
    )
    parser.add_argument(
        "--skip-targeted-fill",
        action="store_true",
        help="只做 closed events bulk，不做基于本地 markets 缺失的定点补齐",
    )
    parser.add_argument(
        "--skip-unknown-review",
        action="store_true",
        help="跳过对 settlement_code=0 老市场的 activity redeem 复核",
    )
    parser.add_argument(
        "--unknown-review-max-markets",
        type=int,
        default=None,
        help="最多复核多少个 settlement_code=0 的市场；默认不设上限",
    )
    parser.add_argument(
        "--unknown-review-max-addresses",
        type=int,
        default=DEFAULT_UNKNOWN_REVIEW_MAX_ADDRESSES,
        help="每个 unknown 市场最多抽样多少个地址去查 REDEEM，默认 25",
    )
    parser.add_argument(
        "--unknown-review-requests-delay",
        type=float,
        default=DEFAULT_UNKNOWN_REVIEW_REQUESTS_DELAY,
        help="unknown activity 复核时，每个地址请求之间的等待秒数，默认 0.05",
    )
    parser.add_argument("--output-json", default=None, help="写出同步摘要 JSON")
    parser.add_argument("--repair-list-json", default=None, help="写出 unknown/异常 market 的待修正清单 JSON")
    add_db_cli_args(parser)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    configure_db_from_args(args)

    summary = sync_market_resolution_fast(
        mode=args.mode,
        page_size=max(1, int(args.page_size)),
        min_page_size=max(1, int(args.min_page_size)),
        max_pages=args.max_pages if args.max_pages and args.max_pages > 0 else None,
        requests_delay=max(0.0, float(args.requests_delay)),
        bulk_bad_windows_limit=max(0, int(args.bulk_bad_windows_limit)),
        skip_bulk=bool(args.skip_bulk),
        targeted_max_markets=args.targeted_max_markets if args.targeted_max_markets and args.targeted_max_markets > 0 else None,
        targeted_batch_size=max(1, int(args.targeted_batch_size)),
        targeted_requests_delay=max(0.0, float(args.targeted_requests_delay)),
        skip_targeted_fill=bool(args.skip_targeted_fill),
        skip_unknown_review=bool(args.skip_unknown_review),
        unknown_review_max_markets=(
            args.unknown_review_max_markets
            if args.unknown_review_max_markets and args.unknown_review_max_markets > 0
            else None
        ),
        unknown_review_max_addresses=max(1, int(args.unknown_review_max_addresses)),
        unknown_review_requests_delay=max(0.0, float(args.unknown_review_requests_delay)),
        db_path=None,
        output_json=args.output_json,
        repair_list_json=args.repair_list_json,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
