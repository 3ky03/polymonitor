#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build PostgreSQL serving rows for single-market workspace panels.

This job is intentionally local-DB only: it does not call Gamma, CLOB, or RPC.
It materializes the data that the panel asks for immediately after a market
click so the API can answer from indexed PostgreSQL rows first.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import (  # type: ignore
    add_db_cli_args,
    configure_db_from_args,
    describe_db_target,
    get_connection,
    get_table_columns,
    init_schema,
    is_postgres_backend,
    table_exists,
)
from db.trade_v2 import get_trade_read_source  # type: ignore


RANGE_SPECS: Dict[str, Tuple[int, str, int]] = {
    "1h": (3600, "1m", 260),
    "6h": (6 * 3600, "3m", 360),
    "1d": (24 * 3600, "5m", 420),
    "1w": (7 * 86400, "1h", 420),
    "7d": (7 * 86400, "1h", 420),
    "1m": (30 * 86400, "4h", 420),
    "30d": (30 * 86400, "4h", 420),
    "all": (30 * 86400, "4h", 420),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "as_dict"):
        return row.as_dict()
    if isinstance(row, dict):
        return row
    return dict(row)


def _json_for_db(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            return [item.strip() for item in text.split(",") if item.strip()]
    return [value]


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _decimal_text(value: Any) -> Optional[str]:
    numeric = _to_decimal(value)
    if numeric is None:
        return None
    text = format(numeric, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _float(value: Any) -> float:
    numeric = _to_decimal(value)
    if numeric is None:
        return 0.0
    try:
        result = float(numeric)
    except (ValueError, OverflowError):
        return 0.0
    return 0.0 if math.isnan(result) else result


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _iso(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _change(current: Any, past: Any) -> Optional[str]:
    current_decimal = _to_decimal(current)
    past_decimal = _to_decimal(past)
    if current_decimal is None or past_decimal is None:
        return None
    return _decimal_text(current_decimal - past_decimal)


def _safe_identifier(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return text


def _history_status(range_name: str, interval: str, points: Sequence[Dict[str, Any]]) -> str:
    if not points:
        return "missing"
    if len(points) <= 2:
        return "snapshot"
    distinct = {
        str(point.get("yesPrice"))
        for point in points
        if point.get("yesPrice") not in (None, "")
    }
    if len(distinct) <= 1:
        return "flat"
    return "ok"


def ensure_schema(conn) -> None:
    init_schema(conn=conn)


def _load_candidate_markets(conn, *, max_markets: int, market_ids: Sequence[int], active_only: bool) -> List[Dict[str, Any]]:
    params: List[Any] = []
    if market_ids:
        placeholders = ", ".join("?" for _ in market_ids)
        candidate_pool_sql = f"SELECT id AS market_id FROM core.markets WHERE id IN ({placeholders})"
        event_pool_sql = ""
        params.extend(int(market_id) for market_id in market_ids)
    else:
        # core.markets is multi-million-row history.  Sorting full market rows
        # (including description/tags JSON) can spill more than PostgreSQL's
        # temp_file_limit before LIMIT is applied.  Build a bounded ID pool
        # from the ranking indexes first, then load the wide payload only for
        # those candidates.  The event defaults are added below so a group
        # leader cannot be displaced by the activity-only pool.
        candidate_pool_sql = """
          SELECT market_id FROM (
            SELECT market_id
            FROM core.market_list_serving
            ORDER BY
              volume_24h DESC,
              trade_count_24h DESC,
              last_trade_at DESC NULLS LAST,
              market_id DESC
            LIMIT ?
          ) activity_candidates
          UNION
          SELECT market_id FROM (
            SELECT market_id
            FROM core.market_latest_prices
            ORDER BY latest_trade_at DESC NULLS LAST, market_id DESC
            LIMIT ?
          ) price_candidates
          UNION
          SELECT market_id FROM (
            SELECT id AS market_id
            FROM core.markets
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT ?
          ) recent_candidates
        """
        event_pool_sql = "UNION SELECT market_id FROM event_candidates"
        params.extend((int(max_markets), int(max_markets), int(max_markets)))
    active_filter_sql = (
        """
        WHERE (
          COALESCE(mss.is_final, FALSE) = FALSE
          OR COALESCE(mls.volume_24h, 0) > 0
          OR ec.market_id IS NOT NULL
          OR m.created_at >= now() - interval '45 days'
        )
        """
        if active_only and not market_ids
        else ""
    )
    params.append(int(max_markets))
    rows = conn.execute(
        f"""
        WITH event_candidates AS (
          SELECT
            default_market_id AS market_id,
            MAX(volume_24h) AS group_volume_24h,
            MAX(trade_count_24h) AS group_trade_count_24h,
            MAX(last_activity_at) AS group_last_activity_at,
            MAX(active_rank) AS group_active_rank
          FROM core.event_market_serving
          WHERE default_market_id IS NOT NULL
          GROUP BY default_market_id
        ),
        candidate_pool AS MATERIALIZED (
          {candidate_pool_sql}
          {event_pool_sql}
        ),
        ranked_candidates AS MATERIALIZED (
          SELECT
            m.id,
            COALESCE(ec.group_active_rank, 0) AS group_active_rank,
            GREATEST(COALESCE(mls.volume_24h, 0), COALESCE(ec.group_volume_24h, 0)) AS rank_volume_24h,
            GREATEST(COALESCE(mls.trade_count_24h, 0), COALESCE(ec.group_trade_count_24h, 0)) AS rank_trade_count_24h,
            COALESCE(
              mls.last_trade_at,
              mls.latest_trade_at,
              mlp.latest_trade_at,
              ec.group_last_activity_at,
              m.created_at
            ) AS rank_activity_at
          FROM candidate_pool cp
          JOIN core.markets m ON m.id = cp.market_id
          LEFT JOIN core.market_status_snapshot mss ON mss.market_id = m.id
          LEFT JOIN core.market_latest_prices mlp ON mlp.market_id = m.id
          LEFT JOIN core.market_list_serving mls ON mls.market_id = m.id
          LEFT JOIN event_candidates ec ON ec.market_id = m.id
          {active_filter_sql}
          ORDER BY
            group_active_rank DESC,
            rank_volume_24h DESC,
            rank_trade_count_24h DESC,
            rank_activity_at DESC NULLS LAST,
            m.id DESC
          LIMIT ?
        )
        SELECT
          m.id,
          m.gamma_market_id,
          m.slug,
          m.title,
          m.description,
          m.condition_id,
          m.question_id,
          m.oracle,
          m.yes_token_id,
          m.no_token_id,
          m.category,
          m.tags,
          m.clob_token_ids,
          m.enable_neg_risk,
          m.end_date,
          m.created_at,
          COALESCE(mss.settlement_code, 0) AS settlement_code,
          COALESCE(mss.settlement_outcome, 'UNKNOWN') AS settlement_outcome,
          mss.settlement_source,
          mss.settlement_raw,
          mss.settlement_event_id,
          mss.settlement_event_time,
          mss.settlement_transaction,
          COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
          COALESCE(mss.is_resolved, FALSE) AS is_resolved,
          COALESCE(mss.is_final, FALSE) AS is_final,
          COALESCE(mss.completion_status, 'OPEN') AS completion_status,
          mss.completion_source,
          mss.completion_time,
          COALESCE(mss.gamma_closed, FALSE) AS gamma_closed,
          mss.gamma_closed_time,
          COALESCE(mlp.latest_yes_price, mls.latest_price) AS latest_yes_price,
          mlp.latest_no_price,
          COALESCE(mlp.latest_price, mls.latest_price) AS latest_price,
          COALESCE(mlp.latest_trade_at, mls.latest_trade_at, mls.last_trade_at, ec.group_last_activity_at) AS latest_trade_at,
          mls.price_24h_ago,
          GREATEST(COALESCE(mls.volume_24h, 0), COALESCE(ec.group_volume_24h, 0)) AS volume_24h,
          GREATEST(COALESCE(mls.trade_count_24h, 0), COALESCE(ec.group_trade_count_24h, 0)) AS trade_count_24h,
          COALESCE(mls.last_trade_at, mls.latest_trade_at, mlp.latest_trade_at, ec.group_last_activity_at, m.created_at) AS last_activity_at,
          COALESCE(ec.group_active_rank, 0) AS group_active_rank
        FROM ranked_candidates rc
        JOIN core.markets m ON m.id = rc.id
        LEFT JOIN core.market_status_snapshot mss ON mss.market_id = m.id
        LEFT JOIN core.market_latest_prices mlp ON mlp.market_id = m.id
        LEFT JOIN core.market_list_serving mls ON mls.market_id = m.id
        LEFT JOIN event_candidates ec ON ec.market_id = m.id
        ORDER BY
          rc.group_active_rank DESC,
          rc.rank_volume_24h DESC,
          rc.rank_trade_count_24h DESC,
          rc.rank_activity_at DESC NULLS LAST,
          rc.id DESC
        """,
        params,
    ).fetchall()
    return [_as_dict(row) for row in rows]


def _price_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    latest_yes = row.get("latest_yes_price") or row.get("latest_price")
    latest_no = row.get("latest_no_price")
    if latest_no in (None, "") and _to_decimal(latest_yes) is not None:
        latest_no = Decimal("1") - (_to_decimal(latest_yes) or Decimal("0"))
    return {
        "marketId": row.get("id"),
        "localMarketId": row.get("id"),
        "latestPrice": _decimal_text(latest_yes or row.get("latest_price")),
        "latestYesPrice": _decimal_text(latest_yes),
        "latestNoPrice": _decimal_text(latest_no),
        "change1h": None,
        "change24h": _change(latest_yes or row.get("latest_price"), row.get("price_24h_ago")),
        "volume24h": _decimal_text(row.get("volume_24h")) or "0",
        "tradeCount24h": _int(row.get("trade_count_24h")),
        "updatedAt": _iso(row.get("latest_trade_at") or row.get("last_activity_at")),
    }


def _market_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "localMarketId": row.get("id"),
        "slug": row.get("slug"),
        "title": row.get("title"),
        "conditionId": row.get("condition_id"),
        "questionId": row.get("question_id"),
        "oracle": row.get("oracle"),
        "yesTokenId": row.get("yes_token_id"),
        "noTokenId": row.get("no_token_id"),
        "description": row.get("description") or "",
        "status": "Active" if str(row.get("completion_status") or "OPEN") == "OPEN" else "Closed",
        "latestPrice": _decimal_text(row.get("latest_price")),
        "latestYesPrice": _decimal_text(row.get("latest_yes_price")),
        "latestNoPrice": _decimal_text(row.get("latest_no_price")),
        "change24h": _change(row.get("latest_yes_price") or row.get("latest_price"), row.get("price_24h_ago")),
        "volume24h": _decimal_text(row.get("volume_24h")) or "0",
        "tradeCount24h": _int(row.get("trade_count_24h")),
        "outcomeCount": 2,
        "enableNegRisk": bool(row.get("enable_neg_risk")),
        "endDate": _iso(row.get("end_date")),
        "createdAt": _iso(row.get("created_at")),
        "category": row.get("category") or "Uncategorized",
        "tags": _json_list(row.get("tags")),
        "gammaMarketId": row.get("gamma_market_id"),
        "settlementCode": row.get("settlement_code") or 0,
        "settlementOutcome": row.get("settlement_outcome") or "UNKNOWN",
        "settlementSource": row.get("settlement_source"),
        "settlementRaw": row.get("settlement_raw"),
        "settlementEventId": row.get("settlement_event_id"),
        "settlementEventTime": _iso(row.get("settlement_event_time")),
        "settlementTransaction": row.get("settlement_transaction"),
        "completionStatus": row.get("completion_status") or "OPEN",
        "completionSource": row.get("completion_source"),
        "completionTime": _iso(row.get("completion_time")),
        "isTradingClosed": bool(row.get("is_trading_closed")),
        "isResolved": bool(row.get("is_resolved")),
        "isFinal": bool(row.get("is_final")),
        "gammaClosed": bool(row.get("gamma_closed")),
        "gammaClosedTime": _iso(row.get("gamma_closed_time")),
    }


def _oracle_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "marketId": row.get("id"),
        "localMarketId": row.get("id"),
        "completionStatus": row.get("completion_status") or "OPEN",
        "isTradingClosed": bool(row.get("is_trading_closed")),
        "isResolved": bool(row.get("is_resolved")),
        "isFinal": bool(row.get("is_final")),
        "settlementCode": row.get("settlement_code") or 0,
        "settlementOutcome": row.get("settlement_outcome") or "UNKNOWN",
        "settlementSource": row.get("settlement_source"),
        "settlementEventId": row.get("settlement_event_id"),
        "settlementEventTime": _iso(row.get("settlement_event_time")),
        "settlementTransaction": row.get("settlement_transaction"),
    }


def _identity(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "marketId": row.get("id"),
        "localMarketId": row.get("id"),
        "gammaMarketId": row.get("gamma_market_id"),
        "conditionId": row.get("condition_id"),
        "questionId": row.get("question_id"),
        "slug": row.get("slug"),
        "yesTokenId": row.get("yes_token_id"),
        "noTokenId": row.get("no_token_id"),
    }


def _load_trade_points(conn, market_id: int, *, range_seconds: int, limit: int) -> List[Dict[str, Any]]:
    source = _safe_identifier(get_trade_read_source())
    if not table_exists(conn, source):
        return []
    columns = set(get_table_columns(conn, source))
    time_col = "block_time" if "block_time" in columns else ("timestamp" if "timestamp" in columns else "")
    if not time_col or "market_id" not in columns or "price" not in columns:
        return []
    if "outcome_code" in columns:
        yes_price_expr = "CASE WHEN outcome_code = 2 THEN 1 - price ELSE price END"
    elif "outcome" in columns:
        yes_price_expr = "CASE WHEN UPPER(COALESCE(outcome, '')) = 'NO' THEN 1 - price ELSE price END"
    else:
        yes_price_expr = "price"
    since = datetime.now(timezone.utc) - timedelta(seconds=range_seconds)
    rows = conn.execute(
        f"""
        SELECT
          {time_col} AS ts,
          {yes_price_expr} AS yes_price,
          block_number,
          log_index
        FROM {source}
        WHERE market_id = ? AND {time_col} >= ?
        ORDER BY {time_col} ASC, block_number ASC NULLS LAST, log_index ASC NULLS LAST
        LIMIT ?
        """,
        (int(market_id), since, int(limit)),
    ).fetchall()
    points: List[Dict[str, Any]] = []
    for raw in rows:
        row = _as_dict(raw)
        yes_price = _to_decimal(row.get("yes_price"))
        if yes_price is None:
            continue
        if yes_price < 0:
            yes_price = Decimal("0")
        elif yes_price > 1:
            yes_price = Decimal("1")
        points.append(
            {
                "timestamp": _iso(row.get("ts")),
                "yesPrice": _decimal_text(yes_price),
                "noPrice": _decimal_text(Decimal("1") - yes_price),
            }
        )
    return _downsample(points, limit)


def _downsample(points: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if len(points) <= limit:
        return points
    step = max(1, math.ceil(len(points) / limit))
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled[-limit:]


def _chart_payload(row: Dict[str, Any], range_name: str, interval: str, trade_points: List[Dict[str, Any]], price: Dict[str, Any]) -> Dict[str, Any]:
    points = trade_points
    latest = price.get("latestYesPrice") or price.get("latestPrice")
    if not points and latest not in (None, ""):
        end_timestamp = price.get("updatedAt") or _iso(row.get("created_at")) or _now_iso()
        start_timestamp = _iso(row.get("created_at")) or end_timestamp
        points = [
            {"timestamp": start_timestamp, "yesPrice": latest, "noPrice": price.get("latestNoPrice")},
            {"timestamp": end_timestamp, "yesPrice": latest, "noPrice": price.get("latestNoPrice")},
        ]
    status = _history_status(range_name, interval, points)
    return {
        "marketId": row.get("id"),
        "localMarketId": row.get("id"),
        "range": range_name,
        "interval": interval,
        "kind": "probability",
        "historyStatus": status,
        "points": points,
    }


def _detail_payload(row: Dict[str, Any], price: Dict[str, Any], chart: Dict[str, Any], oracle: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "market": _market_payload(row),
        "localMarketId": row.get("id"),
        "gammaMarketId": row.get("gamma_market_id"),
        "identity": _identity(row),
        "diagnostics": {
            "marketId": row.get("id"),
            "priceStatus": "ok" if price.get("latestPrice") not in (None, "") else "missing",
            "chartStatus": chart.get("historyStatus") or "missing",
            "oracleStatus": oracle.get("completionStatus") or "OPEN",
            "issues": [],
        },
        "price": price,
        "chart": chart,
        "priceSeries": chart.get("points") or [],
        "trades": [],
        "oracle": {"marketId": row.get("id"), "localMarketId": row.get("id"), "summary": oracle, "timeline": []},
        "oracleEvents": [],
        "content": None,
    }


def refresh_market_workspace_serving(conn, *, max_markets: int, market_ids: Sequence[int], active_only: bool) -> Dict[str, int]:
    rows = _load_candidate_markets(conn, max_markets=max_markets, market_ids=market_ids, active_only=active_only)
    chart_rows: List[Tuple[Any, ...]] = []
    workspace_rows: List[Tuple[Any, ...]] = []
    for row in rows:
        market_id = int(row["id"])
        price = _price_payload(row)
        oracle = _oracle_summary(row)
        chart_payloads: Dict[Tuple[str, str], Dict[str, Any]] = {}
        has_recent_activity = _int(row.get("trade_count_24h")) > 0 or _float(row.get("volume_24h")) > 0
        for range_name, (range_seconds, interval, point_limit) in RANGE_SPECS.items():
            points = (
                _load_trade_points(conn, market_id, range_seconds=range_seconds, limit=point_limit)
                if has_recent_activity
                else []
            )
            chart = _chart_payload(row, range_name, interval, points, price)
            chart_payloads[(range_name, interval)] = chart
            chart_rows.append(
                (
                    market_id,
                    range_name,
                    interval,
                    chart.get("kind") or "probability",
                    chart.get("historyStatus") or "missing",
                    len(chart.get("points") or []),
                    _json_for_db(chart.get("points") or []),
                )
            )
        default_chart = chart_payloads.get(("1d", "5m")) or next(iter(chart_payloads.values()))
        detail = _detail_payload(row, price, default_chart, oracle)
        workspace_rows.append(
            (
                market_id,
                _json_for_db(detail),
                _json_for_db(price),
                _json_for_db(oracle),
                _json_for_db({}),
            )
        )
    if chart_rows:
        conn.executemany(
            """
            INSERT INTO core.market_chart_serving AS current (
              market_id, range_name, interval_name, kind, history_status, point_count, points
            ) VALUES (?, ?, ?, ?, ?, ?, ?::jsonb)
            ON CONFLICT (market_id, range_name, interval_name) DO UPDATE SET
              kind = EXCLUDED.kind,
              history_status = EXCLUDED.history_status,
              point_count = EXCLUDED.point_count,
              points = EXCLUDED.points,
              source = 'postgres',
              updated_at = now()
            WHERE (
              current.kind,
              current.history_status,
              current.point_count,
              current.points,
              current.source
            ) IS DISTINCT FROM (
              EXCLUDED.kind,
              EXCLUDED.history_status,
              EXCLUDED.point_count,
              EXCLUDED.points,
              'postgres'
            )
            """,
            chart_rows,
        )
    if workspace_rows:
        conn.executemany(
            """
            INSERT INTO core.market_workspace_serving AS current (
              market_id, detail_payload, price_payload, oracle_summary, content_summary
            ) VALUES (?, ?::jsonb, ?::jsonb, ?::jsonb, ?::jsonb)
            ON CONFLICT (market_id) DO UPDATE SET
              detail_payload = EXCLUDED.detail_payload,
              price_payload = EXCLUDED.price_payload,
              oracle_summary = EXCLUDED.oracle_summary,
              content_summary = EXCLUDED.content_summary,
              source = 'postgres',
              updated_at = now()
            WHERE (
              current.detail_payload,
              current.price_payload,
              current.oracle_summary,
              current.content_summary,
              current.source
            ) IS DISTINCT FROM (
              EXCLUDED.detail_payload,
              EXCLUDED.price_payload,
              EXCLUDED.oracle_summary,
              EXCLUDED.content_summary,
              'postgres'
            )
            """,
            workspace_rows,
        )
    conn.commit()
    return {"markets": len(rows), "chart_rows": len(chart_rows), "workspace_rows": len(workspace_rows)}


def _parse_market_ids(values: Sequence[str]) -> List[int]:
    ids: List[int] = []
    for raw in values:
        for part in str(raw or "").split(","):
            part = part.strip()
            if not part:
                continue
            ids.append(int(part))
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh PostgreSQL single-market workspace serving tables.")
    add_db_cli_args(parser)
    parser.add_argument("--interval", type=int, default=60, help="Loop interval seconds when --watch is set")
    parser.add_argument("--watch", action="store_true", help="Refresh forever")
    parser.add_argument("--once", action="store_true", help="Refresh once and exit")
    parser.add_argument("--max-markets", type=int, default=80, help="Maximum candidate markets per refresh")
    parser.add_argument("--market-id", action="append", default=[], help="Specific local market id; can be repeated or comma-separated")
    parser.add_argument("--all", action="store_true", help="Include historical markets instead of active/recent candidates")
    args = parser.parse_args()
    configure_db_from_args(args)
    if not is_postgres_backend():
        raise SystemExit("market workspace serving is PostgreSQL-only; run with --backend postgres")
    if not args.watch:
        args.once = True
    market_ids = _parse_market_ids(args.market_id)
    print(f"[market-workspace-serving] target={describe_db_target()}")
    while True:
        started = time.time()
        conn = get_connection()
        try:
            ensure_schema(conn)
            result = refresh_market_workspace_serving(
                conn,
                max_markets=args.max_markets,
                market_ids=market_ids,
                active_only=not args.all,
            )
            elapsed = time.time() - started
            print(
                f"[market-workspace-serving] markets={result['markets']} chart_rows={result['chart_rows']} "
                f"workspace_rows={result['workspace_rows']} elapsed={elapsed:.2f}s at={_now_iso()}",
                flush=True,
            )
        finally:
            conn.close()
        if args.once:
            break
        time.sleep(max(5, int(args.interval)))


if __name__ == "__main__":
    main()
