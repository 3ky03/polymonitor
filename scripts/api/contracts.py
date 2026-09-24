from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from flask import g, has_request_context, request

from api.runtime_panels.types import RuntimePanelModule


API_VERSION = "v1"
OPENAPI_VERSION = "3.1.0"
OPENAPI_DOCUMENT_VERSION = "1.1.0"
ENVELOPE_STATUSES = frozenset({"ok", "partial", "error"})


def current_request_id() -> str:
    if not has_request_context():
        return "-"
    existing = str(getattr(g, "request_id", "") or "").strip()
    if existing:
        return existing
    supplied = str(request.headers.get("X-Request-Id") or "").strip()
    request_id = supplied if supplied and len(supplied) <= 128 else uuid.uuid4().hex
    g.request_id = request_id
    return request_id


def api_error(
    code: str,
    message: str,
    *,
    panel_id: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": str(code),
        "message": str(message),
        "retryable": bool(retryable),
    }
    if panel_id:
        error["panelId"] = str(panel_id)
    return error


def api_envelope(
    *,
    data: Any,
    generated_at: str,
    status: str = "ok",
    meta: Mapping[str, Any] | None = None,
    errors: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_status = status if status in ENVELOPE_STATUSES else "error"
    return {
        "apiVersion": API_VERSION,
        "requestId": current_request_id(),
        "generatedAt": str(generated_at),
        "status": normalized_status,
        "data": data,
        "meta": dict(meta or {}),
        "errors": [dict(error) for error in (errors or ())],
    }


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _payload_observed_at(payload: Mapping[str, Any]) -> str | None:
    for key in ("generatedAt", "updatedAt", "sampledAt", "asOf", "timestamp"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return None


def _payload_age_seconds(payload: Mapping[str, Any], observed_at: str | None) -> int | None:
    raw_age = payload.get("ageSeconds")
    if isinstance(raw_age, (int, float)) and raw_age >= 0:
        return round(raw_age)
    observed = _parse_timestamp(observed_at)
    if observed is None:
        return None
    return max(0, round((datetime.now(timezone.utc) - observed).total_seconds()))


def _freshness_state(payload: Mapping[str, Any], cache_mode: str, age_seconds: int | None) -> str:
    explicit = str(payload.get("freshness") or "").strip().lower()
    if explicit:
        return explicit
    payload_status = str(payload.get("status") or "").strip().lower()
    if "stale" in cache_mode:
        return "stale"
    if payload_status in {"error", "degraded", "partial", "unavailable"}:
        return "degraded"
    if cache_mode in {"live", "live-build", "network"}:
        return "live"
    if age_seconds is not None:
        return "observed"
    return "unknown"


def runtime_panel_metadata(
    panel: RuntimePanelModule,
    payload: Any,
) -> dict[str, Any]:
    body = payload if isinstance(payload, Mapping) else {}
    observed_at = _payload_observed_at(body)
    cache_mode = str(body.get("cacheMode") or "unspecified").strip() or "unspecified"
    age_seconds = _payload_age_seconds(body, observed_at)
    return {
        "panelId": panel.panel_id,
        "route": panel.route,
        "status": str(body.get("status") or "ok"),
        "cache": {
            "mode": cache_mode,
            "ageSeconds": age_seconds,
        },
        "freshness": {
            "state": _freshness_state(body, cache_mode.lower(), age_seconds),
            "observedAt": observed_at,
            "ageSeconds": age_seconds,
        },
        "limits": {
            "default": panel.default_limit,
            "minimum": panel.min_limit,
            "maximum": panel.max_limit,
        },
    }


def build_openapi_document(panels: Sequence[RuntimePanelModule]) -> dict[str, Any]:
    panel_ids = [panel.panel_id for panel in panels]
    panel_manifest = [
        {
            "panelId": panel.panel_id,
            "route": panel.route,
            "limits": {
                "default": panel.default_limit,
                "minimum": panel.min_limit,
                "maximum": panel.max_limit,
            },
        }
        for panel in panels
    ]
    error_schema = {
        "type": "object",
        "required": ["code", "message", "retryable"],
        "properties": {
            "code": {"type": "string"},
            "message": {"type": "string"},
            "panelId": {"type": "string"},
            "retryable": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    envelope_properties = {
        "apiVersion": {"type": "string", "const": API_VERSION},
        "requestId": {"type": "string"},
        "generatedAt": {"type": "string", "format": "date-time"},
        "status": {"type": "string", "enum": sorted(ENVELOPE_STATUSES)},
        "meta": {"type": "object", "additionalProperties": True},
        "errors": {"type": "array", "items": {"$ref": "#/components/schemas/ApiError"}},
    }
    panel_metadata_schema = {
        "type": "object",
        "required": ["panelId", "route", "status", "cache", "freshness", "limits"],
        "properties": {
            "panelId": {"type": "string", "enum": panel_ids},
            "route": {"type": "string"},
            "status": {"type": "string"},
            "cache": {
                "type": "object",
                "required": ["mode", "ageSeconds"],
                "properties": {
                    "mode": {"type": "string"},
                    "ageSeconds": {"type": ["integer", "null"], "minimum": 0},
                },
                "additionalProperties": False,
            },
            "freshness": {
                "type": "object",
                "required": ["state", "observedAt", "ageSeconds"],
                "properties": {
                    "state": {"type": "string"},
                    "observedAt": {"type": ["string", "null"], "format": "date-time"},
                    "ageSeconds": {"type": ["integer", "null"], "minimum": 0},
                },
                "additionalProperties": False,
            },
            "limits": {
                "type": "object",
                "required": ["default", "minimum", "maximum"],
                "properties": {
                    "default": {"type": ["integer", "null"]},
                    "minimum": {"type": ["integer", "null"]},
                    "maximum": {"type": ["integer", "null"]},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }
    json_response = {
        "200": {
            "description": "Successful read-only response",
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True}
                }
            },
        },
        "404": {
            "description": "Resource not found",
            "content": {
                "application/json": {
                    "schema": {"type": "object", "additionalProperties": True}
                }
            },
        },
    }
    market_id_parameter = {
        "name": "marketId",
        "in": "path",
        "required": True,
        "schema": {"type": "integer", "minimum": 1},
        "description": "Canonical local market ID.",
    }
    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "PolyMonitor Runtime API",
            "version": OPENAPI_DOCUMENT_VERSION,
            "description": (
                "Read-only contract for canonical PolyMonitor market, Oracle, data-quality, "
                "public briefing, MCP and panel-runtime surfaces. Private layouts, Watchlists, "
                "alerts, credentials and administrator routes are intentionally excluded."
            ),
        },
        "servers": [{"url": "/wm-api"}],
        "tags": [
            {
                "name": "Runtime Panels",
                "description": "Versioned batch and single-panel runtime payloads.",
            },
            {
                "name": "Prediction Markets",
                "description": "Canonical market identity, evidence workspace and Oracle lifecycle.",
            },
            {
                "name": "Data Quality",
                "description": "Public market and Oracle quality contract.",
            },
            {
                "name": "Public Distribution",
                "description": "Revocable public briefings and bounded MCP transport.",
            },
            {
                "name": "Schema",
                "description": "Machine-readable API contract.",
            },
        ],
        "paths": {
            "/openapi.json": {
                "get": {
                    "tags": ["Schema"],
                    "operationId": "getOpenApiDocument",
                    "responses": {
                        "200": {
                            "description": "OpenAPI 3.1 document",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            },
                        }
                    },
                }
            },
            "/markets": {
                "get": {
                    "tags": ["Prediction Markets"],
                    "operationId": "searchMarkets",
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "maxLength": 240},
                            "description": "Title or market identifier search text.",
                        },
                        {
                            "name": "status",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "default": "active"},
                        },
                        {
                            "name": "page",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 1, "default": 1},
                        },
                        {
                            "name": "pageSize",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 500, "default": 20},
                        },
                    ],
                    "responses": json_response,
                }
            },
            "/markets/{marketId}": {
                "get": {
                    "tags": ["Prediction Markets"],
                    "operationId": "getMarket",
                    "parameters": [market_id_parameter],
                    "responses": json_response,
                }
            },
            "/markets/{marketId}/workspace": {
                "get": {
                    "tags": ["Prediction Markets"],
                    "operationId": "getMarketWorkspace",
                    "parameters": [market_id_parameter],
                    "responses": json_response,
                }
            },
            "/markets/{marketId}/oracle": {
                "get": {
                    "tags": ["Prediction Markets"],
                    "operationId": "getMarketOracleLifecycle",
                    "parameters": [market_id_parameter],
                    "responses": json_response,
                }
            },
            "/data-quality/markets": {
                "get": {
                    "tags": ["Data Quality"],
                    "operationId": "getPredictionMarketDataQuality",
                    "responses": json_response,
                }
            },
            "/briefings/{publicId}": {
                "get": {
                    "tags": ["Public Distribution"],
                    "operationId": "getPublicBriefing",
                    "parameters": [
                        {
                            "name": "publicId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]{32}$"},
                            "description": "Revocable public capability identifier.",
                        }
                    ],
                    "responses": json_response,
                }
            },
            "/mcp": {
                "post": {
                    "tags": ["Public Distribution"],
                    "operationId": "callPredictionMarketMcp",
                    "description": (
                        "Stateless Streamable HTTP JSON-RPC endpoint. Discovery is public; "
                        "tools/call requires an mcp:read Bearer API key."
                    ),
                    "security": [{"McpApiKey": []}],
                    "parameters": [
                        {
                            "name": "MCP-Protocol-Version",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "const": "2025-06-18"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "additionalProperties": True}
                            }
                        },
                    },
                    "responses": {
                        **json_response,
                        "401": {
                            "description": "Missing, expired, revoked or incorrectly scoped API key"
                        },
                        "403": {"description": "Origin is not allowed"},
                    },
                }
            },
            "/v1/runtime/panels": {
                "get": {
                    "tags": ["Runtime Panels"],
                    "operationId": "getRuntimePanels",
                    "parameters": [
                        {
                            "name": "ids",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                            "description": "Comma-separated panel IDs. Omit to request the registry.",
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 1},
                            "description": (
                                "Shared panel limit. A per-panel limit may be supplied as "
                                "limit.<panelId>."
                            ),
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Batch runtime envelope",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RuntimePanelsEnvelope"}
                                }
                            },
                        }
                    },
                }
            },
            "/v1/runtime/panels/{panelId}": {
                "get": {
                    "tags": ["Runtime Panels"],
                    "operationId": "getRuntimePanel",
                    "parameters": [
                        {
                            "name": "panelId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "enum": panel_ids},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 1},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Single-panel runtime envelope",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RuntimePanelEnvelope"}
                                }
                            },
                        },
                        "404": {
                            "description": "Unknown panel ID",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RuntimePanelEnvelope"}
                                }
                            },
                        },
                        "500": {
                            "description": "Panel refresh failed",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RuntimePanelEnvelope"}
                                }
                            },
                        },
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "McpApiKey": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "One-time-issued API key containing only the mcp:read scope.",
                }
            },
            "schemas": {
                "ApiError": error_schema,
                "PanelMetadata": panel_metadata_schema,
                "RuntimePanelsEnvelope": {
                    "type": "object",
                    "required": [
                        "apiVersion",
                        "requestId",
                        "generatedAt",
                        "status",
                        "data",
                        "meta",
                        "errors",
                    ],
                    "properties": {
                        **envelope_properties,
                        "data": {
                            "type": "object",
                            "required": ["panels"],
                            "properties": {
                                "panels": {"type": "object", "additionalProperties": True}
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
                "RuntimePanelEnvelope": {
                    "type": "object",
                    "required": [
                        "apiVersion",
                        "requestId",
                        "generatedAt",
                        "status",
                        "data",
                        "meta",
                        "errors",
                    ],
                    "properties": {
                        **envelope_properties,
                        "data": {
                            "type": ["object", "array", "string", "number", "boolean", "null"]
                        },
                    },
                    "additionalProperties": False,
                },
            }
        },
        "x-runtime-panels": panel_manifest,
        "x-legacy-runtime-routes": {
            "batch": "/runtime/panels",
            "single": "/runtime/panels/{panelId}",
            "status": "compatibility",
        },
    }
