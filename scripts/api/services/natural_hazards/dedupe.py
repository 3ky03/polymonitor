from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping


_USGS_EVENT_RE = re.compile(r"earthquake\.usgs\.gov/earthquakes/eventpage/([A-Za-z0-9_-]+)", re.I)


def canonical_event_identity(event: Mapping[str, Any]) -> tuple[str, str]:
    """Return only an explicit, auditable cross-provider identity.

    Spatial proximity is intentionally absent: a provider-declared canonical
    ID or an official USGS event-page identifier is required before records
    from different sources may be fused.
    """
    properties = event.get("properties") if isinstance(event.get("properties"), Mapping) else {}
    declared = str(properties.get("canonicalEventId") or "").strip()
    if declared:
        return declared, str(properties.get("mergeReason") or "provider-declared canonical identifier")
    if str(event.get("hazardKind") or "") == "earthquake":
        for source in event.get("sources") or []:
            if not isinstance(source, Mapping):
                continue
            match = _USGS_EVENT_RE.search(str(source.get("url") or ""))
            if match:
                return f"earthquake:usgs:{match.group(1).lower()}", "explicit USGS event-page identifier"
    return str(event.get("id") or ""), "source-native event identifier"


def _primary_rank(event: Mapping[str, Any]) -> tuple[int, str]:
    providers = " ".join(
        str(source.get("provider") or "").lower()
        for source in event.get("sources") or []
        if isinstance(source, Mapping)
    )
    canonical_priority = 3 if event.get("hazardKind") == "earthquake" and "usgs" in providers else 1
    updated = str(event.get("updatedAt") or event.get("occurredAt") or "")
    return canonical_priority, updated


def _merge_exact_canonical(events: list[Dict[str, Any]], canonical_id: str, reason: str) -> Dict[str, Any]:
    if len(events) == 1 and str(events[0].get("id") or "") == canonical_id:
        return events[0]
    primary = deepcopy(max(events, key=_primary_rank))
    sources: list[Dict[str, Any]] = []
    seen_sources: set[tuple[str, str]] = set()
    provenance: list[Dict[str, str]] = []
    for event in events:
        revision = event.get("revision") if isinstance(event.get("revision"), Mapping) else {}
        for source in event.get("sources") or []:
            if not isinstance(source, Mapping):
                continue
            key = (str(source.get("provider") or ""), str(source.get("nativeId") or ""))
            if key not in seen_sources:
                seen_sources.add(key)
                sources.append(deepcopy(dict(source)))
            provenance.append({
                "provider": key[0] or "unknown",
                "nativeEventId": str(revision.get("nativeEventId") or key[1]),
                "revisionAt": str(revision.get("revisionAt") or event.get("updatedAt") or ""),
            })
    properties = deepcopy(primary.get("properties") if isinstance(primary.get("properties"), Mapping) else {})
    properties.update({
        "canonicalEventId": canonical_id,
        "mergeReason": reason,
        "sourceProvenance": provenance,
    })
    primary["id"] = canonical_id
    primary["sources"] = sources
    primary["properties"] = properties
    primary["limitations"] = list(dict.fromkeys(
        str(value)
        for event in events
        for value in event.get("limitations") or []
        if str(value).strip()
    ))
    return primary


def latest_revision(events: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    by_id: dict[str, list[Dict[str, Any]]] = {}
    reasons: dict[str, str] = {}
    for event in events:
        event_id, reason = canonical_event_identity(event)
        if not event_id:
            continue
        by_id.setdefault(event_id, []).append(event)
        current_reason = reasons.get(event_id, "")
        if not current_reason or "explicit" in reason.lower() or "declared" in reason.lower():
            reasons[event_id] = reason
    return [
        _merge_exact_canonical(group, event_id, reasons[event_id])
        for event_id, group in by_id.items()
    ]
