"""Market selectors for optional validation DB smoke tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Literal

from ...core.db import postgres_connection
from .data_sources import MarketCandidate


NBA_KEYWORDS = (
    "nba",
    "basketball",
    "knicks",
    "celtics",
    "lakers",
    "warriors",
    "spurs",
    "thunder",
    "mavericks",
    "nuggets",
    "bucks",
    "76ers",
    "heat",
    "suns",
)

UniverseType = Literal["preset", "category", "event", "watchlist"]

SUPPORTED_UNIVERSES = (
    "nba_2024_25_moneyline",
    "sports_recent_ready",
    "crypto_recent_ready",
    "politics_recent_ready",
    "fifa_world_cup_2026",
    "watchlist_slugs",
)

UNIVERSE_CACHE_VERSION = "replay_universe_v1"
DEFAULT_UNIVERSE_CACHE_TTL_SECONDS = 3600


@dataclass(frozen=True)
class UniverseSpec:
    universe_name: str = "nba_2024_25_moneyline"
    universe_type: UniverseType = "preset"
    limit: int = 50
    market_ids: tuple[int, ...] = ()
    market_slugs: tuple[str, ...] = ()
    event_slug: str | None = None
    category: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    require_resolved: bool = True
    require_orderfilled_rows: bool = True
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedMarketCandidate:
    market_id: int
    market_slug: str
    title: str
    end_date: datetime | None
    settlement_code: int
    settlement_outcome: str
    event_slug: str = ""
    category: str = ""
    token_yes_id: str | None = None
    token_no_id: str | None = None
    coverage_status: str = "unknown"
    orderfilled_rows: int = 0
    block_rows: int = 0


def default_universe_specs() -> list[UniverseSpec]:
    return [
        UniverseSpec(universe_name="nba_2024_25_moneyline", universe_type="preset", category="sports"),
        UniverseSpec(universe_name="sports_recent_ready", universe_type="category", category="sports"),
        UniverseSpec(universe_name="crypto_recent_ready", universe_type="category", category="crypto"),
        UniverseSpec(universe_name="politics_recent_ready", universe_type="category", category="politics"),
        UniverseSpec(universe_name="fifa_world_cup_2026", universe_type="event", event_slug="2026-fifa-world-cup-winner-595", category="sports", require_resolved=False),
        UniverseSpec(universe_name="watchlist_slugs", universe_type="watchlist", require_resolved=True),
    ]


def universe_spec_from_payload(payload: dict[str, Any] | None) -> UniverseSpec:
    data = payload or {}
    nested = data.get("universeSpec") or data.get("universe_spec") or {}
    if isinstance(nested, dict):
        merged = {**data, **nested}
    else:
        merged = data
    universe_name = str(merged.get("universe") or merged.get("universeName") or merged.get("universe_name") or "nba_2024_25_moneyline")
    preset = _preset_spec(universe_name)
    market_ids = tuple(int(value) for value in (merged.get("marketIds") or merged.get("market_ids") or preset.market_ids or ()) if str(value).strip())
    market_slugs = tuple(str(value).strip() for value in (merged.get("marketSlugs") or merged.get("market_slugs") or preset.market_slugs or ()) if str(value).strip())
    limit = int(merged.get("limit") or preset.limit or 50)
    require_resolved = _bool_value(merged.get("requireResolved", merged.get("require_resolved", preset.require_resolved)))
    require_orderfilled_rows = _bool_value(merged.get("requireOrderfilledRows", merged.get("require_orderfilled_rows", preset.require_orderfilled_rows)))
    return UniverseSpec(
        universe_name=universe_name,
        universe_type=str(merged.get("universeType") or merged.get("universe_type") or preset.universe_type),  # type: ignore[arg-type]
        limit=max(1, min(limit, 500)),
        market_ids=market_ids,
        market_slugs=market_slugs,
        event_slug=str(merged.get("eventSlug") or merged.get("event_slug") or preset.event_slug or "") or None,
        category=str(merged.get("category") or preset.category or "") or None,
        start_date=str(merged.get("startDate") or merged.get("start_date") or preset.start_date or "") or None,
        end_date=str(merged.get("endDate") or merged.get("end_date") or preset.end_date or "") or None,
        require_resolved=require_resolved,
        require_orderfilled_rows=require_orderfilled_rows,
        meta={"raw": merged},
    )


def select_replay_universe(spec: UniverseSpec) -> list[ResolvedMarketCandidate]:
    cached = _read_universe_cache(spec)
    if cached is not None:
        return cached
    candidates = _select_replay_universe_uncached(spec)
    _write_universe_cache(spec, candidates)
    return candidates


def _select_replay_universe_uncached(spec: UniverseSpec) -> list[ResolvedMarketCandidate]:
    if spec.universe_name == "nba_2024_25_moneyline":
        return select_nba_2024_25_moneyline_markets(limit=spec.limit)
    if spec.universe_name not in SUPPORTED_UNIVERSES and spec.universe_type == "preset":
        raise ValueError(f"unsupported universe: {spec.universe_name}")
    if spec.universe_type == "watchlist" and not spec.market_slugs and not spec.market_ids:
        raise ValueError("watchlist_slugs universe requires marketSlugs or marketIds")
    return _select_metadata_universe(spec)


def list_supported_universes() -> list[dict[str, Any]]:
    return [
        {
            "universeName": spec.universe_name,
            "universeType": spec.universe_type,
            "category": spec.category,
            "eventSlug": spec.event_slug,
            "requireResolved": spec.require_resolved,
            "requireOrderfilledRows": spec.require_orderfilled_rows,
            "label": _universe_label(spec.universe_name),
        }
        for spec in default_universe_specs()
    ]


def select_nba_2024_25_markets(*, limit: int = 5) -> list[MarketCandidate]:
    pattern_filters = " OR ".join(
        [
            "lower(e.event_title) LIKE %s",
            "lower(e.event_slug) LIKE %s",
            "lower(m.question) LIKE %s",
            "lower(m.market_slug) LIKE %s",
            "lower(m.outcome_label) LIKE %s",
        ]
    )
    params: list[Any] = []
    keyword_groups: list[str] = []
    for keyword in NBA_KEYWORDS:
        keyword_groups.append(f"({pattern_filters})")
        params.extend([f"%{keyword}%"] * 5)
    params.append(limit)
    rows = _query_postgres_rows(
        f"""
                SELECT
                    m.market_id,
                    m.market_slug,
                    m.token_yes_id AS token_id,
                    'YES' AS token_side,
                    p.min_block_complete AS from_block,
                    p.max_block_complete AS to_block,
                    COALESCE(m.question, e.event_title, m.market_slug) AS title
                FROM quant.market_event_members m
                JOIN quant.market_event_metadata e ON e.event_slug = m.event_slug
                JOIN quant.market_price_build_market_progress p ON p.market_id = m.market_id
                WHERE ({' OR '.join(keyword_groups)})
                  AND COALESCE(e.start_date, e.created_at, m.created_at) >= TIMESTAMPTZ '2024-10-01'
                  AND COALESCE(e.start_date, e.created_at, m.created_at) < TIMESTAMPTZ '2025-07-01'
                  AND m.coverage_status = 'ready'
                  AND m.block_rows > 0
                  AND m.orderfilled_rows > 0
                  AND p.min_block_complete IS NOT NULL
                  AND p.max_block_complete IS NOT NULL
                  AND m.token_yes_id IS NOT NULL
                ORDER BY m.block_rows DESC, m.orderfilled_rows DESC, m.updated_at DESC
                LIMIT %s
                """,
        params,
    )
    return [
        MarketCandidate(
            market_id=int(row["market_id"]),
            market_slug=str(row["market_slug"]),
            token_id=str(row["token_id"]) if row.get("token_id") else None,
            token_side=str(row["token_side"]),
            from_block=int(row["from_block"]) if row.get("from_block") is not None else None,
            to_block=int(row["to_block"]) if row.get("to_block") is not None else None,
            title=str(row.get("title") or ""),
        )
        for row in rows
    ]


def select_nba_2024_25_moneyline_markets(*, limit: int = 200) -> list[ResolvedMarketCandidate]:
    """Select resolved NBA 2024/25 moneyline markets from metadata tables only.

    This intentionally does not require `quant.market_token_block_close` coverage:
    the 2024/25 NBA moneyline data currently exists in `core.markets` plus raw
    ClickHouse OrderFilled rows, while many of those markets have not been
    materialized into `quant.market_token_block_close` yet.
    """
    rows = _query_postgres_rows(
        """
                SELECT
                    m.id AS market_id,
                    m.slug AS market_slug,
                    m.title,
                    m.end_date,
                    s.settlement_code,
                    s.settlement_outcome
                FROM core.markets m
                JOIN core.market_status_snapshot s ON s.market_id = m.id
                WHERE lower(m.slug) ~ '^nba-[a-z]{2,4}-[a-z]{2,4}-20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
                  AND COALESCE(m.end_date, m.created_at) >= TIMESTAMPTZ '2024-10-01'
                  AND COALESCE(m.end_date, m.created_at) < TIMESTAMPTZ '2025-07-01'
                  AND s.is_resolved IS TRUE
                  AND s.settlement_code IN (1, 2)
                ORDER BY m.end_date ASC, m.id ASC
                LIMIT %s
                """,
        (limit,),
    )
    return [
        ResolvedMarketCandidate(
            market_id=int(row["market_id"]),
            market_slug=str(row["market_slug"]),
            title=str(row.get("title") or row["market_slug"]),
            end_date=row.get("end_date"),
            settlement_code=int(row["settlement_code"]),
            settlement_outcome=str(row.get("settlement_outcome") or ""),
            category="sports",
            event_slug="nba_2024_25",
            coverage_status="raw_orderfilled",
        )
        for row in rows
    ]


def _select_metadata_universe(spec: UniverseSpec) -> list[ResolvedMarketCandidate]:
    filters = [
        "m.token_yes_id IS NOT NULL",
        "m.token_no_id IS NOT NULL",
        "COALESCE(p.min_block_complete, p.first_orderfilled_block, m.latest_block) IS NOT NULL",
        "COALESCE(p.max_block_complete, p.last_orderfilled_block, m.latest_block) IS NOT NULL",
    ]
    params: list[Any] = []
    if spec.require_orderfilled_rows:
        filters.append("COALESCE(m.orderfilled_rows, p.block_rows_written, 0) > 0")
    if spec.require_resolved:
        filters.append("COALESCE(s.is_resolved, m.resolved, false) IS TRUE")
        filters.append("COALESCE(s.settlement_code, 0) IN (1, 2)")
    if spec.category:
        filters.append("(lower(COALESCE(e.event_category, cm.category, '')) = lower(%s) OR lower(COALESCE(e.event_subcategory, '')) = lower(%s))")
        params.extend([spec.category, spec.category])
    if spec.event_slug:
        filters.append("(e.event_slug = %s OR m.event_slug = %s OR cm.event_slug = %s)")
        params.extend([spec.event_slug, spec.event_slug, spec.event_slug])
    if spec.market_ids:
        filters.append("m.market_id = ANY(%s)")
        params.append(list(spec.market_ids))
    if spec.market_slugs:
        filters.append("m.market_slug = ANY(%s)")
        params.append(list(spec.market_slugs))
    if spec.start_date:
        filters.append("COALESCE(cm.end_date, e.end_date, e.resolution_date, e.start_date, m.latest_timestamp, m.updated_at) >= %s::timestamptz")
        params.append(spec.start_date)
    if spec.end_date:
        filters.append("COALESCE(cm.end_date, e.end_date, e.resolution_date, e.start_date, m.latest_timestamp, m.updated_at) < %s::timestamptz")
        params.append(spec.end_date)
    params.append(spec.limit)
    where_sql = "\n                  AND ".join(filters)
    rows = _query_postgres_rows(
        f"""
                SELECT DISTINCT ON (m.market_id)
                    m.market_id,
                    m.market_slug,
                    COALESCE(cm.title, m.question, e.event_title, m.market_slug) AS title,
                    COALESCE(cm.end_date, e.end_date, e.resolution_date, e.start_date, m.latest_timestamp, m.updated_at) AS end_date,
                    COALESCE(s.settlement_code, 0) AS settlement_code,
                    COALESCE(s.settlement_outcome, CASE WHEN COALESCE(s.is_resolved, m.resolved, false) THEN 'RESOLVED' ELSE 'UNRESOLVED' END) AS settlement_outcome,
                    COALESCE(e.event_slug, m.event_slug, cm.event_slug, '') AS event_slug,
                    COALESCE(e.event_category, cm.category, '') AS category,
                    m.token_yes_id,
                    m.token_no_id,
                    COALESCE(m.coverage_status, p.status, 'unknown') AS coverage_status,
                    COALESCE(m.orderfilled_rows, 0) AS orderfilled_rows,
                    COALESCE(m.block_rows, p.block_rows_written, 0) AS block_rows
                FROM quant.market_event_members m
                LEFT JOIN quant.market_event_metadata e ON e.event_slug = m.event_slug
                LEFT JOIN quant.market_price_build_market_progress p ON p.market_id = m.market_id
                LEFT JOIN core.markets cm ON cm.id = m.market_id
                LEFT JOIN core.market_status_snapshot s ON s.market_id = m.market_id
                WHERE {where_sql}
                ORDER BY m.market_id, COALESCE(m.orderfilled_rows, 0) DESC, COALESCE(m.block_rows, p.block_rows_written, 0) DESC
                LIMIT %s
                """,
        params,
    )
    return [
        ResolvedMarketCandidate(
            market_id=int(row["market_id"]),
            market_slug=str(row["market_slug"]),
            title=str(row.get("title") or row["market_slug"]),
            end_date=row.get("end_date"),
            settlement_code=int(row.get("settlement_code") or 0),
            settlement_outcome=str(row.get("settlement_outcome") or ""),
            event_slug=str(row.get("event_slug") or ""),
            category=str(row.get("category") or ""),
            token_yes_id=str(row["token_yes_id"]) if row.get("token_yes_id") else None,
            token_no_id=str(row["token_no_id"]) if row.get("token_no_id") else None,
            coverage_status=str(row.get("coverage_status") or "unknown"),
            orderfilled_rows=int(row.get("orderfilled_rows") or 0),
            block_rows=int(row.get("block_rows") or 0),
        )
        for row in rows
    ]


def _preset_spec(name: str) -> UniverseSpec:
    for spec in default_universe_specs():
        if spec.universe_name == name:
            return spec
    if name == "watchlist_slugs":
        return UniverseSpec(universe_name=name, universe_type="watchlist")
    return UniverseSpec(universe_name=name, universe_type="preset")


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _universe_label(name: str) -> str:
    labels = {
        "nba_2024_25_moneyline": "NBA 2024/25 Moneyline",
        "sports_recent_ready": "Sports Recent Ready",
        "crypto_recent_ready": "Crypto Recent Ready",
        "politics_recent_ready": "Politics Recent Ready",
        "fifa_world_cup_2026": "2026 FIFA World Cup Winner",
        "watchlist_slugs": "Watchlist Slugs",
    }
    return labels.get(name, name.replace("_", " ").title())


def _query_postgres_rows(sql: str, params: Any, *, attempts: int = 3) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            with postgres_connection(readonly=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return list(cur.fetchall())
        except Exception as exc:  # pragma: no cover - exact psycopg subclasses vary by version.
            last_error = exc
            if attempt >= attempts - 1 or not _is_retryable_postgres_error(exc):
                raise
            time.sleep(0.35 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return []


def _is_retryable_postgres_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return (
        "connectiontimeout" in name
        or "operationalerror" in name
        or "connection timeout" in message
        or "timeout expired" in message
        or "connection refused" in message
        or "server closed the connection" in message
    )


def _read_universe_cache(spec: UniverseSpec) -> list[ResolvedMarketCandidate] | None:
    if not _universe_cache_enabled():
        return None
    path = _universe_cache_path(spec)
    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        created_at = float(payload.get("created_at_epoch") or 0)
        ttl = _universe_cache_ttl_seconds(spec)
        if ttl <= 0 or time.time() - created_at > ttl:
            return None
        rows = payload.get("markets")
        if not isinstance(rows, list):
            return None
        return [_candidate_from_cache_row(row) for row in rows if isinstance(row, dict)]
    except Exception:
        return None


def _write_universe_cache(spec: UniverseSpec, candidates: list[ResolvedMarketCandidate]) -> None:
    if not _universe_cache_enabled() or _universe_cache_ttl_seconds(spec) <= 0:
        return
    path = _universe_cache_path(spec)
    payload = {
        "version": UNIVERSE_CACHE_VERSION,
        "created_at_epoch": time.time(),
        "spec": _universe_cache_payload(spec),
        "markets": [_candidate_to_cache_row(candidate) for candidate in candidates],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        return


def _universe_cache_enabled() -> bool:
    return str(os.environ.get("POLYDATA_BACKTEST_UNIVERSE_CACHE_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _universe_cache_ttl_seconds(spec: UniverseSpec) -> int:
    raw_value = os.environ.get("POLYDATA_BACKTEST_UNIVERSE_CACHE_TTL_SECONDS")
    if raw_value:
        try:
            return max(0, int(raw_value))
        except ValueError:
            return DEFAULT_UNIVERSE_CACHE_TTL_SECONDS
    if spec.universe_name == "nba_2024_25_moneyline":
        return 24 * 60 * 60
    return DEFAULT_UNIVERSE_CACHE_TTL_SECONDS


def _universe_cache_path(spec: UniverseSpec) -> Path:
    root = Path(os.environ.get("POLYDATA_BACKTEST_UNIVERSE_CACHE_DIR") or "data/backtest_universe_cache")
    digest = hashlib.sha256(json.dumps(_universe_cache_payload(spec), sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]
    return root / f"{digest}.json"


def _universe_cache_payload(spec: UniverseSpec) -> dict[str, Any]:
    return {
        "version": UNIVERSE_CACHE_VERSION,
        "universe_name": spec.universe_name,
        "universe_type": spec.universe_type,
        "limit": int(spec.limit),
        "market_ids": list(spec.market_ids),
        "market_slugs": list(spec.market_slugs),
        "event_slug": spec.event_slug,
        "category": spec.category,
        "start_date": spec.start_date,
        "end_date": spec.end_date,
        "require_resolved": spec.require_resolved,
        "require_orderfilled_rows": spec.require_orderfilled_rows,
    }


def _candidate_to_cache_row(candidate: ResolvedMarketCandidate) -> dict[str, Any]:
    return {
        "market_id": candidate.market_id,
        "market_slug": candidate.market_slug,
        "title": candidate.title,
        "end_date": candidate.end_date.isoformat() if isinstance(candidate.end_date, datetime) else candidate.end_date,
        "settlement_code": candidate.settlement_code,
        "settlement_outcome": candidate.settlement_outcome,
        "event_slug": candidate.event_slug,
        "category": candidate.category,
        "token_yes_id": candidate.token_yes_id,
        "token_no_id": candidate.token_no_id,
        "coverage_status": candidate.coverage_status,
        "orderfilled_rows": candidate.orderfilled_rows,
        "block_rows": candidate.block_rows,
    }


def _candidate_from_cache_row(row: dict[str, Any]) -> ResolvedMarketCandidate:
    end_date = _parse_cached_datetime(row.get("end_date"))
    return ResolvedMarketCandidate(
        market_id=int(row["market_id"]),
        market_slug=str(row["market_slug"]),
        title=str(row.get("title") or row["market_slug"]),
        end_date=end_date,
        settlement_code=int(row.get("settlement_code") or 0),
        settlement_outcome=str(row.get("settlement_outcome") or ""),
        event_slug=str(row.get("event_slug") or ""),
        category=str(row.get("category") or ""),
        token_yes_id=str(row["token_yes_id"]) if row.get("token_yes_id") else None,
        token_no_id=str(row["token_no_id"]) if row.get("token_no_id") else None,
        coverage_status=str(row.get("coverage_status") or "unknown"),
        orderfilled_rows=int(row.get("orderfilled_rows") or 0),
        block_rows=int(row.get("block_rows") or 0),
    )


def _parse_cached_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
