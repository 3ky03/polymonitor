from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class CommandRequest:
    update_id: int
    chat_id: int | str
    user_id: Optional[int]
    message_id: Optional[int]
    text: str
    command: str
    args: str
    raw: Dict[str, Any]
    callback_query_id: Optional[str] = None


@dataclass(frozen=True)
class BotReply:
    text: str
    link_preview: bool = False
    reply_markup: Optional[Dict[str, Any]] = None
    callback_query_id: Optional[str] = None
