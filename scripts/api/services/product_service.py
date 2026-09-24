"""User watchlists, prediction-market alert rules, and in-app notifications."""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from db import get_connection

from api.auth_schema import product_schema_is_ready
from api.services.auth_service import AuthError, Principal, audit_action
from api.services.web_push_service import configured as web_push_configured
from api.services.web_push_service import queue_alert_deliveries, validate_timezone


ALERT_KINDS = frozenset(
    {
        "price_above",
        "price_below",
        "oracle_gap",
        "oracle_proposed",
        "oracle_disputed",
        "oracle_resolved",
        "market_closed",
    }
)
PRICE_ALERT_KINDS = frozenset({"price_above", "price_below"})
DEFAULT_ALERT_KINDS = ("oracle_gap", "oracle_disputed")
WATCHLIST_LIMIT = 200
RULE_LIMIT = 500
TIMEZONE_PATTERN = re.compile(r"^[A-Za-z0-9_+./-]{1,64}$")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _ensure_schema(conn: Any) -> None:
    if not product_schema_is_ready(conn):
        raise AuthError(503, "PRODUCT_SCHEMA_MISSING", "Watchlist storage is not ready on this deployment.")


def _default_watchlist(conn: Any, user_id: int, *, create: bool) -> str | None:
    row = conn.execute(
        "SELECT id FROM product.watchlists WHERE user_id = ? AND is_default = TRUE LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return str(row[0])
    if not create:
        return None
    watchlist_id = str(uuid.uuid4())
    row = conn.execute(
        """
        INSERT INTO product.watchlists (id, user_id, name, is_default)
        VALUES (?, ?, 'Primary Watchlist', TRUE)
        ON CONFLICT (user_id) WHERE is_default = TRUE
        DO UPDATE SET updated_at = NOW()
        RETURNING id
        """,
        (watchlist_id, user_id),
    ).fetchone()
    return str(row[0])


def _market_row(conn: Any, market_id: int) -> Any:
    return conn.execute(
        """
        SELECT
            m.id,
            m.title,
            m.slug,
            m.category,
            m.end_date,
            m.event_id,
            mls.latest_price,
            mls.price_24h_ago,
            mls.volume_24h,
            mls.trade_count_24h,
            mls.updated_at AS price_updated_at,
            COALESCE(mss.completion_status, 'OPEN') AS completion_status,
            COALESCE(mss.has_propose, FALSE) AS has_propose,
            COALESCE(mss.has_dispute, FALSE) AS has_dispute,
            COALESCE(mss.has_settle, FALSE) AS has_settle,
            COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
            COALESCE(mss.is_final, FALSE) AS is_final,
            mss.updated_at AS status_updated_at
        FROM core.markets m
        LEFT JOIN core.market_list_serving mls ON mls.market_id = m.id
        LEFT JOIN core.market_status_snapshot mss ON mss.market_id = m.id
        WHERE m.id = ?
        LIMIT 1
        """,
        (market_id,),
    ).fetchone()


def _oracle_stage(row: Any) -> str:
    if row["is_final"] or row["has_settle"]:
        return "resolved"
    if row["has_dispute"]:
        return "disputed"
    if row["has_propose"]:
        return "proposed"
    if str(row["completion_status"] or "").upper() == "ENDED_AWAITING_ORACLE":
        return "awaiting-oracle"
    return "open"


def _serialize_rule(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "marketId": int(row[1]),
        "kind": str(row[2]),
        "threshold": float(row[3]) if row[3] is not None else None,
        "enabled": bool(row[4]),
        "cooldownSeconds": int(row[5]),
        "conditionActive": bool(row[6]),
        "lastTriggeredAt": _iso(row[7]),
        "createdAt": _iso(row[8]),
    }


def get_watchlist(principal: Principal) -> dict[str, Any]:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        watchlist_id = _default_watchlist(conn, principal.user_id, create=False)
        if not watchlist_id:
            return {
                "id": None,
                "name": "Primary Watchlist",
                "items": [],
                "summary": {"markets": 0, "activeRules": 0, "oracleGaps": 0, "unreadAlerts": 0},
                "alertKinds": sorted(ALERT_KINDS),
            }
        rows = conn.execute(
            """
            SELECT
                wm.market_id,
                COALESCE(NULLIF(m.title, ''), wm.market_title) AS market_title,
                COALESCE(NULLIF(m.slug, ''), wm.market_slug) AS market_slug,
                m.category,
                m.end_date,
                wm.note,
                wm.added_at,
                mls.latest_price,
                mls.price_24h_ago,
                mls.volume_24h,
                mls.trade_count_24h,
                mls.updated_at AS price_updated_at,
                COALESCE(mss.completion_status, 'OPEN') AS completion_status,
                COALESCE(mss.has_propose, FALSE) AS has_propose,
                COALESCE(mss.has_dispute, FALSE) AS has_dispute,
                COALESCE(mss.has_settle, FALSE) AS has_settle,
                COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
                COALESCE(mss.is_final, FALSE) AS is_final,
                mss.updated_at AS status_updated_at
            FROM product.watchlist_markets wm
            LEFT JOIN core.markets m ON m.id = wm.market_id
            LEFT JOIN core.market_list_serving mls ON mls.market_id = wm.market_id
            LEFT JOIN core.market_status_snapshot mss ON mss.market_id = wm.market_id
            WHERE wm.watchlist_id = ?
            ORDER BY wm.added_at DESC
            """,
            (watchlist_id,),
        ).fetchall()
        rule_rows = conn.execute(
            """
            SELECT id, market_id, kind, threshold, enabled, cooldown_seconds,
                   is_condition_active, last_triggered_at, created_at
            FROM product.alert_rules
            WHERE user_id = ? AND watchlist_id = ?
            ORDER BY created_at ASC
            """,
            (principal.user_id, watchlist_id),
        ).fetchall()
        rules_by_market: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
        for rule in rule_rows:
            rules_by_market[int(rule[1])].append(_serialize_rule(rule))
        items = []
        for row in rows:
            current = row[7]
            previous = row[8]
            change = None
            if current is not None and previous is not None:
                try:
                    change = float(Decimal(str(current)) - Decimal(str(previous)))
                except (InvalidOperation, TypeError, ValueError):
                    change = None
            items.append(
                {
                    "marketId": int(row[0]),
                    "title": row[1],
                    "slug": row[2],
                    "category": row[3],
                    "endDate": _iso(row[4]),
                    "note": row[5],
                    "addedAt": _iso(row[6]),
                    "latestPrice": float(current) if current is not None else None,
                    "price24hAgo": float(previous) if previous is not None else None,
                    "change24h": change,
                    "volume24h": float(row[9] or 0),
                    "tradeCount24h": int(row[10] or 0),
                    "priceUpdatedAt": _iso(row[11]),
                    "completionStatus": row[12],
                    "oracleStage": _oracle_stage(row),
                    "statusUpdatedAt": _iso(row[18]),
                    "rules": rules_by_market[int(row[0])],
                }
            )
        unread = conn.execute(
            "SELECT COUNT(*) FROM product.alert_events WHERE user_id = ? AND read_at IS NULL",
            (principal.user_id,),
        ).fetchone()[0]
        return {
            "id": watchlist_id,
            "name": "Primary Watchlist",
            "items": items,
            "summary": {
                "markets": len(items),
                "activeRules": sum(1 for rule in rule_rows if rule[4]),
                "oracleGaps": sum(1 for item in items if item["oracleStage"] == "awaiting-oracle"),
                "unreadAlerts": int(unread),
            },
            "alertKinds": sorted(ALERT_KINDS),
        }
    finally:
        conn.close()


def add_watchlist_market(
    principal: Principal,
    market_id: Any,
    note: Any,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        normalized_market_id = int(market_id)
    except (TypeError, ValueError) as exc:
        raise AuthError(400, "INVALID_MARKET_ID", "A numeric local market ID is required.") from exc
    clean_note = str(note or "").strip()
    if len(clean_note) > 500:
        raise AuthError(400, "INVALID_NOTE", "Watchlist notes are limited to 500 characters.")
    conn = get_connection()
    try:
        _ensure_schema(conn)
        market = _market_row(conn, normalized_market_id)
        if not market:
            raise AuthError(404, "MARKET_NOT_FOUND", "The local market was not found.")
        watchlist_id = _default_watchlist(conn, principal.user_id, create=True)
        count = conn.execute(
            "SELECT COUNT(*) FROM product.watchlist_markets WHERE watchlist_id = ?",
            (watchlist_id,),
        ).fetchone()[0]
        existing = conn.execute(
            "SELECT 1 FROM product.watchlist_markets WHERE watchlist_id = ? AND market_id = ?",
            (watchlist_id, normalized_market_id),
        ).fetchone()
        if not existing and int(count) >= WATCHLIST_LIMIT:
            raise AuthError(409, "WATCHLIST_LIMIT_REACHED", f"A watchlist can contain at most {WATCHLIST_LIMIT} markets.")
        conn.execute(
            """
            INSERT INTO product.watchlist_markets (
                watchlist_id, market_id, market_title, market_slug, note
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (watchlist_id, market_id)
            DO UPDATE SET note = EXCLUDED.note
            """,
            (watchlist_id, normalized_market_id, str(market[1] or f"Market {normalized_market_id}"), market[2], clean_note or None),
        )
        for kind in DEFAULT_ALERT_KINDS:
            conn.execute(
                """
                INSERT INTO product.alert_rules (
                    id, user_id, watchlist_id, market_id, kind, threshold
                ) VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT (
                    user_id, market_id, kind, (COALESCE(threshold, -1::numeric))
                ) DO UPDATE SET enabled = TRUE, updated_at = NOW()
                """,
                (str(uuid.uuid4()), principal.user_id, watchlist_id, normalized_market_id, kind),
            )
        audit_action(
            conn,
            principal,
            action="watchlist.market_upsert",
            result="success",
            metadata=metadata,
            target_type="market",
            target_id=str(normalized_market_id),
            details={"defaultRules": list(DEFAULT_ALERT_KINDS), "hasNote": bool(clean_note)},
        )
        conn.commit()
        return {"marketId": normalized_market_id, "watchlistId": watchlist_id, "created": not bool(existing)}
    except AuthError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remove_watchlist_market(principal: Principal, market_id: int, metadata: Mapping[str, Any]) -> None:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        watchlist_id = _default_watchlist(conn, principal.user_id, create=False)
        if not watchlist_id:
            raise AuthError(404, "WATCHLIST_ITEM_NOT_FOUND", "The market is not in your watchlist.")
        conn.execute(
            "DELETE FROM product.alert_rules WHERE user_id = ? AND watchlist_id = ? AND market_id = ?",
            (principal.user_id, watchlist_id, market_id),
        )
        cursor = conn.execute(
            "DELETE FROM product.watchlist_markets WHERE watchlist_id = ? AND market_id = ?",
            (watchlist_id, market_id),
        )
        if cursor.rowcount < 1:
            raise AuthError(404, "WATCHLIST_ITEM_NOT_FOUND", "The market is not in your watchlist.")
        audit_action(
            conn,
            principal,
            action="watchlist.market_remove",
            result="success",
            metadata=metadata,
            target_type="market",
            target_id=str(market_id),
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


def create_alert_rule(
    principal: Principal,
    *,
    market_id: Any,
    kind: Any,
    threshold: Any,
    cooldown_seconds: Any,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        normalized_market_id = int(market_id)
    except (TypeError, ValueError) as exc:
        raise AuthError(400, "INVALID_MARKET_ID", "A numeric local market ID is required.") from exc
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in ALERT_KINDS:
        raise AuthError(400, "INVALID_ALERT_KIND", "The requested alert kind is not supported.")
    normalized_threshold: Decimal | None = None
    if normalized_kind in PRICE_ALERT_KINDS:
        try:
            normalized_threshold = Decimal(str(threshold))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise AuthError(400, "INVALID_THRESHOLD", "Price alerts require a probability threshold.") from exc
        if normalized_threshold < Decimal("0.01") or normalized_threshold > Decimal("0.99"):
            raise AuthError(400, "INVALID_THRESHOLD", "Probability thresholds must be between 0.01 and 0.99.")
    try:
        normalized_cooldown = max(60, min(2_592_000, int(cooldown_seconds or 3600)))
    except (TypeError, ValueError) as exc:
        raise AuthError(400, "INVALID_COOLDOWN", "Cooldown must be an integer number of seconds.") from exc
    conn = get_connection()
    try:
        _ensure_schema(conn)
        watchlist_id = _default_watchlist(conn, principal.user_id, create=False)
        if not watchlist_id or not conn.execute(
            "SELECT 1 FROM product.watchlist_markets WHERE watchlist_id = ? AND market_id = ?",
            (watchlist_id, normalized_market_id),
        ).fetchone():
            raise AuthError(409, "MARKET_NOT_WATCHED", "Add the market to your watchlist before creating an alert.")
        count = conn.execute(
            "SELECT COUNT(*) FROM product.alert_rules WHERE user_id = ?",
            (principal.user_id,),
        ).fetchone()[0]
        if int(count) >= RULE_LIMIT:
            raise AuthError(409, "ALERT_RULE_LIMIT_REACHED", f"An account can contain at most {RULE_LIMIT} alert rules.")
        rule_id = str(uuid.uuid4())
        row = conn.execute(
            """
            INSERT INTO product.alert_rules (
                id, user_id, watchlist_id, market_id, kind, threshold, cooldown_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                user_id, market_id, kind, (COALESCE(threshold, -1::numeric))
            ) DO UPDATE SET
                enabled = TRUE,
                cooldown_seconds = EXCLUDED.cooldown_seconds,
                updated_at = NOW()
            RETURNING id, market_id, kind, threshold, enabled, cooldown_seconds,
                      is_condition_active, last_triggered_at, created_at
            """,
            (
                rule_id,
                principal.user_id,
                watchlist_id,
                normalized_market_id,
                normalized_kind,
                normalized_threshold,
                normalized_cooldown,
            ),
        ).fetchone()
        audit_action(
            conn,
            principal,
            action="alert_rule.upsert",
            result="success",
            metadata=metadata,
            target_type="alert_rule",
            target_id=str(row[0]),
            details={"kind": normalized_kind, "marketId": normalized_market_id},
        )
        conn.commit()
        return _serialize_rule(row)
    except AuthError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_alert_rule(principal: Principal, rule_id: str, metadata: Mapping[str, Any]) -> None:
    try:
        normalized_rule_id = str(uuid.UUID(rule_id))
    except (TypeError, ValueError) as exc:
        raise AuthError(404, "ALERT_RULE_NOT_FOUND", "The alert rule was not found.") from exc
    conn = get_connection()
    try:
        _ensure_schema(conn)
        cursor = conn.execute(
            "DELETE FROM product.alert_rules WHERE id = ? AND user_id = ?",
            (normalized_rule_id, principal.user_id),
        )
        if cursor.rowcount < 1:
            raise AuthError(404, "ALERT_RULE_NOT_FOUND", "The alert rule was not found.")
        audit_action(
            conn,
            principal,
            action="alert_rule.delete",
            result="success",
            metadata=metadata,
            target_type="alert_rule",
            target_id=normalized_rule_id,
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


def get_alert_events(principal: Principal, *, limit: int = 100, unread_only: bool = False) -> dict[str, Any]:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        where = "AND ae.read_at IS NULL" if unread_only else ""
        rows = conn.execute(
            f"""
            SELECT
                ae.id, ae.market_id, ae.kind, ae.severity, ae.title, ae.detail,
                ae.observed_price, ae.oracle_status, ae.source_observed_at,
                ae.occurred_at, ae.read_at, COALESCE(m.title, wm.market_title)
            FROM product.alert_events ae
            LEFT JOIN core.markets m ON m.id = ae.market_id
            LEFT JOIN product.watchlist_markets wm
              ON wm.market_id = ae.market_id
             AND wm.watchlist_id IN (
                 SELECT id FROM product.watchlists WHERE user_id = ae.user_id
             )
            WHERE ae.user_id = ?
              {where}
            ORDER BY ae.occurred_at DESC
            LIMIT ?
            """,
            (principal.user_id, max(1, min(250, int(limit)))),
        ).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) FROM product.alert_events WHERE user_id = ? AND read_at IS NULL",
            (principal.user_id,),
        ).fetchone()[0]
        return {
            "items": [
                {
                    "id": str(row[0]),
                    "marketId": int(row[1]),
                    "kind": row[2],
                    "severity": row[3],
                    "title": row[4],
                    "detail": row[5],
                    "observedPrice": float(row[6]) if row[6] is not None else None,
                    "oracleStatus": row[7],
                    "sourceObservedAt": _iso(row[8]),
                    "occurredAt": _iso(row[9]),
                    "readAt": _iso(row[10]),
                    "marketTitle": row[11],
                }
                for row in rows
            ],
            "unreadCount": int(unread),
        }
    finally:
        conn.close()


def mark_alert_read(principal: Principal, event_id: str, metadata: Mapping[str, Any]) -> None:
    try:
        normalized_event_id = str(uuid.UUID(event_id))
    except (TypeError, ValueError) as exc:
        raise AuthError(404, "ALERT_EVENT_NOT_FOUND", "The alert event was not found.") from exc
    conn = get_connection()
    try:
        _ensure_schema(conn)
        cursor = conn.execute(
            """
            UPDATE product.alert_events
            SET read_at = COALESCE(read_at, NOW())
            WHERE id = ? AND user_id = ?
            """,
            (normalized_event_id, principal.user_id),
        )
        if cursor.rowcount < 1:
            raise AuthError(404, "ALERT_EVENT_NOT_FOUND", "The alert event was not found.")
        audit_action(
            conn,
            principal,
            action="alert_event.read",
            result="success",
            metadata=metadata,
            target_type="alert_event",
            target_id=normalized_event_id,
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


def mark_all_alerts_read(principal: Principal, metadata: Mapping[str, Any]) -> int:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        cursor = conn.execute(
            "UPDATE product.alert_events SET read_at = NOW() WHERE user_id = ? AND read_at IS NULL",
            (principal.user_id,),
        )
        count = max(0, int(cursor.rowcount))
        audit_action(
            conn,
            principal,
            action="alert_event.read_all",
            result="success",
            metadata=metadata,
            target_type="alert_event",
            details={"count": count},
        )
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_notification_preferences(principal: Principal) -> dict[str, Any]:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT
                in_app_enabled,
                web_push_enabled,
                digest_mode,
                quiet_start_minute,
                quiet_end_minute,
                timezone,
                updated_at,
                (
                    SELECT COUNT(*)
                    FROM product.web_push_subscriptions s
                    WHERE s.user_id = product.notification_preferences.user_id
                      AND s.revoked_at IS NULL
                ) AS web_push_subscription_count
            FROM product.notification_preferences
            WHERE user_id = ?
            """,
            (principal.user_id,),
        ).fetchone()
        subscription_count = int(row[7]) if row else 0
        return {
            "inAppEnabled": bool(row[0]) if row else True,
            "webPushEnabled": bool(row[1]) if row else False,
            "digestMode": str(row[2]) if row else "realtime",
            "quietStartMinute": int(row[3]) if row and row[3] is not None else None,
            "quietEndMinute": int(row[4]) if row and row[4] is not None else None,
            "timezone": str(row[5]) if row else "UTC",
            "updatedAt": _iso(row[6]) if row else None,
            "channels": {
                "inApp": {"available": True, "connected": True},
                "webPush": {
                    "available": web_push_configured(),
                    "connected": subscription_count > 0,
                    "detail": (
                        f"{subscription_count} active browser subscription"
                        f"{'' if subscription_count == 1 else 's'}."
                        if web_push_configured()
                        else "Web Push is not configured on this deployment."
                    ),
                },
                "telegram": {"available": False, "connected": False, "detail": "Managed by the existing Telegram runtime boundary."},
                "email": {"available": False, "connected": False, "detail": "Email delivery is not configured."},
            },
        }
    finally:
        conn.close()


def update_notification_preferences(
    principal: Principal,
    payload: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    digest_mode = str(payload.get("digestMode") or "realtime").strip().lower()
    if digest_mode not in {"realtime", "hourly", "daily", "off"}:
        raise AuthError(400, "INVALID_DIGEST_MODE", "Digest mode must be realtime, hourly, daily, or off.")
    timezone_name = str(payload.get("timezone") or "UTC").strip()
    if not TIMEZONE_PATTERN.fullmatch(timezone_name):
        raise AuthError(400, "INVALID_TIMEZONE", "Timezone must be a compact IANA timezone name.")
    validate_timezone(timezone_name)

    def _minute(name: str) -> int | None:
        value = payload.get(name)
        if value in (None, ""):
            return None
        try:
            minute = int(value)
        except (TypeError, ValueError) as exc:
            raise AuthError(400, "INVALID_QUIET_HOURS", "Quiet hours must use minute-of-day values.") from exc
        if minute < 0 or minute > 1439:
            raise AuthError(400, "INVALID_QUIET_HOURS", "Quiet hours must be between 0 and 1439.")
        return minute

    raw_in_app = payload.get("inAppEnabled", True)
    if not isinstance(raw_in_app, bool):
        raise AuthError(400, "INVALID_NOTIFICATION_PREFERENCE", "inAppEnabled must be a boolean.")
    raw_web_push = payload.get("webPushEnabled", False)
    if not isinstance(raw_web_push, bool):
        raise AuthError(400, "INVALID_NOTIFICATION_PREFERENCE", "webPushEnabled must be a boolean.")
    if raw_web_push and not web_push_configured():
        raise AuthError(503, "WEB_PUSH_UNAVAILABLE", "Web Push is not configured on this deployment.")
    quiet_start = _minute("quietStartMinute")
    quiet_end = _minute("quietEndMinute")
    conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO product.notification_preferences (
                user_id, in_app_enabled, web_push_enabled, digest_mode,
                quiet_start_minute, quiet_end_minute, timezone
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id)
            DO UPDATE SET
                in_app_enabled = EXCLUDED.in_app_enabled,
                web_push_enabled = EXCLUDED.web_push_enabled,
                digest_mode = EXCLUDED.digest_mode,
                quiet_start_minute = EXCLUDED.quiet_start_minute,
                quiet_end_minute = EXCLUDED.quiet_end_minute,
                timezone = EXCLUDED.timezone,
                updated_at = NOW()
            """,
            (
                principal.user_id,
                raw_in_app,
                raw_web_push,
                digest_mode,
                quiet_start,
                quiet_end,
                timezone_name,
            ),
        )
        audit_action(
            conn,
            principal,
            action="notification_preferences.update",
            result="success",
            metadata=metadata,
            target_type="user",
            target_id=str(principal.user_id),
            details={
                "digestMode": digest_mode,
                "inAppEnabled": raw_in_app,
                "webPushEnabled": raw_web_push,
            },
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
    return get_notification_preferences(principal)


def _condition(rule: Any) -> tuple[bool, str, str, str, Decimal | None, str | None, Any, dict[str, Any]]:
    kind = str(rule[4])
    threshold = Decimal(str(rule[5])) if rule[5] is not None else None
    price = Decimal(str(rule[14])) if rule[14] is not None else None
    completion = str(rule[17] or "OPEN")
    has_propose = bool(rule[18])
    has_dispute = bool(rule[19])
    has_settle = bool(rule[20])
    is_closed = bool(rule[21])
    is_final = bool(rule[22])
    if kind == "price_above":
        active = price is not None and threshold is not None and price >= threshold
        detail = f"YES probability reached {float(price or 0):.1%}, at or above the {float(threshold or 0):.1%} threshold."
        title = "Probability crossed above threshold"
        severity = "warning"
        source_at = rule[15]
    elif kind == "price_below":
        active = price is not None and threshold is not None and price <= threshold
        detail = f"YES probability reached {float(price or 0):.1%}, at or below the {float(threshold or 0):.1%} threshold."
        title = "Probability crossed below threshold"
        severity = "warning"
        source_at = rule[15]
    elif kind == "oracle_gap":
        active = completion.upper() == "ENDED_AWAITING_ORACLE"
        detail = "Trading has ended, but the canonical status still reports an unresolved Oracle gap."
        title = "Market is awaiting Oracle resolution"
        severity = "warning"
        source_at = rule[23]
    elif kind == "oracle_proposed":
        active = has_propose and not has_settle
        detail = "An Oracle proposal is now bound to this market and has not reached final settlement."
        title = "Oracle proposal observed"
        severity = "info"
        source_at = rule[23]
    elif kind == "oracle_disputed":
        active = has_dispute and not has_settle
        detail = "The market's Oracle lifecycle now contains a dispute without final settlement."
        title = "Oracle dispute observed"
        severity = "critical"
        source_at = rule[23]
    elif kind == "oracle_resolved":
        active = has_settle or is_final
        detail = "The canonical market snapshot now reports a final Oracle settlement."
        title = "Market resolution became final"
        severity = "positive"
        source_at = rule[23]
    else:
        active = is_closed
        detail = "The canonical market snapshot now reports trading as closed."
        title = "Market trading closed"
        severity = "info"
        source_at = rule[23]
    facts = {
        "completionStatus": completion,
        "hasPropose": has_propose,
        "hasDispute": has_dispute,
        "hasSettle": has_settle,
        "isTradingClosed": is_closed,
        "isFinal": is_final,
        "threshold": float(threshold) if threshold is not None else None,
    }
    return active, title, detail, severity, price, completion, source_at, facts


def evaluate_alert_rules() -> dict[str, int]:
    conn = get_connection()
    evaluated = triggered = rearmed = queued = 0
    try:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT
                r.id,
                r.user_id,
                r.watchlist_id,
                r.market_id,
                r.kind,
                r.threshold,
                r.cooldown_seconds,
                r.is_condition_active,
                r.last_triggered_at,
                COALESCE(NULLIF(m.title, ''), wm.market_title) AS market_title,
                COALESCE(NULLIF(m.slug, ''), wm.market_slug) AS market_slug,
                u.active AS user_active,
                COALESCE(np.in_app_enabled, TRUE) AS in_app_enabled,
                COALESCE(np.digest_mode, 'realtime') AS digest_mode,
                mls.latest_price,
                mls.updated_at AS price_updated_at,
                mls.price_24h_ago,
                COALESCE(mss.completion_status, 'OPEN') AS completion_status,
                COALESCE(mss.has_propose, FALSE) AS has_propose,
                COALESCE(mss.has_dispute, FALSE) AS has_dispute,
                COALESCE(mss.has_settle, FALSE) AS has_settle,
                COALESCE(mss.is_trading_closed, FALSE) AS is_trading_closed,
                COALESCE(mss.is_final, FALSE) AS is_final,
                mss.updated_at AS status_updated_at
            FROM product.alert_rules r
            JOIN product.watchlist_markets wm
              ON wm.watchlist_id = r.watchlist_id
             AND wm.market_id = r.market_id
            JOIN product.users u ON u.id = r.user_id
            LEFT JOIN product.notification_preferences np ON np.user_id = r.user_id
            LEFT JOIN core.markets m ON m.id = r.market_id
            LEFT JOIN core.market_list_serving mls ON mls.market_id = r.market_id
            LEFT JOIN core.market_status_snapshot mss ON mss.market_id = r.market_id
            WHERE r.enabled = TRUE
              AND u.active = TRUE
              AND (
                    COALESCE(np.in_app_enabled, TRUE) = TRUE
                    OR COALESCE(np.web_push_enabled, FALSE) = TRUE
                  )
              AND COALESCE(np.digest_mode, 'realtime') <> 'off'
            ORDER BY r.created_at ASC
            """
        ).fetchall()
        for rule in rows:
            evaluated += 1
            active, title, detail, severity, price, oracle_status, source_at, facts = _condition(rule)
            was_active = bool(rule[7])
            if not active:
                if was_active:
                    rearmed += 1
                    conn.execute(
                        "UPDATE product.alert_rules SET is_condition_active = FALSE, updated_at = NOW() WHERE id = ?",
                        (rule[0],),
                    )
                continue
            if was_active:
                continue
            if rule[8] is not None:
                cooldown_ready = conn.execute(
                    "SELECT ?::timestamptz + (? * INTERVAL '1 second') <= NOW()",
                    (rule[8], int(rule[6])),
                ).fetchone()[0]
                if not cooldown_ready:
                    conn.execute(
                        "UPDATE product.alert_rules SET is_condition_active = TRUE, updated_at = NOW() WHERE id = ?",
                        (rule[0],),
                    )
                    continue
            observed_key = _iso(source_at) or "no-source-time"
            event_key = f"{rule[0]}:{rule[4]}:{observed_key}"
            event_id = str(uuid.uuid4())
            cursor = conn.execute(
                """
                INSERT INTO product.alert_events (
                    id, user_id, rule_id, market_id, event_key, kind, severity,
                    title, detail, observed_price, oracle_status, source_observed_at, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
                ON CONFLICT (user_id, event_key) DO NOTHING
                """,
                (
                    event_id,
                    rule[1],
                    rule[0],
                    rule[3],
                    event_key,
                    rule[4],
                    severity,
                    title,
                    detail,
                    price,
                    oracle_status,
                    source_at,
                    _json({"marketTitle": rule[9], "marketSlug": rule[10], **facts}),
                ),
            )
            conn.execute(
                """
                UPDATE product.alert_rules
                SET is_condition_active = TRUE,
                    last_triggered_at = CASE WHEN ? > 0 THEN NOW() ELSE last_triggered_at END,
                    updated_at = NOW()
                WHERE id = ?
                """,
                (cursor.rowcount, rule[0]),
            )
            conn.execute(
                """
                UPDATE product.watchlist_markets
                SET last_evaluated_at = NOW()
                WHERE watchlist_id = ? AND market_id = ?
                """,
                (rule[2], rule[3]),
            )
            triggered += max(0, int(cursor.rowcount))
            if int(cursor.rowcount) > 0:
                queued += queue_alert_deliveries(
                    conn,
                    alert_event_id=event_id,
                    user_id=int(rule[1]),
                )
        conn.execute(
            "DELETE FROM product.alert_events WHERE read_at IS NOT NULL AND read_at < NOW() - INTERVAL '90 days'"
        )
        result = {
            "evaluated": evaluated,
            "triggered": triggered,
            "rearmed": rearmed,
            "webPushQueued": queued,
        }
        conn.execute(
            """
            INSERT INTO product.runtime_state (key, status, updated_at, details)
            VALUES ('alert-evaluator', 'ok', NOW(), ?::jsonb)
            ON CONFLICT (key)
            DO UPDATE SET status = EXCLUDED.status, updated_at = NOW(), details = EXCLUDED.details
            """,
            (_json(result),),
        )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_alert_runtime_error(error: BaseException) -> None:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO product.runtime_state (key, status, updated_at, details)
            VALUES ('alert-evaluator', 'error', NOW(), ?::jsonb)
            ON CONFLICT (key)
            DO UPDATE SET status = EXCLUDED.status, updated_at = NOW(), details = EXCLUDED.details
            """,
            (_json({"error": str(error)[:500]}),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
