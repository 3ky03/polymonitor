#!/usr/bin/env python3
"""Validate Panel health contracts and build a per-Panel health snapshot."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ops.lib.incidents import update_incident_history
from scripts.ops.lib.redaction import redact_text
from scripts.ops.lib.snapshot import age_seconds, atomic_write_json, operations_state_dir, read_json, utc_now_iso
from scripts.ops.lib.status import Status, aggregate, normalize


DEFAULT_CONTRACT = REPO_ROOT / "config" / "operations" / "panel_contracts.json"
PANEL_INDEX = REPO_ROOT / "webpage" / "src" / "panels" / "modules" / "index.ts"
DEFAULT_LOB_RUNTIME_STATUS_PATH = Path(
    "/tmp/polydata/local-orderbook-websocket-status.json"
)
IMPORT_RE = re.compile(
    r"import\s+\{\s*panel\s+as\s+([A-Za-z0-9_]+)\s*\}\s+from\s+['\"]\./([^'\"]+)['\"]"
)
PANEL_MODULES_RE = re.compile(
    r"const\s+(?:ALL_)?PANEL_MODULES[^=]*=\s*\[(.*?)\]\s*;",
    re.DOTALL,
)
PANEL_ID_RE = re.compile(
    r"export\s+const\s+panel(?:\s*:[^=]+)?\s*=.*?\bid\s*:\s*['\"]([^'\"]+)['\"]",
    re.DOTALL,
)


def _module_file(relative: str) -> Path:
    base = PANEL_INDEX.parent / relative
    candidates = (base.with_suffix(".tsx"), base.with_suffix(".ts"), base / "index.tsx", base / "index.ts")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError(f"cannot resolve Panel module {relative}")


def discover_registered_panel_ids() -> list[str]:
    index_text = PANEL_INDEX.read_text(encoding="utf-8")
    imports = dict(IMPORT_RE.findall(index_text))
    all_match = PANEL_MODULES_RE.search(index_text)
    if not all_match:
        raise ValueError("PANEL_MODULES registry not found")
    aliases = [
        token.strip()
        for token in all_match.group(1).split(",")
        if re.fullmatch(r"[A-Za-z0-9_]+", token.strip())
    ]
    panel_ids = []
    for alias in aliases:
        relative = imports.get(alias)
        if not relative:
            raise ValueError(f"registry alias {alias} has no Panel import")
        source = _module_file(relative).read_text(encoding="utf-8")
        panel_match = PANEL_ID_RE.search(source)
        if not panel_match:
            raise ValueError(f"module {relative} does not expose a static Panel id")
        panel_ids.append(panel_match.group(1))
    return panel_ids


def validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    groups = contract.get("groups")
    if not isinstance(groups, list):
        raise ValueError("panel contract groups must be a list")
    excluded_ids = {str(value) for value in contract.get("excludedPanelIds", [])}
    excluded_prefixes = tuple(str(value) for value in contract.get("excludedPanelPrefixes", []))
    registered = discover_registered_panel_ids()
    active = {
        panel_id
        for panel_id in registered
        if panel_id not in excluded_ids and not panel_id.startswith(excluded_prefixes)
    }
    declared: dict[str, int] = {}
    errors: list[str] = []
    required_fields = {
        "owner",
        "healthStrategy",
        "dataSources",
        "degradationPolicy",
        "panelIds",
    }
    allowed_strategies = {"seed", "database", "external-api", "browser"}
    for index, group in enumerate(groups):
        missing_fields = sorted(required_fields - set(group))
        if missing_fields:
            errors.append(f"group[{index}] missing {','.join(missing_fields)}")
        if group.get("healthStrategy") not in allowed_strategies:
            errors.append(f"group[{index}] has unsupported healthStrategy")
        if group.get("healthStrategy") != "browser" and not group.get("expectedFreshnessSeconds"):
            errors.append(f"group[{index}] needs expectedFreshnessSeconds")
        if group.get("healthStrategy") == "database" and not group.get("representativeProbe"):
            errors.append(f"group[{index}] database contract needs representativeProbe")
        for panel_id in group.get("panelIds", []):
            panel_id = str(panel_id)
            declared[panel_id] = declared.get(panel_id, 0) + 1
    duplicates = sorted(panel_id for panel_id, count in declared.items() if count > 1)
    missing = sorted(active - set(declared))
    unknown = sorted(set(declared) - active)
    if duplicates:
        errors.append(f"duplicate Panel contracts: {','.join(duplicates)}")
    if missing:
        errors.append(f"missing Panel contracts: {','.join(missing)}")
    if unknown:
        errors.append(f"contracts for non-active Panels: {','.join(unknown)}")
    return {
        "status": Status.HEALTHY.value if not errors else Status.UNHEALTHY.value,
        "registeredCount": len(registered),
        "activeCount": len(active),
        "contractCount": len(declared),
        "excludedCount": len(set(registered) - active),
        "errors": errors,
    }


def _request_json(path: str) -> dict[str, Any]:
    base = os.environ.get(
        "POLYDATA_OPERATIONS_API_BASE",
        f"http://127.0.0.1:{os.environ.get('POLYDATA_API_PORT', '18500')}",
    ).rstrip("/")
    headers = {"Accept": "application/json"}
    api_key = os.environ.get("POLYDATA_OPERATIONS_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(f"{base}/{path.lstrip('/')}", headers=headers)
    response = build_opener(ProxyHandler({})).open(request, timeout=15)
    try:
        if int(getattr(response, "status", 200)) != 200:
            raise RuntimeError("non-200 API response")
        payload = json.loads(response.read(2 * 1024 * 1024 + 1).decode("utf-8"))
    finally:
        response.close()
    if not isinstance(payload, dict):
        raise ValueError("API response must be an object")
    return payload


def _request_json_with_retries(
    path: str,
    *,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
) -> dict[str, Any]:
    retryable_http_statuses = {500, 502, 503, 504}
    for attempt in range(max(1, max_attempts)):
        try:
            return _request_json(path)
        except HTTPError as exc:
            if exc.code not in retryable_http_statuses or attempt + 1 >= max_attempts:
                raise
        except (URLError, TimeoutError, OSError):
            if attempt + 1 >= max_attempts:
                raise
        time.sleep(max(0.0, retry_delay_seconds))
    raise RuntimeError(f"request retry loop exhausted for {path}")


def _freshness_status(updated_at: Any, max_age: int) -> Status:
    age = age_seconds(updated_at)
    if age is None:
        return Status.UNKNOWN
    if age <= max_age:
        return Status.HEALTHY
    if age <= max_age * 3:
        return Status.WARNING
    return Status.DEGRADED


def _service_states(
    contract: dict[str, Any],
    *,
    extra_names: Iterable[str] = (),
) -> dict[str, dict[str, str]]:
    names = sorted({
        *{
            str(name)
            for group in contract.get("groups", [])
            if isinstance(group, dict)
            for name in group.get("serviceNames", [])
            if str(name).startswith("polydata-") and str(name).endswith(".service")
        },
        *{
            str(name)
            for name in extra_names
            if str(name).startswith("polydata-") and str(name).endswith(".service")
        },
    })
    states: dict[str, dict[str, str]] = {}
    for name in names:
        try:
            result = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    name,
                    "--property=LoadState",
                    "--property=ActiveState",
                    "--property=SubState",
                    "--property=Result",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            values = {}
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        values[key] = value
            load_state = values.get("LoadState") or "unknown"
            active_state = values.get("ActiveState") or "unknown"
            if load_state == "not-found":
                status = Status.UNKNOWN
            elif active_state == "active":
                status = Status.HEALTHY
            elif active_state in {"activating", "reloading"}:
                status = Status.WARNING
            elif active_state == "inactive":
                status = Status.DEGRADED
            elif active_state == "failed":
                status = Status.UNHEALTHY
            else:
                status = Status.UNKNOWN
            states[name] = {
                "status": status.value,
                "activeState": active_state,
                "subState": values.get("SubState") or "unknown",
                "result": values.get("Result") or "unknown",
            }
        except (OSError, subprocess.SubprocessError):
            states[name] = {
                "status": Status.UNKNOWN.value,
                "activeState": "unknown",
                "subState": "unknown",
                "result": "unknown",
            }
    return states


def _seed_evidence(
    panel_id: str,
    group: dict[str, Any],
    seed_items: list[dict[str, Any]],
) -> dict[str, Any]:
    services = set(str(value) for value in group.get("serviceNames", []))
    aliases = group.get("evidenceAliases") if isinstance(group.get("evidenceAliases"), dict) else {}
    evidence_key = str(aliases.get(panel_id) or panel_id)
    candidates = [
        item
        for item in seed_items
        if isinstance(item, dict)
        and str(item.get("panelId")) == evidence_key
    ]
    if not candidates and len(group.get("panelIds", [])) == 1:
        candidates = [
            item
            for item in seed_items
            if isinstance(item, dict) and str(item.get("serviceName")) in services
        ]
    if not candidates:
        return {
            "status": Status.UNKNOWN.value,
            "freshness": Status.UNKNOWN.value,
            "observedAt": None,
            "ageSeconds": None,
            "evidence": "seed-metadata-unavailable",
        }
    statuses = []
    latest: dict[str, Any] | None = None
    for item in candidates:
        item_status = normalize(item.get("status"))
        freshness = normalize(item.get("freshness"))
        statuses.append(aggregate([item_status, freshness]))
        if latest is None or str(item.get("lastSuccessAt") or "") > str(latest.get("lastSuccessAt") or ""):
            latest = item
    assert latest is not None
    observed_at = latest.get("lastSuccessAt")
    return {
        "status": aggregate(statuses).value,
        "freshness": normalize(latest.get("freshness")).value,
        "observedAt": observed_at,
        "ageSeconds": age_seconds(observed_at),
        "evidence": "seed-metadata",
        "serviceNames": sorted(
            {
                str(item.get("serviceName"))
                for item in candidates
                if item.get("serviceName")
            }
        ),
        "sourceCount": len(candidates),
        "recordCount": int(latest.get("recordCount") or 0),
    }


def _request_system_health(
    *,
    max_attempts: int = 6,
    retry_delay_seconds: float = 1.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for attempt in range(max(1, max_attempts)):
        payload = _request_json_with_retries("/system/health")
        api_lob_runtime = (
            payload.get("lobRuntime")
            if isinstance(payload.get("lobRuntime"), dict)
            else {}
        )
        local_lob_runtime = _read_local_lob_runtime_status()
        api_lob_age = age_seconds(_lob_observed_at(api_lob_runtime))
        local_lob_age = age_seconds(_lob_observed_at(local_lob_runtime))
        if local_lob_age is not None and (
            api_lob_age is None or local_lob_age < api_lob_age
        ):
            payload = {
                **payload,
                "lobRuntime": {**api_lob_runtime, **local_lob_runtime},
            }
        if payload.get("apiStatus") == "warming":
            if attempt + 1 < max_attempts:
                time.sleep(max(0.0, retry_delay_seconds))
            continue

        # /system/health intentionally serves stale data once while refreshing
        # its shared cache. With multiple API workers, the refreshed payload can
        # take more than one follow-up request to become visible through Redis.
        # Keep polling only while the LOB heartbeat is older than its contract;
        # a genuinely stale runtime still returns after this bounded loop.
        lob_runtime = payload.get("lobRuntime") if isinstance(payload.get("lobRuntime"), dict) else {}
        lob_observed_at = _lob_observed_at(lob_runtime)
        lob_age_seconds = age_seconds(lob_observed_at)
        if (
            attempt + 1 < max_attempts
            and (attempt == 0 or lob_age_seconds is None or lob_age_seconds > 30)
        ):
            time.sleep(max(0.0, retry_delay_seconds))
            continue
        return payload
    raise RuntimeError("system health remained warming")


def _lob_observed_at(item: dict[str, Any]) -> Any:
    candidates = [
        item.get("updatedAt"),
        item.get("observedAt"),
        item.get("statusUpdatedAt"),
        item.get("lastMessageAt"),
        item.get("statusFileWrittenAt"),
    ]
    ranked = [
        (age, value)
        for value in candidates
        if (age := age_seconds(value)) is not None
    ]
    return min(ranked, key=lambda pair: pair[0])[1] if ranked else None


def _read_local_lob_runtime_status() -> dict[str, Any]:
    """Read the local watcher heartbeat when this audit runs on the GCP host.

    The public system-health endpoint is intentionally cached across API
    workers.  That cache is useful remotely, but it can be older than the
    30-second LOB freshness contract even while the watcher is writing its
    atomic status file every two seconds.  A missing or stale local file never
    replaces newer API evidence.
    """

    raw_path = str(
        os.environ.get("POLYDATA_LOB_RUNTIME_STATUS_PATH")
        or DEFAULT_LOB_RUNTIME_STATUS_PATH
    ).strip()
    if not raw_path:
        return {}
    try:
        payload = read_json(Path(raw_path).expanduser())
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _database_evidence(group: dict[str, Any], system: dict[str, Any]) -> dict[str, Any]:
    probe = str(group.get("representativeProbe") or "")
    max_age = int(group.get("expectedFreshnessSeconds") or 300)
    if probe == "lob_runtime":
        item = system.get("lobRuntime") if isinstance(system.get("lobRuntime"), dict) else {}
        observed_at = _lob_observed_at(item)
        source = normalize(item.get("status"))
    else:
        camel = {
            "market_sync": "marketSync",
            "trade_sync": "tradeSync",
            "oracle_sync": "oracleSync",
            "price_sync": "priceSync",
        }.get(probe, probe)
        item = system.get(camel) if isinstance(system.get(camel), dict) else {}
        observed_at = item.get("updatedAt")
        source = normalize(item.get("status"), default=Status.HEALTHY if item else Status.UNKNOWN)
    freshness = _freshness_status(observed_at, max_age)
    return {
        "status": aggregate([source, freshness]).value,
        "freshness": freshness.value,
        "observedAt": observed_at,
        "ageSeconds": age_seconds(observed_at),
        "evidence": f"database:{probe}",
    }


def build_panel_snapshot(
    contract: dict[str, Any],
    validation: dict[str, Any],
    *,
    system: dict[str, Any],
    seed: dict[str, Any],
    service_states: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    seed_items = seed.get("items") if isinstance(seed.get("items"), list) else []
    observed_services = service_states or {}
    panels = []
    for group in contract["groups"]:
        for panel_id in group["panelIds"]:
            strategy = str(group["healthStrategy"])
            if strategy in {"seed", "external-api"}:
                evidence = _seed_evidence(str(panel_id), group, seed_items)
            elif strategy == "database":
                evidence = _database_evidence(group, system)
            else:
                evidence = {
                    "status": Status.UNKNOWN.value,
                    "freshness": Status.UNKNOWN.value,
                    "observedAt": None,
                    "ageSeconds": None,
                    "evidence": "browser-smoke-required",
                }
            configured_service_names = [
                str(name) for name in group.get("serviceNames", [])
            ]
            evidence_service_names = evidence.get("serviceNames")
            relevant_service_names = (
                [str(name) for name in evidence_service_names]
                if isinstance(evidence_service_names, list) and evidence_service_names
                else configured_service_names
            )
            panel_services = [
                {
                    "name": name,
                    **observed_services.get(
                        name,
                        {
                            "status": Status.UNKNOWN.value,
                            "activeState": "unknown",
                            "subState": "unknown",
                            "result": "unknown",
                        },
                    ),
                }
                for name in relevant_service_names
            ]
            service_status = (
                aggregate(item["status"] for item in panel_services)
                if panel_services
                else Status.DISABLED
            )
            panels.append(
                {
                    "panelId": panel_id,
                    "owner": group["owner"],
                    "healthStrategy": strategy,
                    "status": evidence["status"],
                    "freshness": evidence["freshness"],
                    "observedAt": evidence["observedAt"],
                    "ageSeconds": evidence["ageSeconds"],
                    "evidence": evidence["evidence"],
                    "dataSources": group["dataSources"],
                    "serviceNames": configured_service_names,
                    "observedServiceNames": relevant_service_names,
                    "expectedFreshnessSeconds": group.get("expectedFreshnessSeconds"),
                    "degradationPolicy": group["degradationPolicy"],
                    "serviceStatus": service_status.value,
                    "services": panel_services,
                }
            )
            if panel_services:
                panels[-1]["status"] = aggregate(
                    [panels[-1]["status"], service_status]
                ).value
    watermarks = {
        name: system.get(name)
        for name in ("marketSync", "tradeSync", "oracleSync", "priceSync")
        if isinstance(system.get(name), dict)
    }
    active_gaps = [
        {
            "panelId": panel["panelId"],
            "status": panel["status"],
            "freshness": panel["freshness"],
            "evidence": panel["evidence"],
        }
        for panel in panels
        if panel["status"] not in {Status.HEALTHY.value, Status.DISABLED.value}
    ]
    overall = aggregate([validation["status"], *[panel["status"] for panel in panels]])
    return {
        "schemaVersion": "polymonitor.panel-health.v1",
        "generatedAt": utc_now_iso(),
        "status": overall.value,
        "contract": validation,
        "watermarks": watermarks,
        "activeGaps": active_gaps,
        "summary": {
            "panelCount": len(panels),
            "healthyCount": sum(panel["status"] == Status.HEALTHY.value for panel in panels),
            "attentionCount": sum(
                panel["status"] in {Status.WARNING.value, Status.DEGRADED.value, Status.UNHEALTHY.value}
                for panel in panels
            ),
            "unknownCount": sum(panel["status"] == Status.UNKNOWN.value for panel in panels),
        },
        "panels": panels,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-contracts", action="store_true")
    parser.add_argument("--print", action="store_true", dest="print_payload")
    args = parser.parse_args()
    try:
        contract = read_json(args.contract)
        validation = validate_contract(contract)
        if args.check_contracts:
            json.dump(validation, sys.stdout, ensure_ascii=True, indent=2)
            sys.stdout.write("\n")
            return 0 if validation["status"] == Status.HEALTHY.value else 1
        system = _request_system_health()
        seed = _request_json_with_retries("/system/seed-health")
        seed_items = seed.get("items") if isinstance(seed.get("items"), list) else []
        payload = build_panel_snapshot(
            contract,
            validation,
            system=system,
            seed=seed,
            service_states=_service_states(
                contract,
                extra_names=(
                    str(item.get("serviceName"))
                    for item in seed_items
                    if isinstance(item, dict) and item.get("serviceName")
                ),
            ),
        )
        output = args.output or operations_state_dir() / "panel-health.json"
        atomic_write_json(output, payload)
        update_incident_history(
            operations_state_dir() / "incidents.json",
            [
                {
                    "component": f"panel:{panel['panelId']}",
                    "status": panel["status"],
                    "summary": panel["evidence"],
                }
                for panel in payload["panels"]
            ],
        )
        if args.print_payload:
            json.dump(payload, sys.stdout, ensure_ascii=True, indent=2)
            sys.stdout.write("\n")
        return 0 if validation["status"] == Status.HEALTHY.value else 1
    except Exception as exc:
        print(f"[panel-health] audit failed: {redact_text(type(exc).__name__)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
