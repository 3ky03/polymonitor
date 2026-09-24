from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from flask import Blueprint, jsonify, make_response

from api.contracts import build_openapi_document
from api.runtime_panels import RUNTIME_PANEL_MODULES


def create_schema_blueprint(_context: Mapping[str, Any]) -> Blueprint:
    bp = Blueprint("schema_routes", __name__)
    document = build_openapi_document(RUNTIME_PANEL_MODULES)

    @bp.route("/openapi.json", methods=["GET"])
    @bp.route("/v1/openapi.json", methods=["GET"])
    def api_openapi_document():
        response = make_response(jsonify(document))
        response.headers["Cache-Control"] = "public, max-age=300"
        return response

    return bp
