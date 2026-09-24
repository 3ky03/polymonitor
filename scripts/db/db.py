#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket 索引器数据库模块。

当前默认后端为 PostgreSQL。SQLite 仅作为旧迁移/本地调试回退路径；
MySQL 连接代码只保留给显式的历史迁移工具，不再作为运行时默认目标。
该模块尽量兼容原有 sqlite3 风格调用，避免大规模重写业务脚本。
"""

from __future__ import annotations

import os
import re
import sqlite3
import argparse
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import psycopg
except ImportError:
    psycopg = None

try:
    import pymysql
except ImportError:
    pymysql = None


def _load_dotenv_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    project_root = Path(__file__).resolve().parents[2]
    scripts_root = Path(__file__).resolve().parents[1]
    for candidate in (
        project_root / ".env",
        project_root / ".env.local",
        scripts_root / ".env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)


_load_dotenv_files()

DEFAULT_SQLITE_PATH = os.environ.get(
    "POLYMARKET_SQLITE_PATH",
    os.environ.get("POLYMARKET_DB", "/data/hy/myPolyDB/polymarket_indexer.db"),
)
DEFAULT_DB_PATH = DEFAULT_SQLITE_PATH
_REQUESTED_DB_BACKEND = os.environ.get("POLYMARKET_DB_BACKEND", "postgres").strip().lower()
DEFAULT_DB_BACKEND = (
    _REQUESTED_DB_BACKEND
    if _REQUESTED_DB_BACKEND in {"postgres", "postgresql", "sqlite"}
    else "postgres"
)

DEFAULT_MYSQL_HOST = ""
DEFAULT_MYSQL_PORT = 0
DEFAULT_MYSQL_USER = ""
DEFAULT_MYSQL_PASSWORD = ""
DEFAULT_MYSQL_DATABASE = ""
DEFAULT_MYSQL_CHARSET = "utf8mb4"

DEFAULT_POSTGRES_HOST = (
    os.environ.get("POLYDATA_POSTGRES_HOST")
    or os.environ.get("POLYMARKET_POSTGRES_HOST")
    or os.environ.get("POLYMARKET_PostgreSQL_HOST")
    or "127.0.0.1"
)
DEFAULT_POSTGRES_PORT = int(
    os.environ.get("POLYDATA_POSTGRES_PORT")
    or os.environ.get("POLYMARKET_POSTGRES_PORT")
    or os.environ.get("POLYMARKET_PostgreSQL_PORT")
    or "45432"
)
DEFAULT_POSTGRES_USER = (
    os.environ.get("POLYDATA_POSTGRES_USER")
    or os.environ.get("POLYMARKET_POSTGRES_USER")
    or os.environ.get("POLYMARKET_PostgreSQL_USER")
    or "poly_user"
)
DEFAULT_POSTGRES_PASSWORD = (
    os.environ.get("POLYDATA_POSTGRES_PASSWORD")
    or os.environ.get("POLYMARKET_POSTGRES_PASSWORD")
    or os.environ.get("POLYMARKET_POSTGRESQL_PASSWORD")
    or os.environ.get("POLYMARKET_PostgreSQL_PASSWORD")
    or ""
)
DEFAULT_POSTGRES_DATABASE = (
    os.environ.get("POLYDATA_POSTGRES_DATABASE")
    or os.environ.get("POLYMARKET_POSTGRES_DATABASE")
    or os.environ.get("POLYMARKET_PostgreSQL_DATABASE")
    or "poly_data_core"
)
DEFAULT_POSTGRES_SEARCH_PATH = os.environ.get(
    "POLYDATA_POSTGRES_SEARCH_PATH",
    "core,oracle,ops,public",
)

_runtime_db_backend = DEFAULT_DB_BACKEND
_runtime_sqlite_path = DEFAULT_SQLITE_PATH
_runtime_mysql_host = DEFAULT_MYSQL_HOST
_runtime_mysql_port = DEFAULT_MYSQL_PORT
_runtime_mysql_user = DEFAULT_MYSQL_USER
_runtime_mysql_password = DEFAULT_MYSQL_PASSWORD
_runtime_mysql_database = DEFAULT_MYSQL_DATABASE
_runtime_mysql_charset = DEFAULT_MYSQL_CHARSET
_runtime_postgres_host = DEFAULT_POSTGRES_HOST
_runtime_postgres_port = DEFAULT_POSTGRES_PORT
_runtime_postgres_user = DEFAULT_POSTGRES_USER
_runtime_postgres_password = DEFAULT_POSTGRES_PASSWORD
_runtime_postgres_database = DEFAULT_POSTGRES_DATABASE
_runtime_postgres_search_path = DEFAULT_POSTGRES_SEARCH_PATH

_NAMED_PARAM_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")
_ON_CONFLICT_RE = re.compile(
    r"ON\s+CONFLICT\s*\([^)]*\)\s+DO\s+UPDATE\s+SET",
    flags=re.IGNORECASE | re.DOTALL,
)


def get_backend() -> str:
    return _runtime_db_backend


def get_sqlite_path() -> str:
    return _runtime_sqlite_path


def get_mysql_settings() -> Dict[str, Any]:
    return {
        "host": _runtime_mysql_host,
        "port": _runtime_mysql_port,
        "user": _runtime_mysql_user,
        "password": _runtime_mysql_password,
        "database": _runtime_mysql_database,
        "charset": _runtime_mysql_charset,
    }


def get_postgres_settings() -> Dict[str, Any]:
    return {
        "host": _runtime_postgres_host,
        "port": _runtime_postgres_port,
        "user": _runtime_postgres_user,
        "password": _runtime_postgres_password,
        "database": _runtime_postgres_database,
        "search_path": _runtime_postgres_search_path,
    }


def configure_runtime_db(
    *,
    backend: Optional[str] = None,
    sqlite_path: Optional[str] = None,
    mysql_host: Optional[str] = None,
    mysql_port: Optional[int] = None,
    mysql_user: Optional[str] = None,
    mysql_password: Optional[str] = None,
    mysql_database: Optional[str] = None,
    mysql_charset: Optional[str] = None,
    postgres_host: Optional[str] = None,
    postgres_port: Optional[int] = None,
    postgres_user: Optional[str] = None,
    postgres_password: Optional[str] = None,
    postgres_database: Optional[str] = None,
    postgres_search_path: Optional[str] = None,
) -> None:
    global _runtime_db_backend
    global _runtime_sqlite_path
    global _runtime_mysql_host
    global _runtime_mysql_port
    global _runtime_mysql_user
    global _runtime_mysql_password
    global _runtime_mysql_database
    global _runtime_mysql_charset
    global _runtime_postgres_host
    global _runtime_postgres_port
    global _runtime_postgres_user
    global _runtime_postgres_password
    global _runtime_postgres_database
    global _runtime_postgres_search_path

    if backend is not None:
        _runtime_db_backend = backend.strip().lower()
    if sqlite_path is not None:
        _runtime_sqlite_path = str(Path(sqlite_path).expanduser())
    if mysql_host is not None:
        _runtime_mysql_host = mysql_host
    if mysql_port is not None:
        _runtime_mysql_port = int(mysql_port)
    if mysql_user is not None:
        _runtime_mysql_user = mysql_user
    if mysql_password is not None:
        _runtime_mysql_password = mysql_password
    if mysql_database is not None:
        _runtime_mysql_database = mysql_database
    if mysql_charset is not None:
        _runtime_mysql_charset = mysql_charset
    if postgres_host is not None:
        _runtime_postgres_host = postgres_host
    if postgres_port is not None:
        _runtime_postgres_port = int(postgres_port)
    if postgres_user is not None:
        _runtime_postgres_user = postgres_user
    if postgres_password is not None:
        _runtime_postgres_password = postgres_password
    if postgres_database is not None:
        _runtime_postgres_database = postgres_database
    if postgres_search_path is not None:
        _runtime_postgres_search_path = postgres_search_path


def add_db_cli_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=["sqlite", "mysql", "postgres", "postgresql"],
        default=DEFAULT_DB_BACKEND,
        help="数据库后端；默认从 POLYMARKET_DB_BACKEND 读取",
    )
    parser.add_argument(
        "--sqlite-path",
        default=DEFAULT_SQLITE_PATH,
        help="SQLite 文件路径；仅在 --backend sqlite 时作为真实落库目标使用",
    )
    parser.add_argument("--mysql-host", default=DEFAULT_MYSQL_HOST, help="MySQL host")
    parser.add_argument("--mysql-port", type=int, default=DEFAULT_MYSQL_PORT, help="MySQL port")
    parser.add_argument("--mysql-user", default=DEFAULT_MYSQL_USER, help="MySQL user")
    parser.add_argument("--mysql-password", default=DEFAULT_MYSQL_PASSWORD, help="MySQL password")
    parser.add_argument("--mysql-database", default=DEFAULT_MYSQL_DATABASE, help="MySQL database")
    parser.add_argument("--mysql-charset", default=DEFAULT_MYSQL_CHARSET, help="MySQL charset")
    parser.add_argument("--postgres-host", default=DEFAULT_POSTGRES_HOST, help="PostgreSQL host")
    parser.add_argument("--postgres-port", type=int, default=DEFAULT_POSTGRES_PORT, help="PostgreSQL port")
    parser.add_argument("--postgres-user", default=DEFAULT_POSTGRES_USER, help="PostgreSQL user")
    parser.add_argument("--postgres-database", default=DEFAULT_POSTGRES_DATABASE, help="PostgreSQL database")
    parser.add_argument(
        "--postgres-search-path",
        default=DEFAULT_POSTGRES_SEARCH_PATH,
        help="PostgreSQL search_path for legacy unqualified table names",
    )


def configure_db_from_args(args: argparse.Namespace) -> None:
    configure_runtime_db(
        backend=getattr(args, "backend", None),
        sqlite_path=getattr(args, "sqlite_path", None),
        mysql_host=getattr(args, "mysql_host", None),
        mysql_port=getattr(args, "mysql_port", None),
        mysql_user=getattr(args, "mysql_user", None),
        mysql_password=getattr(args, "mysql_password", None),
        mysql_database=getattr(args, "mysql_database", None),
        mysql_charset=getattr(args, "mysql_charset", None),
        postgres_host=getattr(args, "postgres_host", None),
        postgres_port=getattr(args, "postgres_port", None),
        postgres_user=getattr(args, "postgres_user", None),
        postgres_password=None,
        postgres_database=getattr(args, "postgres_database", None),
        postgres_search_path=getattr(args, "postgres_search_path", None),
    )


def describe_db_target() -> str:
    backend = get_backend()
    if backend == "sqlite":
        return f"sqlite:{Path(get_sqlite_path()).expanduser()}"
    if backend in {"postgres", "postgresql"}:
        settings = get_postgres_settings()
        return f"postgres:{settings['user']}@{settings['host']}:{settings['port']}/{settings['database']}"
    if backend == "mysql":
        settings = get_mysql_settings()
        return f"mysql:{settings['user']}@{settings['host']}:{settings['port']}/{settings['database']}"
    return f"unsupported:{backend}"


def is_mysql_backend() -> bool:
    return get_backend() == "mysql"


def is_postgres_backend() -> bool:
    return get_backend() in {"postgres", "postgresql"}


class DBRow:
    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = list(columns)
        self._values = tuple(values)
        self._index = {name: idx for idx, name in enumerate(self._columns)}

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[key]]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values[self._index[key]] if key in self._index else default

    def keys(self) -> List[str]:
        return list(self._columns)

    def as_dict(self) -> Dict[str, Any]:
        return {name: self._values[idx] for idx, name in enumerate(self._columns)}


class MySQLCursorWrapper:
    def __init__(self, connection: "MySQLConnectionWrapper", cursor) -> None:
        self._connection = connection
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def execute(self, query: str, params: Optional[Any] = None):
        sql, bound = adapt_sql_for_mysql(query, params)
        result = self._cursor.execute(sql, bound)
        self._connection._bump_changes(self._cursor.rowcount)
        return result

    def executemany(self, query: str, seq_of_params: Iterable[Any]):
        bound_many = list(seq_of_params)
        if not bound_many:
            return 0
        sql, sample = adapt_sql_for_mysql(query, bound_many[0])
        if sample is None:
            raise RuntimeError("unexpected missing bound parameters while adapting executemany")
        bound_many = [adapt_params_for_mysql(query, item) for item in bound_many]
        result = self._cursor.executemany(sql, bound_many)
        self._connection._bump_changes(self._cursor.rowcount)
        return result

    def fetchone(self):
        row = self._cursor.fetchone()
        return wrap_mysql_row(self._cursor.description, row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [wrap_mysql_row(self._cursor.description, row) for row in rows]

    def close(self) -> None:
        self._cursor.close()


class MySQLConnectionWrapper:
    def __init__(self, raw_conn) -> None:
        self._raw_conn = raw_conn
        self.total_changes = 0
        self.row_factory = DBRow

    def _bump_changes(self, rowcount: int) -> None:
        if rowcount and rowcount > 0:
            self.total_changes += rowcount

    def cursor(self) -> MySQLCursorWrapper:
        return MySQLCursorWrapper(self, self._raw_conn.cursor())

    def execute(self, query: str, params: Optional[Any] = None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def executemany(self, query: str, seq_of_params: Iterable[Any]):
        cur = self.cursor()
        cur.executemany(query, seq_of_params)
        return cur

    def commit(self) -> None:
        self._raw_conn.commit()

    def rollback(self) -> None:
        self._raw_conn.rollback()

    def ping(self, reconnect: bool = True) -> None:
        self._raw_conn.ping(reconnect=reconnect)

    def close(self) -> None:
        self._raw_conn.close()


class PostgresCursorWrapper:
    def __init__(self, connection: "PostgresConnectionWrapper", cursor) -> None:
        self._connection = connection
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def execute(self, query: str, params: Optional[Any] = None):
        sql, bound = adapt_sql_for_postgres(query, params)
        result = self._cursor.execute(sql, bound)
        self._connection._bump_changes(self._cursor.rowcount)
        return result

    def executemany(self, query: str, seq_of_params: Iterable[Any]):
        bound_many = list(seq_of_params)
        if not bound_many:
            return 0
        sql, sample = adapt_sql_for_postgres(query, bound_many[0])
        if sample is None:
            raise RuntimeError("unexpected missing bound parameters while adapting executemany")
        bound_many = [adapt_params_for_postgres(query, item) for item in bound_many]
        result = self._cursor.executemany(sql, bound_many)
        self._connection._bump_changes(self._cursor.rowcount)
        return result

    def fetchone(self):
        row = self._cursor.fetchone()
        return wrap_db_row(self._cursor.description, row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [wrap_db_row(self._cursor.description, row) for row in rows]

    def close(self) -> None:
        self._cursor.close()


class PostgresConnectionWrapper:
    def __init__(self, pg_conn) -> None:
        self._pg_conn = pg_conn
        self.total_changes = 0
        self.row_factory = DBRow

    def _bump_changes(self, rowcount: int) -> None:
        if rowcount and rowcount > 0:
            self.total_changes += rowcount

    def cursor(self) -> PostgresCursorWrapper:
        return PostgresCursorWrapper(self, self._pg_conn.cursor())

    def execute(self, query: str, params: Optional[Any] = None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def executemany(self, query: str, seq_of_params: Iterable[Any]):
        cur = self.cursor()
        cur.executemany(query, seq_of_params)
        return cur

    def commit(self) -> None:
        self._pg_conn.commit()

    def rollback(self) -> None:
        self._pg_conn.rollback()

    def close(self) -> None:
        self._pg_conn.close()


def wrap_mysql_row(description, row):
    if row is None:
        return None
    columns = [col[0] for col in description] if description else []
    return DBRow(columns, row)


def wrap_db_row(description, row):
    if row is None:
        return None
    columns = [col[0] for col in description] if description else []
    return DBRow(columns, row)


def adapt_params_for_mysql(query: str, params: Optional[Any]) -> Any:
    if params is None:
        return None
    if isinstance(params, dict):
        return params
    if isinstance(params, tuple):
        return params
    if isinstance(params, list):
        return tuple(params)
    return (params,)


def adapt_sql_for_mysql(query: str, params: Optional[Any]) -> Tuple[str, Optional[Any]]:
    sql = query.strip()
    sql = sql.replace(" COLLATE NOCASE", "")
    sql = re.sub(r"(FROM\s+sync_state\s+WHERE\s+)key\b", r"\1`key`", sql, flags=re.IGNORECASE)
    sql = re.sub(r"(INTO\s+sync_state\s*\()\s*key\b", r"\1`key`", sql, flags=re.IGNORECASE)
    sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT IGNORE INTO", sql, flags=re.IGNORECASE)
    sql = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "REPLACE INTO", sql, flags=re.IGNORECASE)
    sql = _ON_CONFLICT_RE.sub("ON DUPLICATE KEY UPDATE", sql)
    sql = re.sub(r"excluded\.([a-zA-Z_][a-zA-Z0-9_]*)", r"VALUES(\1)", sql, flags=re.IGNORECASE)
    if isinstance(params, dict):
        sql = _NAMED_PARAM_RE.sub(r"%(\1)s", sql)
        return sql, params
    if params is not None:
        sql = sql.replace("?", "%s")
        return sql, adapt_params_for_mysql(query, params)
    return sql.replace("?", "%s"), None


def adapt_params_for_postgres(query: str, params: Optional[Any]) -> Any:
    if params is None:
        return None
    if isinstance(params, dict):
        return params
    if isinstance(params, tuple):
        return params
    if isinstance(params, list):
        return tuple(params)
    return (params,)


def _replace_insert_or_replace_for_postgres(sql: str) -> str:
    match = re.match(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+sync_state\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", sql, flags=re.IGNORECASE)
    columns = [part.strip().strip('"') for part in match.group(1).split(",")]
    update_columns = [col for col in columns if col != "key"]
    updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in update_columns)
    return (
        re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", sql, flags=re.IGNORECASE)
        + f" ON CONFLICT (key) DO UPDATE SET {updates}"
    )


def adapt_sql_for_postgres(query: str, params: Optional[Any]) -> Tuple[str, Optional[Any]]:
    sql = query.strip()
    sql = sql.replace("`", '"')
    sql = sql.replace(" COLLATE NOCASE", "")
    sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.IGNORECASE)
    if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO", sql, flags=re.IGNORECASE):
        sql = _replace_insert_or_replace_for_postgres(sql)
    if isinstance(params, dict):
        sql = _NAMED_PARAM_RE.sub(r"%(\1)s", sql)
        return sql, params
    if params is not None:
        return sql.replace("?", "%s"), adapt_params_for_postgres(query, params)
    return sql.replace("?", "%s"), None


def get_sqlite_connection(db_path: str = DEFAULT_DB_PATH, readonly: bool = False) -> sqlite3.Connection:
    parent = Path(db_path).expanduser().resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    if readonly:
        uri = f"file:{Path(db_path).expanduser().resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
    conn.row_factory = sqlite3.Row
    return conn


def get_mysql_connection() -> MySQLConnectionWrapper:
    if pymysql is None:
        raise RuntimeError("pymysql is not installed. Please install pymysql first.")
    settings = get_mysql_settings()
    raw = pymysql.connect(
        host=settings["host"],
        port=settings["port"],
        user=settings["user"],
        password=settings["password"],
        database=settings["database"],
        charset=settings["charset"],
        autocommit=False,
        connect_timeout=int(os.environ.get("POLYMARKET_MYSQL_CONNECT_TIMEOUT", "10")),
        read_timeout=int(os.environ.get("POLYMARKET_MYSQL_READ_TIMEOUT", "60")),
        write_timeout=int(os.environ.get("POLYMARKET_MYSQL_WRITE_TIMEOUT", "60")),
    )
    return MySQLConnectionWrapper(raw)


def get_postgres_connection() -> PostgresConnectionWrapper:
    if psycopg is None:
        raise RuntimeError("psycopg is not installed. Please install psycopg[binary] first.")
    settings = get_postgres_settings()
    raw = psycopg.connect(
        host=settings["host"],
        port=settings["port"],
        user=settings["user"],
        password=settings["password"],
        dbname=settings["database"],
        autocommit=False,
    )
    search_path = str(settings.get("search_path") or "").strip()
    if search_path:
        with raw.cursor() as cur:
            cur.execute("SET search_path TO " + search_path)
    return PostgresConnectionWrapper(raw)


def get_connection(
    db_path: Optional[str] = DEFAULT_DB_PATH,
    *,
    backend: Optional[str] = None,
    readonly: bool = False,
):
    chosen = (backend or get_backend()).strip().lower()
    if chosen == "sqlite":
        return get_sqlite_connection(db_path or get_sqlite_path(), readonly=readonly)
    if chosen in {"postgres", "postgresql"}:
        return get_postgres_connection()
    if chosen == "mysql":
        return get_mysql_connection()
    raise ValueError(f"Unsupported database backend: {chosen}")


@contextmanager
def get_db(db_path: Optional[str] = DEFAULT_DB_PATH, *, backend: Optional[str] = None, readonly: bool = False):
    conn = get_connection(db_path, backend=backend, readonly=readonly)
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


def get_table_columns(conn, table: str) -> List[str]:
    if isinstance(conn, sqlite3.Connection):
        cur = conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cur.fetchall()]
    if isinstance(conn, PostgresConnectionWrapper):
        if "." in table:
            schema, table_name = table.split(".", 1)
            params = (schema, table_name)
            schema_filter = "table_schema = %s"
        else:
            table_name = table
            params = (table_name,)
            schema_filter = "table_schema IN ('core', 'oracle', 'ops', 'public')"
        cur = conn.execute(
            f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE {schema_filter} AND table_name = %s
            ORDER BY ordinal_position
            """,
            params,
        )
        return [row[0] for row in cur.fetchall()]

    cur = conn.execute(
        """
        SELECT COLUMN_NAME
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (get_mysql_settings()["database"], table),
    )
    return [row[0] for row in cur.fetchall()]


def get_table_column_types(conn, table: str) -> Dict[str, str]:
    if isinstance(conn, sqlite3.Connection):
        cur = conn.execute(f"PRAGMA table_info({table})")
        return {row[1]: row[2] for row in cur.fetchall()}
    if isinstance(conn, PostgresConnectionWrapper):
        if "." in table:
            schema, table_name = table.split(".", 1)
            params = (schema, table_name)
            schema_filter = "table_schema = %s"
        else:
            table_name = table
            params = (table_name,)
            schema_filter = "table_schema IN ('core', 'oracle', 'ops', 'public')"
        cur = conn.execute(
            f"""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE {schema_filter} AND table_name = %s
            ORDER BY ordinal_position
            """,
            params,
        )
        return {row[0]: row[1] for row in cur.fetchall()}

    cur = conn.execute(
        """
        SELECT COLUMN_NAME, COLUMN_TYPE
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (get_mysql_settings()["database"], table),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def table_exists(conn, table: str) -> bool:
    return bool(get_table_columns(conn, table))


def create_index_if_not_exists(conn, table: str, index_name: str, columns: Sequence[str], unique: bool = False) -> None:
    if isinstance(conn, sqlite3.Connection):
        unique_sql = "UNIQUE " if unique else ""
        conn.execute(f"CREATE {unique_sql}INDEX IF NOT EXISTS {index_name} ON {table}({', '.join(columns)})")
        return
    if isinstance(conn, PostgresConnectionWrapper):
        unique_sql = "UNIQUE " if unique else ""
        conn.execute(
            f"CREATE {unique_sql}INDEX IF NOT EXISTS {index_name} ON {table}({', '.join(columns)})"
        )
        return

    cur = conn.execute(
        """
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = %s AND table_name = %s AND index_name = %s
        LIMIT 1
        """,
        (get_mysql_settings()["database"], table, index_name),
    )
    if cur.fetchone():
        return
    unique_sql = "UNIQUE " if unique else ""
    conn.execute(
        f"ALTER TABLE {table} ADD {unique_sql}INDEX {index_name} ({', '.join(columns)})"
    )


def ensure_column_exists(conn, table: str, column: str, column_type: str) -> None:
    if column in get_table_columns(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _create_mysql_uma_adapter_mapping_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS uma_adapter_mapping (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            ancillary_data LONGTEXT NOT NULL,
            ancillary_data_hash CHAR(64) GENERATED ALWAYS AS (SHA2(ancillary_data, 256)) STORED,
            question_id VARCHAR(255) NOT NULL,
            source_adapter VARCHAR(255),
            UNIQUE KEY uq_uma_adapter_mapping_hash (ancillary_data_hash)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def _create_mysql_neg_risk_request_mapping_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neg_risk_request_mapping (
            request_id VARCHAR(255) NOT NULL PRIMARY KEY,
            question_id VARCHAR(255) NOT NULL,
            market_id VARCHAR(255),
            source_operator VARCHAR(255)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def _ensure_mysql_uma_adapter_mapping_schema(conn) -> None:
    if not table_exists(conn, "uma_adapter_mapping"):
        _create_mysql_uma_adapter_mapping_table(conn)
        return

    column_types = {name: col_type.lower() for name, col_type in get_table_column_types(conn, "uma_adapter_mapping").items()}
    needs_rebuild = (
        column_types.get("ancillary_data", "").startswith("varchar(")
        or "ancillary_data_hash" not in column_types
        or "id" not in column_types
    )
    if not needs_rebuild:
        ensure_column_exists(conn, "uma_adapter_mapping", "source_adapter", "VARCHAR(255)")
        return

    conn.execute("DROP TABLE IF EXISTS uma_adapter_mapping_legacy_tmp")
    conn.execute("RENAME TABLE uma_adapter_mapping TO uma_adapter_mapping_legacy_tmp")
    _create_mysql_uma_adapter_mapping_table(conn)
    conn.execute(
        """
        INSERT INTO uma_adapter_mapping (ancillary_data, question_id, source_adapter)
        SELECT ancillary_data, question_id, NULL
        FROM uma_adapter_mapping_legacy_tmp
        """
    )
    conn.execute("DROP TABLE uma_adapter_mapping_legacy_tmp")


def _ensure_mysql_neg_risk_request_mapping_schema(conn) -> None:
    if not table_exists(conn, "neg_risk_request_mapping"):
        _create_mysql_neg_risk_request_mapping_table(conn)
        return

    ensure_column_exists(conn, "neg_risk_request_mapping", "market_id", "VARCHAR(255)")
    ensure_column_exists(conn, "neg_risk_request_mapping", "source_operator", "VARCHAR(255)")


def _init_postgres_schema(conn: PostgresConnectionWrapper) -> None:
    conn.execute("CREATE SCHEMA IF NOT EXISTS core")
    conn.execute("CREATE SCHEMA IF NOT EXISTS oracle")
    conn.execute("CREATE SCHEMA IF NOT EXISTS ops")
    conn.execute("CREATE SEQUENCE IF NOT EXISTS core.markets_id_seq")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.markets (
            id BIGINT PRIMARY KEY DEFAULT nextval('core.markets_id_seq'),
            gamma_market_id TEXT,
            event_id TEXT,
            event_slug TEXT,
            event_title TEXT,
            slug TEXT NOT NULL,
            condition_id TEXT NOT NULL UNIQUE,
            question_id TEXT,
            oracle TEXT,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            title TEXT,
            description TEXT,
            enable_neg_risk BOOLEAN NOT NULL DEFAULT FALSE,
            end_date TIMESTAMPTZ,
            raw_end_date TEXT,
            created_at TIMESTAMPTZ,
            raw_created_at TEXT,
            category TEXT,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            clob_token_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            migrated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    for col, col_type in (
        ("event_id", "TEXT"),
        ("event_slug", "TEXT"),
        ("event_title", "TEXT"),
    ):
        ensure_column_exists(conn, "core.markets", col, col_type)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.market_status_snapshot (
            market_id BIGINT PRIMARY KEY REFERENCES core.markets(id),
            has_settle BOOLEAN NOT NULL DEFAULT FALSE,
            has_propose BOOLEAN NOT NULL DEFAULT FALSE,
            has_dispute BOOLEAN NOT NULL DEFAULT FALSE,
            settlement_code SMALLINT NOT NULL DEFAULT 0 CHECK (settlement_code IN (0, 1, 2, 3)),
            settlement_outcome TEXT NOT NULL DEFAULT 'UNKNOWN',
            settlement_source TEXT,
            settlement_raw TEXT,
            settlement_event_id BIGINT,
            settlement_event_time TIMESTAMPTZ,
            settlement_transaction TEXT,
            is_trading_closed BOOLEAN NOT NULL DEFAULT FALSE,
            is_resolved BOOLEAN NOT NULL DEFAULT FALSE,
            is_final BOOLEAN NOT NULL DEFAULT FALSE,
            completion_status TEXT NOT NULL DEFAULT 'OPEN',
            completion_source TEXT,
            completion_time TIMESTAMPTZ,
            gamma_closed BOOLEAN NOT NULL DEFAULT FALSE,
            gamma_closed_time TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    for col, col_type in (
        ("has_dispute", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("settlement_code", "SMALLINT NOT NULL DEFAULT 0"),
        ("settlement_outcome", "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
        ("settlement_source", "TEXT"),
        ("settlement_raw", "TEXT"),
        ("settlement_event_id", "BIGINT"),
        ("settlement_event_time", "TIMESTAMPTZ"),
        ("settlement_transaction", "TEXT"),
        ("is_trading_closed", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("is_resolved", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("is_final", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("completion_status", "TEXT NOT NULL DEFAULT 'OPEN'"),
        ("completion_source", "TEXT"),
        ("completion_time", "TIMESTAMPTZ"),
        ("gamma_closed", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("gamma_closed_time", "TIMESTAMPTZ"),
    ):
        ensure_column_exists(conn, "core.market_status_snapshot", col, col_type)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.market_resolution_fast (
            market_id BIGINT PRIMARY KEY REFERENCES core.markets(id),
            settlement_code SMALLINT NOT NULL CHECK (settlement_code IN (0, 1, 2, 3)),
            condition_id TEXT,
            slug TEXT,
            closed_time TIMESTAMPTZ,
            updated_at TIMESTAMPTZ
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.market_tokens (
            id BIGINT PRIMARY KEY,
            market_id BIGINT NOT NULL REFERENCES core.markets(id),
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL UNIQUE,
            outcome TEXT NOT NULL,
            outcome_index INTEGER NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            end_date TIMESTAMPTZ,
            raw_end_date TEXT,
            created_at TIMESTAMPTZ,
            raw_created_at TEXT,
            updated_at TIMESTAMPTZ,
            raw_updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ops.sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            last_block BIGINT,
            updated_at TIMESTAMPTZ
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.market_trade_daily_stats (
            trade_date DATE NOT NULL,
            market_id BIGINT NOT NULL REFERENCES core.markets(id),
            trade_count BIGINT NOT NULL DEFAULT 0,
            volume_notional NUMERIC(38, 18) NOT NULL DEFAULT 0,
            last_trade_at TIMESTAMPTZ,
            last_block_number BIGINT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (trade_date, market_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.market_latest_prices (
            market_id BIGINT PRIMARY KEY REFERENCES core.markets(id),
            latest_trade_at TIMESTAMPTZ,
            latest_trade_block BIGINT,
            latest_trade_log_index BIGINT,
            latest_price NUMERIC(20, 10),
            latest_yes_trade_at TIMESTAMPTZ,
            latest_yes_trade_block BIGINT,
            latest_yes_trade_log_index BIGINT,
            latest_yes_price NUMERIC(20, 10),
            latest_no_trade_at TIMESTAMPTZ,
            latest_no_trade_block BIGINT,
            latest_no_trade_log_index BIGINT,
            latest_no_price NUMERIC(20, 10),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.market_list_serving (
            market_id BIGINT PRIMARY KEY REFERENCES core.markets(id),
            latest_price NUMERIC(20, 10),
            latest_trade_at TIMESTAMPTZ,
            price_24h_ago NUMERIC(20, 10),
            trade_count_24h BIGINT NOT NULL DEFAULT 0,
            volume_24h NUMERIC(38, 18) NOT NULL DEFAULT 0,
            last_trade_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    ensure_column_exists(conn, "core.market_list_serving", "price_24h_ago", "NUMERIC(20, 10)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.market_chart_serving (
            market_id BIGINT NOT NULL REFERENCES core.markets(id) ON DELETE CASCADE,
            range_name TEXT NOT NULL,
            interval_name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'probability',
            history_status TEXT NOT NULL DEFAULT 'missing',
            point_count INTEGER NOT NULL DEFAULT 0,
            points JSONB NOT NULL DEFAULT '[]'::jsonb,
            source TEXT NOT NULL DEFAULT 'postgres',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (market_id, range_name, interval_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.market_workspace_serving (
            market_id BIGINT PRIMARY KEY REFERENCES core.markets(id) ON DELETE CASCADE,
            detail_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            price_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            oracle_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            content_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            source TEXT NOT NULL DEFAULT 'postgres',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS core.event_market_serving (
            serving_key TEXT PRIMARY KEY,
            group_id TEXT NOT NULL UNIQUE,
            event_id TEXT,
            event_slug TEXT,
            event_title TEXT,
            title TEXT NOT NULL,
            category TEXT,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ,
            end_date TIMESTAMPTZ,
            volume_24h NUMERIC(38, 18) NOT NULL DEFAULT 0,
            trade_count_24h BIGINT NOT NULL DEFAULT 0,
            last_activity_at TIMESTAMPTZ,
            outcome_count INTEGER NOT NULL DEFAULT 0,
            default_market_id BIGINT REFERENCES core.markets(id) ON DELETE SET NULL,
            default_condition_id TEXT,
            default_gamma_market_id TEXT,
            default_outcome_key TEXT,
            top_outcomes JSONB NOT NULL DEFAULT '[]'::jsonb,
            outcomes JSONB NOT NULL DEFAULT '[]'::jsonb,
            completion_status TEXT NOT NULL DEFAULT 'OPEN',
            is_trading_closed BOOLEAN NOT NULL DEFAULT FALSE,
            active_rank DOUBLE PRECISION NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'postgres',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    for table, index_name, cols in (
        ("core.markets", "idx_markets_gamma_market_id", ["gamma_market_id"]),
        ("core.markets", "idx_markets_event_id", ["event_id"]),
        ("core.markets", "idx_markets_event_slug", ["event_slug"]),
        ("core.markets", "idx_markets_slug", ["slug"]),
        ("core.markets", "idx_markets_question_id", ["question_id"]),
        ("core.markets", "idx_markets_yes_token_id", ["yes_token_id"]),
        ("core.markets", "idx_markets_no_token_id", ["no_token_id"]),
        ("core.markets", "idx_markets_end_date", ["end_date"]),
        ("core.markets", "idx_markets_created_at", ["created_at"]),
        ("core.market_status_snapshot", "idx_market_status_snapshot_flags", ["has_settle", "has_propose", "market_id"]),
        ("core.market_status_snapshot", "idx_market_status_snapshot_lifecycle_flags", ["has_settle", "has_propose", "has_dispute", "market_id"]),
        ("core.market_status_snapshot", "idx_market_status_snapshot_settlement_code", ["settlement_code"]),
        ("core.market_status_snapshot", "idx_market_status_snapshot_completion_status", ["completion_status", "market_id"]),
        ("core.market_status_snapshot", "idx_market_status_snapshot_completion_flags", ["is_final", "is_resolved", "is_trading_closed", "market_id"]),
        ("core.market_status_snapshot", "idx_market_status_snapshot_completion_time", ["completion_time"]),
        ("core.market_status_snapshot", "idx_market_status_snapshot_gamma_closed", ["gamma_closed", "gamma_closed_time"]),
        ("core.market_resolution_fast", "idx_mrf_settlement_code", ["settlement_code"]),
        ("core.market_resolution_fast", "idx_mrf_condition_id", ["condition_id"]),
        ("core.market_resolution_fast", "idx_mrf_slug", ["slug"]),
        ("core.market_resolution_fast", "idx_mrf_closed_time", ["closed_time"]),
        ("core.market_trade_daily_stats", "idx_market_trade_daily_stats_market_date", ["market_id", "trade_date"]),
        ("core.market_trade_daily_stats", "idx_market_trade_daily_stats_last_trade_at", ["last_trade_at"]),
        ("core.market_latest_prices", "idx_market_latest_prices_latest_trade_at", ["latest_trade_at"]),
        ("core.market_list_serving", "idx_market_list_serving_activity", ["volume_24h", "trade_count_24h", "last_trade_at"]),
        ("core.market_list_serving", "idx_market_list_serving_latest_trade_at", ["latest_trade_at"]),
        ("core.market_chart_serving", "idx_market_chart_serving_updated", ["updated_at"]),
        ("core.market_chart_serving", "idx_market_chart_serving_status", ["history_status", "updated_at"]),
        ("core.market_workspace_serving", "idx_market_workspace_serving_updated", ["updated_at"]),
        ("core.event_market_serving", "idx_event_market_serving_event_default", ["event_id", "default_market_id"]),
    ):
        create_index_if_not_exists(conn, table, index_name, cols)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_markets_tags_gin ON core.markets USING GIN (tags)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_markets_clob_token_ids_gin ON core.markets USING GIN (clob_token_ids)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_markets_category_lower ON core.markets (lower(category))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_markets_created_id_desc ON core.markets (created_at DESC NULLS LAST, id DESC)")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_status_snapshot_open_market_id
          ON core.market_status_snapshot (market_id)
          WHERE has_settle = FALSE
            AND has_propose = FALSE
            AND is_trading_closed = FALSE
            AND settlement_code = 0
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_list_serving_volume_activity_desc
          ON core.market_list_serving (volume_24h DESC, trade_count_24h DESC, last_trade_at DESC, market_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_list_serving_recent_trade_desc
          ON core.market_list_serving (last_trade_at DESC NULLS LAST, latest_trade_at DESC NULLS LAST, market_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_event_market_serving_active_desc
          ON core.event_market_serving (volume_24h DESC, last_activity_at DESC NULLS LAST, serving_key)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_event_market_serving_status_active_desc
          ON core.event_market_serving (completion_status, is_trading_closed, volume_24h DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_event_market_serving_rank_desc
          ON core.event_market_serving (active_rank DESC, volume_24h DESC, last_activity_at DESC NULLS LAST)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_markets_search_text_simple_gin
          ON core.markets USING GIN (
            to_tsvector(
              'simple',
              (((coalesce(title, '') || ' ') || coalesce(slug, '')) || ' ') || coalesce(category, '')
            )
          )
        """
    )
    _ensure_postgres_trade_indexes(conn)
    _ensure_postgres_oracle_indexes(conn)


def _ensure_postgres_trade_indexes(conn: PostgresConnectionWrapper) -> None:
    for table in ("core.trades_v2", "core.trades"):
        row = conn.execute("SELECT to_regclass(?)", (table,)).fetchone()
        if not row or not row[0]:
            continue
        columns = get_table_columns(conn, table)
        table_suffix = table.rsplit(".", 1)[-1]
        if "market_id" in columns and "block_time" in columns:
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{table_suffix}_market_time_desc
                  ON {table} (market_id, block_time DESC NULLS LAST, block_number DESC NULLS LAST, log_index DESC NULLS LAST)
                """
            )
        if "market_id" in columns and "timestamp" in columns:
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{table_suffix}_market_timestamp_desc
                  ON {table} (market_id, timestamp DESC NULLS LAST, block_number DESC NULLS LAST, log_index DESC NULLS LAST)
                """
            )


def _ensure_postgres_oracle_indexes(conn: PostgresConnectionWrapper) -> None:
    row = conn.execute("SELECT to_regclass('oracle.oracle_events')").fetchone()
    if not row or not row[0]:
        return
    for table, index_name, cols in (
        ("oracle.oracle_events", "idx_oracle_events_market_block_id", ["market_id", "block_number", "id"]),
        ("oracle.oracle_events", "idx_oracle_events_external_market_block_id", ["external_market_id", "block_number", "id"]),
        ("oracle.oracle_events", "idx_oracle_events_question_block_id", ["question_id", "block_number", "id"]),
        ("oracle.oracle_events", "idx_oracle_events_condition_block_id", ["condition_id", "block_number", "id"]),
    ):
        create_index_if_not_exists(conn, table, index_name, cols)


def init_schema(conn=None, db_path: str = DEFAULT_DB_PATH) -> None:
    close_after = False
    if conn is None:
        conn = get_connection(db_path)
        close_after = True

    try:
        if isinstance(conn, sqlite3.Connection):
            _init_sqlite_schema(conn)
        elif isinstance(conn, PostgresConnectionWrapper):
            _init_postgres_schema(conn)
        else:
            _init_mysql_schema(conn)
        conn.commit()
    finally:
        if close_after:
            conn.close()


def _init_sqlite_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gamma_market_id TEXT,
            slug TEXT NOT NULL UNIQUE,
            condition_id TEXT NOT NULL UNIQUE,
            question_id TEXT,
            oracle TEXT,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            title TEXT,
            description TEXT,
            enable_neg_risk INTEGER DEFAULT 0,
            end_date TEXT,
            created_at TEXT,
            category TEXT,
            tags TEXT,
            clob_token_ids TEXT
        )
        """
    )
    for col, col_type in (
        ("gamma_market_id", "TEXT"),
        ("category", "TEXT"),
        ("tags", "TEXT"),
        ("clob_token_ids", "TEXT"),
    ):
        try:
            cursor.execute(f"ALTER TABLE markets ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            market_id INTEGER NOT NULL,
            maker TEXT NOT NULL,
            taker TEXT NOT NULL,
            price TEXT NOT NULL,
            size TEXT NOT NULL,
            side TEXT NOT NULL,
            outcome TEXT,
            token_id TEXT NOT NULL,
            block_number INTEGER,
            timestamp TEXT,
            order_hash TEXT,
            maker_asset_id TEXT,
            taker_asset_id TEXT,
            maker_amount INTEGER,
            taker_amount INTEGER,
            fee INTEGER,
            contract TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (market_id) REFERENCES markets(id),
            UNIQUE(tx_hash, log_index)
        )
        """
    )
    for col, col_type in (
        ("order_hash", "TEXT"),
        ("maker_asset_id", "TEXT"),
        ("taker_asset_id", "TEXT"),
        ("maker_amount", "INTEGER"),
        ("taker_amount", "INTEGER"),
        ("fee", "INTEGER"),
        ("contract", "TEXT"),
    ):
        try:
            cursor.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS block_timestamps (
            block_number INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            last_block INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_trade_daily_stats (
            trade_date TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            trade_count INTEGER NOT NULL DEFAULT 0,
            volume_notional REAL NOT NULL DEFAULT 0,
            last_trade_at TEXT,
            last_block_number INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, market_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_latest_prices (
            market_id INTEGER PRIMARY KEY,
            latest_trade_at TEXT,
            latest_trade_block INTEGER,
            latest_trade_log_index INTEGER,
            latest_price REAL,
            latest_yes_trade_at TEXT,
            latest_yes_trade_block INTEGER,
            latest_yes_trade_log_index INTEGER,
            latest_yes_price REAL,
            latest_no_trade_at TEXT,
            latest_no_trade_block INTEGER,
            latest_no_trade_log_index INTEGER,
            latest_no_price REAL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_status_snapshot (
            market_id INTEGER PRIMARY KEY,
            has_settle INTEGER NOT NULL DEFAULT 0,
            has_propose INTEGER NOT NULL DEFAULT 0,
            has_dispute INTEGER NOT NULL DEFAULT 0,
            settlement_code INTEGER NOT NULL DEFAULT 0,
            settlement_outcome TEXT NOT NULL DEFAULT 'UNKNOWN',
            settlement_source TEXT,
            settlement_raw TEXT,
            settlement_event_id INTEGER,
            settlement_event_time TEXT,
            settlement_transaction TEXT,
            is_trading_closed INTEGER NOT NULL DEFAULT 0,
            is_resolved INTEGER NOT NULL DEFAULT 0,
            is_final INTEGER NOT NULL DEFAULT 0,
            completion_status TEXT NOT NULL DEFAULT 'OPEN',
            completion_source TEXT,
            completion_time TEXT,
            gamma_closed INTEGER NOT NULL DEFAULT 0,
            gamma_closed_time TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for col, col_type in (
        ("has_dispute", "INTEGER NOT NULL DEFAULT 0"),
        ("settlement_code", "INTEGER NOT NULL DEFAULT 0"),
        ("settlement_outcome", "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
        ("settlement_source", "TEXT"),
        ("settlement_raw", "TEXT"),
        ("settlement_event_id", "INTEGER"),
        ("settlement_event_time", "TEXT"),
        ("settlement_transaction", "TEXT"),
        ("is_trading_closed", "INTEGER NOT NULL DEFAULT 0"),
        ("is_resolved", "INTEGER NOT NULL DEFAULT 0"),
        ("is_final", "INTEGER NOT NULL DEFAULT 0"),
        ("completion_status", "TEXT NOT NULL DEFAULT 'OPEN'"),
        ("completion_source", "TEXT"),
        ("completion_time", "TEXT"),
        ("gamma_closed", "INTEGER NOT NULL DEFAULT 0"),
        ("gamma_closed_time", "TEXT"),
    ):
        try:
            cursor.execute(f"ALTER TABLE market_status_snapshot ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_list_serving (
            market_id INTEGER PRIMARY KEY,
            latest_price REAL,
            latest_trade_at TEXT,
            price_24h_ago REAL,
            trade_count_24h INTEGER NOT NULL DEFAULT 0,
            volume_24h REAL NOT NULL DEFAULT 0,
            last_trade_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    try:
        cursor.execute("ALTER TABLE market_list_serving ADD COLUMN price_24h_ago REAL")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_addresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            tx_hash TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            market_id INTEGER NOT NULL,
            token_id TEXT NOT NULL,
            outcome TEXT,
            address TEXT NOT NULL,
            role TEXT NOT NULL,
            side_for_address TEXT NOT NULL,
            price REAL NOT NULL,
            size REAL NOT NULL,
            notional REAL NOT NULL,
            fee_amount REAL NOT NULL DEFAULT 0,
            block_number INTEGER,
            trade_time TEXT,
            trade_date TEXT,
            contract TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tx_hash, log_index, address)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS address_trade_daily_stats (
            trade_date TEXT NOT NULL,
            address TEXT NOT NULL,
            trade_count INTEGER NOT NULL DEFAULT 0,
            buy_count INTEGER NOT NULL DEFAULT 0,
            sell_count INTEGER NOT NULL DEFAULT 0,
            volume_notional REAL NOT NULL DEFAULT 0,
            last_trade_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, address)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS address_trade_totals (
            address TEXT PRIMARY KEY,
            total_trade_count INTEGER NOT NULL DEFAULT 0,
            total_buy_count INTEGER NOT NULL DEFAULT 0,
            total_sell_count INTEGER NOT NULL DEFAULT 0,
            total_volume_notional REAL NOT NULL DEFAULT 0,
            first_trade_at TEXT,
            last_trade_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS address_market_stats (
            address TEXT NOT NULL,
            market_id INTEGER NOT NULL,
            trade_count INTEGER NOT NULL DEFAULT 0,
            buy_count INTEGER NOT NULL DEFAULT 0,
            sell_count INTEGER NOT NULL DEFAULT 0,
            volume_notional REAL NOT NULL DEFAULT 0,
            first_trade_at TEXT,
            last_trade_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (address, market_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS uma_adapter_mapping (
            ancillary_data TEXT PRIMARY KEY,
            question_id TEXT NOT NULL,
            source_adapter TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS neg_risk_request_mapping (
            request_id TEXT PRIMARY KEY,
            question_id TEXT NOT NULL,
            market_id TEXT,
            source_operator TEXT
        )
        """
    )
    for col, col_type in (
        ("market_id", "TEXT"),
        ("source_operator", "TEXT"),
    ):
        try:
            cursor.execute(f"ALTER TABLE neg_risk_request_mapping ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS oracle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            block_number INTEGER NOT NULL,
            event_time TEXT,
            event_status TEXT NOT NULL,
            external_market_id TEXT,
            market_id INTEGER,
            market_title TEXT,
            source_adapter TEXT,
            source_oracle TEXT,
            adapter_question_id TEXT,
            matched_by TEXT,
            question_id TEXT,
            condition_id TEXT,
            string_raw TEXT,
            p1 TEXT,
            p2 TEXT,
            proposed_price TEXT,
            settled_price TEXT,
            settlement_recipient TEXT,
            payout TEXT,
            requester TEXT,
            proposer TEXT,
            disputer TEXT,
            request_transaction TEXT,
            proposal_transaction TEXT,
            settlement_transaction TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (market_id) REFERENCES markets(id),
            UNIQUE(tx_hash, log_index)
        )
        """
    )
    for table, index_name, cols in (
        ("markets", "idx_markets_condition_id", ["condition_id"]),
        ("markets", "idx_markets_gamma_market_id", ["gamma_market_id"]),
        ("markets", "idx_markets_yes_token", ["yes_token_id"]),
        ("markets", "idx_markets_no_token", ["no_token_id"]),
        ("trades", "idx_trades_market_id", ["market_id"]),
        ("trades", "idx_trades_timestamp", ["timestamp"]),
        ("trades", "idx_trades_block", ["block_number"]),
        ("market_trade_daily_stats", "idx_market_trade_daily_stats_market_date", ["market_id", "trade_date"]),
        ("market_trade_daily_stats", "idx_market_trade_daily_stats_last_trade_at", ["last_trade_at"]),
        ("market_latest_prices", "idx_market_latest_prices_latest_trade_at", ["latest_trade_at"]),
        ("market_status_snapshot", "idx_market_status_snapshot_flags", ["has_settle", "has_propose", "market_id"]),
        ("market_status_snapshot", "idx_market_status_snapshot_lifecycle_flags", ["has_settle", "has_propose", "has_dispute", "market_id"]),
        ("market_status_snapshot", "idx_market_status_snapshot_settlement_code", ["settlement_code"]),
        ("market_status_snapshot", "idx_market_status_snapshot_completion_status", ["completion_status", "market_id"]),
        ("market_status_snapshot", "idx_market_status_snapshot_completion_flags", ["is_final", "is_resolved", "is_trading_closed", "market_id"]),
        ("market_status_snapshot", "idx_market_status_snapshot_completion_time", ["completion_time"]),
        ("market_status_snapshot", "idx_market_status_snapshot_gamma_closed", ["gamma_closed", "gamma_closed_time"]),
        ("market_list_serving", "idx_market_list_serving_activity", ["volume_24h", "trade_count_24h", "last_trade_at"]),
        ("market_list_serving", "idx_market_list_serving_latest_trade_at", ["latest_trade_at"]),
        ("trade_addresses", "idx_trade_addresses_address_time", ["address", "trade_time", "block_number", "log_index"]),
        ("trade_addresses", "idx_trade_addresses_address_market", ["address", "market_id"]),
        ("trade_addresses", "idx_trade_addresses_market_time", ["market_id", "trade_time", "block_number", "log_index"]),
        ("trade_addresses", "idx_trade_addresses_trade_date_address", ["trade_date", "address"]),
        ("address_trade_daily_stats", "idx_address_trade_daily_stats_address_date", ["address", "trade_date"]),
        ("address_trade_daily_stats", "idx_address_trade_daily_stats_last_trade_at", ["last_trade_at"]),
        ("address_trade_totals", "idx_address_trade_totals_last_trade_at", ["last_trade_at"]),
        ("address_market_stats", "idx_address_market_stats_market", ["market_id"]),
        ("address_market_stats", "idx_address_market_stats_last_trade_at", ["last_trade_at"]),
        ("oracle_events", "idx_oracle_events_market_id", ["market_id"]),
        ("oracle_events", "idx_oracle_events_external_market_id", ["external_market_id"]),
        ("oracle_events", "idx_oracle_events_question_id", ["question_id"]),
        ("oracle_events", "idx_oracle_events_condition_id", ["condition_id"]),
        ("oracle_events", "idx_oracle_events_market_block_id", ["market_id", "block_number", "id"]),
        ("oracle_events", "idx_oracle_events_external_market_block_id", ["external_market_id", "block_number", "id"]),
        ("oracle_events", "idx_oracle_events_question_block_id", ["question_id", "block_number", "id"]),
        ("oracle_events", "idx_oracle_events_condition_block_id", ["condition_id", "block_number", "id"]),
        ("oracle_events", "idx_oracle_events_block", ["block_number"]),
        ("oracle_events", "idx_oracle_events_status", ["event_status"]),
        ("oracle_events", "idx_oracle_events_status_market_id", ["event_status", "market_id"]),
        ("oracle_events", "idx_oracle_events_status_event_time", ["event_status", "event_time"]),
        ("oracle_events", "idx_oracle_events_matched_by", ["matched_by"]),
        ("neg_risk_request_mapping", "idx_neg_risk_request_mapping_question_id", ["question_id"]),
        ("neg_risk_request_mapping", "idx_neg_risk_request_mapping_source_operator", ["source_operator"]),
        ("block_timestamps", "idx_block_timestamps_timestamp", ["timestamp"]),
    ):
        create_index_if_not_exists(conn, table, index_name, cols)


def _init_mysql_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS markets (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            gamma_market_id VARCHAR(255),
            slug VARCHAR(512) NOT NULL,
            condition_id VARCHAR(255) NOT NULL,
            question_id VARCHAR(255),
            oracle VARCHAR(255),
            yes_token_id VARCHAR(255) NOT NULL,
            no_token_id VARCHAR(255) NOT NULL,
            title TEXT,
            description LONGTEXT,
            enable_neg_risk TINYINT DEFAULT 0,
            end_date VARCHAR(255),
            created_at VARCHAR(255),
            category VARCHAR(255),
            tags LONGTEXT,
            clob_token_ids LONGTEXT,
            UNIQUE KEY uq_markets_slug (slug),
            UNIQUE KEY uq_markets_condition_id (condition_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            tx_hash VARCHAR(255) NOT NULL,
            log_index BIGINT NOT NULL,
            market_id BIGINT NOT NULL,
            maker VARCHAR(255) NOT NULL,
            taker VARCHAR(255) NOT NULL,
            price VARCHAR(255) NOT NULL,
            size VARCHAR(255) NOT NULL,
            side VARCHAR(255) NOT NULL,
            outcome VARCHAR(255),
            token_id VARCHAR(255) NOT NULL,
            block_number BIGINT,
            timestamp VARCHAR(255),
            order_hash VARCHAR(255),
            maker_asset_id VARCHAR(255),
            taker_asset_id VARCHAR(255),
            maker_amount BIGINT,
            taker_amount BIGINT,
            fee BIGINT,
            contract VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_trades_tx_log (tx_hash, log_index),
            CONSTRAINT fk_trades_market_id FOREIGN KEY (market_id) REFERENCES markets(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS block_timestamps (
            block_number BIGINT PRIMARY KEY,
            timestamp VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_state (
            `key` VARCHAR(255) PRIMARY KEY,
            value LONGTEXT,
            last_block BIGINT,
            updated_at VARCHAR(255)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_trade_daily_stats (
            trade_date DATE NOT NULL,
            market_id BIGINT NOT NULL,
            trade_count BIGINT NOT NULL DEFAULT 0,
            volume_notional DECIMAL(38, 18) NOT NULL DEFAULT 0,
            last_trade_at DATETIME(6),
            last_block_number BIGINT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, market_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_latest_prices (
            market_id BIGINT NOT NULL PRIMARY KEY,
            latest_trade_at DATETIME(6),
            latest_trade_block BIGINT,
            latest_trade_log_index BIGINT,
            latest_price DECIMAL(20, 10),
            latest_yes_trade_at DATETIME(6),
            latest_yes_trade_block BIGINT,
            latest_yes_trade_log_index BIGINT,
            latest_yes_price DECIMAL(20, 10),
            latest_no_trade_at DATETIME(6),
            latest_no_trade_block BIGINT,
            latest_no_trade_log_index BIGINT,
            latest_no_price DECIMAL(20, 10),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_status_snapshot (
            market_id BIGINT NOT NULL PRIMARY KEY,
            has_settle TINYINT NOT NULL DEFAULT 0,
            has_propose TINYINT NOT NULL DEFAULT 0,
            has_dispute TINYINT NOT NULL DEFAULT 0,
            settlement_code TINYINT NOT NULL DEFAULT 0,
            settlement_outcome VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
            settlement_source VARCHAR(64),
            settlement_raw LONGTEXT,
            settlement_event_id BIGINT,
            settlement_event_time DATETIME(6),
            settlement_transaction VARCHAR(255),
            is_trading_closed TINYINT NOT NULL DEFAULT 0,
            is_resolved TINYINT NOT NULL DEFAULT 0,
            is_final TINYINT NOT NULL DEFAULT 0,
            completion_status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
            completion_source VARCHAR(64),
            completion_time DATETIME(6),
            gamma_closed TINYINT NOT NULL DEFAULT 0,
            gamma_closed_time DATETIME(6),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_market_status_snapshot_market_id FOREIGN KEY (market_id) REFERENCES markets(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_list_serving (
            market_id BIGINT NOT NULL PRIMARY KEY,
            latest_price DECIMAL(20, 10),
            latest_trade_at DATETIME(6),
            price_24h_ago DECIMAL(20, 10),
            trade_count_24h BIGINT NOT NULL DEFAULT 0,
            volume_24h DECIMAL(38, 18) NOT NULL DEFAULT 0,
            last_trade_at DATETIME(6),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_market_list_serving_market_id FOREIGN KEY (market_id) REFERENCES markets(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_addresses (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            trade_id BIGINT,
            tx_hash CHAR(66) NOT NULL,
            log_index BIGINT NOT NULL,
            market_id BIGINT NOT NULL,
            token_id VARCHAR(128) NOT NULL,
            outcome VARCHAR(16),
            address CHAR(42) NOT NULL,
            role VARCHAR(16) NOT NULL,
            side_for_address VARCHAR(16) NOT NULL,
            price DECIMAL(20, 10) NOT NULL,
            size DECIMAL(30, 10) NOT NULL,
            notional DECIMAL(38, 18) NOT NULL,
            fee_amount DECIMAL(38, 18) NOT NULL DEFAULT 0,
            block_number BIGINT,
            trade_time DATETIME(6),
            trade_date DATE,
            contract VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_trade_addresses_trade_address (tx_hash, log_index, address)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS address_trade_daily_stats (
            trade_date DATE NOT NULL,
            address CHAR(42) NOT NULL,
            trade_count BIGINT NOT NULL DEFAULT 0,
            buy_count BIGINT NOT NULL DEFAULT 0,
            sell_count BIGINT NOT NULL DEFAULT 0,
            volume_notional DECIMAL(38, 18) NOT NULL DEFAULT 0,
            last_trade_at DATETIME(6),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, address)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS address_trade_totals (
            address CHAR(42) NOT NULL PRIMARY KEY,
            total_trade_count BIGINT NOT NULL DEFAULT 0,
            total_buy_count BIGINT NOT NULL DEFAULT 0,
            total_sell_count BIGINT NOT NULL DEFAULT 0,
            total_volume_notional DECIMAL(38, 18) NOT NULL DEFAULT 0,
            first_trade_at DATETIME(6),
            last_trade_at DATETIME(6),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS address_market_stats (
            address CHAR(42) NOT NULL,
            market_id BIGINT NOT NULL,
            trade_count BIGINT NOT NULL DEFAULT 0,
            buy_count BIGINT NOT NULL DEFAULT 0,
            sell_count BIGINT NOT NULL DEFAULT 0,
            volume_notional DECIMAL(38, 18) NOT NULL DEFAULT 0,
            first_trade_at DATETIME(6),
            last_trade_at DATETIME(6),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (address, market_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    _create_mysql_uma_adapter_mapping_table(conn)
    _create_mysql_neg_risk_request_mapping_table(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS oracle_events (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            tx_hash VARCHAR(255) NOT NULL,
            log_index BIGINT NOT NULL,
            block_number BIGINT NOT NULL,
            event_time VARCHAR(255),
            event_status VARCHAR(255) NOT NULL,
            external_market_id VARCHAR(255),
            market_id BIGINT,
            market_title TEXT,
            source_adapter VARCHAR(255),
            source_oracle VARCHAR(255),
            adapter_question_id VARCHAR(255),
            matched_by VARCHAR(255),
            question_id VARCHAR(255),
            condition_id VARCHAR(255),
            string_raw LONGTEXT,
            p1 VARCHAR(255),
            p2 VARCHAR(255),
            proposed_price VARCHAR(255),
            settled_price VARCHAR(255),
            settlement_recipient VARCHAR(255),
            payout LONGTEXT,
            requester VARCHAR(255),
            proposer VARCHAR(255),
            disputer VARCHAR(255),
            request_transaction VARCHAR(255),
            proposal_transaction VARCHAR(255),
            settlement_transaction VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_oracle_events_tx_log (tx_hash, log_index),
            CONSTRAINT fk_oracle_events_market_id FOREIGN KEY (market_id) REFERENCES markets(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    for col, col_type in (
        ("gamma_market_id", "VARCHAR(255)"),
        ("category", "VARCHAR(255)"),
        ("tags", "LONGTEXT"),
        ("clob_token_ids", "LONGTEXT"),
    ):
        ensure_column_exists(conn, "markets", col, col_type)
    for col, col_type in (
        ("order_hash", "VARCHAR(255)"),
        ("maker_asset_id", "VARCHAR(255)"),
        ("taker_asset_id", "VARCHAR(255)"),
        ("maker_amount", "BIGINT"),
        ("taker_amount", "BIGINT"),
        ("fee", "BIGINT"),
        ("contract", "VARCHAR(255)"),
    ):
        ensure_column_exists(conn, "trades", col, col_type)
    ensure_column_exists(conn, "market_list_serving", "price_24h_ago", "DECIMAL(20, 10)")
    for col, col_type in (
        ("has_dispute", "TINYINT NOT NULL DEFAULT 0"),
        ("settlement_code", "TINYINT NOT NULL DEFAULT 0"),
        ("settlement_outcome", "VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN'"),
        ("settlement_source", "VARCHAR(64)"),
        ("settlement_raw", "LONGTEXT"),
        ("settlement_event_id", "BIGINT"),
        ("settlement_event_time", "DATETIME(6)"),
        ("settlement_transaction", "VARCHAR(255)"),
        ("is_trading_closed", "TINYINT NOT NULL DEFAULT 0"),
        ("is_resolved", "TINYINT NOT NULL DEFAULT 0"),
        ("is_final", "TINYINT NOT NULL DEFAULT 0"),
        ("completion_status", "VARCHAR(32) NOT NULL DEFAULT 'OPEN'"),
        ("completion_source", "VARCHAR(64)"),
        ("completion_time", "DATETIME(6)"),
        ("gamma_closed", "TINYINT NOT NULL DEFAULT 0"),
        ("gamma_closed_time", "DATETIME(6)"),
    ):
        ensure_column_exists(conn, "market_status_snapshot", col, col_type)
    _ensure_mysql_uma_adapter_mapping_schema(conn)
    _ensure_mysql_neg_risk_request_mapping_schema(conn)
    for table, index_name, cols in (
        ("markets", "idx_markets_condition_id", ["condition_id"]),
        ("markets", "idx_markets_gamma_market_id", ["gamma_market_id"]),
        ("markets", "idx_markets_yes_token", ["yes_token_id"]),
        ("markets", "idx_markets_no_token", ["no_token_id"]),
        ("trades", "idx_trades_market_id", ["market_id"]),
        ("trades", "idx_trades_timestamp", ["timestamp"]),
        ("trades", "idx_trades_block", ["block_number"]),
        ("market_trade_daily_stats", "idx_market_trade_daily_stats_market_date", ["market_id", "trade_date"]),
        ("market_trade_daily_stats", "idx_market_trade_daily_stats_last_trade_at", ["last_trade_at"]),
        ("market_latest_prices", "idx_market_latest_prices_latest_trade_at", ["latest_trade_at"]),
        ("market_status_snapshot", "idx_market_status_snapshot_flags", ["has_settle", "has_propose", "market_id"]),
        ("market_status_snapshot", "idx_market_status_snapshot_lifecycle_flags", ["has_settle", "has_propose", "has_dispute", "market_id"]),
        ("market_status_snapshot", "idx_market_status_snapshot_settlement_code", ["settlement_code"]),
        ("market_status_snapshot", "idx_market_status_snapshot_completion_status", ["completion_status", "market_id"]),
        ("market_status_snapshot", "idx_market_status_snapshot_completion_flags", ["is_final", "is_resolved", "is_trading_closed", "market_id"]),
        ("market_status_snapshot", "idx_market_status_snapshot_completion_time", ["completion_time"]),
        ("market_status_snapshot", "idx_market_status_snapshot_gamma_closed", ["gamma_closed", "gamma_closed_time"]),
        ("market_list_serving", "idx_market_list_serving_activity", ["volume_24h", "trade_count_24h", "last_trade_at"]),
        ("market_list_serving", "idx_market_list_serving_latest_trade_at", ["latest_trade_at"]),
        ("trade_addresses", "idx_trade_addresses_address_time", ["address", "trade_time", "block_number", "log_index"]),
        ("trade_addresses", "idx_trade_addresses_address_market", ["address", "market_id"]),
        ("trade_addresses", "idx_trade_addresses_market_time", ["market_id", "trade_time", "block_number", "log_index"]),
        ("trade_addresses", "idx_trade_addresses_trade_date_address", ["trade_date", "address"]),
        ("address_trade_daily_stats", "idx_address_trade_daily_stats_address_date", ["address", "trade_date"]),
        ("address_trade_daily_stats", "idx_address_trade_daily_stats_last_trade_at", ["last_trade_at"]),
        ("address_trade_totals", "idx_address_trade_totals_last_trade_at", ["last_trade_at"]),
        ("address_market_stats", "idx_address_market_stats_market", ["market_id"]),
        ("address_market_stats", "idx_address_market_stats_last_trade_at", ["last_trade_at"]),
        ("oracle_events", "idx_oracle_events_market_id", ["market_id"]),
        ("oracle_events", "idx_oracle_events_external_market_id", ["external_market_id"]),
        ("oracle_events", "idx_oracle_events_question_id", ["question_id"]),
        ("oracle_events", "idx_oracle_events_condition_id", ["condition_id"]),
        ("oracle_events", "idx_oracle_events_market_block_id", ["market_id", "block_number", "id"]),
        ("oracle_events", "idx_oracle_events_external_market_block_id", ["external_market_id", "block_number", "id"]),
        ("oracle_events", "idx_oracle_events_question_block_id", ["question_id", "block_number", "id"]),
        ("oracle_events", "idx_oracle_events_condition_block_id", ["condition_id", "block_number", "id"]),
        ("oracle_events", "idx_oracle_events_block", ["block_number"]),
        ("oracle_events", "idx_oracle_events_status", ["event_status"]),
        ("oracle_events", "idx_oracle_events_status_market_id", ["event_status", "market_id"]),
        ("oracle_events", "idx_oracle_events_matched_by", ["matched_by"]),
        ("neg_risk_request_mapping", "idx_neg_risk_request_mapping_question_id", ["question_id"]),
        ("neg_risk_request_mapping", "idx_neg_risk_request_mapping_source_operator", ["source_operator"]),
    ):
        create_index_if_not_exists(conn, table, index_name, cols)


def dict_from_row(row) -> dict:
    if row is None:
        return {}
    if hasattr(row, "as_dict"):
        return row.as_dict()
    return dict(row)
