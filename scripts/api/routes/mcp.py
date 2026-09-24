from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, Response, jsonify, request

from api.context import resolve_route_callable, resolve_route_value
from api.services.auth_service import AuthError
from api.services.mcp_service import (
    MCP_PROTOCOL_VERSION,
    MCP_SCOPE,
    McpDependencies,
    McpProtocolError,
    dispatch,
)


@dataclass(frozen=True)
class McpRouteDependencies:
    authenticate: Callable[..., Any]
    search_markets: Callable[..., dict[str, Any]]
    get_market_workspace: Callable[[int], dict[str, Any]]
    get_market_oracle: Callable[[int], dict[str, Any]]
    get_market_data_quality: Callable[[], dict[str, Any]]
    get_public_briefing: Callable[[str], dict[str, Any]]
    allowed_origins: frozenset[str]

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> McpRouteDependencies:
        return cls(
            authenticate=resolve_route_callable(context, "authenticate_request"),
            search_markets=resolve_route_callable(context, "search_markets"),
            get_market_workspace=resolve_route_callable(context, "get_market_workspace_payload"),
            get_market_oracle=resolve_route_callable(context, "get_market_oracle_payload"),
            get_market_data_quality=resolve_route_callable(context, "get_market_data_quality_payload"),
            get_public_briefing=resolve_route_callable(context, "get_public_briefing"),
            allowed_origins=frozenset(
                str(value)
                for value in resolve_route_value(context, "MCP_ALLOWED_ORIGINS", ())
                if str(value)
            ),
        )

    def service_dependencies(self) -> McpDependencies:
        return McpDependencies(
            search_markets=self.search_markets,
            get_market_workspace=self.get_market_workspace,
            get_market_oracle=self.get_market_oracle,
            get_market_data_quality=self.get_market_data_quality,
            get_public_briefing=self.get_public_briefing,
        )


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _origin_allowed(origin: str, request_host: str, configured_origins: frozenset[str]) -> bool:
    if not origin:
        return True
    if origin in configured_origins:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc == request_host


def create_mcp_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = McpRouteDependencies.from_context(context)
    service_dependencies = dependencies.service_dependencies()
    bp = Blueprint("mcp_routes", __name__)

    @bp.route("/mcp", methods=["GET", "POST", "OPTIONS"])
    def mcp_endpoint():
        if request.method == "OPTIONS":
            return Response(status=204)
        if request.method == "GET":
            response = jsonify({
                "error": "This stateless Streamable HTTP endpoint accepts JSON-RPC over POST.",
                "discovery": "/.well-known/mcp/server-card.json",
            })
            response.status_code = 405
            response.headers["Allow"] = "POST, OPTIONS"
            response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
            return response
        origin = request.headers.get("Origin", "").strip()
        if not _origin_allowed(origin, request.host, dependencies.allowed_origins):
            response = jsonify(_error(None, -32002, "Origin is not allowed"))
            response.status_code = 403
            response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
            return response

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            response = jsonify(_error(None, -32600, "Invalid JSON-RPC request"))
            response.status_code = 400
            response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
            return response
        request_id = payload.get("id")
        if payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
            response = jsonify(_error(request_id, -32600, "Invalid JSON-RPC request"))
            response.status_code = 400
            response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
            return response
        method = str(payload["method"])
        protocol_version = request.headers.get("MCP-Protocol-Version", "").strip()
        if method != "initialize" and protocol_version != MCP_PROTOCOL_VERSION:
            response = jsonify(_error(request_id, -32600, "Unsupported or missing MCP-Protocol-Version"))
            response.status_code = 400
            response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
            return response
        if method == "notifications/initialized":
            return Response(status=204)
        if method == "tools/call":
            try:
                dependencies.authenticate(request, required_scope=MCP_SCOPE)
            except AuthError as exc:
                response = jsonify(_error(request_id, -32001, exc.message, {"code": exc.code}))
                response.status_code = exc.status_code
                if exc.retry_after:
                    response.headers["Retry-After"] = str(exc.retry_after)
                if exc.status_code == 401:
                    response.headers["WWW-Authenticate"] = 'Bearer realm="polymonitor-mcp", scope="mcp:read"'
                response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
                return response
        try:
            result = dispatch(method, payload.get("params"), service_dependencies)
        except McpProtocolError as exc:
            response = jsonify(_error(request_id, exc.code, exc.message, exc.data))
            response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
            return response
        except AuthError as exc:
            response = jsonify(_error(request_id, -32004, exc.message, {"code": exc.code}))
            response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
            return response
        response = jsonify({"jsonrpc": "2.0", "id": request_id, "result": result})
        response.headers["MCP-Protocol-Version"] = MCP_PROTOCOL_VERSION
        return response

    return bp
