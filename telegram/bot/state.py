from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional


class BotState:
    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser()
        self.data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self.data = {}
            return
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            self.data = {}

    @property
    def offset(self) -> Optional[int]:
        value = self.data.get("offset")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def mark_update(self, update_id: int) -> None:
        self.data["offset"] = int(update_id) + 1

    def rate_limited(self, *, chat_id: int | str, user_id: int | None, limit: int, window_seconds: int = 60) -> bool:
        now = time.time()
        key = f"{chat_id}:{user_id or 'anon'}"
        buckets = self.data.get("rateLimits")
        if not isinstance(buckets, dict):
            buckets = {}
            self.data["rateLimits"] = buckets
        raw_entries = buckets.get(key)
        entries = raw_entries if isinstance(raw_entries, list) else []
        fresh = [float(ts) for ts in entries if isinstance(ts, (int, float)) and now - float(ts) < window_seconds]
        if len(fresh) >= max(1, int(limit or 1)):
            buckets[key] = fresh
            return True
        fresh.append(now)
        buckets[key] = fresh
        return False

    def record_user(self, *, chat_id: int | str, user_id: int | None) -> None:
        if user_id is None:
            return
        users = self.data.get("users")
        if not isinstance(users, dict):
            users = {}
            self.data["users"] = users
        key = str(user_id)
        now = int(time.time())
        existing = users.get(key) if isinstance(users.get(key), dict) else {}
        users[key] = {
            **existing,
            "userId": int(user_id),
            "chatId": str(chat_id),
            "firstSeenTs": int(existing.get("firstSeenTs") or now),
            "lastSeenTs": now,
        }

    def record_query(
        self,
        *,
        chat_id: int | str,
        user_id: int | None,
        command: str,
        args: str = "",
        matched: bool = True,
    ) -> None:
        analytics = self.data.get("queryAnalytics")
        if not isinstance(analytics, dict):
            analytics = {}
            self.data["queryAnalytics"] = analytics
        commands = analytics.get("commands")
        if not isinstance(commands, dict):
            commands = {}
            analytics["commands"] = commands
        command_key = str(command or "unknown").lower()
        commands[command_key] = int(commands.get(command_key) or 0) + 1
        clean_args = " ".join(str(args or "").strip().lower().split())[:96]
        if clean_args:
            queries = analytics.get("queries")
            if not isinstance(queries, dict):
                queries = {}
                analytics["queries"] = queries
            queries[clean_args] = int(queries.get(clean_args) or 0) + 1
        if not matched:
            misses = analytics.get("misses")
            if not isinstance(misses, list):
                misses = []
                analytics["misses"] = misses
            misses.append({"command": command_key, "args": clean_args, "chatId": str(chat_id), "userId": user_id, "ts": int(time.time())})
            analytics["misses"] = misses[-100:]
        analytics["lastQuery"] = {
            "command": command_key,
            "args": clean_args,
            "chatId": str(chat_id),
            "userId": user_id,
            "matched": bool(matched),
            "ts": int(time.time()),
        }

    @property
    def last_alert_check_ts(self) -> float:
        try:
            return float(self.data.get("lastAlertCheckTs") or 0)
        except (TypeError, ValueError):
            return 0.0

    def mark_alert_check(self, timestamp: float) -> None:
        self.data["lastAlertCheckTs"] = float(timestamp)

    def alerts(self) -> list[Dict[str, Any]]:
        alerts = self.data.get("alerts")
        if not isinstance(alerts, list):
            alerts = []
            self.data["alerts"] = alerts
        return alerts

    def next_alert_id(self) -> int:
        current = int(self.data.get("nextAlertId") or 1)
        self.data["nextAlertId"] = current + 1
        return current

    def add_alert(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        self.alerts().append(alert)
        return alert

    def active_alerts(self) -> list[Dict[str, Any]]:
        return [alert for alert in self.alerts() if alert.get("enabled", True) and not alert.get("triggeredAt")]

    def alerts_for(self, *, chat_id: int | str, user_id: int | None = None) -> list[Dict[str, Any]]:
        result = []
        for alert in self.alerts():
            if str(alert.get("chatId")) != str(chat_id):
                continue
            if user_id is not None and alert.get("userId") not in (None, user_id):
                continue
            if alert.get("enabled", True) and not alert.get("triggeredAt"):
                result.append(alert)
        return result

    def remove_alert(self, *, alert_id: int, chat_id: int | str, user_id: int | None = None) -> bool:
        for alert in self.alerts():
            if int(alert.get("id") or 0) != int(alert_id):
                continue
            if str(alert.get("chatId")) != str(chat_id):
                continue
            if user_id is not None and alert.get("userId") not in (None, user_id):
                continue
            alert["enabled"] = False
            return True
        return False

    def mark_alert_triggered(self, *, alert_id: int, timestamp: str, price: float) -> None:
        for alert in self.alerts():
            if int(alert.get("id") or 0) == int(alert_id):
                alert["triggeredAt"] = timestamp
                alert["triggeredPrice"] = price
                alert["enabled"] = False
                return

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
