from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional


DB_CONNECTION_ERROR_CODES = {1156, 1158, 1159, 1160, 1161, 2006, 2013, 2014}
DB_CONNECTION_ERROR_MARKERS = (
    "packet sequence number wrong",
    "lost connection",
    "server has gone away",
    "commands out of sync",
    "malformed packet",
    "packets out of order",
    "connection already closed",
    "connection is closed",
    "connection reset",
    "terminating connection",
)


def is_db_connection_error(exc: BaseException) -> bool:
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int) and args[0] in DB_CONNECTION_ERROR_CODES:
        return True
    message = str(exc).lower()
    return any(marker in message for marker in DB_CONNECTION_ERROR_MARKERS)


def exit_worker_on_db_connection_error(ctx: dict, exc: BaseException, operation: str) -> None:
    if ctx.get("DB_CONNECTION_EXIT_DISABLED"):
        return
    if not is_db_connection_error(exc):
        return
    ctx["app"].logger.critical(
        "database connection state is unhealthy during %s; exiting current worker for gunicorn respawn: %r",
        operation,
        exc,
    )
    os._exit(75)


def query_all(ctx: dict, sql: str, params: Optional[Iterable[Any]] = None) -> List[Dict[str, Any]]:
    conn = ctx["get_connection"](ctx["DB_PATH"])
    cursor = conn.cursor()
    bound_params = tuple(params or ())
    try:
        cursor.execute(sql, bound_params)
        return [ctx["dict_from_row"](row) for row in cursor.fetchall()]
    except Exception as exc:
        ctx["app"].logger.exception("SQL query_all failed sql=%s params=%s", " ".join(sql.split()), bound_params)
        exit_worker_on_db_connection_error(ctx, exc, "query_all")
        raise
    finally:
        conn.close()


def query_one(ctx: dict, sql: str, params: Optional[Iterable[Any]] = None) -> Dict[str, Any]:
    conn = ctx["get_connection"](ctx["DB_PATH"])
    cursor = conn.cursor()
    bound_params = tuple(params or ())
    try:
        cursor.execute(sql, bound_params)
        return ctx["dict_from_row"](cursor.fetchone())
    except Exception as exc:
        ctx["app"].logger.exception("SQL query_one failed sql=%s params=%s", " ".join(sql.split()), bound_params)
        exit_worker_on_db_connection_error(ctx, exc, "query_one")
        raise
    finally:
        conn.close()


def table_exists(ctx: dict, table_name: str) -> bool:
    conn = ctx["get_connection"](ctx["DB_PATH"])
    cursor = conn.cursor()
    try:
        if ctx["get_backend"]() == "sqlite":
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1", (table_name,))
            return cursor.fetchone() is not None
        if ctx["get_backend"]() in {"postgres", "postgresql"}:
            if "." in table_name:
                schema, name = table_name.split(".", 1)
                cursor.execute(
                    """
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = ? AND table_name = ?
                    LIMIT 1
                    """,
                    (schema, name),
                )
            else:
                cursor.execute(
                    """
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema IN ('core', 'oracle', 'ops', 'public')
                      AND table_name = ?
                    LIMIT 1
                    """,
                    (table_name,),
                )
            return cursor.fetchone() is not None
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = %s
            LIMIT 1
            """,
            (table_name,),
        )
        return cursor.fetchone() is not None
    except Exception as exc:
        ctx["app"].logger.exception("SQL table_exists failed table=%s", table_name)
        exit_worker_on_db_connection_error(ctx, exc, "table_exists")
        raise
    finally:
        conn.close()


def identifier_name(identifier: str) -> str:
    return str(identifier or "").strip().strip("`")


def get_existing_trade_read_source(ctx: dict) -> Optional[str]:
    for candidate in (ctx["TRADE_V2_CORE_TABLE"], ctx["TRADE_READ_SOURCE"], ctx["LEGACY_TRADES_TABLE"]):
        table_name = identifier_name(candidate)
        if table_name and table_exists(ctx, table_name):
            return ctx["sql_identifier"](table_name)
    return None


def get_trades_index_names(ctx: dict, force_refresh: bool = False) -> set[str]:
    return set()
