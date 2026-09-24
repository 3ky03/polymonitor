#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build PostgreSQL event/group serving rows for the market workspace.

The API should not join Gamma + markets + price + status tables on every
market-group click. This job materializes a compact event-first view from local
PostgreSQL tables and uses Gamma only to supplement event identity for active
markets that were discovered before event_id/event_slug were persisted.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import (  # type: ignore
    add_db_cli_args,
    configure_db_from_args,
    describe_db_target,
    get_connection,
    is_postgres_backend,
)


NOISY_MARKET_TERMS = (
    "hide-from-new",
    "recurring",
    "onchain-registry",
    "updown-5m",
    "updown-15m",
)

GENERIC_TAGS = {"all", "featured", "hide-from-new", "recurring", "onchain-registry"}


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


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _timestamp(value: Any) -> float:
    if not value:
        return 0.0
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        if not text:
            return 0.0
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


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


def _json_for_db(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _normalize_condition(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_noisy(row: Dict[str, Any]) -> bool:
    tags = " ".join(str(tag) for tag in _json_list(row.get("tags")))
    text = " ".join(str(row.get(key) or "") for key in ("title", "slug", "event_slug", "event_title", "category"))
    normalized = f"{text} {tags}".lower()
    if " up or down - " in normalized:
        return True
    return any(term in normalized for term in NOISY_MARKET_TERMS)


def _category(row: Dict[str, Any], tags: List[Any]) -> str:
    raw = str(row.get("category") or "").strip().lower()
    text = " ".join(
        [
            str(row.get("event_title") or row.get("title") or ""),
            str(row.get("event_slug") or row.get("slug") or ""),
            raw,
            " ".join(str(tag) for tag in tags),
        ]
    ).lower()
    if any(term in text for term in ("bitcoin", "ethereum", "solana", "xrp", "dogecoin", "crypto", "btc", "eth")):
        return "crypto"
    if any(term in text for term in ("election", "president", "senate", "congress", "politic")):
        return "politics"
    if any(term in text for term in ("nba", "nfl", "mlb", "nhl", "soccer", "tennis", "sports", "itf")):
        return "sports"
    if any(term in text for term in ("fed", "inflation", "rate", "economy", "finance", "macro", "valuation")):
        return "macro"
    if any(term in text for term in ("ai", "openai", "tech", "robot")):
        return "tech"
    for tag in tags:
        slug = _slugify(tag)
        if slug and slug not in GENERIC_TAGS:
            return slug
    return raw or "market"


def _label_for_market(event_title: str, row: Dict[str, Any]) -> str:
    title = str(row.get("title") or "").strip()
    if event_title and title.startswith(event_title):
        candidate = title[len(event_title) :].strip(" -:·")
        if candidate:
            return candidate
    match = re.match(r"Will\s+(.+?)\s+win\b", title, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return title or str(row.get("slug") or row.get("id") or "Outcome")


def _outcome_key(label: str, fallback: Any) -> str:
    return _slugify(label) or _slugify(fallback) or "outcome"


def _column_exists(conn, table_schema: str, table_name: str, column_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = ? AND table_name = ? AND column_name = ?
        LIMIT 1
        """,
        (table_schema, table_name, column_name),
    ).fetchone()
    return bool(row)


def _best_effort_ddl(conn, sql: str, *, label: str) -> bool:
    try:
        conn.execute(sql)
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        print(f"[event-serving] ddl-skip label={label} error={exc}", file=sys.stderr)
        return False


def ensure_schema(conn) -> None:
    conn.execute("SET lock_timeout TO '5s'")
    conn.execute("CREATE SCHEMA IF NOT EXISTS core")
    conn.commit()
    for column in ("event_id", "event_slug", "event_title"):
        if not _column_exists(conn, "core", "markets", column):
            _best_effort_ddl(conn, f"ALTER TABLE core.markets ADD COLUMN {column} TEXT", label=f"markets.{column}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.event_market_serving (
            serving_key TEXT PRIMARY KEY,
            group_id TEXT NOT NULL UNIQUE,
            event_id TEXT,
            event_slug TEXT,
            event_title TEXT,
            title TEXT NOT NULL,
            category TEXT,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ,
            end_date TIMESTAMPTZ,
            volume_24h NUMERIC(38, 18) NOT NULL DEFAULT 0,
            gamma_volume_24h NUMERIC(38, 18),
            gamma_volume_updated_at TIMESTAMPTZ,
            gamma_volume_fetched_at TIMESTAMPTZ,
            trade_count_24h BIGINT NOT NULL DEFAULT 0,
            last_activity_at TIMESTAMPTZ,
            outcome_count INTEGER NOT NULL DEFAULT 0,
            default_market_id BIGINT REFERENCES core.markets(id) ON DELETE SET NULL,
            default_condition_id TEXT,
            default_gamma_market_id TEXT,
            default_outcome_key TEXT,
            top_outcomes JSONB NOT NULL DEFAULT '[]'::jsonb,
            outcomes JSONB NOT NULL DEFAULT '[]'::jsonb,
            completion_status TEXT NOT NULL DEFAULT 'OPEN',
            is_trading_closed BOOLEAN NOT NULL DEFAULT FALSE,
            active_rank DOUBLE PRECISION NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'postgres',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()
    for column, column_type in (
        ("gamma_volume_24h", "NUMERIC(38, 18)"),
        ("gamma_volume_updated_at", "TIMESTAMPTZ"),
        ("gamma_volume_fetched_at", "TIMESTAMPTZ"),
    ):
        if not _column_exists(conn, "core", "event_market_serving", column):
            _best_effort_ddl(
                conn,
                f"ALTER TABLE core.event_market_serving ADD COLUMN {column} {column_type}",
                label=f"event_market_serving.{column}",
            )
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_markets_event_id ON core.markets (event_id)",
        "CREATE INDEX IF NOT EXISTS idx_markets_event_slug ON core.markets (event_slug)",
        """
        CREATE INDEX IF NOT EXISTS idx_event_market_serving_active_desc
          ON core.event_market_serving (volume_24h DESC, last_activity_at DESC NULLS LAST, serving_key)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_event_market_serving_status_active_desc
          ON core.event_market_serving (completion_status, is_trading_closed, volume_24h DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_event_market_serving_event_default
          ON core.event_market_serving (event_id, default_market_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_event_market_serving_rank_desc
          ON core.event_market_serving (active_rank DESC, volume_24h DESC, last_activity_at DESC NULLS LAST)
        """,
    ):
        parts = sql.split()
        label = parts[5] if len(parts) > 5 else "index"
        _best_effort_ddl(conn, sql, label=label)


def _fetch_gamma_events(base_url: str, *, pages: int, target_events: int, order: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    seen: set[str] = set()
    limit = 100
    for page in range(max(1, pages)):
        query = urllib.parse.urlencode(
            {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": page * limit,
                "order": order,
                "ascending": "false",
            }
        )
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/events?{query}",
            headers={"Accept": "application/json", "User-Agent": "polydata-serving-sync/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"[gamma] fetch failed order={order} page={page}: {exc}", file=sys.stderr)
            break
        page_events = payload if isinstance(payload, list) else ((payload or {}).get("events") or (payload or {}).get("data") or [])
        if not page_events:
            break
        for event in page_events:
            if not isinstance(event, dict):
                continue
            identity = str(event.get("id") or event.get("slug") or "").strip()
            if not identity or identity in seen:
                continue
            seen.add(identity)
            events.append(event)
            if len(events) >= target_events:
                return events
        if len(page_events) < limit:
            break
    return events


def supplement_event_identity_from_gamma(conn, *, base_url: str, pages: int, target_events: int) -> int:
    updates: Dict[Tuple[str, str, str], Tuple[str, str, str, str, str, str]] = {}
    for order in ("volume24hr", "startDate"):
        for event in _fetch_gamma_events(base_url, pages=pages, target_events=target_events, order=order):
            event_id = str(event.get("id") or "").strip()
            event_slug = str(event.get("slug") or event.get("ticker") or "").strip()
            event_title = str(event.get("title") or event.get("name") or "").strip()
            if not (event_id or event_slug):
                continue
            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                gamma_market_id = str(market.get("id") or market.get("gamma_market_id") or "").strip()
                condition_id = _normalize_condition(market.get("conditionId") or market.get("condition_id"))
                slug = str(market.get("slug") or "").strip()
                if not (gamma_market_id or condition_id or slug):
                    continue
                updates[(gamma_market_id, condition_id, slug)] = (
                    event_id,
                    event_slug,
                    event_title,
                    gamma_market_id,
                    condition_id,
                    slug,
                )
    if not updates:
        return 0
    conn.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS tmp_event_market_identity (
            event_id TEXT,
            event_slug TEXT,
            event_title TEXT,
            gamma_market_id TEXT,
            condition_id TEXT,
            slug TEXT
        ) ON COMMIT DROP
        """
    )
    conn.execute("TRUNCATE tmp_event_market_identity")
    conn.executemany(
        """
        INSERT INTO tmp_event_market_identity (
            event_id, event_slug, event_title, gamma_market_id, condition_id, slug
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        list(updates.values()),
    )
    changed = 0
    for join_sql in (
        "m.gamma_market_id = t.gamma_market_id AND t.gamma_market_id <> ''",
        "lower(m.condition_id) = t.condition_id AND t.condition_id <> ''",
        "m.slug = t.slug AND t.slug <> ''",
    ):
        cur = conn.execute(
            f"""
            UPDATE core.markets m
            SET
              event_id = COALESCE(NULLIF(t.event_id, ''), m.event_id),
              event_slug = COALESCE(NULLIF(t.event_slug, ''), m.event_slug),
              event_title = COALESCE(NULLIF(t.event_title, ''), m.event_title)
            FROM tmp_event_market_identity t
            WHERE {join_sql}
              AND (
                m.event_id IS DISTINCT FROM COALESCE(NULLIF(t.event_id, ''), m.event_id)
                OR m.event_slug IS DISTINCT FROM COALESCE(NULLIF(t.event_slug, ''), m.event_slug)
                OR m.event_title IS DISTINCT FROM COALESCE(NULLIF(t.event_title, ''), m.event_title)
              )
            """
        )
        changed += max(0, cur.rowcount)
    conn.commit()
    return changed


def _load_candidate_markets(conn, *, max_markets: int) -> List[Dict[str, Any]]:
    cur = conn.execute(
        """
        WITH candidate_pool AS MATERIALIZED (
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
        ),
        ranked_candidates AS MATERIALIZED (
          SELECT
            m.id,
            COALESCE(mls.volume_24h, 0) AS rank_volume_24h,
            COALESCE(mls.trade_count_24h, 0) AS rank_trade_count_24h,
            COALESCE(mls.last_trade_at, mls.latest_trade_at, mlp.latest_trade_at, m.created_at) AS rank_activity_at
          FROM candidate_pool cp
          JOIN core.markets m ON m.id = cp.market_id
          LEFT JOIN core.market_list_serving mls ON mls.market_id = m.id
          LEFT JOIN core.market_latest_prices mlp ON mlp.market_id = m.id
          LEFT JOIN core.market_status_snapshot mss ON mss.market_id = m.id
          WHERE
            COALESCE(mss.is_final, FALSE) = FALSE
            OR COALESCE(mls.volume_24h, 0) > 0
            OR m.created_at >= now() - interval '45 days'
          ORDER BY
            rank_volume_24h DESC,
            rank_trade_count_24h DESC,
            rank_activity_at DESC NULLS LAST,
            m.id DESC
          LIMIT ?
        )
        SELECT
          m.id,
          m.gamma_market_id,
          m.event_id,
          m.event_slug,
          m.event_title,
          m.slug,
          m.condition_id,
          m.question_id,
          m.oracle,
          m.yes_token_id,
          m.no_token_id,
          m.title,
          m.category,
          m.tags,
          m.clob_token_ids,
          m.created_at,
          m.end_date,
          COALESCE(mlp.latest_yes_price, mls.latest_price) AS yes_price,
          mlp.latest_no_price AS no_price,
          mls.price_24h_ago,
          COALESCE(mls.volume_24h, 0) AS volume_24h,
          COALESCE(mls.trade_count_24h, 0) AS trade_count_24h,
          COALESCE(mls.last_trade_at, mls.latest_trade_at, mlp.latest_trade_at, m.created_at) AS last_activity_at,
          COALESCE(mss.completion_status, 'OPEN') AS completion_status,
          COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
          COALESCE(mss.is_final, FALSE) AS is_final,
          COALESCE(mss.gamma_closed, FALSE) AS gamma_closed
        FROM ranked_candidates rc
        JOIN core.markets m ON m.id = rc.id
        LEFT JOIN core.market_list_serving mls ON mls.market_id = m.id
        LEFT JOIN core.market_latest_prices mlp ON mlp.market_id = m.id
        LEFT JOIN core.market_status_snapshot mss ON mss.market_id = m.id
        ORDER BY
          rc.rank_volume_24h DESC,
          rc.rank_trade_count_24h DESC,
          rc.rank_activity_at DESC NULLS LAST,
          rc.id DESC
        """,
        (int(max_markets), int(max_markets), int(max_markets), int(max_markets)),
    )
    return [_as_dict(row) for row in cur.fetchall()]


def _default_score(outcome: Dict[str, Any]) -> Tuple[float, float, int]:
    price = _to_float(outcome.get("yesPrice"))
    volume = _to_float(outcome.get("volume24h")) or 0.0
    trades = _to_int(outcome.get("tradeCount24h"))
    label = str(outcome.get("label") or "").lower()
    score = 0.0
    if outcome.get("marketId") is not None:
        score += 12.0
    if outcome.get("yesTokenId"):
        score += 8.0
    if volume > 0:
        score += min(40.0, volume ** 0.25)
    if trades > 0:
        score += min(20.0, float(trades))
    if price is not None:
        if 0.02 < price < 0.98:
            score += 40.0
        if price <= 0.01 or price >= 0.99:
            score -= 60.0
    if "completed match" in label or label.strip() in {"completed", "match completed"}:
        score -= 30.0
    return (score, volume, trades)


def _is_live_price(outcome: Dict[str, Any]) -> bool:
    price = _to_float(outcome.get("yesPrice"))
    return price is not None and 0.02 < price < 0.98


def _is_terminal_price(outcome: Dict[str, Any]) -> bool:
    price = _to_float(outcome.get("yesPrice"))
    return price is not None and (price <= 0.01 or price >= 0.99)


def _is_flat_mid_price(outcome: Dict[str, Any]) -> bool:
    price = _to_float(outcome.get("yesPrice"))
    return price is not None and abs(price - 0.5) <= 0.005


def _build_groups(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if _is_noisy(row):
            continue
        if bool(row.get("gamma_closed")) or bool(row.get("is_trading_closed")) or bool(row.get("is_final")):
            continue
        event_id = str(row.get("event_id") or "").strip()
        event_slug = str(row.get("event_slug") or "").strip()
        serving_key = event_id or (f"slug:{event_slug}" if event_slug else f"market:{row.get('id')}")
        event_title = str(row.get("event_title") or "").strip()
        title = event_title or str(row.get("title") or "").strip() or f"Market {row.get('id')}"
        tags = _json_list(row.get("tags"))
        group = groups.setdefault(
            serving_key,
            {
                "serving_key": serving_key,
                "group_id": f"event:{event_id}" if event_id else serving_key,
                "event_id": event_id or None,
                "event_slug": event_slug or None,
                "event_title": event_title or None,
                "title": title,
                "category": _category(row, tags),
                "tags": tags,
                "created_at": row.get("created_at"),
                "end_date": row.get("end_date"),
                "volume_24h": 0.0,
                "trade_count_24h": 0,
                "last_activity_at": row.get("last_activity_at"),
                "outcomes": [],
                "statuses": [],
                "closed_flags": [],
            },
        )
        if _timestamp(row.get("created_at")) and (
            not group.get("created_at") or _timestamp(row.get("created_at")) < _timestamp(group.get("created_at"))
        ):
            group["created_at"] = row.get("created_at")
        if _timestamp(row.get("end_date")) and (
            not group.get("end_date") or _timestamp(row.get("end_date")) > _timestamp(group.get("end_date"))
        ):
            group["end_date"] = row.get("end_date")
        if _timestamp(row.get("last_activity_at")) > _timestamp(group.get("last_activity_at")):
            group["last_activity_at"] = row.get("last_activity_at")
        volume = _to_float(row.get("volume_24h")) or 0.0
        raw_trades = _to_float(row.get("trade_count_24h"))
        trades = int(raw_trades) if raw_trades is not None and raw_trades > 0 else None
        group["volume_24h"] += volume
        group["trade_count_24h"] += trades or 0
        yes_price = _to_float(row.get("yes_price"))
        no_price = _to_float(row.get("no_price"))
        if no_price is None and yes_price is not None:
            no_price = max(0.0, min(1.0, 1.0 - yes_price))
        price_24h_ago = _to_float(row.get("price_24h_ago"))
        change_24h = None
        if yes_price is not None and price_24h_ago is not None:
            change_24h = yes_price - price_24h_ago
        label = _label_for_market(event_title, row)
        token_ids = [str(item) for item in _json_list(row.get("clob_token_ids")) if str(item).strip()]
        outcome = {
            "outcomeKey": _outcome_key(label, row.get("id")),
            "marketId": row.get("id"),
            "localMarketId": row.get("id"),
            "gammaMarketId": row.get("gamma_market_id"),
            "label": label,
            "title": row.get("title") or title,
            "yesPrice": yes_price,
            "noPrice": no_price,
            "change24h": change_24h,
            "volume24h": volume,
            "tradeCount24h": trades,
            "volumeScope": "market",
            "volumeSource": "gamma-market",
            "lastTradeAt": row.get("last_activity_at"),
            "conditionId": row.get("condition_id"),
            "slug": row.get("slug"),
            "yesTokenId": row.get("yes_token_id") or (token_ids[0] if token_ids else None),
            "noTokenId": row.get("no_token_id") or (token_ids[1] if len(token_ids) > 1 else None),
        }
        group["outcomes"].append(outcome)
        group["statuses"].append(str(row.get("completion_status") or "OPEN"))
        group["closed_flags"].append(bool(row.get("is_trading_closed")))

    materialized: List[Dict[str, Any]] = []
    for group in groups.values():
        outcomes = group["outcomes"]
        if not outcomes:
            continue
        priced_outcomes = [outcome for outcome in outcomes if _to_float(outcome.get("yesPrice")) is not None]
        top_outcomes = sorted(priced_outcomes, key=_default_score, reverse=True)[:5]
        default_outcome = max(outcomes, key=_default_score)
        statuses = set(group.pop("statuses", []))
        closed_flags = group.pop("closed_flags", [])
        if "OPEN" in statuses:
            completion_status = "OPEN"
        elif "ENDED_AWAITING_ORACLE" in statuses:
            completion_status = "ENDED_AWAITING_ORACLE"
        elif statuses:
            completion_status = sorted(statuses)[0]
        else:
            completion_status = "OPEN"
        volume = _to_float(group.get("volume_24h")) or 0.0
        trades = _to_int(group.get("trade_count_24h"))
        live_outcomes = sum(1 for outcome in outcomes if _is_live_price(outcome))
        terminal_outcomes = sum(1 for outcome in outcomes if _is_terminal_price(outcome))
        flat_mid_outcomes = sum(1 for outcome in outcomes if _is_flat_mid_price(outcome))
        terminal_ratio = terminal_outcomes / max(1, len(outcomes))
        recency_days = max(0.0, (time.time() - (_timestamp(group.get("last_activity_at")) or _timestamp(group.get("created_at")))) / 86400)
        recency_boost = max(0.0, 25.0 - recency_days)
        volume_component = math.log10(max(volume, 0.0) + 1.0) * 16.0
        live_price_boost = min(58.0, live_outcomes * 12.0)
        terminal_penalty = terminal_ratio * 150.0
        if live_outcomes == 0 and trades == 0:
            terminal_penalty += 110.0
        if live_outcomes > 0 and flat_mid_outcomes == live_outcomes and trades == 0:
            terminal_penalty += 80.0
        active_rank = (
            volume_component
            + min(50.0, trades * 0.5)
            + recency_boost
            + live_price_boost
            - terminal_penalty
        )
        group.update(
            {
                "outcome_count": len(outcomes),
                "default_market_id": default_outcome.get("marketId"),
                "default_condition_id": default_outcome.get("conditionId"),
                "default_gamma_market_id": default_outcome.get("gammaMarketId"),
                "default_outcome_key": default_outcome.get("outcomeKey"),
                "top_outcomes": top_outcomes,
                "completion_status": completion_status,
                "is_trading_closed": bool(closed_flags) and all(closed_flags),
                "active_rank": active_rank,
            }
        )
        materialized.append(group)
    materialized.sort(key=lambda group: (group.get("active_rank") or 0.0, group.get("volume_24h") or 0.0), reverse=True)
    return materialized


def refresh_serving(conn, *, max_markets: int, prune: bool) -> int:
    rows = _load_candidate_markets(conn, max_markets=max_markets)
    groups = _build_groups(rows)
    upsert_rows = [
        (
            group.get("serving_key"),
            group.get("group_id"),
            group.get("event_id"),
            group.get("event_slug"),
            group.get("event_title"),
            group.get("title"),
            group.get("category"),
            _json_for_db(group.get("tags") or []),
            group.get("created_at"),
            group.get("end_date"),
            str(group.get("volume_24h") or 0),
            int(group.get("trade_count_24h") or 0),
            group.get("last_activity_at"),
            int(group.get("outcome_count") or 0),
            group.get("default_market_id"),
            group.get("default_condition_id"),
            group.get("default_gamma_market_id"),
            group.get("default_outcome_key"),
            _json_for_db(group.get("top_outcomes") or []),
            _json_for_db(group.get("outcomes") or []),
            group.get("completion_status") or "OPEN",
            bool(group.get("is_trading_closed")),
            float(group.get("active_rank") or 0.0),
        )
        for group in groups
    ]
    if not upsert_rows:
        return 0
    conn.executemany(
        """
        INSERT INTO core.event_market_serving AS current (
          serving_key, group_id, event_id, event_slug, event_title, title, category, tags,
          created_at, end_date, volume_24h, trade_count_24h, last_activity_at, outcome_count,
          default_market_id, default_condition_id, default_gamma_market_id, default_outcome_key,
          top_outcomes, outcomes, completion_status, is_trading_closed, active_rank
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?::jsonb,
          ?, ?, ?, ?, ?, ?,
          ?, ?, ?, ?,
          ?::jsonb, ?::jsonb, ?, ?, ?
        )
        ON CONFLICT (serving_key) DO UPDATE SET
          group_id = EXCLUDED.group_id,
          event_id = EXCLUDED.event_id,
          event_slug = EXCLUDED.event_slug,
          event_title = EXCLUDED.event_title,
          title = EXCLUDED.title,
          category = EXCLUDED.category,
          tags = EXCLUDED.tags,
          created_at = EXCLUDED.created_at,
          end_date = EXCLUDED.end_date,
          volume_24h = CASE
            WHEN current.gamma_volume_24h IS NOT NULL
             AND current.gamma_volume_fetched_at >= now() - interval '30 minutes'
            THEN current.gamma_volume_24h
            ELSE EXCLUDED.volume_24h
          END,
          trade_count_24h = EXCLUDED.trade_count_24h,
          last_activity_at = EXCLUDED.last_activity_at,
          outcome_count = EXCLUDED.outcome_count,
          default_market_id = EXCLUDED.default_market_id,
          default_condition_id = EXCLUDED.default_condition_id,
          default_gamma_market_id = EXCLUDED.default_gamma_market_id,
          default_outcome_key = EXCLUDED.default_outcome_key,
          top_outcomes = EXCLUDED.top_outcomes,
          outcomes = EXCLUDED.outcomes,
          completion_status = EXCLUDED.completion_status,
          is_trading_closed = EXCLUDED.is_trading_closed,
          active_rank = EXCLUDED.active_rank,
          source = CASE
            WHEN current.gamma_volume_24h IS NOT NULL
             AND current.gamma_volume_fetched_at >= now() - interval '30 minutes'
            THEN 'gamma-event'
            ELSE 'postgres'
          END,
          updated_at = now()
        WHERE (
          current.group_id,
          current.event_id,
          current.event_slug,
          current.event_title,
          current.title,
          current.category,
          current.tags,
          current.created_at,
          current.end_date,
          current.volume_24h,
          current.trade_count_24h,
          current.last_activity_at,
          current.outcome_count,
          current.default_market_id,
          current.default_condition_id,
          current.default_gamma_market_id,
          current.default_outcome_key,
          current.top_outcomes,
          current.outcomes,
          current.completion_status,
          current.is_trading_closed,
          current.active_rank,
          current.source
        ) IS DISTINCT FROM (
          EXCLUDED.group_id,
          EXCLUDED.event_id,
          EXCLUDED.event_slug,
          EXCLUDED.event_title,
          EXCLUDED.title,
          EXCLUDED.category,
          EXCLUDED.tags,
          EXCLUDED.created_at,
          EXCLUDED.end_date,
          CASE
            WHEN current.gamma_volume_24h IS NOT NULL
             AND current.gamma_volume_fetched_at >= now() - interval '30 minutes'
            THEN current.gamma_volume_24h
            ELSE EXCLUDED.volume_24h
          END,
          EXCLUDED.trade_count_24h,
          EXCLUDED.last_activity_at,
          EXCLUDED.outcome_count,
          EXCLUDED.default_market_id,
          EXCLUDED.default_condition_id,
          EXCLUDED.default_gamma_market_id,
          EXCLUDED.default_outcome_key,
          EXCLUDED.top_outcomes,
          EXCLUDED.outcomes,
          EXCLUDED.completion_status,
          EXCLUDED.is_trading_closed,
          EXCLUDED.active_rank,
          CASE
            WHEN current.gamma_volume_24h IS NOT NULL
             AND current.gamma_volume_fetched_at >= now() - interval '30 minutes'
            THEN 'gamma-event'
            ELSE 'postgres'
          END
        )
        """,
        upsert_rows,
    )
    if prune:
        keys = [str(row[0]) for row in upsert_rows if row[0]]
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS tmp_event_market_serving_keys (serving_key TEXT PRIMARY KEY) ON COMMIT DROP")
        conn.execute("TRUNCATE tmp_event_market_serving_keys")
        conn.executemany(
            "INSERT INTO tmp_event_market_serving_keys (serving_key) VALUES (?) ON CONFLICT DO NOTHING",
            [(key,) for key in keys],
        )
        conn.execute(
            """
            DELETE FROM core.event_market_serving s
            WHERE s.source IN ('postgres', 'gamma-event')
              AND NOT EXISTS (
                SELECT 1 FROM tmp_event_market_serving_keys k WHERE k.serving_key = s.serving_key
              )
            """
        )
    conn.commit()
    return len(upsert_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh PostgreSQL event/group market serving table.")
    add_db_cli_args(parser)
    parser.add_argument("--interval", type=int, default=60, help="Loop interval seconds when --watch is set")
    parser.add_argument("--watch", action="store_true", help="Refresh forever")
    parser.add_argument("--once", action="store_true", help="Refresh once and exit")
    parser.add_argument("--max-markets", type=int, default=80000, help="Maximum candidate markets per refresh")
    parser.add_argument("--prune", action="store_true", help="Delete stale serving rows not present in this refresh")
    parser.add_argument("--gamma-api-base", default="https://gamma-api.polymarket.com", help="Gamma API base for identity supplement")
    parser.add_argument("--skip-gamma-supplement", action="store_true", help="Do not supplement event ids from Gamma")
    parser.add_argument("--gamma-pages", type=int, default=8, help="Gamma event pages to scan per order")
    parser.add_argument("--gamma-target-events", type=int, default=700, help="Gamma events per order for supplement")
    args = parser.parse_args()
    configure_db_from_args(args)
    if not is_postgres_backend():
        raise SystemExit("event_market_serving is PostgreSQL-only; run with --backend postgres")
    if not args.watch:
        args.once = True

    print(f"[event-serving] target={describe_db_target()}")
    while True:
        started = time.time()
        conn = get_connection()
        try:
            ensure_schema(conn)
            supplemented = 0
            if not args.skip_gamma_supplement:
                supplemented = supplement_event_identity_from_gamma(
                    conn,
                    base_url=args.gamma_api_base,
                    pages=args.gamma_pages,
                    target_events=args.gamma_target_events,
                )
            refreshed = refresh_serving(conn, max_markets=args.max_markets, prune=args.prune)
            elapsed = time.time() - started
            print(
                f"[event-serving] refreshed={refreshed} gamma_supplement_updates={supplemented} "
                f"elapsed={elapsed:.2f}s at={_now_iso()}",
                flush=True,
            )
        finally:
            conn.close()
        if args.once:
            break
        time.sleep(max(5, int(args.interval)))


if __name__ == "__main__":
    main()
