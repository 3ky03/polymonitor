#!/usr/bin/env python3
"""Read-only post-deployment verification across public, API and GCP runtime surfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import ProxyHandler, Request, build_opener

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ops.lib.redaction import redact_text
from scripts.ops.lib.snapshot import atomic_write_json, utc_now_iso
from scripts.ops.lib.status import Status, aggregate


REPRESENTATIVE_ENDPOINTS = (
    ("health", "wm-api/health", None),
    ("markets", "wm-api/markets", {"limit": 1}),
    ("oracle", "wm-api/oracle/recent", {"limit": 1}),
)
EXPECTED_REMOTE_UNITS = (
    "polydata-gcp.target",
    "polydata-api.service",
    "polydata-operations-runtime-health.timer",
    "polydata-operations-panel-health.timer",
)
EXPECTED_REMOTE_SYSTEM_UNITS = ("nginx.service", "redis-server.service")


def _http_json(
    url: str,
    *,
    token: str = "",
    timeout: int = 20,
    required_statuses: set[str] | None = None,
    require_nonempty: bool = False,
) -> dict[str, object]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    started = time.monotonic()
    try:
        response = build_opener(ProxyHandler({})).open(Request(url, headers=headers), timeout=timeout)
        try:
            code = int(getattr(response, "status", 200))
            body = response.read(2 * 1024 * 1024 + 1)
        finally:
            response.close()
        payload = json.loads(body.decode("utf-8"))
        valid = code == 200 and isinstance(payload, (dict, list)) and len(body) <= 2 * 1024 * 1024
        application_status = str(payload.get("status") or "").lower() if isinstance(payload, dict) else ""
        if required_statuses is not None:
            valid = valid and application_status in required_statuses
        if isinstance(payload, list):
            record_count = len(payload)
        elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
            record_count = len(payload["items"])
        else:
            record_count = 1 if payload else 0
        if require_nonempty:
            valid = valid and record_count > 0
        return {
            "status": (Status.HEALTHY if valid else Status.UNHEALTHY).value,
            "httpStatus": code,
            "latencyMs": round((time.monotonic() - started) * 1000),
            "bodyBytes": len(body),
            "applicationStatus": application_status or None,
            "recordCount": record_count,
        }
    except Exception as exc:
        return {
            "status": Status.UNHEALTHY.value,
            "latencyMs": round((time.monotonic() - started) * 1000),
            "errorClass": type(exc).__name__,
        }


def _public_index_hash(base_url: str, timeout: int) -> tuple[str | None, dict[str, object]]:
    started = time.monotonic()
    try:
        response = build_opener(ProxyHandler({})).open(base_url, timeout=timeout)
        try:
            body = response.read(4 * 1024 * 1024 + 1)
            code = int(getattr(response, "status", 200))
        finally:
            response.close()
        valid = code == 200 and len(body) <= 4 * 1024 * 1024 and b"<html" in body[:8192].lower()
        digest = hashlib.sha256(body).hexdigest() if valid else None
        return digest, {
            "status": (Status.HEALTHY if valid else Status.UNHEALTHY).value,
            "httpStatus": code,
            "latencyMs": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return None, {
            "status": Status.UNHEALTHY.value,
            "latencyMs": round((time.monotonic() - started) * 1000),
            "errorClass": type(exc).__name__,
        }


def _ssh(target: str, command: str) -> subprocess.CompletedProcess[str]:
    ssh_command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ConnectionAttempts=1",
    ]
    identity = (
        os.environ.get("POLYDATA_GCP_SSH_IDENTITY_FILE")
        or os.environ.get("POLYDATA_GCP_TUNNEL_HEALTH_SSH_IDENTITY_FILE")
        or os.environ.get("POLYDATA_GCP_TUNNEL_SSH_IDENTITY_FILE")
        or os.environ.get("POLYDATA_GCP_SSH_KEY_PATH")
    )
    if identity:
        ssh_command.extend(["-o", "IdentitiesOnly=yes", "-i", identity])
    return subprocess.run(
        [*ssh_command, target, command],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )


def _remote_snapshot(target: str, app_dir: str) -> dict[str, object]:
    script = f"""
set -eu
cd {app_dir!r}
release_state="$HOME/.local/state/polydata-deploy/current.json"
if test -f "$release_state"; then
  sha="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["target_sha"])' "$release_state")"
  sha_source="release-state"
else
  sha="$(git rev-parse HEAD)"
  sha_source="git-head"
fi
index_hash="$(sha256sum /var/www/polydata/index.html | awk '{{print $1}}')"
units=""
for unit in {' '.join(EXPECTED_REMOTE_UNITS)}; do
  state="$(systemctl --user is-active "$unit" 2>/dev/null || true)"
  units="${{units}}${{unit}}=${{state}};"
done
system_units=""
for unit in {' '.join(EXPECTED_REMOTE_SYSTEM_UNITS)}; do
  state="$(systemctl is-active "$unit" 2>/dev/null || true)"
  system_units="${{system_units}}${{unit}}=${{state}};"
done
printf '%s\\n%s\\n%s\\n%s\\n%s\\n' "$sha" "$sha_source" "$index_hash" "$units" "$system_units"
"""
    result = _ssh(target, script)
    if result.returncode != 0:
        return {"status": Status.UNHEALTHY.value, "errorClass": "RemoteProbeFailed"}
    lines = result.stdout.splitlines()
    if len(lines) < 5:
        return {"status": Status.UNHEALTHY.value, "errorClass": "InvalidRemoteProbe"}
    units = {}
    for item in lines[3].split(";"):
        if "=" in item:
            unit, state = item.split("=", 1)
            units[unit] = state
    system_units = {}
    for item in lines[4].split(";"):
        if "=" in item:
            unit, state = item.split("=", 1)
            system_units[unit] = state
    all_user_active = all(units.get(unit) == "active" for unit in EXPECTED_REMOTE_UNITS)
    all_system_active = all(system_units.get(unit) == "active" for unit in EXPECTED_REMOTE_SYSTEM_UNITS)
    status = Status.HEALTHY if all_user_active and all_system_active else Status.UNHEALTHY
    return {
        "status": status.value,
        "sha": lines[0].strip(),
        "shaSource": lines[1].strip(),
        "indexSha256": lines[2].strip(),
        "units": units,
        "systemUnits": system_units,
    }


def verify(
    *,
    base_url: str,
    api_key: str,
    ssh_target: str,
    remote_app_dir: str,
    expected_sha: str,
    timeout: int,
) -> dict[str, object]:
    normalized = f"{base_url.rstrip('/')}/"
    public_hash, site_probe = _public_index_hash(normalized, timeout)
    endpoints = {}
    for name, relative, params in REPRESENTATIVE_ENDPOINTS:
        suffix = f"?{urlencode(params)}" if params else ""
        endpoints[name] = _http_json(
            urljoin(normalized, relative) + suffix,
            timeout=timeout,
            required_statuses={"ok"} if name == "health" else None,
            require_nonempty=name in {"markets", "oracle"},
        )
    if api_key:
        endpoints["operations"] = _http_json(
            urljoin(normalized, "wm-api/system/operations"),
            token=api_key,
            timeout=timeout,
            required_statuses={"healthy"},
        )
        endpoints["incidents"] = _http_json(
            urljoin(normalized, "wm-api/system/incidents"),
            token=api_key,
            timeout=timeout,
        )
    remote = _remote_snapshot(ssh_target, remote_app_dir) if ssh_target else {
        "status": Status.UNKNOWN.value,
        "reason": "ssh-target-not-provided",
    }
    checks = [site_probe["status"], *[item["status"] for item in endpoints.values()], remote["status"]]
    consistency = Status.UNKNOWN
    if remote.get("indexSha256") and public_hash:
        consistency = Status.HEALTHY if remote["indexSha256"] == public_hash else Status.UNHEALTHY
        checks.append(consistency.value)
    sha_status = Status.UNKNOWN
    if expected_sha and remote.get("sha"):
        sha_status = Status.HEALTHY if remote["sha"] == expected_sha else Status.UNHEALTHY
        checks.append(sha_status.value)
    return {
        "schemaVersion": "polymonitor.production-verification.v1",
        "generatedAt": utc_now_iso(),
        "status": aggregate(checks).value,
        "site": site_probe,
        "endpoints": endpoints,
        "remote": remote,
        "consistency": {
            "status": consistency.value,
            "publicAndRemoteIndexMatch": consistency is Status.HEALTHY,
            "expectedShaStatus": sha_status.value,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("POLYDATA_PUBLIC_BASE_URL", "https://polymonitor.club"))
    parser.add_argument("--api-key", default=os.environ.get("POLYDATA_OPERATIONS_API_KEY", ""))
    parser.add_argument(
        "--ssh-target",
        default=(
            os.environ.get("POLYDATA_GCP_SSH_TARGET")
            or os.environ.get("POLYDATA_GCP_TUNNEL_HEALTH_SSH_TARGET")
            or os.environ.get("POLYDATA_GCP_TUNNEL_SSH_TARGET")
            or ""
        ),
    )
    parser.add_argument("--remote-app-dir", default=os.environ.get("POLYDATA_REMOTE_APP_DIR", "/opt/polyData"))
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = verify(
            base_url=args.base_url,
            api_key=args.api_key,
            ssh_target=args.ssh_target,
            remote_app_dir=args.remote_app_dir,
            expected_sha=args.expected_sha,
            timeout=max(1, args.timeout),
        )
        if args.output:
            atomic_write_json(args.output, payload)
        json.dump(payload, sys.stdout, ensure_ascii=True, indent=2)
        sys.stdout.write("\n")
        return 0 if payload["status"] == Status.HEALTHY.value else 1
    except Exception as exc:
        print(f"[production-verify] verification failed: {redact_text(type(exc).__name__)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
