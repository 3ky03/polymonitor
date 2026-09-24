from __future__ import annotations

import hmac
import ipaddress
import os
from typing import Any

from flask import Flask, jsonify, request

from agent.common.budget import claim_agent_live_call
from agent.common.env import get_env
from agent.market_insight import build_market_insight, build_market_insight_fallback
from agent.market_wide import build_market_wide_fallback, build_market_wide_insight


def _agent_enabled() -> bool:
    return get_env("POLYDATA_AGENT_ENABLED").strip().lower() in {"1", "true", "yes", "on"}


def _authorized() -> bool:
    expected = get_env("POLYDATA_AGENT_GATEWAY_TOKEN")
    if not expected:
        return True
    bearer = request.headers.get("Authorization", "")
    token = request.headers.get("X-PolyData-Agent-Token", "")
    if bearer.startswith("Bearer "):
        token = bearer.removeprefix("Bearer ").strip()
    return hmac.compare_digest(token, expected)


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_ip(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if "," in value:
        value = value.split(",", 1)[0].strip()
    if value.startswith("[") and "]" in value:
        return value[1:value.index("]")]
    if value.count(":") == 1 and "." in value:
        host, _port = value.rsplit(":", 1)
        return host
    return value


def _is_loopback(raw: str | None) -> bool:
    value = _normalize_ip(raw)
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _local_access_allowed() -> bool:
    if not _truthy_env("POLYDATA_AGENT_LOCAL_ONLY", True):
        return True
    forwarded_for = request.headers.get("X-Forwarded-For")
    real_ip = request.headers.get("X-Real-IP")
    if forwarded_for and not _is_loopback(forwarded_for):
        return False
    if real_ip and not _is_loopback(real_ip):
        return False
    return _is_loopback(request.remote_addr)


def _claim_gateway_live_call(kind: str):
    if _truthy_env("POLYDATA_AGENT_GATEWAY_BUDGET_DISABLED", False):
        return True, {"enabled": False, "scope": "gateway", "kind": kind}
    return claim_agent_live_call(kind)


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "polydata-agent-gateway"})

    @app.post("/agent/market-insights")
    def market_insights():
        if not _agent_enabled():
            return jsonify({"error": "agent-disabled", "status": "disabled"}), 404
        if not _local_access_allowed():
            return jsonify({"error": "agent-forbidden", "status": "forbidden"}), 403
        if not _authorized():
            return jsonify({"error": "unauthorized"}), 401
        payload: Any = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON object required"}), 400
        allowed, budget = _claim_gateway_live_call("gateway:market")
        if not allowed:
            result = build_market_insight_fallback(payload)
            result["servedBy"] = "agent-gateway"
            result["cacheStatus"] = "budget-fallback"
            result["dailyBudget"] = budget
            return jsonify(result)
        result = build_market_insight(payload)
        result["servedBy"] = "agent-gateway"
        result["dailyBudget"] = budget
        return jsonify(result)

    @app.post("/agent/market-wide-insights")
    def market_wide_insights():
        if not _agent_enabled():
            return jsonify({"error": "agent-disabled", "status": "disabled"}), 404
        if not _local_access_allowed():
            return jsonify({"error": "agent-forbidden", "status": "forbidden"}), 403
        if not _authorized():
            return jsonify({"error": "unauthorized"}), 401
        payload: Any = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON object required"}), 400
        allowed, budget = _claim_gateway_live_call("gateway:market-wide")
        if not allowed:
            result = build_market_wide_fallback(payload, reason="budget-fallback")
            result["servedBy"] = "agent-gateway"
            result["cacheStatus"] = "budget-fallback"
            result["dailyBudget"] = budget
            return jsonify(result)
        result = build_market_wide_insight(payload)
        result["servedBy"] = "agent-gateway"
        result["dailyBudget"] = budget
        return jsonify(result)

    return app


app = create_app()


def main() -> None:
    host = get_env("POLYDATA_AGENT_GATEWAY_HOST", "127.0.0.1")
    port = int(get_env("POLYDATA_AGENT_GATEWAY_PORT", "18700"))
    debug = os.environ.get("POLYDATA_AGENT_GATEWAY_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
