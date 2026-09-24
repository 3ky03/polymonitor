#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_BASE_URL = os.environ.get("POLYDATA_API_BASE_URL") or "https://polymonitor.club/wm-api"


def get_json(base_url: str, path: str, timeout: float) -> Any:
    url = f"{base_url.rstrip('/')}{path}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def as_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def collect_market_ids(base_url: str, sort: str, page_size: int, timeout: float) -> List[int]:
    ids: List[int] = []
    seen = set()

    def add(value: Any) -> None:
        market_id = as_int(value)
        if market_id is None or market_id in seen:
            return
        seen.add(market_id)
        ids.append(market_id)

    params = urllib.parse.urlencode({"sort": sort, "pageSize": page_size})
    try:
        groups = get_json(base_url, f"/market-groups?{params}", timeout)
        for group in groups.get("items") or []:
            add(group.get("defaultMarketId"))
            for outcome in list(group.get("outcomes") or []) + list(group.get("topOutcomes") or []):
                if isinstance(outcome, dict):
                    add(outcome.get("marketId"))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] market-groups sample failed: {exc}", file=sys.stderr)

    params = urllib.parse.urlencode({"status": "active", "pageSize": page_size})
    try:
        markets = get_json(base_url, f"/markets?{params}", timeout)
        for market in markets.get("items") or []:
            if isinstance(market, dict):
                add(market.get("id"))
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] markets sample failed: {exc}", file=sys.stderr)

    return ids


def id_matches(payload: Dict[str, Any], expected_market_id: int, keys: Iterable[str]) -> bool:
    values = [as_int(payload.get(key)) for key in keys]
    values = [value for value in values if value is not None]
    return not values or expected_market_id in values


def validate_workspace(market_id: int, workspace: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    market = workspace.get("market") if isinstance(workspace.get("market"), dict) else {}
    identity = workspace.get("identity") if isinstance(workspace.get("identity"), dict) else {}
    price = workspace.get("price") if isinstance(workspace.get("price"), dict) else {}
    chart = workspace.get("chart") if isinstance(workspace.get("chart"), dict) else {}
    oracle = workspace.get("oracle") if isinstance(workspace.get("oracle"), dict) else {}
    selected = workspace.get("selectedOutcome") if isinstance(workspace.get("selectedOutcome"), dict) else {}
    health = workspace.get("health") if isinstance(workspace.get("health"), dict) else {}

    if as_int(market.get("id")) not in (None, market_id):
        errors.append(f"market.id={market.get('id')}")
    if not id_matches(identity, market_id, ("localMarketId", "marketId")):
        errors.append(f"identity market mismatch {identity.get('localMarketId')}/{identity.get('marketId')}")
    if selected and as_int(selected.get("marketId")) != market_id:
        errors.append(f"selectedOutcome.marketId={selected.get('marketId')}")
    if price and not id_matches(price, market_id, ("localMarketId", "marketId")):
        errors.append(f"price market mismatch {price.get('localMarketId')}/{price.get('marketId')}")
    if chart and not id_matches(chart, market_id, ("localMarketId", "marketId")):
        errors.append(f"chart market mismatch {chart.get('localMarketId')}/{chart.get('marketId')}")
    if oracle and not id_matches(oracle, market_id, ("localMarketId", "marketId")):
        errors.append(f"oracle market mismatch {oracle.get('localMarketId')}/{oracle.get('marketId')}")
    if str(health.get("oracleStatus") or "").lower() == "mismatch":
        errors.append("health.oracleStatus=mismatch")
    for issue in health.get("issues") or []:
        if "mismatch" in str(issue).lower():
            errors.append(f"health issue={issue}")
    return errors


def audit_market(base_url: str, market_id: int, timeout: float) -> Tuple[bool, str]:
    try:
        workspace = get_json(base_url, f"/markets/{market_id}/workspace", timeout)
    except urllib.error.HTTPError as exc:
        return False, f"workspace HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"workspace error {exc}"

    errors = validate_workspace(market_id, workspace)

    for name, path in (
        ("oracle", f"/markets/{market_id}/oracle"),
        ("chart", f"/markets/{market_id}/chart?range=1d&interval=5m"),
    ):
        try:
            payload = get_json(base_url, path, timeout)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name} endpoint failed: {exc}")
            continue
        if isinstance(payload, dict) and not id_matches(payload, market_id, ("localMarketId", "marketId")):
            errors.append(f"{name} endpoint market mismatch")

    if errors:
        return False, "; ".join(errors)
    health = workspace.get("health") if isinstance(workspace.get("health"), dict) else {}
    return True, f"health={health.get('level') or 'unknown'} oracle={health.get('oracleStatus') or 'unknown'} chart={health.get('chartStatus') or 'unknown'}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit market workspace API consistency.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL, for example https://polymonitor.club/wm-api")
    parser.add_argument("--sort", default="active", choices=["active", "new", "volume"], help="market-groups sort used for sampling")
    parser.add_argument("--page-size", type=int, default=80)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--sleep", type=float, default=0.05)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    market_ids = collect_market_ids(args.base_url, args.sort, args.page_size, args.timeout)[: max(args.limit, 0)]
    if not market_ids:
        print("[fail] no market ids collected")
        return 2

    failures = 0
    for index, market_id in enumerate(market_ids, 1):
        ok, message = audit_market(args.base_url, market_id, args.timeout)
        status = "ok" if ok else "fail"
        print(f"[{status}] {index:03d}/{len(market_ids)} market_id={market_id} {message}")
        if not ok:
            failures += 1
        if args.sleep > 0:
            time.sleep(args.sleep)

    print(f"[summary] checked={len(market_ids)} failures={failures} base_url={args.base_url}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
