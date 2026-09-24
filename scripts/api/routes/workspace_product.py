from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from flask import Blueprint, g, jsonify, request

from api.context import resolve_route_callable


@dataclass(frozen=True)
class WorkspaceProductDependencies:
    authenticate: Callable[..., Any]
    request_metadata: Callable[[Any], dict[str, Any]]
    get_layout: Callable[..., dict[str, Any]]
    put_layout: Callable[..., dict[str, Any]]
    list_briefings: Callable[..., list[dict[str, Any]]]
    create_briefing: Callable[..., dict[str, Any]]
    revoke_briefing: Callable[..., None]
    get_public_briefing: Callable[..., dict[str, Any]]

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> WorkspaceProductDependencies:
        return cls(
            authenticate=resolve_route_callable(context, "authenticate_user_request"),
            request_metadata=resolve_route_callable(context, "auth_request_metadata"),
            get_layout=resolve_route_callable(context, "get_workspace_layout"),
            put_layout=resolve_route_callable(context, "put_workspace_layout"),
            list_briefings=resolve_route_callable(context, "list_briefings"),
            create_briefing=resolve_route_callable(context, "create_briefing"),
            revoke_briefing=resolve_route_callable(context, "revoke_briefing"),
            get_public_briefing=resolve_route_callable(context, "get_public_briefing"),
        )


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _metadata(dependencies: WorkspaceProductDependencies) -> dict[str, Any]:
    value = dependencies.request_metadata(request)
    value["request_id"] = getattr(g, "request_id", "")
    return value


def create_workspace_product_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = WorkspaceProductDependencies.from_context(context)
    bp = Blueprint("workspace_product_routes", __name__)

    @bp.get("/product/workspace-layout")
    def workspace_layout():
        return jsonify(dependencies.get_layout(dependencies.authenticate(request)))

    @bp.put("/product/workspace-layout")
    def update_workspace_layout():
        principal = dependencies.authenticate(request, require_csrf=True)
        return jsonify(dependencies.put_layout(principal, _payload(), _metadata(dependencies)))

    @bp.get("/product/briefings")
    def briefing_registry():
        return jsonify({"items": dependencies.list_briefings(dependencies.authenticate(request))})

    @bp.post("/product/briefings")
    def create_briefing():
        principal = dependencies.authenticate(request, require_csrf=True)
        item = dependencies.create_briefing(principal, _payload().get("title"), _metadata(dependencies))
        return jsonify({"item": item}), 201

    @bp.delete("/product/briefings/<string:briefing_id>")
    def revoke_briefing(briefing_id: str):
        principal = dependencies.authenticate(request, require_csrf=True)
        dependencies.revoke_briefing(principal, briefing_id, _metadata(dependencies))
        return jsonify({"status": "ok"})

    @bp.get("/briefings/<string:public_id>")
    def public_briefing(public_id: str):
        return jsonify(dependencies.get_public_briefing(public_id))

    return bp
