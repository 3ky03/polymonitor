from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
_LOADED_ENV_FILES: set[Path] = set()


def _candidate_env_files() -> Iterable[Path]:
    explicit = os.environ.get("POLYDATA_AGENT_ENV_PATH")
    if explicit:
        yield Path(explicit).expanduser()
    yield REPO_ROOT / ".env"


def load_agent_env() -> None:
    for candidate in _candidate_env_files():
        if candidate in _LOADED_ENV_FILES or not candidate.exists():
            continue
        try:
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue
                os.environ[key] = value.strip().strip('"').strip("'")
            _LOADED_ENV_FILES.add(candidate)
        except OSError:
            continue


def get_env(name: str, default: str = "") -> str:
    load_agent_env()
    return os.environ.get(name, default)


def get_float_env(name: str, default: float) -> float:
    raw = get_env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def get_int_env(name: str, default: int) -> int:
    raw = get_env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_bool_env(name: str, default: bool = False) -> bool:
    raw = get_env(name)
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
