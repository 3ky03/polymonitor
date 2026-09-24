#!/usr/bin/env python3
"""Probe the public site from a host outside the monitored GCP VM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
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
from scripts.ops.lib.snapshot import atomic_write_json, read_json, utc_now_iso
from scripts.ops.lib.status import Status, aggregate


def _running_on_gcp() -> bool:
    if os.environ.get("POLYDATA_DEPLOY_ROLE", "").strip().lower() == "gcp-api":
        return True
    try:
        request = Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/id",
            headers={"Metadata-Flavor": "Google"},
        )
        response = build_opener(ProxyHandler({})).open(request, timeout=1)
        try:
            return response.headers.get("Metadata-Flavor") == "Google"
        finally:
            response.close()
    except Exception:
        return False


def _probe(
    url: str,
    *,
    expect_json: bool,
    timeout: int,
    require_application_status: bool = False,
) -> dict[str, object]:
    started = time.monotonic()
    try:
        request = Request(url, headers={"Accept": "application/json" if expect_json else "text/html"})
        response = build_opener(ProxyHandler({})).open(request, timeout=timeout)
        try:
            body = response.read(2 * 1024 * 1024 + 1)
            code = int(getattr(response, "status", 200))
            content_type = str(response.headers.get("Content-Type") or "")
        finally:
            response.close()
        valid = code == 200 and len(body) <= 2 * 1024 * 1024
        application_status = None
        if expect_json:
            payload = json.loads(body.decode("utf-8"))
            application_status = str(payload.get("status") or "").lower() if isinstance(payload, dict) else ""
            valid = valid and isinstance(payload, (dict, list))
            if require_application_status:
                valid = valid and application_status in {"ok", "healthy", "warning", "degraded"}
        else:
            valid = valid and b"<html" in body[:8192].lower()
        return {
            "status": (Status.HEALTHY if valid else Status.UNHEALTHY).value,
            "httpStatus": code,
            "latencyMs": round((time.monotonic() - started) * 1000),
            "contentType": content_type.split(";", 1)[0],
            "bodyBytes": len(body),
            "bodySha256": hashlib.sha256(body).hexdigest(),
            "applicationStatus": application_status,
        }
    except Exception as exc:
        return {
            "status": Status.UNHEALTHY.value,
            "latencyMs": round((time.monotonic() - started) * 1000),
            "errorClass": type(exc).__name__,
        }


def build_snapshot(base_url: str, *, timeout: int) -> dict[str, object]:
    normalized = f"{base_url.rstrip('/')}/"
    probes = {
        "website": _probe(normalized, expect_json=False, timeout=timeout),
        "apiHealth": _probe(
            urljoin(normalized, "wm-api/health"),
            expect_json=True,
            timeout=timeout,
            require_application_status=True,
        ),
        "dbBackedMarkets": _probe(
            urljoin(normalized, "wm-api/markets") + "?limit=1",
            expect_json=True,
            timeout=timeout,
        ),
    }
    return {
        "schemaVersion": "polymonitor.public-availability.v1",
        "generatedAt": utc_now_iso(),
        "status": aggregate(item["status"] for item in probes.values()).value,
        "observer": {
            "location": os.environ.get("POLYDATA_EXTERNAL_MONITOR_LOCATION", "external"),
            "hostnameHash": hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:12],
        },
        "probes": probes,
    }


def _load_monitor_state(path: Path) -> dict[str, object]:
    try:
        return read_json(path)
    except (OSError, ValueError):
        return {}


def _notification_message(
    payload: dict[str, object],
    state: dict[str, object],
    *,
    recovered: bool,
) -> str:
    probes = payload.get("probes") if isinstance(payload.get("probes"), dict) else {}
    failing = [
        name
        for name, item in probes.items()
        if isinstance(item, dict) and item.get("status") != Status.HEALTHY.value
    ]
    latencies = [
        int(item.get("latencyMs") or 0)
        for item in probes.values()
        if isinstance(item, dict) and item.get("latencyMs") is not None
    ]
    label = "RECOVERED" if recovered else "UNAVAILABLE"
    environment = os.environ.get("POLYDATA_EXTERNAL_MONITOR_ENVIRONMENT", "production")
    runbook = os.environ.get("POLYDATA_EXTERNAL_MONITOR_RUNBOOK_URL", "")
    lines = [
        f"Polymonitor {label}",
        f"Environment: {environment}",
        f"Endpoint categories: {', '.join(failing) if failing else 'website, apiHealth, dbBackedMarkets'}",
        f"Status: {payload.get('status', Status.UNKNOWN.value)}",
        f"Max latency: {max(latencies) if latencies else 0} ms",
        f"First failure: {state.get('firstFailureAt') or 'unknown'}",
        f"Consecutive failures: {int(state.get('consecutiveFailures') or 0)}",
    ]
    if runbook:
        lines.append(f"Runbook: {runbook}")
    return "\n".join(lines)


def _send_telegram(message: str) -> dict[str, str]:
    token = (
        os.environ.get("POLYDATA_EXTERNAL_ALERT_TELEGRAM_BOT_TOKEN", "")
        or os.environ.get("POLYDATA_TELEGRAM_BOT_TOKEN", "")
    )
    chat_id = (
        os.environ.get("POLYDATA_EXTERNAL_ALERT_TELEGRAM_CHAT_ID", "")
        or os.environ.get("POLYDATA_TELEGRAM_CHANNEL_MONITOR", "")
    )
    if not token or not chat_id:
        return {"status": "not-configured"}
    form = {"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}
    thread_id = (
        os.environ.get("POLYDATA_EXTERNAL_ALERT_TELEGRAM_THREAD_ID", "")
        or os.environ.get("POLYDATA_TELEGRAM_THREAD_MONITOR", "")
    )
    if thread_id:
        form["message_thread_id"] = thread_id
    try:
        request = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=urlencode(form).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        # Public availability probes deliberately bypass local proxies, but
        # Telegram delivery must honor the operator's configured egress proxy.
        response = build_opener().open(request, timeout=15)
        try:
            code = int(getattr(response, "status", 200))
            response.read(1024)
        finally:
            response.close()
        return {"status": "delivered" if code == 200 else "failed", "channel": "telegram"}
    except Exception as exc:
        return {"status": "failed", "channel": "telegram", "errorClass": type(exc).__name__}


def apply_monitor_state(
    payload: dict[str, object],
    *,
    state_path: Path,
    failure_threshold: int,
) -> dict[str, object]:
    state = _load_monitor_state(state_path)
    now = utc_now_iso()
    now_epoch = int(time.time())
    notification_backoff = int(
        os.environ.get("POLYDATA_EXTERNAL_MONITOR_NOTIFICATION_BACKOFF_SECONDS", "900")
    )
    can_notify = now_epoch >= int(state.get("notificationBackoffUntilEpoch") or 0)
    healthy = payload.get("status") == Status.HEALTHY.value
    notification: dict[str, str] = {"status": "not-required"}
    if healthy:
        if bool(state.get("alertOpen")) and can_notify:
            notification = _send_telegram(_notification_message(payload, state, recovered=True))
            if notification.get("status") != "delivered":
                state["notificationBackoffUntilEpoch"] = now_epoch + max(60, notification_backoff)
        state.update(
            {
                "consecutiveFailures": 0,
                "lastSuccessAt": now,
            }
        )
        if not bool(state.get("alertOpen")) or notification.get("status") == "delivered":
            state["firstFailureAt"] = None
            state["alertOpen"] = False
            state["notificationBackoffUntilEpoch"] = 0
    else:
        failures = int(state.get("consecutiveFailures") or 0) + 1
        state["consecutiveFailures"] = failures
        state["lastFailureAt"] = now
        state["firstFailureAt"] = state.get("firstFailureAt") or now
        if failures >= failure_threshold and not bool(state.get("alertOpen")) and can_notify:
            notification = _send_telegram(_notification_message(payload, state, recovered=False))
            if notification.get("status") == "delivered":
                state["alertOpen"] = True
                state["notificationBackoffUntilEpoch"] = 0
            else:
                state["notificationBackoffUntilEpoch"] = now_epoch + max(60, notification_backoff)
    state["generatedAt"] = now
    state["lastStatus"] = payload.get("status")
    state["notification"] = notification
    atomic_write_json(state_path, state)
    payload["monitorState"] = {
        "consecutiveFailures": int(state.get("consecutiveFailures") or 0),
        "firstFailureAt": state.get("firstFailureAt"),
        "lastSuccessAt": state.get("lastSuccessAt"),
        "lastFailureAt": state.get("lastFailureAt"),
        "alertOpen": bool(state.get("alertOpen")),
        "notificationBackoffUntilEpoch": int(state.get("notificationBackoffUntilEpoch") or 0),
        "notification": notification,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.environ.get("POLYDATA_PUBLIC_BASE_URL", "https://polymonitor.club"),
    )
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument(
        "--failure-threshold",
        type=int,
        default=int(os.environ.get("POLYDATA_EXTERNAL_MONITOR_FAILURE_THRESHOLD", "2")),
    )
    parser.add_argument("--print", action="store_true", dest="print_payload")
    args = parser.parse_args()
    if _running_on_gcp():
        print("[public-availability] refusing to run on the monitored GCP VM", file=sys.stderr)
        return 2
    try:
        payload = build_snapshot(args.base_url, timeout=max(1, args.timeout))
        state_path = args.state
        if state_path is None:
            state_path = (
                args.output.parent / "state.json"
                if args.output
                else Path.home() / ".local" / "state" / "polydata-external-monitor" / "state.json"
            )
        payload = apply_monitor_state(
            payload,
            state_path=state_path,
            failure_threshold=max(2, args.failure_threshold),
        )
        if args.output:
            atomic_write_json(args.output, payload)
        if args.print_payload or not args.output:
            json.dump(payload, sys.stdout, ensure_ascii=True, indent=2)
            sys.stdout.write("\n")
        return 0 if payload["status"] == Status.HEALTHY.value else 1
    except Exception as exc:
        print(f"[public-availability] probe failed: {redact_text(type(exc).__name__)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
