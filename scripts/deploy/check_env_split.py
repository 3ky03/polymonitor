#!/usr/bin/env python3
"""Validate local-vs-remote polyData environment files without printing secrets."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


SECRET_MARKERS = ("PASSWORD", "SECRET", "TOKEN", "KEY", "NODE_URL", "RPC")

REMOTE_REQUIRED = {
    "POLYDATA_DEPLOY_ROLE": "gcp-api",
    "POLYMARKET_DB_BACKEND": "postgres",
    "POLYDATA_POSTGRES_HOST": "127.0.0.1",
    "POLYDATA_POSTGRES_PORT": "45432",
    "POLYDATA_POSTGRES_USER": "poly_user",
    "POLYDATA_POSTGRES_DATABASE": "poly_data_core",
    "POLYDATA_POSTGRES_SEARCH_PATH": "core,oracle,ops,public",
    "POLYDATA_API_READONLY": "1",
    "POLYDATA_API_HOST": "127.0.0.1",
    "POLYDATA_API_PORT": "18500",
    "POLYDATA_REDIS_URL": "redis://127.0.0.1:6379/0",
    "POLYDATA_REDIS_PREFIX": "polydata:",
    "POLYDATA_SNAPSHOT_SQLITE_PATH": "/opt/polyData/data/panel_snapshots.sqlite3",
    "POLYDATA_SNAPSHOT_PREWARM": "1",
    "POLYDATA_GUNICORN_WORKERS": "3",
    "POLYDATA_GUNICORN_THREADS": "4",
    "POLYDATA_GUNICORN_MAX_REQUESTS": "300",
    "POLYDATA_GUNICORN_MAX_REQUESTS_JITTER": "60",
    "POLYDATA_MARKETS_RUNTIME_PRICES": "0",
    "POLYDATA_MARKETS_LATEST_SNAPSHOT_FALLBACK": "1",
}

LOCAL_REQUIRED = {
    "POLYDATA_DEPLOY_ROLE": "local-collector",
    "POLYMARKET_DB_BACKEND": "postgres",
    "POLYDATA_POSTGRES_HOST": "127.0.0.1",
    "POLYDATA_POSTGRES_DATABASE": "poly_data_core",
    "POLYDATA_SNAPSHOT_PREWARM": "0",
}

REMOTE_UNNEEDED_PREFIXES = ("VITE_",)
REMOTE_UNNEEDED_KEYS = {
    "POLYDATA_PUBLIC_WEB_URL",
    "POLYMARKET_RPC_URL",
    "NODE_URL",
    "POLYDATA_GCP_SSH_TARGET",
    "POLYDATA_LOCAL_POSTGRES_PORT",
    "POLYDATA_REMOTE_POSTGRES_PORT",
}


def parse_env(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def masked(key: str, value: str | None) -> str:
    if value is None:
        return "<missing>"
    if any(marker in key.upper() for marker in SECRET_MARKERS):
        return "<set>" if value else "<empty>"
    return value


def check_required(values: Dict[str, str], required: Dict[str, str]) -> List[Tuple[str, str]]:
    issues: List[Tuple[str, str]] = []
    for key, expected in required.items():
        actual = values.get(key)
        if actual != expected:
            issues.append((key, f"expected {masked(key, expected)}, got {masked(key, actual)}"))
    return issues


def check_remote(values: Dict[str, str]) -> tuple[List[str], List[str]]:
    errors = [f"{key}: {detail}" for key, detail in check_required(values, REMOTE_REQUIRED)]
    warnings: List[str] = []
    if not values.get("POLYDATA_POSTGRES_PASSWORD") and not values.get("POLYMARKET_PostgreSQL_PASSWORD"):
        errors.append("POLYDATA_POSTGRES_PASSWORD: expected <set>, got <missing>")
    for key in sorted(values):
        if key in REMOTE_UNNEEDED_KEYS or any(key.startswith(prefix) for prefix in REMOTE_UNNEEDED_PREFIXES):
            warnings.append(f"{key}: remote readonly API does not need this local/sync/frontend variable")
    for key in sorted(values):
        if key.startswith("POLYMARKET_MYSQL_"):
            warnings.append(f"{key}: legacy MySQL variable is ignored by the PostgreSQL runtime")
    return errors, warnings


def check_local(values: Dict[str, str]) -> tuple[List[str], List[str]]:
    errors = [f"{key}: {detail}" for key, detail in check_required(values, LOCAL_REQUIRED)]
    warnings: List[str] = []
    if not values.get("POLYDATA_POSTGRES_PASSWORD") and not values.get("POLYMARKET_PostgreSQL_PASSWORD"):
        errors.append("POLYDATA_POSTGRES_PASSWORD: expected <set>, got <missing>")
    if values.get("POLYDATA_API_READONLY") == "1":
        warnings.append("POLYDATA_API_READONLY=1: local env is usually for sync/write/development, not readonly API")
    if values.get("POLYDATA_SNAPSHOT_PREWARM") not in {None, "", "0", "false", "False", "off", "OFF"}:
        warnings.append("POLYDATA_SNAPSHOT_PREWARM: local collector should not run API/runtime prewarm loops")
    for key in sorted(values):
        if key.startswith("POLYMARKET_MYSQL_"):
            warnings.append(f"{key}: legacy MySQL variable is ignored by the PostgreSQL runtime")
    for key in ("POLYDATA_GUNICORN_WORKERS", "POLYDATA_GUNICORN_THREADS", "POLYDATA_GUNICORN_MAX_REQUESTS"):
        if key in values:
            warnings.append(f"{key}: local .env usually does not need remote Gunicorn service tuning")
    return errors, warnings


def print_section(title: str, lines: Iterable[str]) -> None:
    print(title)
    materialized = list(lines)
    if not materialized:
        print("  none")
        return
    for line in materialized:
        print(f"  - {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether a polyData env file matches its local or remote role.")
    parser.add_argument("--role", choices=("local", "remote"), required=True)
    parser.add_argument("--env", required=True, type=Path)
    args = parser.parse_args()

    values = parse_env(args.env)
    errors, warnings = check_local(values) if args.role == "local" else check_remote(values)

    print(f"env={args.env}")
    print(f"role={args.role}")
    print(f"keys={len(values)}")
    print_section("errors", errors)
    print_section("warnings", warnings)
    print("verdict=PASS" if not errors else "verdict=FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
