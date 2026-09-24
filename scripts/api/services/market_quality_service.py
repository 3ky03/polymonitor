from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, cast

from api.context import resolve_service_callable, resolve_service_value


CONTRACT_VERSION = "prediction-market-data-quality.v1"
CACHE_NAMESPACE = "snapshot:market_data_quality"
CACHE_KEY = "v1"
CACHE_TTL_SECONDS = 300


def _service_callable(context: Mapping[str, Any], name: str) -> Callable[..., Any]:
    return cast(Callable[..., Any], resolve_service_callable(context, name))


@dataclass(frozen=True)
class MarketQualityDependencies:
    application: Any
    query_one: Callable[..., Any]
    query_all: Callable[..., Any]
    table_exists: Callable[[str], bool]
    get_snapshot_payload: Callable[..., Any]
    get_recent_oracle_snapshot: Callable[..., Any]
    utc_now_iso: Callable[[], str]

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> MarketQualityDependencies:
        return cls(
            application=resolve_service_value(context, "app"),
            query_one=_service_callable(context, "query_one"),
            query_all=_service_callable(context, "query_all"),
            table_exists=cast(Callable[[str], bool], _service_callable(context, "table_exists")),
            get_snapshot_payload=_service_callable(context, "get_snapshot_payload"),
            get_recent_oracle_snapshot=_service_callable(context, "get_recent_oracle_snapshot"),
            utc_now_iso=cast(Callable[[], str], _service_callable(context, "utc_now_iso")),
        )


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(" GMT") and "," in text:
        try:
            return datetime.strptime(text, "%a, %d %b %Y %H:%M:%S GMT").replace(
                tzinfo=timezone.utc
            ).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return text.replace(" ", "T", 1) if " " in text and "T" not in text else text


def _parse_datetime(value: Any) -> datetime | None:
    text = _iso(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, now: datetime) -> int | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _coverage(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100, 2)


def _coverage_status(value: float | None, *, warning_at: float = 95.0, ok_at: float = 99.0) -> str:
    if value is None:
        return "unknown"
    if value >= ok_at:
        return "ok"
    if value >= warning_at:
        return "warning"
    return "critical"


def _freshness_status(age_seconds: int | None, *, fresh_seconds: int, stale_seconds: int) -> str:
    if age_seconds is None:
        return "missing"
    if age_seconds <= fresh_seconds:
        return "fresh"
    if age_seconds <= stale_seconds:
        return "aging"
    return "stale"


def _freshness_score(status: str) -> float:
    return {
        "fresh": 100.0,
        "aging": 60.0,
        "stale": 0.0,
        "missing": 0.0,
    }.get(status, 0.0)


def _query_one(
    dependencies: MarketQualityDependencies,
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any]:
    try:
        payload = dependencies.query_one(sql, params) if params else dependencies.query_one(sql)
        return dict(payload or {})
    except Exception:
        dependencies.application.logger.exception("market-data-quality aggregate query failed")
        return {}


def _query_all(
    dependencies: MarketQualityDependencies,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    try:
        rows = dependencies.query_all(sql, params) if params else dependencies.query_all(sql)
        return [dict(row) for row in rows or []]
    except Exception:
        dependencies.application.logger.exception("market-data-quality detail query failed")
        return []


def _dimension(
    *,
    dimension_id: str,
    label: str,
    numerator: int,
    denominator: int,
    source: str,
    detail: str,
    observed_at: Any = None,
    warning_at: float = 95.0,
    ok_at: float = 99.0,
) -> dict[str, Any]:
    coverage = _coverage(numerator, denominator)
    return {
        "id": dimension_id,
        "label": label,
        "status": _coverage_status(coverage, warning_at=warning_at, ok_at=ok_at),
        "numerator": numerator,
        "denominator": denominator,
        "coveragePct": coverage,
        "source": source,
        "observedAt": _iso(observed_at),
        "detail": detail,
    }


def _sync_watermarks(
    dependencies: MarketQualityDependencies,
) -> list[dict[str, Any]]:
    if not dependencies.table_exists("sync_state"):
        return []
    rows = _query_all(
        dependencies,
        """
        SELECT key, value, last_block, updated_at
        FROM sync_state
        WHERE key IN (
            'market_sync', 'market_sync_live',
            'trade_sync', 'trade_sync_live',
            'oracle_sync', 'oracle_sync_live'
        )
        ORDER BY key
        """,
    )
    preferred: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw_key = str(row.get("key") or "")
        family = raw_key.replace("_live", "")
        current = preferred.get(family)
        if current is None or raw_key.endswith("_live"):
            value: Any = row.get("value")
            if isinstance(value, str) and value.strip().startswith("{"):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            preferred[family] = {
                "id": family,
                "key": raw_key,
                "lastBlock": row.get("last_block"),
                "updatedAt": _iso(row.get("updated_at")),
                "state": value,
            }
    return [preferred[key] for key in ("market_sync", "trade_sync", "oracle_sync") if key in preferred]


def _build_market_data_quality_payload(
    dependencies: MarketQualityDependencies,
) -> dict[str, Any]:
    generated_at = dependencies.utc_now_iso()
    now = _parse_datetime(generated_at) or datetime.now(timezone.utc)
    market_metrics = (
        _query_one(
            dependencies,
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN condition_id IS NOT NULL AND condition_id <> '' THEN 1 ELSE 0 END) AS condition_bound,
                SUM(CASE WHEN question_id IS NOT NULL AND question_id <> '' THEN 1 ELSE 0 END) AS question_bound,
                SUM(CASE WHEN yes_token_id IS NOT NULL AND yes_token_id <> ''
                          AND no_token_id IS NOT NULL AND no_token_id <> '' THEN 1 ELSE 0 END) AS token_pair,
                SUM(CASE WHEN condition_id IS NOT NULL AND condition_id <> ''
                          AND question_id IS NOT NULL AND question_id <> ''
                          AND yes_token_id IS NOT NULL AND yes_token_id <> ''
                          AND no_token_id IS NOT NULL AND no_token_id <> '' THEN 1 ELSE 0 END) AS fully_identified
            FROM markets
            """,
        )
        if dependencies.table_exists("markets")
        else {}
    )
    serving_metrics = (
        _query_one(
            dependencies,
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN latest_price IS NOT NULL THEN 1 ELSE 0 END) AS priced,
                SUM(CASE WHEN last_trade_at IS NOT NULL THEN 1 ELSE 0 END) AS traded,
                MAX(last_trade_at) AS latest_trade_at,
                MAX(updated_at) AS updated_at
            FROM market_list_serving
            """,
        )
        if dependencies.table_exists("market_list_serving")
        else {}
    )
    active_cutoff = (now - timedelta(days=7)).isoformat()
    active_metrics = (
        _query_one(
            dependencies,
            """
            SELECT COUNT(*) AS total
            FROM market_list_serving
            WHERE last_trade_at >= ?
            """,
            (active_cutoff,),
        )
        if dependencies.table_exists("market_list_serving")
        else {}
    )
    token_metrics = (
        _query_one(
            dependencies,
            """
            SELECT
                COUNT(*) AS tokens,
                COUNT(DISTINCT market_id) AS markets,
                SUM(CASE WHEN active THEN 1 ELSE 0 END) AS active_tokens,
                MAX(updated_at) AS updated_at
            FROM market_tokens
            """,
        )
        if dependencies.table_exists("market_tokens")
        else {}
    )
    oracle_metrics = (
        _query_one(
            dependencies,
            """
            SELECT
                COUNT(*) AS events,
                COUNT(DISTINCT market_id) AS bound_markets,
                SUM(CASE WHEN market_id IS NOT NULL THEN 1 ELSE 0 END) AS bound_events,
                SUM(CASE WHEN market_id IS NULL THEN 1 ELSE 0 END) AS unbound_events,
                SUM(CASE WHEN LOWER(event_status) = 'request' THEN 1 ELSE 0 END) AS request_count,
                SUM(CASE WHEN LOWER(event_status) = 'propose' THEN 1 ELSE 0 END) AS propose_count,
                SUM(CASE WHEN LOWER(event_status) = 'dispute' THEN 1 ELSE 0 END) AS dispute_count,
                SUM(CASE WHEN LOWER(event_status) = 'settle' THEN 1 ELSE 0 END) AS settle_count,
                MAX(event_time) AS latest_event_at,
                MAX(block_number) AS latest_block
            FROM oracle_events
            """,
        )
        if dependencies.table_exists("oracle_events")
        else {}
    )
    resolution_metrics = (
        _query_one(
            dependencies,
            """
            SELECT
                COUNT(*) AS snapshots,
                SUM(CASE WHEN is_trading_closed THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN has_propose THEN 1 ELSE 0 END) AS proposed,
                SUM(CASE WHEN has_dispute THEN 1 ELSE 0 END) AS disputed,
                SUM(CASE WHEN has_settle THEN 1 ELSE 0 END) AS settled,
                SUM(CASE WHEN is_final THEN 1 ELSE 0 END) AS final,
                SUM(CASE WHEN is_trading_closed AND NOT has_propose AND NOT has_settle THEN 1 ELSE 0 END) AS awaiting,
                SUM(CASE WHEN completion_status = 'ENDED_AWAITING_ORACLE' THEN 1 ELSE 0 END) AS ended_awaiting_oracle,
                MAX(updated_at) AS updated_at
            FROM market_status_snapshot
            """,
        )
        if dependencies.table_exists("market_status_snapshot")
        else {}
    )

    watermarks = _sync_watermarks(dependencies)
    oracle_watermark = next((item for item in watermarks if item["id"] == "oracle_sync"), {})
    trade_watermark = next((item for item in watermarks if item["id"] == "trade_sync"), {})
    latest_oracle_at = oracle_metrics.get("latest_event_at") or oracle_watermark.get("updatedAt")
    latest_trade_at = serving_metrics.get("latest_trade_at") or trade_watermark.get("updatedAt")
    oracle_age = _age_seconds(latest_oracle_at, now)
    trade_age = _age_seconds(latest_trade_at, now)
    oracle_freshness = _freshness_status(
        oracle_age,
        fresh_seconds=3_600,
        stale_seconds=7 * 86_400,
    )
    trade_freshness = _freshness_status(
        trade_age,
        fresh_seconds=900,
        stale_seconds=86_400,
    )

    market_total = _number(market_metrics.get("total"))
    fully_identified = _number(market_metrics.get("fully_identified"))
    serving_total = _number(serving_metrics.get("total"))
    serving_priced = _number(serving_metrics.get("priced"))
    oracle_events = _number(oracle_metrics.get("events"))
    oracle_bound_events = _number(oracle_metrics.get("bound_events"))
    closed_markets = _number(resolution_metrics.get("closed"))
    final_markets = _number(resolution_metrics.get("final"))
    token_markets = _number(token_metrics.get("markets"))

    dimensions = [
        _dimension(
            dimension_id="identity",
            label="Canonical market identity",
            numerator=fully_identified,
            denominator=market_total,
            source="core.markets",
            detail="Condition, question and YES/NO token identifiers are all present.",
            warning_at=90.0,
            ok_at=99.0,
        ),
        _dimension(
            dimension_id="token-registry",
            label="Normalized token registry",
            numerator=token_markets,
            denominator=market_total,
            source="core.market_tokens",
            detail="Markets represented in the normalized outcome-token registry.",
            observed_at=token_metrics.get("updated_at"),
            warning_at=90.0,
            ok_at=99.0,
        ),
        _dimension(
            dimension_id="serving-price",
            label="Serving price coverage",
            numerator=serving_priced,
            denominator=serving_total,
            source="core.market_list_serving",
            detail="Serving-universe markets with a current probability snapshot.",
            observed_at=serving_metrics.get("updated_at"),
        ),
        _dimension(
            dimension_id="oracle-binding",
            label="Oracle event binding",
            numerator=oracle_bound_events,
            denominator=oracle_events,
            source="oracle.oracle_events",
            detail="Oracle events linked to a canonical local market identifier.",
            observed_at=latest_oracle_at,
            warning_at=90.0,
            ok_at=99.0,
        ),
        _dimension(
            dimension_id="resolution",
            label="Closed-market finality",
            numerator=final_markets,
            denominator=closed_markets,
            source="core.market_status_snapshot",
            detail="Closed markets with a final settlement snapshot.",
            observed_at=resolution_metrics.get("updated_at"),
            warning_at=85.0,
            ok_at=98.0,
        ),
        {
            "id": "oracle-freshness",
            "label": "Oracle index freshness",
            "status": oracle_freshness,
            "numerator": None,
            "denominator": None,
            "coveragePct": _freshness_score(oracle_freshness),
            "source": "ops.sync_state + oracle.oracle_events",
            "observedAt": _iso(latest_oracle_at),
            "ageSeconds": oracle_age,
            "detail": "Latest indexed request, proposal, dispute or settlement observation.",
        },
        {
            "id": "trade-freshness",
            "label": "OrderFilled serving freshness",
            "status": trade_freshness,
            "numerator": None,
            "denominator": None,
            "coveragePct": _freshness_score(trade_freshness),
            "source": "core.market_list_serving",
            "observedAt": _iso(latest_trade_at),
            "ageSeconds": trade_age,
            "detail": "Latest local trade observation used by serving prices.",
        },
    ]

    weighted_dimensions = [
        (dimensions[0], 0.20),
        (dimensions[1], 0.15),
        (dimensions[2], 0.15),
        (dimensions[3], 0.20),
        (dimensions[4], 0.15),
        (dimensions[5], 0.10),
        (dimensions[6], 0.05),
    ]
    score = round(
        sum(float(item.get("coveragePct") or 0.0) * weight for item, weight in weighted_dimensions),
        1,
    )
    critical_dimensions = [
        item["id"]
        for item in dimensions
        if item.get("status") in {"critical", "stale", "missing"}
    ]
    warning_dimensions = [
        item["id"]
        for item in dimensions
        if item.get("status") in {"warning", "aging"}
    ]
    status = "critical" if critical_dimensions else ("degraded" if warning_dimensions else "ok")

    missing_question = max(0, market_total - _number(market_metrics.get("question_bound")))
    missing_token_registry = max(0, market_total - token_markets)
    ended_awaiting = _number(resolution_metrics.get("ended_awaiting_oracle"))
    unbound_oracle = _number(oracle_metrics.get("unbound_events"))
    gaps: list[dict[str, Any]] = []
    if oracle_freshness in {"stale", "missing"}:
        gaps.append(
            {
                "id": "oracle-index-stale",
                "severity": "critical",
                "label": "Oracle index is stale",
                "count": 1,
                "detail": "The latest indexed lifecycle event is older than the seven-day hard limit.",
                "observedAt": _iso(latest_oracle_at),
                "source": "oracle.oracle_events",
            }
        )
    if ended_awaiting:
        gaps.append(
            {
                "id": "closed-awaiting-oracle",
                "severity": "warning",
                "label": "Closed markets awaiting Oracle",
                "count": ended_awaiting,
                "detail": "Trading is closed but no final Oracle-backed settlement is recorded.",
                "source": "core.market_status_snapshot",
            }
        )
    if unbound_oracle:
        gaps.append(
            {
                "id": "unbound-oracle-events",
                "severity": "warning",
                "label": "Oracle events without local market binding",
                "count": unbound_oracle,
                "detail": "Events are durable on-chain observations but cannot yet be joined to a canonical market.",
                "source": "oracle.oracle_events",
            }
        )
    if missing_question:
        gaps.append(
            {
                "id": "missing-question-id",
                "severity": "warning",
                "label": "Markets without question ID",
                "count": missing_question,
                "detail": "Condition and token identity may exist while the Oracle question bridge is absent.",
                "source": "core.markets",
            }
        )
    if missing_token_registry:
        gaps.append(
            {
                "id": "missing-normalized-token-registry",
                "severity": "warning",
                "label": "Markets absent from normalized token registry",
                "count": missing_token_registry,
                "detail": "Raw YES/NO token fields exist, but normalized outcome-token rows are incomplete.",
                "source": "core.market_tokens",
            }
        )

    gap_markets = (
        _query_all(
            dependencies,
            """
            SELECT
                m.id AS market_id,
                m.title,
                m.slug,
                m.category,
                m.end_date,
                s.completion_status,
                s.updated_at
            FROM market_status_snapshot s
            JOIN markets m ON m.id = s.market_id
            WHERE s.completion_status = 'ENDED_AWAITING_ORACLE'
              AND m.title IS NOT NULL
              AND m.title <> ''
            ORDER BY s.updated_at DESC
            LIMIT 12
            """,
        )
        if dependencies.table_exists("market_status_snapshot") and dependencies.table_exists("markets")
        else []
    )
    normalized_gap_markets = [
        {
            "marketId": row.get("market_id"),
            "title": row.get("title"),
            "slug": row.get("slug"),
            "category": row.get("category"),
            "endDate": _iso(row.get("end_date")),
            "completionStatus": row.get("completion_status"),
            "observedAt": _iso(row.get("updated_at")),
        }
        for row in gap_markets
    ]

    recent_oracle = dependencies.get_recent_oracle_snapshot(limit=24)
    if not isinstance(recent_oracle, list):
        recent_oracle = []

    return {
        "contractVersion": CONTRACT_VERSION,
        "generatedAt": generated_at,
        "status": status,
        "score": score,
        "summary": {
            "marketCount": market_total,
            "servingMarketCount": serving_total,
            "recentlyTradedMarketCount": _number(active_metrics.get("total")),
            "oracleEventCount": oracle_events,
            "oracleBoundMarketCount": _number(oracle_metrics.get("bound_markets")),
            "activeGapCount": len(gaps),
            "criticalDimensionCount": len(critical_dimensions),
            "warningDimensionCount": len(warning_dimensions),
            "latestTradeAt": _iso(latest_trade_at),
            "latestOracleAt": _iso(latest_oracle_at),
        },
        "dimensions": dimensions,
        "lifecycle": [
            {
                "id": "discovered",
                "label": "Discovered",
                "count": market_total,
                "source": "core.markets",
                "detail": "Canonical local market records.",
            },
            {
                "id": "tradeable",
                "label": "Tradeable / served",
                "count": serving_priced,
                "source": "core.market_list_serving",
                "detail": "Markets with a serving probability snapshot.",
            },
            {
                "id": "active",
                "label": "Recently active",
                "count": _number(active_metrics.get("total")),
                "source": "core.market_list_serving",
                "detail": "Markets with a local trade in the last seven days.",
            },
            {
                "id": "closed",
                "label": "Closed",
                "count": closed_markets,
                "source": "core.market_status_snapshot",
                "detail": "Trading-close state observed.",
            },
            {
                "id": "proposed",
                "label": "Proposed",
                "count": _number(resolution_metrics.get("proposed")),
                "source": "core.market_status_snapshot",
                "detail": "At least one Oracle proposal is observed.",
            },
            {
                "id": "disputed",
                "label": "Disputed",
                "count": _number(resolution_metrics.get("disputed")),
                "source": "core.market_status_snapshot",
                "detail": "At least one dispute is observed.",
            },
            {
                "id": "resolved",
                "label": "Resolved / final",
                "count": final_markets,
                "source": "core.market_status_snapshot",
                "detail": "A final settlement outcome is available.",
            },
            {
                "id": "redeemed",
                "label": "Redeemed",
                "count": None,
                "source": "not-collected",
                "detail": "Redemption coverage is outside the current read-only quality contract.",
                "status": "not-collected",
            },
        ],
        "oracleLifecycle": {
            "source": "oracle.oracle_events",
            "latestEventAt": _iso(latest_oracle_at),
            "latestBlock": oracle_metrics.get("latest_block"),
            "stages": [
                {"id": "request", "label": "Request", "count": _number(oracle_metrics.get("request_count"))},
                {"id": "propose", "label": "Propose", "count": _number(oracle_metrics.get("propose_count"))},
                {"id": "dispute", "label": "Dispute", "count": _number(oracle_metrics.get("dispute_count"))},
                {"id": "settle", "label": "Settle", "count": _number(oracle_metrics.get("settle_count"))},
            ],
            "recentEvents": recent_oracle,
        },
        "gaps": gaps,
        "gapMarkets": normalized_gap_markets,
        "watermarks": watermarks,
        "semantics": {
            "eventIdentity": "tx_hash + log_index",
            "canonicalOrder": "block_number + log_index",
            "marketBridge": "local market_id + condition_id + question_id + token_id",
            "score": "Weighted coverage and freshness signal; lifecycle counts use different historical universes and are not a funnel.",
        },
    }


def get_market_data_quality_payload(ctx: Mapping[str, Any]) -> dict[str, Any]:
    dependencies = MarketQualityDependencies.from_context(ctx)
    return dependencies.get_snapshot_payload(
        CACHE_NAMESPACE,
        CACHE_KEY,
        lambda: _build_market_data_quality_payload(dependencies),
        ttl_seconds=CACHE_TTL_SECONDS,
    )
