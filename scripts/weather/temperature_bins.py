from __future__ import annotations

import re
from typing import Any, Dict, Optional


_TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?", re.IGNORECASE)
_RANGE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:-|to|–)\s*(-?\d+(?:\.\d+)?)\s*°?\s*([CF])?", re.IGNORECASE)


def _float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def parse_temperature_bin(label: Any, *, default_unit: str = "F") -> Optional[Dict[str, Any]]:
    text = str(label or "").strip()
    if not text:
        return None
    unit = (default_unit or "F").upper()
    range_match = _RANGE_RE.search(text)
    if range_match:
        low = _float(range_match.group(1))
        high = _float(range_match.group(2))
        if range_match.group(3):
            unit = range_match.group(3).upper()
        if low is None or high is None:
            return None
        return {"label": text, "bucketType": "range", "minTemp": low, "maxTemp": high, "unit": unit, "sortKey": low}
    match = _TEMP_RE.search(text)
    if not match:
        return None
    value = _float(match.group(1))
    if value is None:
        return None
    if match.group(2):
        unit = match.group(2).upper()
    lowered = text.lower()
    if "below" in lowered or "or less" in lowered or "under" in lowered:
        bucket_type = "below"
        min_temp = None
        max_temp = value
    elif "higher" in lowered or "above" in lowered or "or more" in lowered:
        bucket_type = "above"
        min_temp = value
        max_temp = None
    else:
        bucket_type = "range"
        min_temp = value
        max_temp = value
    return {"label": text, "bucketType": bucket_type, "minTemp": min_temp, "maxTemp": max_temp, "unit": unit, "sortKey": value}

