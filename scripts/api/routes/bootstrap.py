from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from flask import Blueprint, jsonify, request

from api.context import resolve_route_callable


@dataclass(frozen=True)
class BootstrapRouteDependencies:
    get_dashboard_payload_cached: Callable[[], Any]
    get_bootstrap_payload_cached: Callable[[], Any]
    search_markets: Callable[..., Any]

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> BootstrapRouteDependencies:
        return cls(
            get_dashboard_payload_cached=cast(
                Callable[[], Any],
                resolve_route_callable(context, "get_dashboard_payload_cached"),
            ),
            get_bootstrap_payload_cached=cast(
                Callable[[], Any],
                resolve_route_callable(context, "get_bootstrap_payload_cached"),
            ),
            search_markets=resolve_route_callable(context, "search_markets"),
        )


def create_bootstrap_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = BootstrapRouteDependencies.from_context(context)
    bp = Blueprint("bootstrap_routes", __name__)

    @bp.route("/dashboard", methods=["GET"])
    def api_dashboard():
        return jsonify(dependencies.get_dashboard_payload_cached())

    @bp.route("/bootstrap", methods=["GET"])
    def api_bootstrap():
        return jsonify(dependencies.get_bootstrap_payload_cached())

    @bp.route("/search", methods=["GET"])
    def api_search():
        query = request.args.get("q") or ""
        limit = min(50, max(1, int(request.args.get("limit", 10))))
        return jsonify(dependencies.search_markets(query, limit=limit))

    return bp
