from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def headers() -> Dict[str, str]:
    return {"Accept": "application/json", "User-Agent": "polydata-worldcup-dashboard/1.0"}


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def mean(values: Iterable[float]) -> Optional[float]:
    rows = [value for value in values if value == value]
    return sum(rows) / len(rows) if rows else None


def parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def tokenize(value: Any) -> List[str]:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(part for part in text if not unicodedata.combining(part))
    return [part for part in re.split(r"[^0-9a-z]+", text.lower()) if part]


def team_tokens(value: Any) -> set[str]:
    text = unicodedata.normalize("NFKD", str(value or "").lower().replace("&", " and "))
    text = text.encode("ascii", "ignore").decode("ascii")
    aliases = {
        "usa": "united states america us",
        "us": "united states america usa",
        "south korea": "korea republic korea",
        "czechia": "czech republic czech",
        "turkiye": "turkey turkiye",
        "türkiye": "turkey turkiye",
        "united states": "usa us america",
        "bosnia herzegovina": "bosnia herzogovina",
        "bosnia and herzegovina": "bosnia herzegovina",
        "cote d ivoire": "ivory coast civ",
        "ivory coast": "cote d ivoire civ",
        "turkey": "turkiye tur",
        "turkiye": "turkey tur",
    }
    expanded = text
    for alias, extra in aliases.items():
        if alias in text:
            expanded += " " + extra
    return {token for token in tokenize(expanded) if len(token) > 1}
