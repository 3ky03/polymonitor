"""VAPID-backed Web Push subscriptions and reliable alert delivery."""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from db import get_connection

from api.auth_schema import product_schema_is_ready
from api.services.auth_service import AuthError, Principal, audit_action


MAX_DELIVERY_ATTEMPTS = 5
MAX_SUBSCRIPTIONS_PER_USER = 12
PUSH_TTL_SECONDS = 6 * 60 * 60
BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DEFAULT_PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    ".notify.windows.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
)


def _env(name: str) -> str:
    return str(os.environ.get(name, "") or "").strip()


def public_key() -> str:
    return _env("POLYDATA_WEB_PUSH_PUBLIC_KEY")


def private_key() -> str:
    return _env("POLYDATA_WEB_PUSH_PRIVATE_KEY")


def vapid_subject() -> str:
    return _env("POLYDATA_WEB_PUSH_SUBJECT")


def configured() -> bool:
    return bool(public_key() and private_key() and vapid_subject())


def _allowed_push_host(hostname: str) -> bool:
    configured_suffixes = tuple(
        value.strip().lower()
        for value in _env("POLYDATA_WEB_PUSH_ALLOWED_HOST_SUFFIXES").split(",")
        if value.strip()
    )
    suffixes = configured_suffixes or DEFAULT_PUSH_HOST_SUFFIXES
    normalized = hostname.rstrip(".").lower()
    return any(
        normalized == suffix.lstrip(".")
        or (suffix.startswith(".") and normalized.endswith(suffix))
        for suffix in suffixes
    )


def validate_runtime_config() -> None:
    values = (public_key(), private_key(), vapid_subject())
    if any(values) and not all(values):
        raise RuntimeError(
            "POLYDATA_WEB_PUSH_PUBLIC_KEY, POLYDATA_WEB_PUSH_PRIVATE_KEY and "
            "POLYDATA_WEB_PUSH_SUBJECT must be configured together"
        )
    if not all(values):
        return
    decoded = _decode_base64url(values[0], "POLYDATA_WEB_PUSH_PUBLIC_KEY")
    if len(decoded) != 65 or decoded[0] != 4:
        raise RuntimeError("POLYDATA_WEB_PUSH_PUBLIC_KEY must be an uncompressed P-256 public key")
    subject = values[2]
    if not (subject.startswith("mailto:") or subject.startswith("https://")):
        raise RuntimeError("POLYDATA_WEB_PUSH_SUBJECT must use mailto: or https://")


def _ensure_schema(conn: Any) -> None:
    if not product_schema_is_ready(conn):
        raise AuthError(503, "PRODUCT_SCHEMA_MISSING", "Web Push storage is not ready on this deployment.")


def _decode_base64url(value: str, field: str) -> bytes:
    if not value or not BASE64URL_RE.fullmatch(value):
        raise ValueError(f"{field} must be unpadded base64url")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise ValueError(f"{field} is not valid base64url") from exc


def _validate_subscription(payload: Mapping[str, Any]) -> tuple[str, str, str, int | None]:
    endpoint = str(payload.get("endpoint") or "").strip()
    parsed = urlsplit(endpoint)
    if (
        len(endpoint) > 2048
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not _allowed_push_host(parsed.hostname or "")
    ):
        raise AuthError(
            400,
            "INVALID_PUSH_ENDPOINT",
            "Web Push endpoint must be a bounded HTTPS URL from an allowed push service.",
        )
    keys = payload.get("keys")
    if not isinstance(keys, Mapping):
        raise AuthError(400, "INVALID_PUSH_KEYS", "Web Push subscription keys are required.")
    p256dh = str(keys.get("p256dh") or "").strip()
    auth_secret = str(keys.get("auth") or "").strip()
    try:
        p256dh_bytes = _decode_base64url(p256dh, "p256dh")
        auth_bytes = _decode_base64url(auth_secret, "auth")
    except ValueError as exc:
        raise AuthError(400, "INVALID_PUSH_KEYS", str(exc)) from exc
    if len(p256dh_bytes) != 65 or p256dh_bytes[0] != 4 or not 16 <= len(auth_bytes) <= 32:
        raise AuthError(400, "INVALID_PUSH_KEYS", "Web Push key material has an invalid length.")
    expiration_raw = payload.get("expirationTime")
    if expiration_raw in (None, ""):
        expiration_time = None
    else:
        try:
            expiration_time = int(expiration_raw)
        except (TypeError, ValueError) as exc:
            raise AuthError(400, "INVALID_PUSH_EXPIRATION", "expirationTime must be an epoch millisecond value.") from exc
        if expiration_time <= int(datetime.now(timezone.utc).timestamp() * 1000):
            raise AuthError(400, "INVALID_PUSH_EXPIRATION", "expirationTime must be in the future.")
    return endpoint, p256dh, auth_secret, expiration_time


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise AuthError(400, "INVALID_TIMEZONE", "Timezone must be a valid IANA timezone name.") from exc


def validate_timezone(name: str) -> None:
    _timezone(name)


def _quiet_release(
    candidate: datetime,
    timezone_name: str,
    quiet_start: int | None,
    quiet_end: int | None,
) -> datetime:
    if quiet_start is None or quiet_end is None or quiet_start == quiet_end:
        return candidate
    local = candidate.astimezone(_timezone(timezone_name))
    minute = local.hour * 60 + local.minute
    inside = (
        quiet_start <= minute < quiet_end
        if quiet_start < quiet_end
        else minute >= quiet_start or minute < quiet_end
    )
    if not inside:
        return candidate
    release_date = local.date()
    if quiet_start > quiet_end and minute >= quiet_start:
        release_date += timedelta(days=1)
    release = datetime.combine(
        release_date,
        datetime.min.time(),
        tzinfo=local.tzinfo,
    ) + timedelta(minutes=quiet_end)
    return release.astimezone(timezone.utc)


def _scheduled_at(
    digest_mode: str,
    timezone_name: str,
    quiet_start: int | None,
    quiet_end: int | None,
    *,
    now: datetime | None = None,
) -> datetime:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local = current.astimezone(_timezone(timezone_name))
    if digest_mode == "hourly":
        candidate_local = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif digest_mode == "daily":
        candidate_local = local.replace(hour=9, minute=0, second=0, microsecond=0)
        if candidate_local <= local:
            candidate_local += timedelta(days=1)
    else:
        candidate_local = local
    return _quiet_release(candidate_local.astimezone(timezone.utc), timezone_name, quiet_start, quiet_end)


def get_status(principal: Principal) -> dict[str, Any]:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT
                COALESCE(np.web_push_enabled, FALSE),
                COUNT(s.id) FILTER (WHERE s.revoked_at IS NULL)
            FROM product.users u
            LEFT JOIN product.notification_preferences np ON np.user_id = u.id
            LEFT JOIN product.web_push_subscriptions s ON s.user_id = u.id
            WHERE u.id = ?
            GROUP BY np.web_push_enabled
            """,
            (principal.user_id,),
        ).fetchone()
        count = int(row[1]) if row else 0
        return {
            "available": configured(),
            "publicKey": public_key() if configured() else None,
            "enabled": bool(row[0]) if row else False,
            "connected": count > 0,
            "subscriptionCount": count,
        }
    finally:
        conn.close()


def upsert_subscription(
    principal: Principal,
    payload: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    if not configured():
        raise AuthError(503, "WEB_PUSH_UNAVAILABLE", "Web Push is not configured on this deployment.")
    endpoint, p256dh, auth_secret, expiration_time = _validate_subscription(payload)
    conn = get_connection()
    try:
        _ensure_schema(conn)
        count = conn.execute(
            "SELECT COUNT(*) FROM product.web_push_subscriptions WHERE user_id = ? AND revoked_at IS NULL",
            (principal.user_id,),
        ).fetchone()[0]
        existing = conn.execute(
            "SELECT id, user_id FROM product.web_push_subscriptions WHERE endpoint = ?",
            (endpoint,),
        ).fetchone()
        if not existing and int(count) >= MAX_SUBSCRIPTIONS_PER_USER:
            raise AuthError(409, "WEB_PUSH_LIMIT_REACHED", "Too many active browser subscriptions.")
        if existing and int(existing[1]) != principal.user_id:
            raise AuthError(409, "WEB_PUSH_ENDPOINT_CONFLICT", "This browser subscription belongs to another account.")
        subscription_id = str(existing[0]) if existing else str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO product.web_push_subscriptions (
                id, user_id, endpoint, p256dh, auth_secret, expiration_time, user_agent_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (endpoint)
            DO UPDATE SET
                p256dh = EXCLUDED.p256dh,
                auth_secret = EXCLUDED.auth_secret,
                expiration_time = EXCLUDED.expiration_time,
                user_agent_hash = EXCLUDED.user_agent_hash,
                revoked_at = NULL,
                failure_count = 0,
                updated_at = NOW()
            """,
            (
                subscription_id,
                principal.user_id,
                endpoint,
                p256dh,
                auth_secret,
                expiration_time,
                metadata.get("user_agent_hash"),
            ),
        )
        conn.execute(
            """
            INSERT INTO product.notification_preferences (user_id, web_push_enabled)
            VALUES (?, TRUE)
            ON CONFLICT (user_id)
            DO UPDATE SET web_push_enabled = TRUE, updated_at = NOW()
            """,
            (principal.user_id,),
        )
        audit_action(
            conn,
            principal,
            action="web_push.subscribe",
            result="success",
            metadata=metadata,
            target_type="web_push_subscription",
            target_id=subscription_id,
            details={"expirationConfigured": expiration_time is not None},
        )
        conn.commit()
        return get_status(principal)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_subscription(
    principal: Principal,
    endpoint: Any,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = str(endpoint or "").strip()
    if not normalized:
        raise AuthError(400, "PUSH_ENDPOINT_REQUIRED", "Web Push endpoint is required.")
    conn = get_connection()
    try:
        _ensure_schema(conn)
        row = conn.execute(
            """
            UPDATE product.web_push_subscriptions
            SET revoked_at = NOW(), updated_at = NOW()
            WHERE user_id = ? AND endpoint = ? AND revoked_at IS NULL
            RETURNING id
            """,
            (principal.user_id, normalized),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE product.web_push_deliveries
                SET status = 'cancelled', last_error = 'subscription-revoked'
                WHERE subscription_id = ? AND status IN ('pending', 'retry')
                """,
                (row[0],),
            )
            audit_action(
                conn,
                principal,
                action="web_push.unsubscribe",
                result="success",
                metadata=metadata,
                target_type="web_push_subscription",
                target_id=str(row[0]),
            )
        remaining = int(conn.execute(
            "SELECT COUNT(*) FROM product.web_push_subscriptions WHERE user_id = ? AND revoked_at IS NULL",
            (principal.user_id,),
        ).fetchone()[0])
        if remaining == 0:
            conn.execute(
                "UPDATE product.notification_preferences SET web_push_enabled = FALSE, updated_at = NOW() WHERE user_id = ?",
                (principal.user_id,),
            )
        conn.commit()
        return get_status(principal)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def queue_alert_deliveries(conn: Any, *, alert_event_id: str, user_id: int) -> int:
    if not configured():
        return 0
    preference = conn.execute(
        """
        SELECT web_push_enabled, digest_mode, quiet_start_minute, quiet_end_minute, timezone
        FROM product.notification_preferences
        WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()
    if not preference or not bool(preference[0]) or str(preference[1]) == "off":
        return 0
    scheduled_at = _scheduled_at(
        str(preference[1]),
        str(preference[4] or "UTC"),
        int(preference[2]) if preference[2] is not None else None,
        int(preference[3]) if preference[3] is not None else None,
    )
    subscriptions = conn.execute(
        "SELECT id FROM product.web_push_subscriptions WHERE user_id = ? AND revoked_at IS NULL",
        (user_id,),
    ).fetchall()
    queued = 0
    for subscription in subscriptions:
        cursor = conn.execute(
            """
            INSERT INTO product.web_push_deliveries (
                id, alert_event_id, user_id, subscription_id, next_attempt_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (alert_event_id, subscription_id) DO NOTHING
            """,
            (str(uuid.uuid4()), alert_event_id, user_id, subscription[0], scheduled_at),
        )
        queued += max(0, int(cursor.rowcount))
    return queued


def _payload(rows: list[Any]) -> str:
    first = rows[0]
    count = len(rows)
    digest_mode = str(first[15])
    title = str(first[8]) if count == 1 else f"{count} PolyMonitor alerts"
    body = str(first[9])
    if count > 1:
        body = f"{body} · +{count - 1} more"
    market_id = int(first[10])
    url = f"/markets/{market_id}" if count == 1 and digest_mode == "realtime" else "/watchlist"
    return json.dumps(
        {
            "title": title[:120],
            "body": body[:240],
            "url": url,
            "tag": f"polymonitor-{digest_mode}-{market_id if count == 1 else first[2]}",
            "severity": str(first[7]),
            "count": count,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _mark_batch(
    delivery_ids: list[str],
    subscription_id: str,
    *,
    status: str,
    attempts: int,
    error: str | None = None,
    retry_at: datetime | None = None,
    revoke: bool = False,
) -> None:
    conn = get_connection()
    try:
        placeholders = ",".join("?" for _ in delivery_ids)
        if status == "sent":
            conn.execute(
                f"""
                UPDATE product.web_push_deliveries
                SET status = 'sent', attempts = ?, sent_at = NOW(), last_error = NULL
                WHERE id IN ({placeholders})
                """,
                (attempts, *delivery_ids),
            )
            conn.execute(
                """
                UPDATE product.web_push_subscriptions
                SET failure_count = 0, last_success_at = NOW(), updated_at = NOW()
                WHERE id = ?
                """,
                (subscription_id,),
            )
        else:
            conn.execute(
                f"""
                UPDATE product.web_push_deliveries
                SET status = ?, attempts = ?, next_attempt_at = COALESCE(?, next_attempt_at), last_error = ?
                WHERE id IN ({placeholders})
                """,
                (status, attempts, retry_at, error, *delivery_ids),
            )
            conn.execute(
                """
                UPDATE product.web_push_subscriptions
                SET failure_count = failure_count + 1, last_failure_at = NOW(), updated_at = NOW(),
                    revoked_at = CASE WHEN ? THEN NOW() ELSE revoked_at END
                WHERE id = ?
                """,
                (revoke, subscription_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def publish_pending_deliveries(*, limit: int = 100) -> dict[str, int | str]:
    if not configured():
        return {"status": "disabled", "selected": 0, "sent": 0, "retried": 0, "failed": 0}
    validate_runtime_config()
    conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.execute(
            """
            UPDATE product.web_push_subscriptions
            SET revoked_at = NOW(), updated_at = NOW()
            WHERE revoked_at IS NULL
              AND expiration_time IS NOT NULL
              AND expiration_time <= (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT
            """
        )
        conn.execute(
            """
            UPDATE product.web_push_deliveries d
            SET status = 'cancelled', last_error = 'subscription-expired'
            FROM product.web_push_subscriptions s
            WHERE s.id = d.subscription_id
              AND s.revoked_at IS NOT NULL
              AND d.status IN ('pending', 'retry')
            """
        )
        conn.execute(
            """
            DELETE FROM product.web_push_deliveries
            WHERE status IN ('sent', 'failed', 'cancelled')
              AND created_at < NOW() - INTERVAL '90 days'
            """
        )
        conn.commit()
        rows = conn.execute(
            """
            SELECT
                d.id,
                d.alert_event_id,
                d.user_id,
                d.attempts,
                s.id,
                s.endpoint,
                e.kind,
                e.severity,
                e.title,
                e.detail,
                e.market_id,
                s.p256dh,
                s.auth_secret,
                np.quiet_start_minute,
                np.quiet_end_minute,
                np.digest_mode,
                np.timezone
            FROM product.web_push_deliveries d
            JOIN product.web_push_subscriptions s ON s.id = d.subscription_id
            JOIN product.alert_events e ON e.id = d.alert_event_id
            JOIN product.notification_preferences np ON np.user_id = d.user_id
            WHERE d.status IN ('pending', 'retry')
              AND d.next_attempt_at <= NOW()
              AND s.revoked_at IS NULL
              AND np.web_push_enabled = TRUE
              AND np.digest_mode <> 'off'
            ORDER BY d.next_attempt_at ASC, d.created_at ASC
            LIMIT ?
            """,
            (max(1, min(500, int(limit))),),
        ).fetchall()
    finally:
        conn.close()
    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in rows:
        digest_mode = str(row[15])
        key = (str(row[4]), str(row[0]) if digest_mode == "realtime" else digest_mode)
        grouped[key].append(row)
    result = {"status": "ok", "selected": len(rows), "sent": 0, "retried": 0, "failed": 0}
    if not rows:
        return result
    try:
        from pywebpush import WebPushException, webpush
    except ImportError as exc:
        raise RuntimeError("pywebpush is required when Web Push is configured") from exc
    for (subscription_id, _batch_key), batch in grouped.items():
        first = batch[0]
        delivery_ids = [str(row[0]) for row in batch]
        attempts = max(int(row[3]) for row in batch) + 1
        quiet_release = _quiet_release(
            datetime.now(timezone.utc),
            str(first[16] or "UTC"),
            int(first[13]) if first[13] is not None else None,
            int(first[14]) if first[14] is not None else None,
        )
        if quiet_release > datetime.now(timezone.utc) + timedelta(seconds=1):
            _mark_batch(
                delivery_ids,
                subscription_id,
                status="retry",
                attempts=max(0, attempts - 1),
                error="quiet-hours",
                retry_at=quiet_release,
            )
            result["retried"] += len(delivery_ids)
            continue
        try:
            webpush(
                subscription_info={
                    "endpoint": str(first[5]),
                    "keys": {"p256dh": str(first[11]), "auth": str(first[12])},
                },
                data=_payload(batch),
                vapid_private_key=private_key(),
                vapid_claims={"sub": vapid_subject()},
                ttl=PUSH_TTL_SECONDS,
                timeout=12,
            )
            _mark_batch(delivery_ids, subscription_id, status="sent", attempts=attempts)
            result["sent"] += len(delivery_ids)
        except WebPushException as exc:
            response_status = int(exc.response.status_code) if exc.response is not None else 0
            permanent = response_status in {404, 410}
            exhausted = attempts >= MAX_DELIVERY_ATTEMPTS
            if permanent or exhausted or (400 <= response_status < 500 and response_status != 429):
                _mark_batch(
                    delivery_ids,
                    subscription_id,
                    status="failed",
                    attempts=attempts,
                    error=f"push-http-{response_status or 'transport'}",
                    revoke=permanent,
                )
                result["failed"] += len(delivery_ids)
            else:
                retry_at = datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * (2 ** (attempts - 1))))
                _mark_batch(
                    delivery_ids,
                    subscription_id,
                    status="retry",
                    attempts=attempts,
                    error=f"push-http-{response_status or 'transport'}",
                    retry_at=retry_at,
                )
                result["retried"] += len(delivery_ids)
        except Exception:
            exhausted = attempts >= MAX_DELIVERY_ATTEMPTS
            _mark_batch(
                delivery_ids,
                subscription_id,
                status="failed" if exhausted else "retry",
                attempts=attempts,
                error="push-transport-error",
                retry_at=None if exhausted else datetime.now(timezone.utc) + timedelta(seconds=min(3600, 30 * (2 ** (attempts - 1)))),
            )
            result["failed" if exhausted else "retried"] += len(delivery_ids)
    return result


def record_publisher_runtime(
    *,
    status: str,
    details: Mapping[str, Any],
) -> None:
    conn = get_connection()
    try:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO product.runtime_state (key, status, updated_at, details)
            VALUES ('web-push-publisher', ?, NOW(), ?::jsonb)
            ON CONFLICT (key)
            DO UPDATE SET status = EXCLUDED.status, updated_at = NOW(), details = EXCLUDED.details
            """,
            (
                status,
                json.dumps(dict(details), ensure_ascii=True, separators=(",", ":"), sort_keys=True),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
