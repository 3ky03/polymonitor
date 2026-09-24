from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any, Dict


class PublishState:
    def __init__(self, path: str, *, max_entries: int | None = None) -> None:
        self.path = Path(path).expanduser()
        configured = max_entries
        if configured is None:
            try:
                configured = int(os.environ.get("POLYDATA_TELEGRAM_STATE_MAX_ENTRIES", "20000"))
            except ValueError:
                configured = 20000
        self.max_entries = max(100, int(configured or 20000))
        self.payload: Dict[str, Any] = {"sent": {}, "catalogPrimes": {}}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.payload = {"sent": {}, "catalogPrimes": {}}
            return
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            parsed = {}
        sent = parsed.get("sent") if isinstance(parsed, dict) else {}
        catalog_primes = parsed.get("catalogPrimes") if isinstance(parsed, dict) else {}
        self.payload = {
            "sent": sent if isinstance(sent, dict) else {},
            "catalogPrimes": catalog_primes if isinstance(catalog_primes, dict) else {},
        }

    def seen(self, target: str, dedupe_key: str) -> bool:
        key = self._key(target, dedupe_key)
        return key in self.payload.setdefault("sent", {})

    def mark(self, target: str, dedupe_key: str) -> None:
        key = self._key(target, dedupe_key)
        self.payload.setdefault("sent", {})[key] = {"sentAt": int(time.time())}
        self._trim()

    def catalog_primed(self, version: str) -> bool:
        return str(version or "") in self.payload.setdefault("catalogPrimes", {})

    def mark_catalog_primed(self, version: str) -> None:
        clean_version = str(version or "").strip()
        if clean_version:
            self.payload.setdefault("catalogPrimes", {})[clean_version] = {
                "primedAt": int(time.time())
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self.payload, ensure_ascii=True, sort_keys=True, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def _trim(self) -> None:
        sent = self.payload.setdefault("sent", {})
        if len(sent) <= self.max_entries:
            return
        ordered = sorted(sent.items(), key=lambda item: int((item[1] or {}).get("sentAt") or 0), reverse=True)
        self.payload["sent"] = dict(ordered[: self.max_entries])

    @staticmethod
    def _key(target: str, dedupe_key: str) -> str:
        return f"{target}:{dedupe_key}"


@contextlib.contextmanager
def state_lock(state_path: str):
    lock_path = Path(state_path).expanduser().with_suffix(Path(state_path).suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
