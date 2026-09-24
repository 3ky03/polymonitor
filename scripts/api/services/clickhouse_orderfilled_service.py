from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_CONTAINER = "polydata_clickhouse_orderfilled"
DEFAULT_DATABASE = "poly_orderfilled"
DEFAULT_USER = "poly_user"
DEFAULT_TABLE = "orderfilled_fact"
DEFAULT_WHALE_VOLUME_WINDOW_MINUTES = 60
DEFAULT_ALPHA_VOLUME_WINDOW_MINUTES = 15
DEFAULT_ALPHA_MARKET_BASELINE_MINUTES = 60
DEFAULT_SIGNAL_MIN_PRICE = 0.02
DEFAULT_SIGNAL_MAX_PRICE = 0.98
DEFAULT_ALPHA_MIN_NET_STRENGTH = 0.55
DEFAULT_ALPHA_EDGE_FEE_PROBABILITY = 0.01
_HTTP_QUERY_SLOTS = threading.BoundedSemaphore(
    max(1, min(int(os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_HTTP_CONCURRENCY", "1")), 4))
)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def clickhouse_orderfilled_enabled() -> bool:
    return _env_flag("POLYDATA_ORDERFILLED_CLICKHOUSE_READ_ENABLED", True)


def clickhouse_read_mode() -> str:
    if not clickhouse_orderfilled_enabled():
        return "disabled"
    if os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_HTTP_URL", "").strip():
        return "http-tunnel"
    if shutil.which("docker") is not None:
        return "docker-exec"
    return "unavailable"


def _identifier(value: str, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if not all(ch.isalnum() or ch == "_" for ch in text):
        raise ValueError(f"Unsafe ClickHouse identifier: {value!r}")
    return text


def _settings() -> Dict[str, str]:
    return {
        "http_url": os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_HTTP_URL", "").strip(),
        "container": os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_CONTAINER", DEFAULT_CONTAINER),
        "database": _identifier(os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_DATABASE", DEFAULT_DATABASE), DEFAULT_DATABASE),
        "user": os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_USER", DEFAULT_USER),
        "password": (
            os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_PASSWORD")
            or os.environ.get("CLICKHOUSE_PASSWORD")
            or ""
        ),
        "table": _identifier(os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_READ_TABLE", DEFAULT_TABLE), DEFAULT_TABLE),
    }


def _clickhouse_cmd(query: str) -> List[str]:
    settings = _settings()
    return [
        "docker",
        "exec",
        settings["container"],
        "clickhouse-client",
        "--user",
        settings["user"],
        "--password",
        settings["password"],
        "--database",
        settings["database"],
        "--query",
        query,
    ]


def _query_json_rows_http(ctx: dict, query: str, *, timeout_seconds: float) -> Optional[List[Dict[str, Any]]]:
    settings = _settings()
    base_url = settings["http_url"]
    if not base_url:
        return None
    params = urlencode(
        {
            "database": settings["database"],
            "user": settings["user"],
            "password": settings["password"],
            "query_id": f"polydata-api-{uuid.uuid4().hex}",
            "max_threads": str(_int_env("POLYDATA_ORDERFILLED_CLICKHOUSE_MAX_THREADS", 2, maximum=8)),
            "max_execution_time": str(
                min(
                    _int_env("POLYDATA_ORDERFILLED_CLICKHOUSE_MAX_EXECUTION_SECONDS", 6, maximum=30),
                    int(max(1.0, timeout_seconds)) + 1,
                )
            ),
            "max_memory_usage": str(
                _int_env(
                    "POLYDATA_ORDERFILLED_CLICKHOUSE_MAX_MEMORY_BYTES",
                    512 * 1024 * 1024,
                    minimum=64 * 1024 * 1024,
                    maximum=4 * 1024 * 1024 * 1024,
                )
            ),
            "max_bytes_to_read": str(
                _int_env(
                    "POLYDATA_ORDERFILLED_CLICKHOUSE_MAX_BYTES_TO_READ",
                    512 * 1024 * 1024,
                    minimum=64 * 1024 * 1024,
                    maximum=8 * 1024 * 1024 * 1024,
                )
            ),
            "cancel_http_readonly_queries_on_client_close": "1",
        }
    )
    separator = "&" if "?" in base_url else "?"
    request = Request(
        f"{base_url}{separator}{params}",
        data=query.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    if not _HTTP_QUERY_SLOTS.acquire(blocking=False):
        logger = ctx.get("app").logger if ctx.get("app") is not None else None
        if logger is not None:
            logger.warning("ClickHouse HTTP OrderFilled read deferred: capacity full")
        return None
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            output = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger = ctx.get("app").logger if ctx.get("app") is not None else None
        if logger is not None:
            logger.warning("ClickHouse HTTP OrderFilled read failed: %s", exc)
        return None
    finally:
        _HTTP_QUERY_SLOTS.release()
    rows: List[Dict[str, Any]] = []
    for line in output.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _query_json_rows(ctx: dict, query: str, *, timeout_seconds: float = 1.8) -> Optional[List[Dict[str, Any]]]:
    if not clickhouse_orderfilled_enabled():
        return None
    if ctx.get("app") is None:
        return None
    http_rows = _query_json_rows_http(ctx, query, timeout_seconds=timeout_seconds)
    if http_rows is not None:
        return http_rows
    if shutil.which("docker") is None:
        return None
    try:
        completed = subprocess.run(
            _clickhouse_cmd(query),
            check=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        logger = ctx.get("app").logger if ctx.get("app") is not None else None
        if logger is not None:
            logger.warning("ClickHouse OrderFilled read failed: %s", exc)
        return None
    rows: List[Dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _orderfilled_projection_sql() -> str:
    return """
        lower(f.tx_hash) AS tx_hash,
        f.log_index AS log_index,
        f.market_id AS market_id,
        concat('0x', lower(f.maker)) AS maker,
        concat('0x', lower(f.taker)) AS taker,
        toString(f.price) AS price,
        toString(f.size) AS size,
        multiIf(f.side_code = 1, 'BUY', f.side_code = 2, 'SELL', 'UNKNOWN') AS side,
        multiIf(f.outcome_code = 1, 'YES', f.outcome_code = 2, 'NO', 'UNKNOWN') AS outcome,
        lower(f.token_id) AS token_id,
        if(
            ifNull(bt.block_time <= toDateTime('2000-01-01 00:00:00', 'UTC'), 1),
            CAST(NULL, 'Nullable(String)'),
            formatDateTime(bt.block_time, '%Y-%m-%dT%H:%i:%SZ', 'UTC')
        ) AS timestamp,
        f.block_number AS block_number,
        lower(f.order_hash) AS order_hash,
        multiIf(f.side_code = 1, repeat('0', 64), f.side_code = 2, lower(f.token_id), NULL) AS maker_asset_id,
        multiIf(f.side_code = 1, lower(f.token_id), f.side_code = 2, repeat('0', 64), NULL) AS taker_asset_id,
        if(f.maker_amount IS NULL, NULL, toString(f.maker_amount)) AS maker_amount,
        if(f.taker_amount IS NULL, NULL, toString(f.taker_amount)) AS taker_amount,
        if(f.fee IS NULL, NULL, toString(f.fee)) AS fee,
        f.contract AS contract
    """


def _table_sql() -> str:
    return _settings()["table"]


def _latest_fact_block_sql() -> str:
    settings = _settings()
    table = settings["table"]
    if table != DEFAULT_TABLE:
        return f"SELECT ifNull(max(block_number), 0) FROM {table}"
    # The canonical fact table partitions by intDiv(block_number, 1000000).
    # Locate its newest partition before reading the actual chain watermark.
    return f"""SELECT ifNull(max(block_number), 0) FROM {table}
        PREWHERE intDiv(block_number, 1000000) = (
            SELECT max(toUInt64(partition)) FROM system.parts
            WHERE active AND database = '{settings['database']}' AND table = '{table}'
        )"""


def _int_env(name: str, default: int, *, minimum: int = 1, maximum: int = 1000000) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _float_env(name: str, default: float, *, minimum: float = 0.0, maximum: float = 1_000_000_000.0) -> float:
    try:
        value = float(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _float_or_none(value: Any) -> Optional[float]:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _signal_block(row: Dict[str, Any]) -> Optional[int]:
    for key in ("latest_block", "block_number"):
        try:
            block = int(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if block > 0:
            return block
    return None


def _direction_sign(row: Dict[str, Any]) -> int:
    direction = str(row.get("direction") or row.get("dominant_direction") or "").lower()
    if direction == "bullish":
        return 1
    if direction == "bearish":
        return -1
    side = str(row.get("side") or "").upper()
    outcome = str(row.get("outcome") or "").upper()
    if (side, outcome) in {("BUY", "YES"), ("SELL", "NO")}:
        return 1
    if (side, outcome) in {("SELL", "YES"), ("BUY", "NO")}:
        return -1
    return 0


def _entry_yes_price(row: Dict[str, Any]) -> Optional[float]:
    raw = row.get("entry_yes_price") or row.get("yes_price")
    parsed = _float_or_none(raw)
    if parsed is not None:
        return parsed
    price = _float_or_none(row.get("price") or row.get("avg_price"))
    if price is None:
        return None
    outcome = str(row.get("outcome") or "").upper()
    return 1.0 - price if outcome == "NO" else price


def _attach_post_signal_metrics(ctx: dict, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    for row in rows:
        block = _signal_block(row)
        entry_yes = _entry_yes_price(row)
        direction = _direction_sign(row)
        market_id = row.get("market_id")
        if block is None or entry_yes is None or direction == 0 or market_id is None:
            continue
        signals.append(
            {
                "market_id": int(market_id),
                "block": block,
                "entry_yes": entry_yes,
                "direction": direction,
            }
        )
    if not signals:
        return rows

    market_ids = sorted({signal["market_id"] for signal in signals})
    min_block = min(signal["block"] for signal in signals)
    max_block = max(signal["block"] for signal in signals)
    horizon_blocks = {1: 30, 5: 150, 15: 450}
    market_csv = ", ".join(str(value) for value in market_ids)
    post_rows = _query_json_rows(
        ctx,
        f"""
        SELECT
            market_id,
            block_number,
            log_index,
            toString(if(outcome_code = 2, 1 - price, price)) AS yes_price
        FROM {_table_sql()}
        WHERE market_id IN ({market_csv})
          AND block_number >= {min_block}
          AND block_number <= {max_block + max(horizon_blocks.values()) + 90}
        ORDER BY market_id ASC, block_number ASC, log_index ASC
        FORMAT JSONEachRow
        """,
        timeout_seconds=1.5,
    )
    if post_rows is None:
        return rows

    by_market: Dict[int, List[Dict[str, Any]]] = {}
    for point in post_rows:
        try:
            market_id = int(point.get("market_id") or 0)
            block = int(point.get("block_number") or 0)
            price = float(str(point.get("yes_price")))
        except (TypeError, ValueError):
            continue
        if market_id <= 0 or block <= 0:
            continue
        by_market.setdefault(market_id, []).append({"block": block, "yes_price": price})

    fee = _float_env("POLYDATA_ALPHA_EDGE_FEE_PROBABILITY", DEFAULT_ALPHA_EDGE_FEE_PROBABILITY, maximum=0.25)
    enhanced: List[Dict[str, Any]] = []
    for row in rows:
        block = _signal_block(row)
        entry_yes = _entry_yes_price(row)
        direction = _direction_sign(row)
        try:
            market_id = int(row.get("market_id") or 0)
        except (TypeError, ValueError):
            market_id = 0
        updated = dict(row)
        if block is None or entry_yes is None or direction == 0 or market_id <= 0:
            enhanced.append(updated)
            continue
        points = by_market.get(market_id) or []
        after_yes: Dict[int, Optional[float]] = {}
        for minutes, blocks in horizon_blocks.items():
            target = block + blocks
            value = next((point["yes_price"] for point in points if point["block"] >= target), None)
            after_yes[minutes] = value
            if value is not None:
                action_price = value if direction > 0 else 1.0 - value
                updated[f"price_after_{minutes}m"] = f"{action_price:.10f}"
        five_min_yes = after_yes.get(5)
        if five_min_yes is not None:
            edge = direction * (five_min_yes - entry_yes) - fee
            updated["edge_after_fees"] = f"{edge:.10f}"
        updated.setdefault("entry_yes_price", f"{entry_yes:.10f}")
        updated["edge_fee_probability"] = f"{fee:.10f}"
        enhanced.append(updated)
    return enhanced


def _attach_market_titles(ctx: dict, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    market_ids = sorted({int(row["market_id"]) for row in rows if row.get("market_id") is not None})
    if not market_ids or "query_all" not in ctx:
        return rows
    try:
        placeholders = ", ".join("?" for _ in market_ids)
        market_rows = ctx["query_all"](
            f"SELECT id, title FROM markets WHERE id IN ({placeholders})",
            market_ids,
        )
    except Exception:
        logger = ctx.get("app").logger if ctx.get("app") is not None else None
        if logger is not None:
            logger.warning("ClickHouse OrderFilled title enrichment failed", exc_info=True)
        return rows
    title_map = {int(row["id"]): str(row.get("title") or "") for row in market_rows if row.get("id") is not None}
    for row in rows:
        market_id = row.get("market_id")
        if market_id is not None and not row.get("market_title"):
            row["market_title"] = title_map.get(int(market_id))
    return rows


def _non_placeholder_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for row in rows:
        title = str(row.get("market_title") or "").strip().lower()
        if title.startswith("trade indexer placeholder market"):
            continue
        filtered.append(row)
    return filtered


def get_market_trades(ctx: dict, market_id: int, *, limit: int = 100, offset: int = 0) -> Optional[List[Dict[str, Any]]]:
    limit = min(max(int(limit), 1), 500)
    offset = max(int(offset), 0)
    rows = _query_json_rows(
        ctx,
        f"""
        WITH
            selected AS (
                SELECT *
                FROM {_table_sql()}
                WHERE market_id = {int(market_id)}
                ORDER BY block_number DESC, log_index DESC
                LIMIT {int(offset)}, {int(limit)}
            )
        SELECT {_orderfilled_projection_sql()}
        FROM selected f
        LEFT JOIN (
            SELECT
                block_number,
                argMax(block_time, ingested_at) AS block_time
            FROM block_timestamps
            WHERE block_number IN (SELECT block_number FROM selected)
            GROUP BY block_number
        ) bt ON bt.block_number = f.block_number
        ORDER BY f.block_number DESC, f.log_index DESC
        FORMAT JSONEachRow
        SETTINGS join_use_nulls = 1
        """,
        timeout_seconds=5.0,
    )
    if rows is None:
        return None
    return [ctx["normalize_trade"](row) for row in rows]


def get_recent_trades(ctx: dict, *, limit: int = 24) -> Optional[List[Dict[str, Any]]]:
    limit = min(max(int(limit), 1), 200)
    rows = _query_json_rows(
        ctx,
        f"""
        WITH
            ({_latest_fact_block_sql()}) AS max_fact_block,
            selected AS (
                SELECT * FROM {_table_sql()}
                PREWHERE block_number >= max_fact_block - 20000
                WHERE market_id != 0
                ORDER BY block_number DESC, log_index DESC
                LIMIT {int(limit)}
            )
        SELECT {_orderfilled_projection_sql()}
        FROM selected f
        LEFT JOIN (
            SELECT block_number, argMax(block_time, ingested_at) AS block_time
            FROM block_timestamps
            WHERE block_number IN (SELECT block_number FROM selected)
            GROUP BY block_number
        ) bt ON bt.block_number = f.block_number
        ORDER BY f.block_number DESC, f.log_index DESC
        LIMIT {int(limit)}
        FORMAT JSONEachRow
        """,
        timeout_seconds=5.0,
    )
    if rows is None:
        return None
    normalized = [ctx["normalize_trade"](row) for row in rows]
    market_ids = sorted({int(row["marketId"]) for row in normalized if row.get("marketId") is not None})
    title_map: Dict[int, str] = {}
    if market_ids:
        placeholders = ", ".join("?" for _ in market_ids)
        market_rows = ctx["query_all"](
            f"SELECT id, title FROM markets WHERE id IN ({placeholders})",
            market_ids,
        )
        title_map = {int(row["id"]): str(row.get("title") or "") for row in market_rows if row.get("id") is not None}
    for trade in normalized:
        market_id = trade.get("marketId")
        if market_id is not None and not trade.get("marketTitle"):
            trade["marketTitle"] = title_map.get(int(market_id))
    return normalized


def get_volume_whale_rows(ctx: dict, *, limit: int = 14, window_minutes: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
    limit = min(max(int(limit), 1), 100)
    window_minutes = window_minutes or _int_env(
        "POLYDATA_WHALE_VOLUME_WINDOW_MINUTES",
        DEFAULT_WHALE_VOLUME_WINDOW_MINUTES,
        minimum=5,
        maximum=24 * 60,
    )
    window_blocks = max(60, int(window_minutes) * 30)
    min_watch = _float_env("POLYDATA_WHALE_MIN_NOTIONAL", 1000.0)
    min_elevated = _float_env("POLYDATA_WHALE_ELEVATED_NOTIONAL", 2500.0)
    min_critical = _float_env("POLYDATA_WHALE_CRITICAL_NOTIONAL", 10000.0)
    relative_share = _float_env("POLYDATA_WHALE_MARKET_SHARE_THRESHOLD", 0.10, maximum=1.0)
    relative_min = _float_env("POLYDATA_WHALE_RELATIVE_MIN_NOTIONAL", 500.0)
    min_price = _float_env("POLYDATA_SIGNAL_MIN_PRICE", DEFAULT_SIGNAL_MIN_PRICE, maximum=0.49)
    max_price = _float_env("POLYDATA_SIGNAL_MAX_PRICE", DEFAULT_SIGNAL_MAX_PRICE, minimum=0.51, maximum=1.0)
    notional_expr = "toFloat64(f.price) * toFloat64(f.size)"
    rows = _query_json_rows(
        ctx,
        f"""
        WITH
            ({_latest_fact_block_sql()}) AS max_fact_block,
            {window_blocks} AS window_blocks,
            (SELECT quantileTDigest(0.99)(toFloat64(price) * toFloat64(size)) FROM {_table_sql()} WHERE market_id != 0 AND block_number >= max_fact_block - window_blocks) AS p99_notional,
            (SELECT quantileTDigest(0.995)(toFloat64(price) * toFloat64(size)) FROM {_table_sql()} WHERE market_id != 0 AND block_number >= max_fact_block - window_blocks) AS p995_notional,
            (SELECT quantileTDigest(0.999)(toFloat64(price) * toFloat64(size)) FROM {_table_sql()} WHERE market_id != 0 AND block_number >= max_fact_block - window_blocks) AS p999_notional
        SELECT
            {_orderfilled_projection_sql()},
            toString({notional_expr}) AS notional,
            toString(greatest({min_watch:.6f}, p99_notional)) AS threshold_notional,
            toString(greatest({min_elevated:.6f}, p995_notional)) AS elevated_threshold_notional,
            toString(greatest({min_critical:.6f}, p999_notional)) AS critical_threshold_notional,
            toString(mv.market_window_notional) AS market_window_notional,
            toString(if(mv.market_window_notional > 0, {notional_expr} / mv.market_window_notional, 0)) AS market_share,
            toString(if(f.outcome_code = 2, 1 - f.price, f.price)) AS entry_yes_price,
            multiIf(
                {notional_expr} >= greatest({min_critical:.6f}, p999_notional), 'critical',
                {notional_expr} >= greatest({min_elevated:.6f}, p995_notional), 'elevated',
                'watch'
            ) AS severity,
            'single-trade' AS signal_type,
            'clickhouse-volume-whales' AS source_mode
        FROM {_table_sql()} f
        LEFT JOIN (
            SELECT block_number, argMax(block_time, ingested_at) AS block_time
            FROM block_timestamps
            WHERE block_number >= max_fact_block - window_blocks
            GROUP BY block_number
        ) bt ON bt.block_number = f.block_number
        LEFT JOIN
        (
            SELECT market_id, sum(toFloat64(price) * toFloat64(size)) AS market_window_notional
            FROM {_table_sql()}
            WHERE market_id != 0
              AND block_number >= max_fact_block - window_blocks
            GROUP BY market_id
        ) mv ON mv.market_id = f.market_id
        WHERE f.market_id != 0
          AND f.block_number >= max_fact_block - window_blocks
          AND toFloat64(f.price) >= {min_price:.6f}
          AND toFloat64(f.price) <= {max_price:.6f}
          AND (
              {notional_expr} >= greatest({min_watch:.6f}, p99_notional)
              OR (
                  mv.market_window_notional > 0
                  AND {notional_expr} >= {relative_min:.6f}
                  AND {notional_expr} / mv.market_window_notional >= {relative_share:.6f}
              )
          )
        ORDER BY
            multiIf(severity = 'critical', 3, severity = 'elevated', 2, 1) DESC,
            {notional_expr} DESC,
            market_share DESC,
            f.block_number DESC,
            f.log_index DESC
        LIMIT {limit * 4}
        FORMAT JSONEachRow
        """,
        timeout_seconds=2.5,
    )
    if rows is None:
        return None
    rows = _attach_post_signal_metrics(ctx, rows)
    rows = _attach_market_titles(ctx, rows)
    return _non_placeholder_rows(rows)[:limit]


def get_alpha_volume_signal_rows(ctx: dict, *, limit: int = 8) -> Optional[List[Dict[str, Any]]]:
    limit = min(max(int(limit), 1), 50)
    window_minutes = _int_env(
        "POLYDATA_ALPHA_VOLUME_WINDOW_MINUTES",
        DEFAULT_ALPHA_VOLUME_WINDOW_MINUTES,
        minimum=5,
        maximum=6 * 60,
    )
    baseline_minutes = _int_env(
        "POLYDATA_ALPHA_MARKET_BASELINE_MINUTES",
        DEFAULT_ALPHA_MARKET_BASELINE_MINUTES,
        minimum=15,
        maximum=24 * 60,
    )
    window_blocks = max(60, window_minutes * 30)
    baseline_blocks = max(window_blocks, baseline_minutes * 30)
    min_flow = _float_env("POLYDATA_ALPHA_MIN_FLOW_NOTIONAL", 1000.0)
    min_single = _float_env("POLYDATA_ALPHA_MIN_SINGLE_TRADE_NOTIONAL", 2500.0)
    relative_flow = _float_env("POLYDATA_ALPHA_RELATIVE_MIN_FLOW_NOTIONAL", 500.0)
    relative_share = _float_env("POLYDATA_ALPHA_MARKET_SHARE_THRESHOLD", 0.12, maximum=1.0)
    min_price = _float_env("POLYDATA_SIGNAL_MIN_PRICE", DEFAULT_SIGNAL_MIN_PRICE, maximum=0.49)
    max_price = _float_env("POLYDATA_SIGNAL_MAX_PRICE", DEFAULT_SIGNAL_MAX_PRICE, minimum=0.51, maximum=1.0)
    min_net_strength = _float_env("POLYDATA_ALPHA_MIN_NET_STRENGTH", DEFAULT_ALPHA_MIN_NET_STRENGTH, maximum=1.0)
    price_health_expr = (
        f"greatest(0, least(1, 1 - abs(entry_yes_price - 0.5) / greatest(0.5 - {min_price:.6f}, 0.001)))"
    )
    rows = _query_json_rows(
        ctx,
        f"""
        WITH
            ({_latest_fact_block_sql()}) AS max_fact_block,
            {window_blocks} AS window_blocks,
            {baseline_blocks} AS baseline_blocks,
            (
                SELECT quantileTDigest(0.95)(flow_notional)
                FROM
                (
                    SELECT sum(toFloat64(price) * toFloat64(size)) AS flow_notional
                    FROM {_table_sql()}
                    WHERE market_id != 0
                      AND block_number >= max_fact_block - window_blocks
                      AND toFloat64(price) >= {min_price:.6f}
                      AND toFloat64(price) <= {max_price:.6f}
                    GROUP BY market_id, outcome_code, side_code
                )
            ) AS p95_flow_notional
        SELECT
            f.market_id AS market_id,
            multiIf(
                sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 1 AND f.outcome_code = 1) OR (f.side_code = 2 AND f.outcome_code = 2))
                >= sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 2 AND f.outcome_code = 1) OR (f.side_code = 1 AND f.outcome_code = 2)),
                'bullish',
                'bearish'
            ) AS direction,
            multiIf(direction = 'bullish', 'YES', direction = 'bearish', 'NO', 'UNKNOWN') AS outcome,
            'BUY' AS side,
            count() AS trade_count,
            toString(greatest(
                sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 1 AND f.outcome_code = 1) OR (f.side_code = 2 AND f.outcome_code = 2)),
                sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 2 AND f.outcome_code = 1) OR (f.side_code = 1 AND f.outcome_code = 2))
            )) AS flow_notional,
            toString(abs(
                sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 1 AND f.outcome_code = 1) OR (f.side_code = 2 AND f.outcome_code = 2))
                - sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 2 AND f.outcome_code = 1) OR (f.side_code = 1 AND f.outcome_code = 2))
            )) AS net_flow_notional,
            toString(
                sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 1 AND f.outcome_code = 1) OR (f.side_code = 2 AND f.outcome_code = 2))
            ) AS bullish_notional,
            toString(
                sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 2 AND f.outcome_code = 1) OR (f.side_code = 1 AND f.outcome_code = 2))
            ) AS bearish_notional,
            toString(
                least(
                    sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 1 AND f.outcome_code = 1) OR (f.side_code = 2 AND f.outcome_code = 2)),
                    sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 2 AND f.outcome_code = 1) OR (f.side_code = 1 AND f.outcome_code = 2))
                )
            ) AS opposite_flow_notional,
            toString(
                abs(
                    sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 1 AND f.outcome_code = 1) OR (f.side_code = 2 AND f.outcome_code = 2))
                    - sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 2 AND f.outcome_code = 1) OR (f.side_code = 1 AND f.outcome_code = 2))
                )
                / greatest(
                    sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 1 AND f.outcome_code = 1) OR (f.side_code = 2 AND f.outcome_code = 2))
                    + sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 2 AND f.outcome_code = 1) OR (f.side_code = 1 AND f.outcome_code = 2)),
                    1
                )
            ) AS net_direction_strength,
            toString(
                1 - abs(
                    sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 1 AND f.outcome_code = 1) OR (f.side_code = 2 AND f.outcome_code = 2))
                    - sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 2 AND f.outcome_code = 1) OR (f.side_code = 1 AND f.outcome_code = 2))
                )
                / greatest(
                    sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 1 AND f.outcome_code = 1) OR (f.side_code = 2 AND f.outcome_code = 2))
                    + sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 2 AND f.outcome_code = 1) OR (f.side_code = 1 AND f.outcome_code = 2)),
                    1
                )
            ) AS churn_ratio,
            toString(max(toFloat64(f.price) * toFloat64(f.size))) AS max_trade_notional,
            toString(sum(toFloat64(f.size))) AS flow_size,
            argMax(toFloat64(if(f.outcome_code = 2, 1 - f.price, f.price)), tuple(f.block_number, f.log_index)) AS entry_yes_price,
            toString(multiIf(direction = 'bullish', entry_yes_price, direction = 'bearish', 1 - entry_yes_price, entry_yes_price)) AS avg_price,
            toString(mv.market_baseline_notional) AS market_baseline_notional,
            toString(if(mv.market_baseline_notional > 0, greatest(
                sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 1 AND f.outcome_code = 1) OR (f.side_code = 2 AND f.outcome_code = 2)),
                sumIf(toFloat64(f.price) * toFloat64(f.size), (f.side_code = 2 AND f.outcome_code = 1) OR (f.side_code = 1 AND f.outcome_code = 2))
            ) / mv.market_baseline_notional, 0)) AS market_share,
            uniqExact(f.taker) AS unique_trader_count,
            max(f.block_number) AS latest_block,
            argMax(f.log_index, tuple(f.block_number, f.log_index)) AS latest_log_index,
            lower(argMax(f.tx_hash, tuple(f.block_number, f.log_index))) AS tx_hash,
            if(
                ifNull(argMax(bt.block_time, tuple(f.block_number, f.log_index)) <= toDateTime('2000-01-01 00:00:00', 'UTC'), 1),
                CAST(NULL, 'Nullable(String)'),
                formatDateTime(argMax(bt.block_time, tuple(f.block_number, f.log_index)), '%Y-%m-%dT%H:%i:%SZ', 'UTC')
            ) AS timestamp,
            multiIf(
                toFloat64(flow_notional) >= 10000 OR max(toFloat64(f.price) * toFloat64(f.size)) >= 10000 OR toFloat64(market_share) >= 0.25, 'critical',
                toFloat64(flow_notional) >= 2500 OR max(toFloat64(f.price) * toFloat64(f.size)) >= 2500 OR toFloat64(market_share) >= 0.15, 'elevated',
                'watch'
            ) AS severity,
            toString({price_health_expr}) AS price_health,
            toString(
                least(toFloat64(net_flow_notional) / 1000 * 35, 40)
                + least(toFloat64(net_direction_strength) * 35, 25)
                + least(toFloat64(market_share) * 100, 15)
                + least({price_health_expr} * 15, 10)
                + least(toFloat64(unique_trader_count), 10)
            ) AS score,
            toString(
                least(toFloat64(flow_notional) / 1000 * 30, 45)
                + least(max(toFloat64(f.price) * toFloat64(f.size)) / 2500 * 20, 25)
                + least(toFloat64(market_share) * 100, 20)
                + least(count(), 10)
            ) AS volume_score,
            toString(greatest({min_flow:.6f}, p95_flow_notional)) AS threshold_flow_notional,
            {window_minutes} AS window_minutes,
            {baseline_minutes} AS baseline_minutes,
            'net-directional-flow' AS signal_type,
            'clickhouse-volume-alpha' AS source_mode
        FROM {_table_sql()} f
        LEFT JOIN
        (
            SELECT block_number, argMax(block_time, ingested_at) AS block_time
            FROM block_timestamps
            WHERE block_number >= max_fact_block - baseline_blocks
            GROUP BY block_number
        ) bt ON bt.block_number = f.block_number
        LEFT JOIN
        (
            SELECT market_id, sum(toFloat64(price) * toFloat64(size)) AS market_baseline_notional
            FROM {_table_sql()}
            WHERE market_id != 0
              AND block_number >= max_fact_block - baseline_blocks
            GROUP BY market_id
        ) mv ON mv.market_id = f.market_id
        WHERE f.market_id != 0
          AND f.block_number >= max_fact_block - window_blocks
          AND toFloat64(f.price) >= {min_price:.6f}
          AND toFloat64(f.price) <= {max_price:.6f}
        GROUP BY f.market_id, mv.market_baseline_notional
        HAVING
            entry_yes_price >= {min_price:.6f}
            AND entry_yes_price <= {max_price:.6f}
            AND toFloat64(net_direction_strength) >= {min_net_strength:.6f}
            AND (
            toFloat64(flow_notional) >= greatest({min_flow:.6f}, p95_flow_notional)
            OR max(toFloat64(f.price) * toFloat64(f.size)) >= {min_single:.6f}
            OR (
                toFloat64(flow_notional) >= {relative_flow:.6f}
                AND toFloat64(market_share) >= {relative_share:.6f}
            )
            )
        ORDER BY
            toFloat64(score) DESC,
            toFloat64(net_direction_strength) DESC,
            toFloat64(market_share) DESC,
            toFloat64(price_health) DESC,
            toFloat64(unique_trader_count) DESC,
            latest_block DESC,
            latest_log_index DESC
        LIMIT {limit * 6}
        FORMAT JSONEachRow
        """,
        timeout_seconds=2.5,
    )
    if rows is None:
        return None
    rows = _attach_post_signal_metrics(ctx, rows)
    rows.sort(
        key=lambda row: (
            _float_or_none(row.get("score")) or 0.0,
            _float_or_none(row.get("net_direction_strength")) or 0.0,
            _float_or_none(row.get("market_share")) or 0.0,
            _float_or_none(row.get("price_health")) or 0.0,
            _float_or_none(row.get("edge_after_fees")) or -999.0,
            int(row.get("unique_trader_count") or 0),
        ),
        reverse=True,
    )
    rows = _attach_market_titles(ctx, rows)
    return _non_placeholder_rows(rows)[:limit]


def get_price_series(ctx: dict, market_id: int, *, limit: int = 400) -> Optional[List[Dict[str, Any]]]:
    limit = min(max(int(limit), 1), 1200)
    rows = _query_json_rows(
        ctx,
        f"""
        WITH
            selected AS (
                SELECT outcome_code, price, block_number, log_index
                FROM {_table_sql()}
                WHERE market_id = {int(market_id)}
                ORDER BY block_number DESC, log_index DESC
                LIMIT {int(limit)}
            )
        SELECT
            if(
                ifNull(bt.block_time <= toDateTime('2000-01-01 00:00:00', 'UTC'), 1),
                CAST(NULL, 'Nullable(String)'),
                formatDateTime(bt.block_time, '%Y-%m-%dT%H:%i:%SZ', 'UTC')
            ) AS timestamp,
            multiIf(f.outcome_code = 1, 'YES', f.outcome_code = 2, 'NO', 'UNKNOWN') AS outcome,
            toString(f.price) AS price,
            f.block_number AS block_number,
            f.log_index AS log_index
        FROM selected f
        LEFT JOIN (
            SELECT
                block_number,
                argMax(block_time, ingested_at) AS block_time
            FROM block_timestamps
            WHERE block_number IN (SELECT block_number FROM selected)
            GROUP BY block_number
        ) bt ON bt.block_number = f.block_number
        ORDER BY f.block_number DESC, f.log_index DESC
        FORMAT JSONEachRow
        SETTINGS join_use_nulls = 1
        """,
        timeout_seconds=5.0,
    )
    if rows is None:
        return None
    rows.reverse()
    compacted: Dict[str, Dict[str, Any]] = {}
    points: List[Dict[str, Any]] = []
    for row in rows:
        price = _float_or_none(row.get("price"))
        timestamp = str(row.get("timestamp") or "").strip()
        if price is None or not timestamp:
            continue
        outcome = str(row.get("outcome") or "").upper()
        if outcome == "YES":
            yes_price = price
        elif outcome == "NO":
            yes_price = 1.0 - price
        else:
            continue
        yes_price = max(0.0, min(1.0, yes_price))
        block = int(row.get("block_number") or 0)
        log_index = int(row.get("log_index") or 0)
        compacted[timestamp] = {
            "timestamp": timestamp,
            "yesPrice": f"{yes_price:.10f}",
            "noPrice": f"{1.0 - yes_price:.10f}",
            "_sort": (block, log_index),
        }
    for point in sorted(compacted.values(), key=lambda item: item.get("_sort") or (0, 0)):
        point.pop("_sort", None)
        if points and points[-1].get("yesPrice") == point.get("yesPrice") and points[-1].get("timestamp") == point.get("timestamp"):
            continue
        points.append(point)
    return points


def get_market_stats(ctx: dict, market_ids: Iterable[int], *, hours: int = 24) -> Optional[Dict[int, Dict[str, Any]]]:
    ids = sorted({int(value) for value in market_ids if value is not None})
    if not ids:
        return {}
    id_csv = ", ".join(str(value) for value in ids)
    block_window = max(1, int(hours)) * 1800
    rows = _query_json_rows(
        ctx,
        f"""
        WITH ({_latest_fact_block_sql()}) AS max_fact_block
        SELECT
            f.market_id AS market_id,
            count() AS trade_count_24h,
            toString(sum(f.size * f.price)) AS volume_24h,
            argMax(toString(if(f.outcome_code = 2, 1 - f.price, f.price)), tuple(f.block_number, f.log_index)) AS latest_price,
            max(f.block_number) AS latest_trade_block
        FROM {_table_sql()} f
        WHERE f.market_id IN ({id_csv})
          AND f.block_number >= max_fact_block - {block_window}
        GROUP BY f.market_id
        FORMAT JSONEachRow
        """,
    )
    if rows is None:
        return None
    return {
        int(row["market_id"]): {
            "trade_count_24h": int(row.get("trade_count_24h") or 0),
            "volume_24h": row.get("volume_24h") or 0,
            "latest_price": row.get("latest_price"),
            "latest_trade_block": row.get("latest_trade_block"),
        }
        for row in rows
        if row.get("market_id") is not None
    }


def get_recent_market_activity(ctx: dict, *, limit: int = 1000, hours: int = 24) -> Optional[List[Dict[str, Any]]]:
    limit = min(max(int(limit), 1), 5000)
    hours = max(1, int(hours))
    rows = _query_json_rows(
        ctx,
        f"""
        WITH
            ({_latest_fact_block_sql()}) AS max_fact_block
        SELECT
            f.market_id AS market_id,
            count() AS trade_count_24h,
            toString(sum(f.size * f.price)) AS volume_24h,
            argMax(toString(if(f.outcome_code = 2, 1 - f.price, f.price)), tuple(f.block_number, f.log_index)) AS latest_price,
            if(
                ifNull(argMax(bt.block_time, tuple(f.block_number, f.log_index)) <= toDateTime('2000-01-01 00:00:00', 'UTC'), 1),
                CAST(NULL, 'Nullable(String)'),
                formatDateTime(argMax(bt.block_time, tuple(f.block_number, f.log_index)), '%Y-%m-%dT%H:%i:%SZ', 'UTC')
            ) AS latest_trade_at
        FROM {_table_sql()} f
        LEFT JOIN
        (
            SELECT block_number, argMax(block_time, ingested_at) AS block_time
            FROM block_timestamps
            WHERE block_number >= max_fact_block - {hours * 1800}
            GROUP BY block_number
        ) bt ON bt.block_number = f.block_number
        WHERE f.market_id != 0
          AND f.block_number >= max_fact_block - {hours * 1800}
        GROUP BY f.market_id
        HAVING (trade_count_24h > 0 OR toDecimal128(volume_24h, 10) > 0)
           AND toDecimal128(latest_price, 10) >= 0.05
           AND toDecimal128(latest_price, 10) <= 0.95
        ORDER BY trade_count_24h DESC, toDecimal128(volume_24h, 10) DESC, latest_trade_at DESC
        LIMIT {limit}
        FORMAT JSONEachRow
        """,
        timeout_seconds=2.0,
    )
    if rows is None:
        return None
    return [
        {
            "market_id": int(row.get("market_id") or 0),
            "trade_count_24h": int(row.get("trade_count_24h") or 0),
            "volume_24h": row.get("volume_24h") or 0,
            "latest_price": row.get("latest_price"),
            "last_trade_at": row.get("latest_trade_at"),
            "latest_trade_at": row.get("latest_trade_at"),
        }
        for row in rows
        if int(row.get("market_id") or 0) > 0
    ]
