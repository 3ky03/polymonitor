from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from flask import Blueprint, Response, current_app, jsonify, request

from api.contracts import api_envelope, api_error, runtime_panel_metadata
from api.context import resolve_route_callable
from api.runtime_panels import RUNTIME_PANEL_MODULES, get_panel_by_id
from api.runtime_panels.types import RuntimePanelContext
from api.services import hls_proxy_service, youtube_embed_service, youtube_live_probe_service

try:
    import requests as requests_module
except Exception:  # pragma: no cover
    requests_module = None


@dataclass(frozen=True)
class RuntimePanelRouteDependencies:
    panel_context: RuntimePanelContext
    utc_now_iso: Callable[[], str]
    natural_hazard_map_snapshot: Callable[..., dict[str, Any]] | None
    natural_hazard_event_detail: Callable[..., dict[str, Any] | None] | None
    natural_hazard_related_markets: Callable[..., dict[str, Any] | None] | None
    aviation_viewport_snapshot: Callable[..., dict[str, Any]] | None

    @classmethod
    def from_context(cls, context: Mapping[str, Any]) -> RuntimePanelRouteDependencies:
        return cls(
            panel_context=RuntimePanelContext.from_context(context),
            utc_now_iso=cast(Callable[[], str], resolve_route_callable(context, "utc_now_iso")),
            natural_hazard_map_snapshot=cast(
                Callable[..., dict[str, Any]] | None,
                context.get("get_natural_hazard_map_snapshot"),
            ),
            natural_hazard_event_detail=cast(
                Callable[..., dict[str, Any] | None] | None,
                context.get("get_natural_hazard_event_detail"),
            ),
            natural_hazard_related_markets=cast(
                Callable[..., dict[str, Any] | None] | None,
                context.get("get_natural_hazard_related_markets"),
            ),
            aviation_viewport_snapshot=cast(
                Callable[..., dict[str, Any]] | None,
                context.get("get_aviation_viewport_snapshot"),
            ),
        )


def _publish_runtime_panel(panel_id: str, payload: dict) -> None:
    if request.headers.get("X-PolyData-Telegram-Publisher") == "1":
        return
    try:
        from telegram.topics.runtime_bridge import publish_panel_snapshot
    except Exception:
        return
    try:
        publish_panel_snapshot(panel_id, payload)
    except Exception:
        return


def _get_panel_snapshot(panel, panel_context: RuntimePanelContext, limit: int | None):
    kwargs = {}
    if limit is not None:
        kwargs["limit"] = limit
    if panel.panel_id in {"market-tv-wire", "market-youtube-channels"}:
        category = request.args.get("category")
        if category:
            kwargs["category"] = category
    return panel.get_snapshot(panel_context, **kwargs)


def create_runtime_panels_blueprint(context: Mapping[str, Any]) -> Blueprint:
    dependencies = RuntimePanelRouteDependencies.from_context(context)
    bp = Blueprint("runtime_panel_routes", __name__)

    def _youtube_relay_token() -> str:
        return str(
            os.environ.get("POLYDATA_YOUTUBE_LIVE_RELAY_TOKEN")
            or os.environ.get("RELAY_SHARED_SECRET")
            or ""
        ).strip()

    def _youtube_relay_auth_header() -> str:
        return str(
            os.environ.get("POLYDATA_YOUTUBE_LIVE_RELAY_AUTH_HEADER")
            or os.environ.get("RELAY_AUTH_HEADER")
            or "x-polymonitor-relay-key"
        ).strip() or "x-polymonitor-relay-key"

    def _is_authorized_youtube_relay_request() -> bool:
        expected = _youtube_relay_token()
        if not expected:
            return False
        supplied = request.headers.get(_youtube_relay_auth_header()) or ""
        authorization = request.headers.get("Authorization") or ""
        return supplied == expected or authorization == f"Bearer {expected}"

    def _requested_panel_ids() -> list[str]:
        raw_ids = request.args.get("ids") or ""
        panel_ids = [
            panel_id.strip()
            for panel_id in raw_ids.split(",")
            if panel_id.strip()
        ]
        if not panel_ids:
            panel_ids = [panel.panel_id for panel in RUNTIME_PANEL_MODULES]
        return list(dict.fromkeys(panel_ids))

    def _collect_runtime_panels(panel_ids: list[str]):
        payloads: dict[str, Any] = {}
        legacy_errors: dict[str, str] = {}
        envelope_errors: list[dict[str, Any]] = []
        panel_meta: dict[str, Any] = {}
        for panel_id in panel_ids:
            panel = get_panel_by_id(panel_id)
            if panel is None:
                legacy_errors[panel_id] = "unknown-panel"
                envelope_errors.append(
                    api_error(
                        "unknown-panel",
                        f"Runtime panel '{panel_id}' is not registered.",
                        panel_id=panel_id,
                    )
                )
                continue
            raw_limit = request.args.get(f"limit.{panel_id}") or request.args.get("limit")
            limit = panel.clamp_limit(raw_limit)
            try:
                payload = _get_panel_snapshot(panel, dependencies.panel_context, limit)
                payloads[panel.panel_id] = payload
                panel_meta[panel.panel_id] = runtime_panel_metadata(panel, payload)
                _publish_runtime_panel(panel.panel_id, payload)
            except Exception as exc:
                current_app.logger.exception("runtime-panels batch failed panel_id=%s", panel_id)
                legacy_errors[panel_id] = exc.__class__.__name__
                envelope_errors.append(
                    api_error(
                        "panel-fetch-failed",
                        f"Runtime panel '{panel_id}' could not be refreshed.",
                        panel_id=panel_id,
                        retryable=True,
                    )
                )
        status = "ok" if not envelope_errors else ("partial" if payloads else "error")
        return payloads, legacy_errors, envelope_errors, panel_meta, status

    @bp.route("/runtime/content/hls-proxy", methods=["GET"])
    def api_runtime_hls_proxy():
        target_url = request.args.get("url") or ""
        try:
            data, content_type, status = hls_proxy_service.fetch_hls_resource(target_url)
        except hls_proxy_service.HlsProxyError as exc:
            return jsonify({"status": "error", "error": str(exc)}), exc.status_code
        except Exception as exc:
            current_app.logger.warning("runtime hls proxy failed url=%s error=%s", target_url[:160], exc)
            return jsonify({"status": "error", "error": "upstream HLS fetch failed"}), 502
        response = Response(data, status=status, content_type=content_type)
        response.headers["Cache-Control"] = hls_proxy_service.cache_control_for(content_type)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.route("/runtime/content/youtube-embed", methods=["GET"])
    def api_runtime_youtube_embed():
        video_id = request.args.get("videoId") or ""
        try:
            origin = request.host_url.rstrip("/")
            html = youtube_embed_service.build_youtube_embed_html(
                video_id=video_id,
                request_origin=origin,
                parent_origin=request.args.get("parentOrigin") or "",
                autoplay=request.args.get("autoplay"),
                mute=request.args.get("mute"),
                quality=request.args.get("vq"),
            )
        except ValueError as exc:
            return Response(str(exc), status=400, content_type="text/plain; charset=utf-8")
        return Response(html, status=200, headers=youtube_embed_service.youtube_embed_headers())

    @bp.route("/runtime/content/youtube-live", methods=["GET"])
    def api_runtime_youtube_live_relay():
        if not _is_authorized_youtube_relay_request():
            return jsonify({"error": "Unauthorized"}), 401
        channel = request.args.get("channel") or ""
        video_id = request.args.get("videoId") or ""
        if not channel and not video_id:
            return jsonify({"error": "Missing channel or videoId parameter"}), 400
        if requests_module is None:
            return jsonify({"error": "requests unavailable"}), 503
        ctx = {
            "requests": requests_module,
            "youtube_live_relay_base_url": "",
        }
        payload = youtube_live_probe_service._fetch_live_stream_info(ctx, channel=channel, video_id=video_id)
        response = jsonify(payload)
        response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=120"
        return response

    @bp.route("/runtime/panels", methods=["GET"])
    def api_runtime_panels_batch():
        panel_ids = _requested_panel_ids()
        payloads, errors, _, _, status = _collect_runtime_panels(panel_ids)
        return jsonify(
            {
                "generatedAt": dependencies.utc_now_iso(),
                "status": status,
                "panels": payloads,
                "errors": errors,
            }
        )

    @bp.route("/runtime/world/natural-hazards/related-markets", methods=["GET"])
    def api_natural_hazard_related_markets():
        event_id = str(request.args.get("eventId") or "").strip()
        if not event_id:
            return jsonify({
                "status": "error",
                "error": "event-id-required",
            }), 400
        if dependencies.natural_hazard_related_markets is None:
            return jsonify({
                "status": "error",
                "error": "hazard-market-linker-unavailable",
            }), 503
        try:
            limit = max(1, min(25, int(request.args.get("limit") or 8)))
        except (TypeError, ValueError):
            limit = 8
        payload = dependencies.natural_hazard_related_markets(
            event_id=event_id,
            limit=limit,
        )
        if payload is None:
            return jsonify({
                "status": "error",
                "error": "hazard-event-not-found",
                "eventId": event_id,
            }), 404
        response = jsonify(payload)
        response.headers["Cache-Control"] = "private, max-age=30"
        return response

    def _public_conditional_json(payload: dict[str, Any], cache_control: str):
        response = jsonify(payload)
        response.headers["Cache-Control"] = cache_control
        response.headers["Vary"] = "Accept-Encoding"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.add_etag()
        response.make_conditional(request)
        return response

    @bp.route("/runtime/world/natural-hazards/map", methods=["GET"])
    def api_natural_hazard_map_snapshot():
        if dependencies.natural_hazard_map_snapshot is None:
            return jsonify({"status": "error", "error": "hazard-map-feed-unavailable"}), 503
        source = str(request.args.get("source") or "").strip().lower()
        started_at = time.perf_counter()
        try:
            limit = max(1, min(1200, int(request.args.get("limit") or 1200)))
            zoom = max(0.0, min(12.0, float(request.args.get("zoom") or 2.0)))
            bbox = None
            bbox_text = str(request.args.get("bbox") or "").strip()
            if bbox_text:
                values = tuple(float(value) for value in bbox_text.split(","))
                if len(values) != 4:
                    raise ValueError("invalid-bbox")
                west, south, east, north = values
                if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                    raise ValueError("invalid-bbox")
                bbox = values
            kwargs = {"source": source, "limit": limit, "zoom": zoom}
            if bbox is not None:
                kwargs["bbox"] = bbox
            payload = dependencies.natural_hazard_map_snapshot(**kwargs)
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        response = _public_conditional_json(
            payload,
            "public, max-age=30, stale-while-revalidate=300, stale-if-error=86400",
        )
        response.headers["X-Map-Source"] = source
        response.headers["X-Map-Event-Count"] = str((payload.get("counts") or {}).get("events") or 0)
        response.headers["Server-Timing"] = f"hazard-map;dur={(time.perf_counter() - started_at) * 1000:.1f}"
        return response

    @bp.route("/runtime/transport/aviation-viewport", methods=["GET"])
    def api_aviation_viewport_snapshot():
        if dependencies.aviation_viewport_snapshot is None:
            return jsonify({"status": "error", "error": "aviation-viewport-unavailable"}), 503
        try:
            values = tuple(float(value) for value in str(request.args.get("bbox") or "").split(","))
            if len(values) != 4:
                raise ValueError("invalid-aviation-bbox")
            west, south, east, north = values
            if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                raise ValueError("invalid-aviation-bbox")
            zoom = max(0.0, min(12.0, float(request.args.get("zoom") or 2.0)))
            limit = max(1, min(360, int(request.args.get("limit") or 180)))
            payload = dependencies.aviation_viewport_snapshot(bbox=values, zoom=zoom, limit=limit)
        except ValueError as exc:
            return jsonify({"status": "error", "error": str(exc)}), 400
        return _public_conditional_json(payload, "public, max-age=15, stale-while-revalidate=30")

    @bp.route("/runtime/world/natural-hazards/events/<path:event_id>", methods=["GET"])
    def api_natural_hazard_event_detail(event_id: str):
        if dependencies.natural_hazard_event_detail is None:
            return jsonify({"status": "error", "error": "hazard-detail-unavailable"}), 503
        payload = dependencies.natural_hazard_event_detail(event_id=event_id)
        if payload is None:
            return jsonify({
                "status": "error",
                "error": "hazard-event-not-found",
                "eventId": event_id,
            }), 404
        return _public_conditional_json(
            payload,
            "public, max-age=60, stale-while-revalidate=300, stale-if-error=86400",
        )

    @bp.route("/v1/runtime/panels", methods=["GET"])
    def api_runtime_panels_batch_v1():
        panel_ids = _requested_panel_ids()
        payloads, _, errors, panel_meta, status = _collect_runtime_panels(panel_ids)
        generated_at = dependencies.utc_now_iso()
        return jsonify(
            api_envelope(
                data={"panels": payloads},
                generated_at=generated_at,
                status=status,
                meta={
                    "requestedPanelIds": panel_ids,
                    "returnedPanelIds": list(payloads),
                    "panels": panel_meta,
                },
                errors=errors,
            )
        )

    @bp.route("/runtime/panels/<panel_id>", methods=["GET"])
    def api_runtime_panel_by_id(panel_id: str):
        panel = get_panel_by_id(panel_id)
        if panel is None:
            return jsonify({"error": "unknown-panel", "panelId": panel_id}), 404
        limit = panel.clamp_limit(request.args.get("limit"))
        payload = _get_panel_snapshot(panel, dependencies.panel_context, limit)
        _publish_runtime_panel(panel.panel_id, payload)
        return jsonify(payload)

    @bp.route("/v1/runtime/panels/<panel_id>", methods=["GET"])
    def api_runtime_panel_by_id_v1(panel_id: str):
        panel = get_panel_by_id(panel_id)
        generated_at = dependencies.utc_now_iso()
        if panel is None:
            return jsonify(
                api_envelope(
                    data=None,
                    generated_at=generated_at,
                    status="error",
                    meta={"panelId": panel_id},
                    errors=[
                        api_error(
                            "unknown-panel",
                            f"Runtime panel '{panel_id}' is not registered.",
                            panel_id=panel_id,
                        )
                    ],
                )
            ), 404
        limit = panel.clamp_limit(request.args.get("limit"))
        try:
            payload = _get_panel_snapshot(panel, dependencies.panel_context, limit)
            _publish_runtime_panel(panel.panel_id, payload)
        except Exception:
            current_app.logger.exception("runtime-panel failed panel_id=%s", panel_id)
            return jsonify(
                api_envelope(
                    data=None,
                    generated_at=generated_at,
                    status="error",
                    meta={"panelId": panel_id},
                    errors=[
                        api_error(
                            "panel-fetch-failed",
                            f"Runtime panel '{panel_id}' could not be refreshed.",
                            panel_id=panel_id,
                            retryable=True,
                        )
                    ],
                )
            ), 500
        return jsonify(
            api_envelope(
                data=payload,
                generated_at=generated_at,
                meta={"panel": runtime_panel_metadata(panel, payload)},
            )
        )

    for panel in RUNTIME_PANEL_MODULES:
        endpoint = f"api_runtime_panel_{panel.panel_id.replace('-', '_')}"

        def _handler(panel=panel):
            limit = panel.clamp_limit(request.args.get("limit"))
            payload = _get_panel_snapshot(panel, dependencies.panel_context, limit)
            _publish_runtime_panel(panel.panel_id, payload)
            return jsonify(payload)

        bp.add_url_rule(panel.route, endpoint, _handler, methods=["GET"])

    return bp
