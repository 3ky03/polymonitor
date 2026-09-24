#!/usr/bin/env python3
"""Audit whether market workspace panels can hydrate for sampled markets.

The frontend can only be trusted if the API can consistently bind:

- market-group outcome -> local market id
- local market id -> detail / price / chart / oracle / LOB / trades payloads

This script intentionally exercises the same HTTP endpoints as the UI instead
of only checking database rows. It is safe to run after market sync, before a
deploy, or from a cron/systemd health check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

Severity = Literal["critical", "warn", "info"]


@dataclass
class Finding:
    severity: Severity
    scope: str
    message: str
    endpoint: str | None = None
    market_id: int | None = None
    event_id: str | None = None
    outcome_key: str | None = None
    elapsed_ms: float | None = None


@dataclass
class EndpointResult:
    status: int | str
    elapsed_ms: float
    data: Any
    error: str | None = None


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def default_api_base() -> str:
    env_base = (os.environ.get("POLYDATA_API_BASE_URL") or os.environ.get("VITE_POLYDATA_API_BASE_URL") or "").strip()
    if env_base and not env_base.endswith("/wm-api"):
        return env_base.rstrip("/")
    host = os.environ.get("POLYDATA_API_HOST", "127.0.0.1")
    port = os.environ.get("POLYDATA_API_PORT", "5000")
    return f"http://{host}:{port}"


class ApiClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)

    def get(self, path: str, *, timeout: float | None = None) -> EndpointResult:
        started = time.perf_counter()
        url = f"{self.base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout or self.timeout) as response:
                raw = response.read()
                elapsed_ms = (time.perf_counter() - started) * 1000
                payload = json.loads(raw.decode("utf-8")) if raw else None
                return EndpointResult(status=response.status, elapsed_ms=elapsed_ms, data=payload)
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            try:
                payload: Any = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = None
            return EndpointResult(status=exc.code, elapsed_ms=elapsed_ms, data=payload, error=str(exc))
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            return EndpointResult(status="ERR", elapsed_ms=elapsed_ms, data=None, error=repr(exc))


def add_finding(findings: list[Finding], severity: Severity, scope: str, message: str, **kwargs: Any) -> None:
    findings.append(Finding(severity=severity, scope=scope, message=message, **kwargs))


def is_success(result: EndpointResult) -> bool:
    return result.status == 200 and result.error is None


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def numeric(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def market_group_paths(sorts: Iterable[str], page_size: int) -> list[str]:
    paths: list[str] = []
    for sort in sorts:
        params = urllib.parse.urlencode({"status": "active", "pageSize": page_size, "sort": sort})
        paths.append(f"/market-groups?{params}")
    return paths


def audit_groups(
    client: ApiClient,
    findings: list[Finding],
    *,
    sorts: Iterable[str],
    page_size: int,
    max_groups: int,
) -> tuple[list[dict[str, Any]], list[int]]:
    groups_by_event: dict[str, dict[str, Any]] = {}
    market_ids: list[int] = []

    for path in market_group_paths(sorts, page_size):
        result = client.get(path)
        if not is_success(result):
            add_finding(findings, "critical", "market-groups", f"Cannot load group list: {result.error or result.status}", endpoint=path, elapsed_ms=result.elapsed_ms)
            continue
        items = as_list(as_dict(result.data).get("items"))
        if not items:
            add_finding(findings, "warn", "market-groups", "Group list returned zero items", endpoint=path, elapsed_ms=result.elapsed_ms)
        for group in items:
            if not isinstance(group, dict):
                continue
            event_id = str(group.get("eventId") or group.get("groupId") or "").strip()
            if event_id and event_id not in groups_by_event:
                groups_by_event[event_id] = group

    groups = list(groups_by_event.values())[:max_groups]
    for group in groups:
        event_id = str(group.get("eventId") or "").strip()
        title = str(group.get("title") or "untitled")
        outcomes = as_list(group.get("outcomes"))
        default_market_id = group.get("defaultMarketId")
        if not default_market_id:
            add_finding(findings, "critical", "market-group-binding", f"Group has no defaultMarketId: {title}", event_id=event_id)
        missing = [outcome for outcome in outcomes if isinstance(outcome, dict) and outcome.get("marketId") in (None, "")]
        if missing:
            add_finding(
                findings,
                "critical",
                "market-group-binding",
                f"Group has {len(missing)}/{len(outcomes)} outcomes without local marketId: {title}",
                event_id=event_id,
            )
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            market_id = outcome.get("marketId")
            if market_id not in (None, ""):
                try:
                    market_ids.append(int(market_id))
                except (TypeError, ValueError):
                    add_finding(findings, "critical", "market-group-binding", f"Invalid outcome marketId={market_id!r}: {title}", event_id=event_id, outcome_key=str(outcome.get("outcomeKey") or ""))

        if event_id:
            detail = client.get(f"/market-groups/{urllib.parse.quote(event_id)}/detail")
            if not is_success(detail):
                add_finding(findings, "critical", "market-group-detail", f"Cannot load group detail: {title}", endpoint=f"/market-groups/{event_id}/detail", event_id=event_id, elapsed_ms=detail.elapsed_ms)
            else:
                detail_outcomes = as_list(as_dict(detail.data).get("outcomes"))
                detail_missing = [outcome for outcome in detail_outcomes if isinstance(outcome, dict) and outcome.get("marketId") in (None, "")]
                if detail_missing:
                    add_finding(
                        findings,
                        "critical",
                        "market-group-detail",
                        f"Group detail has {len(detail_missing)}/{len(detail_outcomes)} outcomes without local marketId: {title}",
                        endpoint=f"/market-groups/{event_id}/detail",
                        event_id=event_id,
                        elapsed_ms=detail.elapsed_ms,
                    )

    return groups, market_ids


def audit_explicit_events(client: ApiClient, findings: list[Finding], event_ids: Iterable[str]) -> list[int]:
    market_ids: list[int] = []
    for event_id in event_ids:
        event_id = str(event_id or "").strip()
        if not event_id:
            continue
        path = f"/market-groups/{urllib.parse.quote(event_id)}/detail"
        result = client.get(path)
        if not is_success(result):
            add_finding(findings, "critical", "market-group-detail", f"Cannot load explicit group detail: {result.error or result.status}", endpoint=path, event_id=event_id, elapsed_ms=result.elapsed_ms)
            continue
        data = as_dict(result.data)
        outcomes = as_list(data.get("outcomes"))
        if not outcomes:
            add_finding(findings, "critical", "market-group-detail", "Explicit group detail returned zero outcomes", endpoint=path, event_id=event_id, elapsed_ms=result.elapsed_ms)
        missing = [outcome for outcome in outcomes if isinstance(outcome, dict) and outcome.get("marketId") in (None, "")]
        if missing:
            add_finding(
                findings,
                "critical",
                "market-group-detail",
                f"Explicit group detail has {len(missing)}/{len(outcomes)} outcomes without local marketId",
                endpoint=path,
                event_id=event_id,
                elapsed_ms=result.elapsed_ms,
            )
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            market_id = outcome.get("marketId")
            if market_id not in (None, ""):
                try:
                    market_ids.append(int(market_id))
                except (TypeError, ValueError):
                    add_finding(findings, "critical", "market-group-detail", f"Invalid explicit outcome marketId={market_id!r}", endpoint=path, event_id=event_id)
    return market_ids


def audit_active_markets(client: ApiClient, findings: list[Finding], *, page_size: int) -> list[int]:
    path = f"/markets?{urllib.parse.urlencode({'status': 'active', 'page': 1, 'pageSize': page_size})}"
    result = client.get(path)
    if not is_success(result):
        add_finding(findings, "critical", "markets", f"Cannot load active market list: {result.error or result.status}", endpoint=path, elapsed_ms=result.elapsed_ms)
        return []
    items = as_list(as_dict(result.data).get("items"))
    if not items:
        add_finding(findings, "critical", "markets", "Active markets list returned zero items", endpoint=path, elapsed_ms=result.elapsed_ms)
    market_ids: list[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            market_ids.append(int(item.get("id")))
        except (TypeError, ValueError):
            add_finding(findings, "critical", "markets", f"Invalid active market id: {item.get('id')!r}", endpoint=path)
    return market_ids


def chart_quality(points: list[Any]) -> tuple[int, int]:
    values: set[str] = set()
    for point in points:
        if not isinstance(point, dict):
            continue
        value = point.get("yesPrice")
        if value in (None, ""):
            value = point.get("value")
        if value in (None, ""):
            value = point.get("price")
        if value not in (None, ""):
            values.add(str(value))
    return len(points), len(values)


def audit_market(client: ApiClient, findings: list[Finding], market_id: int, *, include_lob: bool) -> dict[str, Any]:
    summary: dict[str, Any] = {"marketId": market_id}

    detail_path = f"/markets/{market_id}/detail"
    detail = client.get(detail_path)
    if not is_success(detail):
        add_finding(findings, "critical", "market-detail", f"Cannot load market detail: {detail.error or detail.status}", endpoint=detail_path, market_id=market_id, elapsed_ms=detail.elapsed_ms)
        return summary
    detail_data = as_dict(detail.data)
    market = as_dict(detail_data.get("market"))
    price = as_dict(detail_data.get("price"))
    oracle = as_dict(detail_data.get("oracle"))
    summary["title"] = market.get("title")
    summary["price"] = price.get("latestPrice")
    summary["volume24h"] = price.get("volume24h")
    if int(market.get("id") or 0) != market_id:
        add_finding(findings, "critical", "market-detail", "Detail payload market.id does not match requested id", endpoint=detail_path, market_id=market_id, elapsed_ms=detail.elapsed_ms)
    if not price:
        add_finding(findings, "critical", "market-detail", "Detail payload is missing price", endpoint=detail_path, market_id=market_id, elapsed_ms=detail.elapsed_ms)
    if not oracle:
        add_finding(findings, "critical", "market-detail", "Detail payload is missing oracle status payload", endpoint=detail_path, market_id=market_id, elapsed_ms=detail.elapsed_ms)

    oracle_path = f"/markets/{market_id}/oracle"
    oracle_result = client.get(oracle_path)
    if not is_success(oracle_result):
        add_finding(findings, "critical", "market-oracle", f"Cannot load market oracle: {oracle_result.error or oracle_result.status}", endpoint=oracle_path, market_id=market_id, elapsed_ms=oracle_result.elapsed_ms)
    else:
        oracle_data = as_dict(oracle_result.data)
        status = str(oracle_data.get("completionStatus") or "UNKNOWN")
        timeline_count = len(as_list(oracle_data.get("timeline")))
        if int(oracle_data.get("marketId") or 0) != market_id:
            add_finding(findings, "critical", "market-oracle", "Oracle payload marketId does not match requested id", endpoint=oracle_path, market_id=market_id, elapsed_ms=oracle_result.elapsed_ms)
        if status not in {"OPEN", "UNKNOWN"} and timeline_count == 0:
            add_finding(findings, "warn", "market-oracle", f"Non-open status has no oracle timeline: {status}", endpoint=oracle_path, market_id=market_id, elapsed_ms=oracle_result.elapsed_ms)
        if status == "OPEN" and timeline_count == 0:
            add_finding(findings, "info", "market-oracle", "Open market has no oracle events yet", endpoint=oracle_path, market_id=market_id, elapsed_ms=oracle_result.elapsed_ms)

    for range_name, interval in (("1h", "1m"), ("1d", "5m"), ("1w", "1h")):
        path = f"/markets/{market_id}/chart?{urllib.parse.urlencode({'range': range_name, 'interval': interval})}"
        result = client.get(path, timeout=max(client.timeout, 10))
        if not is_success(result):
            add_finding(findings, "warn", "market-chart", f"Cannot load {range_name} chart: {result.error or result.status}", endpoint=path, market_id=market_id, elapsed_ms=result.elapsed_ms)
            continue
        data = as_dict(result.data)
        points = as_list(data.get("points"))
        point_count, distinct_count = chart_quality(points)
        if data.get("range") == "snapshot" or point_count <= 2:
            add_finding(findings, "warn", "market-chart", f"{range_name} chart fell back to snapshot/short series ({point_count} points)", endpoint=path, market_id=market_id, elapsed_ms=result.elapsed_ms)
        elif distinct_count <= 1:
            add_finding(findings, "warn", "market-chart", f"{range_name} chart is flat ({point_count} points, {distinct_count} distinct values)", endpoint=path, market_id=market_id, elapsed_ms=result.elapsed_ms)

    if include_lob:
        lob_path = f"/runtime/lob/{market_id}"
        lob = client.get(lob_path, timeout=max(client.timeout, 8))
        if not is_success(lob):
            add_finding(findings, "warn", "market-lob", f"Cannot load LOB: {lob.error or lob.status}", endpoint=lob_path, market_id=market_id, elapsed_ms=lob.elapsed_ms)
        else:
            data = as_dict(lob.data)
            yes = as_dict(data.get("yes"))
            no = as_dict(data.get("no"))
            yes_levels = len(as_list(yes.get("bids"))) + len(as_list(yes.get("asks")))
            no_levels = len(as_list(no.get("bids"))) + len(as_list(no.get("asks")))
            if data.get("error"):
                add_finding(findings, "warn", "market-lob", f"LOB returned error: {data.get('error')}", endpoint=lob_path, market_id=market_id, elapsed_ms=lob.elapsed_ms)
            elif yes_levels == 0 and no_levels == 0:
                add_finding(findings, "warn", "market-lob", "LOB returned zero levels for both YES/NO", endpoint=lob_path, market_id=market_id, elapsed_ms=lob.elapsed_ms)

    trades_path = f"/markets/{market_id}/trades?limit=12"
    trades = client.get(trades_path)
    if not is_success(trades):
        add_finding(findings, "warn", "market-trades", f"Cannot load trades: {trades.error or trades.status}", endpoint=trades_path, market_id=market_id, elapsed_ms=trades.elapsed_ms)
    else:
        rows = as_list(trades.data)
        volume = numeric(price.get("volume24h"))
        trade_count = numeric(price.get("tradeCount24h"))
        if not rows and ((volume or 0) > 0 or (trade_count or 0) > 0):
            add_finding(findings, "warn", "market-trades", "Serving volume/tradeCount exists but raw OrderFilled rows are empty", endpoint=trades_path, market_id=market_id, elapsed_ms=trades.elapsed_ms)

    return summary


def severity_counts(findings: list[Finding]) -> dict[str, int]:
    counts = {"critical": 0, "warn": 0, "info": 0}
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit market workspace API hydration and binding quality")
    parser.add_argument("--api-base", default=None, help="API base URL. Default: POLYDATA_API_BASE_URL/VITE_POLYDATA_API_BASE_URL or POLYDATA_API_HOST:POLYDATA_API_PORT")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout seconds per endpoint")
    parser.add_argument("--group-page-size", type=int, default=80, help="Market group page size per sort")
    parser.add_argument("--market-page-size", type=int, default=40, help="Plain active market sample size")
    parser.add_argument("--max-groups", type=int, default=80, help="Max groups to audit")
    parser.add_argument("--max-markets", type=int, default=80, help="Max unique markets to audit across groups and list")
    parser.add_argument("--sorts", default="new,active,volume", help="Comma-separated market-group sorts to sample")
    parser.add_argument("--event-id", action="append", default=[], help="Specific market-group event id to audit. Can be repeated or comma-separated")
    parser.add_argument("--market-id", action="append", default=[], help="Specific local market id to audit. Can be repeated or comma-separated")
    parser.add_argument("--skip-lob", action="store_true", help="Skip LOB runtime checks")
    parser.add_argument("--json-out", default="", help="Optional JSON report path")
    parser.add_argument("--fail-on", choices=("critical", "warn", "never"), default="critical", help="Exit nonzero on this severity or worse")
    return parser.parse_args()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv(repo_root / ".env")
    args = parse_args()
    client = ApiClient(args.api_base or default_api_base(), timeout=args.timeout)
    findings: list[Finding] = []
    sorts = [item.strip() for item in str(args.sorts or "").split(",") if item.strip()]
    explicit_event_ids = [item.strip() for raw in args.event_id for item in str(raw).split(",") if item.strip()]
    explicit_market_ids: list[int] = []
    for raw in args.market_id:
        for item in str(raw).split(","):
            item = item.strip()
            if not item:
                continue
            try:
                explicit_market_ids.append(int(item))
            except ValueError:
                add_finding(findings, "critical", "args", f"Invalid --market-id value: {item!r}")

    groups, group_market_ids = audit_groups(
        client,
        findings,
        sorts=sorts or ["new", "active", "volume"],
        page_size=args.group_page_size,
        max_groups=args.max_groups,
    )
    explicit_event_market_ids = audit_explicit_events(client, findings, explicit_event_ids)
    active_market_ids = audit_active_markets(client, findings, page_size=args.market_page_size)
    market_ids = list(dict.fromkeys([*explicit_market_ids, *explicit_event_market_ids, *group_market_ids, *active_market_ids]))[: max(1, int(args.max_markets))]
    market_summaries = [
        audit_market(client, findings, market_id, include_lob=not args.skip_lob)
        for market_id in market_ids
    ]

    counts = severity_counts(findings)
    report = {
        "apiBase": client.base_url,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sample": {
            "groups": len(groups),
            "markets": len(market_ids),
            "groupMarketIds": len(group_market_ids),
            "explicitEventMarketIds": len(explicit_event_market_ids),
            "activeMarketIds": len(active_market_ids),
        },
        "counts": counts,
        "findings": [asdict(finding) for finding in findings],
        "markets": market_summaries,
    }

    if args.json_out:
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"[audit] api={client.base_url}")
    print(f"[audit] groups={len(groups)} markets={len(market_ids)} critical={counts['critical']} warn={counts['warn']} info={counts['info']}")
    for finding in findings:
        if finding.severity == "info":
            continue
        target = f" market={finding.market_id}" if finding.market_id is not None else ""
        event = f" event={finding.event_id}" if finding.event_id else ""
        endpoint = f" endpoint={finding.endpoint}" if finding.endpoint else ""
        print(f"[{finding.severity}] {finding.scope}:{target}{event}{endpoint} {finding.message}")

    if args.fail_on == "never":
        return 0
    if args.fail_on == "warn" and (counts["critical"] or counts["warn"]):
        return 2
    if args.fail_on == "critical" and counts["critical"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
