"""Redaction helpers for public-safe operational diagnostics."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_SECRET_FIELD = re.compile(
    r"(api[-_]?key|authorization|bearer|cookie|credential|password|private[-_]?key|secret|token)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_CREDENTIAL_URL = re.compile(r"([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@", re.IGNORECASE)
_HOME_PATH = re.compile(r"/home/[^/\s]+")
_IP_ADDRESS = re.compile(
    r"(?<![0-9A-Fa-f:])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![0-9A-Fa-f:])"
)
_SSH_TARGET = re.compile(r"\b[A-Za-z0-9._-]+@(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+\b")
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[-_]?key|authorization|cookie|password|private[-_]?key|secret|token)\s*[:=]\s*[^\s,;]+"
)


def redact_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "")
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _CREDENTIAL_URL.sub(r"\1[REDACTED]@", text)
    text = _ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _HOME_PATH.sub("/home/[USER]", text)
    text = _SSH_TARGET.sub("[SSH_TARGET]", text)
    text = _IP_ADDRESS.sub("[IP_ADDRESS]", text)
    return text[:limit]


def safe_url(value: Any) -> str:
    try:
        parsed = urlsplit(str(value or ""))
    except ValueError:
        return ""
    if not parsed.scheme:
        return ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[str(key)] = "[REDACTED]" if _SECRET_FIELD.search(str(key)) else redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value, limit=1000)
    return value
