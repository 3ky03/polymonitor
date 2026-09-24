"""Server-side sessions, RBAC, scoped API keys, and security audit events."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from db import dict_from_row, get_backend, get_connection

from api.auth_schema import schema_is_ready


SESSION_COOKIE_DEFAULT = "__Host-polydata-session"
SESSION_SCOPE = "operations:read"
MCP_SCOPE = "mcp:read"
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
ALLOWED_SCOPES = frozenset({SESSION_SCOPE, MCP_SCOPE})
PASSWORD_N = 2**15
PASSWORD_R = 8
PASSWORD_P = 3
PASSWORD_DKLEN = 32
PASSWORD_MAXMEM = 128 * 1024 * 1024
DUMMY_PASSWORD_HASH = (
    "scrypt$32768$8$3$00112233445566778899aabbccddeeff$"
    "0110265e850eac364fded01d50fcb423e068db5e3bd898b8929337c6f504dbcc"
)


class AuthError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True)
class Principal:
    kind: str
    user_id: int
    username: str
    role: str
    force_password_change: bool
    scopes: frozenset[str]
    session_id: str | None = None
    api_key_id: str | None = None

    def public_user(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "username": self.username,
            "role": self.role,
            "forcePasswordChange": self.force_password_change,
        }


def auth_enabled() -> bool:
    return str(os.environ.get("POLYDATA_AUTH_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}


def session_cookie_name() -> str:
    return os.environ.get("POLYDATA_AUTH_COOKIE_NAME", SESSION_COOKIE_DEFAULT).strip() or SESSION_COOKIE_DEFAULT


def session_ttl_seconds() -> int:
    return max(900, min(7 * 86_400, _env_int("POLYDATA_AUTH_SESSION_TTL_SECONDS", 43_200)))


def cookie_secure() -> bool:
    raw = str(os.environ.get("POLYDATA_AUTH_COOKIE_SECURE", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _audit_pepper() -> bytes:
    value = os.environ.get("POLYDATA_AUTH_AUDIT_PEPPER", "")
    if auth_enabled() and len(value) < 32:
        raise RuntimeError("POLYDATA_AUTH_AUDIT_PEPPER must contain at least 32 characters when auth is enabled")
    return value.encode("utf-8")


def validate_runtime_config() -> None:
    if not auth_enabled():
        return
    if get_backend().strip().lower() not in {"postgres", "postgresql"}:
        raise RuntimeError("product authentication requires PostgreSQL")
    _audit_pepper()
    conn = get_connection()
    try:
        if not schema_is_ready(conn):
            raise RuntimeError("product authentication schema is missing; run `python -m api.manage_auth migrate`")
    finally:
        conn.close()


def normalize_username(value: Any) -> str:
    username = str(value or "").strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise AuthError(400, "INVALID_USERNAME", "Username must be 3-64 lowercase letters, numbers, dots, underscores, or hyphens.")
    return username


def validate_password(value: Any) -> str:
    password = str(value or "")
    if len(password) < 12 or len(password) > 256:
        raise AuthError(400, "INVALID_PASSWORD", "Password must contain between 12 and 256 characters.")
    return password


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_N,
        r=PASSWORD_R,
        p=PASSWORD_P,
        dklen=PASSWORD_DKLEN,
        maxmem=PASSWORD_MAXMEM,
    )
    return f"scrypt${PASSWORD_N}${PASSWORD_R}${PASSWORD_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(expected_hex)),
            maxmem=PASSWORD_MAXMEM,
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected_hex))
    except (TypeError, ValueError):
        return False


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _privacy_hash(value: str) -> str | None:
    if not value:
        return None
    return hmac.new(_audit_pepper(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def request_metadata(request: Any) -> dict[str, str | None]:
    forwarded = str(request.headers.get("X-Forwarded-For", "")).split(",", 1)[0].strip()
    remote = forwarded or str(request.remote_addr or "").strip()
    return {
        "request_id": str(getattr(request, "request_id", "") or ""),
        "ip_hash": _privacy_hash(remote),
        "user_agent_hash": _privacy_hash(str(request.headers.get("User-Agent", ""))),
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _audit(
    conn: Any,
    *,
    actor_user_id: int | None,
    actor_kind: str,
    action: str,
    result: str,
    metadata: Mapping[str, Any],
    target_type: str | None = None,
    target_id: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO product.audit_log (
            actor_user_id, actor_kind, action, target_type, target_id,
            result, request_id, ip_hash, details
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
        """,
        (
            actor_user_id,
            actor_kind,
            action,
            target_type,
            target_id,
            result,
            metadata.get("request_id"),
            metadata.get("ip_hash"),
            _json(dict(details or {})),
        ),
    )


def _consume_rate_limit(conn: Any, scope: str, subject_hash: str, *, seconds: int, limit: int) -> None:
    row = conn.execute(
        """
        INSERT INTO product.auth_rate_limits (scope, subject_hash, window_start, request_count)
        VALUES (?, ?, TO_TIMESTAMP(FLOOR(EXTRACT(EPOCH FROM NOW()) / ?) * ?), 1)
        ON CONFLICT (scope, subject_hash, window_start)
        DO UPDATE SET request_count = product.auth_rate_limits.request_count + 1
        RETURNING request_count
        """,
        (scope, subject_hash, seconds, seconds),
    ).fetchone()
    if row and int(row[0]) > limit:
        raise AuthError(429, "RATE_LIMITED", "Too many authentication attempts. Try again later.", retry_after=seconds)


def create_or_update_user(
    username: str,
    password: str,
    *,
    role: str = "user",
    force_password_change: bool = True,
) -> dict[str, Any]:
    normalized = normalize_username(username)
    validated = validate_password(password)
    if role not in {"user", "admin"}:
        raise ValueError("role must be user or admin")
    password_hash = hash_password(validated)
    conn = get_connection()
    try:
        row = conn.execute(
            """
            INSERT INTO product.users (username, password_hash, role, force_password_change)
            VALUES (?, ?, ?, ?)
            ON CONFLICT ((LOWER(username)))
            DO UPDATE SET
                password_hash = EXCLUDED.password_hash,
                role = EXCLUDED.role,
                active = TRUE,
                force_password_change = EXCLUDED.force_password_change,
                updated_at = NOW()
            RETURNING id, username, role, force_password_change, active
            """,
            (normalized, password_hash, role, force_password_change),
        ).fetchone()
        conn.execute("UPDATE product.sessions SET revoked_at = NOW() WHERE user_id = ? AND revoked_at IS NULL", (row[0],))
        _audit(
            conn,
            actor_user_id=int(row[0]),
            actor_kind="system",
            action="user.upsert",
            result="success",
            metadata={},
            target_type="user",
            target_id=str(row[0]),
            details={"role": role, "forcePasswordChange": force_password_change},
        )
        conn.commit()
        return dict_from_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def login(username: Any, password: Any, metadata: Mapping[str, Any]) -> tuple[Principal, str, str]:
    if not auth_enabled():
        raise AuthError(503, "AUTH_DISABLED", "Authentication is not enabled on this deployment.")
    try:
        normalized = normalize_username(username)
    except AuthError:
        normalized = str(username or "").strip().lower()[:64]
    supplied_password = str(password or "")[:256]
    conn = get_connection()
    try:
        subject_hash = _privacy_hash(f"{normalized}|{metadata.get('ip_hash') or ''}") or _token_hash(normalized)
        _consume_rate_limit(conn, "login", subject_hash, seconds=900, limit=10)
        row = conn.execute(
            """
            SELECT id, username, password_hash, role, active, force_password_change
            FROM product.users
            WHERE LOWER(username) = ?
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
        password_matches = verify_password(supplied_password, str(row[2]) if row else DUMMY_PASSWORD_HASH)
        valid = bool(row and row[4] and password_matches)
        if not valid:
            _audit(
                conn,
                actor_user_id=int(row[0]) if row else None,
                actor_kind="anonymous",
                action="auth.login",
                result="denied",
                metadata=metadata,
                details={"reason": "invalid_credentials"},
            )
            conn.commit()
            raise AuthError(401, "INVALID_CREDENTIALS", "Invalid username or password.")

        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO product.sessions (
                id, user_id, token_hash, csrf_hash, expires_at, user_agent_hash, ip_hash
            ) VALUES (?, ?, ?, ?, NOW() + (? * INTERVAL '1 second'), ?, ?)
            """,
            (
                session_id,
                row[0],
                _token_hash(raw_token),
                _token_hash(csrf_token),
                session_ttl_seconds(),
                metadata.get("user_agent_hash"),
                metadata.get("ip_hash"),
            ),
        )
        conn.execute("UPDATE product.users SET last_login_at = NOW(), updated_at = NOW() WHERE id = ?", (row[0],))
        _audit(
            conn,
            actor_user_id=int(row[0]),
            actor_kind="session",
            action="auth.login",
            result="success",
            metadata=metadata,
            target_type="session",
            target_id=session_id,
        )
        conn.commit()
        return (
            Principal(
                kind="session",
                user_id=int(row[0]),
                username=str(row[1]),
                role=str(row[3]),
                force_password_change=bool(row[5]),
                scopes=frozenset({SESSION_SCOPE}) if row[3] == "admin" else frozenset(),
                session_id=session_id,
            ),
            raw_token,
            csrf_token,
        )
    except AuthError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _session_principal(conn: Any, raw_token: str, *, touch: bool) -> tuple[Principal, str]:
    row = conn.execute(
        """
        SELECT
            s.id, s.csrf_hash, u.id, u.username, u.role, u.active, u.force_password_change
        FROM product.sessions s
        JOIN product.users u ON u.id = s.user_id
        WHERE s.token_hash = ?
          AND s.revoked_at IS NULL
          AND s.expires_at > NOW()
        LIMIT 1
        """,
        (_token_hash(raw_token),),
    ).fetchone()
    if not row or not row[5]:
        raise AuthError(401, "AUTH_REQUIRED", "A valid session is required.")
    if touch:
        conn.execute("UPDATE product.sessions SET last_seen_at = NOW() WHERE id = ?", (row[0],))
    principal = Principal(
        kind="session",
        user_id=int(row[2]),
        username=str(row[3]),
        role=str(row[4]),
        force_password_change=bool(row[6]),
        scopes=frozenset({SESSION_SCOPE}) if row[4] == "admin" else frozenset(),
        session_id=str(row[0]),
    )
    return principal, str(row[1])


def _consume_api_key_quota(conn: Any, api_key_id: str, per_minute: int, per_day: int) -> None:
    for kind, expression, limit, retry_after in (
        ("minute", "DATE_TRUNC('minute', NOW())", per_minute, 60),
        ("day", "DATE_TRUNC('day', NOW())", per_day, 86_400),
    ):
        row = conn.execute(
            f"""
            INSERT INTO product.api_key_usage (api_key_id, window_kind, window_start, request_count)
            VALUES (?, ?, {expression}, 1)
            ON CONFLICT (api_key_id, window_kind, window_start)
            DO UPDATE SET request_count = product.api_key_usage.request_count + 1
            RETURNING request_count
            """,
            (api_key_id, kind),
        ).fetchone()
        if row and int(row[0]) > limit:
            raise AuthError(429, "API_KEY_QUOTA_EXCEEDED", f"API key {kind} quota exceeded.", retry_after=retry_after)


def _api_key_principal(conn: Any, raw_key: str) -> Principal:
    row = conn.execute(
        """
        SELECT
            k.id, k.scopes::text, k.rate_limit_per_minute, k.daily_quota,
            u.id, u.username, u.role, u.active, u.force_password_change
        FROM product.api_keys k
        JOIN product.users u ON u.id = k.user_id
        WHERE k.key_hash = ?
          AND k.revoked_at IS NULL
          AND (k.expires_at IS NULL OR k.expires_at > NOW())
        LIMIT 1
        """,
        (_token_hash(raw_key),),
    ).fetchone()
    if not row or not row[7]:
        raise AuthError(401, "INVALID_API_KEY", "The API key is invalid, expired, or revoked.")
    _consume_api_key_quota(conn, str(row[0]), int(row[2]), int(row[3]))
    conn.execute("UPDATE product.api_keys SET last_used_at = NOW() WHERE id = ?", (row[0],))
    scopes = frozenset(str(item) for item in json.loads(str(row[1]) or "[]"))
    return Principal(
        kind="api_key",
        user_id=int(row[4]),
        username=str(row[5]),
        role=str(row[6]),
        force_password_change=bool(row[8]),
        scopes=scopes,
        api_key_id=str(row[0]),
    )


def authenticate_request(
    request: Any,
    *,
    required_role: str | None = None,
    required_scope: str | None = None,
    require_csrf: bool = False,
) -> Principal:
    if not auth_enabled():
        return Principal("system", 0, "local-disabled", "admin", False, ALLOWED_SCOPES)
    authorization = str(request.headers.get("Authorization", "")).strip()
    raw_cookie = str(request.cookies.get(session_cookie_name(), "")).strip()
    has_bearer = authorization.lower().startswith("bearer ")
    if not has_bearer and not raw_cookie:
        raise AuthError(401, "AUTH_REQUIRED", "Sign in with an administrator account or provide a scoped API key.")

    conn = get_connection()
    try:
        csrf_hash = ""
        if has_bearer:
            principal = _api_key_principal(conn, authorization[7:].strip())
        elif raw_cookie:
            principal, csrf_hash = _session_principal(conn, raw_cookie, touch=True)
        else:
            raise AuthError(401, "AUTH_REQUIRED", "Sign in with an administrator account or provide a scoped API key.")
        if required_role and principal.role != required_role:
            raise AuthError(403, "FORBIDDEN", f"The {required_role} role is required.")
        if required_scope and required_scope not in principal.scopes:
            raise AuthError(403, "SCOPE_REQUIRED", f"The {required_scope} scope is required.")
        if principal.force_password_change and (required_role or required_scope):
            raise AuthError(403, "PASSWORD_CHANGE_REQUIRED", "Change the bootstrap password before opening protected resources.")
        if require_csrf and principal.kind == "session":
            supplied = str(request.headers.get("X-CSRF-Token", ""))
            if not supplied or not hmac.compare_digest(_token_hash(supplied), csrf_hash):
                raise AuthError(403, "CSRF_REJECTED", "The request CSRF token is missing or invalid.")
            fetch_site = str(request.headers.get("Sec-Fetch-Site", "")).lower()
            if fetch_site == "cross-site":
                raise AuthError(403, "CROSS_SITE_REJECTED", "Cross-site state changes are not allowed.")
        conn.commit()
        return principal
    except AuthError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def authenticate_user_request(request: Any, *, require_csrf: bool = False) -> Principal:
    principal = authenticate_request(request, require_csrf=require_csrf)
    if principal.kind != "session":
        raise AuthError(403, "SESSION_REQUIRED", "This user workspace requires a browser session.")
    if principal.force_password_change:
        raise AuthError(403, "PASSWORD_CHANGE_REQUIRED", "Change the bootstrap password before opening user workspaces.")
    return principal


def audit_action(
    conn: Any,
    principal: Principal,
    *,
    action: str,
    result: str,
    metadata: Mapping[str, Any],
    target_type: str | None = None,
    target_id: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    _audit(
        conn,
        actor_user_id=principal.user_id,
        actor_kind=principal.kind,
        action=action,
        result=result,
        metadata=metadata,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )


def session_snapshot(request: Any) -> tuple[Principal | None, str | None]:
    if not auth_enabled():
        return None, None
    raw_cookie = str(request.cookies.get(session_cookie_name(), "")).strip()
    if not raw_cookie:
        return None, None
    conn = get_connection()
    try:
        principal, _ = _session_principal(conn, raw_cookie, touch=True)
        csrf_token = secrets.token_urlsafe(32)
        conn.execute(
            "UPDATE product.sessions SET csrf_hash = ?, last_seen_at = NOW() WHERE id = ?",
            (_token_hash(csrf_token), principal.session_id),
        )
        conn.commit()
        return principal, csrf_token
    except AuthError:
        conn.rollback()
        return None, None
    finally:
        conn.close()


def logout(request: Any, principal: Principal, metadata: Mapping[str, Any]) -> None:
    if principal.kind != "session" or not principal.session_id:
        return
    conn = get_connection()
    try:
        conn.execute("UPDATE product.sessions SET revoked_at = NOW() WHERE id = ?", (principal.session_id,))
        _audit(
            conn,
            actor_user_id=principal.user_id,
            actor_kind="session",
            action="auth.logout",
            result="success",
            metadata=metadata,
            target_type="session",
            target_id=principal.session_id,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def change_password(principal: Principal, current: Any, replacement: Any, metadata: Mapping[str, Any]) -> None:
    new_password = validate_password(replacement)
    conn = get_connection()
    try:
        row = conn.execute("SELECT password_hash FROM product.users WHERE id = ? AND active = TRUE", (principal.user_id,)).fetchone()
        if not row or not verify_password(str(current or ""), str(row[0])):
            _audit(
                conn,
                actor_user_id=principal.user_id,
                actor_kind="session",
                action="auth.password_change",
                result="denied",
                metadata=metadata,
                details={"reason": "invalid_current_password"},
            )
            conn.commit()
            raise AuthError(401, "INVALID_CREDENTIALS", "The current password is incorrect.")
        conn.execute(
            """
            UPDATE product.users
            SET password_hash = ?, force_password_change = FALSE, updated_at = NOW()
            WHERE id = ?
            """,
            (hash_password(new_password), principal.user_id),
        )
        conn.execute(
            "UPDATE product.sessions SET revoked_at = NOW() WHERE user_id = ? AND id <> ? AND revoked_at IS NULL",
            (principal.user_id, principal.session_id),
        )
        _audit(
            conn,
            actor_user_id=principal.user_id,
            actor_kind="session",
            action="auth.password_change",
            result="success",
            metadata=metadata,
            target_type="user",
            target_id=str(principal.user_id),
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


def list_api_keys(principal: Principal) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, name, key_prefix, scopes::text, rate_limit_per_minute, daily_quota,
                   created_at, expires_at, last_used_at, revoked_at
            FROM product.api_keys
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (principal.user_id,),
        ).fetchall()
        return [
            {
                "id": str(row[0]),
                "name": row[1],
                "prefix": row[2],
                "scopes": json.loads(str(row[3]) or "[]"),
                "rateLimitPerMinute": row[4],
                "dailyQuota": row[5],
                "createdAt": _iso(row[6]),
                "expiresAt": _iso(row[7]),
                "lastUsedAt": _iso(row[8]),
                "revokedAt": _iso(row[9]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def create_api_key(
    principal: Principal,
    *,
    name: Any,
    scopes: Iterable[Any],
    rate_limit: Any,
    daily_quota: Any,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    clean_name = str(name or "").strip()
    if not clean_name or len(clean_name) > 80:
        raise AuthError(400, "INVALID_KEY_NAME", "API key name must contain 1-80 characters.")
    clean_scopes = sorted({str(scope) for scope in scopes if str(scope) in ALLOWED_SCOPES})
    if not clean_scopes:
        raise AuthError(400, "INVALID_SCOPE", "At least one supported API scope is required.")
    try:
        per_minute = max(1, min(600, int(rate_limit or 60)))
        per_day = max(1, min(1_000_000, int(daily_quota or 5000)))
    except (TypeError, ValueError) as exc:
        raise AuthError(400, "INVALID_QUOTA", "API key limits must be integers.") from exc
    raw_key = f"pm_live_{secrets.token_urlsafe(32)}"
    key_id = str(uuid.uuid4())
    prefix = raw_key[:16]
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO product.api_keys (
                id, user_id, name, key_prefix, key_hash, scopes,
                rate_limit_per_minute, daily_quota
            ) VALUES (?, ?, ?, ?, ?, ?::jsonb, ?, ?)
            """,
            (key_id, principal.user_id, clean_name, prefix, _token_hash(raw_key), _json(clean_scopes), per_minute, per_day),
        )
        _audit(
            conn,
            actor_user_id=principal.user_id,
            actor_kind="session",
            action="api_key.create",
            result="success",
            metadata=metadata,
            target_type="api_key",
            target_id=key_id,
            details={"name": clean_name, "scopes": clean_scopes},
        )
        conn.commit()
        return {"id": key_id, "name": clean_name, "prefix": prefix, "key": raw_key, "scopes": clean_scopes}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_api_key(principal: Principal, key_id: str, metadata: Mapping[str, Any]) -> None:
    try:
        normalized_key_id = str(uuid.UUID(key_id))
    except (TypeError, ValueError) as exc:
        raise AuthError(404, "API_KEY_NOT_FOUND", "API key was not found.") from exc
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE product.api_keys
            SET revoked_at = COALESCE(revoked_at, NOW())
            WHERE id = ? AND user_id = ?
            """,
            (normalized_key_id, principal.user_id),
        )
        if cursor.rowcount < 1:
            raise AuthError(404, "API_KEY_NOT_FOUND", "API key was not found.")
        _audit(
            conn,
            actor_user_id=principal.user_id,
            actor_kind="session",
            action="api_key.revoke",
            result="success",
            metadata=metadata,
            target_type="api_key",
            target_id=normalized_key_id,
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


def list_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT a.id, a.occurred_at, u.username, a.actor_kind, a.action,
                   a.target_type, a.target_id, a.result, a.request_id, a.details::text
            FROM product.audit_log a
            LEFT JOIN product.users u ON u.id = a.actor_user_id
            ORDER BY a.occurred_at DESC
            LIMIT ?
            """,
            (max(1, min(250, int(limit))),),
        ).fetchall()
        return [
            {
                "id": row[0],
                "occurredAt": _iso(row[1]),
                "username": row[2],
                "actorKind": row[3],
                "action": row[4],
                "targetType": row[5],
                "targetId": row[6],
                "result": row[7],
                "requestId": row[8],
                "details": json.loads(str(row[9]) or "{}"),
            }
            for row in rows
        ]
    finally:
        conn.close()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)
