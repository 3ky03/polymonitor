"""Versioned server storage for the Atlas workspace layout."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from db import get_connection

from api.auth_schema import product_schema_is_ready
from api.services.auth_service import AuthError, Principal, audit_action


PANEL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
MAX_PANELS = 100
MAX_PAYLOAD_BYTES = 64 * 1024
REGIONS = frozenset({"global", "america", "mena", "eu", "asia", "latam", "africa", "oceania"})
VIEW_MODES = frozenset({"2d", "3d", "heatmap", "density"})
MARKET_GROUP_SORTS = frozenset({"active", "new", "volume", "close", "move", "trades"})


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _ensure_schema(conn: Any) -> None:
    if not product_schema_is_ready(conn):
        raise AuthError(503, "PRODUCT_SCHEMA_MISSING", "Workspace synchronization is not ready on this deployment.")


def _decode_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _serialize(row: Any) -> dict[str, Any]:
    if not row:
        return {
            "exists": False,
            "revision": 0,
            "activePanelIds": [],
            "panelLayout": {},
            "preferences": {},
            "clientUpdatedAt": None,
            "updatedAt": None,
        }
    return {
        "exists": True,
        "revision": int(row[0]),
        "activePanelIds": _decode_json(row[1], []),
        "panelLayout": _decode_json(row[2], {}),
        "preferences": _decode_json(row[3], {}),
        "clientUpdatedAt": _iso(row[4]),
        "updatedAt": _iso(row[5]),
    }


def get_workspace_layout(principal: Principal) -> dict[str, Any]:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT revision, active_panel_ids, panel_layout, preferences, client_updated_at, updated_at
            FROM product.workspace_layouts
            WHERE user_id = ?
            """,
            (principal.user_id,),
        ).fetchone()
        return _serialize(row)
    finally:
        conn.close()


def _normalized_payload(payload: Mapping[str, Any]) -> tuple[list[str], dict[str, dict[str, int]], dict[str, Any], str | None]:
    raw_panels = payload.get("activePanelIds")
    if not isinstance(raw_panels, list):
        raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "activePanelIds must be an array.")
    panels: list[str] = []
    seen: set[str] = set()
    for raw_panel in raw_panels:
        panel = str(raw_panel or "").strip()
        if not PANEL_ID_PATTERN.fullmatch(panel):
            raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "Workspace panel IDs must use lowercase letters, numbers, and hyphens.")
        if panel not in seen:
            seen.add(panel)
            panels.append(panel)
    if len(panels) > MAX_PANELS:
        raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", f"A workspace can contain at most {MAX_PANELS} panels.")

    raw_layout = payload.get("panelLayout")
    if not isinstance(raw_layout, dict):
        raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "panelLayout must be an object.")
    panel_layout: dict[str, dict[str, int]] = {}
    for raw_panel, raw_size in raw_layout.items():
        panel = str(raw_panel or "").strip()
        if not PANEL_ID_PATTERN.fullmatch(panel) or not isinstance(raw_size, dict):
            raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "Panel sizes must be keyed by valid panel IDs.")
        size: dict[str, int] = {}
        for key, minimum, maximum in (("rowSpan", 1, 4), ("colSpan", 1, 3)):
            if key not in raw_size:
                continue
            try:
                value = int(raw_size[key])
            except (TypeError, ValueError) as exc:
                raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", f"{key} must be an integer.") from exc
            if value < minimum or value > maximum:
                raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", f"{key} is outside the supported grid range.")
            size[key] = value
        if size:
            panel_layout[panel] = size
    if len(panel_layout) > MAX_PANELS:
        raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "The workspace contains too many panel size overrides.")

    raw_preferences = payload.get("preferences")
    if not isinstance(raw_preferences, dict):
        raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "preferences must be an object.")
    region = str(raw_preferences.get("region") or "global")
    view_mode = str(raw_preferences.get("viewMode") or "2d")
    if region not in REGIONS or view_mode not in VIEW_MODES:
        raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "The workspace region or map mode is not supported.")
    try:
        map_zoom = max(1, min(4, int(raw_preferences.get("mapZoom") or 1)))
    except (TypeError, ValueError) as exc:
        raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "mapZoom must be an integer.") from exc
    market_group_sort = str(raw_preferences.get("marketGroupSort") or "active")
    if market_group_sort not in MARKET_GROUP_SORTS:
        raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "The market group sort is not supported.")
    raw_panel_library = raw_preferences.get("showPanelLibrary", True)
    if not isinstance(raw_panel_library, bool):
        raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "showPanelLibrary must be a boolean.")
    preferences = {
        "region": region,
        "viewMode": view_mode,
        "mapZoom": map_zoom,
        "showPanelLibrary": raw_panel_library,
        "marketGroupSort": market_group_sort,
    }

    client_updated_at = payload.get("clientUpdatedAt")
    normalized_client_time: str | None = None
    if client_updated_at not in (None, ""):
        try:
            normalized_client_time = datetime.fromisoformat(str(client_updated_at).replace("Z", "+00:00")).isoformat()
        except ValueError as exc:
            raise AuthError(400, "INVALID_WORKSPACE_LAYOUT", "clientUpdatedAt must be an ISO timestamp.") from exc

    encoded_size = len(json.dumps([panels, panel_layout, preferences], separators=(",", ":")).encode("utf-8"))
    if encoded_size > MAX_PAYLOAD_BYTES:
        raise AuthError(413, "WORKSPACE_LAYOUT_TOO_LARGE", "The workspace layout exceeds the 64 KiB limit.")
    return panels, panel_layout, preferences, normalized_client_time


def put_workspace_layout(
    principal: Principal,
    payload: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    panels, panel_layout, preferences, client_updated_at = _normalized_payload(payload)
    try:
        expected_revision = int(payload.get("revision") or 0)
    except (TypeError, ValueError) as exc:
        raise AuthError(400, "INVALID_WORKSPACE_REVISION", "revision must be an integer.") from exc
    if expected_revision < 0:
        raise AuthError(400, "INVALID_WORKSPACE_REVISION", "revision cannot be negative.")

    conn = get_connection()
    try:
        _ensure_schema(conn)
        current = conn.execute(
            "SELECT revision FROM product.workspace_layouts WHERE user_id = ? FOR UPDATE",
            (principal.user_id,),
        ).fetchone()
        current_revision = int(current[0]) if current else 0
        if current_revision != expected_revision:
            raise AuthError(409, "WORKSPACE_LAYOUT_CONFLICT", "The server layout changed on another device. Refresh before saving.")
        next_revision = current_revision + 1
        row = conn.execute(
            """
            INSERT INTO product.workspace_layouts (
                user_id, revision, active_panel_ids, panel_layout, preferences, client_updated_at
            ) VALUES (?, ?, ?::jsonb, ?::jsonb, ?::jsonb, ?::timestamptz)
            ON CONFLICT (user_id)
            DO UPDATE SET
                revision = EXCLUDED.revision,
                active_panel_ids = EXCLUDED.active_panel_ids,
                panel_layout = EXCLUDED.panel_layout,
                preferences = EXCLUDED.preferences,
                client_updated_at = EXCLUDED.client_updated_at,
                updated_at = NOW()
            RETURNING revision, active_panel_ids, panel_layout, preferences, client_updated_at, updated_at
            """,
            (
                principal.user_id,
                next_revision,
                json.dumps(panels, separators=(",", ":")),
                json.dumps(panel_layout, separators=(",", ":"), sort_keys=True),
                json.dumps(preferences, separators=(",", ":"), sort_keys=True),
                client_updated_at,
            ),
        ).fetchone()
        audit_action(
            conn,
            principal,
            action="workspace_layout.update",
            result="success",
            metadata=metadata,
            target_type="workspace_layout",
            target_id=str(principal.user_id),
            details={"revision": next_revision, "panelCount": len(panels)},
        )
        conn.commit()
        return _serialize(row)
    except AuthError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
