"""Canonical prediction-market briefing snapshots with revocable public links."""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from db import get_connection

from api.auth_schema import product_schema_is_ready
from api.services.auth_service import AuthError, Principal, audit_action


PUBLIC_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32}$")
MAX_ACTIVE_BRIEFINGS = 20
BRIEFING_TTL_DAYS = 30


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _json_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _ensure_schema(conn: Any) -> None:
    if not product_schema_is_ready(conn):
        raise AuthError(503, "PRODUCT_SCHEMA_MISSING", "Briefing storage is not ready on this deployment.")


def _market_item(row: Any) -> dict[str, Any]:
    current = float(row[3]) if row[3] is not None else None
    previous = float(row[4]) if row[4] is not None else None
    return {
        "marketId": int(row[0]),
        "title": row[1],
        "category": row[2],
        "latestPrice": current,
        "change24h": current - previous if current is not None and previous is not None else None,
        "volume24h": float(row[5] or 0),
        "tradeCount24h": int(row[6] or 0),
        "completionStatus": row[7],
        "oracleStage": (
            "resolved" if row[12] or row[11]
            else "disputed" if row[10]
            else "proposed" if row[9]
            else "awaiting-oracle" if str(row[7] or "").upper() == "ENDED_AWAITING_ORACLE"
            else "open"
        ),
        "observedAt": _iso(max((value for value in (row[8], row[13]) if value is not None), default=None)),
    }


def _build_snapshot(conn: Any, user_id: int) -> dict[str, Any]:
    tracked_rows = conn.execute(
        """
        SELECT m.id, COALESCE(NULLIF(m.title, ''), wm.market_title), m.category,
               mls.latest_price, mls.price_24h_ago, mls.volume_24h, mls.trade_count_24h,
               COALESCE(mss.completion_status, 'OPEN'), mls.updated_at,
               COALESCE(mss.has_propose, FALSE), COALESCE(mss.has_dispute, FALSE),
               COALESCE(mss.has_settle, FALSE), COALESCE(mss.is_final, FALSE), mss.updated_at
        FROM product.watchlist_markets wm
        JOIN product.watchlists w ON w.id = wm.watchlist_id
        LEFT JOIN core.markets m ON m.id = wm.market_id
        LEFT JOIN core.market_list_serving mls ON mls.market_id = wm.market_id
        LEFT JOIN core.market_status_snapshot mss ON mss.market_id = wm.market_id
        WHERE w.user_id = ?
        ORDER BY wm.added_at DESC
        LIMIT 12
        """,
        (user_id,),
    ).fetchall()
    top_rows = conn.execute(
        """
        SELECT m.id, m.title, m.category, mls.latest_price, mls.price_24h_ago,
               mls.volume_24h, mls.trade_count_24h, COALESCE(mss.completion_status, 'OPEN'),
               mls.updated_at, COALESCE(mss.has_propose, FALSE), COALESCE(mss.has_dispute, FALSE),
               COALESCE(mss.has_settle, FALSE), COALESCE(mss.is_final, FALSE), mss.updated_at
        FROM core.market_list_serving mls
        JOIN core.markets m ON m.id = mls.market_id
        LEFT JOIN core.market_status_snapshot mss ON mss.market_id = m.id
        WHERE COALESCE(mss.is_trading_closed, FALSE) = FALSE
          AND COALESCE(mls.volume_24h, 0) > 0
        ORDER BY mls.volume_24h DESC, mls.trade_count_24h DESC
        LIMIT 8
        """
    ).fetchall()
    oracle_rows = conn.execute(
        """
        SELECT m.id, m.title, m.category, mls.latest_price, mls.price_24h_ago,
               mls.volume_24h, mls.trade_count_24h, COALESCE(mss.completion_status, 'OPEN'),
               mls.updated_at, COALESCE(mss.has_propose, FALSE), COALESCE(mss.has_dispute, FALSE),
               COALESCE(mss.has_settle, FALSE), COALESCE(mss.is_final, FALSE), mss.updated_at
        FROM core.market_status_snapshot mss
        JOIN core.markets m ON m.id = mss.market_id
        LEFT JOIN core.market_list_serving mls ON mls.market_id = m.id
        WHERE COALESCE(mss.is_final, FALSE) = FALSE
          AND (
            COALESCE(mss.has_dispute, FALSE) = TRUE
            OR COALESCE(mss.completion_status, '') = 'ENDED_AWAITING_ORACLE'
          )
        ORDER BY COALESCE(mss.has_dispute, FALSE) DESC, mss.updated_at DESC
        LIMIT 8
        """
    ).fetchall()
    layout = conn.execute(
        "SELECT revision, active_panel_ids, updated_at FROM product.workspace_layouts WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return {
        "schema": "prediction-market-briefing.v1",
        "generatedAt": _iso(datetime.now(timezone.utc)),
        "source": {
            "kind": "canonical-snapshot",
            "markets": "core.markets + core.market_list_serving",
            "oracle": "core.market_status_snapshot",
            "warning": "Probabilities and lifecycle states are point-in-time observations, not trading advice.",
        },
        "summary": {
            "trackedMarkets": len(tracked_rows),
            "topMarkets": len(top_rows),
            "oracleAttention": len(oracle_rows),
        },
        "trackedMarkets": [_market_item(row) for row in tracked_rows],
        "topMarkets": [_market_item(row) for row in top_rows],
        "oracleAttention": [_market_item(row) for row in oracle_rows],
        "workspaceLens": {
            "revision": int(layout[0]) if layout else 0,
            "activePanelIds": _json_value(layout[1], []) if layout else [],
            "updatedAt": _iso(layout[2]) if layout else None,
        },
    }


def _serialize_registry(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "publicId": row[1],
        "title": row[2],
        "createdAt": _iso(row[3]),
        "expiresAt": _iso(row[4]),
        "revokedAt": _iso(row[5]),
        "active": row[5] is None and bool(row[6]),
    }


def list_briefings(principal: Principal) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT id, public_id, title, created_at, expires_at, revoked_at, expires_at > NOW()
            FROM product.briefings
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 100
            """,
            (principal.user_id,),
        ).fetchall()
        return [_serialize_registry(row) for row in rows]
    finally:
        conn.close()


def create_briefing(principal: Principal, title: Any, metadata: Mapping[str, Any]) -> dict[str, Any]:
    clean_title = str(title or "").strip()
    if not clean_title:
        clean_title = f"Prediction Market Brief · {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    if len(clean_title) > 120:
        raise AuthError(400, "INVALID_BRIEFING_TITLE", "Briefing titles are limited to 120 characters.")
    conn = get_connection()
    try:
        _ensure_schema(conn)
        active_count = conn.execute(
            "SELECT COUNT(*) FROM product.briefings WHERE user_id = ? AND revoked_at IS NULL AND expires_at > NOW()",
            (principal.user_id,),
        ).fetchone()[0]
        if int(active_count) >= MAX_ACTIVE_BRIEFINGS:
            raise AuthError(409, "BRIEFING_LIMIT_REACHED", f"Revoke an active briefing before creating more than {MAX_ACTIVE_BRIEFINGS}.")
        snapshot = _build_snapshot(conn, principal.user_id)
        briefing_id = str(uuid.uuid4())
        public_id = secrets.token_urlsafe(24)
        row = conn.execute(
            """
            INSERT INTO product.briefings (id, user_id, public_id, title, snapshot, expires_at)
            VALUES (?, ?, ?, ?, ?::jsonb, NOW() + INTERVAL '30 days')
            RETURNING id, public_id, title, created_at, expires_at, revoked_at, TRUE
            """,
            (
                briefing_id,
                principal.user_id,
                public_id,
                clean_title,
                json.dumps(snapshot, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
            ),
        ).fetchone()
        audit_action(
            conn,
            principal,
            action="briefing.create",
            result="success",
            metadata=metadata,
            target_type="briefing",
            target_id=briefing_id,
            details={"publicIdPrefix": public_id[:6], "expiresInDays": BRIEFING_TTL_DAYS},
        )
        conn.commit()
        return _serialize_registry(row)
    except AuthError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_briefing(principal: Principal, briefing_id: str, metadata: Mapping[str, Any]) -> None:
    try:
        normalized_id = str(uuid.UUID(briefing_id))
    except (TypeError, ValueError) as exc:
        raise AuthError(404, "BRIEFING_NOT_FOUND", "The briefing was not found.") from exc
    conn = get_connection()
    try:
        _ensure_schema(conn)
        cursor = conn.execute(
            "UPDATE product.briefings SET revoked_at = COALESCE(revoked_at, NOW()) WHERE id = ? AND user_id = ?",
            (normalized_id, principal.user_id),
        )
        if cursor.rowcount < 1:
            raise AuthError(404, "BRIEFING_NOT_FOUND", "The briefing was not found.")
        audit_action(
            conn,
            principal,
            action="briefing.revoke",
            result="success",
            metadata=metadata,
            target_type="briefing",
            target_id=normalized_id,
        )
        conn.commit()
    except AuthError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_public_briefing(public_id: str) -> dict[str, Any]:
    if not PUBLIC_ID_PATTERN.fullmatch(str(public_id or "")):
        raise AuthError(404, "BRIEFING_NOT_FOUND", "The briefing was not found or is no longer available.")
    conn = get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT title, snapshot, created_at, expires_at
            FROM product.briefings
            WHERE public_id = ? AND revoked_at IS NULL AND expires_at > NOW()
            """,
            (public_id,),
        ).fetchone()
        if not row:
            raise AuthError(404, "BRIEFING_NOT_FOUND", "The briefing was not found or is no longer available.")
        return {
            "title": row[0],
            "snapshot": _json_value(row[1], {}),
            "createdAt": _iso(row[2]),
            "expiresAt": _iso(row[3]),
        }
    finally:
        conn.close()
