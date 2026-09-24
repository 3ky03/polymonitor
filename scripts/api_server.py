#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""polyData dashboard API server."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import uuid
import fcntl
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# 保证 scripts 根目录在 path 中（支持从仓库根目录运行）
_scripts_root = Path(__file__).resolve().parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))
_repo_root = _scripts_root.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

try:
    from flask import Flask, g, jsonify, request
except ImportError:
    print("Error: flask not installed. pip install flask", file=sys.stderr)
    sys.exit(1)

try:
    from werkzeug.exceptions import HTTPException
except ImportError:
    HTTPException = Exception

try:
    import redis
except ImportError:
    redis = None

try:
    from eth_utils import to_checksum_address
except ImportError:
    to_checksum_address = None

try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import xlrd
except ImportError:
    xlrd = None

from db import add_db_cli_args, configure_db_from_args, describe_db_target, dict_from_row, get_backend, get_connection, init_schema, DEFAULT_DB_PATH
from db.trade_v2 import (
    LEGACY_TRADES_TABLE,
    TRADE_V2_CORE_TABLE,
    compat_maker_asset_id_sql,
    compat_taker_asset_id_sql,
    get_address_history_source,
    get_trade_read_source,
    get_trade_stats_source,
    sql_identifier,
    uint256_storage_to_text,
)
from runtime.content_runtime import RuntimeContentProvider
from runtime import local_orderbook_websocket_watcher
from runtime.snapshot_store import SnapshotStore
from oracle.settlement_parser import parse_oracle_settlement_event
from api import cache as api_cache, db as api_db
from api.config import load_api_settings
from api.context import RouteContext, ServiceContext
from api.db_pool import build_api_connection_factory
from api.clients import market_data_client
from api.routes import register_blueprints
from api.services import address_service, auth_service, bootstrap_service, breaking_event_radar_service, briefing_service, clickhouse_orderfilled_service, content_service, cpi_release_calendar_service, crypto_funding_service, defi_token_watch_service, energy_gasoline_shock_service, f1_runtime_service, finance_panels_service, finance_watch_panels_service, food_retail_basket_service, geo_sanctions_shock_service, global_transport_shipping_service, global_weather_map_service, grid_esports_service, jin10_runtime_service, live_video_source_service, lob_service, macro_cpi_panels_service, macro_cpi_registry_service, market_group_service, market_quality_service, market_service, market_workspace_cache_service, natural_hazards, new_market_signal_service, polybeats_service, polymarket_macro_map_service, product_service, query_service, runtime_service, signal_service, sports_odds_service, system_service, tech_panels_service, weather_news_service, web_push_service, workspace_layout_service, world_cup_match_ops_service, worldcup_dashboard_service, worldcup_intel_service

app = Flask(__name__)
SETTINGS = load_api_settings()
DEPLOY_ROLE = SETTINGS.deploy_role
DB_PATH = SETTINGS.db_path
ALLOWED_ORIGINS = set(SETTINGS.allowed_origins)
DASHBOARD_CACHE_TTL_SECONDS = SETTINGS.dashboard_cache_ttl_seconds
MARKETS_CACHE_TTL_SECONDS = SETTINGS.markets_cache_ttl_seconds
BOOTSTRAP_CACHE_TTL_SECONDS = SETTINGS.bootstrap_cache_ttl_seconds
BOOTSTRAP_COMPONENT_TTL_SECONDS = SETTINGS.bootstrap_component_ttl_seconds
RECENT_TRADE_WINDOW = SETTINGS.recent_trade_window
ADDRESS_CACHE_TTL_SECONDS = SETTINGS.address_cache_ttl_seconds
REDIS_URL = SETTINGS.redis_url
REDIS_PREFIX = SETTINGS.redis_prefix
SNAPSHOT_SQLITE_PATH = SETTINGS.snapshot_sqlite_path
SNAPSHOT_PREWARM_ENABLED = SETTINGS.snapshot_prewarm_enabled
CLOB_API_BASE = SETTINGS.clob_api_base
CLOB_TIMEOUT_SECONDS = SETTINGS.clob_timeout_seconds
CLOB_PRICE_CACHE_TTL_SECONDS = SETTINGS.clob_price_cache_ttl_seconds
FINANCE_RUNTIME_TTL_SECONDS = SETTINGS.finance_runtime_ttl_seconds
SPORTS_RUNTIME_TTL_SECONDS = SETTINGS.sports_runtime_ttl_seconds
SIGNAL_RUNTIME_TTL_SECONDS = SETTINGS.signal_runtime_ttl_seconds
_dashboard_cache_lock = threading.Lock()
_dashboard_cache: Dict[str, Any] = {"value": None, "expires_at": 0.0}
_bootstrap_cache_lock = threading.Lock()
_bootstrap_cache: Dict[str, Any] = {"value": None, "expires_at": 0.0}
_markets_cache_lock = threading.Lock()
_markets_cache: Dict[str, Dict[str, Any]] = {}
_trade_index_cache_lock = threading.Lock()
_trade_index_cache: Dict[str, Any] = {"names": set(), "loaded_at": 0.0}
_redis_client = None
_redis_init_lock = threading.Lock()
_clob_session = None
_clob_session_lock = threading.Lock()
_clob_price_cache_lock = threading.Lock()
_api_connection_factory = build_api_connection_factory(get_connection, get_backend)
_clob_price_cache: Dict[str, Dict[str, Any]] = {}
TRADE_READ_SOURCE = sql_identifier(get_trade_read_source())
TRADE_STATS_SOURCE = sql_identifier(get_trade_stats_source())
ADDRESS_HISTORY_SOURCE = sql_identifier(get_address_history_source())
CONTENT_RUNTIME_PROVIDER = RuntimeContentProvider()
LOB_RUNTIME_MANAGER = lob_service.LocalOrderBookRuntimeManager(
    api_base=SETTINGS.clob_api_base,
    timeout_seconds=min(max(1, SETTINGS.clob_timeout_seconds), 3),
)
SNAPSHOT_STORE = SnapshotStore(SNAPSHOT_SQLITE_PATH)
_runtime_init_lock = threading.Lock()
_runtime_initialized = False
_snapshot_prewarm_owner_fd = None
_service_context_lock = threading.Lock()
_service_context: Optional[ServiceContext] = None
_route_context_lock = threading.Lock()
_route_context: Optional[RouteContext] = None


def _runtime_coordination_dir() -> Path:
    candidate = Path(SNAPSHOT_SQLITE_PATH).expanduser()
    base_dir = candidate.parent if candidate.parent != Path("") else Path("/tmp/polydata")
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _try_acquire_runtime_lock(lock_name: str):
    lock_path = _runtime_coordination_dir() / lock_name
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def _claim_startup_prewarm_slot() -> bool:
    cooldown_seconds = max(30, int(os.environ.get("POLYDATA_STARTUP_PREWARM_COOLDOWN_SECONDS", "300")))
    marker_path = _runtime_coordination_dir() / "startup-prewarm.marker"
    lock_path = _runtime_coordination_dir() / "startup-prewarm.marker.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        now = time.time()
        last_run = 0.0
        if marker_path.exists():
            try:
                payload = json.loads(marker_path.read_text(encoding="utf-8") or "{}")
                last_run = float(payload.get("last_run_ts") or 0.0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                last_run = 0.0
        if now - last_run < cooldown_seconds:
            app.logger.info(
                "startup-prewarm skip reason=cooldown pid=%s cooldown_seconds=%s age_seconds=%.2f",
                os.getpid(),
                cooldown_seconds,
                max(0.0, now - last_run),
            )
            return False
        marker_path.write_text(
            json.dumps({"last_run_ts": now, "pid": os.getpid()}),
            encoding="utf-8",
        )
        app.logger.info("startup-prewarm claim pid=%s cooldown_seconds=%s", os.getpid(), cooldown_seconds)
        return True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _claim_snapshot_prewarm_owner() -> bool:
    global _snapshot_prewarm_owner_fd
    if _snapshot_prewarm_owner_fd is not None:
        return True
    fd = _try_acquire_runtime_lock("snapshot-prewarm.worker.lock")
    if fd is None:
        app.logger.info("snapshot-prewarm thread-skip reason=lock-held pid=%s", os.getpid())
        return False
    _snapshot_prewarm_owner_fd = fd
    app.logger.info("snapshot-prewarm thread-owner pid=%s", os.getpid())
    return True


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    app.logger.propagate = False


configure_logging()


def create_app() -> Flask:
    auth_service.validate_runtime_config()
    web_push_service.validate_runtime_config()
    app.config["POLYDATA_SETTINGS"] = SETTINGS
    app.config["POLYDATA_API_HOST"] = SETTINGS.host
    app.config["POLYDATA_API_PORT"] = SETTINGS.port
    service_context = build_service_context()
    route_context = get_route_context()
    app.config["POLYDATA_SERVICE_CONTEXT"] = service_context
    app.config["POLYDATA_ROUTE_CONTEXT"] = route_context
    register_blueprints(app, route_context)
    return app


def api_readonly_enabled() -> bool:
    raw = os.environ.get("POLYDATA_API_READONLY", "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def build_route_context(service_context: ServiceContext) -> RouteContext:
    return RouteContext(services=service_context, capabilities={
        "AUTH_ALLOWED_SCOPES": tuple(sorted(auth_service.ALLOWED_SCOPES)),
        "MCP_ALLOWED_ORIGINS": tuple(sorted(ALLOWED_ORIGINS)),
        "auth_cookie_secure": auth_service.cookie_secure,
        "auth_enabled": auth_service.auth_enabled,
        "auth_login": auth_service.login,
        "auth_logout": auth_service.logout,
        "auth_request_metadata": auth_service.request_metadata,
        "authenticate_request": auth_service.authenticate_request,
        "authenticate_user_request": auth_service.authenticate_user_request,
        "get_product_watchlist": product_service.get_watchlist,
        "add_product_watchlist_market": product_service.add_watchlist_market,
        "remove_product_watchlist_market": product_service.remove_watchlist_market,
        "create_product_alert_rule": product_service.create_alert_rule,
        "delete_product_alert_rule": product_service.delete_alert_rule,
        "get_product_alert_events": product_service.get_alert_events,
        "mark_product_alert_read": product_service.mark_alert_read,
        "mark_all_product_alerts_read": product_service.mark_all_alerts_read,
        "get_product_notification_preferences": product_service.get_notification_preferences,
        "update_product_notification_preferences": product_service.update_notification_preferences,
        "get_product_web_push": web_push_service.get_status,
        "subscribe_product_web_push": web_push_service.upsert_subscription,
        "unsubscribe_product_web_push": web_push_service.revoke_subscription,
        "get_workspace_layout": workspace_layout_service.get_workspace_layout,
        "put_workspace_layout": workspace_layout_service.put_workspace_layout,
        "list_briefings": briefing_service.list_briefings,
        "create_briefing": briefing_service.create_briefing,
        "revoke_briefing": briefing_service.revoke_briefing,
        "get_public_briefing": briefing_service.get_public_briefing,
        "change_password": auth_service.change_password,
        "create_api_key": auth_service.create_api_key,
        "list_api_keys": auth_service.list_api_keys,
        "list_audit_log": auth_service.list_audit_log,
        "revoke_api_key": auth_service.revoke_api_key,
        "session_cookie_name": auth_service.session_cookie_name,
        "session_snapshot": auth_service.session_snapshot,
        "session_ttl_seconds": auth_service.session_ttl_seconds,
        "build_seed_health_payload": lambda: system_service.build_seed_health_payload(build_service_context()),
        "build_system_health_payload": build_system_health_payload,
        "app": app,
        "COMMODITY_SYMBOLS": COMMODITY_SYMBOLS,
        "CRYPTO_SYMBOLS": CRYPTO_SYMBOLS,
        "LOB_RUNTIME_MANAGER": LOB_RUNTIME_MANAGER,
        "describe_db_target": describe_db_target,
        "table_exists": table_exists,
        "enrich_market_rows_with_runtime_prices": enrich_market_rows_with_runtime_prices,
        "get_active_markets_snapshot": get_active_markets_snapshot,
        "get_active_addresses_cached": lambda days=30: address_service.get_active_addresses_cached(build_service_context(), days),
        "get_address_summary_cached": lambda address, days=30: address_service.get_address_summary_cached(build_service_context(), address, days),
        "get_address_trades_payload": lambda address, **kwargs: address_service.get_address_trades_payload(build_service_context(), address, **kwargs),
        "get_alpha_signal_snapshot": get_alpha_signal_snapshot,
        "get_breaking_event_radar_snapshot": lambda limit=12: breaking_event_radar_service.get_breaking_event_radar_snapshot(build_service_context(), limit=limit),
        "get_bootstrap_payload_cached": get_bootstrap_payload_cached,
        "get_cached_json": get_cached_json,
        "get_dashboard_payload_cached": get_dashboard_payload_cached,
        "get_crypto_funding_watch_snapshot": lambda limit=16: crypto_funding_service.get_crypto_funding_watch_snapshot(build_service_context(), limit=limit),
        "get_defi_token_watch_snapshot": lambda limit=10: defi_token_watch_service.get_defi_token_watch_snapshot(build_service_context(), limit=limit),
        "get_finance_watch_panel_snapshot": lambda panel_id, limit=10: finance_watch_panels_service.get_finance_watch_panel_snapshot(build_service_context(), panel_id, limit=limit),
        "get_tech_panel_snapshot": lambda panel_id, limit=10: tech_panels_service.get_tech_panel_snapshot(build_service_context(), panel_id, limit=limit),
        "get_cpi_release_calendar_snapshot": get_cpi_release_calendar_snapshot,
        "get_cpi_release_command_center_snapshot": get_cpi_release_command_center_snapshot,
        "get_cpi_components_pressure_registry_snapshot": get_cpi_components_pressure_registry_snapshot,
        "get_energy_gasoline_shock_snapshot": get_energy_gasoline_shock_snapshot,
        "get_fed_reaction_growth_risk_board_snapshot": get_fed_reaction_growth_risk_board_snapshot,
        "get_fed_rates_polymarket_gap_snapshot": get_fed_rates_polymarket_gap_snapshot,
        "get_finance_market_atlas_snapshot": lambda limit=16: finance_panels_service.get_finance_market_atlas_snapshot(build_service_context(), limit=limit),
        "get_equity_event_command_snapshot": lambda limit=12: finance_panels_service.get_equity_event_command_snapshot(build_service_context(), limit=limit),
        "get_onchain_tradfi_perp_radar_snapshot": lambda limit=12: finance_panels_service.get_onchain_tradfi_perp_radar_snapshot(build_service_context(), limit=limit),
        "get_finance_liquidity_regime_snapshot": lambda limit=12: finance_panels_service.get_finance_liquidity_regime_snapshot(build_service_context(), limit=limit),
        "get_food_retail_basket_snapshot": get_food_retail_basket_snapshot,
        "get_f1_panel_snapshot": get_f1_panel_snapshot,
        "get_geo_sanctions_shock_snapshot": get_geo_sanctions_shock_snapshot,
        "get_natural_hazards_snapshot": get_natural_hazards_snapshot,
        "get_natural_hazard_map_snapshot": get_natural_hazard_map_snapshot,
        "get_natural_hazard_event_detail": get_natural_hazard_event_detail,
        "get_natural_hazard_related_markets": get_natural_hazard_related_markets,
        "get_global_transport_shipping_snapshot": lambda limit=14: global_transport_shipping_service.get_global_transport_shipping_snapshot(build_service_context(), limit=limit),
        "get_aviation_viewport_snapshot": lambda bbox, zoom, limit=180: global_transport_shipping_service.get_aviation_viewport_snapshot(
            build_service_context(), bbox=bbox, zoom=zoom, limit=limit,
        ),
        "get_global_weather_map_snapshot": get_global_weather_map_snapshot,
        "get_grid_esports_snapshot": lambda limit=10: grid_esports_service.get_grid_esports_snapshot(build_service_context(), limit=limit),
        "get_sports_odds_snapshot": lambda limit=8: sports_odds_service.get_sports_odds_snapshot(build_service_context(), limit=limit),
        "get_growth_demand_recession_tracker_snapshot": get_growth_demand_recession_tracker_snapshot,
        "get_goods_tariff_supply_watch_snapshot": get_goods_tariff_supply_watch_snapshot,
        "get_inflation_nowcast_snapshot": get_inflation_nowcast_snapshot,
        "get_jin10_panel_snapshot": get_jin10_panel_snapshot,
        "get_labor_wage_services_pressure_snapshot": get_labor_wage_services_pressure_snapshot,
        "get_labor_services_inflation_monitor_snapshot": get_labor_services_inflation_monitor_snapshot,
        "get_latest_content_payload": lambda limit=8: content_service.get_latest_content_payload(build_service_context(), limit=limit),
        "get_market_tv_wire_snapshot": lambda limit=24, category=None: live_video_source_service.get_market_tv_wire_snapshot(build_service_context(), limit=limit, category=category),
        "get_market_youtube_channels_snapshot": lambda limit=12, category=None: live_video_source_service.get_market_youtube_channels_snapshot(build_service_context(), limit=limit, category=category),
        "get_runtime_content_latest": lambda limit=8: {
            "items": CONTENT_RUNTIME_PROVIDER.get_latest_items(limit=limit),
            "sourceMode": "runtime-rss",
        },
        "get_market_by_id": get_market_by_id,
        "get_market_by_slug": get_market_by_slug,
        "get_market_chart_payload": lambda market_id, range_name="1d", interval="5m": market_workspace_cache_service.get_market_chart_payload(
            build_service_context(),
            market_id,
            range_name=range_name,
            interval=interval,
        ),
        "get_gamma_active_market_filter": lambda: market_data_client.get_gamma_active_market_filter(build_service_context()),
        "get_market_detail_payload": lambda market_id: market_workspace_cache_service.get_market_detail_payload(build_service_context(), market_id),
        "get_market_data_quality_payload": lambda: market_quality_service.get_market_data_quality_payload(build_service_context()),
        "get_market_workspace_payload": lambda market_id: market_workspace_cache_service.get_market_workspace_payload(build_service_context(), market_id),
        "get_market_focus_tile_payload": lambda market_id: market_workspace_cache_service.get_market_focus_tile_payload(build_service_context(), market_id),
        "get_market_group_snapshot": get_market_group_snapshot,
        "get_market_groups_payload": lambda query="", page=1, page_size=80, sort="active": market_group_service.get_market_groups_payload(
            build_service_context(),
            query=query,
            page=page,
            page_size=page_size,
            sort=sort,
        ),
        "get_polymarket_macro_map_snapshot": get_polymarket_macro_map_snapshot,
        "get_shelter_rent_oer_pressure_snapshot": get_shelter_rent_oer_pressure_snapshot,
        "get_supply_tariff_import_watch_snapshot": get_supply_tariff_import_watch_snapshot,
        "get_market_group_detail_payload": lambda event_id: market_group_service.get_market_group_detail_payload(
            build_service_context(),
            event_id,
        ),
        "get_market_group_chart_payload": lambda event_id, range_name="1d": market_group_service.get_market_group_chart_payload(
            build_service_context(),
            event_id,
            range_name=range_name,
        ),
        "get_market_oracle_payload": lambda market_id: market_service.get_market_oracle_payload(build_service_context(), market_id),
        "get_markets_payload": lambda status="active", query="", page=1, page_size=20: market_service.get_markets_payload(
            build_service_context(),
            status=status,
            query=query,
            page=page,
            page_size=page_size,
        ),
        "get_market_price_summary": get_market_price_summary,
        "get_markets_payload_cached": get_markets_payload_cached,
        "get_nba_intel_snapshot": get_nba_intel_snapshot,
        "get_nba_matchup_predictor_snapshot": get_nba_matchup_predictor_snapshot,
        "get_nba_scoreboard_snapshot": get_nba_scoreboard_snapshot,
        "get_worldcup_core_snapshot": lambda: worldcup_dashboard_service.get_worldcup_core_snapshot(build_service_context()),
        "get_worldcup_dashboard_snapshot": lambda: worldcup_dashboard_service.get_worldcup_dashboard_snapshot(build_service_context()),
        "get_worldcup_live_snapshot": lambda: worldcup_dashboard_service.get_worldcup_live_snapshot(build_service_context()),
        "get_worldcup_panel_snapshot": lambda panel_id: worldcup_dashboard_service.get_worldcup_panel_snapshot(build_service_context(), panel_id),
        "get_world_cup_match_ops_snapshot": lambda limit=12: world_cup_match_ops_service.get_world_cup_match_ops_snapshot(build_service_context(), limit=limit),
        "get_worldcup_intel_snapshot": lambda limit=96: worldcup_intel_service.get_worldcup_intel_snapshot(build_service_context(), limit=limit),
        "get_new_market_signals_snapshot": get_new_market_signals_snapshot,
        "get_polybeats_snapshot": get_polybeats_snapshot,
        "get_oracle_events_by_market_id": get_oracle_events_by_market_id,
        "get_recent_oracle_snapshot": get_recent_oracle_snapshot,
        "get_recent_trades_snapshot": get_recent_trades_snapshot,
        "get_related_content_payload": lambda market_id, limit=8: content_service.get_related_content_payload(build_service_context(), market_id, limit=limit),
        "get_redis_client": get_redis_client,
        "get_runtime_lob_payload": lambda market_id: market_workspace_cache_service.get_market_orderbook_payload(build_service_context(), market_id),
        "get_runtime_lob_by_token_payload": lambda token_id, no_token_id="", market_title="", market_id=None: lob_service.get_runtime_lob_by_token_payload(
            build_service_context(),
            token_id,
            no_token_id=no_token_id,
            market_title=market_title,
            market_id=market_id,
        ),
        "get_lob_snapshots_by_token_payload": lambda token_id, side="", limit=48: lob_service.get_lob_snapshots_by_token_payload(
            build_service_context(),
            token_id,
            side=side,
            limit=limit,
        ),
        "get_lob_coverage_targets_payload": lambda limit=250, topics="": lob_service.get_lob_coverage_targets_payload(
            build_service_context(),
            limit=limit,
            topics=topics,
        ),
        "get_snapshot_payload": get_snapshot_payload,
        "SNAPSHOT_STORE": SNAPSHOT_STORE,
        "get_suspicious_trades_snapshot": get_suspicious_trades_snapshot,
        "get_top_addresses_cached": lambda days=None, limit=50: address_service.get_top_addresses_cached(build_service_context(), days, limit),
        "get_whale_trades_snapshot": get_whale_trades_snapshot,
        "get_weather_news_snapshot": get_weather_news_snapshot,
        "search_markets": lambda query, limit=10: market_service.search_markets(build_service_context(), query, limit=limit),
        "set_cached_json": set_cached_json,
        "get_trades_by_market_id": lambda market_id, limit=100, offset=0: market_workspace_cache_service.get_market_flow_rows(
            build_service_context(),
            market_id,
            limit=limit,
            offset=offset,
        ),
        "normalize_address": normalize_address,
        "normalize_market": normalize_market,
        "parse_json_list": parse_json_list,
        "query_all": query_all,
        "utc_now_iso": utc_now_iso,
    })


def get_route_context() -> RouteContext:
    global _route_context
    if _route_context is not None:
        return _route_context
    with _route_context_lock:
        if _route_context is None:
            _route_context = build_route_context(build_service_context())
    return _route_context


def build_service_context() -> ServiceContext:
    global _service_context
    if _service_context is not None:
        return _service_context
    with _service_context_lock:
        if _service_context is None:
            _service_context = _create_service_context()
    return _service_context


def _create_service_context() -> ServiceContext:
    capabilities = {
        "ADDRESS_CACHE_TTL_SECONDS": ADDRESS_CACHE_TTL_SECONDS,
        "ADDRESS_HISTORY_SOURCE": ADDRESS_HISTORY_SOURCE,
        "BOOTSTRAP_CACHE_TTL_SECONDS": BOOTSTRAP_CACHE_TTL_SECONDS,
        "BOOTSTRAP_COMPONENT_TTL_SECONDS": BOOTSTRAP_COMPONENT_TTL_SECONDS,
        "COMMODITY_SYMBOLS": COMMODITY_SYMBOLS,
        "CONTENT_RUNTIME_PROVIDER": CONTENT_RUNTIME_PROVIDER,
        "CLOB_API_BASE": CLOB_API_BASE,
        "CLOB_TIMEOUT_SECONDS": CLOB_TIMEOUT_SECONDS,
        "CRYPTO_COINGECKO_IDS": CRYPTO_COINGECKO_IDS,
        "CRYPTO_SYMBOLS": CRYPTO_SYMBOLS,
        "DB_PATH": DB_PATH,
        "DASHBOARD_CACHE_TTL_SECONDS": DASHBOARD_CACHE_TTL_SECONDS,
        "DEPLOY_ROLE": DEPLOY_ROLE,
        "FINANCE_RUNTIME_TTL_SECONDS": FINANCE_RUNTIME_TTL_SECONDS,
        "LEGACY_TRADES_TABLE": LEGACY_TRADES_TABLE,
        "LOB_RUNTIME_MANAGER": LOB_RUNTIME_MANAGER,
        "MARKETS_CACHE_TTL_SECONDS": MARKETS_CACHE_TTL_SECONDS,
        "REDIS_PREFIX": REDIS_PREFIX,
        "REDIS_URL": REDIS_URL,
        "RECENT_TRADE_WINDOW": RECENT_TRADE_WINDOW,
        "SETTINGS": SETTINGS,
        "SIGNAL_RUNTIME_TTL_SECONDS": SIGNAL_RUNTIME_TTL_SECONDS,
        "SNAPSHOT_PREWARM_ENABLED": SNAPSHOT_PREWARM_ENABLED,
        "SNAPSHOT_STORE": SNAPSHOT_STORE,
        "SPORTS_RUNTIME_TTL_SECONDS": SPORTS_RUNTIME_TTL_SECONDS,
        "TRADE_READ_SOURCE": TRADE_READ_SOURCE,
        "TRADE_V2_CORE_TABLE": TRADE_V2_CORE_TABLE,
        "_bootstrap_cache": _bootstrap_cache,
        "_bootstrap_cache_lock": _bootstrap_cache_lock,
        "_clob_price_cache": _clob_price_cache,
        "_clob_price_cache_lock": _clob_price_cache_lock,
        "_dashboard_cache": _dashboard_cache,
        "_dashboard_cache_lock": _dashboard_cache_lock,
        "_identifier_name": _identifier_name,
        "_markets_cache": _markets_cache,
        "_markets_cache_lock": _markets_cache_lock,
        "_redis_init_lock": _redis_init_lock,
        "_safe_decimal": _safe_decimal,
        "_safe_float": _safe_float,
        "_trade_index_cache": _trade_index_cache,
        "_trade_index_cache_lock": _trade_index_cache_lock,
        "app": app,
        "BeautifulSoup": BeautifulSoup,
        "build_system_health_payload": lambda: system_service.build_system_health_payload(build_service_context()),
        "build_seed_health_payload": lambda: system_service.build_seed_health_payload(build_service_context()),
        "build_market_status_case": build_market_status_case,
        "compat_maker_asset_id_sql": compat_maker_asset_id_sql,
        "compat_taker_asset_id_sql": compat_taker_asset_id_sql,
        "describe_db_target": describe_db_target,
        "dict_from_row": dict_from_row,
        "enrich_market_rows_with_runtime_prices": lambda rows, max_updates=18, force_refresh=False: market_service.enrich_market_rows_with_runtime_prices(
            build_service_context(),
            rows,
            max_updates=max_updates,
            force_refresh=force_refresh,
        ),
        "fetch_dashboard_market_status": fetch_dashboard_market_status,
        "fetch_dashboard_recent_markets": fetch_dashboard_recent_markets,
        "fetch_dashboard_trade_volume": fetch_dashboard_trade_volume,
        "fetch_recent_trade_window_bounds": fetch_recent_trade_window_bounds,
        "fetch_trade_count_estimate": fetch_trade_count_estimate,
        "format_trade_decimal": format_trade_decimal,
        "format_trade_address": format_trade_address,
        "get_active_markets_snapshot": lambda page_size=40, include_runtime_prices=False, include_change_24h=False: market_service.get_active_markets_snapshot(
            build_service_context(),
            page_size=page_size,
            include_runtime_prices=include_runtime_prices,
            include_change_24h=include_change_24h,
        ),
        "get_lob_runtime_status": local_orderbook_websocket_watcher.get_runtime_status,
        "get_lob_storage_status": lob_service.get_lob_storage_status,
        "get_alpha_signal_snapshot": get_alpha_signal_snapshot,
        "get_breaking_event_radar_snapshot": lambda limit=12: breaking_event_radar_service.get_breaking_event_radar_snapshot(build_service_context(), limit=limit),
        "get_bootstrap_component_cached": get_bootstrap_component_cached,
        "get_bootstrap_payload_cached": get_bootstrap_payload_cached,
        "get_cached_json": get_cached_json,
        "get_cached_runtime_payload": get_cached_runtime_payload,
        "get_crypto_funding_watch_snapshot": lambda limit=16: crypto_funding_service.get_crypto_funding_watch_snapshot(build_service_context(), limit=limit),
        "get_defi_token_watch_snapshot": lambda limit=10: defi_token_watch_service.get_defi_token_watch_snapshot(build_service_context(), limit=limit),
        "get_finance_watch_panel_snapshot": lambda panel_id, limit=10: finance_watch_panels_service.get_finance_watch_panel_snapshot(build_service_context(), panel_id, limit=limit),
        "get_tech_panel_snapshot": lambda panel_id, limit=10: tech_panels_service.get_tech_panel_snapshot(build_service_context(), panel_id, limit=limit),
        "get_cpi_release_calendar_snapshot": lambda limit=8: cpi_release_calendar_service.get_cpi_release_calendar_snapshot(build_service_context(), limit=limit),
        "get_cpi_release_command_center_snapshot": lambda limit=36: macro_cpi_registry_service.get_cpi_release_command_center_snapshot(build_service_context(), limit=limit),
        "get_cpi_components_pressure_registry_snapshot": lambda limit=36: macro_cpi_registry_service.get_cpi_components_pressure_registry_snapshot(build_service_context(), limit=limit),
        "get_energy_gasoline_shock_snapshot": lambda limit=6: energy_gasoline_shock_service.get_energy_gasoline_shock_snapshot(build_service_context(), limit=limit),
        "get_fed_reaction_growth_risk_board_snapshot": lambda limit=36: macro_cpi_registry_service.get_fed_reaction_growth_risk_board_snapshot(build_service_context(), limit=limit),
        "get_fed_rates_polymarket_gap_snapshot": lambda limit=8: macro_cpi_panels_service.get_fed_rates_polymarket_gap_snapshot(build_service_context(), limit=limit),
        "get_finance_market_atlas_snapshot": lambda limit=16: finance_panels_service.get_finance_market_atlas_snapshot(build_service_context(), limit=limit),
        "get_equity_event_command_snapshot": lambda limit=12: finance_panels_service.get_equity_event_command_snapshot(build_service_context(), limit=limit),
        "get_onchain_tradfi_perp_radar_snapshot": lambda limit=12: finance_panels_service.get_onchain_tradfi_perp_radar_snapshot(build_service_context(), limit=limit),
        "get_finance_liquidity_regime_snapshot": lambda limit=12: finance_panels_service.get_finance_liquidity_regime_snapshot(build_service_context(), limit=limit),
        "get_food_retail_basket_snapshot": lambda limit=8: food_retail_basket_service.get_food_retail_basket_snapshot(build_service_context(), limit=limit),
        "get_f1_panel_snapshot": lambda limit=10: f1_runtime_service.get_f1_panel_snapshot(build_service_context(), limit=limit),
        "get_geo_sanctions_shock_snapshot": lambda limit=geo_sanctions_shock_service.DEFAULT_ITEM_LIMIT: geo_sanctions_shock_service.get_geo_sanctions_shock_snapshot(build_service_context(), limit=limit),
        "get_natural_hazards_snapshot": lambda limit=natural_hazards.DEFAULT_EVENT_LIMIT: natural_hazards.get_natural_hazards_snapshot(
            build_service_context(),
            limit=limit,
            allow_provider_fetch=False,
        ),
        "get_natural_hazard_map_snapshot": lambda source, limit=natural_hazards.DEFAULT_EVENT_LIMIT, zoom=2.0, bbox=None: natural_hazards.get_natural_hazard_map_snapshot(
            build_service_context(),
            source=source,
            limit=limit,
            zoom=zoom,
            bbox=bbox,
        ),
        "get_natural_hazard_event_detail": lambda event_id: natural_hazards.get_natural_hazard_event_detail(
            build_service_context(),
            event_id=event_id,
        ),
        "get_natural_hazard_related_markets": lambda event_id, limit=natural_hazards.DEFAULT_RELATED_MARKET_LIMIT: get_natural_hazard_related_markets(event_id=event_id, limit=limit),
        "get_global_transport_shipping_snapshot": lambda limit=14: global_transport_shipping_service.get_global_transport_shipping_snapshot(build_service_context(), limit=limit),
        "get_global_weather_map_snapshot": lambda limit=34: global_weather_map_service.get_global_weather_map_snapshot(build_service_context(), limit=limit),
        "get_grid_esports_snapshot": lambda limit=10: grid_esports_service.get_grid_esports_snapshot(build_service_context(), limit=limit),
        "get_sports_odds_snapshot": lambda limit=8: sports_odds_service.get_sports_odds_snapshot(build_service_context(), limit=limit),
        "get_growth_demand_recession_tracker_snapshot": lambda limit=8: macro_cpi_panels_service.get_growth_demand_recession_tracker_snapshot(build_service_context(), limit=limit),
        "get_goods_tariff_supply_watch_snapshot": lambda limit=36: macro_cpi_registry_service.get_goods_tariff_supply_watch_snapshot(build_service_context(), limit=limit),
        "get_existing_trade_read_source": get_existing_trade_read_source,
        "get_gamma_active_market_filter": lambda: market_data_client.get_gamma_active_market_filter(build_service_context()),
        "get_latest_content_snapshot": get_latest_content_snapshot,
        "get_market_tv_wire_snapshot": lambda limit=24, category=None: live_video_source_service.get_market_tv_wire_snapshot(build_service_context(), limit=limit, category=category),
        "get_market_youtube_channels_snapshot": lambda limit=12, category=None: live_video_source_service.get_market_youtube_channels_snapshot(build_service_context(), limit=limit, category=category),
        "get_market_by_id": lambda market_id: market_service.get_market_by_id(build_service_context(), market_id),
        "get_market_chart_payload": lambda market_id, range_name="1d", interval="5m": market_service.get_market_chart_payload(build_service_context(), market_id, range_name=range_name, interval=interval),
        "get_market_focus_tile_payload": lambda market_id: market_workspace_cache_service.get_market_focus_tile_payload(
            build_service_context(),
            market_id,
        ),
        "get_market_clob_price_series": lambda market, range_name="1d", interval="5m": market_data_client.get_market_clob_price_series(
            build_service_context(),
            market,
            range_name=range_name,
            interval=interval,
        ),
        "get_market_clob_price_snapshot": lambda market: market_data_client.get_market_clob_price_snapshot(build_service_context(), market),
        "get_market_group_snapshot": lambda items, kind: runtime_service.get_market_group_snapshot(build_service_context(), items, kind=kind),
        "get_market_groups_payload": lambda query="", page=1, page_size=80, sort="active": market_group_service.get_market_groups_payload(
            build_service_context(),
            query=query,
            page=page,
            page_size=page_size,
            sort=sort,
        ),
        "get_polymarket_macro_map_snapshot": lambda limit=12: polymarket_macro_map_service.get_polymarket_macro_map_snapshot(build_service_context(), limit=limit),
        "get_market_group_detail_payload": lambda event_id: market_group_service.get_market_group_detail_payload(
            build_service_context(),
            event_id,
        ),
        "get_market_group_chart_payload": lambda event_id, range_name="1d": market_group_service.get_market_group_chart_payload(
            build_service_context(),
            event_id,
            range_name=range_name,
        ),
        "get_polybeats_snapshot": get_polybeats_snapshot,
        "get_market_by_slug": lambda slug: market_service.get_market_by_slug(build_service_context(), slug),
        "get_market_oracle_payload": lambda market_id: market_service.get_market_oracle_payload(build_service_context(), market_id),
        "get_market_price_summary": lambda market_id: market_service.get_market_price_summary(build_service_context(), market_id),
        "get_markets_payload_cached": get_markets_payload_cached,
        "get_markets_payload": lambda status="active", query="", page=1, page_size=20: market_service.get_markets_payload(
            build_service_context(),
            status=status,
            query=query,
            page=page,
            page_size=page_size,
        ),
        "get_inflation_nowcast_snapshot": lambda: runtime_service.get_inflation_nowcast_snapshot(build_service_context()),
        "get_jin10_panel_snapshot": lambda limit=24: jin10_runtime_service.get_jin10_panel_snapshot(build_service_context(), limit=limit),
        "get_labor_wage_services_pressure_snapshot": lambda limit=8: macro_cpi_panels_service.get_labor_wage_services_pressure_snapshot(build_service_context(), limit=limit),
        "get_labor_services_inflation_monitor_snapshot": lambda limit=36: macro_cpi_registry_service.get_labor_services_inflation_monitor_snapshot(build_service_context(), limit=limit),
        "get_nba_intel_snapshot": lambda limit=12: runtime_service.get_nba_intel_snapshot(build_service_context(), limit=limit),
        "get_nba_matchup_predictor_snapshot": lambda limit=8: runtime_service.get_nba_matchup_predictor_snapshot(build_service_context(), limit=limit),
        "get_nba_scoreboard_snapshot": lambda limit=10: runtime_service.get_nba_scoreboard_snapshot(build_service_context(), limit=limit),
        "get_new_market_signals_snapshot": lambda limit=12: new_market_signal_service.get_new_market_signals_snapshot(build_service_context(), limit=limit),
        "get_oracle_events_by_market_id": lambda market_id: market_service.get_oracle_events_by_market_id(build_service_context(), market_id),
        "get_recent_oracle_events": get_recent_oracle_events,
        "get_recent_oracle_snapshot": lambda limit=24: market_service.get_recent_oracle_snapshot(build_service_context(), limit=limit),
        "get_recent_trades": lambda limit=24: query_service.get_recent_trades(build_service_context(), limit=limit),
        "get_recent_trades_snapshot": lambda limit=24: market_service.get_recent_trades_snapshot(build_service_context(), limit=limit),
        "get_redis_client": get_redis_client,
        "get_related_content_by_market_id": lambda market_id, limit=8: query_service.get_related_content_by_market_id(build_service_context(), market_id, limit=limit),
        "get_runtime_lob_payload": lambda market_id: lob_service.get_runtime_lob_payload(build_service_context(), market_id),
        "get_runtime_lob_by_token_payload": lambda token_id, no_token_id="", market_title="", market_id=None: lob_service.get_runtime_lob_by_token_payload(
            build_service_context(),
            token_id,
            no_token_id=no_token_id,
            market_title=market_title,
            market_id=market_id,
        ),
        "get_lob_snapshots_by_token_payload": lambda token_id, side="", limit=48: lob_service.get_lob_snapshots_by_token_payload(
            build_service_context(),
            token_id,
            side=side,
            limit=limit,
        ),
        "get_lob_coverage_targets_payload": lambda limit=250, topics="": lob_service.get_lob_coverage_targets_payload(
            build_service_context(),
            limit=limit,
            topics=topics,
        ),
        "get_snapshot_payload": get_snapshot_payload,
        "get_shelter_rent_oer_pressure_snapshot": lambda limit=8: macro_cpi_panels_service.get_shelter_rent_oer_pressure_snapshot(build_service_context(), limit=limit),
        "get_suspicious_trades_snapshot": get_suspicious_trades_snapshot,
        "get_supply_tariff_import_watch_snapshot": lambda limit=8: macro_cpi_panels_service.get_supply_tariff_import_watch_snapshot(build_service_context(), limit=limit),
        "get_trade_derived_market_price_series": get_trade_derived_market_price_series,
        "get_trade_market_projection_sql": get_trade_market_projection_sql,
        "get_trades_by_market_id": lambda market_id, limit=100, offset=0: market_service.get_trades_by_market_id(build_service_context(), market_id, limit=limit, offset=offset),
        "get_whale_trades_snapshot": get_whale_trades_snapshot,
        "get_weather_news_snapshot": lambda limit=24: weather_news_service.get_weather_news_snapshot(build_service_context(), limit=limit),
        "get_world_cup_match_ops_snapshot": lambda limit=12: world_cup_match_ops_service.get_world_cup_match_ops_snapshot(build_service_context(), limit=limit),
        "get_worldcup_dashboard_snapshot": lambda: worldcup_dashboard_service.get_worldcup_dashboard_snapshot(build_service_context()),
        "get_yahoo_market_snapshot": lambda symbol, interval="30m", range_name="5d", ttl_seconds=None: market_data_client.get_yahoo_market_snapshot(
            build_service_context(),
            symbol,
            interval=interval,
            range_name=range_name,
            ttl_seconds=ttl_seconds,
        ),
        "http_json_get": lambda url, params=None, timeout=12, headers=None: market_data_client.http_json_get(
            build_service_context(),
            url,
            params=params,
            timeout=timeout,
            headers=headers,
        ),
        "http_form_post": http_form_post,
        "http_text_get": http_text_get,
        "http_bytes_get": http_bytes_get,
        "xlrd": xlrd,
        "iso_days_before": iso_days_before,
        "get_connection": _api_connection_factory,
        "get_redis_client_state": lambda: _redis_client,
        "normalize_market": normalize_market,
        "normalize_address": normalize_address,
        "normalize_oracle_event": normalize_oracle_event,
        "normalize_trade": normalize_trade,
        "parse_iso_datetime": parse_iso_datetime,
        "parse_json_list": parse_json_list,
        "query_all": query_all,
        "query_one": query_one,
        "redis_module": redis,
        "requests": requests,
        "search_markets": lambda query, limit=10: market_service.search_markets(build_service_context(), query, limit=limit),
        "set_redis_client_state": lambda value: globals().__setitem__("_redis_client", value),
        "set_cached_json": set_cached_json,
        "set_cached_runtime_payload": set_cached_runtime_payload,
        "sql_identifier": sql_identifier,
        "table_exists": table_exists,
        "threading": threading,
        "utc_date_days_ago": utc_date_days_ago,
        "utc_now_iso": utc_now_iso,
        "get_backend": get_backend,
        "get_clob_session": get_clob_session,
    }
    return ServiceContext(
        application=app,
        settings=SETTINGS,
        database_path=DB_PATH,
        content_runtime_provider=CONTENT_RUNTIME_PROVIDER,
        lob_runtime_manager=LOB_RUNTIME_MANAGER,
        snapshot_store=SNAPSHOT_STORE,
        capabilities=capabilities,
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    text = value.strip()
    if not text:
        return None
    normalized = text.replace(" UTC", "Z").replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def iso_days_before(anchor: Optional[str], days: int) -> Optional[str]:
    parsed = parse_iso_datetime(anchor)
    if parsed is None:
        return None
    return (parsed - timedelta(days=days)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_json_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
        except Exception:
            return [item.strip() for item in text.split(",") if item.strip()]
    return [value]


def get_f1_panel_snapshot(limit: int = 10) -> Dict[str, Any]:
    return f1_runtime_service.get_f1_panel_snapshot(build_service_context(), limit=limit)


def normalize_address(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def format_trade_decimal(value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return ""
    try:
        normalized = format(Decimal(text), "f")
    except (InvalidOperation, ValueError, TypeError):
        return value
    if "." not in normalized:
        return normalized
    return normalized.rstrip("0").rstrip(".")


def format_trade_address(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if len(raw) == 20:
            text = "0x" + raw.hex()
        else:
            return value
    else:
        text = str(value or "").strip()
    if not text.startswith("0x") or len(text) != 42:
        return value
    lowered = "0x" + text[2:].lower()
    if to_checksum_address is None:
        return lowered
    try:
        return to_checksum_address(lowered)
    except Exception:
        return lowered


def utc_date_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()


def get_redis_client():
    return api_cache.get_redis_client(build_service_context())


def get_clob_session():
    global _clob_session
    if requests is None:
        return None
    if _clob_session is not None:
        return _clob_session
    with _clob_session_lock:
        if _clob_session is not None:
            return _clob_session
        session = requests.Session()
        session.trust_env = str(os.environ.get("POLYDATA_CLOB_TRUST_ENV_PROXY") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "polyData-api/1.0",
            }
        )
        _clob_session = session
        return _clob_session


def parse_interval_minutes(interval: str) -> int:
    text = str(interval or "5m").strip().lower()
    match = re.fullmatch(r"(\d+)(m|h|d)", text)
    if not match:
        return 5
    value = max(1, int(match.group(1)))
    unit = match.group(2)
    if unit == "m":
        return value
    if unit == "h":
        return value * 60
    return value * 1440


def range_to_seconds(range_name: str) -> int:
    normalized = str(range_name or "1d").strip().lower()
    mapping = {
        "1h": 3600,
        "6h": 21600,
        "12h": 43200,
        "1d": 86400,
        "3d": 259200,
        "7d": 604800,
        "30d": 2592000,
    }
    return mapping.get(normalized, 86400)


def get_cached_runtime_payload(namespace: str, cache_key: str) -> Optional[Any]:
    return api_cache.get_cached_runtime_payload(build_service_context(), namespace, cache_key)


def set_cached_runtime_payload(namespace: str, cache_key: str, payload: Any, ttl_seconds: int = CLOB_PRICE_CACHE_TTL_SECONDS) -> Any:
    return api_cache.set_cached_runtime_payload(build_service_context(), namespace, cache_key, payload, ttl_seconds)


def _redis_key(namespace: str, cache_key: str) -> str:
    return api_cache._redis_key(build_service_context(), namespace, cache_key)


def get_cached_payload(namespace: str, cache_key: str) -> Optional[Any]:
    return api_cache.get_cached_payload(build_service_context(), namespace, cache_key)


def set_cached_payload(namespace: str, cache_key: str, payload: Any, ttl_seconds: int) -> None:
    api_cache.set_cached_payload(build_service_context(), namespace, cache_key, payload, ttl_seconds)


def get_cached_json(namespace: str, cache_key: str) -> Optional[Dict[str, Any]]:
    return api_cache.get_cached_json(build_service_context(), namespace, cache_key)


def set_cached_json(namespace: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int) -> None:
    api_cache.set_cached_json(build_service_context(), namespace, cache_key, payload, ttl_seconds)


def get_snapshot_payload(namespace: str, cache_key: str, builder, *, ttl_seconds: int) -> Any:
    return api_cache.get_snapshot_payload(build_service_context(), namespace, cache_key, builder, ttl_seconds=ttl_seconds)


def http_json_get(url: str, *, params: Optional[Dict[str, Any]] = None, timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> Any:
    return market_data_client.http_json_get(build_service_context(), url, params=params, timeout=timeout, headers=headers)


def http_form_post(url: str, *, data: Dict[str, Any], timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> Any:
    if requests is None:
        raise RuntimeError("requests is not installed")
    session = requests.Session()
    session.trust_env = str(os.environ.get("POLYDATA_API_HTTP_TRUST_ENV_PROXY") or "").strip().lower() in {"1", "true", "yes", "on"}
    try:
        response = session.post(url, data=data, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.json()
    finally:
        session.close()


def http_text_get(url: str, *, timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> str:
    if requests is None:
        raise RuntimeError("requests is not installed")
    session = requests.Session()
    session.trust_env = str(os.environ.get("POLYDATA_API_HTTP_TRUST_ENV_PROXY") or "").strip().lower() in {"1", "true", "yes", "on"}
    try:
        response = session.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.text
    finally:
        session.close()


def http_bytes_get(url: str, *, timeout: int = 12, headers: Optional[Dict[str, str]] = None) -> bytes:
    if requests is None:
        raise RuntimeError("requests is not installed")
    session = requests.Session()
    session.trust_env = str(os.environ.get("POLYDATA_API_HTTP_TRUST_ENV_PROXY") or "").strip().lower() in {"1", "true", "yes", "on"}
    try:
        response = session.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.content
    finally:
        session.close()


def _safe_decimal(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_percent_text(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return format_trade_decimal(value)


COMMODITY_SYMBOLS = [
    ("vix", "VIX", "^VIX"),
    ("gold", "GOLD", "GC=F"),
    ("silver", "SILVER", "SI=F"),
    ("copper", "COPPER", "HG=F"),
    ("platinum", "PLATINUM", "PL=F"),
    ("palladium", "PALLADIUM", "PA=F"),
    ("aluminum", "ALUMINUM", "ALI=F"),
    ("oil", "OIL", "CL=F"),
    ("brent", "BRENT", "BZ=F"),
    ("natgas", "NATGAS", "NG=F"),
    ("ttf", "TTF GAS", "TTF=F"),
    ("gasoline", "GASOLINE", "RB=F"),
    ("heating-oil", "HEATING OIL", "HO=F"),
    ("uranium", "URANIUM", "URA"),
    ("lithium", "LITHIUM", "LIT"),
    ("coal", "COAL", "MTF=F"),
    ("wheat", "WHEAT", "ZW=F"),
    ("corn", "CORN", "ZC=F"),
    ("soybeans", "SOYBEANS", "ZS=F"),
    ("rice", "RICE", "ZR=F"),
    ("coffee", "COFFEE", "KC=F"),
    ("sugar", "SUGAR", "SB=F"),
    ("cocoa", "COCOA", "CC=F"),
    ("cotton", "COTTON", "CT=F"),
    ("eurusd", "EUR/USD", "EURUSD=X"),
    ("gbpusd", "GBP/USD", "GBPUSD=X"),
    ("usdjpy", "USD/JPY", "USDJPY=X"),
    ("usdcny", "USD/CNY", "USDCNY=X"),
    ("usdinr", "USD/INR", "USDINR=X"),
    ("audusd", "AUD/USD", "AUDUSD=X"),
    ("usdchf", "USD/CHF", "USDCHF=X"),
    ("usdcad", "USD/CAD", "USDCAD=X"),
    ("usdtry", "USD/TRY", "USDTRY=X"),
]

CRYPTO_SYMBOLS = [
    ("btc", "BTC", "BTC-USD"),
    ("eth", "ETH", "ETH-USD"),
    ("sol", "SOL", "SOL-USD"),
    ("doge", "DOGE", "DOGE-USD"),
    ("bnb", "BNB", "BNB-USD"),
    ("xrp", "XRP", "XRP-USD"),
    ("ada", "ADA", "ADA-USD"),
    ("avax", "AVAX", "AVAX-USD"),
    ("link", "LINK", "LINK-USD"),
    ("ltc", "LTC", "LTC-USD"),
    ("dot", "DOT", "DOT-USD"),
    ("trx", "TRX", "TRX-USD"),
    ("bch", "BCH", "BCH-USD"),
]

CRYPTO_COINGECKO_IDS = {
    "BTC-USD": "bitcoin",
    "ETH-USD": "ethereum",
    "SOL-USD": "solana",
    "DOGE-USD": "dogecoin",
    "BNB-USD": "binancecoin",
    "XRP-USD": "ripple",
    "ADA-USD": "cardano",
    "AVAX-USD": "avalanche-2",
    "LINK-USD": "chainlink",
    "LTC-USD": "litecoin",
    "DOT-USD": "polkadot",
    "TRX-USD": "tron",
    "BCH-USD": "bitcoin-cash",
}


def get_yahoo_market_snapshot(
    symbol: str,
    *,
    interval: str = "30m",
    range_name: str = "5d",
    ttl_seconds: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    return market_data_client.get_yahoo_market_snapshot(
        build_service_context(),
        symbol,
        interval=interval,
        range_name=range_name,
        ttl_seconds=ttl_seconds,
    )


def get_market_group_snapshot(items: List[tuple[str, str, str]], *, kind: str) -> Dict[str, Any]:
    return runtime_service.get_market_group_snapshot(build_service_context(), items, kind=kind)


def get_nba_scoreboard_snapshot(limit: int = 10) -> Dict[str, Any]:
    return runtime_service.get_nba_scoreboard_snapshot(build_service_context(), limit=limit)


def get_nba_intel_snapshot(limit: int = 12) -> Dict[str, Any]:
    return runtime_service.get_nba_intel_snapshot(build_service_context(), limit=limit)


def get_nba_matchup_predictor_snapshot(limit: int = 8) -> Dict[str, Any]:
    return runtime_service.get_nba_matchup_predictor_snapshot(build_service_context(), limit=limit)


def get_inflation_nowcast_snapshot() -> Dict[str, Any]:
    return runtime_service.get_inflation_nowcast_snapshot(build_service_context())


def get_geo_sanctions_shock_snapshot(limit: int = geo_sanctions_shock_service.DEFAULT_ITEM_LIMIT) -> Dict[str, Any]:
    return geo_sanctions_shock_service.get_geo_sanctions_shock_snapshot(build_service_context(), limit=limit)


def get_natural_hazards_snapshot(limit: int = natural_hazards.DEFAULT_EVENT_LIMIT) -> Dict[str, Any]:
    return natural_hazards.get_natural_hazards_snapshot(
        build_service_context(),
        limit=limit,
        allow_provider_fetch=False,
    )


def get_natural_hazard_map_snapshot(
    source: str,
    limit: int = natural_hazards.DEFAULT_EVENT_LIMIT,
    zoom: float = 2.0,
    bbox: tuple[float, float, float, float] | None = None,
) -> Dict[str, Any]:
    return natural_hazards.get_natural_hazard_map_snapshot(
        build_service_context(),
        source=source,
        limit=limit,
        zoom=zoom,
        bbox=bbox,
    )


def get_natural_hazard_event_detail(event_id: str) -> Dict[str, Any] | None:
    return natural_hazards.get_natural_hazard_event_detail(
        build_service_context(),
        event_id=event_id,
    )


def get_polymarket_macro_map_snapshot(limit: int = 12) -> Dict[str, Any]:
    return polymarket_macro_map_service.get_polymarket_macro_map_snapshot(build_service_context(), limit=limit)


def get_cpi_release_calendar_snapshot(limit: int = 8) -> Dict[str, Any]:
    return cpi_release_calendar_service.get_cpi_release_calendar_snapshot(build_service_context(), limit=limit)


def get_cpi_release_command_center_snapshot(limit: int = 36) -> Dict[str, Any]:
    return macro_cpi_registry_service.get_cpi_release_command_center_snapshot(build_service_context(), limit=limit)


def get_cpi_components_pressure_registry_snapshot(limit: int = 36) -> Dict[str, Any]:
    return macro_cpi_registry_service.get_cpi_components_pressure_registry_snapshot(build_service_context(), limit=limit)


def get_goods_tariff_supply_watch_snapshot(limit: int = 36) -> Dict[str, Any]:
    return macro_cpi_registry_service.get_goods_tariff_supply_watch_snapshot(build_service_context(), limit=limit)


def get_labor_services_inflation_monitor_snapshot(limit: int = 36) -> Dict[str, Any]:
    return macro_cpi_registry_service.get_labor_services_inflation_monitor_snapshot(build_service_context(), limit=limit)


def get_fed_reaction_growth_risk_board_snapshot(limit: int = 36) -> Dict[str, Any]:
    return macro_cpi_registry_service.get_fed_reaction_growth_risk_board_snapshot(build_service_context(), limit=limit)


def get_energy_gasoline_shock_snapshot(limit: int = 6) -> Dict[str, Any]:
    return energy_gasoline_shock_service.get_energy_gasoline_shock_snapshot(build_service_context(), limit=limit)


def get_global_weather_map_snapshot(limit: int = 34) -> Dict[str, Any]:
    return global_weather_map_service.get_global_weather_map_snapshot(build_service_context(), limit=limit)


def get_natural_hazard_related_markets(
    event_id: str,
    limit: int = natural_hazards.DEFAULT_RELATED_MARKET_LIMIT,
) -> Dict[str, Any] | None:
    hazards = natural_hazards.get_natural_hazards_snapshot(
        build_service_context(),
        limit=natural_hazards.DEFAULT_EVENT_LIMIT,
        allow_provider_fetch=False,
    )
    weather_markets = global_weather_map_service.get_global_weather_map_snapshot(
        build_service_context(),
        limit=100,
        allow_live_build=False,
    )
    return natural_hazards.related_weather_markets_snapshot(
        event_id=event_id,
        natural_hazards_payload=hazards,
        weather_map_payload=weather_markets,
        limit=limit,
    )


def get_weather_news_snapshot(limit: int = 24) -> Dict[str, Any]:
    return weather_news_service.get_weather_news_snapshot(build_service_context(), limit=limit)


def get_food_retail_basket_snapshot(limit: int = 8) -> Dict[str, Any]:
    return food_retail_basket_service.get_food_retail_basket_snapshot(build_service_context(), limit=limit)


def get_supply_tariff_import_watch_snapshot(limit: int = 8) -> Dict[str, Any]:
    return macro_cpi_panels_service.get_supply_tariff_import_watch_snapshot(build_service_context(), limit=limit)


def get_shelter_rent_oer_pressure_snapshot(limit: int = 8) -> Dict[str, Any]:
    return macro_cpi_panels_service.get_shelter_rent_oer_pressure_snapshot(build_service_context(), limit=limit)


def get_labor_wage_services_pressure_snapshot(limit: int = 8) -> Dict[str, Any]:
    return macro_cpi_panels_service.get_labor_wage_services_pressure_snapshot(build_service_context(), limit=limit)


def get_growth_demand_recession_tracker_snapshot(limit: int = 8) -> Dict[str, Any]:
    return macro_cpi_panels_service.get_growth_demand_recession_tracker_snapshot(build_service_context(), limit=limit)


def get_fed_rates_polymarket_gap_snapshot(limit: int = 8) -> Dict[str, Any]:
    return macro_cpi_panels_service.get_fed_rates_polymarket_gap_snapshot(build_service_context(), limit=limit)


def get_jin10_panel_snapshot(limit: int = 24) -> Dict[str, Any]:
    return jin10_runtime_service.get_jin10_panel_snapshot(build_service_context(), limit=limit)


def get_new_market_signals_snapshot(limit: int = 12) -> Dict[str, Any]:
    return new_market_signal_service.get_new_market_signals_snapshot(build_service_context(), limit=limit)


def get_whale_trades_snapshot(limit: int = 14, lookback_days: int = 7) -> Dict[str, Any]:
    return signal_service.get_whale_trades_snapshot(build_service_context(), limit=limit, lookback_days=lookback_days)


def get_suspicious_trades_snapshot(limit: int = 12) -> Dict[str, Any]:
    return signal_service.get_suspicious_trades_snapshot(build_service_context(), limit=limit)


def get_alpha_signal_snapshot(limit: int = 8) -> Dict[str, Any]:
    return signal_service.get_alpha_signal_snapshot(build_service_context(), limit=limit)


def get_polybeats_snapshot(limit: int = 8) -> Dict[str, Any]:
    return polybeats_service.get_polybeats_snapshot(build_service_context(), limit=limit)


def query_all(sql: str, params: Optional[Iterable[Any]] = None) -> List[Dict[str, Any]]:
    return api_db.query_all(build_service_context(), sql, params)


def query_one(sql: str, params: Optional[Iterable[Any]] = None) -> Dict[str, Any]:
    return api_db.query_one(build_service_context(), sql, params)


def table_exists(table_name: str) -> bool:
    return api_db.table_exists(build_service_context(), table_name)


def _identifier_name(identifier: str) -> str:
    return api_db.identifier_name(identifier)


def get_existing_trade_read_source() -> Optional[str]:
    return api_db.get_existing_trade_read_source(build_service_context())


def get_trades_index_names(force_refresh: bool = False) -> set[str]:
    return api_db.get_trades_index_names(build_service_context(), force_refresh=force_refresh)


@app.before_request
def log_request_start() -> None:
    g.request_started_at = time.perf_counter()
    g.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    app.logger.info(
        "request-start request_id=%s method=%s path=%s query=%s remote=%s",
        g.request_id,
        request.method,
        request.path,
        request.query_string.decode("utf-8", errors="replace"),
        request.headers.get("X-Forwarded-For", request.remote_addr),
    )


@app.after_request
def log_request_end(response):
    request_id = getattr(g, "request_id", "-")
    started_at = getattr(g, "request_started_at", None)
    duration_ms = (time.perf_counter() - started_at) * 1000 if started_at else -1
    response.headers["X-Request-ID"] = request_id
    origin = request.headers.get("Origin", "").strip()
    if origin and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept, X-Requested-With, X-CSRF-Token, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.path == "/mcp" or request.path.startswith("/auth/") or request.path.startswith("/product/") or request.path.startswith("/briefings/") or request.path.startswith("/system/") or request.path.startswith("/runtime/system/"):
        response.headers["Cache-Control"] = "no-store"
    app.logger.info(
        "request-end request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.path,
        response.status_code,
        duration_ms,
    )
    return response


@app.errorhandler(HTTPException)
def handle_http_exception(error: HTTPException):
    request_id = getattr(g, "request_id", "-")
    app.logger.warning(
        "http-error request_id=%s method=%s path=%s status=%s detail=%s",
        request_id,
        request.method,
        request.path,
        getattr(error, "code", 500),
        getattr(error, "description", str(error)),
    )
    return jsonify({"error": getattr(error, "description", "HTTP error"), "requestId": request_id}), getattr(error, "code", 500)


@app.errorhandler(auth_service.AuthError)
def handle_auth_error(error: auth_service.AuthError):
    request_id = getattr(g, "request_id", "-")
    app.logger.warning(
        "auth-error request_id=%s method=%s path=%s status=%s code=%s",
        request_id,
        request.method,
        request.path,
        error.status_code,
        error.code,
    )
    response = jsonify(
        {
            "error": {"code": error.code, "message": error.message},
            "requestId": request_id,
        }
    )
    response.status_code = error.status_code
    if error.retry_after:
        response.headers["Retry-After"] = str(error.retry_after)
    if error.status_code == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="polymonitor"'
    return response


@app.errorhandler(Exception)
def handle_unexpected_exception(error: Exception):
    request_id = getattr(g, "request_id", "-")
    app.logger.exception(
        "unhandled-error request_id=%s method=%s path=%s error=%s",
        request_id,
        request.method,
        request.path,
        error,
    )
    return jsonify({"error": "Internal server error", "requestId": request_id}), 500


def build_market_status_case(now_iso: str) -> str:
    return (
        "CASE "
        "WHEN EXISTS (SELECT 1 FROM market_status_snapshot mss WHERE mss.market_id = m.id AND COALESCE(mss.is_final, FALSE) = TRUE) THEN 'Settled' "
        "WHEN EXISTS (SELECT 1 FROM market_status_snapshot mss WHERE mss.market_id = m.id AND COALESCE(mss.completion_status, '') = 'DISPUTED') THEN 'Disputed' "
        "WHEN EXISTS (SELECT 1 FROM market_status_snapshot mss WHERE mss.market_id = m.id AND (COALESCE(mss.has_settle, FALSE) = TRUE OR mss.settlement_code IN (1, 2, 3))) THEN 'Settled' "
        "WHEN EXISTS (SELECT 1 FROM market_status_snapshot mss WHERE mss.market_id = m.id AND COALESCE(mss.has_propose, FALSE) = TRUE) THEN 'Proposed' "
        "WHEN EXISTS (SELECT 1 FROM market_status_snapshot mss WHERE mss.market_id = m.id AND COALESCE(mss.is_trading_closed, FALSE) = TRUE) THEN 'Closed' "
        "WHEN m.end_date IS NOT NULL AND m.end_date < ? THEN 'Closed' "
        "ELSE 'Active' END"
    )


def fetch_dashboard_market_status(now_iso: str) -> List[Dict[str, Any]]:
    return query_service.fetch_dashboard_market_status(build_service_context(), now_iso)


def fetch_recent_trade_window_bounds(window_size: int) -> Dict[str, Any]:
    return query_service.fetch_recent_trade_window_bounds(build_service_context(), window_size)


def fetch_dashboard_trade_volume(window_size: int) -> List[Dict[str, Any]]:
    return query_service.fetch_dashboard_trade_volume(build_service_context(), window_size)


def fetch_dashboard_recent_markets(now_iso: str, window_size: int) -> List[Dict[str, Any]]:
    return query_service.fetch_dashboard_recent_markets(build_service_context(), now_iso, window_size)


def fetch_trade_count_estimate() -> Dict[str, Any]:
    return query_service.fetch_trade_count_estimate(build_service_context())


def build_dashboard_payload() -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    last_24h = (now - timedelta(hours=24)).isoformat().replace("+00:00", "Z")

    status_rows = fetch_dashboard_market_status(now_iso)
    active_markets = sum(
        int(row.get("value") or 0)
        for row in status_rows
        if row.get("name") in {"Active", "Proposed"}
    )
    settlements_row = query_one(
        """
        SELECT COUNT(*) AS settlements_24h
        FROM oracle_events
        WHERE event_status = 'settle' AND event_time >= ?
        """,
        (last_24h,),
    )
    trade_volume_rows = fetch_dashboard_trade_volume(RECENT_TRADE_WINDOW)
    recent_rows = fetch_dashboard_recent_markets(now_iso, RECENT_TRADE_WINDOW)
    trade_window = fetch_recent_trade_window_bounds(RECENT_TRADE_WINDOW)
    trade_count_estimate = fetch_trade_count_estimate()

    latest_trade_ts = trade_window.get("latest_timestamp")
    earliest_trade_ts = trade_window.get("earliest_timestamp")
    coverage_7d_start = iso_days_before(latest_trade_ts, 7)
    coverage_30d_start = iso_days_before(latest_trade_ts, 30)

    return {
        "metrics": {
            "activeMarkets": active_markets,
            "totalTrades": int(trade_count_estimate.get("table_rows") or 0),
            "settlements24h": int(settlements_row.get("settlements_24h") or 0),
        },
        "volume7d": [
            {
                "day": str(row.get("day")) if row.get("day") is not None else None,
                "trade_count": int(row.get("trade_count") or 0),
            }
            for row in trade_volume_rows[-7:]
        ],
        "volume30d": [
            {
                "day": str(row.get("day")) if row.get("day") is not None else None,
                "trade_count": int(row.get("trade_count") or 0),
            }
            for row in trade_volume_rows[-30:]
        ],
        "statusShare": status_rows,
        "recentActiveMarkets": [
            {
                "id": row.get("id"),
                "slug": row.get("slug"),
                "title": row.get("title"),
                "tradeCount": int(row.get("trade_count") or 0),
                "lastTradeAt": row.get("last_trade_at"),
                "status": row.get("status"),
                "endDate": row.get("end_date"),
                "latestPrice": row.get("latest_price"),
            }
            for row in recent_rows
        ],
        "metadata": {
            "generatedAt": now_iso,
            "cacheTtlSeconds": DASHBOARD_CACHE_TTL_SECONDS,
            "tradeWindowSize": RECENT_TRADE_WINDOW,
            "tradeWindowEarliestTimestamp": earliest_trade_ts,
            "tradeWindowLatestTimestamp": latest_trade_ts,
            "tradeWindowSource": trade_window.get("source"),
            "tradeWindowCovers7d": bool(coverage_7d_start and earliest_trade_ts and earliest_trade_ts <= coverage_7d_start),
            "tradeWindowCovers30d": bool(coverage_30d_start and earliest_trade_ts and earliest_trade_ts <= coverage_30d_start),
            "totalTradesSource": "information_schema.table_rows",
            "totalTradesAutoIncrement": int(trade_count_estimate.get("auto_increment") or 0),
        },
    }


def get_dashboard_payload_cached() -> Dict[str, Any]:
    return bootstrap_service.get_dashboard_payload_cached(build_service_context())


def get_markets_payload_cached(cache_key: str, builder, *, namespace: str = "markets", ttl_seconds: int = MARKETS_CACHE_TTL_SECONDS) -> Dict[str, Any]:
    return api_cache.get_markets_payload_cached(
        build_service_context(),
        cache_key,
        builder,
        namespace=namespace,
        ttl_seconds=ttl_seconds,
    )


def get_bootstrap_component_cached(component_key: str, builder, *, ttl_seconds: int = BOOTSTRAP_COMPONENT_TTL_SECONDS) -> Any:
    return api_cache.get_bootstrap_component_cached(build_service_context(), component_key, builder, ttl_seconds=ttl_seconds)


def normalize_market(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "localMarketId": row.get("id"),
        "slug": row.get("slug"),
        "title": row.get("title"),
        "conditionId": row.get("condition_id"),
        "questionId": row.get("question_id"),
        "oracle": row.get("oracle"),
        "yesTokenId": row.get("yes_token_id"),
        "noTokenId": row.get("no_token_id"),
        "description": row.get("description") or "",
        "status": row.get("status") or "Unknown",
        "latestPrice": row.get("latest_price"),
        "latestYesPrice": row.get("latest_yes_price"),
        "latestNoPrice": row.get("latest_no_price"),
        "enableNegRisk": bool(row.get("enable_neg_risk")),
        "endDate": row.get("end_date"),
        "createdAt": row.get("created_at"),
        "category": row.get("category") or "Uncategorized",
        "tags": parse_json_list(row.get("tags")),
        "gammaMarketId": row.get("gamma_market_id"),
        "settlementCode": row.get("settlement_code") or 0,
        "settlementOutcome": row.get("settlement_outcome") or "UNKNOWN",
        "settlementSource": row.get("settlement_source"),
        "settlementRaw": row.get("settlement_raw"),
        "settlementEventId": row.get("settlement_event_id"),
        "settlementEventTime": row.get("settlement_event_time"),
        "settlementTransaction": row.get("settlement_transaction"),
        "completionStatus": row.get("completion_status") or "OPEN",
        "completionSource": row.get("completion_source"),
        "completionTime": row.get("completion_time"),
        "isTradingClosed": bool(row.get("is_trading_closed")),
        "isResolved": bool(row.get("is_resolved")),
        "isFinal": bool(row.get("is_final")),
        "gammaClosed": bool(row.get("gamma_closed")),
        "gammaClosedTime": row.get("gamma_closed_time"),
    }


def normalize_trade(row: Dict[str, Any]) -> Dict[str, Any]:
    token_id_text = uint256_storage_to_text(row.get("token_id"))
    maker_asset_id = uint256_storage_to_text(row.get("maker_asset_id"))
    taker_asset_id = uint256_storage_to_text(row.get("taker_asset_id"))
    side_text = row.get("side")
    if maker_asset_id is None and token_id_text is not None:
        if side_text == "BUY":
            maker_asset_id = "0"
            taker_asset_id = token_id_text
        elif side_text == "SELL":
            maker_asset_id = token_id_text
            taker_asset_id = "0"
    return {
        "txHash": row.get("tx_hash").hex() if isinstance(row.get("tx_hash"), (bytes, bytearray, memoryview)) else row.get("tx_hash"),
        "logIndex": row.get("log_index"),
        "blockNumber": row.get("block_number"),
        "timestamp": row.get("timestamp"),
        "maker": format_trade_address(row.get("maker")),
        "taker": format_trade_address(row.get("taker")),
        "price": format_trade_decimal(row.get("price")),
        "size": format_trade_decimal(row.get("size")),
        "side": row.get("side"),
        "outcome": row.get("outcome"),
        "tokenId": token_id_text,
        "marketId": row.get("market_id"),
        "localMarketId": row.get("market_id"),
        "marketTitle": row.get("market_title"),
        "orderHash": row.get("order_hash").hex() if isinstance(row.get("order_hash"), (bytes, bytearray, memoryview)) else row.get("order_hash"),
        "makerAssetId": maker_asset_id,
        "takerAssetId": taker_asset_id,
        "makerAmount": row.get("maker_amount"),
        "takerAmount": row.get("taker_amount"),
        "fee": row.get("fee"),
        "contract": format_trade_address(row.get("contract")),
    }


def normalize_oracle_event(row: Dict[str, Any]) -> Dict[str, Any]:
    settlement = parse_oracle_settlement_event(row)
    snapshot_code = row.get("snapshot_settlement_code")
    snapshot_outcome = row.get("snapshot_settlement_outcome")
    snapshot_source = row.get("snapshot_settlement_source")
    if settlement.settlement_code == 0 and snapshot_code not in (None, "", 0, "0"):
        effective_code = snapshot_code
        effective_outcome = snapshot_outcome
        effective_source = snapshot_source
    else:
        effective_code = settlement.settlement_code
        effective_outcome = settlement.settlement_outcome
        effective_source = settlement.settlement_source
    return {
        "id": row.get("id"),
        "txHash": row.get("tx_hash"),
        "logIndex": row.get("log_index"),
        "blockNumber": row.get("block_number"),
        "eventTime": row.get("event_time"),
        "eventStatus": row.get("event_status"),
        "externalMarketId": row.get("external_market_id"),
        "marketId": row.get("market_id"),
        "localMarketId": row.get("market_id"),
        "gammaMarketId": row.get("external_market_id"),
        "marketTitle": row.get("market_title"),
        "marketSlug": row.get("market_slug"),
        "marketCategory": row.get("market_category"),
        "isBound": row.get("market_id") is not None,
        "matchedBy": row.get("matched_by"),
        "questionId": row.get("question_id"),
        "conditionId": row.get("condition_id"),
        "proposedPrice": row.get("proposed_price"),
        "settledPrice": row.get("settled_price"),
        "payout": row.get("payout"),
        "settlementCode": settlement.settlement_code,
        "settlementOutcome": settlement.settlement_outcome,
        "settlementSource": settlement.settlement_source,
        "settlementRaw": settlement.settlement_raw,
        "effectiveSettlementCode": effective_code,
        "effectiveSettlementOutcome": effective_outcome,
        "effectiveSettlementSource": effective_source,
        "completionStatus": row.get("completion_status") or "OPEN",
        "isTradingClosed": bool(row.get("is_trading_closed")),
        "isResolved": bool(row.get("is_resolved")),
        "isFinal": bool(row.get("is_final")),
        "requester": row.get("requester"),
        "proposer": row.get("proposer"),
        "disputer": row.get("disputer"),
        "proposalTransaction": row.get("proposal_transaction"),
        "settlementTransaction": row.get("settlement_transaction"),
        "sourceAdapter": row.get("source_adapter"),
        "sourceOracle": row.get("source_oracle"),
    }


def get_trade_market_projection_sql(alias: str) -> str:
    return f"""
        {alias}.tx_hash AS tx_hash,
        {alias}.log_index AS log_index,
        {alias}.market_id AS market_id,
        {alias}.maker AS maker,
        {alias}.taker AS taker,
        {alias}.price AS price,
        {alias}.size AS size,
        CASE {alias}.side_code
            WHEN 1 THEN 'BUY'
            WHEN 2 THEN 'SELL'
            ELSE 'UNKNOWN'
        END AS side,
        CASE {alias}.outcome_code
            WHEN 1 THEN 'YES'
            WHEN 2 THEN 'NO'
            ELSE 'UNKNOWN'
        END AS outcome,
        {alias}.token_id AS token_id,
        DATE_FORMAT({alias}.block_time, '%%Y-%%m-%%dT%%H:%%i:%%sZ') AS timestamp,
        {alias}.block_number AS block_number,
        {alias}.order_hash AS order_hash,
        {compat_maker_asset_id_sql(alias)} AS maker_asset_id,
        {compat_taker_asset_id_sql(alias)} AS taker_asset_id,
        {alias}.maker_amount AS maker_amount,
        {alias}.taker_amount AS taker_amount,
        {alias}.fee AS fee,
        {alias}.contract AS contract
    """


def get_market_by_slug(slug: str) -> Optional[dict]:
    return market_service.get_market_by_slug(build_service_context(), slug)


def get_market_by_id(market_id: int) -> Optional[dict]:
    return market_service.get_market_by_id(build_service_context(), market_id)


def get_trades_by_market_id(market_id: int, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    return market_service.get_trades_by_market_id(build_service_context(), market_id, limit=limit, offset=offset)


def get_recent_trades(limit: int = 24) -> List[Dict[str, Any]]:
    return query_service.get_recent_trades(build_service_context(), limit=limit)


def get_recent_trades_snapshot(limit: int = 24) -> List[Dict[str, Any]]:
    return market_service.get_recent_trades_snapshot(build_service_context(), limit=limit)


def get_oracle_events_by_market_id(market_id: int) -> List[Dict[str, Any]]:
    return market_service.get_oracle_events_by_market_id(build_service_context(), market_id)


def get_recent_oracle_events(limit: int = 24) -> List[Dict[str, Any]]:
    return query_service.get_recent_oracle_events(build_service_context(), limit=limit)


def get_recent_oracle_snapshot(limit: int = 24) -> List[Dict[str, Any]]:
    return market_service.get_recent_oracle_snapshot(build_service_context(), limit=limit)


def get_market_clob_price_snapshot(market: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return market_data_client.get_market_clob_price_snapshot(build_service_context(), market)


def get_market_clob_price_series(market: Optional[Dict[str, Any]], range_name: str = "1d", interval: str = "5m") -> List[Dict[str, Any]]:
    return market_data_client.get_market_clob_price_series(build_service_context(), market, range_name=range_name, interval=interval)


def get_trade_derived_market_price_series(market_id: int, limit: int = 400) -> List[Dict[str, Any]]:
    clickhouse_points = clickhouse_orderfilled_service.get_price_series(build_service_context(), market_id, limit=limit)
    if clickhouse_points is not None:
        return clickhouse_points
    trade_source = get_existing_trade_read_source()
    if trade_source is None:
        return []
    if _identifier_name(trade_source) == TRADE_V2_CORE_TABLE:
        rows = query_all(
            f"""
            SELECT
                DATE_FORMAT(block_time, '%%Y-%%m-%%dT%%H:%%i:%%sZ') AS timestamp,
                CASE outcome_code
                    WHEN 1 THEN 'YES'
                    WHEN 2 THEN 'NO'
                    ELSE 'UNKNOWN'
                END AS outcome,
                price,
                block_number,
                log_index
            FROM {trade_source}
            WHERE market_id = ?
            ORDER BY block_time DESC, block_number DESC, log_index DESC
            LIMIT ?
            """,
            (market_id, limit),
        )
    else:
        rows = query_all(
            f"""
            SELECT timestamp, outcome, price, block_number, log_index
            FROM {trade_source}
            WHERE market_id = ?
            ORDER BY timestamp DESC, block_number DESC, log_index DESC
            LIMIT ?
            """,
            (market_id, limit),
        )
    rows.reverse()
    yes_price = None
    no_price = None
    points = []
    for row in rows:
        if row.get("outcome") == "YES":
            yes_price = row.get("price")
        elif row.get("outcome") == "NO":
            no_price = row.get("price")
        points.append({"timestamp": row.get("timestamp"), "yesPrice": yes_price, "noPrice": no_price})
    return points


def get_market_price_summary(market_id: int) -> Dict[str, Any]:
    return market_service.get_market_price_summary(build_service_context(), market_id)


def get_market_chart_payload(market_id: int, range_name: str = "1d", interval: str = "5m") -> Dict[str, Any]:
    return market_service.get_market_chart_payload(build_service_context(), market_id, range_name=range_name, interval=interval)


def get_related_content_by_market_id(market_id: int, limit: int = 8) -> Dict[str, Any]:
    return query_service.get_related_content_by_market_id(build_service_context(), market_id, limit=limit)


def get_latest_content_snapshot(limit: int = 8) -> Dict[str, Any]:
    return query_service.get_latest_content_snapshot(build_service_context(), limit=limit)


def build_system_health_payload() -> Dict[str, Any]:
    return system_service.build_system_health_payload(build_service_context())


def enrich_market_rows_with_runtime_prices(rows: List[Dict[str, Any]], *, max_updates: int = 18) -> List[Dict[str, Any]]:
    return market_service.enrich_market_rows_with_runtime_prices(build_service_context(), rows, max_updates=max_updates)


def build_active_markets_payload(page_size: int = 40) -> Dict[str, Any]:
    return market_service.build_active_markets_payload(build_service_context(), page_size=page_size)


def get_active_markets_snapshot(
    page_size: int = 40,
    include_runtime_prices: bool = False,
    include_change_24h: bool = False,
) -> Dict[str, Any]:
    return market_service.get_active_markets_snapshot(
        build_service_context(),
        page_size=page_size,
        include_runtime_prices=include_runtime_prices,
        include_change_24h=include_change_24h,
    )


def build_bootstrap_payload() -> Dict[str, Any]:
    return bootstrap_service.build_bootstrap_payload(build_service_context())


def get_bootstrap_payload_cached() -> Dict[str, Any]:
    return bootstrap_service.get_bootstrap_payload_cached(build_service_context())


def prewarm_snapshot_payloads() -> None:
    bootstrap_service.prewarm_snapshot_payloads(build_service_context())


def prewarm_critical_payloads() -> None:
    bootstrap_service.prewarm_critical_payloads(build_service_context())


def start_snapshot_prewarm_thread() -> None:
    bootstrap_service.start_snapshot_prewarm_thread(build_service_context())


def get_top_addresses_payload(days: Optional[int], limit: int) -> Dict[str, Any]:
    return address_service.get_top_addresses_payload(build_service_context(), days, limit)


def get_active_addresses_payload(days: int) -> Dict[str, Any]:
    return address_service.get_active_addresses_payload(build_service_context(), days)


def get_address_summary_payload(address: str, days: int) -> Dict[str, Any]:
    return address_service.get_address_summary_payload(build_service_context(), address, days)


def get_address_trades_payload(
    address: str,
    *,
    limit: int = 100,
    market_id: Optional[int] = None,
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    before_ts: Optional[str] = None,
    before_block_number: Optional[int] = None,
    before_log_index: Optional[int] = None,
) -> Dict[str, Any]:
    return address_service.get_address_trades_payload(
        build_service_context(),
        address,
        limit=limit,
        market_id=market_id,
        start_ts=start_ts,
        end_ts=end_ts,
        before_ts=before_ts,
        before_block_number=before_block_number,
        before_log_index=before_log_index,
    )

    normalized = normalize_address(address)
    if not normalized:
        return {"address": normalized, "items": [], "nextCursor": None}

    trade_index_names = get_trades_index_names()
    query_source = ADDRESS_HISTORY_SOURCE
    if query_source == TRADE_V2_CORE_TABLE:
        maker_time_index = "idx_trades_v2_maker_time_log"
        taker_time_index = "idx_trades_v2_taker_time_log"
        maker_market_index = ""
        taker_market_index = ""
        maker_projection = """
            t.id AS id,
            LOWER(HEX(t.tx_hash)) AS tx_hash,
            t.log_index AS log_index,
            t.market_id AS market_id,
            CONCAT('0x', LOWER(HEX(t.maker))) AS maker,
            CONCAT('0x', LOWER(HEX(t.taker))) AS taker,
            CAST(t.price AS CHAR) AS price,
            CAST(t.size AS CHAR) AS size,
            CASE t.side_code
                WHEN 1 THEN 'BUY'
                WHEN 2 THEN 'SELL'
                ELSE 'UNKNOWN'
            END AS side,
            CASE t.outcome_code
                WHEN 1 THEN 'YES'
                WHEN 2 THEN 'NO'
                ELSE 'UNKNOWN'
            END AS outcome,
            LOWER(HEX(t.token_id)) AS token_id,
            t.block_number AS block_number,
            DATE_FORMAT(t.block_time, '%%Y-%%m-%%dT%%H:%%i:%%sZ') AS timestamp,
            LOWER(HEX(t.order_hash)) AS order_hash,
            {compat_maker_asset_id_sql('t')} AS maker_asset_id,
            {compat_taker_asset_id_sql('t')} AS taker_asset_id,
            t.maker_amount AS maker_amount,
            t.taker_amount AS taker_amount,
            t.fee AS fee,
            t.contract AS contract
        """
        taker_projection = maker_projection
        maker_filters = ["maker = UNHEX(REPLACE(LOWER(?), '0x', ''))"]
        taker_filters = ["taker = UNHEX(REPLACE(LOWER(?), '0x', ''))"]
    else:
        maker_market_index = "idx_trades_maker_market_time_block_log"
        taker_market_index = "idx_trades_taker_market_time_block_log"
        maker_time_index = "idx_trades_maker_time_block_log"
        taker_time_index = "idx_trades_taker_time_block_log"
        maker_projection = """
            tx_hash, log_index, market_id, maker, taker, price, size, side, outcome,
            token_id, timestamp, block_number, order_hash, maker_asset_id, taker_asset_id,
            maker_amount, taker_amount, fee, contract
        """
        taker_projection = maker_projection
        maker_filters = ["maker = ?"]
        taker_filters = ["taker = ?"]

    maker_hint = ""
    taker_hint = ""
    if (
        market_id is not None
        and maker_market_index
        and taker_market_index
        and maker_market_index in trade_index_names
        and taker_market_index in trade_index_names
    ):
        maker_hint = f" FORCE INDEX ({maker_market_index})"
        taker_hint = f" FORCE INDEX ({taker_market_index})"
    elif maker_time_index in trade_index_names and taker_time_index in trade_index_names:
        maker_hint = f" FORCE INDEX ({maker_time_index})"
        taker_hint = f" FORCE INDEX ({taker_time_index})"
    else:
        return {
            "address": normalized,
            "items": [],
            "nextCursor": None,
            "error": "Required maker/taker address indexes are missing on trades",
        }

    arm_limit = max(100, limit * 2)
    maker_params: List[Any] = [normalized]
    taker_params: List[Any] = [normalized]

    if market_id is not None:
        maker_filters.append("market_id = ?")
        taker_filters.append("market_id = ?")
        maker_params.append(market_id)
        taker_params.append(market_id)
    if start_ts:
        maker_filters.append("timestamp >= ?")
        taker_filters.append("timestamp >= ?")
        maker_params.append(start_ts)
        taker_params.append(start_ts)
    if end_ts:
        maker_filters.append("timestamp < ?")
        taker_filters.append("timestamp < ?")
        maker_params.append(end_ts)
        taker_params.append(end_ts)
    if before_ts and before_block_number is not None and before_log_index is not None:
        cursor_clause = (
            "(timestamp < ? OR (timestamp = ? AND (block_number < ? "
            "OR (block_number = ? AND log_index < ?))))"
        )
        maker_filters.append(cursor_clause)
        taker_filters.append(cursor_clause)
        cursor_params = [before_ts, before_ts, before_block_number, before_block_number, before_log_index]
        maker_params.extend(cursor_params)
        taker_params.extend(cursor_params)

    maker_sql = f"""
        SELECT
            'maker' AS address_role,
            {maker_projection}
        FROM {query_source} t
        {maker_hint}
        WHERE {' AND '.join(maker_filters)}
        ORDER BY timestamp DESC, block_number DESC, log_index DESC
        LIMIT {arm_limit}
    """
    taker_sql = f"""
        SELECT
            'taker' AS address_role,
            {taker_projection}
        FROM {query_source} t
        {taker_hint}
        WHERE {' AND '.join(taker_filters)}
        ORDER BY timestamp DESC, block_number DESC, log_index DESC
        LIMIT {arm_limit}
    """
    sql = f"""
        SELECT *
        FROM (
            ({maker_sql})
            UNION ALL
            ({taker_sql})
        ) address_trades
        ORDER BY timestamp DESC, block_number DESC, log_index DESC
        LIMIT {limit + 1}
    """
    rows = query_all(sql, [*maker_params, *taker_params])
    has_more = len(rows) > limit
    visible_rows = rows[:limit]
    next_cursor = None
    if has_more and visible_rows:
        last_row = visible_rows[-1]
        next_cursor = {
            "beforeTs": last_row.get("timestamp"),
            "beforeBlockNumber": last_row.get("block_number"),
            "beforeLogIndex": last_row.get("log_index"),
        }

    return {
        "address": normalized,
        "items": [
            {
                **normalize_trade(row),
                "addressRole": row.get("address_role"),
            }
            for row in visible_rows
        ],
        "nextCursor": next_cursor,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Polymarket Indexer API Server")
    parser.add_argument("--host", default=SETTINGS.host, help="Bind host")
    parser.add_argument("--port", type=int, default=SETTINGS.port, help="Bind port")
    parser.add_argument("--skip-init-schema", action="store_true", help="Do not initialize schema on startup")
    add_db_cli_args(parser)

    args = parser.parse_args()
    configure_db_from_args(args)
    global DB_PATH
    DB_PATH = args.sqlite_path
    skip_init_schema = args.skip_init_schema or api_readonly_enabled()
    initialize_runtime(
        host=args.host,
        port=args.port,
        skip_init_schema=skip_init_schema,
        log_startup=True,
    )
    app.run(host=args.host, port=args.port, debug=False)


def initialize_runtime(
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    skip_init_schema: bool = False,
    log_startup: bool = False,
) -> Flask:
    global DB_PATH
    global _runtime_initialized
    with _runtime_init_lock:
        if _runtime_initialized:
            return app
        if not skip_init_schema:
            init_schema(db_path=DB_PATH)
        create_app()
        if log_startup:
            app.logger.info("Starting API server at http://%s:%s", host or SETTINGS.host, port or SETTINGS.port)
            app.logger.info("Database: %s", describe_db_target())
        startup_prewarm_owner = _claim_startup_prewarm_slot()
        if startup_prewarm_owner:
            system_service.prewarm_system_health_payload(build_service_context())
            if SNAPSHOT_PREWARM_ENABLED:
                prewarm_critical_payloads()
        if SNAPSHOT_PREWARM_ENABLED and _claim_snapshot_prewarm_owner():
            start_snapshot_prewarm_thread()
        local_orderbook_websocket_watcher.start_background_worker(build_service_context(), lock_dir=_runtime_coordination_dir())
        _runtime_initialized = True
        return app


if __name__ == "__main__":
    main()
