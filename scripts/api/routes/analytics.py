from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from flask import Blueprint, jsonify, request

from api.context import resolve_route_callable


@dataclass(frozen=True)
class AnalyticsRouteDependencies:
    get_top_addresses_cached: Callable[..., Any]
    get_active_addresses_cached: Callable[..., Any]
    normalize_address: Callable[..., Any]
    get_address_summary_cached: Callable[..., Any]
    get_address_trades_payload: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> AnalyticsRouteDependencies:
        return cls(
            get_top_addresses_cached=resolve_route_callable(
                context,
                "get_top_addresses_cached",
            ),
            get_active_addresses_cached=resolve_route_callable(
                context,
                "get_active_addresses_cached",
            ),
            normalize_address=resolve_route_callable(
                context,
                "normalize_address",
            ),
            get_address_summary_cached=resolve_route_callable(
                context,
                "get_address_summary_cached",
            ),
            get_address_trades_payload=resolve_route_callable(
                context,
                "get_address_trades_payload",
            ),
        )


def create_analytics_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = AnalyticsRouteDependencies.from_context(context)
    bp = Blueprint("analytics_routes", __name__)

    @bp.route("/analytics/addresses/top", methods=["GET"])
    def api_top_addresses():
        limit = min(200, max(1, int(request.args.get("limit", 50))))
        days_raw = request.args.get("days")
        days = int(days_raw) if days_raw not in (None, "", "0") else None
        return jsonify(
            dependencies.get_top_addresses_cached(days=days, limit=limit)
        )

    @bp.route("/analytics/addresses/active", methods=["GET"])
    def api_active_addresses():
        days = min(365, max(1, int(request.args.get("days", 30))))
        return jsonify(dependencies.get_active_addresses_cached(days=days))

    @bp.route("/analytics/addresses/<address>", methods=["GET"])
    def api_address_summary(address: str):
        normalized = dependencies.normalize_address(address)
        if not normalized:
            return jsonify({"error": "address required"}), 400
        days = min(365, max(1, int(request.args.get("days", 30))))
        return jsonify(
            dependencies.get_address_summary_cached(normalized, days=days)
        )

    @bp.route("/analytics/addresses/<address>/trades", methods=["GET"])
    def api_address_trades(address: str):
        normalized = dependencies.normalize_address(address)
        if not normalized:
            return jsonify({"error": "address required"}), 400

        limit = min(200, max(1, int(request.args.get("limit", 100))))
        market_id_raw = request.args.get("marketId")
        market_id = int(market_id_raw) if market_id_raw not in (None, "") else None
        start_ts = (request.args.get("startTs") or "").strip() or None
        end_ts = (request.args.get("endTs") or "").strip() or None
        before_ts = (request.args.get("beforeTs") or "").strip() or None
        before_block_raw = request.args.get("beforeBlockNumber")
        before_log_raw = request.args.get("beforeLogIndex")
        before_block_number = int(before_block_raw) if before_block_raw not in (None, "") else None
        before_log_index = int(before_log_raw) if before_log_raw not in (None, "") else None

        return jsonify(
            dependencies.get_address_trades_payload(
                normalized,
                limit=limit,
                market_id=market_id,
                start_ts=start_ts,
                end_ts=end_ts,
                before_ts=before_ts,
                before_block_number=before_block_number,
                before_log_index=before_log_index,
            )
        )

    return bp
