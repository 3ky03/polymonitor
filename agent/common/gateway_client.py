from __future__ import annotations

from typing import Any

import requests

from .env import get_env, get_float_env
from .json_utils import compact_text


def _configured_gateway_url(path: str = "/agent/market-insights") -> str:
    base = get_env("POLYDATA_AGENT_GATEWAY_BASE_URL").strip().rstrip("/")
    if base:
        return f"{base}{path}"
    url = get_env("POLYDATA_AGENT_GATEWAY_URL").strip()
    if path != "/agent/market-insights" and url.endswith("/agent/market-insights"):
        return f"{url.removesuffix('/agent/market-insights')}{path}"
    return url


def gateway_configured() -> bool:
    return bool(_configured_gateway_url())


def _call_gateway(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = _configured_gateway_url(path)
    if not url:
        raise RuntimeError("POLYDATA_AGENT_GATEWAY_URL is not configured")
    token = get_env("POLYDATA_AGENT_GATEWAY_TOKEN")
    timeout = get_float_env("POLYDATA_AGENT_GATEWAY_TIMEOUT_SECONDS", 55.0)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-PolyData-Agent-Gateway-Attempt": "1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-PolyData-Agent-Token"] = token
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("agent gateway response was not a JSON object")
    if not data.get("brief") or not isinstance(data.get("focus"), list):
        raise RuntimeError("agent gateway response missed required insight fields")
    data["viaGateway"] = True
    data["gatewayUrl"] = compact_text(url, 120)
    return data


def call_market_insight_gateway(payload: dict[str, Any]) -> dict[str, Any]:
    return _call_gateway("/agent/market-insights", payload)


def call_market_wide_insight_gateway(payload: dict[str, Any]) -> dict[str, Any]:
    return _call_gateway("/agent/market-wide-insights", payload)
