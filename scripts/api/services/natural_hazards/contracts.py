from __future__ import annotations

from typing import Any, Dict, List, TypedDict


SCHEMA_VERSION = "natural-hazards.v1"
SEVERITY_MAPPING_VERSION = "hazard-severity.v1"


class ProviderResult(TypedDict):
    events: List[Dict[str, Any]]
    data_updated_at: str | None


class SourceFetchResult(TypedDict):
    key: str
    status: str
    coverage: Dict[str, Any]
    events: List[Dict[str, Any]]
    fetchedAt: str | None
    dataUpdatedAt: str | None
    staleAfter: str | None
    lastSuccessAt: str | None
    errorCode: str | None


def coverage(
    *,
    scope: str,
    label: str,
    complete: bool,
    gaps: list[str] | None = None,
) -> Dict[str, Any]:
    return {
        "scope": scope,
        "label": label,
        "isComplete": complete,
        "gaps": list(gaps or []),
    }
