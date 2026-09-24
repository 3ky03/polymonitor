from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from flask import Blueprint, jsonify, request

from api.context import RouteContext, resolve_route_callable
from api.services import system_service


@dataclass(frozen=True)
class SystemRouteDependencies:
    authenticate_request: Callable[..., Any]
    build_system_health_payload: Callable[[], Any]
    build_seed_health_payload: Callable[[], Any]
    describe_db_target: Callable[[], str]
    get_redis_client: Callable[[], Any]

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> SystemRouteDependencies:
        authenticate_request = (
            resolve_route_callable(context, "authenticate_request")
            if isinstance(context, RouteContext)
            else cast(Callable[..., Any], context.get("authenticate_request", lambda *_args, **_kwargs: None))
        )
        return cls(
            authenticate_request=authenticate_request,
            build_system_health_payload=cast(
                Callable[[], Any],
                resolve_route_callable(context, "build_system_health_payload"),
            ),
            build_seed_health_payload=cast(
                Callable[[], Any],
                resolve_route_callable(context, "build_seed_health_payload"),
            ),
            describe_db_target=cast(
                Callable[[], str],
                resolve_route_callable(context, "describe_db_target"),
            ),
            get_redis_client=cast(
                Callable[[], Any],
                resolve_route_callable(context, "get_redis_client"),
            ),
        )


def create_system_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = SystemRouteDependencies.from_context(context)
    bp = Blueprint("system_routes", __name__)

    @bp.route("/system/health", methods=["GET"])
    def api_system_health():
        dependencies.authenticate_request(request, required_role="admin", required_scope="operations:read")
        return jsonify(dependencies.build_system_health_payload())

    @bp.route("/system/seed-health", methods=["GET"])
    @bp.route("/runtime/system/seed-health", methods=["GET"])
    def api_seed_health():
        dependencies.authenticate_request(request, required_role="admin", required_scope="operations:read")
        return jsonify(dependencies.build_seed_health_payload())

    @bp.route("/system/operations", methods=["GET"])
    def api_operations():
        dependencies.authenticate_request(request, required_role="admin", required_scope="operations:read")
        return jsonify(system_service.build_operations_payload())

    @bp.route("/system/incidents", methods=["GET"])
    def api_operations_incidents():
        dependencies.authenticate_request(request, required_role="admin", required_scope="operations:read")
        return jsonify(system_service.build_incidents_payload())

    @bp.route("/health", methods=["GET"])
    def health():
        try:
            database_ready = bool(dependencies.describe_db_target())
        except Exception:
            database_ready = False
        try:
            redis_client = dependencies.get_redis_client()
            ping = getattr(redis_client, "ping", None)
            redis_ready = bool(ping()) if callable(ping) else bool(redis_client)
        except Exception:
            redis_ready = False
        return jsonify(
            {
                "status": "ok" if database_ready and redis_ready else "degraded",
                "database": database_ready,
                "redis": redis_ready,
                "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        )

    return bp
