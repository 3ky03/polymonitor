from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from api.context import resolve_route_value
from agent.common.budget import claim_agent_live_call
from agent.common.gateway_client import call_market_insight_gateway, call_market_wide_insight_gateway, gateway_configured
from agent.market_insight import build_market_insight, build_market_insight_fallback
from agent.market_wide import build_market_wide_fallback, build_market_wide_insight


AGENT_CACHE_NAMESPACE = "agent:insights"
AGENT_CACHE_VERSION = "v2"
_REFRESH_LOCK = threading.Lock()
_REFRESHING_KEYS: set[str] = set()
_RATE_LOCK = threading.Lock()
_RATE_WINDOW_SECONDS = 60
_RATE_BUCKETS: dict[str, list[float]] = {}


@dataclass(frozen=True)
class AgentRouteDependencies:
    application: Any
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    get_redis_client: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> AgentRouteDependencies:
        return cls(
            application=resolve_route_value(context, "app"),
            get_cached_json=_optional_route_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=_optional_route_callable(
                context,
                "set_cached_json",
            ),
            get_redis_client=_optional_route_callable(
                context,
                "get_redis_client",
            ),
        )


def _optional_route_callable(
    context: Mapping[str, Any],
    name: str,
) -> Callable[..., Any] | None:
    dependency = resolve_route_value(context, name)
    if dependency is None:
        return None
    if not callable(dependency):
        raise TypeError(f"Route dependency is not callable: {name}")
    return dependency


def _agent_enabled() -> bool:
    return os.environ.get("POLYDATA_AGENT_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _agent_disabled_response():
    return jsonify({"error": "agent-disabled", "status": "disabled"}), 404


def _agent_forbidden_response():
    return jsonify({"error": "agent-forbidden", "status": "forbidden"}), 403


def _agent_rate_limited_response(retry_after_seconds: int):
    response = jsonify({"error": "agent-rate-limited", "status": "rate-limited", "retryAfterSeconds": retry_after_seconds})
    response.status_code = 429
    response.headers["Retry-After"] = str(retry_after_seconds)
    return response


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _agent_local_only() -> bool:
    return _truthy_env("POLYDATA_AGENT_LOCAL_ONLY", True)


def _agent_rate_limit_per_minute() -> int:
    try:
        return max(1, int(os.environ.get("POLYDATA_AGENT_RATE_LIMIT_PER_MINUTE", "6")))
    except ValueError:
        return 6


def _normalize_ip(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if "," in value:
        value = value.split(",", 1)[0].strip()
    if value.startswith("[") and "]" in value:
        return value[1:value.index("]")]
    if value.count(":") == 0 and ":" in value:
        return value
    if value.count(":") == 1 and "." in value:
        host, _port = value.rsplit(":", 1)
        return host
    return value


def _is_loopback(raw: str | None) -> bool:
    value = _normalize_ip(raw)
    if value in {"localhost"}:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _agent_token_authorized() -> bool:
    expected = os.environ.get("POLYDATA_AGENT_SERVER_TOKEN", "").strip()
    if not expected:
        return False
    token = request.headers.get("X-PolyData-Agent-Token", "").strip()
    bearer = request.headers.get("Authorization", "").strip()
    if bearer.lower().startswith("bearer "):
        token = bearer.split(None, 1)[1].strip()
    return hmac.compare_digest(token, expected)


def _agent_access_allowed() -> bool:
    if not _agent_local_only():
        expected = os.environ.get("POLYDATA_AGENT_SERVER_TOKEN", "").strip()
        return _agent_token_authorized() if expected else True

    forwarded_for = request.headers.get("X-Forwarded-For")
    real_ip = request.headers.get("X-Real-IP")
    if forwarded_for and not _is_loopback(forwarded_for):
        return False
    if real_ip and not _is_loopback(real_ip):
        return False
    return _is_loopback(request.remote_addr)


def _agent_rate_key() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    return _normalize_ip(forwarded_for) or _normalize_ip(request.remote_addr) or "unknown"


def _check_agent_rate_limit() -> int | None:
    limit = _agent_rate_limit_per_minute()
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW_SECONDS
    key = _agent_rate_key()
    with _RATE_LOCK:
        bucket = [ts for ts in _RATE_BUCKETS.get(key, []) if ts >= cutoff]
        if len(bucket) >= limit:
            _RATE_BUCKETS[key] = bucket
            oldest = min(bucket) if bucket else now
            return max(1, int(_RATE_WINDOW_SECONDS - (now - oldest)))
        bucket.append(now)
        _RATE_BUCKETS[key] = bucket
    return None


def _agent_cache_ttl() -> int:
    try:
        return max(30, int(os.environ.get("POLYDATA_AGENT_CACHE_TTL_SECONDS", "300")))
    except ValueError:
        return 300


def _fallback_cache_ttl() -> int:
    return min(60, _agent_cache_ttl())


def _cache_key(kind: str, payload: dict[str, Any]) -> str:
    digest_payload = {
        "kind": kind,
        "lens": payload.get("lens"),
        "market": _market_identity(payload.get("market")),
        "selectedGroup": _market_identity(payload.get("selectedGroup")),
        "selectedOutcome": payload.get("selectedOutcome"),
        "markets": [_market_identity(item) for item in _list(payload.get("markets"))[:48]],
        "marketGroups": [_market_identity(item) for item in _list(payload.get("marketGroups"))[:36]],
        "trades": [_event_identity(item) for item in _list(payload.get("trades"))[:24]],
        "oracle": [_event_identity(item) for item in _list(payload.get("oracle"))[:24]],
        "content": [_event_identity(item) for item in _list(payload.get("content"))[:12]],
        "alphaSignals": [_event_identity(item) for item in _list(payload.get("alphaSignals"))[:10]],
        "whaleSignals": [_event_identity(item) for item in _list(payload.get("whaleSignals"))[:10]],
        "suspiciousSignals": [_event_identity(item) for item in _list(payload.get("suspiciousSignals"))[:10]],
    }
    raw = json.dumps(digest_payload, sort_keys=True, ensure_ascii=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{AGENT_CACHE_VERSION}:{kind}:{digest}"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _market_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "id": value.get("id") or value.get("groupId") or value.get("eventId") or value.get("slug"),
        "title": value.get("title"),
        "category": value.get("category"),
        "volume24h": value.get("volume24h"),
        "tradeCount24h": value.get("tradeCount24h"),
        "latestPrice": value.get("latestPrice"),
        "outcomeCount": value.get("outcomeCount"),
        "endDate": value.get("endDate"),
    }


def _event_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "id": value.get("id") or value.get("txHash") or value.get("marketId") or value.get("title"),
        "marketTitle": value.get("marketTitle") or value.get("title"),
        "timestamp": value.get("timestamp") or value.get("eventTime") or value.get("publishedAt"),
        "status": value.get("eventStatus") or value.get("status"),
        "price": value.get("price") or value.get("latestPrice") or value.get("proposedPrice"),
        "size": value.get("size") or value.get("notional"),
    }


def _cached_response(
    dependencies: AgentRouteDependencies,
    cache_key: str,
) -> dict[str, Any] | None:
    reader = dependencies.get_cached_json
    cached = (
        reader(AGENT_CACHE_NAMESPACE, cache_key)
        if callable(reader)
        else None
    )
    if not isinstance(cached, dict):
        cached = _direct_redis_get(dependencies, cache_key)
    if not isinstance(cached, dict):
        return None
    response = dict(cached)
    response["cacheStatus"] = "hit"
    return response


def _store_response(
    dependencies: AgentRouteDependencies,
    cache_key: str,
    response: dict[str, Any],
    ttl_seconds: int,
) -> None:
    payload = dict(response)
    payload.pop("cacheStatus", None)
    setter = dependencies.set_cached_json
    if callable(setter):
        setter(AGENT_CACHE_NAMESPACE, cache_key, payload, ttl_seconds)
        return
    _direct_redis_set(dependencies, cache_key, payload, ttl_seconds)


def _direct_redis_key(cache_key: str) -> str:
    prefix = os.environ.get("POLYDATA_REDIS_PREFIX", "polydata:")
    return f"{prefix}{AGENT_CACHE_NAMESPACE}:{cache_key}"


def _direct_redis_get(
    dependencies: AgentRouteDependencies,
    cache_key: str,
) -> dict[str, Any] | None:
    getter = dependencies.get_redis_client
    if not callable(getter):
        return None
    client = getter()
    if client is None:
        return None
    try:
        raw = client.get(_direct_redis_key(cache_key))
        return json.loads(raw) if raw else None
    except Exception:
        _log_exception(
            dependencies,
            "agent-cache redis-get failed key=%s",
            cache_key,
        )
        return None


def _direct_redis_set(
    dependencies: AgentRouteDependencies,
    cache_key: str,
    payload: dict[str, Any],
    ttl_seconds: int,
) -> None:
    getter = dependencies.get_redis_client
    if not callable(getter):
        return
    client = getter()
    if client is None:
        return
    try:
        client.setex(_direct_redis_key(cache_key), ttl_seconds, json.dumps(payload, ensure_ascii=True, default=str))
    except Exception:
        _log_exception(
            dependencies,
            "agent-cache redis-set failed key=%s ttl=%s",
            cache_key,
            ttl_seconds,
        )


def _log_exception(
    dependencies: AgentRouteDependencies,
    message: str,
    *args: Any,
) -> None:
    logger = getattr(dependencies.application, "logger", None)
    if logger is not None:
        logger.exception(message, *args)


def _enter_singleflight(cache_key: str) -> bool:
    with _REFRESH_LOCK:
        if cache_key in _REFRESHING_KEYS:
            return False
        _REFRESHING_KEYS.add(cache_key)
        return True


def _leave_singleflight(cache_key: str) -> None:
    with _REFRESH_LOCK:
        _REFRESHING_KEYS.discard(cache_key)


def _serve_agent_with_cache(
    dependencies: AgentRouteDependencies,
    *,
    kind: str,
    payload: dict[str, Any],
    live_builder: Callable[[], dict[str, Any]],
    fallback_builder: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    cache_key = _cache_key(kind, payload)
    cached = _cached_response(dependencies, cache_key)
    if cached is not None:
        return cached

    if not _enter_singleflight(cache_key):
        fallback = fallback_builder()
        fallback["cacheStatus"] = "in-flight"
        fallback["cacheKey"] = cache_key
        return fallback

    try:
        allowed, budget = claim_agent_live_call(kind)
        if not allowed:
            fallback = fallback_builder()
            fallback["cacheStatus"] = "budget-fallback"
            fallback["cacheKey"] = cache_key
            fallback["dailyBudget"] = budget
            _store_response(
                dependencies,
                cache_key,
                fallback,
                _fallback_cache_ttl(),
            )
            return fallback
        response = live_builder()
        if isinstance(response, dict) and response.get("brief") and isinstance(response.get("focus"), list):
            ttl = _agent_cache_ttl() if response.get("status") == "live" else _fallback_cache_ttl()
            response["cacheStatus"] = "miss"
            response["cacheKey"] = cache_key
            response["dailyBudget"] = budget
            _store_response(dependencies, cache_key, response, ttl)
            return response
        fallback = fallback_builder()
        fallback["cacheStatus"] = "fallback"
        fallback["cacheKey"] = cache_key
        _store_response(
            dependencies,
            cache_key,
            fallback,
            _fallback_cache_ttl(),
        )
        return fallback
    except Exception:
        _log_exception(
            dependencies,
            "agent-cache live call failed key=%s",
            cache_key,
        )
        fallback = fallback_builder()
        fallback["cacheStatus"] = "error-fallback"
        fallback["cacheKey"] = cache_key
        _store_response(
            dependencies,
            cache_key,
            fallback,
            _fallback_cache_ttl(),
        )
        return fallback
    finally:
        _leave_singleflight(cache_key)


def create_agent_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = AgentRouteDependencies.from_context(context)
    bp = Blueprint("agent_routes", __name__)

    @bp.route("/agent/market-insights", methods=["POST"])
    def api_market_insights():
        if not _agent_enabled():
            return _agent_disabled_response()
        if not _agent_access_allowed():
            return _agent_forbidden_response()
        retry_after = _check_agent_rate_limit()
        if retry_after is not None:
            return _agent_rate_limited_response(retry_after)
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON object required"}), 400
        if request.headers.get("X-PolyData-Agent-Gateway-Attempt") == "1":
            return jsonify(build_market_insight(payload))
        return jsonify(_serve_agent_with_cache(
            dependencies,
            kind="market",
            payload=payload,
            live_builder=lambda: call_market_insight_gateway(payload) if gateway_configured() else build_market_insight(payload),
            fallback_builder=lambda: build_market_insight_fallback(payload),
        ))

    @bp.route("/agent/market-wide-insights", methods=["POST"])
    def api_market_wide_insights():
        if not _agent_enabled():
            return _agent_disabled_response()
        if not _agent_access_allowed():
            return _agent_forbidden_response()
        retry_after = _check_agent_rate_limit()
        if retry_after is not None:
            return _agent_rate_limited_response(retry_after)
        payload = request.get_json(silent=True)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON object required"}), 400
        if request.headers.get("X-PolyData-Agent-Gateway-Attempt") == "1":
            return jsonify(build_market_wide_insight(payload))
        return jsonify(_serve_agent_with_cache(
            dependencies,
            kind="market-wide",
            payload=payload,
            live_builder=lambda: call_market_wide_insight_gateway(payload) if gateway_configured() else build_market_wide_insight(payload),
            fallback_builder=lambda: build_market_wide_fallback(payload),
        ))

    return bp
