#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

_scripts_root = Path(__file__).resolve().parents[1]
_project_root = _scripts_root.parent
for _path in (str(_project_root), str(_scripts_root)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from api.services import lob_service
from quant.core.db import ClickHouseClient, ClickHouseSettings, PostgresSettings, env_bool, env_int, postgres_connection


DEFAULT_API_BASE = "http://127.0.0.1:18500"
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_API_TIMEOUT_SECONDS = 60
DEFAULT_COVERAGE_API_TIMEOUT_SECONDS = 15
DEFAULT_RUN_TIMEOUT_SECONDS = 120
DEFAULT_POSTGRES_STATEMENT_TIMEOUT_MS = 15000
DEFAULT_LOOKAHEAD_HOURS = 36
DEFAULT_LOOKBACK_HOURS = 12
DEFAULT_PRE_KICKOFF_MINUTES = 60
DEFAULT_POST_KICKOFF_MINUTES = 150
DEFAULT_PRECHECK_MARKET_MINUTES = 90
DEFAULT_PRECHECK_COVERAGE_MINUTES = 75
DEFAULT_ACTIVE_GRACE_MINUTES = 7
DEFAULT_COMPLETENESS_TOLERANCE_MINUTES = 12
DEFAULT_MIN_MARKETS = 1
DEFAULT_STATUS_PATH = "/tmp/polydata/worldcup-lob-guard-status.json"
_WRITTEN_ALERT_SIGNATURES: set[str] = set()


@dataclass(frozen=True)
class GuardPolicy:
    lookahead_hours: int = DEFAULT_LOOKAHEAD_HOURS
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS
    pre_kickoff_minutes: int = DEFAULT_PRE_KICKOFF_MINUTES
    post_kickoff_minutes: int = DEFAULT_POST_KICKOFF_MINUTES
    precheck_market_minutes: int = DEFAULT_PRECHECK_MARKET_MINUTES
    precheck_coverage_minutes: int = DEFAULT_PRECHECK_COVERAGE_MINUTES
    active_grace_minutes: int = DEFAULT_ACTIVE_GRACE_MINUTES
    completeness_tolerance_minutes: int = DEFAULT_COMPLETENESS_TOLERANCE_MINUTES
    min_markets: int = DEFAULT_MIN_MARKETS
    clickhouse_enabled: bool = True


def parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_json(url: str, *, timeout_seconds: int = 20) -> dict[str, Any]:
    with urlopen(url, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def fetch_optional_json(url: str, *, timeout_seconds: int) -> tuple[dict[str, Any], str | None]:
    try:
        return fetch_json(url, timeout_seconds=timeout_seconds), None
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {str(exc)[:240]}"


def api_url(base: str, path: str, params: dict[str, Any] | None = None) -> str:
    root = str(base or DEFAULT_API_BASE).rstrip("/")
    query = ("?" + urlencode(params)) if params else ""
    return f"{root}{path}{query}"


def match_label(match: dict[str, Any]) -> str:
    home = str(match.get("homeTeam") or match.get("home") or "").strip()
    away = str(match.get("awayTeam") or match.get("away") or "").strip()
    if home or away:
        return f"{home} vs {away}".strip()
    return str(match.get("entity") or match.get("id") or "unknown")


def match_key(match: dict[str, Any]) -> str:
    kickoff = str(match.get("kickoffUtc") or match.get("eventTime") or "")[:19]
    return f"{match_label(match).lower()}|{kickoff}"


def match_prefixes(match: dict[str, Any]) -> list[str]:
    return sorted(lob_service._worldcup_fixture_slug_prefixes(match))


def relevant_matches(payload: dict[str, Any], *, now: datetime, policy: GuardPolicy) -> list[dict[str, Any]]:
    matches = [item for item in (payload.get("matches") or payload.get("items") or []) if isinstance(item, dict)]
    start_cutoff = now - timedelta(hours=max(1, int(policy.lookback_hours)))
    end_cutoff = now + timedelta(hours=max(1, int(policy.lookahead_hours)))
    selected: list[dict[str, Any]] = []
    for item in matches:
        kickoff = parse_iso_datetime(item.get("kickoffUtc") or item.get("eventTime"))
        if kickoff is None:
            continue
        if start_cutoff <= kickoff <= end_cutoff:
            selected.append(item)
    return sorted(selected, key=lambda item: parse_iso_datetime(item.get("kickoffUtc") or item.get("eventTime")) or now)


def _like_patterns(prefixes: list[str]) -> list[str]:
    return [f"{prefix}%" for prefix in prefixes]


def market_stats(conn: Any, prefixes: list[str]) -> dict[str, Any]:
    if not prefixes:
        return {"markets": 0, "tokenized": 0, "sampleSlugs": []}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                count(*) AS markets,
                count(*) FILTER (WHERE COALESCE(yes_token_id, '') <> '' AND COALESCE(no_token_id, '') <> '') AS tokenized,
                array_agg(slug ORDER BY slug) FILTER (WHERE COALESCE(yes_token_id, '') <> '' AND COALESCE(no_token_id, '') <> '') AS slugs
            FROM core.markets
            WHERE slug LIKE ANY(%s)
            """,
            (_like_patterns(prefixes),),
        )
        row = cur.fetchone() or {}
    return {
        "markets": int(row.get("markets") or 0),
        "tokenized": int(row.get("tokenized") or 0),
        "sampleSlugs": list(row.get("slugs") or [])[:10],
    }


def snapshot_stats(conn: Any, prefixes: list[str], *, window_start: datetime, window_end: datetime, now: datetime) -> dict[str, Any]:
    if not prefixes:
        return {"rows": 0, "markets": 0, "tokens": 0, "rowsInWindow": 0, "marketsInWindow": 0}
    scan_start = min(window_start, now - timedelta(minutes=15))
    scan_end = max(window_end, now)
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH scoped AS (
                SELECT
                    market_id,
                    token_id,
                    snapshot_source,
                    source,
                    payload,
                    COALESCE(snapshot_timestamp, fetched_at, created_at) AS ts
                FROM quant.clob_orderbook_snapshots
                WHERE market_slug LIKE ANY(%s)
                  AND COALESCE(snapshot_timestamp, fetched_at, created_at) >= %s
                  AND COALESCE(snapshot_timestamp, fetched_at, created_at) <= %s
            )
            SELECT
                count(*) AS rows,
                count(DISTINCT market_id) AS markets,
                count(DISTINCT token_id) AS tokens,
                min(ts) AS first_ts,
                max(ts) AS last_ts,
                min(ts) FILTER (
                    WHERE ts >= %s
                      AND ts <= %s
                ) AS first_ts_in_window,
                max(ts) FILTER (
                    WHERE ts >= %s
                      AND ts <= %s
                ) AS last_ts_in_window,
                count(*) FILTER (
                    WHERE ts >= %s
                      AND ts <= %s
                ) AS rows_in_window,
                count(DISTINCT market_id) FILTER (
                    WHERE ts >= %s
                      AND ts <= %s
                ) AS markets_in_window,
                count(*) FILTER (WHERE ts >= now() - interval '15 minutes') AS rows_15m,
                count(*) FILTER (WHERE COALESCE(snapshot_source, source, payload->>'source') = 'websocket') AS websocket_rows,
                count(*) FILTER (WHERE COALESCE(snapshot_source, source, payload->>'source') = 'registry') AS registry_rows,
                count(*) FILTER (WHERE COALESCE(snapshot_source, source, payload->>'source') = 'rest-book') AS rest_rows
            FROM scoped
            """,
            (
                _like_patterns(prefixes),
                scan_start,
                scan_end,
                window_start,
                window_end,
                window_start,
                window_end,
                window_start,
                window_end,
                window_start,
                window_end,
            ),
        )
        row = cur.fetchone() or {}
    return {
        "rows": int(row.get("rows") or 0),
        "markets": int(row.get("markets") or 0),
        "tokens": int(row.get("tokens") or 0),
        "firstTs": _iso_or_none(row.get("first_ts")),
        "lastTs": _iso_or_none(row.get("last_ts")),
        "firstTsInWindow": _iso_or_none(row.get("first_ts_in_window")),
        "lastTsInWindow": _iso_or_none(row.get("last_ts_in_window")),
        "rowsInWindow": int(row.get("rows_in_window") or 0),
        "marketsInWindow": int(row.get("markets_in_window") or 0),
        "rows15m": int(row.get("rows_15m") or 0),
        "sourceRows": {
            "websocket": int(row.get("websocket_rows") or 0),
            "registry": int(row.get("registry_rows") or 0),
            "restBook": int(row.get("rest_rows") or 0),
        },
    }


def clickhouse_stats(prefixes: list[str]) -> dict[str, Any]:
    if not prefixes:
        return {"enabled": False, "reason": "no-prefixes"}
    client = ClickHouseClient(ClickHouseSettings())
    condition = " OR ".join(f"market_slug LIKE '{_clickhouse_string_literal(prefix + '%')}'" for prefix in prefixes)
    result: dict[str, Any] = {"enabled": True}
    for table in ("quant_lob_delta_fact", "quant_lob_level_fact"):
        try:
            rows = client.query_json_rows(
                f"""
                SELECT
                    count() AS rows,
                    uniqExact(market_id) AS markets,
                    uniqExact(token_id) AS tokens,
                    min(event_ts) AS first_ts,
                    max(event_ts) AS last_ts,
                    countIf(event_ts >= now() - INTERVAL 15 MINUTE) AS rows_15m
                FROM {table}
                WHERE {condition}
                """,
                timeout_seconds=10,
            )
            result[table] = rows[0] if rows else {}
        except Exception as exc:
            result[table] = {"error": type(exc).__name__, "detail": str(exc)[:240]}
    return result


def _clickhouse_string_literal(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def coverage_prefix_count(coverage_payload: dict[str, Any], prefixes: list[str]) -> int | None:
    if coverage_payload.get("_unavailable"):
        return None
    if not prefixes:
        return 0
    count = 0
    for item in coverage_payload.get("items") or []:
        slug = str((item or {}).get("marketSlug") or "").lower()
        if any(slug.startswith(prefix) for prefix in prefixes):
            count += 1
    return count


def evaluate_match(
    match: dict[str, Any],
    *,
    now: datetime,
    policy: GuardPolicy,
    market: dict[str, Any],
    snapshots: dict[str, Any],
    coverage_count: int | None,
    ch_stats: dict[str, Any] | None,
) -> dict[str, Any]:
    kickoff = parse_iso_datetime(match.get("kickoffUtc") or match.get("eventTime"))
    assert kickoff is not None
    start = kickoff - timedelta(minutes=policy.pre_kickoff_minutes)
    end = kickoff + timedelta(minutes=policy.post_kickoff_minutes)
    minutes_until = int((kickoff - now).total_seconds() // 60)
    phase = match_phase(now, start=start, kickoff=kickoff, end=end)
    prefixes = match_prefixes(match)
    checks: list[dict[str, Any]] = []

    if not prefixes:
        if now <= end and minutes_until <= policy.precheck_market_minutes:
            checks.append(check_result("market-prefixes", False, "no fixture slug prefixes resolved"))
        return {
            "key": match_key(match),
            "label": match_label(match),
            "phase": phase,
            "kickoffUtc": kickoff.isoformat().replace("+00:00", "Z"),
            "lobWindow": {"startUtc": start.isoformat().replace("+00:00", "Z"), "endUtc": end.isoformat().replace("+00:00", "Z")},
            "minutesUntilKickoff": minutes_until,
            "prefixes": [],
            "market": market,
            "coverageMarketCount": coverage_count,
            "snapshots": snapshots,
            "clickhouse": ch_stats or {"enabled": False},
            "checks": checks,
            "status": "alert" if checks else "unmapped",
        }

    if minutes_until <= policy.precheck_market_minutes and now < start:
        checks.append(check_result("market-linked", market.get("tokenized", 0) >= policy.min_markets, f"tokenized={market.get('tokenized', 0)}"))
    if minutes_until <= policy.precheck_coverage_minutes and now >= start and now < kickoff:
        checks.append(coverage_check("coverage-candidate", coverage_count, policy))
    if start <= now <= end:
        grace_ready = now >= start + timedelta(minutes=policy.active_grace_minutes)
        checks.append(coverage_check("active-coverage", coverage_count, policy))
        if grace_ready:
            checks.append(check_result("recent-snapshot", snapshots.get("rows15m", 0) > 0, f"rows15m={snapshots.get('rows15m', 0)}"))
    if now > end:
        checks.extend(completeness_checks(snapshots, start=start, end=end, policy=policy))

    status = "ok"
    if any(not item["ok"] for item in checks):
        status = "alert"
    if not checks:
        status = "watching"
    return {
        "key": match_key(match),
        "label": match_label(match),
        "phase": phase,
        "kickoffUtc": kickoff.isoformat().replace("+00:00", "Z"),
        "lobWindow": {"startUtc": start.isoformat().replace("+00:00", "Z"), "endUtc": end.isoformat().replace("+00:00", "Z")},
        "minutesUntilKickoff": minutes_until,
        "prefixes": prefixes,
        "market": market,
        "coverageMarketCount": coverage_count,
        "snapshots": snapshots,
        "clickhouse": ch_stats or {"enabled": False},
        "checks": checks,
        "status": status,
    }


def match_phase(now: datetime, *, start: datetime, kickoff: datetime, end: datetime) -> str:
    if now < start:
        return "preflight"
    if now < kickoff:
        return "pre-kickoff-active"
    if now <= end:
        return "live-or-post-window"
    return "complete"


def completeness_checks(snapshots: dict[str, Any], *, start: datetime, end: datetime, policy: GuardPolicy) -> list[dict[str, Any]]:
    tolerance = timedelta(minutes=policy.completeness_tolerance_minutes)
    first_ts = parse_iso_datetime(snapshots.get("firstTsInWindow"))
    last_ts = parse_iso_datetime(snapshots.get("lastTsInWindow"))
    return [
        check_result("complete-has-rows", snapshots.get("rowsInWindow", 0) > 0, f"rowsInWindow={snapshots.get('rowsInWindow', 0)}"),
        check_result("complete-has-markets", snapshots.get("marketsInWindow", 0) >= policy.min_markets, f"marketsInWindow={snapshots.get('marketsInWindow', 0)}"),
        check_result("complete-start-covered", first_ts is not None and first_ts <= start + tolerance, f"firstTsInWindow={snapshots.get('firstTsInWindow')}"),
        check_result("complete-end-covered", last_ts is not None and last_ts >= end - tolerance, f"lastTsInWindow={snapshots.get('lastTsInWindow')}"),
    ]


def check_result(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": str(detail or "")}


def coverage_check(name: str, coverage_count: int | None, policy: GuardPolicy) -> dict[str, Any]:
    if coverage_count is None:
        return check_result(name, True, "coverage API unavailable; recent snapshot check remains authoritative")
    return check_result(name, coverage_count >= policy.min_markets, f"coverageMarkets={coverage_count}")


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def write_status(path: str, payload: dict[str, Any]) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, default=str, sort_keys=True), encoding="utf-8")
    tmp.replace(target)


def write_alerts(reports: list[dict[str, Any]], *, dry_run: bool) -> int:
    count = 0
    for report in reports:
        failed = [check for check in report.get("checks") or [] if not check.get("ok")]
        if not failed:
            continue
        signature = "|".join([str(report.get("key")), str(report.get("phase")), ",".join(str(item.get("name")) for item in failed)])
        report["alertSignature"] = signature
        if signature in _WRITTEN_ALERT_SIGNATURES:
            continue
        _WRITTEN_ALERT_SIGNATURES.add(signature)
        count += 1
        if dry_run:
            continue
        lob_service.write_lob_dead_letter(
            reason="worldcup_lob_guard_alert",
            raw_payload={"match": report, "failedChecks": failed},
            event_type="worldcup_lob_guard",
            source="worldcup-lob-guard",
            detail=f"{report.get('label')}: " + ", ".join(str(item.get("name")) for item in failed),
        )
    return count


def run_once(
    *,
    api_base: str,
    status_path: str,
    policy: GuardPolicy,
    dry_run: bool = False,
    api_timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
    coverage_api_timeout_seconds: int = DEFAULT_COVERAGE_API_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    dashboard = fetch_json(api_url(api_base, "/runtime/worldcup/dashboard"), timeout_seconds=api_timeout_seconds)
    coverage, coverage_error = fetch_optional_json(
        api_url(api_base, "/runtime/lob/coverage-targets", {"topics": "worldcup", "limit": 250}),
        timeout_seconds=coverage_api_timeout_seconds,
    )
    if coverage_error:
        coverage = {"_unavailable": True, "error": coverage_error}
    matches = relevant_matches(dashboard, now=now, policy=policy)
    reports: list[dict[str, Any]] = []
    with postgres_connection(PostgresSettings(), readonly=True) as conn:
        with conn.cursor() as cur:
            timeout_ms = env_int("POLYDATA_LOB_WORLDCUP_GUARD_POSTGRES_STATEMENT_TIMEOUT_MS", DEFAULT_POSTGRES_STATEMENT_TIMEOUT_MS)
            cur.execute("SELECT set_config('statement_timeout', %s, true)", (str(max(1000, timeout_ms)),))
        for match in matches:
            prefixes = match_prefixes(match)
            kickoff = parse_iso_datetime(match.get("kickoffUtc") or match.get("eventTime"))
            if kickoff is None:
                continue
            start = kickoff - timedelta(minutes=policy.pre_kickoff_minutes)
            end = kickoff + timedelta(minutes=policy.post_kickoff_minutes)
            market = market_stats(conn, prefixes)
            snapshots = snapshot_stats(conn, prefixes, window_start=start, window_end=end, now=now)
            ch_stats = clickhouse_stats(prefixes) if policy.clickhouse_enabled else {"enabled": False}
            reports.append(
                evaluate_match(
                    match,
                    now=now,
                    policy=policy,
                    market=market,
                    snapshots=snapshots,
                    coverage_count=coverage_prefix_count(coverage, prefixes),
                    ch_stats=ch_stats,
                )
            )
    alert_count = write_alerts(reports, dry_run=dry_run)
    payload = {
        "source": "worldcup-lob-guard",
        "generatedAt": utc_now_iso(),
        "dryRun": bool(dry_run),
        "apiBase": api_base,
        "policy": policy.__dict__,
        "summary": {
            "matchCount": len(reports),
            "alerts": alert_count,
            "ok": sum(1 for item in reports if item.get("status") == "ok"),
            "watching": sum(1 for item in reports if item.get("status") == "watching"),
        },
        "coverageDiagnostics": {
            "available": not bool(coverage.get("_unavailable")),
            "error": coverage.get("error"),
            "count": coverage.get("count"),
            "summary": coverage.get("summary") or coverage.get("selectionContext"),
        },
        "matches": reports,
    }
    write_status(status_path, payload)
    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guard World Cup LocalOrderBook coverage and completeness")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--watch", action="store_true", help="Run forever")
    parser.add_argument("--dry-run", action="store_true", help="Do not write dead-letter alerts")
    parser.add_argument("--api-base", default=os.environ.get("POLYDATA_LOB_GUARD_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--status-path", default=os.environ.get("POLYDATA_LOB_WORLDCUP_GUARD_STATUS_PATH", DEFAULT_STATUS_PATH))
    parser.add_argument("--interval", type=int, default=env_int("POLYDATA_LOB_WORLDCUP_GUARD_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
    parser.add_argument("--api-timeout-seconds", type=int, default=env_int("POLYDATA_LOB_WORLDCUP_GUARD_API_TIMEOUT_SECONDS", DEFAULT_API_TIMEOUT_SECONDS))
    parser.add_argument("--coverage-api-timeout-seconds", type=int, default=env_int("POLYDATA_LOB_WORLDCUP_GUARD_COVERAGE_API_TIMEOUT_SECONDS", DEFAULT_COVERAGE_API_TIMEOUT_SECONDS))
    parser.add_argument("--run-timeout-seconds", type=int, default=env_int("POLYDATA_LOB_WORLDCUP_GUARD_RUN_TIMEOUT_SECONDS", DEFAULT_RUN_TIMEOUT_SECONDS))
    parser.add_argument("--lookahead-hours", type=int, default=env_int("POLYDATA_LOB_WORLDCUP_GUARD_LOOKAHEAD_HOURS", DEFAULT_LOOKAHEAD_HOURS))
    parser.add_argument("--lookback-hours", type=int, default=env_int("POLYDATA_LOB_WORLDCUP_GUARD_LOOKBACK_HOURS", DEFAULT_LOOKBACK_HOURS))
    parser.add_argument("--pre-kickoff-minutes", type=int, default=env_int("POLYDATA_LOB_WORLDCUP_PRE_KICKOFF_MINUTES", DEFAULT_PRE_KICKOFF_MINUTES))
    parser.add_argument("--post-kickoff-minutes", type=int, default=env_int("POLYDATA_LOB_WORLDCUP_FALLBACK_MATCH_MINUTES", DEFAULT_POST_KICKOFF_MINUTES))
    parser.add_argument("--min-markets", type=int, default=env_int("POLYDATA_LOB_WORLDCUP_GUARD_MIN_MARKETS", DEFAULT_MIN_MARKETS))
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    policy = GuardPolicy(
        lookahead_hours=args.lookahead_hours,
        lookback_hours=args.lookback_hours,
        pre_kickoff_minutes=args.pre_kickoff_minutes,
        post_kickoff_minutes=args.post_kickoff_minutes,
        min_markets=args.min_markets,
        clickhouse_enabled=env_bool("POLYDATA_LOB_WORLDCUP_GUARD_CLICKHOUSE_ENABLED", False),
    )
    watch = bool(args.watch or not args.once)
    run_timeout_seconds = max(30, int(args.run_timeout_seconds or DEFAULT_RUN_TIMEOUT_SECONDS))
    while True:
        try:
            previous_handler = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, _raise_run_timeout)
            signal.alarm(run_timeout_seconds)
            try:
                payload = run_once(
                    api_base=args.api_base,
                    status_path=args.status_path,
                    policy=policy,
                    dry_run=args.dry_run,
                    api_timeout_seconds=max(10, int(args.api_timeout_seconds or DEFAULT_API_TIMEOUT_SECONDS)),
                    coverage_api_timeout_seconds=max(3, int(args.coverage_api_timeout_seconds or DEFAULT_COVERAGE_API_TIMEOUT_SECONDS)),
                )
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous_handler)
            print(json.dumps(payload, ensure_ascii=True, default=str, sort_keys=True), flush=True)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            error_payload = {"source": "worldcup-lob-guard", "generatedAt": utc_now_iso(), "status": "error", "error": str(exc)}
            write_status(args.status_path, error_payload)
            print(json.dumps(error_payload, ensure_ascii=True, sort_keys=True), file=sys.stderr, flush=True)
            if not watch:
                return 1
        if not watch:
            return 0
        time.sleep(max(60, int(args.interval or DEFAULT_INTERVAL_SECONDS)))


def _raise_run_timeout(signum: int, frame: Any) -> None:
    raise TimeoutError("worldcup LOB guard run timed out")


if __name__ == "__main__":
    raise SystemExit(main())
