#!/usr/bin/env python3
"""Shared RPC helpers for long-running Polymarket chain indexers."""

from __future__ import annotations

import sys
import time
from typing import Any, Optional

try:
    import requests
    from requests.adapters import HTTPAdapter
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]
    HTTPAdapter = None  # type: ignore[assignment]

try:
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover
    Retry = None  # type: ignore[assignment]

try:
    from web3 import Web3
except ImportError as exc:  # pragma: no cover
    raise SystemExit("web3 is required: pip install web3") from exc

try:
    from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware
except ImportError:  # pragma: no cover
    try:
        from web3.middleware import geth_poa_middleware  # type: ignore[no-redef]
    except ImportError:
        geth_poa_middleware = None  # type: ignore[assignment]


DEFAULT_RPC_TIMEOUT_SECONDS = 60
DEFAULT_RPC_CONNECT_RETRIES = 3
DEFAULT_RPC_CONNECT_RETRY_DELAY_SECONDS = 10
DEFAULT_HTTP_RETRIES = 5
DEFAULT_POOL_CONNECTIONS = 64
DEFAULT_POOL_MAXSIZE = 64


def format_rpc_error(exc: BaseException, max_len: int = 240) -> str:
    text = " ".join(str(exc).split())
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


def is_transient_rpc_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        marker in msg
        for marker in (
            "cannot connect to rpc",
            "connection aborted",
            "connection reset",
            "remote disconnected",
            "temporarily unavailable",
            "timed out",
            "read timed out",
            "max retries exceeded",
            "ssl",
            "unexpected_eof",
            "eof occurred",
            "503",
            "502",
            "504",
            "429",
            "network",
        )
    )


def _build_retry() -> Optional[Any]:
    if Retry is None:
        return None
    retry_kwargs = {
        "total": DEFAULT_HTTP_RETRIES,
        "connect": DEFAULT_HTTP_RETRIES,
        "read": DEFAULT_HTTP_RETRIES,
        "status": DEFAULT_HTTP_RETRIES,
        "backoff_factor": 0.5,
        "status_forcelist": (429, 500, 502, 503, 504),
        "raise_on_status": False,
    }
    try:
        return Retry(allowed_methods=frozenset(["POST"]), **retry_kwargs)
    except TypeError:  # urllib3 < 1.26
        return Retry(method_whitelist=frozenset(["POST"]), **retry_kwargs)


def build_retry_session() -> Optional[Any]:
    if requests is None or HTTPAdapter is None:
        return None
    session = requests.Session()
    retry = _build_retry()
    adapter = HTTPAdapter(
        max_retries=retry if retry is not None else DEFAULT_HTTP_RETRIES,
        pool_connections=DEFAULT_POOL_CONNECTIONS,
        pool_maxsize=DEFAULT_POOL_MAXSIZE,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def build_web3(
    rpc_url: str,
    *,
    timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    connect_retries: int = DEFAULT_RPC_CONNECT_RETRIES,
    connect_retry_delay_seconds: int = DEFAULT_RPC_CONNECT_RETRY_DELAY_SECONDS,
) -> Web3:
    """Build a Web3 client with HTTP connection retries and POA middleware."""

    last_error: Optional[BaseException] = None
    for attempt in range(1, max(1, connect_retries) + 1):
        try:
            session = build_retry_session()
            provider = Web3.HTTPProvider(
                rpc_url,
                request_kwargs={"timeout": timeout_seconds},
                session=session,
            )
            w3 = Web3(provider)
            if geth_poa_middleware is not None:
                w3.middleware_onion.inject(geth_poa_middleware, layer=0)
            if not w3.is_connected():
                raise ConnectionError(f"Cannot connect to RPC: {rpc_url}")
            return w3
        except Exception as exc:
            last_error = exc
            if attempt >= max(1, connect_retries):
                break
            print(
                f"[rpc] connect failed ({attempt}/{connect_retries}): {format_rpc_error(exc)}. "
                f"Retrying in {connect_retry_delay_seconds}s...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(connect_retry_delay_seconds)
    raise ConnectionError(f"Cannot connect to RPC: {rpc_url}") from last_error

