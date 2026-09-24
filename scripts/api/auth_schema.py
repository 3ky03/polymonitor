"""Idempotent PostgreSQL schema for product identity and access control."""

from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "2026-07-28-auth-v1"
PRODUCT_SCHEMA_VERSION = "2026-07-29-web-push-v1"

DDL = """
CREATE SCHEMA IF NOT EXISTS product;

CREATE TABLE IF NOT EXISTS product.schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS product.users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    force_password_change BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS users_username_normalized_uq
    ON product.users (LOWER(username));

CREATE TABLE IF NOT EXISTS product.sessions (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    token_hash CHAR(64) NOT NULL UNIQUE,
    csrf_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    user_agent_hash CHAR(64),
    ip_hash CHAR(64)
);
CREATE INDEX IF NOT EXISTS sessions_user_active_idx
    ON product.sessions (user_id, expires_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS product.api_keys (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL UNIQUE,
    key_hash CHAR(64) NOT NULL UNIQUE,
    scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 60 CHECK (rate_limit_per_minute BETWEEN 1 AND 600),
    daily_quota INTEGER NOT NULL DEFAULT 5000 CHECK (daily_quota BETWEEN 1 AND 1000000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS api_keys_user_active_idx
    ON product.api_keys (user_id, created_at DESC) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS product.api_key_usage (
    api_key_id UUID NOT NULL REFERENCES product.api_keys(id) ON DELETE CASCADE,
    window_kind TEXT NOT NULL CHECK (window_kind IN ('minute', 'day')),
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (api_key_id, window_kind, window_start)
);
CREATE INDEX IF NOT EXISTS api_key_usage_retention_idx
    ON product.api_key_usage (window_start);

CREATE TABLE IF NOT EXISTS product.auth_rate_limits (
    scope TEXT NOT NULL,
    subject_hash CHAR(64) NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, subject_hash, window_start)
);
CREATE INDEX IF NOT EXISTS auth_rate_limits_retention_idx
    ON product.auth_rate_limits (window_start);

CREATE TABLE IF NOT EXISTS product.audit_log (
    id BIGSERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_user_id BIGINT REFERENCES product.users(id) ON DELETE SET NULL,
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('anonymous', 'session', 'api_key', 'system')),
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    result TEXT NOT NULL CHECK (result IN ('success', 'denied', 'error')),
    request_id TEXT,
    ip_hash CHAR(64),
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_log_occurred_idx
    ON product.audit_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_actor_idx
    ON product.audit_log (actor_user_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS product.watchlists (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT 'Primary Watchlist',
    is_default BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS watchlists_default_user_uq
    ON product.watchlists (user_id) WHERE is_default = TRUE;

CREATE TABLE IF NOT EXISTS product.watchlist_markets (
    watchlist_id UUID NOT NULL REFERENCES product.watchlists(id) ON DELETE CASCADE,
    market_id BIGINT NOT NULL,
    market_title TEXT NOT NULL,
    market_slug TEXT,
    note TEXT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_evaluated_at TIMESTAMPTZ,
    PRIMARY KEY (watchlist_id, market_id)
);
CREATE INDEX IF NOT EXISTS watchlist_markets_market_idx
    ON product.watchlist_markets (market_id);

CREATE TABLE IF NOT EXISTS product.alert_rules (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    watchlist_id UUID NOT NULL REFERENCES product.watchlists(id) ON DELETE CASCADE,
    market_id BIGINT NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN (
            'price_above',
            'price_below',
            'oracle_gap',
            'oracle_proposed',
            'oracle_disputed',
            'oracle_resolved',
            'market_closed'
        )
    ),
    threshold NUMERIC(18, 10),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    cooldown_seconds INTEGER NOT NULL DEFAULT 3600 CHECK (cooldown_seconds BETWEEN 60 AND 2592000),
    is_condition_active BOOLEAN NOT NULL DEFAULT FALSE,
    last_triggered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (kind IN ('price_above', 'price_below') AND threshold IS NOT NULL AND threshold BETWEEN 0 AND 1)
        OR
        (kind NOT IN ('price_above', 'price_below') AND threshold IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS alert_rules_market_kind_threshold_uq
    ON product.alert_rules (
        user_id,
        market_id,
        kind,
        COALESCE(threshold, -1::numeric)
    );
CREATE INDEX IF NOT EXISTS alert_rules_enabled_idx
    ON product.alert_rules (enabled, market_id) WHERE enabled = TRUE;

CREATE TABLE IF NOT EXISTS product.notification_preferences (
    user_id BIGINT PRIMARY KEY REFERENCES product.users(id) ON DELETE CASCADE,
    in_app_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    web_push_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    digest_mode TEXT NOT NULL DEFAULT 'realtime' CHECK (digest_mode IN ('realtime', 'hourly', 'daily', 'off')),
    quiet_start_minute SMALLINT CHECK (quiet_start_minute BETWEEN 0 AND 1439),
    quiet_end_minute SMALLINT CHECK (quiet_end_minute BETWEEN 0 AND 1439),
    timezone TEXT NOT NULL DEFAULT 'UTC',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE product.notification_preferences
    ADD COLUMN IF NOT EXISTS web_push_enabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS product.alert_events (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    rule_id UUID REFERENCES product.alert_rules(id) ON DELETE SET NULL,
    market_id BIGINT NOT NULL,
    event_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical', 'positive')),
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    observed_price NUMERIC(18, 10),
    oracle_status TEXT,
    source_observed_at TIMESTAMPTZ,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at TIMESTAMPTZ,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (user_id, event_key)
);
CREATE INDEX IF NOT EXISTS alert_events_user_unread_idx
    ON product.alert_events (user_id, occurred_at DESC) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS alert_events_user_recent_idx
    ON product.alert_events (user_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS product.web_push_subscriptions (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth_secret TEXT NOT NULL,
    expiration_time BIGINT,
    user_agent_hash CHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS web_push_subscriptions_user_active_idx
    ON product.web_push_subscriptions (user_id, updated_at DESC) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS product.web_push_deliveries (
    id UUID PRIMARY KEY,
    alert_event_id UUID NOT NULL REFERENCES product.alert_events(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    subscription_id UUID NOT NULL REFERENCES product.web_push_subscriptions(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'retry', 'sent', 'failed', 'cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    UNIQUE (alert_event_id, subscription_id)
);
CREATE INDEX IF NOT EXISTS web_push_deliveries_due_idx
    ON product.web_push_deliveries (next_attempt_at, created_at)
    WHERE status IN ('pending', 'retry');

CREATE TABLE IF NOT EXISTS product.runtime_state (
    key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS product.workspace_layouts (
    user_id BIGINT PRIMARY KEY REFERENCES product.users(id) ON DELETE CASCADE,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    active_panel_ids JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(active_panel_ids) = 'array'),
    panel_layout JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(panel_layout) = 'object'),
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(preferences) = 'object'),
    client_updated_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS product.briefings (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    public_id VARCHAR(48) NOT NULL UNIQUE,
    title TEXT NOT NULL,
    snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS briefings_user_recent_idx
    ON product.briefings (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS briefings_public_active_idx
    ON product.briefings (public_id, expires_at) WHERE revoked_at IS NULL;

INSERT INTO product.schema_migrations (version)
VALUES ('2026-07-28-auth-v1')
ON CONFLICT (version) DO NOTHING;

INSERT INTO product.schema_migrations (version)
VALUES ('2026-07-29-watchlist-alerts-v1')
ON CONFLICT (version) DO NOTHING;

INSERT INTO product.schema_migrations (version)
VALUES ('2026-07-29-layout-briefings-v1')
ON CONFLICT (version) DO NOTHING;

INSERT INTO product.schema_migrations (version)
VALUES ('2026-07-29-web-push-v1')
ON CONFLICT (version) DO NOTHING;
"""


def apply_schema(connection: Any) -> None:
    try:
        connection.execute(DDL)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def schema_is_ready(connection: Any) -> bool:
    row = connection.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM product.schema_migrations
            WHERE version = ?
        ) AS ready
        """,
        (SCHEMA_VERSION,),
    ).fetchone()
    return bool(row and row[0])


def product_schema_is_ready(connection: Any) -> bool:
    row = connection.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM product.schema_migrations
            WHERE version = ?
        ) AS ready
        """,
        (PRODUCT_SCHEMA_VERSION,),
    ).fetchone()
    return bool(row and row[0])
