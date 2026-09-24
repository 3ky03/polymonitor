#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep frontend prices-history rows warm for active quant events."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from quant.core.db import PostgresSettings, postgres_connection
from quant.prices.frontend_backfill import insert_frontend_points
from quant.prices.frontend_client import DEFAULT_CLOB_API_BASE, FrontendPriceClient


DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_MAX_EVENTS = 8
DEFAULT_MAX_TOKENS = 160
DEFAULT_FIDELITY_MINUTES = 60
DEFAULT_HISTORY_INTERVAL = "all"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class QuantFrontendActiveWatcher:
    def __init__(
        self,
        *,
        interval_seconds: int,
        max_events: int,
        max_tokens: int,
        fidelity_minutes: int,
        history_interval: str,
        clob_api_base: str,
        pause_seconds: float,
    ) -> None:
        self.interval_seconds = max(60, int(interval_seconds or DEFAULT_INTERVAL_SECONDS))
        self.max_events = max(1, int(max_events or DEFAULT_MAX_EVENTS))
        self.max_tokens = max(1, int(max_tokens or DEFAULT_MAX_TOKENS))
        self.fidelity_minutes = max(1, int(fidelity_minutes or DEFAULT_FIDELITY_MINUTES))
        self.history_interval = str(history_interval or DEFAULT_HISTORY_INTERVAL).strip() or DEFAULT_HISTORY_INTERVAL
        self.pause_seconds = max(0.0, float(pause_seconds or 0.0))
        self.client = FrontendPriceClient(clob_api_base=clob_api_base)

    def discover_members(self) -> list[dict[str, Any]]:
        with postgres_connection(PostgresSettings(), readonly=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH active_events AS (
                        SELECT
                            e.event_slug,
                            max(e.event_title) AS event_title,
                            bool_or(COALESCE(m.active, FALSE)) AS active,
                            max(e.end_date) AS end_date,
                            sum(COALESCE(m.block_rows, 0)) AS block_rows,
                            sum(COALESCE(m.frontend_rows, 0)) AS frontend_rows,
                            max(m.latest_timestamp) AS latest_timestamp
                        FROM quant.market_event_metadata e
                        JOIN quant.market_event_members m ON m.event_slug = e.event_slug
                        GROUP BY e.event_slug
                    ),
                    selected_events AS (
                        SELECT
                            event_slug,
                            row_number() OVER (
                                ORDER BY
                                    (event_slug = '2026-fifa-world-cup-winner-595') DESC,
                                    active DESC,
                                    latest_timestamp ASC NULLS FIRST,
                                    block_rows DESC,
                                    event_slug ASC
                            ) AS priority
                        FROM active_events
                        WHERE active
                           OR end_date >= now() - interval '14 days'
                           OR event_slug = '2026-fifa-world-cup-winner-595'
                        LIMIT %s
                    )
                    SELECT
                        m.event_slug,
                        m.market_id,
                        m.market_slug,
                        m.outcome_order,
                        m.outcome_label,
                        m.token_yes_id,
                        m.token_no_id
                    FROM quant.market_event_members m
                    JOIN selected_events e ON e.event_slug = m.event_slug
                    WHERE m.token_yes_id IS NOT NULL OR m.token_no_id IS NOT NULL
                    ORDER BY e.priority ASC, m.outcome_order ASC, m.market_id ASC
                    LIMIT %s
                    """,
                    (self.max_events, self.max_tokens),
                )
                return [dict(row) for row in cur.fetchall()]

    def refresh_member_stats(self, conn: Any, event_slugs: set[str]) -> None:
        with conn.cursor() as cur:
            for event_slug in sorted(event_slugs):
                cur.execute(
                    """
                    UPDATE quant.market_event_members m
                    SET
                        frontend_rows = COALESCE((
                            SELECT count(*)
                            FROM quant.market_token_frontend_price_1m p
                            WHERE p.token_id IN (m.token_yes_id, m.token_no_id)
                        ), 0),
                        latest_timestamp = (
                            SELECT max(p.ts_minute)
                            FROM quant.market_token_frontend_price_1m p
                            WHERE p.token_id IN (m.token_yes_id, m.token_no_id)
                        ),
                        coverage_status = CASE
                            WHEN EXISTS (
                                SELECT 1
                                FROM quant.market_token_frontend_price_1m p
                                WHERE p.token_id IN (m.token_yes_id, m.token_no_id)
                                LIMIT 1
                            ) THEN 'ready'
                            WHEN COALESCE(m.orderfilled_rows, 0) > 0 THEN 'queued'
                            ELSE m.coverage_status
                        END,
                        updated_at = now()
                    WHERE m.event_slug = %s
                    """,
                    (event_slug,),
                )

    def run_once(self) -> dict[str, Any]:
        started_at = time.perf_counter()
        members = self.discover_members()
        rows_written = 0
        tokens_seen = 0
        failures: list[str] = []
        event_slugs: set[str] = set()
        with postgres_connection(PostgresSettings(), readonly=False) as conn:
            for member in members:
                event_slugs.add(str(member.get("event_slug") or ""))
                token_specs = [
                    (member.get("token_yes_id"), "YES"),
                    (member.get("token_no_id"), "NO"),
                ]
                for token_id, token_side in token_specs:
                    if not token_id or tokens_seen >= self.max_tokens:
                        continue
                    tokens_seen += 1
                    try:
                        points = self.client.fetch_prices_history_interval(
                            str(token_id),
                            interval=self.history_interval,
                            fidelity_minutes=self.fidelity_minutes,
                        )
                        rows_written += insert_frontend_points(
                            conn,
                            market_id=int(member["market_id"]),
                            market_slug=member.get("market_slug"),
                            token_id=str(token_id),
                            token_side=token_side,
                            points=points,
                            fidelity_minutes=self.fidelity_minutes,
                        )
                    except Exception as exc:
                        failures.append(f"{member.get('market_slug')}:{token_side}:{exc}")
                    if self.pause_seconds > 0:
                        time.sleep(self.pause_seconds)
            self.refresh_member_stats(conn, {slug for slug in event_slugs if slug})
        return {
            "status": "ok" if not failures else "degraded",
            "members": len(members),
            "tokens": tokens_seen,
            "rowsWritten": rows_written,
            "errors": len(failures),
            "durationMs": int((time.perf_counter() - started_at) * 1000),
            "updatedAt": utc_now_iso(),
        }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Keep active quant frontend prices-history rows warm")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("POLYDATA_QUANT_FRONTEND_ACTIVE_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)))
    parser.add_argument("--max-events", type=int, default=int(os.environ.get("POLYDATA_QUANT_FRONTEND_ACTIVE_MAX_EVENTS", DEFAULT_MAX_EVENTS)))
    parser.add_argument("--max-tokens", type=int, default=int(os.environ.get("POLYDATA_QUANT_FRONTEND_ACTIVE_MAX_TOKENS", DEFAULT_MAX_TOKENS)))
    parser.add_argument("--fidelity-minutes", type=int, default=int(os.environ.get("POLYDATA_QUANT_FRONTEND_ACTIVE_FIDELITY_MINUTES", DEFAULT_FIDELITY_MINUTES)))
    parser.add_argument("--history-interval", default=os.environ.get("POLYDATA_QUANT_FRONTEND_ACTIVE_HISTORY_INTERVAL", DEFAULT_HISTORY_INTERVAL))
    parser.add_argument("--pause-seconds", type=float, default=float(os.environ.get("POLYDATA_QUANT_FRONTEND_ACTIVE_PAUSE_SECONDS", "0.05")))
    parser.add_argument("--clob-api-base", default=os.environ.get("POLYMARKET_CLOB_API_BASE", DEFAULT_CLOB_API_BASE))
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    watcher = QuantFrontendActiveWatcher(
        interval_seconds=args.interval,
        max_events=args.max_events,
        max_tokens=args.max_tokens,
        fidelity_minutes=args.fidelity_minutes,
        history_interval=args.history_interval,
        clob_api_base=args.clob_api_base,
        pause_seconds=args.pause_seconds,
    )
    print(
        f"[quant-frontend-active] max_events={watcher.max_events} max_tokens={watcher.max_tokens} fidelity={watcher.fidelity_minutes}",
        file=sys.stderr,
    )
    if not args.watch:
        print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        return 0
    while True:
        try:
            print(json.dumps(watcher.run_once(), ensure_ascii=False), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[quant-frontend-active] ERROR watch loop failed: {exc}", file=sys.stderr)
        time.sleep(watcher.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
