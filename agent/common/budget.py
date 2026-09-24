from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Linux fallback
    fcntl = None


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _daily_limit() -> int:
    try:
        return max(0, int(os.environ.get("POLYDATA_AGENT_DAILY_LIVE_CALL_LIMIT", "4")))
    except ValueError:
        return 4


def _state_path() -> Path:
    raw = os.environ.get("POLYDATA_AGENT_BUDGET_STATE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "polydata" / "agent-budget.json"


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _empty_state() -> dict[str, Any]:
    return {
        "date": _today_key(),
        "total": 0,
        "kinds": {},
        "updatedAt": int(time.time()),
    }


def claim_agent_live_call(kind: str, amount: int = 1) -> tuple[bool, dict[str, Any]]:
    """Claim one budget unit before an outbound Agent API call.

    The budget is intentionally process-independent so API routes, seed scripts,
    and manual jobs share the same daily cap.
    """
    if _truthy_env("POLYDATA_AGENT_BUDGET_DISABLED", False):
        return True, {"enabled": False, "limit": None, "remaining": None}

    limit = _daily_limit()
    kind_key = str(kind or "agent").strip() or "agent"
    amount = max(1, int(amount or 1))
    if limit <= 0:
        return False, {"enabled": True, "limit": limit, "used": 0, "remaining": 0, "kind": kind_key}

    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            raw = handle.read().strip()
            state = json.loads(raw) if raw else _empty_state()
            if not isinstance(state, dict) or state.get("date") != _today_key():
                state = _empty_state()
            total = int(state.get("total") or 0)
            if total + amount > limit:
                return False, {
                    "enabled": True,
                    "limit": limit,
                    "used": total,
                    "remaining": max(0, limit - total),
                    "kind": kind_key,
                }
            kinds = state.get("kinds") if isinstance(state.get("kinds"), dict) else {}
            kinds[kind_key] = int(kinds.get(kind_key) or 0) + amount
            state["total"] = total + amount
            state["kinds"] = kinds
            state["updatedAt"] = int(time.time())
            handle.seek(0)
            handle.truncate()
            json.dump(state, handle, ensure_ascii=True, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
            return True, {
                "enabled": True,
                "limit": limit,
                "used": state["total"],
                "remaining": max(0, limit - state["total"]),
                "kind": kind_key,
            }
    except Exception:
        return False, {"enabled": True, "limit": limit, "used": None, "remaining": 0, "kind": kind_key}
