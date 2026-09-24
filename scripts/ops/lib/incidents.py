"""Transition-based incident history with stable signatures and bounded retention."""

from __future__ import annotations

import hashlib
import fcntl
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .redaction import redact_text
from .snapshot import atomic_write_json, parse_timestamp, read_json, utc_now_iso
from .status import Status, is_actionable, normalize


def incident_signature(component: str, status: Any, summary: str = "") -> str:
    material = f"{component}\0{normalize(status).value}\0{redact_text(summary, limit=160)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def update_incident_history(
    path: str | Path,
    observations: Iterable[dict[str, Any]],
    *,
    max_items: int = 50,
    retention_days: int = 7,
) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = target.with_suffix(f"{target.suffix}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            previous = read_json(target)
        except (OSError, ValueError):
            previous = {}
        items = previous.get("items") if isinstance(previous.get("items"), list) else []
        open_by_component = {
            str(item.get("component")): item
            for item in items
            if isinstance(item, dict) and not item.get("resolvedAt")
        }
        timestamp = utc_now_iso()

        for observation in observations:
            component = str(observation.get("component") or "").strip()
            if not component:
                continue
            current = normalize(observation.get("status"))
            existing = open_by_component.get(component)
            if not is_actionable(current):
                if existing is not None:
                    existing["resolvedAt"] = timestamp
                    existing["lastObservedAt"] = timestamp
                    existing["resolutionStatus"] = current.value
                continue
            summary = redact_text(observation.get("summary"), limit=240)
            signature = incident_signature(component, current, summary)
            if existing and existing.get("signature") == signature:
                existing["lastObservedAt"] = timestamp
                existing["observations"] = int(existing.get("observations") or 1) + 1
                continue
            if existing is not None:
                existing["resolvedAt"] = timestamp
                existing["lastObservedAt"] = timestamp
                existing["resolutionStatus"] = Status.UNKNOWN.value
            created = {
                "signature": signature,
                "component": component,
                "status": current.value,
                "summary": summary,
                "openedAt": timestamp,
                "lastObservedAt": timestamp,
                "resolvedAt": None,
                "observations": 1,
            }
            items.append(created)
            open_by_component[component] = created

        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
        retained = []
        for item in items:
            observed = parse_timestamp(item.get("lastObservedAt")) if isinstance(item, dict) else None
            if observed is None or observed >= cutoff or not item.get("resolvedAt"):
                retained.append(item)
        retained.sort(key=lambda item: str(item.get("lastObservedAt") or ""), reverse=True)
        payload = {
            "schemaVersion": "polymonitor.incidents.v1",
            "generatedAt": timestamp,
            "items": retained[: max(1, max_items)],
        }
        atomic_write_json(target, payload)
        return payload
