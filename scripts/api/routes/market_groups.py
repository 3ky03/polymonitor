from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from flask import Blueprint, jsonify, request

from api.context import resolve_route_callable


@dataclass(frozen=True)
class MarketGroupRouteDependencies:
    get_market_groups_payload: Callable[..., dict[str, Any]]
    get_market_group_detail_payload: Callable[[str], dict[str, Any] | None]
    get_market_group_chart_payload: Callable[..., dict[str, Any] | None]

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> MarketGroupRouteDependencies:
        return cls(
            get_market_groups_payload=cast(
                Callable[..., dict[str, Any]],
                resolve_route_callable(context, "get_market_groups_payload"),
            ),
            get_market_group_detail_payload=cast(
                Callable[[str], dict[str, Any] | None],
                resolve_route_callable(context, "get_market_group_detail_payload"),
            ),
            get_market_group_chart_payload=cast(
                Callable[..., dict[str, Any] | None],
                resolve_route_callable(context, "get_market_group_chart_payload"),
            ),
        )


def create_market_groups_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = MarketGroupRouteDependencies.from_context(context)
    bp = Blueprint("market_group_routes", __name__)

    @bp.route("/market-groups", methods=["GET"])
    def api_market_groups():
        query = (request.args.get("q") or "").strip()
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(200, max(1, int(request.args.get("pageSize", 80))))
        sort = (request.args.get("sort") or "active").strip().lower()
        return jsonify(
            dependencies.get_market_groups_payload(
                query=query,
                page=page,
                page_size=page_size,
                sort=sort,
            )
        )

    @bp.route("/market-groups/<event_id>/detail", methods=["GET"])
    def api_market_group_detail(event_id: str):
        payload = dependencies.get_market_group_detail_payload(event_id)
        if not payload:
            return jsonify({"error": "market group not found", "eventId": event_id}), 404
        return jsonify(payload)

    @bp.route("/market-groups/<event_id>/chart", methods=["GET"])
    def api_market_group_chart(event_id: str):
        range_name = (request.args.get("range") or "1d").strip().lower()
        payload = dependencies.get_market_group_chart_payload(event_id, range_name=range_name)
        if not payload:
            return jsonify({"error": "market group chart unavailable", "eventId": event_id, "range": range_name}), 404
        return jsonify(payload)

    return bp
