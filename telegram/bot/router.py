from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .models import CommandRequest


COMMAND_RE = re.compile(r"^/([A-Za-z0-9_\u4e00-\u9fff]+)(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$", re.DOTALL)


def parse_update(update: Dict[str, Any]) -> Optional[CommandRequest]:
    callback = update.get("callback_query") if isinstance(update.get("callback_query"), dict) else None
    if callback:
        data = str(callback.get("data") or "").strip()
        message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
        text = data if data.startswith("/") else f"/{data}"
        match = COMMAND_RE.match(text)
        if not match:
            return None
        return CommandRequest(
            update_id=int(update.get("update_id") or 0),
            chat_id=(message.get("chat") or {}).get("id", ""),
            user_id=(callback.get("from") or {}).get("id"),
            message_id=message.get("message_id"),
            text=text,
            command=match.group(1).lower(),
            args=str(match.group(2) or "").strip(),
            raw=update,
            callback_query_id=str(callback.get("id") or ""),
        )
    message = update.get("message") if isinstance(update.get("message"), dict) else None
    if not message:
        return None
    text = str(message.get("text") or "").strip()
    if not text:
        return None
    match = COMMAND_RE.match(text)
    if not match:
        return CommandRequest(
            update_id=int(update.get("update_id") or 0),
            chat_id=(message.get("chat") or {}).get("id", ""),
            user_id=(message.get("from") or {}).get("id"),
            message_id=message.get("message_id"),
            text=text,
            command="help",
            args="",
            raw=update,
        )
    return CommandRequest(
        update_id=int(update.get("update_id") or 0),
        chat_id=(message.get("chat") or {}).get("id", ""),
        user_id=(message.get("from") or {}).get("id"),
        message_id=message.get("message_id"),
        text=text,
        command=match.group(1).lower(),
        args=str(match.group(2) or "").strip(),
        raw=update,
    )
