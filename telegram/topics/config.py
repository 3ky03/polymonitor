from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
class TopicConfig:
    name: str
    chat_id: str
    message_thread_id: Optional[int] = None

    @property
    def enabled(self) -> bool:
        return bool(self.chat_id.strip())


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    telegram_api_base: str
    polydata_api_base: str
    polydata_api_candidates: tuple[str, ...]
    state_path: str
    request_timeout_seconds: int
    watch_interval_seconds: int
    dry_run: bool
    disable_notification: bool
    publish_on_api_fetch: bool
    news: TopicConfig
    intel: TopicConfig
    alpha: TopicConfig
    macro: TopicConfig
    nba: TopicConfig
    worldcup: TopicConfig
    weather: TopicConfig
    monitor: TopicConfig

    def topic_config(self, topic: str) -> TopicConfig:
        if topic == "news":
            return self.news
        if topic == "intel":
            return self.intel
        if topic == "alpha":
            return self.alpha
        if topic == "macro":
            return self.macro
        if topic == "nba":
            return self.nba
        if topic == "worldcup":
            return self.worldcup
        if topic == "weather":
            return self.weather
        if topic == "monitor":
            return self.monitor
        return TopicConfig(name=topic, chat_id="")


def _topic(name: str, chat_env: str, thread_env: str) -> TopicConfig:
    return _topic_from_candidates(name, ((chat_env, thread_env),))


def _topic_from_candidates(name: str, candidates: Iterable[tuple[str, str]]) -> TopicConfig:
    chat_id = ""
    raw_thread = ""
    for chat_env, thread_env in candidates:
        chat_id = chat_id or _get_str(chat_env, "")
        raw_thread = raw_thread or _get_str(thread_env, "")
    message_thread_id = None
    if raw_thread:
        try:
            message_thread_id = int(raw_thread)
        except ValueError:
            message_thread_id = None
    return TopicConfig(name=name, chat_id=chat_id, message_thread_id=message_thread_id)


def load_settings() -> TelegramSettings:
    _load_dotenv_files()
    api_port = _get_int("POLYDATA_API_PORT", 18500)
    local_api_base = f"http://127.0.0.1:{api_port}"
    local_default_api_base = "http://127.0.0.1:18500"
    explicit_api_base = _get_str("POLYDATA_TELEGRAM_POLYDATA_API_BASE", "")
    remote_api_base = _get_str(
        "POLYDATA_TELEGRAM_REMOTE_API_BASE",
        _get_str("POLYDATA_REMOTE_API_BASE", _get_str("POLYDATA_PUBLIC_API_BASE", "")),
    )
    polydata_api_candidates = _unique_urls(
        (
            explicit_api_base,
            remote_api_base,
            local_default_api_base,
            local_api_base,
            _get_str("VITE_POLYDATA_API_BASE_URL", ""),
        )
    )
    default_api_base = polydata_api_candidates[0] if polydata_api_candidates else local_default_api_base
    return TelegramSettings(
        bot_token=_get_str("POLYDATA_TELEGRAM_BOT_TOKEN", ""),
        telegram_api_base=_get_str("POLYDATA_TELEGRAM_API_BASE", "https://api.telegram.org"),
        polydata_api_base=default_api_base,
        polydata_api_candidates=polydata_api_candidates,
        state_path=_get_str("POLYDATA_TELEGRAM_STATE_PATH", str(PROJECT_ROOT / "data" / "telegram_state.json")),
        request_timeout_seconds=max(3, _get_int("POLYDATA_TELEGRAM_TIMEOUT_SECONDS", 12)),
        watch_interval_seconds=max(15, _get_int("POLYDATA_TELEGRAM_WATCH_INTERVAL_SECONDS", 60)),
        dry_run=_get_bool("POLYDATA_TELEGRAM_DRY_RUN", False),
        disable_notification=_get_bool("POLYDATA_TELEGRAM_DISABLE_NOTIFICATION", False),
        publish_on_api_fetch=_get_bool("POLYDATA_TELEGRAM_PUBLISH_ON_API_FETCH", False),
        news=_topic("news", "POLYDATA_TELEGRAM_CHANNEL_NEWS", "POLYDATA_TELEGRAM_THREAD_NEWS"),
        intel=_topic_from_candidates(
            "intel",
            (
                ("POLYDATA_TELEGRAM_CHANNEL_INTEL", "POLYDATA_TELEGRAM_THREAD_INTEL"),
                ("POLYDATA_TELEGRAM_CHANNEL_MARKET_INTEL", "POLYDATA_TELEGRAM_THREAD_MARKET_INTEL"),
            ),
        ),
        alpha=_topic("alpha", "POLYDATA_TELEGRAM_CHANNEL_ALPHA", "POLYDATA_TELEGRAM_THREAD_ALPHA"),
        macro=_topic("macro", "POLYDATA_TELEGRAM_CHANNEL_MACRO", "POLYDATA_TELEGRAM_THREAD_MACRO"),
        nba=_topic("nba", "POLYDATA_TELEGRAM_CHANNEL_NBA", "POLYDATA_TELEGRAM_THREAD_NBA"),
        worldcup=_topic("worldcup", "POLYDATA_TELEGRAM_CHANNEL_WORLDCUP", "POLYDATA_TELEGRAM_THREAD_WORLDCUP"),
        weather=_topic("weather", "POLYDATA_TELEGRAM_CHANNEL_WEATHER", "POLYDATA_TELEGRAM_THREAD_WEATHER"),
        monitor=_topic_from_candidates(
            "monitor",
            (
                ("POLYDATA_TELEGRAM_CHANNEL_MONITOR", "POLYDATA_TELEGRAM_THREAD_MONITOR"),
                ("POLYDATA_TELEGRAM_CHANNEL_ANNOUNCEMENTS", "POLYDATA_TELEGRAM_THREAD_ANNOUNCEMENTS"),
                ("POLYDATA_TELEGRAM_CHANNEL_ANNOUNCEMENT", "POLYDATA_TELEGRAM_THREAD_ANNOUNCEMENT"),
            ),
        ),
    )
