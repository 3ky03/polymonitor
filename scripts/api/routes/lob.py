from __future__ import annotations

from flask import Blueprint, jsonify, request


def create_lob_blueprint(helpers: dict) -> Blueprint:
    bp = Blueprint("lob_routes", __name__)

    @bp.route("/runtime/lob/<int:market_id>", methods=["GET"])
    def api_runtime_lob_by_market_id(market_id: int):
        payload = helpers["get_runtime_lob_payload"](market_id)
        status_code = int(payload.pop("_status", 200))
        return jsonify(payload), status_code

    @bp.route("/runtime/lob/coverage-targets", methods=["GET"])
    def api_runtime_lob_coverage_targets():
        payload = helpers["get_lob_coverage_targets_payload"](
            limit=request.args.get("limit") or 250,
            topics=request.args.get("topics") or "",
        )
        status_code = int(payload.pop("_status", 200))
        return jsonify(payload), status_code

    @bp.route("/runtime/lob/token/<token_id>", methods=["GET"])
    def api_runtime_lob_by_token(token_id: str):
        payload = helpers["get_runtime_lob_by_token_payload"](
            token_id,
            no_token_id=request.args.get("noTokenId") or "",
            market_title=request.args.get("title") or "",
            market_id=request.args.get("marketId", type=int),
        )
        status_code = int(payload.pop("_status", 200))
        return jsonify(payload), status_code

    @bp.route("/runtime/lob/token/<token_id>/snapshots", methods=["GET"])
    def api_runtime_lob_snapshots_by_token(token_id: str):
        payload = helpers["get_lob_snapshots_by_token_payload"](
            token_id,
            side=request.args.get("side") or "",
            limit=request.args.get("limit") or 48,
        )
        status_code = int(payload.pop("_status", 200))
        return jsonify(payload), status_code

    return bp
