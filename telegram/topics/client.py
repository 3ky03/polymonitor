from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests


MAX_TELEGRAM_TEXT_LENGTH = 4096
SAFE_TEXT_LENGTH = 3900
MAX_SEND_ATTEMPTS = 3


def split_message(text: str, *, limit: int = SAFE_TEXT_LENGTH) -> List[str]:
    clean = str(text or "").strip()
    if len(clean) <= limit:
        return [clean] if clean else []
    chunks: List[str] = []
    remaining = clean
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [chunk for chunk in chunks if chunk]


def _retry_after_seconds(response: requests.Response, body: Dict[str, Any]) -> int:
    parameters = body.get("parameters") if isinstance(body.get("parameters"), dict) else {}
    values = [parameters.get("retry_after"), response.headers.get("Retry-After")]
    for value in values:
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            continue
        return max(1, seconds)
    return 5


def _telegram_error_message(response: requests.Response, body: Dict[str, Any]) -> str:
    description = body.get("description") if isinstance(body, dict) else ""
    if description:
        return f"Telegram sendMessage failed HTTP {response.status_code}: {description}"
    return f"Telegram sendMessage failed HTTP {response.status_code}"


class TelegramClient:
    def __init__(self, *, bot_token: str, api_base: str = "https://api.telegram.org", timeout_seconds: int = 12) -> None:
        self.bot_token = str(bot_token or "").strip()
        self.api_base = str(api_base or "https://api.telegram.org").rstrip("/")
        self.timeout_seconds = max(1, int(timeout_seconds or 12))
        self.session = requests.Session()
        self.session.trust_env = False

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        message_thread_id: Optional[int] = None,
        disable_web_page_preview: bool = True,
        disable_notification: bool = False,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not self.bot_token:
            raise RuntimeError("POLYDATA_TELEGRAM_BOT_TOKEN is required unless dry-run is enabled")
        results: List[Dict[str, Any]] = []
        for chunk in split_message(text):
            payload: Dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk[:MAX_TELEGRAM_TEXT_LENGTH],
                "disable_web_page_preview": disable_web_page_preview,
                "disable_notification": disable_notification,
            }
            if message_thread_id is not None:
                payload["message_thread_id"] = message_thread_id
            if reply_markup:
                payload["reply_markup"] = reply_markup
            body: Dict[str, Any] = {}
            response: Optional[requests.Response] = None
            for attempt in range(MAX_SEND_ATTEMPTS):
                response = self.session.post(
                    f"{self.api_base}/bot{self.bot_token}/sendMessage",
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                if response.status_code == 429 and attempt < MAX_SEND_ATTEMPTS - 1:
                    time.sleep(_retry_after_seconds(response, body) + 1)
                    continue
                break
            if response is None:
                raise RuntimeError("Telegram sendMessage failed before request was sent")
            if response.status_code >= 400:
                raise RuntimeError(_telegram_error_message(response, body))
            if not body.get("ok"):
                raise RuntimeError(f"Telegram sendMessage failed: {body}")
            results.append(body)
        return results
