from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from flask import Blueprint, jsonify, make_response

from api.context import resolve_route_value
from agent.market_wide.snapshot import normalize_lens, read_market_wide_quant_snapshot, read_market_wide_snapshot, snapshot_response


@dataclass(frozen=True)
class AgentSnapshotRouteDependencies:
    source: Mapping[str, Any]
    snapshot_store: Any

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> AgentSnapshotRouteDependencies:
        return cls(
            source=context,
            snapshot_store=resolve_route_value(context, "SNAPSHOT_STORE"),
        )


def create_agent_snapshot_blueprint(
    context: Mapping[str, Any],
) -> Blueprint:
    dependencies = AgentSnapshotRouteDependencies.from_context(context)
    bp = Blueprint("agent_snapshot_routes", __name__)

    @bp.route("/runtime/agent/market-wide-insights/<lens>", methods=["GET"])
    def api_market_wide_snapshot(lens: str):
        normalized_lens = normalize_lens(lens)
        snapshot = read_market_wide_snapshot(
            dependencies.source,
            normalized_lens,
            allow_stale=True,
        )
        if snapshot is None:
            return jsonify({"error": "agent-snapshot-missing", "status": "missing", "lens": normalized_lens}), 404
        response = make_response(jsonify(snapshot_response(snapshot)))
        response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300"
        return response

    @bp.route("/runtime/agent/market-wide-quant/<lens>", methods=["GET"])
    def api_market_wide_quant_snapshot(lens: str):
        normalized_lens = normalize_lens(lens)
        snapshot = read_market_wide_quant_snapshot(
            dependencies.source,
            normalized_lens,
            allow_stale=True,
        )
        if snapshot is None:
            return jsonify({"error": "agent-quant-snapshot-missing", "status": "missing", "lens": normalized_lens}), 404
        response = make_response(jsonify(snapshot))
        response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300"
        return response

    @bp.route("/runtime/agent/market-wide-events/<run_id>", methods=["GET"])
    def api_market_wide_events(run_id: str):
        store = dependencies.snapshot_store
        if store is None or not hasattr(store, "get_agent_node_events"):
            return jsonify({"error": "agent-event-log-unavailable", "status": "unavailable", "runId": run_id}), 404
        events = store.get_agent_node_events(run_id)
        return jsonify({"status": "ok", "runId": run_id, "items": events, "count": len(events)})

    return bp
