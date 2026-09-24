from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable

from .client import TelegramClient
from .config import TelegramSettings, load_settings
from .formatters import format_panel_snapshot_outcome
from .models import MessageCandidate
from .publisher import publish_candidates
from .state import PublishState, state_lock


_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="polydata-telegram")


def publish_panel_snapshot(panel_id: str, payload: Dict[str, Any]) -> None:
    """Publish a runtime panel payload in the background after an API fetch."""
    try:
        settings = load_settings()
    except Exception as exc:
        _log(f"settings-load-failed panel={panel_id} error_class={exc.__class__.__name__}")
        return
    if not _enabled(settings):
        return
    if not isinstance(payload, dict):
        return
    _EXECUTOR.submit(_format_and_publish, panel_id, dict(payload), settings)


def _format_and_publish(panel_id: str, payload: Dict[str, Any], settings: TelegramSettings) -> None:
    outcome = format_panel_snapshot_outcome(panel_id, payload)
    if not outcome.candidates:
        _log(
            "panel=%s mode=%s candidates=0 reason=%s"
            % (panel_id, outcome.mode, outcome.reason or "empty")
        )
        return
    _publish_candidates(outcome.candidates, settings, panel_id)


def _enabled(settings: TelegramSettings) -> bool:
    return bool(settings.publish_on_api_fetch and (settings.bot_token or settings.dry_run))


def _publish_candidates(candidates: Iterable[MessageCandidate], settings: TelegramSettings, panel_id: str) -> None:
    try:
        with state_lock(settings.state_path):
            state = PublishState(settings.state_path)
            telegram = TelegramClient(
                bot_token=settings.bot_token,
                api_base=settings.telegram_api_base,
                timeout_seconds=settings.request_timeout_seconds,
            )
            result = publish_candidates(
                list(candidates),
                settings=settings,
                state=state,
                telegram=telegram,
                dry_run=settings.dry_run,
            )
        if result.sent or result.skipped_seen or result.skipped_unconfigured or result.failed_sends:
            _log(
                "panel=%s candidates=%s sent=%s failed_sends=%s skipped_seen=%s skipped_unconfigured=%s"
                % (panel_id, result.candidates, result.sent, result.failed_sends, result.skipped_seen, result.skipped_unconfigured)
            )
    except Exception as exc:
        _log(f"publish-failed panel={panel_id} error_class={exc.__class__.__name__}")


def _log(message: str) -> None:
    print(f"[telegram-runtime-bridge] {message}", file=sys.stderr)
