#!/usr/bin/env python3
"""Collect a redacted GCP runtime snapshot without changing service state."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ops.lib.incidents import update_incident_history
from scripts.ops.lib.redaction import redact_text
from scripts.ops.lib.snapshot import age_seconds, atomic_write_json, operations_state_dir, read_json, utc_now_iso
from scripts.ops.lib.status import Status, aggregate, threshold_status


DEFAULT_CONTRACT = REPO_ROOT / "config" / "operations" / "runtime_contract.json"


def _run(command: list[str], *, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )


def _systemctl(scope: str, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["systemctl"]
    if scope == "user":
        command.append("--user")
    return _run([*command, *args])


def _show_unit(unit: str, *, scope: str = "user") -> dict[str, Any]:
    properties = ("LoadState", "ActiveState", "SubState", "Result", "NRestarts")
    result = _systemctl(scope, "show", unit, *[f"--property={name}" for name in properties])
    values: dict[str, str] = {}
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
    return {
        "unit": unit,
        "scope": scope,
        "status": status.value,
        "loadState": load_state,
        "activeState": active_state,
        "subState": values.get("SubState") or "unknown",
        "result": values.get("Result") or "unknown",
        "restartCount": int(values.get("NRestarts") or 0),
    }


def _target_units(target: str, prefix: str) -> list[str]:
    result = _systemctl("user", "show", target, "--property=Wants", "--value")
    if result.returncode != 0:
        return []
    return sorted(
        {
            unit
            for unit in result.stdout.split()
            if unit.startswith(prefix) and unit.endswith((".service", ".timer", ".target"))
        }
    )


def _memory_snapshot(thresholds: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
        total = values["MemTotal"]
        available = values["MemAvailable"]
        available_pct = round((available / total) * 100, 2) if total else None
        status = threshold_status(
            available_pct,
            warning=float(thresholds["memoryAvailableWarningPct"]),
            critical=float(thresholds["memoryAvailableCriticalPct"]),
            lower_is_worse=True,
        )
        return {
            "status": status.value,
            "totalBytes": total,
            "availableBytes": available,
            "availablePct": available_pct,
        }
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        return {"status": Status.UNKNOWN.value}


def _disk_snapshot(path: Path, thresholds: dict[str, Any]) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
        free_pct = round((usage.free / usage.total) * 100, 2) if usage.total else None
        status = threshold_status(
            free_pct,
            warning=float(thresholds["diskFreeWarningPct"]),
            critical=float(thresholds["diskFreeCriticalPct"]),
            lower_is_worse=True,
        )
        return {
            "status": status.value,
            "totalBytes": usage.total,
            "freeBytes": usage.free,
            "freePct": free_pct,
        }
    except (OSError, ValueError, ZeroDivisionError):
        return {"status": Status.UNKNOWN.value}


def _load_snapshot(thresholds: dict[str, Any]) -> dict[str, Any]:
    try:
        one, five, fifteen = os.getloadavg()
        cpus = max(1, os.cpu_count() or 1)
        per_cpu = one / cpus
        status = threshold_status(
            per_cpu,
            warning=float(thresholds["loadPerCpuWarning"]),
            critical=float(thresholds["loadPerCpuCritical"]),
        )
        return {
            "status": status.value,
            "oneMinute": round(one, 2),
            "fiveMinute": round(five, 2),
            "fifteenMinute": round(fifteen, 2),
            "cpuCount": cpus,
            "oneMinutePerCpu": round(per_cpu, 3),
        }
    except OSError:
        return {"status": Status.UNKNOWN.value}


def _uptime_seconds() -> int | None:
    try:
        return max(0, int(float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])))
    except (OSError, ValueError, IndexError):
        return None


def _probe_api() -> dict[str, Any]:
    base = os.environ.get(
        "POLYDATA_OPERATIONS_API_BASE",
        f"http://127.0.0.1:{os.environ.get('POLYDATA_API_PORT', '18500')}",
    ).rstrip("/")
    started = time.monotonic()
    try:
        response = build_opener(ProxyHandler({})).open(f"{base}/health", timeout=8)
        try:
            payload = json.loads(response.read(65537).decode("utf-8"))
            code = int(getattr(response, "status", 200))
        finally:
            response.close()
        healthy = code == 200 and isinstance(payload, dict) and str(payload.get("status")).lower() == "ok"
        return {
            "status": (Status.HEALTHY if healthy else Status.UNHEALTHY).value,
            "httpStatus": code,
            "latencyMs": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "status": Status.UNHEALTHY.value,
            "errorClass": type(exc).__name__,
            "latencyMs": round((time.monotonic() - started) * 1000),
        }


def _probe_redis() -> dict[str, Any]:
    raw_url = os.environ.get("POLYDATA_REDIS_URL", "redis://127.0.0.1:6379/0")
    try:
        parsed = urlsplit(raw_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 6379
        started = time.monotonic()
        with socket.create_connection((host, port), timeout=5) as connection:
            connection.sendall(b"*1\r\n$4\r\nPING\r\n")
            reply = connection.recv(64)
        healthy = reply.startswith(b"+PONG")
        return {
            "status": (Status.HEALTHY if healthy else Status.UNHEALTHY).value,
            "latencyMs": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {"status": Status.UNHEALTHY.value, "errorClass": type(exc).__name__}


def _probe_postgres() -> dict[str, Any]:
    started = time.monotonic()
    try:
        from scripts.db import db

        with db.get_db(readonly=True) as connection:
            row = connection.execute("SELECT 1").fetchone()
        healthy = bool(row and int(row[0]) == 1)
        return {
            "status": (Status.HEALTHY if healthy else Status.UNHEALTHY).value,
            "query": "SELECT 1",
            "latencyMs": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "status": Status.UNHEALTHY.value,
            "query": "SELECT 1",
            "errorClass": type(exc).__name__,
            "latencyMs": round((time.monotonic() - started) * 1000),
        }


def _probe_clickhouse() -> dict[str, Any]:
    enabled = str(os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_READ_ENABLED", "0")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    raw_url = os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_HTTP_URL", "").rstrip("/")
    if not enabled:
        return {"status": Status.DISABLED.value}
    if not raw_url:
        return {"status": Status.UNKNOWN.value, "reason": "http-url-not-configured"}
    headers = {"Content-Type": "text/plain"}
    user = os.environ.get("POLYDATA_ORDERFILLED_CLICKHOUSE_USER")
    password = os.environ.get("CLICKHOUSE_PASSWORD")
    if user:
        headers["X-ClickHouse-User"] = user
    if password:
        headers["X-ClickHouse-Key"] = password
    started = time.monotonic()
    try:
        request = Request(raw_url, data=b"SELECT 1 FORMAT TabSeparated", headers=headers, method="POST")
        response = build_opener(ProxyHandler({})).open(request, timeout=8)
        try:
            healthy = response.read(64).strip() == b"1"
            code = int(getattr(response, "status", 200))
        finally:
            response.close()
        return {
            "status": (Status.HEALTHY if healthy and code == 200 else Status.UNHEALTHY).value,
            "query": "SELECT 1",
            "httpStatus": code,
            "latencyMs": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "status": Status.UNHEALTHY.value,
            "query": "SELECT 1",
            "errorClass": type(exc).__name__,
            "latencyMs": round((time.monotonic() - started) * 1000),
        }


def _read_tunnel_heartbeat(path: Path, max_age: int) -> dict[str, Any]:
    try:
        payload = read_json(path)
        observed_at = payload.get("observedAt")
        age = age_seconds(observed_at)
        source_status = str(payload.get("status") or Status.UNKNOWN.value)
        unit_status = str(payload.get("unit") or Status.UNKNOWN.value)
        if age is None:
            status = Status.UNKNOWN
        elif age > max_age:
            status = aggregate([source_status, unit_status, Status.DEGRADED])
        else:
            status = aggregate([source_status, unit_status])
        return {
            "status": status.value,
            "observedAt": observed_at,
            "ageSeconds": age,
            "postgres": str(payload.get("postgres") or Status.UNKNOWN.value),
            "clickhouse": str(payload.get("clickhouse") or Status.UNKNOWN.value),
            "unit": unit_status,
            "recovery": payload.get("recovery") if isinstance(payload.get("recovery"), dict) else {},
        }
    except (OSError, ValueError):
        return {"status": Status.UNKNOWN.value, "reason": "heartbeat-unavailable"}


def collect(contract: dict[str, Any]) -> dict[str, Any]:
    role = contract["roles"]["gcp-api"]
    thresholds = contract["thresholds"]
    state_dir = operations_state_dir()
    unit_names = _target_units(str(role["target"]), str(role["unitPrefix"]))
    service_rows = [_show_unit(unit) for unit in unit_names]
    system_rows = [_show_unit(unit, scope="system") for unit in role.get("systemUnits", [])]
    forbidden_rows = [_show_unit(unit) for unit in role.get("forbiddenUnits", [])]
    active_forbidden = [row["unit"] for row in forbidden_rows if row["activeState"] == "active"]
    ownership_status = Status.UNHEALTHY if active_forbidden else Status.HEALTHY

    dependencies = {
        "api": _probe_api(),
        "redis": _probe_redis(),
        "postgres": _probe_postgres(),
        "clickhouse": _probe_clickhouse(),
        "tunnel": _read_tunnel_heartbeat(
            state_dir / str(contract["state"]["tunnelHeartbeat"]),
            int(thresholds["tunnelHeartbeatMaxAgeSeconds"]),
        ),
    }
    resources = {
        "memory": _memory_snapshot(thresholds),
        "disk": _disk_snapshot(REPO_ROOT, thresholds),
        "load": _load_snapshot(thresholds),
        "uptimeSeconds": _uptime_seconds(),
    }
    status = aggregate(
        [
            ownership_status,
            *[row["status"] for row in service_rows],
            *[row["status"] for row in system_rows],
            *[item["status"] for item in dependencies.values()],
            resources["memory"]["status"],
            resources["disk"]["status"],
            resources["load"]["status"],
        ]
    )
    return {
        "schemaVersion": "polymonitor.operations-runtime.v1",
        "generatedAt": utc_now_iso(),
        "role": "gcp-api",
        "status": status.value,
        "ownership": {
            "status": ownership_status.value,
            "target": role["target"],
            "activeForbiddenUnits": active_forbidden,
        },
        "resources": resources,
        "dependencies": dependencies,
        "services": service_rows,
        "systemServices": system_rows,
        "summary": {
            "serviceCount": len(service_rows),
            "healthyServices": sum(row["status"] == Status.HEALTHY.value for row in service_rows),
            "attentionServices": sum(
                row["status"] in {Status.WARNING.value, Status.DEGRADED.value, Status.UNHEALTHY.value}
                for row in service_rows
            ),
        },
    }


def _incident_observations(payload: dict[str, Any]) -> list[dict[str, str]]:
    observations = [
        {
            "component": "runtime:ownership",
            "status": payload["ownership"]["status"],
            "summary": "forbidden runtime unit active"
            if payload["ownership"]["activeForbiddenUnits"]
            else "deployment role boundary satisfied",
        }
    ]
    for name, item in payload["dependencies"].items():
        summary = f"{name} probe"
        if name == "tunnel" and isinstance(item.get("recovery"), dict):
            recovery = item["recovery"]
            summary = (
                f"tunnel probe; recovery={recovery.get('decision', 'none')}; "
                f"attempts={int(recovery.get('restartAttemptsInWindow') or 0)}"
            )
        observations.append(
            {"component": f"dependency:{name}", "status": item["status"], "summary": summary}
        )
    for name, item in payload["resources"].items():
        if isinstance(item, dict) and "status" in item:
            observations.append(
                {"component": f"resource:{name}", "status": item["status"], "summary": f"{name} threshold"}
            )
    for item in payload["services"]:
        observations.append(
            {
                "component": f"service:{item['unit']}",
                "status": item["status"],
                "summary": f"{item['activeState']}/{item['subState']}",
            }
        )
    return observations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--print", action="store_true", dest="print_payload")
    args = parser.parse_args()
    try:
        contract = read_json(args.contract)
        output = args.output or operations_state_dir() / str(contract["state"]["runtimeSnapshot"])
        payload = collect(contract)
        atomic_write_json(output, payload)
        update_incident_history(
            operations_state_dir() / str(contract["state"]["incidentHistory"]),
            _incident_observations(payload),
        )
        if args.print_payload:
            json.dump(payload, sys.stdout, ensure_ascii=True, indent=2)
            sys.stdout.write("\n")
        return 0
    except Exception as exc:
        print(
            f"[operations-runtime] collection failed: {redact_text(type(exc).__name__)}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
