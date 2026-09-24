"""Fixed-template quant backtest engine.

This first production pass intentionally keeps the strategy small: one long
position template, two price sources, and durable run/result tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from typing import Any

from .execution import (
    BookSnapshot,
    FillResult,
    execution_config_from_params,
    lookup_snapshot,
    parse_book_snapshot,
    simulate_depth_fill,
    snapshot_from_any,
    snapshot_to_dict,
)
from .execution_profiles import apply_adverse_slippage, apply_probability_haircut, effective_execution_profile
from .frameworks import normalize_backtest_engine, run_framework_backtest
from .ledger import build_ledger_rows, ledger_summary
from .orders import next_order_id, order_from_fill, summarize_orders
from .runners.execution_replay import OrderIntent, ReplayTradeEvent, replay_limit_order, sequence_key
from ..prices.build_targets import target_reason, upsert_price_build_targets_for_market


SUPPORTED_PRICE_SOURCES = {"frontend", "orderfilled_block_close"}
PRICE_QUERY_GUARD_VERSION = "phase1_keyed_price_access_v1"


DATA_ACCESS_POLICY = {
    "market_search": {
        "allowed_tables": [
            "quant.market_event_metadata",
            "quant.market_event_members",
            "quant.market_price_build_market_progress",
            "quant.market_token_metadata",
        ],
        "forbidden": "Do not discover markets from quant.market_token_block_close with GROUP BY/count or title contains search.",
    },
    "backtest_price_read": {
        "allowed_tables": ["quant.market_token_block_close", "quant.market_token_frontend_price_1m"],
        "required_access": ["token_id range", "market_slug + token_side range", "market_id + token_side range"],
        "forbidden": "Do not scan or group all markets from price detail tables during online backtests.",
    },
    "raw_orderfilled_verification": {
        "allowed_tables": ["ClickHouse poly_orderfilled.orderfilled_fact"],
        "required_access": ["market_id", "token_id", "block_number range", "explicit limit"],
        "forbidden": "Do not run online all-market raw fact aggregation.",
    },
}


@dataclass(frozen=True)
class BacktestParameters:
    entry_threshold: Decimal = Decimal("0.58")
    exit_threshold: Decimal = Decimal("0.44")
    stop_loss: Decimal = Decimal("0.075")
    take_profit: Decimal = Decimal("0.16")
    max_holding_bars: int = 96
    initial_capital: Decimal = Decimal("100000")
    position_size: Decimal = Decimal("100")
    fee_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    liquidity_cap_pct: Decimal = Decimal("100")
    max_position_notional: Decimal = Decimal("0")
    min_fill_pct: Decimal = Decimal("0")
    execution_price_mode: str = "ORDERFILLED_LIMIT_REPLAY"
    execution_profile: str = "realistic"
    order_role: str = "taker"
    latency_blocks: int = 0
    adverse_slippage_cents: Decimal = Decimal("0.005")
    fill_probability_haircut_pct: Decimal = Decimal("20")
    latency_seconds: Decimal = Decimal("0")
    max_book_staleness_seconds: Decimal = Decimal("900")
    allow_partial_fill: bool = True
    min_fill_size: Decimal = Decimal("0")
    reject_on_stale_book: bool = True
    final_valuation_mode: str = "SETTLEMENT"
    max_entry_price: Decimal = Decimal("1")
    min_exit_price: Decimal = Decimal("0")
    buy_limit_price: Decimal | None = None
    sell_limit_price: Decimal | None = None
    settlement_value: Decimal | None = None


@dataclass(frozen=True)
class PricePoint:
    x_value: int
    price: Decimal
    volume: Decimal
    trade_count: int = 0
    timestamp: datetime | None = None


@dataclass
class OpenPosition:
    trade_index: int
    entry_index: int
    entry_x: int
    entry_price: Decimal
    size: Decimal
    requested_notional: Decimal
    filled_notional: Decimal
    fill_pct: Decimal
    fill_status: str = "FILLED"
    book_snapshot_id: int | None = None
    snapshot_version: str | None = None
    staleness_seconds: Decimal | None = None
    staleness_blocks: int | None = None
    avg_fill_price: Decimal | None = None
    fill_probability: Decimal = Decimal("0")
    block_volume: Decimal = Decimal("0")
    trade_count: int = 0
    available_notional: Decimal = Decimal("0")
    entry_order_id: str | None = None
    entry_fee_cost: Decimal = Decimal("0")
    entry_slippage_cost: Decimal = Decimal("0")


def decimal_or_default(value: Any, default: Decimal) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(str(value))
    except Exception:
        return default
    return parsed if parsed > 0 else default


def bool_or_default(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed >= 0 else None


def normalize_price_source(value: Any) -> str:
    text = str(value or "frontend").strip().lower()
    aliases = {
        "orderfilled": "orderfilled_block_close",
        "block_close": "orderfilled_block_close",
        "orderfilled_block_close": "orderfilled_block_close",
        "frontend_price_history": "frontend",
        "frontend-price-history": "frontend",
        "frontend": "frontend",
    }
    source = aliases.get(text, text)
    if source not in SUPPORTED_PRICE_SOURCES:
        raise ValueError(f"unsupported price_source: {value!r}")
    return source


def parse_parameters(payload: dict[str, Any]) -> BacktestParameters:
    return BacktestParameters(
        entry_threshold=decimal_or_default(payload.get("entry_threshold", payload.get("entryThreshold")), Decimal("0.58")),
        exit_threshold=decimal_or_default(payload.get("exit_threshold", payload.get("exitThreshold")), Decimal("0.44")),
        stop_loss=decimal_or_default(payload.get("stop_loss", payload.get("stopLoss")), Decimal("0.075")),
        take_profit=decimal_or_default(payload.get("take_profit", payload.get("takeProfit")), Decimal("0.16")),
        max_holding_bars=int_or_default(payload.get("max_holding_bars", payload.get("maxHoldingBars")), 96),
        initial_capital=decimal_or_default(payload.get("initial_capital", payload.get("initialCapital")), Decimal("100000")),
        position_size=decimal_or_default(payload.get("position_size", payload.get("positionSize")), Decimal("100")),
        fee_bps=decimal_or_default(payload.get("fee_bps", payload.get("feeBps")), Decimal("0")),
        slippage_bps=decimal_or_default(payload.get("slippage_bps", payload.get("slippageBps")), Decimal("0")),
        liquidity_cap_pct=decimal_or_default(payload.get("liquidity_cap_pct", payload.get("liquidityCapPct")), Decimal("100")),
        max_position_notional=decimal_or_default(payload.get("max_position_notional", payload.get("maxPositionNotional")), Decimal("0")),
        min_fill_pct=decimal_or_default(payload.get("min_fill_pct", payload.get("minFillPct")), Decimal("0")),
        execution_price_mode=str(payload.get("execution_price_mode", payload.get("executionPriceMode", "ORDERFILLED_LIMIT_REPLAY")) or "ORDERFILLED_LIMIT_REPLAY").upper(),
        execution_profile=str(payload.get("execution_profile", payload.get("executionProfile", "realistic")) or "realistic").lower(),
        order_role=str(payload.get("order_role", payload.get("orderRole", "taker")) or "taker").lower(),
        latency_blocks=max(0, int_or_default(payload.get("latency_blocks", payload.get("latencyBlocks")), 0)),
        adverse_slippage_cents=decimal_or_default(payload.get("adverse_slippage_cents", payload.get("adverseSlippageCents")), Decimal("0.005")),
        fill_probability_haircut_pct=decimal_or_default(payload.get("fill_probability_haircut_pct", payload.get("fillProbabilityHaircutPct")), Decimal("20")),
        latency_seconds=decimal_or_default(payload.get("latency_seconds", payload.get("latencySeconds")), Decimal("0")),
        max_book_staleness_seconds=decimal_or_default(payload.get("max_book_staleness_seconds", payload.get("maxBookStalenessSeconds")), Decimal("900")),
        allow_partial_fill=bool_or_default(payload.get("allow_partial_fill", payload.get("allowPartialFill")), True),
        min_fill_size=decimal_or_default(payload.get("min_fill_size", payload.get("minFillSize")), Decimal("0")),
        reject_on_stale_book=bool_or_default(payload.get("reject_on_stale_book", payload.get("rejectOnStaleBook")), True),
        final_valuation_mode=str(payload.get("final_valuation_mode", payload.get("finalValuationMode", "SETTLEMENT")) or "SETTLEMENT").upper(),
        max_entry_price=decimal_or_default(payload.get("max_entry_price", payload.get("maxEntryPrice")), Decimal("1")),
        min_exit_price=decimal_or_default(payload.get("min_exit_price", payload.get("minExitPrice")), Decimal("0")),
        buy_limit_price=decimal_or_none(payload.get("buy_limit_price", payload.get("buyLimitPrice"))),
        sell_limit_price=decimal_or_none(payload.get("sell_limit_price", payload.get("sellLimitPrice"))),
        settlement_value=decimal_or_none(payload.get("settlement_value", payload.get("settlementValue"))),
    )


def _decimal_text(value: Decimal) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return _decimal_text(value) if value is not None else None


def backtest_parameter_snapshot(
    *,
    market_slug: str,
    token_side: str,
    token_id: str | None,
    outcome_label: str | None,
    price_source: str,
    backtest_engine: str,
    from_ts: int | None,
    to_ts: int | None,
    from_block: int | None,
    to_block: int | None,
    params: BacktestParameters,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = {
        "strategy": "fixed_threshold_v1",
        "market_slug": market_slug,
        "token_side": token_side,
        "token_id": token_id,
        "outcome_label": outcome_label,
        "price_source": price_source,
        "backtest_engine": backtest_engine,
        "from_ts": from_ts,
        "to_ts": to_ts,
        "from_block": from_block,
        "to_block": to_block,
        "parameters": {
            "entry_threshold": _decimal_text(params.entry_threshold),
            "exit_threshold": _decimal_text(params.exit_threshold),
            "stop_loss": _decimal_text(params.stop_loss),
            "take_profit": _decimal_text(params.take_profit),
            "max_holding_bars": int(params.max_holding_bars),
            "initial_capital": _decimal_text(params.initial_capital),
            "position_size": _decimal_text(params.position_size),
            "fee_bps": _decimal_text(params.fee_bps),
            "slippage_bps": _decimal_text(params.slippage_bps),
            "liquidity_cap_pct": _decimal_text(params.liquidity_cap_pct),
            "max_position_notional": _decimal_text(params.max_position_notional),
            "min_fill_pct": _decimal_text(params.min_fill_pct),
            "execution_price_mode": params.execution_price_mode,
            "execution_profile": params.execution_profile,
            "order_role": params.order_role,
            "latency_blocks": int(params.latency_blocks),
            "adverse_slippage_cents": _decimal_text(params.adverse_slippage_cents),
            "fill_probability_haircut_pct": _decimal_text(params.fill_probability_haircut_pct),
            "latency_seconds": _decimal_text(params.latency_seconds),
            "max_book_staleness_seconds": _decimal_text(params.max_book_staleness_seconds),
            "allow_partial_fill": bool(params.allow_partial_fill),
            "min_fill_size": _decimal_text(params.min_fill_size),
            "reject_on_stale_book": bool(params.reject_on_stale_book),
            "final_valuation_mode": params.final_valuation_mode,
            "max_entry_price": _decimal_text(params.max_entry_price),
            "min_exit_price": _decimal_text(params.min_exit_price),
            "buy_limit_price": _optional_decimal_text(params.buy_limit_price),
            "sell_limit_price": _optional_decimal_text(params.sell_limit_price),
            "settlement_value": _optional_decimal_text(params.settlement_value),
        },
    }
    if execution_context:
        snapshot["execution_context"] = execution_context
    return snapshot


def backtest_parameter_fingerprint(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _json_safe_scalar(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _sanitize_json_value(value: Any, *, depth: int = 0, max_items: int = 24) -> Any:
    if depth > 4:
        return _json_safe_scalar(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, inner in list(value.items())[:max_items]:
            if not isinstance(key, str):
                key = str(key)
            result[key[:80]] = _sanitize_json_value(inner, depth=depth + 1, max_items=max_items)
        return result
    if isinstance(value, list):
        return [_sanitize_json_value(item, depth=depth + 1, max_items=max_items) for item in value[:max_items]]
    return _json_safe_scalar(value)


def _execution_context_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("execution_context", payload.get("executionContext"))
    if not isinstance(raw, dict):
        raw = {}
    context = _sanitize_json_value(raw)
    if not isinstance(context, dict):
        context = {}
    mode = str(payload.get("execution_price_mode", payload.get("executionPriceMode", "ORDERFILLED_LIMIT_REPLAY")) or "").upper()
    context.setdefault("model", "fixed_threshold_v1")
    context.setdefault(
        "fill_model",
        "orderfilled_limit_cross_then_settlement" if mode in {"ORDERFILLED_LIMIT_REPLAY", "LIMIT_REPLAY"} else "orderfilled_probability_with_participation_cap",
    )
    return context


def create_and_execute_backtest(conn: Any, payload: dict[str, Any]) -> dict[str, Any]:
    run_id = create_backtest_run(conn, payload)
    try:
        execute_backtest_run(conn, run_id)
    except Exception as exc:
        mark_run_failed(conn, run_id, str(exc))
    return get_backtest_run_for_update_free(conn, run_id)


def list_queued_backtest_run_ids(conn: Any, *, limit: int = 10) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_id
            FROM quant.quant_backtest_runs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (int(limit),),
        )
        return [int(row["run_id"]) for row in cur.fetchall()]


def claim_backtest_run(conn: Any, run_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quant.quant_backtest_runs
            SET status = 'running',
                started_at = CASE WHEN started_at IS NULL THEN now() ELSE started_at END,
                error = NULL
            WHERE run_id = %s
              AND status = 'queued'
            RETURNING run_id
            """,
            (int(run_id),),
        )
        return cur.fetchone() is not None


def create_backtest_run(conn: Any, payload: dict[str, Any]) -> int:
    market_slug = str(payload.get("market_slug", payload.get("marketSlug", ""))).strip()
    if not market_slug:
        raise ValueError("market_slug is required")
    token_id = str(payload.get("token_id", payload.get("tokenId", ""))).strip() or None
    outcome_label = str(payload.get("outcome_label", payload.get("outcomeLabel", ""))).strip() or None
    token_side = str(payload.get("token_side", payload.get("tokenSide", "YES"))).strip().upper() or "YES"
    if token_side not in {"YES", "NO"}:
        raise ValueError("token_side must be YES or NO")
    price_source = normalize_price_source(payload.get("price_source", payload.get("priceSource", "frontend")))
    backtest_engine = normalize_backtest_engine(
        payload.get("backtest_engine", payload.get("backtestEngine", payload.get("engine", payload.get("framework", "builtin"))))
    )
    params = parse_parameters(payload)
    from_ts = _optional_int(payload.get("from_ts", payload.get("from")))
    to_ts = _optional_int(payload.get("to_ts", payload.get("to")))
    from_block = _optional_int(payload.get("from_block", payload.get("fromBlock")))
    to_block = _optional_int(payload.get("to_block", payload.get("toBlock")))
    execution_context = _execution_context_from_payload(payload)
    parameter_snapshot = backtest_parameter_snapshot(
        market_slug=market_slug,
        token_side=token_side,
        token_id=token_id,
        outcome_label=outcome_label,
        price_source=price_source,
        backtest_engine=backtest_engine,
        from_ts=from_ts,
        to_ts=to_ts,
        from_block=from_block,
        to_block=to_block,
        params=params,
        execution_context=execution_context,
    )
    parameter_fingerprint = backtest_parameter_fingerprint(parameter_snapshot)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO quant.quant_backtest_runs (
                status, market_slug, token_side, price_source, backtest_engine,
                from_ts, to_ts, from_block, to_block, meta
            )
            VALUES ('queued', %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING run_id
            """,
            (
                market_slug,
                token_side,
                price_source,
                backtest_engine,
                from_ts,
                to_ts,
                from_block,
                to_block,
                json.dumps(
                    {
                        "strategy": "fixed_threshold_v1",
                        "backtest_engine": backtest_engine,
                        "token_id": token_id,
                        "outcome_label": outcome_label,
                        "execution_context": execution_context,
                        "parameter_fingerprint": parameter_fingerprint,
                        "parameter_snapshot": parameter_snapshot,
                    }
                ),
            ),
        )
        run_id = int(cur.fetchone()["run_id"])
        cur.execute(
            """
            INSERT INTO quant.quant_backtest_parameters (
                run_id, entry_threshold, exit_threshold, stop_loss, take_profit,
                max_holding_bars, initial_capital, position_size,
                fee_bps, slippage_bps, liquidity_cap_pct,
                max_position_notional, min_fill_pct,
                execution_price_mode, execution_profile, order_role,
                latency_blocks, adverse_slippage_cents, fill_probability_haircut_pct,
                latency_seconds, max_book_staleness_seconds,
                allow_partial_fill, min_fill_size, reject_on_stale_book,
                final_valuation_mode, max_entry_price, min_exit_price,
                buy_limit_price, sell_limit_price, settlement_value
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id,
                params.entry_threshold,
                params.exit_threshold,
                params.stop_loss,
                params.take_profit,
                params.max_holding_bars,
                params.initial_capital,
                params.position_size,
                params.fee_bps,
                params.slippage_bps,
                params.liquidity_cap_pct,
                params.max_position_notional,
                params.min_fill_pct,
                params.execution_price_mode,
                params.execution_profile,
                params.order_role,
                params.latency_blocks,
                params.adverse_slippage_cents,
                params.fill_probability_haircut_pct,
                params.latency_seconds,
                params.max_book_staleness_seconds,
                params.allow_partial_fill,
                params.min_fill_size,
                params.reject_on_stale_book,
                params.final_valuation_mode,
                params.max_entry_price,
                params.min_exit_price,
                params.buy_limit_price,
                params.sell_limit_price,
                params.settlement_value,
            ),
        )
    upsert_price_build_targets_for_market(
        conn,
        source=price_source,
        market_slug=market_slug,
        token_side=token_side,
        priority=2000,
        reason=target_reason("backtest_requested", {"run_id": run_id}),
        from_ts=from_ts,
        to_ts=to_ts,
        from_block=from_block,
        to_block=to_block,
    )
    return run_id


def execute_backtest_run(conn: Any, run_id: int) -> None:
    run = _get_run(conn, run_id)
    params = _get_parameters(conn, run_id)
    _set_run_status(conn, run_id, "running")
    clob_snapshots = load_clob_execution_snapshots(conn, run)
    if clob_snapshots:
        run["_clob_snapshots"] = [snapshot_to_dict(snapshot) for snapshot in clob_snapshots]
    points = fetch_price_points(conn, run)
    if len(points) < 2:
        raise RuntimeError("not enough price rows for backtest")
    data_quality_report = build_data_quality_report(points, run)
    data_quality_report["execution_depth"] = _public_clob_execution_context(clob_snapshots)
    result = run_framework_backtest(
        run.get("backtest_engine") or "builtin",
        points,
        run,
        params,
        builtin_simulator=simulate_strategy,
        metrics_builder=build_metrics,
    )
    result.setdefault("metrics", [])
    result["metrics"].extend(data_quality_metrics(data_quality_report))
    replace_backtest_results(conn, run_id, result)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quant.quant_backtest_runs
            SET status = 'succeeded',
                rows_processed = %s,
                meta = meta || %s::jsonb,
                finished_at = now(),
                error = NULL
            WHERE run_id = %s
            """,
            (
                len(points),
                json.dumps({"actual_data_quality": data_quality_report}),
                run_id,
            ),
        )


def mark_run_failed(conn: Any, run_id: int, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quant.quant_backtest_runs
            SET status = 'failed', error = %s, finished_at = now()
            WHERE run_id = %s
            """,
            (error[:4000], run_id),
        )


def fetch_price_points(conn: Any, run: dict[str, Any], *, limit: int = 25000) -> list[PricePoint]:
    meta = run.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    token_id = str(meta.get("token_id") or "").strip()
    if run["price_source"] == "frontend":
        filters: list[str]
        values: list[Any]
        if token_id:
            filters = ["token_id = %s"]
            values = [token_id]
        else:
            filters = ["market_slug = %s", "token_side = %s"]
            values = [run["market_slug"], run["token_side"]]
        if run.get("from_ts") is not None:
            filters.append("timestamp >= %s")
            values.append(run["from_ts"])
        if run.get("to_ts") is not None:
            filters.append("timestamp <= %s")
            values.append(run["to_ts"])
        values.append(limit)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT timestamp AS x_value, price, 0::numeric AS volume, 0::bigint AS trade_count, ts_minute AS block_timestamp
                FROM quant.market_token_frontend_price_1m
                WHERE {" AND ".join(filters)}
                ORDER BY timestamp ASC
                LIMIT %s
                """,
                values,
            )
            return [_price_point(row) for row in cur.fetchall()]

    if token_id:
        filters = ["token_id = %s"]
        values = [token_id]
    else:
        filters = ["market_slug = %s", "token_side = %s"]
        values = [run["market_slug"], run["token_side"]]
    if run.get("from_block") is not None:
        filters.append("block_number >= %s")
        values.append(run["from_block"])
    if run.get("to_block") is not None:
        filters.append("block_number <= %s")
        values.append(run["to_block"])
    values.append(limit)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT block_number AS x_value, close_price AS price, volume, trade_count, block_timestamp
            FROM quant.market_token_block_close
            WHERE {" AND ".join(filters)}
            ORDER BY block_number ASC
            LIMIT %s
            """,
            values,
        )
        return [_price_point(row) for row in cur.fetchall()]


def price_access_report(run: dict[str, Any], points: list[PricePoint]) -> dict[str, Any]:
    meta = run.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    token_id = str(meta.get("token_id") or "").strip() or None
    source = normalize_price_source(run.get("price_source"))
    first_x = int(points[0].x_value) if points else None
    last_x = int(points[-1].x_value) if points else None
    if source == "frontend":
        access_path = "token_id+timestamp_range" if token_id else "market_slug+token_side+timestamp_range"
        index_hint = "market_token_frontend_price_1m_pkey" if token_id else "idx_quant_frontend_slug_side_time"
        source_table = "quant.market_token_frontend_price_1m"
    else:
        access_path = "token_id+block_number_range" if token_id else "market_slug+token_side+block_number_range"
        index_hint = "market_token_block_close_pkey" if token_id else "idx_quant_block_close_slug_side_block"
        source_table = "quant.market_token_block_close"
    return {
        "query_guard_version": PRICE_QUERY_GUARD_VERSION,
        "source_table": source_table,
        "access_path": access_path,
        "index_hint": index_hint,
        "token_id": token_id,
        "market_slug": run.get("market_slug"),
        "token_side": run.get("token_side"),
        "requested_from": run.get("from_block") if source == "orderfilled_block_close" else run.get("from_ts"),
        "requested_to": run.get("to_block") if source == "orderfilled_block_close" else run.get("to_ts"),
        "actual_first_x": first_x,
        "actual_last_x": last_x,
        "row_count": len(points),
        "policy": DATA_ACCESS_POLICY["backtest_price_read"],
    }


def load_clob_execution_snapshots(conn: Any, run: dict[str, Any]) -> list[BookSnapshot]:
    meta = run.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    token_id = str(meta.get("token_id") or "").strip()
    if not token_id:
        return []
    side = str(run.get("token_side") or "").strip().upper()
    values: list[Any] = [token_id]
    side_filter = ""
    if side in {"YES", "NO"}:
        side_filter = "AND side = %s"
        values.append(side)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                snapshot_id, token_id, side, source, book_status,
                block_number, snapshot_timestamp, snapshot_version,
                best_bid, best_ask, spread, mid,
                bid_depth, ask_depth, depth_total,
                level_count_bid, level_count_ask, payload, fetched_at, created_at
            FROM quant.clob_orderbook_snapshots
            WHERE token_id = %s
              {side_filter}
              AND book_status = 'ok'
              AND (level_count_bid > 0 OR level_count_ask > 0)
            ORDER BY COALESCE(snapshot_timestamp, fetched_at) ASC, block_number ASC NULLS LAST, snapshot_id ASC
            LIMIT 5000
            """,
            tuple(values),
        )
        rows = cur.fetchall()
    return [parse_book_snapshot(dict(row)) for row in rows]


def _public_clob_execution_context(snapshots: list[BookSnapshot]) -> dict[str, Any]:
    if not snapshots:
        return {"source": "clob_orderbook_snapshots", "snapshot_count": 0, "snapshot_version": None, "warning": "no historical CLOB snapshots loaded"}
    first = snapshots[0]
    last = snapshots[-1]
    version_payload = "|".join(snapshot.snapshot_version for snapshot in snapshots if snapshot.snapshot_version)
    snapshot_version = hashlib.sha256(version_payload.encode("ascii")).hexdigest()[:20] if version_payload else None
    return {
        "source": "clob_orderbook_snapshots",
        "snapshot_count": len(snapshots),
        "snapshot_version": snapshot_version,
        "first_snapshot_id": first.snapshot_id,
        "last_snapshot_id": last.snapshot_id,
        "first_timestamp": first.timestamp.isoformat() if first.timestamp else None,
        "last_timestamp": last.timestamp.isoformat() if last.timestamp else None,
        "first_block": first.block_number,
        "last_block": last.block_number,
        "latest_best_bid": _decimal_text(last.best_bid) if last.best_bid is not None else None,
        "latest_best_ask": _decimal_text(last.best_ask) if last.best_ask is not None else None,
        "latest_spread": _decimal_text(last.spread) if last.spread is not None else None,
        "latest_ask_depth": _decimal_text(last.ask_depth),
        "latest_bid_depth": _decimal_text(last.bid_depth),
    }


def simulate_strategy(points: list[PricePoint], run: dict[str, Any], params: BacktestParameters) -> dict[str, Any]:
    if _is_limit_replay_mode(params):
        return simulate_limit_replay_strategy(points, run, params)

    x_axis = "timestamp" if run["price_source"] == "frontend" else "block_number"
    equity = params.initial_capital
    peak = params.initial_capital
    open_position: OpenPosition | None = None
    trades: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    order_index = 0
    profile = effective_execution_profile(params)

    for index, point in enumerate(points):
        if open_position is None and point.price >= params.entry_threshold:
            order_index += 1
            order_id = next_order_id(order_index)
            fill = _fill_decision(params, point, run, "BUY_YES")
            trade_id = f"T-{len(trades) + 1:04d}"
            orders.append(order_from_fill(
                order_id=order_id,
                signal_index=index + 1,
                x_axis=x_axis,
                x_value=point.x_value,
                side="BUY_YES",
                role=profile.order_role,
                order_type="market_like_limit",
                decision_price=point.price,
                fill=fill,
                trade_id=trade_id if fill["size"] > 0 else None,
                latency_seconds=params.latency_seconds,
                latency_blocks=profile.latency_blocks,
            ))
            if fill["size"] <= 0:
                events.append(_event(
                    "fill_rejected",
                    x_axis,
                    point.x_value,
                    None,
                    point.price,
                    "entry signal rejected by minimum fill or liquidity constraints",
                    meta=fill,
                ))
            else:
                open_position = OpenPosition(
                    trade_index=len(trades) + 1,
                    entry_index=index,
                    entry_x=point.x_value,
                    entry_price=fill.get("entry_price") or _execution_price(point.price, params, "entry"),
                    size=fill["size"],
                    requested_notional=fill["requested_notional"],
                    filled_notional=fill["filled_notional"],
                    fill_pct=fill["fill_pct"],
                    fill_status=fill.get("fill_status", "FILLED"),
                    book_snapshot_id=fill.get("book_snapshot_id"),
                    snapshot_version=fill.get("snapshot_version"),
                    staleness_seconds=fill.get("staleness_seconds"),
                    staleness_blocks=fill.get("staleness_blocks"),
                    avg_fill_price=fill.get("avg_fill_price"),
                    fill_probability=fill.get("fill_probability", Decimal("0")),
                    block_volume=fill.get("block_volume", point.volume),
                    trade_count=int(fill.get("trade_count", point.trade_count) or 0),
                    available_notional=fill.get("available_notional", Decimal("0")),
                    entry_order_id=order_id,
                    entry_fee_cost=Decimal(str(fill.get("fee_cost") or 0)),
                    entry_slippage_cost=Decimal(str(fill.get("slippage_cost") or 0)),
                )
                events.append(_event(
                    "open",
                    x_axis,
                    point.x_value,
                    f"T-{open_position.trade_index:04d}",
                    point.price,
                    "entry threshold reached",
                    meta=fill,
                ))
        elif open_position is not None:
            exit_reason = _exit_reason(point.price, open_position.entry_price, index - open_position.entry_index, params)
            if exit_reason:
                order_index += 1
                order_id = next_order_id(order_index)
                exit_fill = _fill_decision(params, point, run, "SELL_YES", target_size=open_position.size)
                trade_id = f"T-{open_position.trade_index:04d}"
                orders.append(order_from_fill(
                    order_id=order_id,
                    signal_index=index + 1,
                    x_axis=x_axis,
                    x_value=point.x_value,
                    side="SELL_YES",
                    role=profile.order_role,
                    order_type="market_like_limit",
                    decision_price=point.price,
                    fill=exit_fill,
                    trade_id=trade_id if exit_fill["size"] > 0 else trade_id,
                    latency_seconds=params.latency_seconds,
                    latency_blocks=profile.latency_blocks,
                ))
                if exit_fill["size"] <= 0:
                    events.append(_event(
                        "exit_rejected",
                        x_axis,
                        point.x_value,
                        f"T-{open_position.trade_index:04d}",
                        point.price,
                        exit_reason,
                        meta=exit_fill,
                    ))
                    continue
                trade = _close_trade(run, x_axis, open_position, point, index, exit_reason, params, exit_fill=exit_fill, exit_order_id=order_id)
                trades.append(trade)
                equity += trade["pnl"]
                events.append(_event("close", x_axis, point.x_value, trade["trade_id"], point.price, exit_reason))
                remaining_size = (open_position.size - exit_fill["size"]).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
                open_position.size = remaining_size
                if remaining_size <= 0:
                    open_position = None
                else:
                    open_position.trade_index = len(trades) + 1

        mark_equity = equity
        if open_position is not None:
            mark_equity += (_execution_price(point.price, params, "exit") - open_position.entry_price) * open_position.size
        peak = max(peak, mark_equity)
        drawdown = mark_equity - peak
        equity_rows.append(
            {
                "point_index": index + 1,
                "x_axis": x_axis,
                "x_value": point.x_value,
                "equity": mark_equity,
                "drawdown": drawdown,
                "drawdown_pct": _pct(drawdown, peak),
                "cumulative_return": _pct(mark_equity - params.initial_capital, params.initial_capital),
            }
        )

    if open_position is not None:
        last = points[-1]
        order_index += 1
        order_id = next_order_id(order_index)
        if params.final_valuation_mode == "FORCE_CLOSE":
            exit_fill = _force_close_fill(params, last, open_position.size)
        else:
            exit_fill = _fill_decision(params, last, run, "SELL_YES", target_size=open_position.size)
        trade_id = f"T-{open_position.trade_index:04d}"
        orders.append(order_from_fill(
            order_id=order_id,
            signal_index=len(points),
            x_axis=x_axis,
            x_value=last.x_value,
            side="SELL_YES",
            role=profile.order_role,
            order_type="force_close_limit",
            decision_price=last.price,
            fill=exit_fill,
            trade_id=trade_id if exit_fill["size"] > 0 else trade_id,
            latency_seconds=params.latency_seconds,
            latency_blocks=profile.latency_blocks,
        ))
        if exit_fill["size"] > 0:
            trade = _close_trade(run, x_axis, open_position, last, len(points) - 1, "end_of_data", params, exit_fill=exit_fill, exit_order_id=order_id)
            trades.append(trade)
            equity += trade["pnl"]
            events.append(_event("close", x_axis, last.x_value, trade["trade_id"], last.price, "end_of_data"))
        else:
            events.append(_event("force_close_rejected", x_axis, last.x_value, f"T-{open_position.trade_index:04d}", last.price, "end_of_data", meta=exit_fill))

    ledger_rows = build_ledger_rows(trades, params.initial_capital)
    metrics = build_metrics(trades, equity_rows, points, params, orders=orders, ledger_rows=ledger_rows)
    return {"trades": trades, "equity": equity_rows, "metrics": metrics, "events": events, "orders": orders, "ledger": ledger_rows}


def simulate_limit_replay_strategy(points: list[PricePoint], run: dict[str, Any], params: BacktestParameters) -> dict[str, Any]:
    """Replay a small passive limit-order strategy against historical OrderFilled prices.

    This mode intentionally does not treat `price >= threshold` as a fillable
    opportunity. A buy only fills after a later historical trade/block close is
    at or below the buy limit; a sell only fills after a later price is at or
    above the sell limit. If the sell never crosses, the position is held to
    settlement instead of being force-closed at the final traded price.
    """

    x_axis = "timestamp" if run["price_source"] == "frontend" else "block_number"
    equity = params.initial_capital
    peak = params.initial_capital
    orders: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    order_index = 0
    buy_limit = _buy_limit_price(params)
    first = points[0]
    open_position: OpenPosition | None = None
    buy_order_emitted = False
    sell_order_emitted = False
    sell_order_signal_x: int | None = None
    sell_limit: Decimal | None = None

    for index, point in enumerate(points):
        if open_position is None and not buy_order_emitted and _after_latency(first, point, params, x_axis):
            if point.price <= buy_limit:
                order_index += 1
                order_id = next_order_id(order_index)
                fill = _limit_replay_fill(
                    params,
                    point,
                    "BUY_YES",
                    limit_price=buy_limit,
                    decision_x=first.x_value,
                )
                trade_id = f"T-{len(trades) + 1:04d}" if not fill.get("rejected") else None
                orders.append(order_from_fill(
                    order_id=order_id,
                    signal_index=1,
                    x_axis=x_axis,
                    x_value=first.x_value,
                    side="BUY_YES",
                    role="maker",
                    order_type="resting_limit",
                    decision_price=buy_limit,
                    fill=fill,
                    trade_id=trade_id,
                    latency_seconds=params.latency_seconds,
                    latency_blocks=params.latency_blocks,
                ))
                buy_order_emitted = True
                if fill.get("rejected") or Decimal(str(fill.get("size") or 0)) <= 0:
                    events.append(_event("buy_no_fill", x_axis, point.x_value, None, point.price, "buy limit crossed but volume cap prevented fill", meta=fill))
                    continue
                open_position = OpenPosition(
                    trade_index=len(trades) + 1,
                    entry_index=index,
                    entry_x=point.x_value,
                    entry_price=fill.get("entry_price") or buy_limit,
                    size=fill["size"],
                    requested_notional=fill["requested_notional"],
                    filled_notional=fill["filled_notional"],
                    fill_pct=fill["fill_pct"],
                    fill_status=fill.get("fill_status", "FILLED"),
                    fill_probability=fill.get("fill_probability", Decimal("100")),
                    block_volume=fill.get("block_volume", point.volume),
                    trade_count=int(fill.get("trade_count", point.trade_count) or 0),
                    available_notional=fill.get("available_notional", fill["filled_notional"]),
                    entry_order_id=order_id,
                    entry_fee_cost=Decimal(str(fill.get("fee_cost") or 0)),
                    entry_slippage_cost=Decimal(str(fill.get("slippage_cost") or 0)),
                )
                sell_order_signal_x = point.x_value
                sell_limit = _sell_limit_price(params, open_position.entry_price)
                events.append(_event("open", x_axis, point.x_value, trade_id, point.price, "buy limit crossed", meta=fill))

        elif open_position is not None and not sell_order_emitted:
            if sell_order_signal_x is None:
                sell_order_signal_x = open_position.entry_x
            if sell_limit is None:
                sell_limit = _sell_limit_price(params, open_position.entry_price)
            if _after_latency_value(sell_order_signal_x, point, params, x_axis) and _sell_limit_can_fill(point.price, sell_limit):
                order_index += 1
                order_id = next_order_id(order_index)
                exit_fill = _limit_replay_fill(
                    params,
                    point,
                    "SELL_YES",
                    limit_price=sell_limit,
                    decision_x=sell_order_signal_x,
                    target_size=open_position.size,
                )
                trade_id = f"T-{open_position.trade_index:04d}"
                orders.append(order_from_fill(
                    order_id=order_id,
                    signal_index=index + 1,
                    x_axis=x_axis,
                    x_value=sell_order_signal_x,
                    side="SELL_YES",
                    role="maker",
                    order_type="resting_limit",
                    decision_price=sell_limit,
                    fill=exit_fill,
                    trade_id=trade_id,
                    latency_seconds=params.latency_seconds,
                    latency_blocks=params.latency_blocks,
                ))
                sell_order_emitted = True
                if exit_fill.get("rejected") or Decimal(str(exit_fill.get("size") or 0)) <= 0:
                    events.append(_event("sell_no_fill", x_axis, point.x_value, trade_id, point.price, "sell limit crossed but volume cap prevented fill", meta=exit_fill))
                    continue
                trade = _close_trade(run, x_axis, open_position, point, index, "limit_exit", params, exit_fill=exit_fill, exit_order_id=order_id)
                trades.append(trade)
                equity += trade["pnl"]
                events.append(_event("close", x_axis, point.x_value, trade_id, point.price, "sell limit crossed", meta=exit_fill))
                open_position = None

        mark_equity = equity
        if open_position is not None:
            mark_equity += (point.price - open_position.entry_price) * open_position.size
        peak = max(peak, mark_equity)
        drawdown = mark_equity - peak
        equity_rows.append(
            {
                "point_index": index + 1,
                "x_axis": x_axis,
                "x_value": point.x_value,
                "equity": mark_equity,
                "drawdown": drawdown,
                "drawdown_pct": _pct(drawdown, peak),
                "cumulative_return": _pct(mark_equity - params.initial_capital, params.initial_capital),
            }
        )

    if not buy_order_emitted:
        order_index += 1
        orders.append(order_from_fill(
            order_id=next_order_id(order_index),
            signal_index=1,
            x_axis=x_axis,
            x_value=first.x_value,
            side="BUY_YES",
            role="maker",
            order_type="resting_limit",
            decision_price=buy_limit,
            fill=_limit_replay_no_fill(params, first, "BUY_YES", limit_price=buy_limit, note="buy_limit_not_crossed"),
            latency_seconds=params.latency_seconds,
            latency_blocks=params.latency_blocks,
        ))
        events.append(_event("buy_no_fill", x_axis, first.x_value, None, first.price, "buy limit never crossed"))

    if open_position is not None:
        assert sell_order_signal_x is not None
        assert sell_limit is not None
        if not sell_order_emitted:
            order_index += 1
            orders.append(order_from_fill(
                order_id=next_order_id(order_index),
                signal_index=open_position.entry_index + 1,
                x_axis=x_axis,
                x_value=sell_order_signal_x,
                side="SELL_YES",
                role="maker",
                order_type="resting_limit",
                decision_price=sell_limit,
                fill=_limit_replay_no_fill(
                    params,
                    points[-1],
                    "SELL_YES",
                    limit_price=sell_limit,
                    note="sell_limit_not_crossed" if sell_limit < Decimal("0.98") else "terminal_price_limit_not_fillable",
                    target_size=open_position.size,
                ),
                trade_id=f"T-{open_position.trade_index:04d}",
                latency_seconds=params.latency_seconds,
                latency_blocks=params.latency_blocks,
            ))
        settlement_value = _settlement_value(params, points)
        if settlement_value is not None:
            last = points[-1]
            order_index += 1
            order_id = next_order_id(order_index)
            settlement_fill = _settlement_fill(params, last, open_position.size, settlement_value)
            trade_id = f"T-{open_position.trade_index:04d}"
            orders.append(order_from_fill(
                order_id=order_id,
                signal_index=len(points),
                x_axis=x_axis,
                x_value=last.x_value,
                side="SELL_YES",
                role="settlement",
                order_type="settlement",
                decision_price=settlement_value,
                fill=settlement_fill,
                trade_id=trade_id,
                latency_seconds=Decimal("0"),
                latency_blocks=0,
            ))
            trade = _close_trade(run, x_axis, open_position, last, len(points) - 1, "settlement", params, exit_fill=settlement_fill, exit_order_id=order_id)
            trades.append(trade)
            equity += trade["pnl"]
            events.append(_event("settlement", x_axis, last.x_value, trade_id, settlement_value, "held to settlement payoff", meta=settlement_fill))
        else:
            events.append(_event(
                "unresolved_open",
                x_axis,
                points[-1].x_value,
                f"T-{open_position.trade_index:04d}",
                points[-1].price,
                "sell limit not crossed and settlement value unavailable",
                meta={"settlement_value": None, "position_size": _decimal_text(open_position.size)},
            ))

    ledger_rows = build_ledger_rows(trades, params.initial_capital)
    metrics = build_metrics(trades, equity_rows, points, params, orders=orders, ledger_rows=ledger_rows)
    return {"trades": trades, "equity": equity_rows, "metrics": metrics, "events": events, "orders": orders, "ledger": ledger_rows}


def build_metrics(
    trades: list[dict[str, Any]],
    equity_rows: list[dict[str, Any]],
    points: list[PricePoint],
    params: BacktestParameters,
    *,
    orders: list[dict[str, Any]] | None = None,
    ledger_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    net = sum((trade["pnl"] for trade in trades), Decimal("0"))
    settlement_pnl = sum(
        (trade["pnl"] for trade in trades if str(trade.get("exit_reason") or "").lower() == "settlement"),
        Decimal("0"),
    )
    trade_exit_pnl = net - settlement_pnl
    gross_profit = sum((trade["pnl"] for trade in trades if trade["pnl"] > 0), Decimal("0"))
    gross_loss = sum((trade["pnl"] for trade in trades if trade["pnl"] < 0), Decimal("0"))
    winners = len([trade for trade in trades if trade["pnl"] > 0])
    max_drawdown = min((row["drawdown"] for row in equity_rows), default=Decimal("0"))
    avg_trade = net / Decimal(max(1, len(trades)))
    avg_holding = sum((trade["holding_bars"] for trade in trades), 0) / max(1, len(trades))
    total_return = _pct(net, params.initial_capital)
    profit_factor = gross_profit / abs(gross_loss) if gross_loss else Decimal("0")
    fill_coverage = Decimal(len(points)) / Decimal(max(1, len(points))) * Decimal("100")
    stale_ratio = Decimal("0")
    execution_cost = sum((trade.get("execution_cost", Decimal("0")) for trade in trades), Decimal("0"))
    filled_notional = sum((Decimal(str(trade.get("filled_notional") or trade.get("notional") or 0)) for trade in trades), Decimal("0"))
    requested_notional = sum((Decimal(str(trade.get("requested_notional") or trade.get("notional") or 0)) for trade in trades), Decimal("0"))
    liquidity_fill_rate = _pct(filled_notional, requested_notional) if requested_notional else Decimal("0")
    capped_trades = len([
        trade for trade in trades
        if Decimal(str(trade.get("filled_notional") or trade.get("notional") or 0))
        < Decimal(str(trade.get("requested_notional") or params.position_size)) * Decimal("0.999")
    ])
    partial_trades = len([trade for trade in trades if str(trade.get("fill_status") or "").upper() == "PARTIAL"])
    snapshot_trades = len([trade for trade in trades if trade.get("book_snapshot_id") is not None])
    orderfilled_trades = len([trade for trade in trades if str(trade.get("execution_source") or "").lower() == "orderfilled_volume"])
    fill_probability_values = [
        Decimal(str(trade.get("fill_probability") or 0))
        for trade in trades
        if trade.get("fill_probability") is not None
    ]
    avg_fill_probability = sum(fill_probability_values, Decimal("0")) / Decimal(max(1, len(fill_probability_values)))
    stale_trade_count = len([
        trade for trade in trades
        if trade.get("staleness_seconds") is not None
        and Decimal(str(trade.get("staleness_seconds") or 0)) > Decimal(str(params.max_book_staleness_seconds))
    ])
    avg_staleness_values = [
        Decimal(str(trade.get("staleness_seconds") or 0))
        for trade in trades
        if trade.get("staleness_seconds") is not None
    ]
    avg_book_staleness = sum(avg_staleness_values, Decimal("0")) / Decimal(max(1, len(avg_staleness_values)))
    avg_notional = filled_notional / Decimal(max(1, len(trades)))
    order_counts = summarize_orders(orders or [])
    account = ledger_summary(ledger_rows or [], params.initial_capital)
    resolved_pnl = settlement_pnl
    unrealized_pnl = net - resolved_pnl
    mode = str(params.execution_price_mode or "ORDERFILLED").upper()
    if mode == "DEPTH":
        mode_delta = "depth replay"
        snapshot_value = _ratio(snapshot_trades, len(trades)) * Decimal("100")
        snapshot_formatted = f"{snapshot_value:.1f}%"
        snapshot_delta = f"{snapshot_trades} / {len(trades)} trades"
        snapshot_status = "positive" if snapshot_trades == len(trades) and trades else "negative" if trades else "neutral"
        snapshot_tooltip = "Closed trades linked to persisted historical CLOB snapshots"
        stale_status = "negative" if stale_trade_count else "positive"
    elif mode == "ORDERFILLED":
        mode_delta = "historical fills"
        snapshot_value = Decimal("0")
        snapshot_formatted = "not required"
        snapshot_delta = f"{orderfilled_trades} orderfilled trades"
        snapshot_status = "neutral"
        snapshot_tooltip = "OrderFilled execution uses historical traded volume and participation caps; CLOB snapshots are optional, not required"
        stale_status = "neutral"
    elif mode in {"ORDERFILLED_LIMIT_REPLAY", "LIMIT_REPLAY"}:
        mode_delta = "limit replay"
        snapshot_value = Decimal("0")
        snapshot_formatted = "not required"
        snapshot_delta = "crossing price required"
        snapshot_status = "neutral"
        snapshot_tooltip = "OrderFilled limit replay only fills BUY when trade price <= limit and SELL when trade price >= limit; residual positions settle at 0/1 when known"
        stale_status = "neutral"
    else:
        mode_delta = "legacy"
        snapshot_value = Decimal("0")
        snapshot_formatted = "not required"
        snapshot_delta = "legacy mode"
        snapshot_status = "neutral"
        snapshot_tooltip = "Legacy execution does not require persisted CLOB snapshots"
        stale_status = "neutral"
    rows = [
        ("net_profit", "Net Profit", "overview", net, _money(net), _percent(total_return), _status(net), "Closed realized strategy PnL"),
        ("total_return", "Total Return", "overview", total_return, _percent(total_return), "capital", _status(total_return), "Return on initial capital"),
        ("max_drawdown", "Max Drawdown", "overview", max_drawdown, _money(max_drawdown), _percent(_pct(max_drawdown, params.initial_capital)), "negative", "Largest peak-to-trough equity loss"),
        ("win_rate", "Win Rate", "overview", _ratio(winners, len(trades)) * Decimal("100"), f"{_ratio(winners, len(trades)) * Decimal('100'):.2f}%", f"{winners} / {len(trades)}", "positive" if winners else "neutral", "Percent profitable closed trades"),
        ("profit_factor", "Profit Factor", "overview", profit_factor, f"{profit_factor:.3f}", "gross P/L", "neutral", "Gross profit divided by gross loss"),
        ("total_trades", "Total Trades", "overview", Decimal(len(trades)), str(len(trades)), "closed", "neutral", "Closed strategy trades"),
        ("signal_count", "Signals", "overview", Decimal(order_counts["signal_count"]), str(order_counts["signal_count"]), "order intents", "neutral", "Entry and exit order intents generated by strategy signals"),
        ("submitted_orders", "Submitted Orders", "overview", Decimal(order_counts["submitted_count"]), str(order_counts["submitted_count"]), "lifecycle", "neutral", "Orders submitted to the simulated execution model"),
        ("no_fill_orders", "No Fill Orders", "overview", Decimal(order_counts["no_fill_count"]), str(order_counts["no_fill_count"]), "missed fills", "negative" if order_counts["no_fill_count"] else "positive", "Orders that saw a signal but did not receive executable historical flow"),
        ("avg_trade", "Avg Trade", "overview", avg_trade, _money(avg_trade), _percent(_pct(avg_trade, params.initial_capital)), _status(avg_trade), "Average closed trade PnL"),
        ("avg_holding", "Avg Holding", "overview", Decimal(str(avg_holding)), f"{avg_holding:.1f} bars", "bars", "neutral", "Average bars held per trade"),
        ("resolved_pnl", "Resolved PnL", "prediction", resolved_pnl, _money(resolved_pnl), "settled", _status(resolved_pnl), "PnL from resolved markets"),
        ("unrealized_pnl", "Unrealized PnL", "prediction", unrealized_pnl, _money(unrealized_pnl), "pending", _status(unrealized_pnl), "Mark-to-market PnL for unresolved exposure"),
        ("settlement_pnl", "Settlement PnL", "prediction", settlement_pnl, _money(settlement_pnl), "resolution payoff", _status(settlement_pnl), "PnL attributable to final payoff"),
        ("trade_exit_pnl", "Trade Exit PnL", "prediction", trade_exit_pnl, _money(trade_exit_pnl), "matched exits", _status(trade_exit_pnl), "PnL from exits that crossed a real historical sell limit"),
        ("slippage_cost", "Execution Cost", "prediction", -execution_cost, _money(-execution_cost), f"{params.fee_bps} fee bps / {params.slippage_bps} slip bps", "negative" if execution_cost else "neutral", "Modeled fees plus entry/exit slippage"),
        ("ledger_cash_balance", "Ledger Cash", "prediction", account["cash_balance"], _money(account["cash_balance"]), "cash after fills", "neutral", "Cash balance reconstructed from BUY/SELL cashflow ledger"),
        ("ledger_realized_pnl", "Ledger Realized PnL", "prediction", account["realized_pnl"], _money(account["realized_pnl"]), f"{int(account['ledger_rows'])} ledger rows", _status(account["realized_pnl"]), "Realized PnL sourced from ledger cashflows"),
        ("ledger_fee_total", "Ledger Fees", "prediction", -account["fee_total"], _money(-account["fee_total"]), "fee attribution", "negative" if account["fee_total"] else "neutral", "Total simulated fees recorded in the ledger"),
        ("execution_profile", "Execution Profile", "prediction", Decimal("0"), str(params.execution_profile), f"{params.order_role} role", "neutral", "Execution assumption profile controlling fill probability haircut, latency, and adverse slippage"),
        ("liquidity_fill_rate", "Liquidity Fill", "prediction", liquidity_fill_rate, f"{liquidity_fill_rate:.1f}%", f"{_money(filled_notional)} filled", "positive" if liquidity_fill_rate >= Decimal("99") else "negative" if capped_trades else "neutral", "Share of requested USDC notional actually filled after position, liquidity, and min-fill constraints"),
        ("capped_trades", "Capped Trades", "prediction", Decimal(capped_trades), str(capped_trades), f"{_money(avg_notional)} avg fill", "negative" if capped_trades else "positive", "Trades whose filled notional was reduced by volume/liquidity constraints"),
        ("min_fill_pct", "Min Fill", "prediction", params.min_fill_pct, f"{params.min_fill_pct:.1f}%", "entry gate", "neutral", "Entry signals below this fill percentage are rejected instead of partially filled"),
        ("max_position_notional", "Max Position", "prediction", params.max_position_notional, _money(params.max_position_notional) if params.max_position_notional > 0 else "off", "per trade", "neutral", "Maximum requested USDC notional per open position"),
        ("execution_mode", "Execution Mode", "prediction", Decimal("0"), mode, mode_delta, "neutral", "Fill model selected for this run"),
        ("avg_fill_probability", "Avg Fill Probability", "prediction", avg_fill_probability, f"{avg_fill_probability:.1f}%", "orderfilled participation" if mode == "ORDERFILLED" else "fills", "neutral", "Deterministic expected fill probability from the execution model, not a random draw"),
        ("snapshot_fill_coverage", "Snapshot Fill Coverage", "prediction", snapshot_value, snapshot_formatted, snapshot_delta, snapshot_status, snapshot_tooltip),
        ("partial_fill_count", "Partial Fills", "prediction", Decimal(partial_trades), str(partial_trades), "liquidity constrained", "negative" if partial_trades else "positive", "Closed trades where the requested order could only partially fill"),
        ("stale_book_trade_count", "Stale Book Trades", "prediction", Decimal(stale_trade_count), str(stale_trade_count), f"limit {params.max_book_staleness_seconds}s", stale_status, "Closed trades whose book staleness exceeded the configured limit"),
        ("avg_book_staleness", "Avg Book Staleness", "prediction", avg_book_staleness, f"{avg_book_staleness:.1f}s", "execution snapshots", "neutral", "Average age of the book snapshots used by closed trades"),
        ("fill_coverage", "Fill Coverage", "prediction", fill_coverage, f"{fill_coverage:.1f}%", "price rows", "positive", "Usable fill price coverage"),
        ("stale_price_ratio", "Stale Price Ratio", "prediction", stale_ratio, f"{stale_ratio:.2f}%", "exact rows", "neutral", "Share of stale/forward-filled prices"),
    ]
    return [
        {
            "metric_key": key,
            "metric_name": name,
            "metric_group": group,
            "value": value,
            "formatted_value": formatted,
            "delta": delta,
            "status": status,
            "tooltip": tooltip,
            "sort_order": index,
        }
        for index, (key, name, group, value, formatted, delta, status, tooltip) in enumerate(rows, start=1)
    ]


def build_data_quality_report(points: list[PricePoint], run: dict[str, Any]) -> dict[str, Any]:
    x_values = [int(point.x_value) for point in points]
    prices = [point.price for point in points]
    deltas = [x_values[index] - x_values[index - 1] for index in range(1, len(x_values)) if x_values[index] > x_values[index - 1]]
    sorted_deltas = sorted(deltas)
    median_delta = sorted_deltas[len(sorted_deltas) // 2] if sorted_deltas else 0
    gap_threshold = int(median_delta * 4) if median_delta else 0
    gaps = [
        {"from_x": x_values[index - 1], "to_x": x_values[index], "span": x_values[index] - x_values[index - 1]}
        for index in range(1, len(x_values))
        if gap_threshold and x_values[index] - x_values[index - 1] > gap_threshold
    ]
    jumps = [
        {
            "x": x_values[index],
            "from_price": _decimal_text(prices[index - 1]),
            "to_price": _decimal_text(prices[index]),
            "delta": _decimal_text((prices[index] - prices[index - 1]).copy_abs()),
        }
        for index in range(1, len(prices))
        if (prices[index] - prices[index - 1]).copy_abs() > Decimal("0.18")
    ]
    requested_from = run.get("from_block") if run.get("price_source") == "orderfilled_block_close" else run.get("from_ts")
    requested_to = run.get("to_block") if run.get("price_source") == "orderfilled_block_close" else run.get("to_ts")
    first_x = x_values[0] if x_values else None
    last_x = x_values[-1] if x_values else None
    requested_span = int(requested_to - requested_from) if requested_from is not None and requested_to is not None and requested_to > requested_from else None
    observed_span = int(last_x - first_x) if first_x is not None and last_x is not None and last_x >= first_x else None
    span_coverage = Decimal(str(observed_span or 0)) / Decimal(str(requested_span)) if requested_span else Decimal("1")
    status = "ready"
    caveats: list[str] = []
    if gaps:
        status = "review"
        caveats.append(f"{len(gaps)} large x-axis gaps")
    if jumps:
        status = "review"
        caveats.append(f"{len(jumps)} price jumps over 18 percentage points")
    if len(points) < 50:
        status = "review"
        caveats.append("fewer than 50 rows")
    if span_coverage < Decimal("0.75"):
        status = "review"
        caveats.append("observed span covers less than 75% of requested range")
    warning_level = "OK"
    if caveats:
        warning_level = "WARN"
    if len(points) < 10 or span_coverage < Decimal("0.50"):
        warning_level = "BAD"
    data_version = _points_data_version(points, run)
    data_access = price_access_report(run, points)
    return {
        "status": status,
        "warning_level": warning_level,
        "price_source": run.get("price_source"),
        "x_axis": "block_number" if run.get("price_source") == "orderfilled_block_close" else "timestamp",
        "data_version": data_version,
        "data_access": {**data_access, "data_version": data_version},
        "source_table": data_access["source_table"],
        "access_path": data_access["access_path"],
        "index_hint": data_access["index_hint"],
        "query_guard_version": data_access["query_guard_version"],
        "checksum": data_version,
        "version_basis": "x_value:price:volume:trade_count",
        "rows": len(points),
        "first_x": first_x,
        "last_x": last_x,
        "median_delta": median_delta,
        "gap_count": len(gaps),
        "gap_threshold": gap_threshold,
        "largest_gaps": gaps[:8],
        "jump_count": len(jumps),
        "largest_jumps": jumps[:8],
        "requested_from": requested_from,
        "requested_to": requested_to,
        "observed_span": observed_span,
        "requested_span": requested_span,
        "span_coverage_pct": _decimal_text((span_coverage * Decimal("100")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        "caveats": caveats,
    }


def _points_data_version(points: list[PricePoint], run: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(str(run.get("market_slug") or "").encode("utf-8"))
    digest.update(b"|")
    digest.update(str(run.get("token_side") or "").encode("utf-8"))
    digest.update(b"|")
    digest.update(str(run.get("price_source") or "").encode("utf-8"))
    for point in points:
        digest.update(str(point.x_value).encode("ascii"))
        digest.update(b":")
        digest.update(_decimal_text(point.price).encode("ascii"))
        digest.update(b":")
        digest.update(_decimal_text(point.volume).encode("ascii"))
        digest.update(b":")
        digest.update(str(point.trade_count).encode("ascii"))
        digest.update(b";")
    return digest.hexdigest()[:20]


def data_quality_metrics(report: dict[str, Any]) -> list[dict[str, Any]]:
    status = "positive" if report.get("status") == "ready" else "negative"
    return [
        {
            "metric_key": "data_quality_status",
            "metric_name": "Data Quality",
            "metric_group": "prediction",
            "value": Decimal("1") if report.get("status") == "ready" else Decimal("0"),
            "formatted_value": str(report.get("warning_level") or report.get("status") or "unknown"),
            "delta": f"{report.get('rows', 0)} rows",
            "status": status,
            "tooltip": "; ".join(report.get("caveats") or []) or "No large gaps or jumps detected in the executed price rows",
            "sort_order": 90,
        },
        {
            "metric_key": "gap_count",
            "metric_name": "Gap Count",
            "metric_group": "prediction",
            "value": Decimal(int(report.get("gap_count") or 0)),
            "formatted_value": str(report.get("gap_count") or 0),
            "delta": f"threshold {report.get('gap_threshold') or 0}",
            "status": "negative" if report.get("gap_count") else "positive",
            "tooltip": "Large x-axis gaps detected in the rows used by this backtest",
            "sort_order": 91,
        },
        {
            "metric_key": "jump_count",
            "metric_name": "Jump Count",
            "metric_group": "prediction",
            "value": Decimal(int(report.get("jump_count") or 0)),
            "formatted_value": str(report.get("jump_count") or 0),
            "delta": ">18 pct points",
            "status": "negative" if report.get("jump_count") else "positive",
            "tooltip": "Large adjacent price jumps detected in the rows used by this backtest",
            "sort_order": 92,
        },
        {
            "metric_key": "data_version",
            "metric_name": "Data Version",
            "metric_group": "prediction",
            "value": Decimal("0"),
            "formatted_value": str(report.get("data_version") or "-"),
            "delta": str(report.get("version_basis") or "price rows"),
            "status": "neutral",
            "tooltip": "Stable checksum of the exact x/price/volume rows used by this backtest run",
            "sort_order": 93,
        },
        {
            "metric_key": "data_access_path",
            "metric_name": "Data Access Path",
            "metric_group": "system",
            "value": Decimal("0"),
            "formatted_value": str(report.get("access_path") or "-"),
            "delta": str(report.get("source_table") or "-"),
            "status": "neutral",
            "tooltip": f"Index hint: {report.get('index_hint') or '-'}; guard: {report.get('query_guard_version') or '-'}",
            "sort_order": 94,
        },
        {
            "metric_key": "span_coverage",
            "metric_name": "Span Coverage",
            "metric_group": "prediction",
            "value": Decimal(str(report.get("span_coverage_pct") or "0")),
            "formatted_value": f"{report.get('span_coverage_pct') or '0'}%",
            "delta": f"{report.get('first_x') or '-'} -> {report.get('last_x') or '-'}",
            "status": "positive" if Decimal(str(report.get("span_coverage_pct") or "0")) >= Decimal("75") else "negative",
            "tooltip": "Observed row span compared with the requested backtest range",
            "sort_order": 95,
        },
    ]


def replace_backtest_results(conn: Any, run_id: int, result: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM quant.quant_backtest_metrics WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM quant.quant_backtest_equity WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM quant.quant_backtest_trades WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM quant.quant_backtest_orders WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM quant.quant_backtest_ledger WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM quant.quant_backtest_events WHERE run_id = %s", (run_id,))
        cur.executemany(
            """
            INSERT INTO quant.quant_backtest_metrics (
                run_id, metric_key, metric_name, metric_group, value,
                formatted_value, delta, status, tooltip, sort_order
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    run_id,
                    row["metric_key"],
                    row["metric_name"],
                    row["metric_group"],
                    row["value"],
                    row["formatted_value"],
                    row["delta"],
                    row["status"],
                    row["tooltip"],
                    row["sort_order"],
                )
                for row in result["metrics"]
            ],
        )
        cur.executemany(
            """
            INSERT INTO quant.quant_backtest_equity (
                run_id, point_index, x_axis, x_value, equity,
                drawdown, drawdown_pct, cumulative_return
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    run_id,
                    row["point_index"],
                    row["x_axis"],
                    row["x_value"],
                    row["equity"],
                    row["drawdown"],
                    row["drawdown_pct"],
                    row["cumulative_return"],
                )
                for row in result["equity"]
            ],
        )
        cur.executemany(
            """
            INSERT INTO quant.quant_backtest_trades (
                run_id, trade_id, entry_order_id, exit_order_id,
                market_slug, token_side, side, x_axis,
                entry_x, exit_x, entry_price, exit_price, size, notional,
                requested_notional, filled_notional, fill_pct,
                requested_size, filled_size, unfilled_size, fill_status,
                book_snapshot_id, snapshot_version, staleness_seconds, staleness_blocks,
                avg_fill_price, fill_probability, block_volume, trade_count,
                available_notional, execution_source, fee_cost, slippage_cost, execution_cost,
                pnl, pnl_pct, holding_bars, exit_reason
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    run_id,
                    row["trade_id"],
                    row.get("entry_order_id"),
                    row.get("exit_order_id"),
                    row["market_slug"],
                    row["token_side"],
                    row["side"],
                    row["x_axis"],
                    row["entry_x"],
                    row["exit_x"],
                    row["entry_price"],
                    row["exit_price"],
                    row["size"],
                    row["notional"],
                    row.get("requested_notional", row["notional"]),
                    row.get("filled_notional", row["notional"]),
                    row.get("fill_pct", Decimal("100")),
                    row.get("requested_size", row.get("size", Decimal("0"))),
                    row.get("filled_size", row.get("size", Decimal("0"))),
                    row.get("unfilled_size", Decimal("0")),
                    row.get("fill_status", "FILLED"),
                    row.get("book_snapshot_id"),
                    row.get("snapshot_version"),
                    row.get("staleness_seconds"),
                    row.get("staleness_blocks"),
                    row.get("avg_fill_price", row.get("exit_price")),
                    row.get("fill_probability", Decimal("0")),
                    row.get("block_volume", Decimal("0")),
                    row.get("trade_count", 0),
                    row.get("available_notional", Decimal("0")),
                    row.get("execution_source", "unknown"),
                    row.get("fee_cost", Decimal("0")),
                    row.get("slippage_cost", Decimal("0")),
                    row.get("execution_cost", Decimal("0")),
                    row["pnl"],
                    row["pnl_pct"],
                    row["holding_bars"],
                    row["exit_reason"],
                )
                for row in result["trades"]
            ],
        )
        cur.executemany(
            """
            INSERT INTO quant.quant_backtest_orders (
                run_id, order_id, signal_index, trade_id, x_axis,
                signal_x, submit_x, decision_price, requested_price, side,
                role, order_type, status, requested_size, requested_notional,
                filled_size, filled_notional, unfilled_size, avg_fill_price,
                fill_probability, fill_pct, block_volume, trade_count,
                available_notional, fee_cost, slippage_cost, execution_cost,
                latency_blocks, latency_seconds, no_fill_reason, execution_source, meta
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            [
                (
                    run_id,
                    row["order_id"],
                    row["signal_index"],
                    row.get("trade_id"),
                    row["x_axis"],
                    row["signal_x"],
                    row["submit_x"],
                    row["decision_price"],
                    row.get("requested_price"),
                    row["side"],
                    row["role"],
                    row["order_type"],
                    row["status"],
                    row["requested_size"],
                    row["requested_notional"],
                    row["filled_size"],
                    row["filled_notional"],
                    row["unfilled_size"],
                    row.get("avg_fill_price"),
                    row["fill_probability"],
                    row["fill_pct"],
                    row["block_volume"],
                    row["trade_count"],
                    row["available_notional"],
                    row["fee_cost"],
                    row["slippage_cost"],
                    row["execution_cost"],
                    row["latency_blocks"],
                    row["latency_seconds"],
                    row.get("no_fill_reason"),
                    row["execution_source"],
                    json.dumps(row.get("meta") or {}, default=str),
                )
                for row in result.get("orders", [])
            ],
        )
        cur.executemany(
            """
            INSERT INTO quant.quant_backtest_ledger (
                run_id, ledger_id, order_id, trade_id, event_type, x_axis,
                x_value, market_slug, token_side, shares_delta, cash_delta,
                fee, rebate, slippage_cost, execution_cost, realized_pnl,
                position_after, cash_after, price, source, meta
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            [
                (
                    run_id,
                    row["ledger_id"],
                    row.get("order_id"),
                    row.get("trade_id"),
                    row["event_type"],
                    row["x_axis"],
                    row["x_value"],
                    row["market_slug"],
                    row["token_side"],
                    row["shares_delta"],
                    row["cash_delta"],
                    row["fee"],
                    row["rebate"],
                    row["slippage_cost"],
                    row["execution_cost"],
                    row["realized_pnl"],
                    row["position_after"],
                    row["cash_after"],
                    row.get("price"),
                    row["source"],
                    json.dumps(row.get("meta") or {}, default=str),
                )
                for row in result.get("ledger", [])
            ],
        )
        cur.executemany(
            """
            INSERT INTO quant.quant_backtest_events (
                run_id, event_index, event_type, x_axis, x_value,
                trade_id, price, message, meta
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            [
                (
                    run_id,
                    index,
                    row["event_type"],
                    row["x_axis"],
                    row["x_value"],
                    row["trade_id"],
                    row["price"],
                    row["message"],
                    json.dumps(row["meta"], default=str),
                )
                for index, row in enumerate(result["events"], start=1)
            ],
        )


def get_backtest_run_for_update_free(conn: Any, run_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM quant.quant_backtest_runs WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
    if not row:
        raise KeyError(f"backtest run not found: {run_id}")
    return dict(row)


def _get_run(conn: Any, run_id: int) -> dict[str, Any]:
    return get_backtest_run_for_update_free(conn, run_id)


def _get_parameters(conn: Any, run_id: int) -> BacktestParameters:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM quant.quant_backtest_parameters WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
    if not row:
        raise KeyError(f"backtest parameters not found: {run_id}")
    return BacktestParameters(
        entry_threshold=row["entry_threshold"],
        exit_threshold=row["exit_threshold"],
        stop_loss=row["stop_loss"],
        take_profit=row["take_profit"],
        max_holding_bars=int(row["max_holding_bars"]),
        initial_capital=row["initial_capital"],
        position_size=row["position_size"],
        fee_bps=row.get("fee_bps", Decimal("0")),
        slippage_bps=row.get("slippage_bps", Decimal("0")),
        liquidity_cap_pct=row.get("liquidity_cap_pct", Decimal("100")),
        max_position_notional=row.get("max_position_notional", Decimal("0")),
        min_fill_pct=row.get("min_fill_pct", Decimal("0")),
        execution_price_mode=row.get("execution_price_mode", "ORDERFILLED_LIMIT_REPLAY"),
        execution_profile=row.get("execution_profile", "realistic"),
        order_role=row.get("order_role", "taker"),
        latency_blocks=int(row.get("latency_blocks", 0) or 0),
        adverse_slippage_cents=row.get("adverse_slippage_cents", Decimal("0.005")),
        fill_probability_haircut_pct=row.get("fill_probability_haircut_pct", Decimal("20")),
        latency_seconds=row.get("latency_seconds", Decimal("0")),
        max_book_staleness_seconds=row.get("max_book_staleness_seconds", Decimal("900")),
        allow_partial_fill=bool(row.get("allow_partial_fill", True)),
        min_fill_size=row.get("min_fill_size", Decimal("0")),
        reject_on_stale_book=bool(row.get("reject_on_stale_book", True)),
        final_valuation_mode=row.get("final_valuation_mode", "SETTLEMENT"),
        max_entry_price=row.get("max_entry_price", Decimal("1")),
        min_exit_price=row.get("min_exit_price", Decimal("0")),
        buy_limit_price=row.get("buy_limit_price"),
        sell_limit_price=row.get("sell_limit_price"),
        settlement_value=row.get("settlement_value"),
    )


def _set_run_status(conn: Any, run_id: int, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quant.quant_backtest_runs
            SET status = %s,
                started_at = CASE WHEN started_at IS NULL THEN now() ELSE started_at END
            WHERE run_id = %s
            """,
            (status, run_id),
        )


def _price_point(row: dict[str, Any]) -> PricePoint:
    return PricePoint(
        x_value=int(row["x_value"]),
        price=Decimal(str(row["price"])),
        volume=Decimal(str(row.get("volume") or 0)),
        trade_count=int(row.get("trade_count") or 0),
        timestamp=_datetime_or_none(row.get("block_timestamp") or row.get("timestamp")),
    )


def _datetime_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _exit_reason(price: Decimal, entry_price: Decimal, holding_bars: int, params: BacktestParameters) -> str | None:
    if price <= params.exit_threshold:
        return "exit_threshold"
    if price <= entry_price * (Decimal("1") - params.stop_loss):
        return "stop_loss"
    if price >= entry_price * (Decimal("1") + params.take_profit):
        return "take_profit"
    if holding_bars >= params.max_holding_bars:
        return "max_holding_bars"
    return None


def _close_trade(
    run: dict[str, Any],
    x_axis: str,
    position: OpenPosition,
    point: PricePoint,
    point_index: int,
    exit_reason: str,
    params: BacktestParameters,
    *,
    exit_fill: dict[str, Any] | None = None,
    exit_order_id: str | None = None,
) -> dict[str, Any]:
    close_size = Decimal(str(exit_fill.get("size"))) if exit_fill else position.size
    exit_price_value = None
    if exit_fill:
        exit_price_value = exit_fill.get("exit_price")
        if exit_price_value is None:
            exit_price_value = exit_fill.get("avg_fill_price")
    exit_price = Decimal(str(exit_price_value)) if exit_price_value is not None else _execution_price(point.price, params, "exit")
    notional = position.entry_price * close_size
    exit_notional = exit_price * close_size
    entry_fee_cost = Decimal(str(getattr(position, "entry_fee_cost", Decimal("0")) or 0))
    if entry_fee_cost <= 0 and params.fee_bps > 0:
        entry_fee_cost = notional * _bps_fraction(params.fee_bps)
    exit_fee_cost = Decimal(str(exit_fill.get("fee_cost") or 0)) if exit_fill else (exit_notional * _bps_fraction(params.fee_bps))
    if exit_fee_cost <= 0 and params.fee_bps > 0 and exit_reason != "settlement":
        exit_fee_cost = exit_notional * _bps_fraction(params.fee_bps)
    fee_cost = entry_fee_cost + exit_fee_cost
    entry_slippage_cost = Decimal(str(getattr(position, "entry_slippage_cost", Decimal("0")) or 0))
    if entry_slippage_cost <= 0 and params.slippage_bps > 0:
        entry_slippage_cost = (notional * _bps_fraction(params.slippage_bps)).copy_abs()
    exit_slippage_cost = Decimal(str(exit_fill.get("slippage_cost") or 0)) if exit_fill else ((point.price - exit_price) * close_size).copy_abs()
    if exit_slippage_cost <= 0 and params.slippage_bps > 0 and exit_reason != "settlement":
        exit_slippage_cost = (exit_notional * _bps_fraction(params.slippage_bps)).copy_abs()
    slippage_cost = entry_slippage_cost + exit_slippage_cost
    execution_cost = fee_cost + slippage_cost
    pnl = (exit_price - position.entry_price) * close_size - fee_cost
    return {
        "trade_id": f"T-{position.trade_index:04d}",
        "entry_order_id": position.entry_order_id,
        "exit_order_id": exit_order_id,
        "market_slug": run["market_slug"],
        "token_side": run["token_side"],
        "side": "LONG",
        "x_axis": x_axis,
        "entry_x": position.entry_x,
        "exit_x": point.x_value,
        "entry_price": position.entry_price,
        "exit_price": exit_price,
        "size": close_size,
        "notional": notional,
        "requested_notional": position.requested_notional,
        "filled_notional": position.filled_notional,
        "requested_size": position.size,
        "filled_size": close_size,
        "unfilled_size": max(Decimal("0"), position.size - close_size),
        "fill_pct": position.fill_pct,
        "fill_status": exit_fill.get("fill_status") if exit_fill else position.fill_status,
        "book_snapshot_id": exit_fill.get("book_snapshot_id") if exit_fill else position.book_snapshot_id,
        "snapshot_version": exit_fill.get("snapshot_version") if exit_fill else position.snapshot_version,
        "staleness_seconds": exit_fill.get("staleness_seconds") if exit_fill else position.staleness_seconds,
        "staleness_blocks": exit_fill.get("staleness_blocks") if exit_fill else position.staleness_blocks,
        "fill_probability": exit_fill.get("fill_probability") if exit_fill else position.fill_probability,
        "block_volume": exit_fill.get("block_volume") if exit_fill else position.block_volume,
        "trade_count": exit_fill.get("trade_count") if exit_fill else position.trade_count,
        "available_notional": exit_fill.get("available_notional") if exit_fill else position.available_notional,
        "execution_source": exit_fill.get("execution_source") if exit_fill else "unknown",
        "avg_fill_price": exit_price,
        "pnl": pnl.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "pnl_pct": _pct(pnl, notional),
        "holding_bars": max(1, point_index - position.entry_index),
        "exit_reason": exit_reason,
        "fee_cost": fee_cost.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "slippage_cost": slippage_cost.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "execution_cost": execution_cost.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "entry_fee_cost": entry_fee_cost.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "exit_fee_cost": exit_fee_cost.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "entry_slippage_cost": entry_slippage_cost.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "exit_slippage_cost": exit_slippage_cost.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
    }


def _event(event_type: str, x_axis: str, x_value: int, trade_id: str | None, price: Decimal, message: str, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "x_axis": x_axis,
        "x_value": x_value,
        "trade_id": trade_id,
        "price": price,
        "message": message,
        "meta": meta or {},
    }


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except Exception:
        return None


def _is_limit_replay_mode(params: BacktestParameters) -> bool:
    return str(params.execution_price_mode or "").upper() in {"ORDERFILLED_LIMIT_REPLAY", "LIMIT_REPLAY"}


def _buy_limit_price(params: BacktestParameters) -> Decimal:
    value = params.buy_limit_price if params.buy_limit_price is not None else params.entry_threshold
    return min(max(Decimal("0"), Decimal(str(value))), Decimal("1")).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)


def _sell_limit_price(params: BacktestParameters, entry_price: Decimal) -> Decimal:
    if params.sell_limit_price is not None:
        value = params.sell_limit_price
    else:
        value = Decimal(str(entry_price)) * (Decimal("1") + Decimal(str(params.take_profit)))
    return min(max(Decimal("0"), Decimal(str(value))), Decimal("1")).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)


def _settlement_value(params: BacktestParameters, points: list[PricePoint]) -> Decimal | None:
    if params.settlement_value is not None:
        value = Decimal(str(params.settlement_value))
        if value in (Decimal("0"), Decimal("1")):
            return value
    if points:
        last_price = Decimal(str(points[-1].price))
        if last_price in (Decimal("0"), Decimal("1")):
            return last_price
    return None


def _after_latency(first: PricePoint, point: PricePoint, params: BacktestParameters, x_axis: str) -> bool:
    return _after_latency_value(first.x_value, point, params, x_axis)


def _after_latency_value(start_x: int, point: PricePoint, params: BacktestParameters, x_axis: str) -> bool:
    if x_axis == "block_number":
        return int(point.x_value) >= int(start_x) + int(params.latency_blocks or 0)
    return True


def _sell_limit_can_fill(trade_price: Decimal, sell_limit: Decimal) -> bool:
    if sell_limit >= Decimal("0.98"):
        return False
    return Decimal(str(trade_price)) >= Decimal(str(sell_limit))


def _limit_replay_fill(
    params: BacktestParameters,
    point: PricePoint,
    side: str,
    *,
    limit_price: Decimal,
    decision_x: int,
    target_size: Decimal | None = None,
) -> dict[str, Any]:
    price = Decimal(str(limit_price)).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    target_notional = _target_notional(params)
    if target_size is None:
        requested_size = (target_notional / max(price, Decimal("0.0000000001"))).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    else:
        requested_size = max(Decimal("0"), Decimal(str(target_size)))
        target_notional = (requested_size * price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    event = ReplayTradeEvent(
        market_id=0,
        token_id="",
        block_number=int(point.x_value),
        transaction_index=0,
        log_index=1,
        tx_hash=f"synthetic-{int(point.x_value)}",
        trade_price=Decimal(str(point.price)),
        size=max(Decimal("0"), Decimal(str(point.volume or 0))),
    )
    replay = replay_limit_order(
        OrderIntent(
            side="BUY_YES" if side.startswith("BUY") else "SELL_YES",
            limit_price=price,
            size=requested_size,
            time_in_force="GTC",
            submit_sequence=sequence_key(int(decision_x), 0, 0, "synthetic-submit"),
            liquidity_cap_pct=max(Decimal("0"), Decimal(str(params.liquidity_cap_pct))),
            fee_bps=max(Decimal("0"), Decimal(str(params.fee_bps))),
        ),
        [event],
    )
    price_key = "entry_price" if side.startswith("BUY") else "exit_price"
    filled_notional = replay.filled_notional
    return {
        "requested_notional": target_notional,
        "filled_notional": filled_notional,
        "fill_pct": replay.fill_pct,
        "fill_probability": replay.fill_pct,
        "size": replay.filled_size,
        price_key: price,
        "avg_fill_price": price,
        "liquidity_cap_pct": max(Decimal("0"), Decimal(str(params.liquidity_cap_pct))),
        "min_fill_pct": max(Decimal("0"), Decimal(str(params.min_fill_pct))),
        "partial_fill": replay.status == "PARTIAL_FILLED",
        "rejected": replay.status in {"NO_FILL", "REJECTED", "EXPIRED"},
        "fill_status": "PARTIAL" if replay.status == "PARTIAL_FILLED" else replay.status,
        "requested_size": requested_size,
        "filled_size": replay.filled_size,
        "unfilled_size": replay.unfilled_size,
        "block_volume": max(Decimal("0"), Decimal(str(point.volume or 0))),
        "trade_count": int(point.trade_count or 0),
        "available_notional": (max(Decimal("0"), Decimal(str(point.volume or 0))) * price * max(Decimal("0"), Decimal(str(params.liquidity_cap_pct))) / Decimal("100")).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "fee_cost": replay.fee,
        "slippage_cost": Decimal("0"),
        "execution_cost": replay.fee,
        "execution_source": "orderfilled_limit_replay",
        "execution_profile": params.execution_profile,
        "order_role": "maker",
        "limit_price": price,
        "crossing_price": Decimal(str(point.price)),
        "decision_x": int(decision_x),
        "crossing_x": int(point.x_value),
        "notes": replay.to_fill_dict().get("notes") or (["buy_limit_crossed"] if side.startswith("BUY") else ["sell_limit_crossed"]),
    }


def _limit_replay_no_fill(
    params: BacktestParameters,
    point: PricePoint,
    side: str,
    *,
    limit_price: Decimal,
    note: str,
    target_size: Decimal | None = None,
) -> dict[str, Any]:
    price = max(Decimal(str(limit_price)), Decimal("0.0000000001"))
    target_notional = _target_notional(params)
    if target_size is None:
        requested_size = (target_notional / price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    else:
        requested_size = max(Decimal("0"), Decimal(str(target_size)))
        target_notional = (requested_size * price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    return {
        "requested_notional": target_notional,
        "filled_notional": Decimal("0"),
        "fill_pct": Decimal("0"),
        "fill_probability": Decimal("0"),
        "size": Decimal("0"),
        "liquidity_cap_pct": max(Decimal("0"), Decimal(str(params.liquidity_cap_pct))),
        "min_fill_pct": max(Decimal("0"), Decimal(str(params.min_fill_pct))),
        "partial_fill": False,
        "rejected": True,
        "fill_status": "NO_FILL",
        "requested_size": requested_size,
        "filled_size": Decimal("0"),
        "unfilled_size": requested_size,
        "block_volume": max(Decimal("0"), Decimal(str(point.volume or 0))),
        "trade_count": int(point.trade_count or 0),
        "available_notional": Decimal("0"),
        "fee_cost": Decimal("0"),
        "slippage_cost": Decimal("0"),
        "execution_cost": Decimal("0"),
        "execution_source": "orderfilled_limit_replay",
        "execution_profile": params.execution_profile,
        "order_role": "maker",
        "limit_price": Decimal(str(limit_price)),
        "last_seen_price": Decimal(str(point.price)),
        "notes": [note],
    }


def _settlement_fill(params: BacktestParameters, point: PricePoint, target_size: Decimal, settlement_value: Decimal) -> dict[str, Any]:
    requested_size = max(Decimal("0"), Decimal(str(target_size)))
    payoff = Decimal(str(settlement_value))
    filled_notional = (requested_size * payoff).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    return {
        "requested_notional": filled_notional,
        "filled_notional": filled_notional,
        "fill_pct": Decimal("100"),
        "fill_probability": Decimal("100"),
        "size": requested_size,
        "exit_price": payoff,
        "avg_fill_price": payoff,
        "liquidity_cap_pct": Decimal("0"),
        "min_fill_pct": Decimal("0"),
        "partial_fill": False,
        "rejected": False,
        "fill_status": "FILLED",
        "requested_size": requested_size,
        "filled_size": requested_size,
        "unfilled_size": Decimal("0"),
        "block_volume": max(Decimal("0"), Decimal(str(point.volume or 0))),
        "trade_count": int(point.trade_count or 0),
        "available_notional": filled_notional,
        "fee_cost": Decimal("0"),
        "slippage_cost": Decimal("0"),
        "execution_cost": Decimal("0"),
        "execution_source": "settlement_payoff",
        "execution_profile": params.execution_profile,
        "order_role": "settlement",
        "settlement_value": payoff,
        "notes": ["settlement_payoff"],
    }


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    if not denominator:
        return Decimal("0")
    return (numerator / denominator * Decimal("100")).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)


def _bps_fraction(value: Decimal) -> Decimal:
    return max(Decimal("0"), Decimal(str(value))) / Decimal("10000")


def _execution_price(price: Decimal, params: BacktestParameters, side: str) -> Decimal:
    if side == "raw_entry":
        fraction = _bps_fraction(params.slippage_bps)
        if fraction <= 0:
            return price
        return (price / (Decimal("1") + fraction)).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    fraction = _bps_fraction(params.slippage_bps)
    if side == "entry":
        return min(Decimal("0.9999999999"), price * (Decimal("1") + fraction)).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    return max(Decimal("0"), price * (Decimal("1") - fraction)).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)


def _size_for_liquidity(params: BacktestParameters, price: Decimal, volume: Decimal) -> Decimal:
    return _legacy_fill_decision(params, price, volume)["size"]


def _target_notional(params: BacktestParameters) -> Decimal:
    target = max(Decimal("0"), Decimal(str(params.position_size)))
    max_position = max(Decimal("0"), Decimal(str(params.max_position_notional)))
    if max_position > 0:
        target = min(target, max_position)
    return target


def _fill_decision(
    params: BacktestParameters,
    point: PricePoint,
    run: dict[str, Any],
    side: str,
    *,
    target_size: Decimal | None = None,
) -> dict[str, Any]:
    mode = str(params.execution_price_mode or "ORDERFILLED").upper()
    if mode == "ORDERFILLED":
        return _orderfilled_fill_decision(params, point, side, target_size=target_size)
    if mode == "LEGACY":
        fill = _legacy_fill_decision(params, point.price, point.volume)
        if target_size is not None and fill["size"] > target_size:
            fill = {**fill, "size": target_size, "filled_notional": (target_size * point.price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)}
        return fill
    config = execution_config_from_params(params)
    snapshots = [
        snapshot
        for snapshot in (snapshot_from_any(item) for item in run.get("_clob_snapshots") or [])
        if snapshot is not None
    ]
    lookup = lookup_snapshot(
        snapshots,
        decision_block=point.x_value if run.get("price_source") == "orderfilled_block_close" else None,
        decision_timestamp=point.timestamp,
        config=config,
    )
    requested_size = target_size
    if requested_size is None:
        target_notional = _target_notional(params)
        reference_price = max(point.price, Decimal("0.0000000001"))
        requested_size = (target_notional / reference_price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    fill = simulate_depth_fill(
        side=side,  # type: ignore[arg-type]
        target_size=max(Decimal("0"), requested_size),
        config=config,
        lookup=lookup,
        cash_available=_target_notional(params) if side.startswith("BUY") else None,
    )
    return _fill_result_to_engine(fill, point.price, params, side)


def _orderfilled_fill_decision(
    params: BacktestParameters,
    point: PricePoint,
    side: str,
    *,
    target_size: Decimal | None = None,
) -> dict[str, Any]:
    price = Decimal(str(point.price))
    profile = effective_execution_profile(params)
    exec_price = _execution_price(price, params, "entry" if side.startswith("BUY") else "exit")
    exec_price = apply_adverse_slippage(exec_price, profile, side)
    target_notional = _target_notional(params)
    if target_size is not None:
        requested_size = max(Decimal("0"), Decimal(str(target_size)))
        target_notional = (requested_size * max(exec_price, Decimal("0.0000000001"))).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    else:
        requested_size = (target_notional / max(exec_price, Decimal("0.0000000001"))).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)

    cap_pct = max(Decimal("0"), Decimal(str(params.liquidity_cap_pct)))
    min_fill_pct = max(Decimal("0"), Decimal(str(params.min_fill_pct)))
    block_volume = max(Decimal("0"), Decimal(str(point.volume or 0)))
    available_notional = (block_volume * cap_pct / Decimal("100")).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    raw_fill_probability = min(Decimal("100"), _pct(available_notional, target_notional)) if target_notional else Decimal("0")
    fill_probability = apply_probability_haircut(raw_fill_probability, profile)
    effective_available_notional = min(
        available_notional,
        (target_notional * fill_probability / Decimal("100")).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
    )
    empty = {
        "requested_notional": target_notional,
        "filled_notional": Decimal("0"),
        "fill_pct": Decimal("0"),
        "fill_probability": fill_probability,
        "size": Decimal("0"),
        "liquidity_cap_pct": cap_pct,
        "min_fill_pct": min_fill_pct,
        "partial_fill": False,
        "rejected": True,
        "fill_status": "REJECTED",
        "requested_size": requested_size,
        "filled_size": Decimal("0"),
        "unfilled_size": requested_size,
        "block_volume": block_volume,
        "trade_count": int(point.trade_count or 0),
        "available_notional": available_notional,
        "execution_source": "orderfilled_volume",
        "execution_profile": profile.name,
        "order_role": profile.order_role,
        "latency_blocks": profile.latency_blocks,
        "adverse_slippage_cents": profile.adverse_slippage_cents,
        "fill_probability_haircut_pct": profile.fill_probability_haircut_pct,
        "raw_fill_probability": raw_fill_probability,
        "notes": ["no_orderfilled_volume"] if block_volume <= 0 else [],
    }
    if price <= 0 or exec_price <= 0 or target_notional <= 0 or cap_pct <= 0:
        return empty
    if available_notional <= 0:
        return empty

    if params.allow_partial_fill:
        filled_notional = min(target_notional, effective_available_notional)
    else:
        if effective_available_notional < target_notional:
            return {**empty, "notes": ["insufficient_orderfilled_volume_for_full_fill"]}
        filled_notional = target_notional
    filled_notional = filled_notional.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    filled_size = (filled_notional / exec_price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    min_size_from_pct = requested_size * min_fill_pct / Decimal("100")
    min_required_size = max(Decimal(str(params.min_fill_size)), min_size_from_pct).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    fill_pct = _pct(filled_notional, target_notional) if target_notional else Decimal("0")
    if filled_size <= 0 or filled_size < min_required_size:
        return {
            **empty,
            "filled_notional": filled_notional,
            "fill_pct": fill_pct,
            "filled_size": filled_size,
            "unfilled_size": max(Decimal("0"), requested_size - filled_size),
            "notes": ["below_min_fill_threshold"],
        }
    fill_status = "FILLED" if filled_notional >= target_notional else "PARTIAL"
    price_key = "entry_price" if side.startswith("BUY") else "exit_price"
    slippage_cost = ((exec_price - price) * filled_size).copy_abs().quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    fee_cost = (filled_notional * _bps_fraction(params.fee_bps)).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    return {
        "requested_notional": target_notional,
        "filled_notional": filled_notional,
        "fill_pct": fill_pct,
        "fill_probability": fill_probability,
        "size": filled_size,
        price_key: exec_price,
        "avg_fill_price": exec_price,
        "liquidity_cap_pct": cap_pct,
        "min_fill_pct": min_fill_pct,
        "partial_fill": fill_status == "PARTIAL",
        "rejected": False,
        "fill_status": fill_status,
        "requested_size": requested_size,
        "filled_size": filled_size,
        "unfilled_size": max(Decimal("0"), requested_size - filled_size).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "block_volume": block_volume,
        "trade_count": int(point.trade_count or 0),
        "available_notional": available_notional,
        "execution_source": "orderfilled_volume",
        "execution_profile": profile.name,
        "order_role": profile.order_role,
        "latency_blocks": profile.latency_blocks,
        "adverse_slippage_cents": profile.adverse_slippage_cents,
        "fill_probability_haircut_pct": profile.fill_probability_haircut_pct,
        "raw_fill_probability": raw_fill_probability,
        "fee_cost": fee_cost,
        "slippage_cost": slippage_cost,
        "execution_cost": fee_cost + slippage_cost,
        "notes": ["expected_fill_from_historical_orderfilled_volume"],
    }


def _fill_result_to_engine(fill: FillResult, signal_price: Decimal, params: BacktestParameters, side: str) -> dict[str, Any]:
    filled_notional = fill.filled_notional
    size = fill.filled_size
    requested_notional = (fill.requested_size * signal_price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    price_key = "entry_price" if side.startswith("BUY") else "exit_price"
    return {
        "requested_notional": requested_notional,
        "filled_notional": filled_notional,
        "fill_pct": fill.fill_pct,
        "fill_probability": fill.fill_pct,
        "size": size,
        price_key: fill.avg_fill_price if fill.avg_fill_price > 0 else None,
        "avg_fill_price": fill.avg_fill_price,
        "liquidity_cap_pct": max(Decimal("0"), Decimal(str(params.liquidity_cap_pct))),
        "min_fill_pct": max(Decimal("0"), Decimal(str(params.min_fill_pct))),
        "partial_fill": fill.status == "PARTIAL",
        "rejected": fill.status not in {"FILLED", "PARTIAL"},
        "fill_status": fill.status,
        "book_snapshot_id": fill.snapshot_id,
        "snapshot_version": fill.snapshot_version,
        "staleness_seconds": fill.staleness_seconds,
        "staleness_blocks": fill.staleness_blocks,
        "requested_size": fill.requested_size,
        "filled_size": fill.filled_size,
        "unfilled_size": fill.unfilled_size,
        "fee_cost": fill.fee,
        "slippage_cost": fill.slippage,
        "execution_cost": fill.fee + fill.slippage,
        "execution_source": "clob_depth" if fill.snapshot_id else "no_book",
        "block_volume": Decimal("0"),
        "trade_count": 0,
        "available_notional": filled_notional,
        "best_bid": fill.best_bid,
        "best_ask": fill.best_ask,
        "spread": fill.spread,
        "mid": fill.mid,
        "levels_consumed": fill.levels_consumed,
        "notes": fill.notes,
    }


def _force_close_fill(params: BacktestParameters, point: PricePoint, target_size: Decimal) -> dict[str, Any]:
    requested_size = max(Decimal("0"), Decimal(str(target_size)))
    profile = effective_execution_profile(params)
    raw_price = Decimal(str(point.price))
    exit_price = apply_adverse_slippage(_execution_price(raw_price, params, "exit"), profile, "SELL_YES")
    filled_notional = (requested_size * exit_price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    fee_cost = (filled_notional * _bps_fraction(params.fee_bps)).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    slippage_cost = ((raw_price - exit_price) * requested_size).copy_abs().quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    return {
        "requested_notional": filled_notional,
        "filled_notional": filled_notional,
        "fill_pct": Decimal("100"),
        "fill_probability": Decimal("100"),
        "size": requested_size,
        "exit_price": exit_price,
        "avg_fill_price": exit_price,
        "liquidity_cap_pct": max(Decimal("0"), Decimal(str(params.liquidity_cap_pct))),
        "min_fill_pct": max(Decimal("0"), Decimal(str(params.min_fill_pct))),
        "partial_fill": False,
        "rejected": False,
        "fill_status": "FILLED",
        "requested_size": requested_size,
        "filled_size": requested_size,
        "unfilled_size": Decimal("0"),
        "block_volume": max(Decimal("0"), Decimal(str(point.volume or 0))),
        "trade_count": int(point.trade_count or 0),
        "available_notional": filled_notional,
        "fee_cost": fee_cost,
        "slippage_cost": slippage_cost,
        "execution_cost": fee_cost + slippage_cost,
        "execution_source": "forced_mark_to_market",
        "execution_profile": profile.name,
        "order_role": profile.order_role,
        "notes": ["force_close_marked_to_last_orderfilled_price"],
    }


def _legacy_fill_decision(params: BacktestParameters, price: Decimal, volume: Decimal) -> dict[str, Any]:
    target_notional = _target_notional(params)
    empty = {
        "requested_notional": target_notional,
        "filled_notional": Decimal("0"),
        "fill_pct": Decimal("0"),
        "size": Decimal("0"),
        "liquidity_cap_pct": max(Decimal("0"), Decimal(str(params.liquidity_cap_pct))),
        "min_fill_pct": max(Decimal("0"), Decimal(str(params.min_fill_pct))),
        "partial_fill": False,
    }
    if price <= 0:
        return empty
    cap_pct = max(Decimal("0"), Decimal(str(params.liquidity_cap_pct)))
    if cap_pct <= 0:
        return empty
    if volume <= 0:
        filled_notional = target_notional
    else:
        filled_notional = min(target_notional, volume * cap_pct / Decimal("100"))
    fill_pct = _pct(filled_notional, target_notional) if target_notional else Decimal("0")
    min_fill_pct = max(Decimal("0"), Decimal(str(params.min_fill_pct)))
    if target_notional <= 0 or fill_pct < min_fill_pct:
        return {
            **empty,
            "filled_notional": filled_notional,
            "fill_pct": fill_pct,
            "partial_fill": filled_notional > 0 and filled_notional < target_notional,
            "fill_probability": fill_pct,
            "rejected": True,
        }
    size = (filled_notional / price).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    return {
        "requested_notional": target_notional,
        "filled_notional": filled_notional.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "fill_pct": fill_pct,
        "fill_probability": fill_pct,
        "size": size,
        "liquidity_cap_pct": cap_pct,
        "min_fill_pct": min_fill_pct,
        "partial_fill": filled_notional < target_notional,
        "fill_status": "FILLED" if filled_notional >= target_notional else "PARTIAL",
        "requested_size": size,
        "filled_size": size,
        "unfilled_size": Decimal("0"),
        "block_volume": max(Decimal("0"), Decimal(str(volume or 0))),
        "trade_count": 0,
        "available_notional": filled_notional.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "execution_source": "legacy_volume_cap",
        "rejected": False,
    }


def _fill_from_clob_book(target_notional: Decimal, cap_pct: Decimal, book: dict[str, Any] | None) -> dict[str, Any] | None:
    if not book or target_notional <= 0:
        return None
    levels = _book_levels(book.get("asks"), reverse=False)
    if not levels:
        return None
    max_book_notional = target_notional * cap_pct / Decimal("100")
    remaining = min(target_notional, max_book_notional)
    if remaining <= 0:
        return {
            "requested_notional": target_notional,
            "filled_notional": Decimal("0"),
            "size": Decimal("0"),
            "entry_price": Decimal("0"),
            "execution_source": "clob_snapshot",
            "snapshot_id": book.get("snapshot_id"),
            "book_side": "asks",
            "levels_consumed": 0,
            "book_depth_notional": Decimal("0"),
        }
    filled_notional = Decimal("0")
    filled_size = Decimal("0")
    levels_consumed = 0
    for level in levels:
        level_notional = level["price"] * level["size"]
        if level_notional <= 0:
            continue
        take_notional = min(remaining, level_notional)
        filled_notional += take_notional
        filled_size += take_notional / level["price"]
        remaining -= take_notional
        levels_consumed += 1
        if remaining <= 0:
            break
    entry_price = (filled_notional / filled_size).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP) if filled_size > 0 else Decimal("0")
    return {
        "requested_notional": target_notional,
        "filled_notional": filled_notional.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "size": filled_size.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "entry_price": entry_price,
        "execution_source": "clob_snapshot",
        "snapshot_id": book.get("snapshot_id"),
        "book_side": "asks",
        "levels_consumed": levels_consumed,
        "book_depth_notional": sum((level["price"] * level["size"] for level in levels), Decimal("0")).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP),
        "best_bid": book.get("best_bid"),
        "best_ask": book.get("best_ask"),
        "spread": book.get("spread"),
        "fetched_at": book.get("fetched_at"),
    }


def _book_levels(rows: Any, *, reverse: bool) -> list[dict[str, Decimal]]:
    if not isinstance(rows, list):
        return []
    levels: list[dict[str, Decimal]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            price = Decimal(str(row.get("price")))
            size = Decimal(str(row.get("size")))
        except Exception:
            continue
        if price <= 0 or size <= 0:
            continue
        levels.append({"price": price, "size": size})
    levels.sort(key=lambda item: item["price"], reverse=reverse)
    return levels


def _ratio(numerator: int, denominator: int) -> Decimal:
    if not denominator:
        return Decimal("0")
    return Decimal(numerator) / Decimal(denominator)


def _money(value: Decimal) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,} USDC"


def _percent(value: Decimal) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"


def _status(value: Decimal) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "neutral"
