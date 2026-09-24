"""Market/token metadata loader for quant price builders."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class MarketTokenMetadata:
    market_id: int
    gamma_market_id: str | None
    market_slug: str | None
    condition_id: str | None
    question_id: str | None
    market_title: str | None
    token_id: str
    token_id_hex: str | None
    token_side: str
    outcome_index: int | None
    active: bool
    closed: bool
    archived: bool
    deprecated: bool
    duplicate_group_key: str | None
    end_date: Any
    created_at: Any


def _row_to_metadata(row: dict[str, Any]) -> MarketTokenMetadata:
    token_id = str(row["token_id"])
    return MarketTokenMetadata(
        market_id=int(row["market_id"]),
        gamma_market_id=row.get("gamma_market_id"),
        market_slug=row.get("market_slug"),
        condition_id=row.get("condition_id"),
        question_id=row.get("question_id"),
        market_title=row.get("market_title"),
        token_id=token_id,
        token_id_hex=derive_clickhouse_token_id_hex(token_id),
        token_side=str(row.get("token_side") or "").upper(),
        outcome_index=row.get("outcome_index"),
        active=bool(row.get("active", True)),
        closed=bool(row.get("closed", False)),
        archived=bool(row.get("archived", False)),
        deprecated=bool(row.get("deprecated", False)),
        duplicate_group_key=row.get("duplicate_group_key"),
        end_date=row.get("end_date"),
        created_at=row.get("created_at"),
    )


def derive_clickhouse_token_id_hex(token_id: str | None) -> str | None:
    """Convert a CLOB decimal token id to ClickHouse's normalized hex token id."""

    text = str(token_id or "").strip().lower()
    if not text:
        return None
    if text.startswith("0x"):
        text = text[2:]
    if len(text) == 64 and all(ch in "0123456789abcdef" for ch in text):
        return text
    if not text.isdigit():
        return None
    try:
        return format(int(text), "064x")
    except ValueError:
        return None


def fetch_market_token_metadata(
    conn: Any,
    *,
    limit: int | None = None,
    market_slug: str | None = None,
    since_ts: int | None = None,
) -> list[MarketTokenMetadata]:
    params: list[Any] = []
    filters = ["mt.token_id IS NOT NULL", "mt.token_id <> ''"]
    if market_slug:
        filters.append("m.slug = %s")
        params.append(market_slug)
    if since_ts is not None:
        filters.append(
            """
            (
                m.created_at >= to_timestamp(%s)
                OR m.end_date >= to_timestamp(%s)
            )
            """
        )
        params.extend([int(since_ts), int(since_ts)])
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT %s"
        params.append(int(limit))
    sql = f"""
        SELECT
            m.id AS market_id,
            m.gamma_market_id,
            m.slug AS market_slug,
            m.condition_id,
            m.question_id,
            COALESCE(m.title, m.slug) AS market_title,
            mt.token_id,
            UPPER(COALESCE(mt.outcome, CASE WHEN mt.outcome_index = 0 THEN 'YES' WHEN mt.outcome_index = 1 THEN 'NO' ELSE 'UNKNOWN' END)) AS token_side,
            mt.outcome_index,
            COALESCE(mt.active, TRUE) AS active,
            COALESCE(mss.is_trading_closed, FALSE) AS closed,
            (
                m.slug ILIKE 'arch-%%'
                OR m.slug ILIKE '%%-arch-%%'
                OR m.title ILIKE 'ARCH:%%'
                OR m.title ILIKE '[ARCH]%%'
            ) AS archived,
            (
                m.slug ILIKE '%%deprecated%%'
                OR m.title ILIKE '%%deprecated%%'
            ) AS deprecated,
            lower(COALESCE(NULLIF(m.condition_id, ''), NULLIF(m.question_id, ''), NULLIF(m.slug, ''))) AS duplicate_group_key,
            COALESCE(mt.end_date, m.end_date) AS end_date,
            m.created_at AS created_at
        FROM core.market_tokens mt
        JOIN core.markets m ON m.id = mt.market_id
        LEFT JOIN core.market_status_snapshot mss ON mss.market_id = m.id
        WHERE {" AND ".join(filters)}
        ORDER BY m.id ASC, mt.outcome_index ASC, mt.token_id ASC
        {limit_sql}
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [_row_to_metadata(dict(row)) for row in cur.fetchall()]


def upsert_market_token_metadata(conn: Any, rows: Iterable[MarketTokenMetadata]) -> int:
    values = [
        (
            row.market_id,
            row.gamma_market_id,
            row.market_slug,
            row.condition_id,
            row.question_id,
            row.market_title,
            row.token_id,
            row.token_id_hex,
            row.token_side,
            row.outcome_index,
            row.active,
            row.closed,
            row.archived,
            row.deprecated,
            row.duplicate_group_key,
            row.end_date,
            row.created_at,
        )
        for row in rows
    ]
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO quant.market_token_metadata (
                market_id, gamma_market_id, market_slug, condition_id, question_id,
                market_title, token_id, token_id_hex, token_side, outcome_index, active, closed,
                archived, deprecated, duplicate_group_key, end_date, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (token_id) DO UPDATE SET
                market_id = EXCLUDED.market_id,
                gamma_market_id = EXCLUDED.gamma_market_id,
                market_slug = EXCLUDED.market_slug,
                condition_id = EXCLUDED.condition_id,
                question_id = EXCLUDED.question_id,
                market_title = EXCLUDED.market_title,
                token_id_hex = EXCLUDED.token_id_hex,
                token_side = EXCLUDED.token_side,
                outcome_index = EXCLUDED.outcome_index,
                active = EXCLUDED.active,
                closed = EXCLUDED.closed,
                archived = EXCLUDED.archived,
                deprecated = EXCLUDED.deprecated,
                duplicate_group_key = EXCLUDED.duplicate_group_key,
                end_date = EXCLUDED.end_date,
                created_at = EXCLUDED.created_at,
                updated_at = now()
            """,
            values,
        )
        return cur.rowcount or len(values)


def refresh_market_token_metadata(conn: Any, *, limit: int | None = None, market_slug: str | None = None, since_ts: int | None = None) -> int:
    return upsert_market_token_metadata(conn, fetch_market_token_metadata(conn, limit=limit, market_slug=market_slug, since_ts=since_ts))


SLUG_SAFE_RE = re.compile(r"[^a-z0-9]+")
MATCHUP_RE = re.compile(r"\s+(?:vs\.?|v\.?)\s+", re.IGNORECASE)
RATE_NO_CHANGE_RE = re.compile(
    r"will\s+there\s+be\s+no\s+change\s+in\s+(?P<bank>.+?)\s+interest\s+rates\s+after\s+the\s+(?P<month>[a-z]+)\s+(?P<year>\d{4})\s+meeting",
    re.IGNORECASE,
)
RATE_MOVE_RE = re.compile(
    r"will\s+the\s+(?P<bank>.+?)\s+(?P<direction>increase|decrease)\s+interest\s+rates\s+by\s+(?P<size>\d+\+?)\s+bps\s+after\s+the\s+(?P<month>[a-z]+)\s+(?P<year>\d{4})\s+meeting",
    re.IGNORECASE,
)


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().removesuffix("?").strip()


def _slugify(value: str | None) -> str:
    text = SLUG_SAFE_RE.sub("-", _clean_text(value).lower()).strip("-")
    return text or "event"


def _outcome_key(value: str | None, market_id: Any) -> str:
    return f"{_slugify(value)}-{market_id}"


def _label_from_rate_market(title: str | None) -> str | None:
    text = _clean_text(title)
    no_change = RATE_NO_CHANGE_RE.search(text)
    if no_change:
        return "No change"
    move = RATE_MOVE_RE.search(text)
    if move:
        return f"{move.group('size')} bps {move.group('direction').lower()}"
    return None


def _fallback_event(row: dict[str, Any]) -> dict[str, str] | None:
    title = _clean_text(row.get("market_title"))
    slug = str(row.get("market_slug") or "").strip()
    rate_match = RATE_NO_CHANGE_RE.search(title) or RATE_MOVE_RE.search(title)
    if rate_match:
        bank = _clean_text(rate_match.group("bank"))
        month = rate_match.group("month").lower()
        year = rate_match.group("year")
        event_slug = f"{_slugify(bank)}-decision-in-{month}-{year}"
        return {
            "event_id": f"inferred:central-bank-rate:{event_slug}",
            "event_slug": event_slug,
            "event_title": f"{bank} Decision in {month.title()} {year}?",
            "outcome_label": _label_from_rate_market(title) or title,
            "grouping_confidence": "high_confidence",
            "source": "fallback.central_bank_rate",
        }

    return None


def _official_outcome_label(row: dict[str, Any]) -> str:
    title = _clean_text(row.get("market_title"))
    event_title = _clean_text(row.get("event_title"))
    rate_label = _label_from_rate_market(title)
    if rate_label:
        return rate_label
    if event_title and title.lower().startswith(event_title.lower()):
        suffix = _clean_text(title[len(event_title) :].lstrip(":- "))
        if suffix:
            return suffix
    if ":" in title:
        suffix = _clean_text(title.split(":", 1)[1])
        if suffix:
            return suffix
    matchup = [part.strip() for part in MATCHUP_RE.split(title, maxsplit=1) if part.strip()]
    if len(matchup) == 2:
        return matchup[0]
    return title or str(row.get("market_slug") or row.get("market_id") or "Outcome")


def _event_identity(row: dict[str, Any]) -> dict[str, str] | None:
    event_slug = str(row.get("event_slug") or "").strip()
    event_id = str(row.get("event_id") or "").strip()
    event_title = _clean_text(row.get("event_title"))
    if event_slug or event_id or event_title:
        slug = event_slug or _slugify(event_title or event_id)
        return {
            "event_id": event_id or slug,
            "event_slug": slug,
            "event_title": event_title or _clean_text(row.get("market_title")) or slug,
            "outcome_label": _official_outcome_label(row),
            "grouping_confidence": "official",
            "source": "core.markets",
        }
    return _fallback_event(row)


def refresh_market_event_memberships(
    conn: Any,
    *,
    limit: int | None = None,
    market_slug: str | None = None,
    since_ts: int | None = None,
) -> dict[str, int]:
    """Materialize event -> member market -> token membership for quant reads.

    This table is intentionally a read model. Prices remain in token-granular
    production tables, while event reads join through this membership layer.
    """

    params: list[Any] = []
    filters = [
        "m.slug IS NOT NULL",
        """
        (
            m.event_id IS NOT NULL
            OR m.event_slug IS NOT NULL
            OR m.event_title IS NOT NULL
            OR m.title ILIKE '%% interest rates after the %% meeting%%'
            OR m.title ~* '\\s(vs\\.?|v\\.?)\\s'
        )
        """,
    ]
    if market_slug:
        filters.append("m.slug = %s")
        params.append(market_slug)
    if since_ts is not None:
        filters.append(
            """
            (
                m.created_at >= to_timestamp(%s)
                OR m.end_date >= to_timestamp(%s)
                OR m.event_id IS NOT NULL
                OR m.event_slug IS NOT NULL
            )
            """
        )
        params.extend([int(since_ts), int(since_ts)])
    limit_sql = ""
    if limit is not None:
        limit_sql = "LIMIT %s"
        params.append(int(limit))

    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH token_rollup AS (
                SELECT
                    market_id,
                    max(token_id) FILTER (
                        WHERE upper(COALESCE(outcome, '')) = 'YES' OR outcome_index = 0
                    ) AS token_yes_id,
                    max(token_id) FILTER (
                        WHERE upper(COALESCE(outcome, '')) = 'NO' OR outcome_index = 1
                    ) AS token_no_id
                FROM core.market_tokens
                WHERE token_id IS NOT NULL AND token_id <> ''
                GROUP BY market_id
            )
            SELECT
                m.id AS market_id,
                m.slug AS market_slug,
                m.condition_id,
                COALESCE(m.title, m.slug) AS market_title,
                m.description,
                m.category,
                m.tags,
                m.clob_token_ids,
                m.event_id,
                m.event_slug,
                m.event_title,
                m.created_at,
                m.end_date,
                COALESCE(mss.completion_status, 'unknown') AS status,
                COALESCE(mss.is_trading_closed, FALSE) AS closed,
                COALESCE(mss.is_resolved, FALSE) AS resolved,
                NOT COALESCE(mss.is_trading_closed, FALSE) AS active,
                tr.token_yes_id,
                tr.token_no_id,
                COALESCE(p.block_rows_written, 0) AS block_rows,
                COALESCE(p.frontend_rows_written, 0) AS frontend_rows,
                COALESCE(s.trade_count, 0) AS orderfilled_rows,
                p.max_block_complete AS latest_block,
                p.max_frontend_complete_ts AS latest_timestamp
            FROM core.markets m
            JOIN token_rollup tr ON tr.market_id = m.id
            LEFT JOIN core.market_status_snapshot mss ON mss.market_id = m.id
            LEFT JOIN quant.market_price_build_market_progress p ON p.market_id = m.id
            LEFT JOIN quant.market_orderfilled_market_stats s ON s.market_id = m.id
            WHERE {" AND ".join(filters)}
            ORDER BY m.created_at ASC NULLS LAST, m.id ASC
            {limit_sql}
            """,
            params,
        )
        source_rows = [dict(row) for row in cur.fetchall()]

    events: dict[str, dict[str, Any]] = {}
    members: list[dict[str, Any]] = []
    targets: list[tuple[Any, ...]] = []
    event_member_counts: dict[str, int] = {}

    for row in source_rows:
        identity = _event_identity(row)
        if not identity:
            continue
        event_slug = identity["event_slug"]
        outcome_label = identity["outcome_label"]
        outcome_order = event_member_counts.get(event_slug, 0)
        event_member_counts[event_slug] = outcome_order + 1
        block_rows = int(row.get("block_rows") or 0)
        frontend_rows = int(row.get("frontend_rows") or 0)
        orderfilled_rows = int(row.get("orderfilled_rows") or 0)
        coverage_status = "ready" if block_rows or frontend_rows else "queued" if orderfilled_rows else "none"
        active = bool(row.get("active"))
        closed = bool(row.get("closed"))
        event = events.get(event_slug)
        if not event:
            events[event_slug] = {
                "event_id": identity["event_id"],
                "event_slug": event_slug,
                "event_title": identity["event_title"],
                "event_category": row.get("category"),
                "event_subcategory": None,
                "event_image_url": None,
                "event_icon_url": None,
                "description": row.get("description"),
                "start_date": row.get("created_at"),
                "end_date": row.get("end_date"),
                "resolution_date": row.get("end_date") if bool(row.get("resolved")) else None,
                "status": row.get("status") or "unknown",
                "volume": None,
                "liquidity": None,
                "grouping_confidence": identity["grouping_confidence"],
                "source": identity["source"],
                "created_at": row.get("created_at"),
            }
        else:
            if row.get("created_at") and (event["start_date"] is None or row["created_at"] < event["start_date"]):
                event["start_date"] = row.get("created_at")
            if row.get("end_date") and (event["end_date"] is None or row["end_date"] > event["end_date"]):
                event["end_date"] = row.get("end_date")
            if active:
                event["status"] = "active"
            elif closed and event["status"] != "active":
                event["status"] = "closed"

        members.append(
            {
                "event_slug": event_slug,
                "event_id": identity["event_id"],
                "market_id": row.get("market_id"),
                "market_slug": row.get("market_slug"),
                "condition_id": row.get("condition_id"),
                "question": row.get("market_title"),
                "outcome_label": outcome_label,
                "outcome_key": _outcome_key(outcome_label, row.get("market_id")),
                "outcome_order": outcome_order,
                "token_yes_id": row.get("token_yes_id"),
                "token_no_id": row.get("token_no_id"),
                "clob_token_ids": json.dumps(row.get("clob_token_ids") or []),
                "status": row.get("status") or "unknown",
                "active": active,
                "closed": closed,
                "resolved": bool(row.get("resolved")),
                "volume": None,
                "liquidity": None,
                "block_rows": block_rows,
                "frontend_rows": frontend_rows,
                "orderfilled_rows": orderfilled_rows,
                "latest_yes": None,
                "latest_no": None,
                "latest_block": row.get("latest_block"),
                "latest_timestamp": row.get("latest_timestamp"),
                "coverage_status": coverage_status,
                "grouping_confidence": identity["grouping_confidence"],
                "source": identity["source"],
                "created_at": row.get("created_at"),
            }
        )

        for token_id, token_side in ((row.get("token_yes_id"), "YES"), (row.get("token_no_id"), "NO")):
            if not token_id:
                continue
            for source, priority in (("orderfilled_block_close", 1250), ("frontend", 850)):
                targets.append(
                    (
                        source,
                        token_id,
                        row.get("market_id"),
                        row.get("market_slug"),
                        token_side,
                        priority,
                        f"event_member:{event_slug}",
                    )
                )

    if not events and not members:
        return {"events": 0, "members": 0, "targets": 0}

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO quant.market_event_metadata (
                event_id, event_slug, event_title, event_category, event_subcategory,
                event_image_url, event_icon_url, description, start_date, end_date,
                resolution_date, status, volume, liquidity, grouping_confidence, source,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (event_slug) DO UPDATE SET
                event_id = EXCLUDED.event_id,
                event_title = EXCLUDED.event_title,
                event_category = EXCLUDED.event_category,
                event_subcategory = EXCLUDED.event_subcategory,
                event_image_url = EXCLUDED.event_image_url,
                event_icon_url = EXCLUDED.event_icon_url,
                description = EXCLUDED.description,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                resolution_date = EXCLUDED.resolution_date,
                status = EXCLUDED.status,
                volume = EXCLUDED.volume,
                liquidity = EXCLUDED.liquidity,
                grouping_confidence = EXCLUDED.grouping_confidence,
                source = EXCLUDED.source,
                created_at = EXCLUDED.created_at,
                updated_at = now()
            """,
            [
                (
                    event["event_id"],
                    event["event_slug"],
                    event["event_title"],
                    event["event_category"],
                    event["event_subcategory"],
                    event["event_image_url"],
                    event["event_icon_url"],
                    event["description"],
                    event["start_date"],
                    event["end_date"],
                    event["resolution_date"],
                    event["status"],
                    event["volume"],
                    event["liquidity"],
                    event["grouping_confidence"],
                    event["source"],
                    event["created_at"],
                )
                for event in events.values()
            ],
        )
        cur.executemany(
            """
            INSERT INTO quant.market_event_members (
                event_slug, event_id, market_id, market_slug, condition_id, question,
                outcome_label, outcome_key, outcome_order, token_yes_id, token_no_id,
                clob_token_ids, status, active, closed, resolved, volume, liquidity,
                block_rows, frontend_rows, orderfilled_rows, latest_yes, latest_no,
                latest_block, latest_timestamp, coverage_status, grouping_confidence,
                source, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (event_slug, market_id) DO UPDATE SET
                event_id = EXCLUDED.event_id,
                market_slug = EXCLUDED.market_slug,
                condition_id = EXCLUDED.condition_id,
                question = EXCLUDED.question,
                outcome_label = EXCLUDED.outcome_label,
                outcome_key = EXCLUDED.outcome_key,
                outcome_order = EXCLUDED.outcome_order,
                token_yes_id = EXCLUDED.token_yes_id,
                token_no_id = EXCLUDED.token_no_id,
                clob_token_ids = EXCLUDED.clob_token_ids,
                status = EXCLUDED.status,
                active = EXCLUDED.active,
                closed = EXCLUDED.closed,
                resolved = EXCLUDED.resolved,
                volume = EXCLUDED.volume,
                liquidity = EXCLUDED.liquidity,
                block_rows = EXCLUDED.block_rows,
                frontend_rows = EXCLUDED.frontend_rows,
                orderfilled_rows = EXCLUDED.orderfilled_rows,
                latest_yes = EXCLUDED.latest_yes,
                latest_no = EXCLUDED.latest_no,
                latest_block = EXCLUDED.latest_block,
                latest_timestamp = EXCLUDED.latest_timestamp,
                coverage_status = EXCLUDED.coverage_status,
                grouping_confidence = EXCLUDED.grouping_confidence,
                source = EXCLUDED.source,
                created_at = EXCLUDED.created_at,
                updated_at = now()
            """,
            [
                (
                    item["event_slug"],
                    item["event_id"],
                    item["market_id"],
                    item["market_slug"],
                    item["condition_id"],
                    item["question"],
                    item["outcome_label"],
                    item["outcome_key"],
                    item["outcome_order"],
                    item["token_yes_id"],
                    item["token_no_id"],
                    item["clob_token_ids"],
                    item["status"],
                    item["active"],
                    item["closed"],
                    item["resolved"],
                    item["volume"],
                    item["liquidity"],
                    item["block_rows"],
                    item["frontend_rows"],
                    item["orderfilled_rows"],
                    item["latest_yes"],
                    item["latest_no"],
                    item["latest_block"],
                    item["latest_timestamp"],
                    item["coverage_status"],
                    item["grouping_confidence"],
                    item["source"],
                    item["created_at"],
                )
                for item in members
            ],
        )
        if targets:
            cur.executemany(
                """
                INSERT INTO quant.market_price_build_targets (
                    source, token_id, market_id, market_slug, token_side, priority, reason, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'active'
                )
                ON CONFLICT (source, token_id) DO UPDATE SET
                    priority = GREATEST(quant.market_price_build_targets.priority, EXCLUDED.priority),
                    reason = EXCLUDED.reason,
                    status = 'active',
                    updated_at = now()
                """,
                targets,
            )
    return {"events": len(events), "members": len(members), "targets": len(targets)}
