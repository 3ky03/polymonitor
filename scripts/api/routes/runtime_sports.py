from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from flask import Blueprint, jsonify, request

from api.context import resolve_route_callable


WORLDCUP_DASHBOARD_CACHE_CONTROL = "public, max-age=20, stale-while-revalidate=120"


@dataclass(frozen=True)
class RuntimeSportsRouteDependencies:
    get_nba_scoreboard_snapshot: Callable[..., Any]
    get_nba_intel_snapshot: Callable[..., Any]
    get_nba_matchup_predictor_snapshot: Callable[..., Any]
    get_worldcup_intel_snapshot: Callable[..., Any]
    get_worldcup_dashboard_snapshot: Callable[..., Any]
    get_worldcup_core_snapshot: Callable[..., Any]
    get_worldcup_live_snapshot: Callable[..., Any]
    get_worldcup_panel_snapshot: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> RuntimeSportsRouteDependencies:
        return cls(
            get_nba_scoreboard_snapshot=resolve_route_callable(
                context,
                "get_nba_scoreboard_snapshot",
            ),
            get_nba_intel_snapshot=resolve_route_callable(
                context,
                "get_nba_intel_snapshot",
            ),
            get_nba_matchup_predictor_snapshot=resolve_route_callable(
                context,
                "get_nba_matchup_predictor_snapshot",
            ),
            get_worldcup_intel_snapshot=resolve_route_callable(
                context,
                "get_worldcup_intel_snapshot",
            ),
            get_worldcup_dashboard_snapshot=resolve_route_callable(
                context,
                "get_worldcup_dashboard_snapshot",
            ),
            get_worldcup_core_snapshot=resolve_route_callable(
                context,
                "get_worldcup_core_snapshot",
            ),
            get_worldcup_live_snapshot=resolve_route_callable(
                context,
                "get_worldcup_live_snapshot",
            ),
            get_worldcup_panel_snapshot=resolve_route_callable(
                context,
                "get_worldcup_panel_snapshot",
            ),
        )


def _bounded_int_arg(name: str, default: int, *, lower: int, upper: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(upper, max(lower, value))


def create_runtime_sports_blueprint(
    context: Mapping[str, Any],
) -> Blueprint:
    dependencies = RuntimeSportsRouteDependencies.from_context(context)
    bp = Blueprint("runtime_sports_routes", __name__)

    @bp.route("/runtime/sports/nba", methods=["GET"])
    def api_runtime_nba():
        limit = _bounded_int_arg("limit", 10, lower=1, upper=20)
        return jsonify(
            dependencies.get_nba_scoreboard_snapshot(limit=limit)
        )

    @bp.route("/runtime/sports/nba-intel", methods=["GET"])
    def api_runtime_nba_intel():
        limit = _bounded_int_arg("limit", 12, lower=1, upper=24)
        return jsonify(dependencies.get_nba_intel_snapshot(limit=limit))

    @bp.route("/runtime/sports/nba-matchup-predictor", methods=["GET"])
    def api_runtime_nba_matchup_predictor():
        limit = _bounded_int_arg("limit", 8, lower=1, upper=16)
        return jsonify(
            dependencies.get_nba_matchup_predictor_snapshot(limit=limit)
        )

    @bp.route("/runtime/sports/worldcup-intel", methods=["GET"])
    def api_runtime_worldcup_intel():
        limit = _bounded_int_arg("limit", 96, lower=12, upper=160)
        return jsonify(dependencies.get_worldcup_intel_snapshot(limit=limit))

    @bp.route("/runtime/worldcup/dashboard", methods=["GET"])
    def api_runtime_worldcup_dashboard():
        response = jsonify(dependencies.get_worldcup_dashboard_snapshot())
        response.headers["Cache-Control"] = WORLDCUP_DASHBOARD_CACHE_CONTROL
        response.headers["Vary"] = "Accept-Encoding"
        return response

    @bp.route("/runtime/worldcup/core", methods=["GET"])
    def api_runtime_worldcup_core():
        response = jsonify(dependencies.get_worldcup_core_snapshot())
        response.headers["Cache-Control"] = WORLDCUP_DASHBOARD_CACHE_CONTROL
        response.headers["Vary"] = "Accept-Encoding"
        return response

    @bp.route("/runtime/worldcup/live", methods=["GET"])
    def api_runtime_worldcup_live():
        response = jsonify(dependencies.get_worldcup_live_snapshot())
        response.headers["Cache-Control"] = "public, max-age=20, stale-while-revalidate=60"
        response.headers["Vary"] = "Accept-Encoding"
        return response

    @bp.route("/runtime/worldcup/panel/<panel_id>", methods=["GET"])
    def api_runtime_worldcup_panel(panel_id: str):
        response = jsonify(
            dependencies.get_worldcup_panel_snapshot(panel_id=panel_id)
        )
        response.headers["Cache-Control"] = WORLDCUP_DASHBOARD_CACHE_CONTROL
        response.headers["Vary"] = "Accept-Encoding"
        return response

    return bp
