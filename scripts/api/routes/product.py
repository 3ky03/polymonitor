from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from flask import Blueprint, g, jsonify, request

from api.context import resolve_route_callable


@dataclass(frozen=True)
class ProductRouteDependencies:
    authenticate: Callable[..., Any]
    request_metadata: Callable[[Any], dict[str, Any]]
    get_watchlist: Callable[..., dict[str, Any]]
    add_market: Callable[..., dict[str, Any]]
    remove_market: Callable[..., None]
    create_rule: Callable[..., dict[str, Any]]
    delete_rule: Callable[..., None]
    get_alerts: Callable[..., dict[str, Any]]
    mark_alert_read: Callable[..., None]
    mark_all_read: Callable[..., int]
    get_preferences: Callable[..., dict[str, Any]]
    update_preferences: Callable[..., dict[str, Any]]
    get_web_push: Callable[..., dict[str, Any]]
    subscribe_web_push: Callable[..., dict[str, Any]]
    unsubscribe_web_push: Callable[..., dict[str, Any]]

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> ProductRouteDependencies:
        return cls(
            authenticate=resolve_route_callable(context, "authenticate_user_request"),
            request_metadata=resolve_route_callable(context, "auth_request_metadata"),
            get_watchlist=resolve_route_callable(context, "get_product_watchlist"),
            add_market=resolve_route_callable(context, "add_product_watchlist_market"),
            remove_market=resolve_route_callable(context, "remove_product_watchlist_market"),
            create_rule=resolve_route_callable(context, "create_product_alert_rule"),
            delete_rule=resolve_route_callable(context, "delete_product_alert_rule"),
            get_alerts=resolve_route_callable(context, "get_product_alert_events"),
            mark_alert_read=resolve_route_callable(context, "mark_product_alert_read"),
            mark_all_read=resolve_route_callable(context, "mark_all_product_alerts_read"),
            get_preferences=resolve_route_callable(context, "get_product_notification_preferences"),
            update_preferences=resolve_route_callable(context, "update_product_notification_preferences"),
            get_web_push=resolve_route_callable(context, "get_product_web_push"),
            subscribe_web_push=resolve_route_callable(context, "subscribe_product_web_push"),
            unsubscribe_web_push=resolve_route_callable(context, "unsubscribe_product_web_push"),
        )


def _payload() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _metadata(dependencies: ProductRouteDependencies) -> dict[str, Any]:
    value = dependencies.request_metadata(request)
    value["request_id"] = getattr(g, "request_id", "")
    return value


def create_product_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = ProductRouteDependencies.from_context(context)
    bp = Blueprint("product_routes", __name__)

    @bp.get("/product/watchlist")
    def watchlist():
        return jsonify(dependencies.get_watchlist(dependencies.authenticate(request)))

    @bp.post("/product/watchlist/markets")
    def add_watchlist_market():
        principal = dependencies.authenticate(request, require_csrf=True)
        data = _payload()
        item = dependencies.add_market(principal, data.get("marketId"), data.get("note"), _metadata(dependencies))
        return jsonify({"item": item}), 201

    @bp.delete("/product/watchlist/markets/<int:market_id>")
    def remove_watchlist_market(market_id: int):
        principal = dependencies.authenticate(request, require_csrf=True)
        dependencies.remove_market(principal, market_id, _metadata(dependencies))
        return jsonify({"status": "ok"})

    @bp.post("/product/alert-rules")
    def create_alert_rule():
        principal = dependencies.authenticate(request, require_csrf=True)
        data = _payload()
        item = dependencies.create_rule(
            principal,
            market_id=data.get("marketId"),
            kind=data.get("kind"),
            threshold=data.get("threshold"),
            cooldown_seconds=data.get("cooldownSeconds"),
            metadata=_metadata(dependencies),
        )
        return jsonify({"item": item}), 201

    @bp.delete("/product/alert-rules/<string:rule_id>")
    def delete_alert_rule(rule_id: str):
        principal = dependencies.authenticate(request, require_csrf=True)
        dependencies.delete_rule(principal, rule_id, _metadata(dependencies))
        return jsonify({"status": "ok"})

    @bp.get("/product/alerts")
    def alerts():
        principal = dependencies.authenticate(request)
        return jsonify(
            dependencies.get_alerts(
                principal,
                limit=request.args.get("limit", 100, type=int) or 100,
                unread_only=request.args.get("unread", "").strip().lower() in {"1", "true", "yes"},
            )
        )

    @bp.post("/product/alerts/<string:event_id>/read")
    def mark_alert_read(event_id: str):
        principal = dependencies.authenticate(request, require_csrf=True)
        dependencies.mark_alert_read(principal, event_id, _metadata(dependencies))
        return jsonify({"status": "ok"})

    @bp.post("/product/alerts/read-all")
    def mark_all_alerts_read():
        principal = dependencies.authenticate(request, require_csrf=True)
        count = dependencies.mark_all_read(principal, _metadata(dependencies))
        return jsonify({"status": "ok", "count": count})

    @bp.get("/product/notification-preferences")
    def notification_preferences():
        return jsonify(dependencies.get_preferences(dependencies.authenticate(request)))

    @bp.put("/product/notification-preferences")
    def update_notification_preferences():
        principal = dependencies.authenticate(request, require_csrf=True)
        return jsonify(dependencies.update_preferences(principal, _payload(), _metadata(dependencies)))

    @bp.get("/product/web-push")
    def web_push_status():
        return jsonify(dependencies.get_web_push(dependencies.authenticate(request)))

    @bp.post("/product/web-push/subscriptions")
    def subscribe_web_push():
        principal = dependencies.authenticate(request, require_csrf=True)
        return jsonify(dependencies.subscribe_web_push(principal, _payload(), _metadata(dependencies))), 201

    @bp.delete("/product/web-push/subscriptions")
    def unsubscribe_web_push():
        principal = dependencies.authenticate(request, require_csrf=True)
        return jsonify(
            dependencies.unsubscribe_web_push(
                principal,
                _payload().get("endpoint"),
                _metadata(dependencies),
            )
        )

    return bp
