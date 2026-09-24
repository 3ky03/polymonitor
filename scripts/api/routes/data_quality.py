from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from flask import Blueprint, jsonify

from api.context import resolve_route_callable


@dataclass(frozen=True)
class DataQualityRouteDependencies:
    get_market_data_quality_payload: Callable[[], dict[str, Any]]

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> DataQualityRouteDependencies:
        return cls(
            get_market_data_quality_payload=cast(
                Callable[[], dict[str, Any]],
                resolve_route_callable(context, "get_market_data_quality_payload"),
            )
        )


def create_data_quality_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = DataQualityRouteDependencies.from_context(context)
    bp = Blueprint("data_quality_routes", __name__)

    @bp.route("/data-quality/markets", methods=["GET"])
    def api_market_data_quality():
        return jsonify(dependencies.get_market_data_quality_payload())

    return bp
