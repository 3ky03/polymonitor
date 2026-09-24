#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only Polymarket search helpers for standalone seed watchers."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Any, Dict, List

from api import db as api_db
from api.services import market_service
from db import dict_from_row, get_backend, get_connection


class _LoggerAdapter:
    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        return None

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        return None


class _AppAdapter:
    logger = _LoggerAdapter()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def build_market_search_context(settings: Any) -> Dict[str, Any]:
    return {
        "SETTINGS": settings,
        "DB_PATH": str(getattr(settings, "db_path", "") or ""),
        "DB_CONNECTION_EXIT_DISABLED": True,
        "app": _AppAdapter(),
        "build_market_status_case": build_market_status_case,
        "dict_from_row": dict_from_row,
        "format_trade_decimal": format_trade_decimal,
        "get_backend": get_backend,
        "get_connection": get_connection,
        "parse_json_list": parse_json_list,
        "query_all": lambda sql, params=None: api_db.query_all(build_market_search_context(settings), sql, params),
        "utc_now_iso": utc_now_iso,
    }


def build_market_search(settings: Any):
    def search(query: str, limit: int = 10) -> Dict[str, Any]:
        return market_service.search_markets(build_market_search_context(settings), query, limit=limit)

    return search
