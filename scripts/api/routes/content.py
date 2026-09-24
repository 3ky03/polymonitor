from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from flask import Blueprint, jsonify, request

from api.context import resolve_route_callable


@dataclass(frozen=True)
class ContentRouteDependencies:
    get_market_by_id: Callable[[int], dict[str, Any] | None]
    get_related_content_payload: Callable[..., dict[str, Any]]
    get_latest_content_payload: Callable[..., dict[str, Any]]
    get_runtime_content_latest: Callable[..., dict[str, Any]]

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> ContentRouteDependencies:
        return cls(
            get_market_by_id=cast(
                Callable[[int], dict[str, Any] | None],
                resolve_route_callable(context, "get_market_by_id"),
            ),
            get_related_content_payload=cast(
                Callable[..., dict[str, Any]],
                resolve_route_callable(context, "get_related_content_payload"),
            ),
            get_latest_content_payload=cast(
                Callable[..., dict[str, Any]],
                resolve_route_callable(context, "get_latest_content_payload"),
            ),
            get_runtime_content_latest=cast(
                Callable[..., dict[str, Any]],
                resolve_route_callable(context, "get_runtime_content_latest"),
            ),
        )

def _publish_latest_content(payload: dict) -> None:
    if request.headers.get("X-PolyData-Telegram-Publisher") == "1":
        return
    try:
        from telegram.topics.runtime_bridge import publish_panel_snapshot
    except Exception:
        return
    try:
        publish_panel_snapshot("latest-content", payload)
    except Exception:
        return


def _publish_related_content(payload: dict) -> None:
    if request.headers.get("X-PolyData-Telegram-Publisher") == "1":
        return
    try:
        from telegram.topics.runtime_bridge import publish_panel_snapshot
    except Exception:
        return
    try:
        publish_panel_snapshot("related-news", payload)
    except Exception:
        return


def _runtime_content_fallback(
    limit: int,
    *,
    dependencies: ContentRouteDependencies,
    market_id: int | None = None,
) -> dict:
    enabled = str(os.environ.get("POLYDATA_CONTENT_API_REFRESH_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        payload = {"items": [], "sourceMode": "database-empty", "degraded": True}
    else:
        payload = dependencies.get_runtime_content_latest(limit=limit)
        payload["sourceMode"] = f"{payload.get('sourceMode') or 'runtime-rss'}:db-fallback"
        payload["degraded"] = True
    if market_id is not None:
        payload["marketId"] = market_id
    return payload


def create_content_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = ContentRouteDependencies.from_context(context)
    bp = Blueprint("content_routes", __name__)

    @bp.route("/content/market/<int:market_id>", methods=["GET"])
    def api_content_by_market_id(market_id: int):
        limit = min(20, max(1, int(request.args.get("limit", 8))))
        try:
            market = dependencies.get_market_by_id(market_id)
            if not market:
                return jsonify({"error": "Market not found", "marketId": market_id}), 404
            payload = dependencies.get_related_content_payload(market_id, limit=limit)
            payload = {
                **payload,
                "marketTitle": market.get("title"),
                "marketSlug": market.get("slug"),
                "marketCategory": market.get("category"),
            }
            _publish_related_content(payload)
            return jsonify(payload)
        except Exception:
            return jsonify(
                _runtime_content_fallback(
                    limit,
                    dependencies=dependencies,
                    market_id=market_id,
                )
            )

    @bp.route("/content/latest", methods=["GET"])
    def api_content_latest():
        limit = min(20, max(1, int(request.args.get("limit", 8))))
        try:
            payload = dependencies.get_latest_content_payload(limit=limit)
        except Exception:
            payload = _runtime_content_fallback(limit, dependencies=dependencies)
        _publish_latest_content(payload)
        return jsonify(payload)

    return bp
