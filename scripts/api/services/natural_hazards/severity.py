from __future__ import annotations

from typing import Any, Dict

from .contracts import SEVERITY_MAPPING_VERSION
from .normalize import finite_number


def usgs_severity(properties: Dict[str, Any]) -> tuple[str, Dict[str, str]]:
    pager = str(properties.get("alert") or "").strip().lower()
    significance = finite_number(properties.get("sig")) or 0
    magnitude = finite_number(properties.get("mag")) or 0
    tsunami = bool(properties.get("tsunami"))
    if pager in {"red", "orange"} or significance >= 1000:
        severity = "critical"
    elif pager == "yellow" or tsunami or significance >= 600 or magnitude >= 6.5:
        severity = "warning"
    elif pager == "green" or significance >= 300 or magnitude >= 5:
        severity = "watch"
    else:
        severity = "info"
    reason = f"USGS PAGER={pager or 'none'}, significance={int(significance)}, magnitude={magnitude:.1f}, tsunami={tsunami}"
    return severity, {
        "provider": "USGS",
        "rawLevel": pager or f"sig-{int(significance)}",
        "mappingVersion": SEVERITY_MAPPING_VERSION,
        "reason": reason,
    }


def nws_severity(properties: Dict[str, Any]) -> tuple[str, Dict[str, str]]:
    raw = str(properties.get("severity") or "Unknown").strip()
    urgency = str(properties.get("urgency") or "Unknown").strip()
    certainty = str(properties.get("certainty") or "Unknown").strip()
    if raw.lower() == "extreme":
        severity = "critical"
    elif raw.lower() in {"severe", "moderate"}:
        severity = "warning"
    else:
        severity = "watch"
    reason = f"NWS CAP severity={raw}, urgency={urgency}, certainty={certainty}"
    return severity, {
        "provider": "NWS",
        "rawLevel": raw,
        "mappingVersion": SEVERITY_MAPPING_VERSION,
        "reason": reason,
    }


def eonet_severity(
    hazard_kind: str,
    magnitude_value: Any,
    magnitude_unit: Any,
) -> tuple[str, Dict[str, str]]:
    magnitude = finite_number(magnitude_value)
    unit = str(magnitude_unit or "").strip()
    severity = "watch"
    reason = "EONET is a discovery source; default severity is watch."
    if hazard_kind == "tropical-cyclone" and magnitude is not None:
        if unit.lower() in {"kph", "km/h"}:
            knots = magnitude / 1.852
        elif unit.lower() in {"mph"}:
            knots = magnitude / 1.15078
        else:
            knots = magnitude
        if knots >= 100:
            severity = "critical"
        elif knots >= 64:
            severity = "warning"
        reason = f"EONET storm magnitude={magnitude:g} {unit or 'source-unit'}."
    elif hazard_kind == "wildfire" and magnitude is not None and "acre" in unit.lower():
        if magnitude >= 100_000:
            severity = "critical"
        elif magnitude >= 10_000:
            severity = "warning"
        reason = f"EONET named-fire area={magnitude:g} {unit}."
    return severity, {
        "provider": "NASA EONET",
        "rawLevel": f"{magnitude:g} {unit}".strip() if magnitude is not None else "discovery",
        "mappingVersion": SEVERITY_MAPPING_VERSION,
        "reason": reason,
    }
