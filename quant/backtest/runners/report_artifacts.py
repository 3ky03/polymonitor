"""Report artifacts for OrderFilled-first replay benchmarks."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
from typing import Any


def build_benchmark_artifacts(
    *,
    reports: dict[str, Any],
    comparison_rows: list[Any],
    coverage: dict[str, Any],
    universe: dict[str, Any],
) -> dict[str, Any]:
    reference = reports.get("accurate:realistic") or reports.get("fast:realistic") or next(iter(reports.values()), None)
    trade_rows = list(getattr(reference, "trade_rows", []) if reference is not None else [])
    profile_rows = [_plain_profile(key, report) for key, report in reports.items()]
    return {
        "profiles": profile_rows,
        "per_market_rows": [_plain_trade(row) for row in trade_rows],
        "fast_accurate_rows": [_as_plain(row) for row in comparison_rows],
        "fill_quality": build_fill_quality(reference),
        "data_quality": build_data_quality(reference, comparison_rows=comparison_rows, coverage=coverage, universe=universe),
        "regime_buckets": build_regime_buckets(trade_rows),
        "prediction_quality": build_prediction_quality(trade_rows),
    }


def build_fill_quality(report: Any | None) -> dict[str, Any]:
    if report is None:
        return _empty_quality()
    rows = list(getattr(report, "trade_rows", []))
    signals = int(getattr(report, "signal_count", len(rows)) or len(rows))
    filled = [row for row in rows if Decimal(str(getattr(row, "filled_size", "0") or "0")) > 0]
    partial = [row for row in rows if str(getattr(row, "order_status", "")) == "PARTIAL_FILLED"]
    no_fill = [row for row in rows if Decimal(str(getattr(row, "filled_size", "0") or "0")) <= 0]
    no_fill_reasons: dict[str, int] = {}
    for row in no_fill:
        reason = str(getattr(row, "order_status", "NO_FILL") or "NO_FILL")
        no_fill_reasons[reason] = no_fill_reasons.get(reason, 0) + 1
    avg_fill_price = _avg([_entry_price(row) for row in filled])
    avg_slippage = _avg([abs(Decimal(str(getattr(row, "crossing_trade_price", "") or getattr(row, "limit_price", "0") or "0")) - Decimal(str(getattr(row, "limit_price", "0") or "0"))) for row in filled])
    return {
        "signal_count": signals,
        "submitted_count": len(rows),
        "filled_count": len(filled),
        "partial_fill_count": len(partial),
        "no_fill_count": len(no_fill),
        "fill_rate": _ratio(len(filled), signals),
        "partial_fill_rate": _ratio(len(partial), signals),
        "no_fill_rate": _ratio(len(no_fill), signals),
        "avg_fill_price": _decimal_text(avg_fill_price),
        "avg_slippage": _decimal_text(avg_slippage),
        "avg_latency_blocks": _decimal_text(_avg([Decimal(str(max(0, int(getattr(row, "fill_block", 0) or 0) - int(getattr(row, "signal_block", 0) or 0)))) for row in filled])),
        "no_fill_reasons": no_fill_reasons,
    }


def build_data_quality(report: Any | None, *, comparison_rows: list[Any], coverage: dict[str, Any], universe: dict[str, Any]) -> dict[str, Any]:
    return {
        "universe": universe,
        "source_table": getattr(report, "replay_table", None) or "orderfilled_fact/orderfilled_block_replay",
        "raw_market_count": int(getattr(report, "raw_market_count", 0) or 0) if report is not None else 0,
        "raw_rows": int(sum(int(getattr(row, "raw_rows_for_outcome", 0) or 0) for row in getattr(report, "trade_rows", []))) if report is not None else 0,
        "coverage": coverage,
        "status_mismatch_count": sum(1 for row in comparison_rows if getattr(row, "fast_status", None) != getattr(row, "accurate_status", None)),
        "pnl_drift_count": sum(1 for row in comparison_rows if Decimal(str(getattr(row, "pnl_diff", "0") or "0")) != 0),
        "gap_status": "not_measured",
        "stale_status": "not_measured",
    }


def build_regime_buckets(rows: list[Any]) -> dict[str, Any]:
    return {
        "price_bucket": _bucket_counts(rows, lambda row: _price_bucket(Decimal(str(getattr(row, "signal_probability", "0") or "0")))),
        "liquidity_bucket": _bucket_counts(rows, lambda row: _liquidity_bucket(int(getattr(row, "raw_rows_for_outcome", 0) or 0))),
        "drift_bucket": _bucket_counts(rows, lambda row: _drift_bucket(Decimal(str(getattr(row, "snapshot_drift", "0") or "0")))),
        "settlement_bucket": _bucket_counts(rows, lambda row: "unresolved" if str(getattr(row, "order_status", "")) == "UNRESOLVED" else ("resolved_win" if Decimal(str(getattr(row, "payoff_per_share", "0") or "0")) > 0 else "resolved_loss")),
    }


def build_prediction_quality(rows: list[Any]) -> dict[str, Any]:
    scored = []
    baseline = []
    buckets: dict[str, dict[str, Decimal | int]] = {}
    for row in rows:
        if str(getattr(row, "order_status", "")) == "UNRESOLVED":
            continue
        probability = Decimal(str(getattr(row, "signal_probability", "0") or "0"))
        actual = Decimal("1") if Decimal(str(getattr(row, "payoff_per_share", "0") or "0")) > 0 else Decimal("0")
        close_line = Decimal(str(getattr(row, "close_line_probability", probability) or probability))
        scored.append((probability - actual) ** 2)
        baseline.append((close_line - actual) ** 2)
        bucket = _price_bucket(probability)
        state = buckets.setdefault(bucket, {"count": 0, "predicted_sum": Decimal("0"), "actual_sum": Decimal("0"), "brier_sum": Decimal("0")})
        state["count"] = int(state["count"]) + 1
        state["predicted_sum"] = Decimal(state["predicted_sum"]) + probability
        state["actual_sum"] = Decimal(state["actual_sum"]) + actual
        state["brier_sum"] = Decimal(state["brier_sum"]) + scored[-1]
    brier = _avg(scored)
    baseline_brier = _avg(baseline)
    return {
        "sample_count": len(scored),
        "brier_score": _decimal_text(brier),
        "market_brier_score": _decimal_text(baseline_brier),
        "brier_advantage": _decimal_text(baseline_brier - brier),
        "calibration_buckets": [
            {
                "bucket": bucket,
                "count": int(values["count"]),
                "avg_predicted": _decimal_text(Decimal(values["predicted_sum"]) / Decimal(int(values["count"]))),
                "actual_rate": _decimal_text(Decimal(values["actual_sum"]) / Decimal(int(values["count"]))),
                "brier_score": _decimal_text(Decimal(values["brier_sum"]) / Decimal(int(values["count"]))),
            }
            for bucket, values in sorted(buckets.items())
            if int(values["count"]) > 0
        ],
        "avg_snapshot_drift": _decimal_text(_avg([Decimal(str(getattr(row, "snapshot_drift", "0") or "0")) for row in rows])),
        "avg_close_line_drift": _decimal_text(_avg([Decimal(str(getattr(row, "close_line_edge", "0") or "0")) for row in rows])),
    }


def _empty_quality() -> dict[str, Any]:
    return {
        "signal_count": 0,
        "submitted_count": 0,
        "filled_count": 0,
        "partial_fill_count": 0,
        "no_fill_count": 0,
        "fill_rate": "0",
        "partial_fill_rate": "0",
        "no_fill_rate": "0",
    }


def _plain_profile(key: str, report: Any) -> dict[str, Any]:
    return {
        "key": key,
        "execution_profile": getattr(report, "execution_profile", ""),
        "signal_count": int(getattr(report, "signal_count", 0) or 0),
        "trades": int(getattr(report, "trades", 0) or 0),
        "no_fills": int(getattr(report, "no_fills", 0) or 0),
        "total_pnl": str(getattr(report, "total_pnl", "0")),
        "settlement_pnl": str(getattr(report, "settlement_pnl", "0")),
        "trade_exit_pnl": str(getattr(report, "trade_exit_pnl", "0")),
        "fee_total": str(getattr(report, "fee_total", "0")),
        "slippage_total": str(getattr(report, "slippage_total", "0")),
        "db_query_sec": float(getattr(report, "db_query_sec", 0.0) or 0.0),
        "engine_sec": float(getattr(report, "engine_sec", 0.0) or 0.0),
    }


def _plain_trade(row: Any) -> dict[str, Any]:
    return _as_plain(row)


def _as_plain(value: Any) -> Any:
    if is_dataclass(value):
        return _as_plain(asdict(value))
    if isinstance(value, dict):
        return {str(key): _as_plain(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_as_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_as_plain(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def _entry_price(row: Any) -> Decimal:
    filled_size = Decimal(str(getattr(row, "filled_size", "0") or "0"))
    buy_cost = Decimal(str(getattr(row, "buy_cost", "0") or "0"))
    return buy_cost / filled_size if filled_size else Decimal("0")


def _avg(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")


def _ratio(numerator: int, denominator: int) -> str:
    return _decimal_text(Decimal(numerator) / Decimal(denominator) if denominator else Decimal("0"))


def _decimal_text(value: Decimal | int | str) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(decimal_value.normalize(), "f")


def _bucket_counts(rows: list[Any], bucket_fn) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Decimal | int]] = {}
    for row in rows:
        bucket = str(bucket_fn(row))
        state = buckets.setdefault(bucket, {"count": 0, "pnl": Decimal("0")})
        state["count"] = int(state["count"]) + 1
        state["pnl"] = Decimal(state["pnl"]) + Decimal(str(getattr(row, "pnl", "0") or "0"))
    return [{"bucket": key, "count": int(value["count"]), "pnl": _decimal_text(Decimal(value["pnl"]))} for key, value in sorted(buckets.items())]


def _price_bucket(value: Decimal) -> str:
    if value < Decimal("0.2"):
        return "00_20"
    if value < Decimal("0.4"):
        return "20_40"
    if value < Decimal("0.6"):
        return "40_60"
    if value < Decimal("0.8"):
        return "60_80"
    return "80_100"


def _liquidity_bucket(rows: int) -> str:
    if rows < 10:
        return "thin"
    if rows < 100:
        return "medium"
    return "active"


def _drift_bucket(value: Decimal) -> str:
    if value <= Decimal("-0.05"):
        return "drift_down_5c"
    if value < Decimal("0"):
        return "drift_down"
    if value >= Decimal("0.05"):
        return "drift_up_5c"
    if value > Decimal("0"):
        return "drift_up"
    return "flat"
