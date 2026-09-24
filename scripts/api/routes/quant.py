from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import sys
import time
from typing import Any
import uuid

from flask import Blueprint, Response, jsonify, request, stream_with_context


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.api.read_api import (  # noqa: E402
    get_backtest_equity,
    get_backtest_ledger,
    get_backtest_metrics,
    get_backtest_orders,
    get_backtest_run,
    get_backtest_runs,
    get_backtest_trades,
    get_block_close_prices,
    get_event_price_head,
    get_event_price_tile,
    get_event_price_series,
    get_frontend_prices,
    get_market_price_series,
    get_price_build_status,
    get_quant_event_members,
    get_quant_price_events,
    get_quant_price_markets,
)
from quant.backtest.backtest_engine import create_and_execute_backtest  # noqa: E402
from quant.backtest.benchmark_persistence import (  # noqa: E402
    cancel_queued_benchmark_run,
    create_benchmark_run,
    get_benchmark_queue_status,
    get_benchmark_artifacts,
    get_benchmark_rows,
    get_benchmark_run,
    list_benchmark_runs,
    retry_benchmark_run,
)
from quant.backtest.runners.coverage_build import build_replay_coverage  # noqa: E402
from quant.backtest.runners.selectors import list_supported_universes, universe_spec_from_payload  # noqa: E402
from quant.core.db import PostgresConnectionPool, PostgresSettings, postgres_connection  # noqa: E402
from quant.core.schema import create_schema  # noqa: E402

LOGGER = logging.getLogger(__name__)
BENCHMARK_DB_POOL = PostgresConnectionPool(PostgresSettings(), max_size=4)
QUANT_EVENT_TILE_NAMESPACE = "quant-event-tile"
QUANT_EVENT_TILE_WARM_KEY = "quant-event-tile-warm:events"
QUANT_PRICE_TILE_NAMESPACE = "quant-price-series-tiles"
QUANT_ENTITY_SNAPSHOT_NAMESPACE = "quant-entity-snapshot"
QUANT_EVENT_MEMBERS_NAMESPACE = "quant-event-members"
QUANT_PRICE_TILE_KEY_VERSION = 1


def _canonical_tile_range(value: str | None) -> str:
    normalized = str(value or "latest").strip().lower()
    return "full" if normalized in {"all", "full"} else "latest"


def _canonical_price_range(value: str | None) -> str:
    normalized = str(value or "latest").strip().lower()
    if normalized in {"all", "full"}:
        return "full"
    if normalized in {"window", "viewport", "custom"}:
        return "window"
    if normalized in {"1h", "6h", "1d", "1w", "1m", "500blk", "2.5k", "5k", "15k"}:
        return normalized
    return "latest"


def _clamp_int(value: int | None, default: int, low: int, high: int) -> int:
    if value is None:
        value = default
    return min(max(int(value), low), high)


def _viewport_max_points(default: int = 900, *, full: bool = False) -> int:
    width = _parse_int_arg("viewport_width")
    if width is None:
        return default
    multiplier = 1.05 if full else 1.35
    return _clamp_int(int(width * multiplier), default, 180, 2500)


def _parse_int_arg(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _parse_bool_arg(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_time_arg(name: str) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _optional_decimal_payload(value: Any) -> Decimal | None:
    if value in (None, "", "null"):
        return None
    return Decimal(str(value))


def _optional_int_payload(value: Any) -> int | None:
    if value in (None, "", "null"):
        return None
    return int(value)


def _benchmark_profile_keys(payload: dict[str, Any]) -> tuple[str, ...]:
    replay_profiles = payload.get("replayProfiles") or payload.get("replay_profiles")
    execution_profiles = payload.get("executionProfiles") or payload.get("execution_profiles")
    if replay_profiles and execution_profiles:
        keys = tuple(
            f"{str(replay)}:{str(profile)}"
            for replay in replay_profiles
            for profile in execution_profiles
            if str(replay) in {"fast", "accurate"}
        )
        return keys or ("fast:optimistic", "fast:realistic", "fast:stress", "accurate:realistic")
    bundle = payload.get("profileBundle") or payload.get("profile_bundle")
    if bundle in (None, "", "fast-vs-accurate"):
        return ("fast:optimistic", "fast:realistic", "fast:stress", "accurate:realistic")
    if isinstance(bundle, list):
        return tuple(str(item) for item in bundle)
    return tuple(str(item).strip() for item in str(bundle).split(",") if str(item).strip())


def _benchmark_request_parts(payload: dict[str, Any]) -> dict[str, Any]:
    universe_spec = universe_spec_from_payload(payload)
    strategy_payload = payload.get("strategySpec") or payload.get("strategy_spec") or {}
    if not isinstance(strategy_payload, dict):
        strategy_payload = {}
    profile_keys = _benchmark_profile_keys(payload)
    min_probability = Decimal(str(strategy_payload.get("minProbability") or strategy_payload.get("min_probability") or payload.get("minProbability") or "0.60"))
    max_probability = Decimal(str(strategy_payload.get("maxProbability") or strategy_payload.get("max_probability") or payload.get("maxProbability") or "0.80"))
    stake = Decimal(str(strategy_payload.get("stake") or payload.get("stake") or "10"))
    initial_capital = Decimal(str(strategy_payload.get("initialCapital") or strategy_payload.get("initial_capital") or payload.get("initialCapital") or "1000"))
    max_daily_cost = _optional_decimal_payload(strategy_payload.get("maxDailyCost", strategy_payload.get("max_daily_cost", payload.get("maxDailyCost", payload.get("max_daily_cost", "20")))))
    max_concurrent_positions = _optional_int_payload(strategy_payload.get("maxConcurrentPositions", strategy_payload.get("max_concurrent_positions", payload.get("maxConcurrentPositions", payload.get("max_concurrent_positions", 2)))))
    max_daily_trades = _optional_int_payload(strategy_payload.get("maxDailyTrades", strategy_payload.get("max_daily_trades", payload.get("maxDailyTrades", payload.get("max_daily_trades")))))
    parameters = {
        "limit": int(universe_spec.limit),
        "universe": {
            "universeName": universe_spec.universe_name,
            "universeType": universe_spec.universe_type,
            "limit": int(universe_spec.limit),
            "marketIds": list(universe_spec.market_ids or []),
            "marketSlugs": list(universe_spec.market_slugs or []),
            "eventSlug": universe_spec.event_slug,
            "category": universe_spec.category,
            "startDate": universe_spec.start_date,
            "endDate": universe_spec.end_date,
            "requireResolved": bool(universe_spec.require_resolved),
            "requireOrderfilledRows": bool(universe_spec.require_orderfilled_rows),
        },
        "min_probability": str(min_probability),
        "max_probability": str(max_probability),
        "snapshot_hours_before_start": "1",
        "signal_lookback_hours": "24",
        "window_start_hours": "1",
        "window_end_hours": "0",
        "initial_capital": str(initial_capital),
        "stake": str(stake),
        "max_daily_cost": str(max_daily_cost) if max_daily_cost is not None else None,
        "max_concurrent_positions": max_concurrent_positions,
        "max_daily_trades": max_daily_trades,
        "yes_only": True,
        "sort_by": "probability_desc",
    }
    return {
        "universe_spec": universe_spec,
        "profile_keys": profile_keys,
        "parameters": parameters,
        "profiles": {"requested": list(profile_keys)},
        "force_block_replay_backfill": bool(payload.get("forceBlockReplayBackfill") or payload.get("force_block_replay_backfill")),
        "min_probability": min_probability,
        "max_probability": max_probability,
        "stake": stake,
        "initial_capital": initial_capital,
        "max_daily_cost": max_daily_cost,
        "max_concurrent_positions": max_concurrent_positions,
        "max_daily_trades": max_daily_trades,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def _camel_row(row: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "token_id": "tokenId",
        "market_id": "marketId",
        "market_slug": "marketSlug",
        "token_side": "tokenSide",
        "ts_minute": "tsMinute",
        "block_number": "blockNumber",
        "close_price": "closePrice",
        "yes_probability_close": "yesProbabilityClose",
        "vwap_price": "vwapPrice",
        "yes_probability_vwap": "yesProbabilityVwap",
        "close_raw_price": "closeRawPrice",
        "close_price_source": "closePriceSource",
        "close_tx_hash": "closeTxHash",
        "close_log_index": "closeLogIndex",
        "trade_count": "tradeCount",
        "raw_trade_count": "rawTradeCount",
        "run_id": "runId",
        "started_at": "startedAt",
        "finished_at": "finishedAt",
        "requested_from_ts": "requestedFromTs",
        "requested_to_ts": "requestedToTs",
        "requested_from_block": "requestedFromBlock",
        "requested_to_block": "requestedToBlock",
        "markets_total": "marketsTotal",
        "markets_complete": "marketsComplete",
        "rows_written": "rowsWritten",
        "error_count": "errorCount",
        "last_error": "lastError",
        "price_source": "priceSource",
        "backtest_engine": "backtestEngine",
        "from_ts": "fromTs",
        "to_ts": "toTs",
        "from_block": "fromBlock",
        "to_block": "toBlock",
        "rows_processed": "rowsProcessed",
        "created_at": "createdAt",
        "entry_threshold": "entryThreshold",
        "exit_threshold": "exitThreshold",
        "stop_loss": "stopLoss",
        "take_profit": "takeProfit",
        "max_holding_bars": "maxHoldingBars",
        "initial_capital": "initialCapital",
        "position_size": "positionSize",
        "fee_bps": "feeBps",
        "slippage_bps": "slippageBps",
        "liquidity_cap_pct": "liquidityCapPct",
        "max_position_notional": "maxPositionNotional",
        "min_fill_pct": "minFillPct",
        "execution_price_mode": "executionPriceMode",
        "execution_profile": "executionProfile",
        "order_role": "orderRole",
        "latency_blocks": "latencyBlocks",
        "adverse_slippage_cents": "adverseSlippageCents",
        "fill_probability_haircut_pct": "fillProbabilityHaircutPct",
        "latency_seconds": "latencySeconds",
        "max_book_staleness_seconds": "maxBookStalenessSeconds",
        "allow_partial_fill": "allowPartialFill",
        "min_fill_size": "minFillSize",
        "reject_on_stale_book": "rejectOnStaleBook",
        "final_valuation_mode": "finalValuationMode",
        "max_entry_price": "maxEntryPrice",
        "min_exit_price": "minExitPrice",
        "buy_limit_price": "buyLimitPrice",
        "sell_limit_price": "sellLimitPrice",
        "settlement_value": "settlementValue",
        "metric_key": "metricKey",
        "metric_name": "metricName",
        "metric_group": "metricGroup",
        "formatted_value": "formattedValue",
        "sort_order": "sortOrder",
        "point_index": "pointIndex",
        "x_axis": "xAxis",
        "x_value": "xValue",
        "drawdown_pct": "drawdownPct",
        "cumulative_return": "cumulativeReturn",
        "trade_id": "tradeId",
        "entry_order_id": "entryOrderId",
        "exit_order_id": "exitOrderId",
        "order_id": "orderId",
        "signal_index": "signalIndex",
        "signal_x": "signalX",
        "submit_x": "submitX",
        "decision_price": "decisionPrice",
        "requested_price": "requestedPrice",
        "order_type": "orderType",
        "no_fill_reason": "noFillReason",
        "ledger_id": "ledgerId",
        "event_type": "eventType",
        "shares_delta": "sharesDelta",
        "cash_delta": "cashDelta",
        "position_after": "positionAfter",
        "cash_after": "cashAfter",
        "entry_x": "entryX",
        "exit_x": "exitX",
        "entry_price": "entryPrice",
        "exit_price": "exitPrice",
        "requested_notional": "requestedNotional",
        "filled_notional": "filledNotional",
        "requested_size": "requestedSize",
        "filled_size": "filledSize",
        "unfilled_size": "unfilledSize",
        "fill_pct": "fillPct",
        "fill_status": "fillStatus",
        "book_snapshot_id": "bookSnapshotId",
        "snapshot_version": "snapshotVersion",
        "staleness_seconds": "stalenessSeconds",
        "staleness_blocks": "stalenessBlocks",
        "avg_fill_price": "avgFillPrice",
        "fill_probability": "fillProbability",
        "block_volume": "blockVolume",
        "trade_count": "tradeCount",
        "available_notional": "availableNotional",
        "execution_source": "executionSource",
        "fee_cost": "feeCost",
        "slippage_cost": "slippageCost",
        "execution_cost": "executionCost",
        "pnl_pct": "pnlPct",
        "holding_bars": "holdingBars",
        "exit_reason": "exitReason",
        "block_rows": "blockRows",
        "frontend_rows": "frontendRows",
        "first_block": "firstBlock",
        "last_block": "lastBlock",
        "latest_block_price": "latestBlockPrice",
        "latest_block_at": "latestBlockAt",
        "first_ts": "firstTs",
        "last_ts": "lastTs",
        "latest_frontend_price": "latestFrontendPrice",
        "latest_frontend_at": "latestFrontendAt",
        "market_title": "marketTitle",
        "item_kind": "itemKind",
        "event_id": "eventId",
        "event_slug": "eventSlug",
        "event_title": "eventTitle",
        "event_category": "eventCategory",
        "event_subcategory": "eventSubcategory",
        "event_image_url": "eventImageUrl",
        "event_icon_url": "eventIconUrl",
        "event": "event",
        "members": "members",
        "outcome_count": "outcomeCount",
        "total_members": "totalMembers",
        "ready_members": "readyMembers",
        "orderfilled_rows": "orderfilledRows",
        "grouping_confidence": "groupingConfidence",
        "coverage_status": "coverageStatus",
        "outcome_key": "outcomeKey",
        "condition_id": "conditionId",
        "end_date": "endDate",
        "outcome_index": "outcomeIndex",
        "outcome_label": "outcomeLabel",
        "buy_yes_token_id": "buyYesTokenId",
        "buy_yes_token_side": "buyYesTokenSide",
        "buy_yes_label": "buyYesLabel",
        "buy_yes_price": "buyYesPrice",
        "buy_no_token_id": "buyNoTokenId",
        "buy_no_token_side": "buyNoTokenSide",
        "buy_no_label": "buyNoLabel",
        "buy_no_price": "buyNoPrice",
        "first_x": "firstX",
        "last_x": "lastX",
        "latest_price": "latestPrice",
        "complement_rows": "complementRows",
        "complement_first_x": "complementFirstX",
        "complement_last_x": "complementLastX",
        "complement_latest_price": "complementLatestPrice",
        "complement_points": "complementPoints",
        "x_axis": "xAxis",
        "yes_probability_close": "yesProbabilityClose",
        "yes_probability_vwap": "yesProbabilityVwap",
        "top_n": "topN",
        "max_points": "maxPoints",
        "source_limit": "sourceLimit",
        "benchmark_id": "benchmarkId",
        "universe_type": "universeType",
        "universe_name": "universeName",
        "strategy_name": "strategyName",
        "data_version": "dataVersion",
        "row_index": "rowIndex",
        "fast_status": "fastStatus",
        "accurate_status": "accurateStatus",
        "fast_pnl": "fastPnl",
        "accurate_pnl": "accuratePnl",
        "pnl_diff": "pnlDiff",
        "fast_fill_block": "fastFillBlock",
        "accurate_fill_block": "accurateFillBlock",
        "data_quality": "dataQuality",
        "artifact_key": "artifactKey",
        "artifact_kind": "artifactKind",
        "queued_count": "queuedCount",
        "running_count": "runningCount",
        "completed_count": "completedCount",
        "failed_count": "failedCount",
        "canceled_count": "canceledCount",
        "oldest_queued_at": "oldestQueuedAt",
        "oldest_queued_age_seconds": "oldestQueuedAgeSeconds",
        "last_completed_at": "lastCompletedAt",
        "last_failed_at": "lastFailedAt",
        "worker_online": "workerOnline",
        "worker_id": "workerId",
        "current_benchmark_id": "currentBenchmarkId",
        "heartbeat_at": "heartbeatAt",
        "started_at": "startedAt",
    }
    result = {mapping.get(key, key): _camel_value(value, mapping) for key, value in row.items()}
    meta = result.get("meta")
    if isinstance(meta, dict):
        fingerprint = meta.get("parameterFingerprint") or meta.get("parameter_fingerprint")
        snapshot = meta.get("parameterSnapshot") or meta.get("parameter_snapshot")
        if fingerprint and "parameterFingerprint" not in result:
            result["parameterFingerprint"] = fingerprint
        if snapshot and "parameterSnapshot" not in result:
            result["parameterSnapshot"] = snapshot
    return result


def _camel_value(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {mapping.get(key, key): _camel_value(inner, mapping) for key, inner in value.items()}
    if isinstance(value, list):
        return [_camel_value(item, mapping) for item in value]
    return _json_value(value)


def _lite_point(point: dict[str, Any]) -> dict[str, Any]:
    keys = ("x", "timestamp", "block_number", "price", "volume", "is_implied")
    return {key: point[key] for key in keys if point.get(key) is not None}


def _apply_point_payload_format(payload: dict[str, Any], point_format: str) -> dict[str, Any]:
    if point_format != "lite":
        return payload
    for outcome in payload.get("outcomes") or []:
        if isinstance(outcome, dict):
            outcome["points"] = [_lite_point(point) for point in outcome.get("points") or [] if isinstance(point, dict)]
            outcome["complement_points"] = [
                _lite_point(point) for point in outcome.get("complement_points") or [] if isinstance(point, dict)
            ]
    return payload


def create_quant_blueprint(helpers: dict) -> Blueprint:
    bp = Blueprint("quant_routes", __name__, url_prefix="/quant")
    route_logger = getattr(helpers.get("app"), "logger", LOGGER)
    get_cached_json = helpers.get("get_cached_json")
    set_cached_json = helpers.get("set_cached_json")
    get_snapshot_payload = helpers.get("get_snapshot_payload")

    def _cache_key(name: str, *, version: int = 1) -> str:
        args = {key: request.args.getlist(key) for key in sorted(request.args.keys())}
        return json.dumps({"name": name, "v": version, "args": args}, sort_keys=True, ensure_ascii=True)

    def _cache_key_for_args(name: str, args: dict[str, str], *, version: int = 1) -> str:
        route_args = {key: [str(args[key])] for key in sorted(args.keys())}
        return json.dumps({"name": name, "v": version, "args": route_args}, sort_keys=True, ensure_ascii=True)

    def _cached_quant_payload(namespace: str, cache_key: str, ttl_seconds: int, builder, *, snapshot: bool = False):
        if snapshot and callable(get_snapshot_payload):
            return get_snapshot_payload(namespace, cache_key, builder, ttl_seconds=ttl_seconds)
        if callable(get_cached_json):
            cached = get_cached_json(namespace, cache_key)
            if isinstance(cached, dict):
                return cached
        payload = builder()
        if callable(set_cached_json):
            set_cached_json(namespace, cache_key, payload, ttl_seconds)
        return payload

    def _load_persistent_tile(cache_key: str) -> dict[str, Any] | None:
        try:
            with postgres_connection(PostgresSettings(), readonly=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT payload
                        FROM quant.quant_price_series_tiles
                        WHERE tile_key = %s
                          AND (expires_at IS NULL OR expires_at > now())
                        LIMIT 1
                        """,
                        (cache_key,),
                    )
                    row = cur.fetchone()
        except Exception:
            return None
        payload = row.get("payload") if row else None
        return payload if isinstance(payload, dict) else None

    def _load_seeded_tile(cache_key: str) -> tuple[dict[str, Any] | None, str]:
        if callable(get_cached_json):
            cached = get_cached_json(QUANT_EVENT_TILE_NAMESPACE, cache_key)
            if isinstance(cached, dict):
                return cached, "redis"
        snapshot_store = helpers.get("SNAPSHOT_STORE")
        if snapshot_store is not None:
            try:
                cached = snapshot_store.get(QUANT_EVENT_TILE_NAMESPACE, cache_key)
                if isinstance(cached, dict):
                    return cached, "snapshot"
                stale = snapshot_store.get_stale(QUANT_EVENT_TILE_NAMESPACE, cache_key)
                if isinstance(stale, dict):
                    stale_payload = dict(stale)
                    stale_payload["stale"] = True
                    stale_payload["cacheStatus"] = "stale"
                    return stale_payload, "snapshot-stale"
            except Exception:
                route_logger.exception("quant-event-tile snapshot lookup failed")
        persisted = _load_persistent_tile(cache_key)
        if isinstance(persisted, dict):
            return persisted, "postgres"
        return None, "miss"

    def _price_tile_cache_key(name: str, args: dict[str, Any]) -> str:
        normalized = {key: str(value) for key, value in args.items() if value is not None}
        route_args = {key: [normalized[key]] for key in sorted(normalized.keys())}
        return json.dumps(
            {"name": name, "v": QUANT_PRICE_TILE_KEY_VERSION, "args": route_args},
            sort_keys=True,
            ensure_ascii=True,
        )

    def _load_price_tile(cache_key: str) -> tuple[dict[str, Any] | None, str]:
        if callable(get_cached_json):
            cached = get_cached_json(QUANT_PRICE_TILE_NAMESPACE, cache_key)
            if isinstance(cached, dict):
                return cached, "redis"
        snapshot_store = helpers.get("SNAPSHOT_STORE")
        if snapshot_store is not None:
            try:
                cached = snapshot_store.get(QUANT_PRICE_TILE_NAMESPACE, cache_key)
                if isinstance(cached, dict):
                    return cached, "snapshot"
                stale = snapshot_store.get_stale(QUANT_PRICE_TILE_NAMESPACE, cache_key)
                if isinstance(stale, dict):
                    stale_payload = dict(stale)
                    stale_payload["stale"] = True
                    stale_payload["cacheStatus"] = "stale"
                    return stale_payload, "snapshot-stale"
            except Exception:
                route_logger.exception("quant price tile snapshot lookup failed")
        persisted = _load_persistent_tile(cache_key)
        if isinstance(persisted, dict):
            return persisted, "postgres"
        return None, "miss"

    def _payload_bounds(payload: dict[str, Any]) -> tuple[int, int, int]:
        xs: list[int] = []
        point_count = 0
        for outcome in payload.get("outcomes") or []:
            if not isinstance(outcome, dict):
                continue
            for point_list_name in ("points", "complementPoints", "complement_points"):
                for point in outcome.get(point_list_name) or []:
                    if not isinstance(point, dict):
                        continue
                    raw_x = point.get("x") or point.get("blockNumber") or point.get("block_number") or point.get("timestamp")
                    try:
                        x_value = int(raw_x)
                    except (TypeError, ValueError):
                        continue
                    xs.append(x_value)
                    point_count += 1
        if not xs:
            return 0, 0, 0
        return min(xs), max(xs), point_count

    def _store_price_tile(
        cache_key: str,
        payload: dict[str, Any],
        *,
        entity_type: str,
        entity_slug: str,
        price_source: str,
        range_name: str,
        resolution: str,
        top_n: int,
        max_points: int,
        window_from_x: int | None,
        window_to_x: int | None,
        point_format: str,
        ttl_seconds: int,
        reason: str,
    ) -> None:
        if callable(set_cached_json):
            try:
                set_cached_json(QUANT_PRICE_TILE_NAMESPACE, cache_key, payload, ttl_seconds)
            except Exception:
                route_logger.exception("quant price tile redis store failed slug=%s", entity_slug)
        try:
            payload_json = json.dumps(payload, ensure_ascii=True, default=str)
            data_min_x, data_max_x, row_count = _payload_bounds(payload)
            with postgres_connection(PostgresSettings(), readonly=False) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO quant.quant_price_series_tiles (
                            tile_key, key_version, entity_type, tile_kind, scope, entity_slug,
                            price_source, range_name, resolution, point_format, top_n, max_points,
                            window_from_x, window_to_x, payload, payload_bytes, row_count,
                            data_min_x, data_max_x, cache_ttl_seconds, updated_reason,
                            expires_at, updated_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s::jsonb, %s, %s,
                            %s, %s, %s, %s,
                            now() + (%s || ' seconds')::interval, now()
                        )
                        ON CONFLICT (tile_key) DO UPDATE SET
                            key_version = EXCLUDED.key_version,
                            entity_type = EXCLUDED.entity_type,
                            tile_kind = EXCLUDED.tile_kind,
                            scope = EXCLUDED.scope,
                            entity_slug = EXCLUDED.entity_slug,
                            price_source = EXCLUDED.price_source,
                            range_name = EXCLUDED.range_name,
                            resolution = EXCLUDED.resolution,
                            point_format = EXCLUDED.point_format,
                            top_n = EXCLUDED.top_n,
                            max_points = EXCLUDED.max_points,
                            window_from_x = EXCLUDED.window_from_x,
                            window_to_x = EXCLUDED.window_to_x,
                            payload = EXCLUDED.payload,
                            payload_bytes = EXCLUDED.payload_bytes,
                            row_count = EXCLUDED.row_count,
                            data_min_x = EXCLUDED.data_min_x,
                            data_max_x = EXCLUDED.data_max_x,
                            cache_ttl_seconds = EXCLUDED.cache_ttl_seconds,
                            updated_reason = EXCLUDED.updated_reason,
                            expires_at = EXCLUDED.expires_at,
                            updated_at = now()
                        """,
                        (
                            cache_key,
                            QUANT_PRICE_TILE_KEY_VERSION,
                            entity_type,
                            "series",
                            entity_type,
                            entity_slug,
                            price_source,
                            range_name,
                            resolution,
                            point_format,
                            top_n,
                            max_points,
                            window_from_x,
                            window_to_x,
                            payload_json,
                            len(payload_json),
                            row_count,
                            data_min_x or None,
                            data_max_x or None,
                            ttl_seconds,
                            reason,
                            ttl_seconds,
                        ),
                    )
                conn.commit()
        except Exception:
            route_logger.exception("quant price tile persistent store failed slug=%s", entity_slug)

    def _redis_prefix() -> str:
        app_obj = helpers.get("app")
        settings = getattr(app_obj, "config", {}).get("POLYDATA_SETTINGS") if app_obj is not None else None
        return str(getattr(settings, "redis_prefix", "polydata:") or "")

    def _enqueue_event_tile_warm(args: dict[str, str]) -> bool:
        getter = helpers.get("get_redis_client")
        if not callable(getter):
            return False
        try:
            client = getter()
            if client is None:
                return False
            payload = {
                **args,
                "requested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            client.sadd(f"{_redis_prefix()}{QUANT_EVENT_TILE_WARM_KEY}", json.dumps(payload, sort_keys=True, ensure_ascii=True))
            client.expire(f"{_redis_prefix()}{QUANT_EVENT_TILE_WARM_KEY}", 3600)
            return True
        except Exception:
            route_logger.exception("quant-event-tile warm enqueue failed event_slug=%s", args.get("event_slug"))
            return False

    def _event_tile_args(
        *,
        event_slug: str,
        price_source: str,
        limit: int,
        max_outcomes: int,
        top_n: int,
        max_points: int,
        tile_range: str,
        resolution: str,
        point_format: str,
    ) -> dict[str, str]:
        return {
            "event_slug": event_slug,
            "price_source": price_source,
            "limit": str(limit),
            "max_outcomes": str(max_outcomes),
            "top_n": str(top_n),
            "max_points": str(max_points),
            "range": tile_range,
            "resolution": resolution,
            "point_format": point_format,
        }

    def _json_response(payload: dict[str, Any], *, status_code: int = 200, headers: dict[str, str] | None = None):
        response = jsonify(payload)
        response.status_code = status_code
        for key, value in (headers or {}).items():
            response.headers[key] = value
        return response

    @bp.route("/markets", methods=["GET"])
    def api_quant_price_markets():
        limit = min(max(_parse_int_arg("limit", 50) or 50, 1), 200)
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_quant_price_markets(
                conn,
                search=(request.args.get("q") or "").strip() or None,
                token_side=(request.args.get("token_side") or "").strip() or None,
                limit=limit,
            )
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/events", methods=["GET"])
    def api_quant_price_events():
        limit = min(max(_parse_int_arg("limit", 50) or 50, 1), 200)
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_quant_price_events(
                conn,
                search=(request.args.get("q") or "").strip() or None,
                limit=limit,
            )
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/event-members", methods=["GET"])
    def api_quant_event_members():
        event_slug = (request.args.get("event_slug") or request.args.get("market_slug") or "").strip()
        if not event_slug:
            return jsonify({"error": "event_slug is required"}), 400
        limit = min(max(_parse_int_arg("limit", 200) or 200, 1), 500)
        cache_key = _price_tile_cache_key("event-members", {"event_slug": event_slug, "limit": limit})

        def build_payload() -> dict[str, Any]:
            with postgres_connection(PostgresSettings(), readonly=True) as conn:
                rows = get_quant_event_members(conn, event_slug=event_slug, limit=limit)
            return {
                "eventSlug": event_slug,
                "items": [_camel_row(row) for row in rows],
                "count": len(rows),
                "cacheKey": cache_key,
            }

        payload = _cached_quant_payload(QUANT_EVENT_MEMBERS_NAMESPACE, cache_key, 300, build_payload, snapshot=True)
        return _json_response(payload, headers={"X-Quant-Cache": "event-members"})

    @bp.route("/frontend-prices", methods=["GET"])
    def api_quant_frontend_prices():
        limit = min(max(_parse_int_arg("limit", 1000) or 1000, 1), 25000)
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_frontend_prices(
                conn,
                market_slug=(request.args.get("market_slug") or "").strip() or None,
                token_side=(request.args.get("token_side") or "").strip() or None,
                token_id=(request.args.get("token_id") or "").strip() or None,
                from_ts=_parse_time_arg("from"),
                to_ts=_parse_time_arg("to"),
                limit=limit,
            )
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows), "source": "frontend"})

    @bp.route("/block-close-prices", methods=["GET"])
    def api_quant_block_close_prices():
        limit = min(max(_parse_int_arg("limit", 1000) or 1000, 1), 25000)
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_block_close_prices(
                conn,
                market_slug=(request.args.get("market_slug") or "").strip() or None,
                token_side=(request.args.get("token_side") or "").strip() or None,
                token_id=(request.args.get("token_id") or "").strip() or None,
                from_block=_parse_int_arg("from_block"),
                to_block=_parse_int_arg("to_block"),
                limit=limit,
            )
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows), "source": "orderfilled_block_close"})

    @bp.route("/market-price-series", methods=["GET"])
    def api_quant_market_price_series():
        market_slug = (request.args.get("market_slug") or "").strip()
        if not market_slug:
            return jsonify({"error": "market_slug is required"}), 400
        limit = min(max(_parse_int_arg("limit", 2500) or 2500, 1), 25000)
        max_outcomes = min(max(_parse_int_arg("max_outcomes", 24) or 24, 1), 100)
        max_points = min(max(_parse_int_arg("max_points", 900) or 900, 50), 2500)
        price_source = (request.args.get("price_source") or request.args.get("source") or "orderfilled_block_close").strip()
        point_format = (request.args.get("point_format") or "lite").strip().lower()
        scope = (request.args.get("scope") or "auto").strip().lower()
        token_id = (request.args.get("token_id") or "").strip() or None
        cache_key = _cache_key("market-price-series", version=2)
        live_request = _parse_bool_arg("live")

        def build_payload() -> dict[str, Any]:
            with postgres_connection(PostgresSettings(), readonly=True) as conn:
                payload = get_market_price_series(
                    conn,
                    market_slug=market_slug,
                    price_source=price_source,
                    scope=scope,
                    token_id=token_id,
                    token_side=(request.args.get("token_side") or "").strip() or None,
                    from_ts=_parse_time_arg("from"),
                    to_ts=_parse_time_arg("to"),
                    from_block=_parse_int_arg("from_block"),
                    to_block=_parse_int_arg("to_block"),
                    limit=limit,
                    max_outcomes=max_outcomes,
                    max_points=max_points,
                )
            return _camel_row(_apply_point_payload_format(payload, point_format))

        return jsonify(_cached_quant_payload("quant-market-series", cache_key, 2 if live_request else 12, build_payload))

    @bp.route("/event-price-series", methods=["GET"])
    def api_quant_event_price_series():
        event_slug = (request.args.get("event_slug") or request.args.get("market_slug") or "").strip()
        if not event_slug:
            return jsonify({"error": "event_slug is required"}), 400
        limit = min(max(_parse_int_arg("limit", 2500) or 2500, 1), 25000)
        max_outcomes = min(max(_parse_int_arg("max_outcomes", 100) or 100, 1), 200)
        price_source = (request.args.get("price_source") or request.args.get("source") or "orderfilled_block_close").strip()
        point_format = (request.args.get("point_format") or "lite").strip().lower()
        cache_key = _cache_key("event-price-series", version=2)
        live_request = _parse_bool_arg("live")

        def build_payload() -> dict[str, Any]:
            with postgres_connection(PostgresSettings(), readonly=True) as conn:
                payload = get_event_price_series(
                    conn,
                    event_slug=event_slug,
                    price_source=price_source,
                    from_ts=_parse_time_arg("from"),
                    to_ts=_parse_time_arg("to"),
                    from_block=_parse_int_arg("from_block"),
                    to_block=_parse_int_arg("to_block"),
                    limit=limit,
                    max_outcomes=max_outcomes,
                )
            return _camel_row(_apply_point_payload_format(payload, point_format))

        return jsonify(_cached_quant_payload("quant-event-series", cache_key, 2 if live_request else 12, build_payload))

    @bp.route("/event-price-tile", methods=["GET"])
    def api_quant_event_price_tile():
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        event_slug = (request.args.get("event_slug") or request.args.get("market_slug") or "").strip()
        if not event_slug:
            return jsonify({"error": "event_slug is required"}), 400
        tile_range = _canonical_tile_range(request.args.get("range"))
        limit_cap = 250000 if tile_range == "full" else 25000
        limit = min(max(_parse_int_arg("limit", 2500) or 2500, 1), limit_cap)
        max_outcomes = min(max(_parse_int_arg("max_outcomes", 100) or 100, 1), 200)
        max_points = min(max(_parse_int_arg("max_points", 600) or 600, 50), 2500)
        top_n = min(max(_parse_int_arg("top_n", 12) or 12, 1), max_outcomes)
        price_source = (request.args.get("price_source") or request.args.get("source") or "orderfilled_block_close").strip()
        point_format = (request.args.get("point_format") or "lite").strip().lower()
        resolution = (request.args.get("resolution") or "auto").strip().lower()
        live_request = _parse_bool_arg("live")
        request_args = _event_tile_args(
            event_slug=event_slug,
            price_source=price_source,
            limit=limit,
            max_outcomes=max_outcomes,
            top_n=top_n,
            max_points=max_points,
            tile_range=tile_range,
            resolution=resolution,
            point_format=point_format,
        )
        cache_key = _cache_key_for_args("event-price-tile", request_args, version=4)
        lookup_started = time.perf_counter()
        persisted, cache_layer = (None, "live") if live_request else _load_seeded_tile(cache_key)
        lookup_ms = int((time.perf_counter() - lookup_started) * 1000)
        if persisted is not None:
            payload = dict(persisted)
            payload["cacheHit"] = True
            payload["cacheLayer"] = cache_layer
            payload.setdefault("status", "ok")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            route_logger.info(
                "quant-event-tile request_id=%s status=hit cache_layer=%s event_slug=%s source=%s range=%s resolution=%s top_n=%s max_outcomes=%s max_points=%s lookup_ms=%s total_ms=%s bytes=%s",
                request_id,
                cache_layer,
                event_slug,
                price_source,
                tile_range,
                resolution,
                top_n,
                max_outcomes,
                max_points,
                lookup_ms,
                elapsed_ms,
                len(json.dumps(payload, ensure_ascii=True, default=str)),
            )
            return _json_response(payload, headers={
                "X-Quant-Cache": cache_layer,
                "X-Quant-Request-Id": request_id,
                "X-Quant-Elapsed-Ms": str(elapsed_ms),
            })

        warm_enqueued = _enqueue_event_tile_warm(request_args)
        head_started = time.perf_counter()
        try:
            with postgres_connection(PostgresSettings(), readonly=True) as conn:
                payload = get_event_price_head(
                    conn,
                    event_slug=event_slug,
                    price_source=price_source,
                    from_ts=_parse_time_arg("from"),
                    to_ts=_parse_time_arg("to"),
                    from_block=_parse_int_arg("from_block"),
                    to_block=_parse_int_arg("to_block"),
                    max_outcomes=max_outcomes,
                    top_n=top_n,
                )
            payload = _camel_row(_apply_point_payload_format(payload, point_format))
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            route_logger.exception(
                "quant-event-tile request_id=%s status=error event_slug=%s source=%s lookup_ms=%s total_ms=%s",
                request_id,
                event_slug,
                price_source,
                lookup_ms,
                elapsed_ms,
            )
            return _json_response({
                "status": "warming",
                "cacheHit": False,
                "cacheStatus": "warming",
                "warming": True,
                "eventSlug": event_slug,
                "priceSource": price_source,
                "message": "Price tile is warming. Retry shortly.",
                "error": str(exc),
                "retryAfterMs": 1500,
                "requestId": request_id,
            }, status_code=200, headers={
                "X-Quant-Cache": "warming",
                "X-Quant-Request-Id": request_id,
                "X-Quant-Elapsed-Ms": str(elapsed_ms),
            })
        head_ms = int((time.perf_counter() - head_started) * 1000)
        payload["status"] = "warming" if not (payload.get("outcomes") or []) else "partial"
        payload["cacheHit"] = False
        payload["cacheStatus"] = "warming"
        payload["warming"] = True
        payload["retryAfterMs"] = 1500
        payload["requestId"] = request_id
        payload["message"] = "Historical price tile is warming; latest outcome snapshot is returned."
        payload.setdefault("tile", {})
        if isinstance(payload["tile"], dict):
            payload["tile"].update({
                "cache_key": cache_key,
                "status": "warming",
                "warm_enqueued": warm_enqueued,
                "range": tile_range,
                "resolution": resolution,
                "top_n": top_n,
                "max_points": max_points,
            })
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        route_logger.info(
            "quant-event-tile request_id=%s status=warming event_slug=%s source=%s range=%s resolution=%s top_n=%s max_outcomes=%s max_points=%s lookup_ms=%s head_ms=%s total_ms=%s warm_enqueued=%s outcomes=%s bytes=%s",
            request_id,
            event_slug,
            price_source,
            tile_range,
            resolution,
            top_n,
            max_outcomes,
            max_points,
            lookup_ms,
            head_ms,
            elapsed_ms,
            warm_enqueued,
            len(payload.get("outcomes") or []),
            len(json.dumps(payload, ensure_ascii=True, default=str)),
        )
        return _json_response(payload, headers={
            "X-Quant-Cache": "warming",
            "X-Quant-Request-Id": request_id,
            "X-Quant-Elapsed-Ms": str(elapsed_ms),
            "Retry-After": "2",
        })

    @bp.route("/event-price-head", methods=["GET"])
    def api_quant_event_price_head():
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        event_slug = (request.args.get("event_slug") or request.args.get("market_slug") or "").strip()
        if not event_slug:
            return jsonify({"error": "event_slug is required"}), 400
        max_outcomes = min(max(_parse_int_arg("max_outcomes", 100) or 100, 1), 200)
        top_n = min(max(_parse_int_arg("top_n", 12) or 12, 1), max_outcomes)
        price_source = (request.args.get("price_source") or request.args.get("source") or "orderfilled_block_close").strip()
        point_format = (request.args.get("point_format") or "lite").strip().lower()
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            payload = get_event_price_head(
                conn,
                event_slug=event_slug,
                price_source=price_source,
                from_ts=_parse_time_arg("from"),
                to_ts=_parse_time_arg("to"),
                from_block=_parse_int_arg("from_block"),
                to_block=_parse_int_arg("to_block"),
                max_outcomes=max_outcomes,
                top_n=top_n,
            )
        payload = _camel_row(_apply_point_payload_format(payload, point_format))
        payload["requestId"] = request_id
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _json_response(payload, headers={
            "X-Quant-Cache": "head",
            "X-Quant-Request-Id": request_id,
            "X-Quant-Elapsed-Ms": str(elapsed_ms),
        })

    @bp.route("/entity-snapshot", methods=["GET"])
    def api_quant_entity_snapshot():
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        entity_type = (request.args.get("entity_type") or request.args.get("kind") or "auto").strip().lower()
        event_slug = (request.args.get("event_slug") or "").strip()
        market_slug = (request.args.get("market_slug") or request.args.get("slug") or "").strip()
        if entity_type not in {"event", "market"}:
            entity_type = "event" if event_slug else "market"
        entity_slug = event_slug if entity_type == "event" else market_slug
        if not entity_slug:
            return jsonify({"error": "slug is required"}), 400
        max_outcomes = min(max(_parse_int_arg("max_outcomes", 100) or 100, 1), 200)
        top_n = min(max(_parse_int_arg("top_n", 12) or 12, 1), max_outcomes)
        price_source = (request.args.get("price_source") or request.args.get("source") or "orderfilled_block_close").strip()
        point_format = (request.args.get("point_format") or "lite").strip().lower()
        cache_key = _price_tile_cache_key(
            "entity-snapshot",
            {
                "entity_type": entity_type,
                "entity_slug": entity_slug,
                "price_source": price_source,
                "max_outcomes": max_outcomes,
                "top_n": top_n,
                "point_format": point_format,
            },
        )

        def build_payload() -> dict[str, Any]:
            with postgres_connection(PostgresSettings(), readonly=True) as conn:
                if entity_type == "event":
                    payload = get_event_price_head(
                        conn,
                        event_slug=entity_slug,
                        price_source=price_source,
                        max_outcomes=max_outcomes,
                        top_n=top_n,
                    )
                else:
                    payload = get_market_price_series(
                        conn,
                        market_slug=entity_slug,
                        price_source=price_source,
                        scope="auto",
                        limit=1,
                        max_outcomes=min(max_outcomes, 24),
                        max_points=50,
                    )
            payload = _camel_row(_apply_point_payload_format(payload, point_format))
            payload["snapshot"] = True
            payload["cacheKey"] = cache_key
            payload["requestId"] = request_id
            return payload

        ttl = 3 if _parse_bool_arg("live") else 20
        payload = _cached_quant_payload(QUANT_ENTITY_SNAPSHOT_NAMESPACE, cache_key, ttl, build_payload, snapshot=True)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        payload = dict(payload)
        payload.setdefault("requestId", request_id)
        payload.setdefault("snapshot", True)
        return _json_response(payload, headers={
            "X-Quant-Cache": "snapshot",
            "X-Quant-Request-Id": request_id,
            "X-Quant-Elapsed-Ms": str(elapsed_ms),
        })

    @bp.route("/price-window", methods=["GET"])
    def api_quant_price_window():
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        entity_type = (request.args.get("entity_type") or request.args.get("kind") or "auto").strip().lower()
        event_slug = (request.args.get("event_slug") or "").strip()
        market_slug = (request.args.get("market_slug") or request.args.get("slug") or "").strip()
        if entity_type not in {"event", "market"}:
            entity_type = "event" if event_slug else "market"
        entity_slug = event_slug if entity_type == "event" else market_slug
        if not entity_slug:
            return jsonify({"error": "slug is required"}), 400
        price_source = (request.args.get("price_source") or request.args.get("source") or "orderfilled_block_close").strip()
        range_name = _canonical_price_range(request.args.get("range"))
        resolution = (request.args.get("resolution") or "auto").strip().lower()
        point_format = (request.args.get("point_format") or "lite").strip().lower()
        from_block = _parse_int_arg("from_block")
        to_block = _parse_int_arg("to_block")
        from_ts = _parse_time_arg("from")
        to_ts = _parse_time_arg("to")
        if from_block is not None or to_block is not None or from_ts is not None or to_ts is not None:
            range_name = "window"
        max_outcomes = min(max(_parse_int_arg("max_outcomes", 100) or 100, 1), 200)
        top_n = min(max(_parse_int_arg("top_n", 12) or 12, 1), max_outcomes)
        full_window = range_name == "full"
        max_points_default = _viewport_max_points(1200 if full_window else 600, full=full_window)
        max_points = min(max(_parse_int_arg("max_points", max_points_default) or max_points_default, 80), 2500)
        limit_default = 250000 if full_window else max(2500, max_points * 8)
        limit_cap = 250000 if full_window else 50000
        limit = min(max(_parse_int_arg("limit", limit_default) or limit_default, 1), limit_cap)
        cache_ttl = 12 if range_name in {"latest", "window"} else 900
        if _parse_bool_arg("live"):
            cache_ttl = 2
        window_from_x = from_block if price_source == "orderfilled_block_close" else from_ts
        window_to_x = to_block if price_source == "orderfilled_block_close" else to_ts
        key_args = {
            "entity_type": entity_type,
            "entity_slug": entity_slug,
            "price_source": price_source,
            "range": range_name,
            "resolution": resolution,
            "top_n": top_n,
            "max_outcomes": max_outcomes,
            "max_points": max_points,
            "from_block": from_block,
            "to_block": to_block,
            "from": from_ts,
            "to": to_ts,
            "point_format": point_format,
        }
        cache_key = _price_tile_cache_key("price-window", key_args)
        live_request = _parse_bool_arg("live")
        cached, cache_layer = (None, "live") if live_request else _load_price_tile(cache_key)
        if cached is not None:
            payload = dict(cached)
            payload["cacheHit"] = True
            payload["cacheLayer"] = cache_layer
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return _json_response(payload, headers={
                "X-Quant-Cache": cache_layer,
                "X-Quant-Request-Id": request_id,
                "X-Quant-Elapsed-Ms": str(elapsed_ms),
            })

        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            if entity_type == "event":
                payload = get_event_price_tile(
                    conn,
                    event_slug=entity_slug,
                    price_source=price_source,
                    from_ts=from_ts,
                    to_ts=to_ts,
                    from_block=from_block,
                    to_block=to_block,
                    limit=limit,
                    max_outcomes=max_outcomes,
                    top_n=top_n,
                    max_points=max_points,
                    tile_range="full" if full_window else "latest",
                    resolution=resolution,
                )
            else:
                payload = get_market_price_series(
                    conn,
                    market_slug=entity_slug,
                    price_source=price_source,
                    scope="auto",
                    from_ts=from_ts,
                    to_ts=to_ts,
                    from_block=from_block,
                    to_block=to_block,
                    limit=limit,
                    max_outcomes=min(max_outcomes, 24),
                    max_points=max_points,
                )
        payload = _camel_row(_apply_point_payload_format(payload, point_format))
        payload["cacheHit"] = False
        payload["cacheLayer"] = "miss"
        payload["requestId"] = request_id
        payload.setdefault("tile", {})
        if isinstance(payload["tile"], dict):
            payload["tile"].update({
                "cacheKey": cache_key,
                "entityType": entity_type,
                "entitySlug": entity_slug,
                "range": range_name,
                "resolution": resolution,
                "topN": top_n,
                "maxPoints": max_points,
                "fromX": window_from_x,
                "toX": window_to_x,
            })
        _store_price_tile(
            cache_key,
            payload,
            entity_type=entity_type,
            entity_slug=entity_slug,
            price_source=price_source,
            range_name=range_name,
            resolution=resolution,
            top_n=top_n,
            max_points=max_points,
            window_from_x=window_from_x,
            window_to_x=window_to_x,
            point_format=point_format,
            ttl_seconds=cache_ttl,
            reason="api-price-window",
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _json_response(payload, headers={
            "X-Quant-Cache": "miss",
            "X-Quant-Request-Id": request_id,
            "X-Quant-Elapsed-Ms": str(elapsed_ms),
        })

    @bp.route("/event-price-stream", methods=["GET"])
    def api_quant_event_price_stream():
        event_slug = (request.args.get("event_slug") or request.args.get("market_slug") or "").strip()
        if not event_slug:
            return jsonify({"error": "event_slug is required"}), 400
        price_source = (request.args.get("price_source") or request.args.get("source") or "orderfilled_block_close").strip()
        interval_seconds = min(max(_parse_int_arg("interval", 5) or 5, 1), 60)
        max_outcomes = min(max(_parse_int_arg("max_outcomes", 24) or 24, 1), 100)

        def build_latest_payload() -> dict[str, Any]:
            with postgres_connection(PostgresSettings(), readonly=True) as conn:
                payload = get_event_price_tile(
                    conn,
                    event_slug=event_slug,
                    price_source=price_source,
                    limit=1,
                    max_outcomes=max_outcomes,
                    top_n=max_outcomes,
                    max_points=50,
                    tile_range="latest",
                    resolution="latest",
                )
            payload = _camel_row(_apply_point_payload_format(payload, "lite"))
            return {
                "eventSlug": event_slug,
                "priceSource": price_source,
                "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "outcomes": [
                    {
                        "tokenId": outcome.get("tokenId"),
                        "outcomeLabel": outcome.get("outcomeLabel"),
                        "buyYesPrice": outcome.get("buyYesPrice"),
                        "buyNoPrice": outcome.get("buyNoPrice"),
                        "latestPrice": outcome.get("latestPrice"),
                        "lastX": outcome.get("lastX"),
                        "rows": outcome.get("rows"),
                    }
                    for outcome in payload.get("outcomes") or []
                ],
            }

        @stream_with_context
        def generate():
            last_serialized = ""
            while True:
                try:
                    serialized = json.dumps(build_latest_payload(), ensure_ascii=True, default=str)
                    if serialized != last_serialized:
                        last_serialized = serialized
                        yield f"event: price\\ndata: {serialized}\\n\\n"
                    else:
                        yield ": keepalive\\n\\n"
                except GeneratorExit:
                    return
                except Exception as exc:
                    serialized_error = json.dumps({"error": str(exc), "eventSlug": event_slug}, ensure_ascii=True)
                    yield f"event: error\\ndata: {serialized_error}\\n\\n"
                time.sleep(interval_seconds)

        return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @bp.route("/price-stream", methods=["GET"])
    def api_quant_price_stream():
        entity_type = (request.args.get("entity_type") or request.args.get("kind") or "auto").strip().lower()
        event_slug = (request.args.get("event_slug") or "").strip()
        market_slug = (request.args.get("market_slug") or request.args.get("slug") or "").strip()
        if entity_type not in {"event", "market"}:
            entity_type = "event" if event_slug else "market"
        entity_slug = event_slug if entity_type == "event" else market_slug
        if not entity_slug:
            return jsonify({"error": "slug is required"}), 400
        price_source = (request.args.get("price_source") or request.args.get("source") or "orderfilled_block_close").strip()
        interval_seconds = min(max(_parse_int_arg("interval", 2) or 2, 1), 60)
        max_outcomes = min(max(_parse_int_arg("max_outcomes", 24) or 24, 1), 100)

        def build_latest_payload() -> dict[str, Any]:
            with postgres_connection(PostgresSettings(), readonly=True) as conn:
                if entity_type == "event":
                    payload = get_event_price_tile(
                        conn,
                        event_slug=entity_slug,
                        price_source=price_source,
                        limit=1,
                        max_outcomes=max_outcomes,
                        top_n=max_outcomes,
                        max_points=50,
                        tile_range="latest",
                        resolution="latest",
                    )
                else:
                    payload = get_market_price_series(
                        conn,
                        market_slug=entity_slug,
                        price_source=price_source,
                        scope="auto",
                        limit=1,
                        max_outcomes=min(max_outcomes, 24),
                        max_points=50,
                    )
            payload = _camel_row(_apply_point_payload_format(payload, "lite"))
            return {
                "entityType": entity_type,
                "eventSlug": entity_slug if entity_type == "event" else None,
                "marketSlug": entity_slug if entity_type == "market" else None,
                "priceSource": price_source,
                "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "outcomes": [
                    {
                        "tokenId": outcome.get("tokenId"),
                        "outcomeLabel": outcome.get("outcomeLabel"),
                        "buyYesTokenId": outcome.get("buyYesTokenId"),
                        "buyNoTokenId": outcome.get("buyNoTokenId"),
                        "buyYesPrice": outcome.get("buyYesPrice"),
                        "buyNoPrice": outcome.get("buyNoPrice"),
                        "latestPrice": outcome.get("latestPrice"),
                        "lastX": outcome.get("lastX"),
                        "rows": outcome.get("rows"),
                    }
                    for outcome in payload.get("outcomes") or []
                ],
            }

        @stream_with_context
        def generate():
            last_serialized = ""
            while True:
                try:
                    serialized = json.dumps(build_latest_payload(), ensure_ascii=True, default=str)
                    if serialized != last_serialized:
                        last_serialized = serialized
                        yield f"event: price\\ndata: {serialized}\\n\\n"
                    else:
                        yield ": keepalive\\n\\n"
                except GeneratorExit:
                    return
                except Exception as exc:
                    serialized_error = json.dumps({"error": str(exc), "slug": entity_slug}, ensure_ascii=True)
                    yield f"event: error\\ndata: {serialized_error}\\n\\n"
                time.sleep(interval_seconds)

        return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @bp.route("/price-build-status", methods=["GET"])
    def api_quant_price_build_status():
        limit = min(max(_parse_int_arg("limit", 100) or 100, 1), 1000)
        source = (request.args.get("source") or "").strip() or None
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_price_build_status(conn, source=source, limit=limit)
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/backtest-runs", methods=["POST"])
    def api_quant_create_backtest_run():
        payload = request.get_json(silent=True) or {}
        try:
            with postgres_connection(PostgresSettings(), readonly=False) as conn:
                create_schema(conn)
                created = create_and_execute_backtest(conn, payload)
                conn.commit()
                run_id = int(created["run_id"])
                row = get_backtest_run(conn, run_id=run_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        if not row:
            return jsonify({"error": "backtest run not found"}), 500
        return jsonify({"item": _camel_row(row), "runId": row.get("run_id"), "status": row.get("status")}), 202

    @bp.route("/backtest-runs", methods=["GET"])
    def api_quant_list_backtest_runs():
        limit = min(max(_parse_int_arg("limit", 25) or 25, 1), 100)
        market_slug = (request.args.get("market_slug") or request.args.get("marketSlug") or "").strip() or None
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_backtest_runs(conn, market_slug=market_slug, limit=limit)
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/backtest-runs/<int:run_id>", methods=["GET"])
    def api_quant_get_backtest_run(run_id: int):
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            row = get_backtest_run(conn, run_id=run_id)
        if not row:
            return jsonify({"error": "backtest run not found"}), 404
        return jsonify({"item": _camel_row(row)})

    @bp.route("/backtest-runs/<int:run_id>/trades", methods=["GET"])
    def api_quant_get_backtest_trades(run_id: int):
        limit = min(max(_parse_int_arg("limit", 1000) or 1000, 1), 25000)
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_backtest_trades(conn, run_id=run_id, limit=limit)
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/backtest-runs/<int:run_id>/orders", methods=["GET"])
    def api_quant_get_backtest_orders(run_id: int):
        limit = min(max(_parse_int_arg("limit", 1000) or 1000, 1), 25000)
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_backtest_orders(conn, run_id=run_id, limit=limit)
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/backtest-runs/<int:run_id>/ledger", methods=["GET"])
    def api_quant_get_backtest_ledger(run_id: int):
        limit = min(max(_parse_int_arg("limit", 1000) or 1000, 1), 25000)
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_backtest_ledger(conn, run_id=run_id, limit=limit)
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/backtest-runs/<int:run_id>/equity", methods=["GET"])
    def api_quant_get_backtest_equity(run_id: int):
        limit = min(max(_parse_int_arg("limit", 25000) or 25000, 1), 25000)
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_backtest_equity(conn, run_id=run_id, limit=limit)
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/backtest-runs/<int:run_id>/metrics", methods=["GET"])
    def api_quant_get_backtest_metrics(run_id: int):
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            rows = get_backtest_metrics(conn, run_id=run_id)
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/backtest-benchmarks", methods=["POST"])
    def api_quant_create_backtest_benchmark():
        payload = request.get_json(silent=True) or {}
        try:
            parts = _benchmark_request_parts(payload)
            with BENCHMARK_DB_POOL.connection(readonly=False) as conn:
                benchmark_id = create_benchmark_run(
                    conn,
                    universe_type=parts["universe_spec"].universe_type,
                    universe_name=parts["universe_spec"].universe_name,
                    market_count=parts["universe_spec"].limit,
                    strategy_name="favorite_hold_v1",
                    parameters=parts["parameters"],
                    profiles=parts["profiles"],
                    status="queued",
                )
                conn.commit()
                row = get_benchmark_run(conn, benchmark_id=int(benchmark_id))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            route_logger.exception("quant backtest benchmark enqueue failed")
            return jsonify({"error": str(exc)}), 500
        if not row:
            return jsonify({"error": "benchmark run not found"}), 500
        return jsonify({
            "item": _camel_row(row),
            "benchmarkId": row.get("benchmark_id"),
            "status": row.get("status"),
            "artifacts": [],
        }), 202

    @bp.route("/backtest-universes", methods=["GET"])
    def api_quant_list_backtest_universes():
        return jsonify({"items": list_supported_universes(), "count": len(list_supported_universes())})

    @bp.route("/backtest-benchmarks", methods=["GET"])
    def api_quant_list_backtest_benchmarks():
        limit = min(max(_parse_int_arg("limit", 25) or 25, 1), 100)
        with BENCHMARK_DB_POOL.connection(readonly=True) as conn:
            rows = list_benchmark_runs(conn, limit=limit)
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/backtest-benchmark-queue", methods=["GET"])
    def api_quant_get_backtest_benchmark_queue():
        with BENCHMARK_DB_POOL.connection(readonly=True) as conn:
            payload = get_benchmark_queue_status(conn)
        return jsonify(_camel_row(payload))

    @bp.route("/backtest-benchmarks/<int:benchmark_id>", methods=["GET"])
    def api_quant_get_backtest_benchmark(benchmark_id: int):
        include_artifacts = _parse_bool_arg("includeArtifacts", _parse_bool_arg("include_artifacts", True))
        with BENCHMARK_DB_POOL.connection(readonly=True) as conn:
            row = get_benchmark_run(conn, benchmark_id=benchmark_id)
            artifacts = get_benchmark_artifacts(conn, benchmark_id=benchmark_id) if include_artifacts else []
        if not row:
            return jsonify({"error": "benchmark not found"}), 404
        return jsonify({"item": _camel_row(row), "artifacts": [_camel_row(item) for item in artifacts]})

    @bp.route("/backtest-benchmarks/<int:benchmark_id>/cancel", methods=["POST"])
    def api_quant_cancel_backtest_benchmark(benchmark_id: int):
        with BENCHMARK_DB_POOL.connection(readonly=False) as conn:
            row = cancel_queued_benchmark_run(conn, benchmark_id=benchmark_id)
        if not row:
            return jsonify({"error": "benchmark is not queued or does not exist"}), 409
        return jsonify({"item": _camel_row(row), "benchmarkId": row.get("benchmark_id"), "status": row.get("status")})

    @bp.route("/backtest-benchmarks/<int:benchmark_id>/retry", methods=["POST"])
    def api_quant_retry_backtest_benchmark(benchmark_id: int):
        with BENCHMARK_DB_POOL.connection(readonly=False) as conn:
            row = retry_benchmark_run(conn, benchmark_id=benchmark_id)
        if not row:
            return jsonify({"error": "benchmark is not failed/canceled or does not exist"}), 409
        return jsonify({"item": _camel_row(row), "benchmarkId": row.get("benchmark_id"), "status": row.get("status")}), 202

    @bp.route("/backtest-benchmarks/<int:benchmark_id>/rows", methods=["GET"])
    def api_quant_get_backtest_benchmark_rows(benchmark_id: int):
        limit = min(max(_parse_int_arg("limit", 10000) or 10000, 1), 25000)
        with BENCHMARK_DB_POOL.connection(readonly=True) as conn:
            rows = get_benchmark_rows(conn, benchmark_id=benchmark_id, limit=limit)
        return jsonify({"items": [_camel_row(row) for row in rows], "count": len(rows)})

    @bp.route("/backtest-replay-coverage/build", methods=["POST"])
    def api_quant_build_backtest_replay_coverage():
        payload = request.get_json(silent=True) or {}
        universe = str(payload.get("universe") or "nba_2024_25_moneyline")
        limit = min(max(int(payload.get("limit") or 500), 1), 500)
        try:
            result = build_replay_coverage(
                universe=universe,
                limit=limit,
                window_start_hours=Decimal(str(payload.get("windowStartHours") or payload.get("window_start_hours") or "25")),
                window_end_hours=Decimal(str(payload.get("windowEndHours") or payload.get("window_end_hours") or "0")),
                force=bool(payload.get("force")),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            route_logger.exception("quant replay coverage build failed")
            return jsonify({"error": str(exc)}), 500
        return jsonify(_camel_row(result)), 202

    return bp
