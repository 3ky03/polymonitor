from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local"):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def _get_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _get_first(names: Iterable[str], default: str = "") -> str:
    for name in names:
        value = _get_str(name, "")
        if value:
            return value
    return default


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _csv_set(raw: str) -> set[str]:
    return {part.strip() for part in str(raw or "").split(",") if part.strip()}


def _csv_int_set(raw: str) -> set[int]:
    values: set[int] = set()
    for part in _csv_set(raw):
        try:
            values.add(int(part))
        except ValueError:
            continue
    return values


def _unique_urls(values: Iterable[str]) -> tuple[str, ...]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = str(value or "").strip().rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return tuple(urls)


@dataclass(frozen=True)
class BotSettings:
    bot_token: str
    telegram_api_base: str
    polydata_api_base: str
    polydata_api_candidates: tuple[str, ...]
    state_path: str
    request_timeout_seconds: int
    poll_interval_seconds: int
    long_poll_timeout_seconds: int
    alert_check_interval_seconds: int
    dry_run: bool
    allowed_chat_ids: set[str]
    admin_user_ids: set[int]
    rate_limit_per_minute: int

    def chat_allowed(self, chat_id: int | str, user_id: int | None = None) -> bool:
        if user_id is not None and user_id in self.admin_user_ids:
            return True
        if not self.allowed_chat_ids and not self.admin_user_ids:
            return True
        return str(chat_id) in self.allowed_chat_ids


def load_settings() -> BotSettings:
    _load_dotenv_files()
    api_port = _get_int("POLYDATA_API_PORT", 18500)
    local_api_base = f"http://127.0.0.1:{api_port}"
    query_bot_token = _get_str("POLYDATA_TELEGRAM_QUERY_BOT_TOKEN", "")
    default_state_path = str(PROJECT_ROOT / "data" / "telegram_query_bot_state.json") if query_bot_token else _get_str("POLYDATA_TELEGRAM_BOT_STATE_PATH", str(PROJECT_ROOT / "data" / "telegram_bot_state.json"))
    polydata_api_candidates = _unique_urls(
        (
            _get_str("POLYDATA_TELEGRAM_BOT_POLYDATA_API_BASE", ""),
            _get_str("POLYDATA_TELEGRAM_POLYDATA_API_BASE", ""),
            _get_str("POLYDATA_TELEGRAM_REMOTE_API_BASE", ""),
            _get_str("POLYDATA_REMOTE_API_BASE", ""),
            _get_str("POLYDATA_PUBLIC_API_BASE", ""),
            _get_str("VITE_POLYDATA_API_BASE_URL", ""),
            "http://127.0.0.1:18500",
            local_api_base,
        )
    )
    polydata_api_base = polydata_api_candidates[0] if polydata_api_candidates else local_api_base
    return BotSettings(
        bot_token=_get_first(("POLYDATA_TELEGRAM_QUERY_BOT_TOKEN", "POLYDATA_TELEGRAM_BOT_TOKEN", "POLYDATA_TELEGRAM_TOKEN"), ""),
        telegram_api_base=_get_first(("POLYDATA_TELEGRAM_BOT_API_BASE", "POLYDATA_TELEGRAM_API_BASE"), "https://api.telegram.org"),
        polydata_api_base=polydata_api_base.rstrip("/"),
        polydata_api_candidates=polydata_api_candidates,
        state_path=_get_first(
            ("POLYDATA_TELEGRAM_QUERY_BOT_STATE_PATH",),
            default_state_path,
        ),
        request_timeout_seconds=max(3, _get_int("POLYDATA_TELEGRAM_QUERY_BOT_TIMEOUT_SECONDS", _get_int("POLYDATA_TELEGRAM_BOT_TIMEOUT_SECONDS", _get_int("POLYDATA_TELEGRAM_TIMEOUT_SECONDS", 12)))),
        poll_interval_seconds=max(1, _get_int("POLYDATA_TELEGRAM_QUERY_BOT_POLL_INTERVAL_SECONDS", _get_int("POLYDATA_TELEGRAM_BOT_POLL_INTERVAL_SECONDS", 2))),
        long_poll_timeout_seconds=max(1, _get_int("POLYDATA_TELEGRAM_QUERY_BOT_LONG_POLL_TIMEOUT_SECONDS", _get_int("POLYDATA_TELEGRAM_BOT_LONG_POLL_TIMEOUT_SECONDS", 25))),
        alert_check_interval_seconds=max(5, _get_int("POLYDATA_TELEGRAM_QUERY_BOT_ALERT_CHECK_INTERVAL_SECONDS", _get_int("POLYDATA_TELEGRAM_BOT_ALERT_CHECK_INTERVAL_SECONDS", 30))),
        dry_run=_get_bool("POLYDATA_TELEGRAM_QUERY_BOT_DRY_RUN", _get_bool("POLYDATA_TELEGRAM_BOT_DRY_RUN", _get_bool("POLYDATA_TELEGRAM_DRY_RUN", False))),
        allowed_chat_ids=_csv_set(_get_first(("POLYDATA_TELEGRAM_QUERY_BOT_ALLOWED_CHAT_IDS", "POLYDATA_TELEGRAM_BOT_ALLOWED_CHAT_IDS"), "")),
        admin_user_ids=_csv_int_set(_get_first(("POLYDATA_TELEGRAM_QUERY_BOT_ADMIN_USER_IDS", "POLYDATA_TELEGRAM_BOT_ADMIN_USER_IDS"), "")),
        rate_limit_per_minute=max(1, _get_int("POLYDATA_TELEGRAM_QUERY_BOT_RATE_LIMIT_PER_MINUTE", _get_int("POLYDATA_TELEGRAM_BOT_RATE_LIMIT_PER_MINUTE", 20))),
    )
