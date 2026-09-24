from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests


@dataclass(frozen=True)
class ApiBaseResolution:
    base_url: str
    healthy: bool
    checked: tuple[str, ...] = ()
    errors: Dict[str, str] = field(default_factory=dict)


def _health_url(base_url: str) -> str:
    return f"{str(base_url or '').rstrip('/')}/health"


def resolve_polydata_api_base(
    candidates: tuple[str, ...] | list[str],
    *,
    timeout_seconds: int = 3,
    session: Optional[requests.Session] = None,
) -> ApiBaseResolution:
    clean_candidates = tuple(dict.fromkeys(str(candidate or "").strip().rstrip("/") for candidate in candidates if str(candidate or "").strip()))
    if not clean_candidates:
        return ApiBaseResolution(base_url="", healthy=False)
    client = session or requests.Session()
    if session is None:
        client.trust_env = False
    errors: Dict[str, str] = {}
    checked: list[str] = []
    for base_url in clean_candidates:
        checked.append(base_url)
        try:
            response = client.get(_health_url(base_url), timeout=max(1, int(timeout_seconds or 3)))
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            errors[base_url] = str(exc)
            continue
        if isinstance(payload, dict) and str(payload.get("status") or "").lower() == "ok":
            return ApiBaseResolution(base_url=base_url, healthy=True, checked=tuple(checked), errors=errors)
        errors[base_url] = f"health status was {payload!r}"
    return ApiBaseResolution(base_url=clean_candidates[0], healthy=False, checked=tuple(checked), errors=errors)


class PolyDataApiClient:
    def __init__(self, *, base_url: str, timeout_seconds: int = 12) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout_seconds = max(1, int(timeout_seconds or 12))
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "polydata-telegram-publisher/1.0",
                "X-PolyData-Telegram-Publisher": "1",
            }
        )

    def isolated(self, *, timeout_seconds: Optional[int] = None) -> "PolyDataApiClient":
        return PolyDataApiClient(
            base_url=self.base_url,
            timeout_seconds=self.timeout_seconds if timeout_seconds is None else timeout_seconds,
        )

    def get_json(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{str(path).lstrip('/')}"
        timeout = self.timeout_seconds if timeout_seconds is None else max(1, int(timeout_seconds))
        response = self.session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"items": [], "status": "invalid", "raw": payload}
