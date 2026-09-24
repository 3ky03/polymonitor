#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回填历史 markets 表中的 canonical 字段。

用途：
1. 修复旧版本 market_discovery 写入后遗留的 gamma_market_id 缺失
2. 修复 question_id / oracle / created_at / end_date 被半结构 payload 覆盖为空的问题
3. 不重跑全量 market discovery，只对已有缺失记录做定向补齐
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import requests
except ImportError:
    print("Error: requests not installed. pip install requests", file=sys.stderr)
    sys.exit(1)

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import add_db_cli_args, configure_db_from_args, describe_db_target, get_connection, init_schema
from market.market_discovery import (
    CLOB_API_BASE,
    GAMMA_MARKETS_URL,
    REQUEST_TIMEOUT,
    batch_upsert_markets,
    normalize_market_from_gamma,
)


DEFAULT_BATCH_SIZE = 200
DEFAULT_WORKERS = 8
DEFAULT_SYNC_STATE_KEY = "market_canonical_backfill"


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_blank(value: Any) -> bool:
    return _text(value) == ""


def _response_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    finally:
        resp.close()


def _fetch_gamma_market_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    slug = _text(slug)
    if not slug:
        return None
    url = f"{GAMMA_MARKETS_URL}/slug/{slug}"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = _response_json(resp)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _fetch_clob_market_by_condition_id(condition_id: str) -> Optional[Dict[str, Any]]:
    cid = _text(condition_id)
    if not cid:
        return None
    url = f"{CLOB_API_BASE}/markets/{cid}"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = _response_json(resp)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _merge_prefer_non_empty(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        merged[key] = value
    return merged


def _canonicalize_market_payload(raw: Dict[str, Any], fallback_condition_id: str) -> Dict[str, Any]:
    data = dict(raw)
    data["id"] = data.get("id") or data.get("market_id")
    data["conditionId"] = data.get("conditionId") or data.get("condition_id") or fallback_condition_id
    data["questionId"] = data.get("questionId") or data.get("question_id")
    data["questionID"] = data.get("questionID") or data.get("question_id") or data.get("questionId")
    data["slug"] = data.get("slug") or data.get("market_slug")
    data["createdAt"] = data.get("createdAt") or data.get("created_at")
    data["endDate"] = data.get("endDate") or data.get("end_date") or data.get("end_date_iso")
    data["negRisk"] = data.get("negRisk") if data.get("negRisk") is not None else data.get("neg_risk")
    return data


def _load_resume_id(conn, key: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT last_block FROM sync_state WHERE key = ?", (key,))
    row = cur.fetchone()
    if not row or row[0] is None:
        return 0
    return int(row[0])


def _save_resume_id(conn, key: str, last_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO sync_state (key, value, last_block, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        (key, str(last_id), int(last_id)),
    )
    conn.commit()


def _select_candidates(
    conn,
    start_id: int,
    batch_size: int,
    max_id: Optional[int] = None,
) -> List[Tuple[Any, ...]]:
    cur = conn.cursor()
    max_filter = ""
    params: List[Any] = [int(start_id)]
    if max_id is not None:
        max_filter = "AND id <= ?"
        params.append(int(max_id))
    params.append(int(batch_size))
    cur.execute(
        f"""
        SELECT id, slug, condition_id, title, gamma_market_id, question_id, oracle, created_at, end_date
        FROM markets
        WHERE id > ?
          {max_filter}
          AND (
                gamma_market_id IS NULL OR TRIM(gamma_market_id) = ''
             OR question_id IS NULL OR TRIM(question_id) = ''
             OR oracle IS NULL OR TRIM(oracle) = ''
             OR created_at IS NULL
             OR end_date IS NULL
          )
        ORDER BY id ASC
        LIMIT ?
        """,
        tuple(params),
    )
    return [tuple(row) for row in cur.fetchall()]


def _resolve_candidate(row: Sequence[Any]) -> Dict[str, Any]:
    market_id, slug, condition_id, title, gamma_market_id, question_id, oracle, created_at, end_date = row
    slug = _text(slug)
    condition_id = _text(condition_id)

    raw: Optional[Dict[str, Any]] = None
    source = ""

    if slug:
        raw = _fetch_gamma_market_by_slug(slug)
        if raw:
            source = "gamma_slug"

    if raw is None and condition_id:
        clob = _fetch_clob_market_by_condition_id(condition_id)
        if clob:
            source = "clob_condition"
            raw = _canonicalize_market_payload(clob, condition_id)
            slug_from_clob = _text(raw.get("market_slug") or raw.get("slug"))
            if slug_from_clob:
                gamma_market = _fetch_gamma_market_by_slug(slug_from_clob)
                if gamma_market:
                    raw = _merge_prefer_non_empty(raw, gamma_market)
                    source = "clob+gamma_slug"

    if raw is None:
        return {
            "market_row_id": int(market_id),
            "condition_id": condition_id,
            "slug": slug,
            "title": _text(title),
            "status": "unresolved",
            "reason": "fetch_failed",
            "source": source,
        }

    raw = _canonicalize_market_payload(raw, condition_id)
    norm = normalize_market_from_gamma(raw)
    if not norm:
        return {
            "market_row_id": int(market_id),
            "condition_id": condition_id,
            "slug": slug,
            "title": _text(title),
            "status": "unresolved",
            "reason": "normalize_failed",
            "source": source,
        }

    changed_fields: List[str] = []
    if _is_blank(gamma_market_id) and not _is_blank(norm.get("gamma_market_id")):
        changed_fields.append("gamma_market_id")
    if _is_blank(question_id) and not _is_blank(norm.get("question_id")):
        changed_fields.append("question_id")
    if _is_blank(oracle) and not _is_blank(norm.get("oracle")):
        changed_fields.append("oracle")
    if _is_blank(created_at) and not _is_blank(norm.get("created_at")):
        changed_fields.append("created_at")
    if _is_blank(end_date) and not _is_blank(norm.get("end_date")):
        changed_fields.append("end_date")

    if not changed_fields:
        return {
            "market_row_id": int(market_id),
            "condition_id": condition_id,
            "slug": slug,
            "title": _text(title),
            "status": "noop",
            "reason": "no_improvement",
            "source": source,
        }

    return {
        "market_row_id": int(market_id),
        "condition_id": condition_id,
        "slug": slug,
        "title": _text(title),
        "status": "resolved",
        "source": source,
        "changed_fields": changed_fields,
        "market": norm,
    }


def run_backfill(
    db_path: Optional[str],
    *,
    batch_size: int,
    workers: int,
    limit: Optional[int],
    start_id: int,
    max_id: Optional[int],
    resume: bool,
    sync_state_key: str,
    dry_run: bool,
) -> Dict[str, int]:
    init_schema(db_path=db_path)
    conn = get_connection(db_path)
    try:
        effective_start_id = int(start_id)
        if resume:
            effective_start_id = max(effective_start_id, _load_resume_id(conn, sync_state_key))

        stats = {
            "scanned": 0,
            "resolved": 0,
            "updated": 0,
            "noop": 0,
            "unresolved": 0,
        }
        unresolved_samples: List[str] = []
        last_seen_id = effective_start_id

        while True:
            remaining = None if limit is None else max(0, int(limit) - stats["scanned"])
            if remaining == 0:
                break
            fetch_size = batch_size if remaining is None else min(batch_size, remaining)
            rows = _select_candidates(conn, last_seen_id, fetch_size, max_id=max_id)
            if not rows:
                break

            norms: List[Dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
                future_to_row = {executor.submit(_resolve_candidate, row): row for row in rows}
                for future in as_completed(future_to_row):
                    result = future.result()
                    stats["scanned"] += 1
                    status = result["status"]
                    if status == "resolved":
                        stats["resolved"] += 1
                        norms.append(result["market"])
                    elif status == "noop":
                        stats["noop"] += 1
                    else:
                        stats["unresolved"] += 1
                        if len(unresolved_samples) < 10:
                            unresolved_samples.append(
                                f"id={result['market_row_id']} slug={result['slug']} reason={result['reason']}"
                            )

            if norms and not dry_run:
                stats["updated"] += batch_upsert_markets(conn, norms)

            last_seen_id = max(int(row[0]) for row in rows)
            if resume and not dry_run:
                _save_resume_id(conn, sync_state_key, last_seen_id)

            print(
                f"[backfill] scanned={stats['scanned']} resolved={stats['resolved']} "
                f"updated={stats['updated']} noop={stats['noop']} unresolved={stats['unresolved']} "
                f"last_id={last_seen_id}",
                file=sys.stderr,
            )

            if len(rows) < fetch_size:
                break

        if unresolved_samples:
            print("[backfill] unresolved samples:", file=sys.stderr)
            for sample in unresolved_samples:
                print(f"  - {sample}", file=sys.stderr)

        return stats
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill existing markets with missing gamma_market_id/question_id/oracle without rerunning full discovery"
    )
    add_db_cli_args(parser)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"每轮扫描多少条候选 market（default: {DEFAULT_BATCH_SIZE}）")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"并发请求数（default: {DEFAULT_WORKERS}）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少条候选记录")
    parser.add_argument("--start-id", type=int, default=0, help="只处理 markets.id 大于该值的记录")
    parser.add_argument("--max-id", type=int, default=None, help="只处理 markets.id 小于等于该值的记录")
    parser.add_argument("--resume", action="store_true", help="从 sync_state 中记录的上次进度继续")
    parser.add_argument("--sync-state-key", default=DEFAULT_SYNC_STATE_KEY, help=f"resume 进度键名（default: {DEFAULT_SYNC_STATE_KEY}）")
    parser.add_argument("--dry-run", action="store_true", help="只扫描和请求，不写回数据库")
    args = parser.parse_args()

    configure_db_from_args(args)
    db_path = args.sqlite_path
    print(f"Database target: {describe_db_target()}", file=sys.stderr)
    stats = run_backfill(
        db_path,
        batch_size=args.batch_size,
        workers=args.workers,
        limit=args.limit,
        start_id=args.start_id,
        max_id=args.max_id,
        resume=args.resume,
        sync_state_key=args.sync_state_key,
        dry_run=args.dry_run,
    )
    print(stats)


if __name__ == "__main__":
    main()
