from __future__ import annotations

from flask import Blueprint, jsonify, request


def create_runtime_signals_blueprint(helpers: dict) -> Blueprint:
    bp = Blueprint("runtime_signal_routes", __name__)

    @bp.route("/runtime/signals/alpha", methods=["GET"])
    def api_runtime_alpha():
        limit = min(20, max(1, int(request.args.get("limit", 8))))
        return jsonify(helpers["get_alpha_signal_snapshot"](limit=limit))

    @bp.route("/runtime/trades/whales", methods=["GET"])
    def api_runtime_whales():
        limit = min(40, max(1, int(request.args.get("limit", 14))))
        return jsonify(helpers["get_whale_trades_snapshot"](limit=limit))

    @bp.route("/runtime/trades/suspicious", methods=["GET"])
    def api_runtime_suspicious():
        limit = min(40, max(1, int(request.args.get("limit", 12))))
        return jsonify(helpers["get_suspicious_trades_snapshot"](limit=limit))

    return bp
