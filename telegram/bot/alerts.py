from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import requests

from .formatters import crypto_price_map, format_alert_triggered
from .models import BotReply
from .polydata_api import PolyDataBotApi
from .state import BotState


def _triggered(alert: dict, prices: dict[str, float]) -> tuple[bool, float | None]:
    symbol = str(alert.get("symbol") or "").upper()
    price = prices.get(symbol)
    if price is None:
        return False, None
    threshold = float(alert.get("threshold") or 0)
    if alert.get("direction") == "below":
        return price <= threshold, price
    return price >= threshold, price


def due_alert_replies(state: BotState, api: PolyDataBotApi) -> Iterable[tuple[int | str, int, BotReply, float]]:
    try:
        prices = crypto_price_map(api.crypto_markets())
    except requests.RequestException:
        return []
    replies = []
    for alert in state.active_alerts():
        is_triggered, price = _triggered(alert, prices)
        if not is_triggered or price is None:
            continue
        replies.append((alert.get("chatId"), int(alert.get("id") or 0), BotReply(format_alert_triggered(alert, price)), price))
    return replies


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
