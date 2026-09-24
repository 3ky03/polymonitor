from __future__ import annotations

import io
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional
from xml.etree import ElementTree as ET

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
    resolve_service_value,
)

GEO_SHOCK_SNAPSHOT_NAMESPACE = "snapshot:world:geo-sanctions-shock"
GEO_SHOCK_CACHE_KEY = "panel-v1"
ACLED_AUTH_NAMESPACE = "auth:world:geo-shock:acled"
ACLED_AUTH_CACHE_KEY = "oauth-v1"
ACLED_AUTH_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_FEDERAL_REGISTER_TERMS = (
    "OFAC sanctions action",
    "Iran sanctions",
    "Russia sanctions",
    "China sanctions",
    "nuclear emergency",
    "export controls China",
)
DEFAULT_ACLED_COUNTRY_FILTER = (
    "Iran:OR:country=Russia:OR:country=Ukraine:OR:country=China:OR:country=Taiwan:"
    "OR:country=Israel:OR:country=Palestine:OR:country=Lebanon:OR:country=North Korea"
)
DEFAULT_GDELT_CONFLICT_QUERY = (
    "(missile OR drone OR airstrike OR sanctions OR ceasefire OR military OR nuclear) "
    "(Iran OR Russia OR Ukraine OR China OR Taiwan OR Israel OR Gaza)"
)
DEFAULT_ITEM_LIMIT = 2000
UCDP_PAGE_SIZE = 1000
UCDP_MAX_PAGES = 6
UCDP_MAX_EVENTS = 2000
UCDP_TRAILING_WINDOW_DAYS = 365
TARGET_ALIASES: Dict[str, tuple[str, ...]] = {
    "IRAN": ("iran", "iranian", "tehran", "persian gulf"),
    "RUSSIA": ("russia", "russian", "moscow", "crimea", "kremlin"),
    "CHINA": ("china", "chinese", "beijing", "prc", "xinjiang", "hong kong"),
    "NORTH KOREA": ("north korea", "dprk", "pyongyang"),
    "ISRAEL / GAZA": ("israel", "israeli", "gaza", "hamas", "hezbollah", "lebanon"),
    "UKRAINE": ("ukraine", "ukrainian", "kyiv", "kiev", "donetsk", "luhansk"),
    "TAIWAN": ("taiwan", "taipei", "taiwan strait"),
}
SHOCK_KEYWORDS = (
    "sanction",
    "war",
    "ceasefire",
    "military",
    "missile",
    "drone",
    "strike",
    "nuclear",
    "uranium",
    "tariff",
    "export control",
    "oil",
    "shipping",
    "embargo",
)
MILITARY_KEYWORDS = ("military", "missile", "drone", "strike", "naval", "troop", "defense", "rocket")
NUCLEAR_KEYWORDS = ("nuclear", "uranium", "reactor", "atomic", "radiological")
SEVERITY_ORDER = {"critical": 3, "warning": 2, "watch": 1, "muted": 0}
DEFAULT_SOURCE_STATES = {
    "ofacSdn": "seed-missing",
    "ofacConsolidated": "seed-missing",
    "federalRegister": "seed-missing",
    "conflictFeed": "seed-missing",
}


@dataclass(frozen=True)
class GeoSanctionsShockDependencies:
    settings: Any
    application: Any
    utc_now_iso: Callable[..., Any]
    requests_lib: Any
    http_json_get: Callable[..., Any] | None
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    snapshot_store: Any
    get_acled_auth_state: Callable[..., Any] | None
    store_acled_auth_state: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> GeoSanctionsShockDependencies:
        return cls(
            settings=resolve_service_value(context, "SETTINGS"),
            application=resolve_service_value(context, "app"),
            utc_now_iso=resolve_service_callable(
                context,
                "utc_now_iso",
            ),
            requests_lib=resolve_optional_service_value(
                context,
                "requests",
            ),
            http_json_get=resolve_optional_service_callable(
                context,
                "http_json_get",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
            get_acled_auth_state=resolve_optional_service_callable(
                context,
                "get_acled_auth_state",
            ),
            store_acled_auth_state=resolve_optional_service_callable(
                context,
                "store_acled_auth_state",
            ),
        )


GeoSanctionsShockContext = (
    Mapping[str, Any] | GeoSanctionsShockDependencies
)


def _dependencies(
    context: GeoSanctionsShockContext,
) -> GeoSanctionsShockDependencies:
    if isinstance(context, GeoSanctionsShockDependencies):
        return context
    return GeoSanctionsShockDependencies.from_context(context)


def _local_name(tag: str) -> str:
    return str(tag).split("}", 1)[-1]


def _text_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _unique(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = _text_or_none(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_or_none(value: Any) -> Optional[str]:
    parsed = _parse_datetime(value)
    if parsed is None:
        return _text_or_none(value)
    return parsed.isoformat().replace("+00:00", "Z")


def _exception_http_status(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    try:
        if status_code is not None:
            return int(status_code)
    except (TypeError, ValueError):
        pass
    text = str(exc).lower()
    for candidate in (429, 403, 401, 500, 502, 503, 504):
        if str(candidate) in text or f"http {candidate}" in text or f"status {candidate}" in text:
            return candidate
    return None


def _target_hits(*parts: Any) -> List[str]:
    haystack = " ".join(str(part or "") for part in parts).lower()
    hits: List[str] = []
    for label, aliases in TARGET_ALIASES.items():
        if any(alias in haystack for alias in aliases):
            hits.append(label)
    return hits


def _has_keyword(*parts: Any, keywords: Iterable[str]) -> bool:
    haystack = " ".join(str(part or "") for part in parts).lower()
    return any(keyword in haystack for keyword in keywords)


def _empty_payload(ctx: GeoSanctionsShockContext, *, status: str = "degraded", source_states: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    states = dict(DEFAULT_SOURCE_STATES)
    states.update(source_states or {})
    return {
        "generatedAt": _dependencies(ctx).utc_now_iso(),
        "source": "OFAC / Federal Register / Conflict feed",
        "sourceUrl": _dependencies(ctx).settings.geo_shock_source_url,
        "status": status,
        "sources": states,
        "conflictProvider": None,
        "conflictState": states.get("conflictFeed"),
        "summary": {
            "hotspotCount": 0,
            "newSanctionsCount": 0,
            "targetLabels": [],
            "targetSummary": "MONITORING",
            "nuclearRisk": "guarded",
            "militaryFeed": "standby",
        },
        "items": [],
        "targetBreakdown": [],
        "sanctionsTargetBreakdown": [],
        "countryRiskBreakdown": [],
        "linkedMarkets": [],
        "ofacRecordCountTotal": 0,
    }


def _seed_cache_ttl_seconds(ctx: GeoSanctionsShockContext) -> int:
    return max(300, int(_dependencies(ctx).settings.geo_shock_ttl_seconds or 900))


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _copy_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=True, default=str))


def _seeded_payload_from_cache(ctx: GeoSanctionsShockContext) -> Optional[Dict[str, Any]]:
    getter = _dependencies(ctx).get_cached_json
    if callable(getter):
        payload = getter(GEO_SHOCK_SNAPSHOT_NAMESPACE, GEO_SHOCK_CACHE_KEY)
        if isinstance(payload, dict):
            snapshot_store = _dependencies(ctx).snapshot_store
            if snapshot_store is not None:
                snapshot_store.set(
                    GEO_SHOCK_SNAPSHOT_NAMESPACE,
                    GEO_SHOCK_CACHE_KEY,
                    payload,
                    _seed_cache_ttl_seconds(ctx),
                )
            return payload

    snapshot_store = _dependencies(ctx).snapshot_store
    if snapshot_store is None:
        return None
    payload = snapshot_store.get(GEO_SHOCK_SNAPSHOT_NAMESPACE, GEO_SHOCK_CACHE_KEY)
    if isinstance(payload, dict):
        setter = _dependencies(ctx).set_cached_json
        if callable(setter):
            setter(GEO_SHOCK_SNAPSHOT_NAMESPACE, GEO_SHOCK_CACHE_KEY, payload, _seed_cache_ttl_seconds(ctx))
        return payload
    return None


def _stale_seeded_payload(ctx: GeoSanctionsShockContext) -> Optional[Dict[str, Any]]:
    snapshot_store = _dependencies(ctx).snapshot_store
    if snapshot_store is None:
        return None
    payload = snapshot_store.get_stale(GEO_SHOCK_SNAPSHOT_NAMESPACE, GEO_SHOCK_CACHE_KEY)
    return payload if isinstance(payload, dict) else None


def _seeded_fallback_payload(ctx: GeoSanctionsShockContext) -> Dict[str, Any]:
    stale = _stale_seeded_payload(ctx)
    if isinstance(stale, dict):
        return stale
    payload = _empty_payload(
        ctx,
        status="degraded",
        source_states={key: "warming" for key in DEFAULT_SOURCE_STATES},
    )
    payload["cacheMode"] = "warming"
    return payload


def _previous_seed_payload(ctx: GeoSanctionsShockContext) -> Dict[str, Any]:
    payload = _seeded_payload_from_cache(ctx)
    if isinstance(payload, dict):
        return payload
    payload = _stale_seeded_payload(ctx)
    if isinstance(payload, dict):
        return payload
    return {}


def _with_limit(payload: Dict[str, Any], limit: int) -> Dict[str, Any]:
    normalized_limit = max(1, min(int(limit or DEFAULT_ITEM_LIMIT), DEFAULT_ITEM_LIMIT))
    result = _copy_payload(payload)
    items = result.get("items")
    if isinstance(items, list):
        result["items"] = items[:normalized_limit]
    else:
        result["items"] = []
    result["linkedMarkets"] = []
    return result


def payload_has_material_signal(payload: Dict[str, Any]) -> bool:
    items = payload.get("items")
    if isinstance(items, list) and items:
        return True
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if int(summary.get("hotspotCount") or 0) > 0:
        return True
    if int(summary.get("newSanctionsCount") or 0) > 0:
        return True
    if summary.get("targetLabels"):
        return True
    if str(summary.get("targetSummary") or "").strip().upper() not in {"", "MONITORING"}:
        return True
    if str(summary.get("nuclearRisk") or "").strip().lower() not in {"", "guarded"}:
        return True
    if str(summary.get("militaryFeed") or "").strip().lower() not in {"", "standby"}:
        return True
    return False


def payload_has_source_success(payload: Dict[str, Any]) -> bool:
    sources = payload.get("sources")
    if not isinstance(sources, dict):
        return False
    return any(str(state or "").strip().lower() == "ok" for state in sources.values())


def _ofac_headers() -> Dict[str, str]:
    return {
        "User-Agent": "polydata-runtime/1.0",
        "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
    }


def _iter_texts(parent: ET.Element, path: tuple[str, ...]) -> List[str]:
    nodes = [parent]
    for name in path:
        next_nodes: List[ET.Element] = []
        for node in nodes:
            for child in list(node):
                if _local_name(child.tag) == name:
                    next_nodes.append(child)
        nodes = next_nodes
        if not nodes:
            return []
    return [text for text in (_text_or_none(node.text) for node in nodes) if text]


def _parse_ofac_xml(xml_bytes: bytes, *, list_name: str) -> Dict[str, Any]:
    publish_date = None
    record_count = 0
    focus_entries: List[Dict[str, Any]] = []
    target_scores: Dict[str, int] = defaultdict(int)

    for _, elem in ET.iterparse(io.BytesIO(xml_bytes), events=("end",)):
        tag = _local_name(elem.tag)
        if tag == "publshInformation":
            publish_date = _text_or_none(next((child.text for child in list(elem) if _local_name(child.tag) == "Publish_Date"), None))
            raw_count = _text_or_none(next((child.text for child in list(elem) if _local_name(child.tag) == "Record_Count"), None))
            try:
                record_count = int(raw_count or 0)
            except (TypeError, ValueError):
                record_count = 0
            elem.clear()
            continue
        if tag != "sdnEntry":
            continue

        uid = _text_or_none(next((child.text for child in list(elem) if _local_name(child.tag) == "uid"), None))
        first_name = _text_or_none(next((child.text for child in list(elem) if _local_name(child.tag) == "firstName"), None))
        last_name = _text_or_none(next((child.text for child in list(elem) if _local_name(child.tag) == "lastName"), None))
        entity_type = _text_or_none(next((child.text for child in list(elem) if _local_name(child.tag) == "sdnType"), None)) or "Entity"
        programs = _unique(_iter_texts(elem, ("programList", "program")))
        countries = _unique(
            [
                *_iter_texts(elem, ("addressList", "address", "country")),
                *_iter_texts(elem, ("nationalityList", "nationality", "country")),
                *_iter_texts(elem, ("citizenshipList", "citizenship", "country")),
            ]
        )
        name = " ".join(part for part in (first_name, last_name) if part) or last_name or first_name or f"{list_name} #{uid or 'unknown'}"
        targets = _target_hits(name, " ".join(programs), " ".join(countries))
        for target in targets:
            target_scores[target] += 3 if list_name == "OFAC SDN" else 2
        if targets:
            focus_entries.append(
                {
                    "id": f"ofac:{uid or name}",
                    "kind": "sanction",
                    "headline": name,
                    "summary": " / ".join(_unique([entity_type, *programs[:2], *countries[:1]])) or entity_type,
                    "source": list_name,
                    "sourceUrl": None,
                    "occurredAt": _iso_or_none(publish_date),
                    "severity": "critical" if any(target in {"IRAN", "RUSSIA", "CHINA", "NORTH KOREA"} for target in targets) else "warning",
                    "targetLabels": targets,
                }
            )
        elem.clear()

    return {
        "publishDate": _iso_or_none(publish_date),
        "recordCount": record_count,
        "focusEntries": focus_entries[:10],
        "targetScores": dict(target_scores),
    }


def _fetch_ofac_snapshot(ctx: GeoSanctionsShockContext) -> Dict[str, Any]:
    requests_lib = _dependencies(ctx).requests_lib
    settings = _dependencies(ctx).settings
    sources = {
        "ofacSdn": settings.geo_shock_ofac_sdn_url,
        "ofacConsolidated": settings.geo_shock_ofac_consolidated_url,
    }
    source_states: Dict[str, str] = {}
    combined_entries: List[Dict[str, Any]] = []
    target_scores: Dict[str, int] = defaultdict(int)
    record_count_total = 0
    publish_dates: List[str] = []
    if requests_lib is None:
        return {
            "states": {name: "requests-missing" for name in sources},
            "recordCountTotal": 0,
            "focusEntries": [],
            "targetScores": {},
            "publishDates": [],
        }
    for name, url in sources.items():
        if not url:
            source_states[name] = "missing-url"
            continue
        try:
            response = requests_lib.get(url, timeout=20, headers=_ofac_headers())
            response.raise_for_status()
            parsed = _parse_ofac_xml(response.content, list_name="OFAC SDN" if name == "ofacSdn" else "OFAC Consolidated")
            source_states[name] = "ok"
            combined_entries.extend(parsed["focusEntries"])
            record_count_total += int(parsed["recordCount"] or 0)
            if parsed.get("publishDate"):
                publish_dates.append(parsed["publishDate"])
            for target, score in (parsed.get("targetScores") or {}).items():
                target_scores[target] += int(score or 0)
        except Exception:
            _dependencies(ctx).application.logger.exception("geo shock ofac fetch failed source=%s", name)
            source_states[name] = "error"
    return {
        "states": source_states,
        "recordCountTotal": record_count_total,
        "focusEntries": combined_entries[:12],
        "targetScores": dict(target_scores),
        "publishDates": publish_dates,
    }


def _normalize_notice(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    title = _text_or_none(doc.get("title"))
    if not title:
        return None
    summary = _text_or_none(doc.get("abstract")) or _text_or_none(doc.get("excerpts")) or ""
    targets = _target_hits(title, summary)
    if not targets and not _has_keyword(title, summary, keywords=SHOCK_KEYWORDS):
        return None
    doc_type = _text_or_none(doc.get("type")) or "Notice"
    severity = "critical" if _has_keyword(title, summary, keywords=NUCLEAR_KEYWORDS) else ("warning" if "executive order" in title.lower() or "ofac" in summary.lower() else "watch")
    return {
        "id": f"fr:{doc.get('document_number') or title}",
        "kind": "notice",
        "headline": title,
        "summary": " / ".join(_unique([doc_type, *targets[:2]])),
        "source": "Federal Register",
        "sourceUrl": doc.get("html_url"),
        "occurredAt": _iso_or_none(doc.get("publication_date")),
        "severity": severity,
        "targetLabels": targets,
    }


def _fetch_federal_register_snapshot(ctx: GeoSanctionsShockContext) -> Dict[str, Any]:
    url = _dependencies(ctx).settings.geo_shock_federal_register_api_url
    if not url:
        return {"state": "missing-url", "items": [], "targetScores": {}}
    http_json_get = _dependencies(ctx).http_json_get
    if not callable(http_json_get):
        return {"state": "requests-missing", "items": [], "targetScores": {}}

    seen: set[str] = set()
    items: List[Dict[str, Any]] = []
    target_scores: Dict[str, int] = defaultdict(int)
    any_ok = False
    for term in DEFAULT_FEDERAL_REGISTER_TERMS:
        try:
            payload = http_json_get(
                url,
                params={
                    "per_page": 6,
                    "order": "newest",
                    "conditions[term]": term,
                },
                timeout=15,
                headers={"Accept": "application/json", "User-Agent": "polydata-runtime/1.0"},
            )
            any_ok = True
        except Exception:
            _dependencies(ctx).application.logger.exception("geo shock federal register fetch failed term=%s", term)
            continue
        for raw in (payload or {}).get("results") or []:
            key = str(raw.get("document_number") or raw.get("html_url") or raw.get("title") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            item = _normalize_notice(raw)
            if item is None:
                continue
            items.append(item)
            for target in item.get("targetLabels") or []:
                target_scores[target] += 2
    if items:
        items.sort(key=lambda item: (_parse_datetime(item.get("occurredAt")) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    state = "ok" if any_ok else "error"
    return {"state": state, "items": items[:10], "targetScores": dict(target_scores)}


def _coerce_conflict_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("items", "results", "events", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _normalize_conflict_item(raw: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    headline = _text_or_none(raw.get("headline")) or _text_or_none(raw.get("title")) or _text_or_none(raw.get("event_type")) or _text_or_none(raw.get("eventType"))
    country = _text_or_none(raw.get("country")) or _text_or_none(raw.get("region")) or _text_or_none(raw.get("location"))
    tags = raw.get("tags") or raw.get("layers") or raw.get("topics") or []
    tag_values = [str(value).strip() for value in tags] if isinstance(tags, list) else [part.strip() for part in str(tags).split(",") if part.strip()]
    if not headline and not country:
        return None
    occurred_at = _iso_or_none(
        raw.get("event_date")
        or raw.get("eventDate")
        or raw.get("published_at")
        or raw.get("publishedAt")
        or raw.get("timestamp")
        or raw.get("updated_at")
        or raw.get("updatedAt")
    )
    fatalities = raw.get("fatalities") or raw.get("fatality_count")
    try:
        fatality_count = int(fatalities or 0)
    except (TypeError, ValueError):
        fatality_count = 0
    text_blob = " ".join(part for part in (headline, country, " ".join(tag_values)) if part)
    if fatality_count >= 20 or _has_keyword(text_blob, keywords=NUCLEAR_KEYWORDS):
        severity = "critical"
    elif fatality_count > 0 or _has_keyword(text_blob, keywords=MILITARY_KEYWORDS):
        severity = "warning"
    else:
        severity = "watch"
    return {
        "id": f"conflict:{raw.get('id') or raw.get('event_id') or index}",
        "kind": "conflict",
        "headline": headline or country or f"Hotspot {index + 1}",
        "summary": " / ".join(_unique([country or "Unknown", *tag_values[:2]])),
        "source": _text_or_none(raw.get("source")) or "Conflict feed",
        "sourceUrl": raw.get("url"),
        "occurredAt": occurred_at,
        "severity": severity,
        "targetLabels": _target_hits(text_blob),
        "country": country,
        "tags": tag_values,
    }


def _normalize_gdelt_article(raw: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    title = _text_or_none(raw.get("title"))
    if not title:
        return None
    summary = _text_or_none(raw.get("snippet")) or _text_or_none(raw.get("domain")) or ""
    targets = _target_hits(title, summary)
    if not targets and not _has_keyword(title, summary, keywords=SHOCK_KEYWORDS):
        return None
    occurred_at = _iso_or_none(raw.get("seendate"))
    text_blob = " ".join(part for part in (title, summary, " ".join(targets)) if part)
    if _has_keyword(text_blob, keywords=NUCLEAR_KEYWORDS):
        severity = "critical"
    elif _has_keyword(text_blob, keywords=MILITARY_KEYWORDS):
        severity = "warning"
    else:
        severity = "watch"
    country = targets[0] if targets else None
    return {
        "id": f"gdelt:{raw.get('url') or index}",
        "kind": "conflict",
        "headline": title,
        "summary": " / ".join(_unique([country or "Monitoring", _text_or_none(raw.get("domain")) or "GDELT"])) or "Monitoring / GDELT",
        "source": "GDELT DOC 2.0",
        "sourceUrl": raw.get("url"),
        "occurredAt": occurred_at,
        "severity": severity,
        "targetLabels": targets,
        "country": country,
        "tags": ["gdelt", "news"] + (["military"] if _has_keyword(text_blob, keywords=MILITARY_KEYWORDS) else []),
    }


def _fetch_gdelt_conflict_snapshot(ctx: GeoSanctionsShockContext) -> Dict[str, Any]:
    http_json_get = _dependencies(ctx).http_json_get
    url = _dependencies(ctx).settings.geo_shock_gdelt_doc_api_url
    if not callable(http_json_get) or not url:
        return {"state": "missing-url", "provider": "GDELT", "items": [], "targetScores": {}, "hotspotCount": 0}
    try:
        payload = http_json_get(
            url,
            params={
                "query": DEFAULT_GDELT_CONFLICT_QUERY,
                "mode": "ArtList",
                "format": "json",
                "maxrecords": 12,
                "timespan": "3days",
            },
            timeout=20,
            headers={"Accept": "application/json", "User-Agent": "polydata-runtime/1.0"},
        )
    except Exception as exc:
        status_code = _exception_http_status(exc)
        if status_code == 429:
            _dependencies(ctx).application.logger.warning("geo shock gdelt fallback rate limited")
            return {"state": "rate-limited", "provider": "GDELT", "items": [], "targetScores": {}, "hotspotCount": 0}
        _dependencies(ctx).application.logger.exception("geo shock gdelt fallback fetch failed")
        return {"state": "error", "provider": "GDELT", "items": [], "targetScores": {}, "hotspotCount": 0}

    items: List[Dict[str, Any]] = []
    target_scores: Dict[str, int] = defaultdict(int)
    for index, row in enumerate((payload or {}).get("articles") or []):
        if not isinstance(row, dict):
            continue
        item = _normalize_gdelt_article(row, index)
        if item is None:
            continue
        items.append(item)
        for target in item.get("targetLabels") or []:
            target_scores[target] += 1
    hotspot_count = len(_unique([item.get("country") or item.get("headline") or "" for item in items]))
    items.sort(
        key=lambda item: (
            _parse_datetime(item.get("occurredAt")) or datetime.min.replace(tzinfo=timezone.utc),
            SEVERITY_ORDER.get(str(item.get("severity")), 0),
        ),
        reverse=True,
    )
    return {
        "state": "ok" if items else "empty",
        "provider": "GDELT",
        "items": items[:UCDP_MAX_EVENTS],
        "targetScores": dict(target_scores),
        "hotspotCount": hotspot_count,
    }


def _normalize_ucdp_item(raw: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    conflict_name = _text_or_none(raw.get("conflict_name"))
    dyad_name = _text_or_none(raw.get("dyad_name"))
    country = _text_or_none(raw.get("country"))
    region = _text_or_none(raw.get("region"))
    location = (
        _text_or_none(raw.get("where_coordinates"))
        or _text_or_none(raw.get("where_description"))
        or _text_or_none(raw.get("location"))
        or _text_or_none(raw.get("adm_1"))
    )
    side_a = _text_or_none(raw.get("side_a"))
    side_b = _text_or_none(raw.get("side_b"))
    latitude = _float_or_none(raw.get("latitude") or raw.get("lat"))
    longitude = _float_or_none(raw.get("longitude") or raw.get("lon") or raw.get("lng"))
    headline = conflict_name or dyad_name or " vs ".join(part for part in (side_a, side_b) if part) or "UCDP conflict event"

    try:
        best = int(raw.get("best") or 0)
    except (TypeError, ValueError):
        best = 0
    try:
        low = int(raw.get("low") or 0)
    except (TypeError, ValueError):
        low = 0
    try:
        high = int(raw.get("high") or 0)
    except (TypeError, ValueError):
        high = 0

    text_blob = " ".join(part for part in (headline, dyad_name, country, region, location, side_a, side_b) if part)
    if best >= 20 or _has_keyword(text_blob, keywords=NUCLEAR_KEYWORDS):
        severity = "critical"
    elif best > 0 or _has_keyword(text_blob, keywords=MILITARY_KEYWORDS):
        severity = "warning"
    else:
        severity = "watch"

    tags = _unique(
        [
            _text_or_none(raw.get("type_of_violence")) or "",
            _text_or_none(raw.get("active_year")) or "",
            region or "",
        ]
    )
    summary_parts = _unique(
        [
            country or "",
            location or "",
            dyad_name or "",
            f"{best} fatalities" if best > 0 else "",
        ]
    )
    return {
        "id": f"ucdp:{raw.get('id') or raw.get('relid') or index}",
        "kind": "conflict",
        "headline": headline,
        "summary": " / ".join(summary_parts[:3]) or "UCDP conflict event",
        "source": "UCDP",
        "sourceUrl": None,
        "occurredAt": _iso_or_none(raw.get("date_end") or raw.get("date_start")),
        "severity": severity,
        "targetLabels": _target_hits(text_blob),
        "country": country or location or region,
        "tags": tags,
        "sideA": side_a,
        "sideB": side_b,
        "locationLabel": location,
        "latitude": latitude,
        "longitude": longitude,
        "violenceType": _text_or_none(raw.get("type_of_violence")),
        "deathsBest": best,
        "deathsLow": low,
        "deathsHigh": high,
}


def _ucdp_version_candidates(configured_url: str) -> List[str]:
    year = datetime.now(timezone.utc).year - 2000
    candidates = [f"{year}.1", f"{year - 1}.1", "25.1", "24.1"]
    configured = str(configured_url or "").rstrip("/").rsplit("/", 1)[-1]
    if configured and "." in configured:
        candidates.append(configured)
    return _unique(candidates)


def _ucdp_url_for_version(configured_url: str, version: str) -> str:
    base = str(configured_url or "https://ucdpapi.pcr.uu.se/api/gedevents/25.1").strip().rstrip("/")
    tail = base.rsplit("/", 1)[-1]
    if "." in tail:
        return f"{base.rsplit('/', 1)[0]}/{version}"
    return f"{base}/{version}"


def _fetch_ucdp_page(ctx: GeoSanctionsShockContext, *, api_url: str, token: str, version: str, page: int) -> Dict[str, Any]:
    requests_lib = _dependencies(ctx).requests_lib
    headers = {
        "Accept": "application/json",
        "User-Agent": "polydata-runtime/1.0",
        "x-ucdp-access-token": token,
    }
    response = requests_lib.get(
        _ucdp_url_for_version(api_url, version),
        params={"pagesize": UCDP_PAGE_SIZE, "page": page},
        timeout=30,
        headers=headers,
    )
    status_code = int(getattr(response, "status_code", 500) or 500)
    if status_code == 429:
        raise RuntimeError("ucdp-rate-limited")
    if status_code in {401, 403}:
        raise RuntimeError(f"ucdp-access-denied:{status_code}")
    response.raise_for_status()
    payload = response.json() if hasattr(response, "json") else {}
    if not isinstance(payload, dict):
        raise RuntimeError("ucdp-invalid-payload")
    return payload


def _discover_ucdp_version(ctx: GeoSanctionsShockContext, *, api_url: str, token: str) -> tuple[str, Dict[str, Any]]:
    errors: List[str] = []
    for version in _ucdp_version_candidates(api_url):
        try:
            payload = _fetch_ucdp_page(ctx, api_url=api_url, token=token, version=version, page=0)
        except RuntimeError as exc:
            message = str(exc)
            if message.startswith("ucdp-access-denied") or message == "ucdp-rate-limited":
                raise
            errors.append(f"{version}:{message}")
            continue
        except Exception as exc:
            errors.append(f"{version}:{type(exc).__name__}")
            continue
        if isinstance(payload.get("Result"), list):
            return version, payload
        errors.append(f"{version}:no-result")
    raise RuntimeError("ucdp-version-discovery-failed:" + ";".join(errors[:4]))


def _latest_ucdp_event_datetime(rows: Iterable[Dict[str, Any]]) -> Optional[datetime]:
    latest: Optional[datetime] = None
    for row in rows:
        parsed = _parse_datetime(row.get("date_start") or row.get("date_end"))
        if parsed is None:
            continue
        if latest is None or parsed > latest:
            latest = parsed
    return latest


def _fetch_ucdp_conflict_snapshot(ctx: GeoSanctionsShockContext) -> Dict[str, Any]:
    settings = _dependencies(ctx).settings
    requests_lib = _dependencies(ctx).requests_lib
    api_url = str(getattr(settings, "geo_shock_ucdp_api_url", "") or "").strip()
    token = str(getattr(settings, "geo_shock_ucdp_access_token", "") or "").strip()
    if not api_url:
        return {"state": "missing-url", "provider": "UCDP", "items": [], "targetScores": {}, "hotspotCount": 0}
    if not token:
        return {"state": "auth-missing", "provider": "UCDP", "items": [], "targetScores": {}, "hotspotCount": 0}
    if requests_lib is None:
        return {"state": "requests-missing", "provider": "UCDP", "items": [], "targetScores": {}, "hotspotCount": 0}

    try:
        version, page0 = _discover_ucdp_version(ctx, api_url=api_url, token=token)
        total_pages = max(1, int(page0.get("TotalPages") or 1))
        newest_page = total_pages - 1
        page_payloads: List[Dict[str, Any]] = []
        failed_pages = 0
        for offset in range(UCDP_MAX_PAGES):
            page = newest_page - offset
            if page < 0:
                break
            try:
                page_payloads.append(page0 if page == 0 else _fetch_ucdp_page(ctx, api_url=api_url, token=token, version=version, page=page))
            except Exception as exc:
                failed_pages += 1
                _dependencies(ctx).application.logger.warning("geo shock ucdp page fetch failed version=%s page=%s error=%s", version, page, exc)
    except Exception as exc:
        text = str(exc)
        if text == "ucdp-rate-limited":
            _dependencies(ctx).application.logger.warning("geo shock ucdp rate limited")
            return {"state": "rate-limited", "provider": "UCDP", "items": [], "targetScores": {}, "hotspotCount": 0}
        if text.startswith("ucdp-access-denied"):
            _dependencies(ctx).application.logger.warning("geo shock ucdp access denied error=%s", text)
            return {"state": "access-denied", "provider": "UCDP", "items": [], "targetScores": {}, "hotspotCount": 0}
        status_code = _exception_http_status(exc)
        if status_code == 429:
            return {"state": "rate-limited", "provider": "UCDP", "items": [], "targetScores": {}, "hotspotCount": 0}
        _dependencies(ctx).application.logger.exception("geo shock ucdp fetch failed")
        return {"state": "error", "provider": "UCDP", "items": [], "targetScores": {}, "hotspotCount": 0}

    rows: List[Dict[str, Any]] = []
    for payload in page_payloads:
        rows.extend(_coerce_conflict_rows((payload or {}).get("Result") if isinstance(payload, dict) else payload))
    latest = _latest_ucdp_event_datetime(rows)
    if latest is not None:
        floor = latest - timedelta(days=UCDP_TRAILING_WINDOW_DAYS)
        rows = [
            row for row in rows
            if (_parse_datetime(row.get("date_start") or row.get("date_end")) or datetime.min.replace(tzinfo=timezone.utc)) >= floor
        ]
    items: List[Dict[str, Any]] = []
    target_scores: Dict[str, int] = defaultdict(int)
    for index, row in enumerate(rows):
        item = _normalize_ucdp_item(row, index)
        if item is None:
            continue
        items.append(item)
        for target in item.get("targetLabels") or []:
            target_scores[target] += 2
    hotspot_count = len(_unique([item.get("country") or item.get("headline") or "" for item in items]))
    items.sort(
        key=lambda item: (
            _parse_datetime(item.get("occurredAt")) or datetime.min.replace(tzinfo=timezone.utc),
            SEVERITY_ORDER.get(str(item.get("severity")), 0),
        ),
        reverse=True,
    )
    return {
        "state": "ok" if items else "empty",
        "provider": "UCDP",
        "items": items[:UCDP_MAX_EVENTS],
        "targetScores": dict(target_scores),
        "hotspotCount": hotspot_count,
        "version": version,
        "rawCount": len(rows),
        "failedPages": failed_pages,
    }


def _has_acled_credentials(settings: Any) -> bool:
    return bool(str(getattr(settings, "geo_shock_acled_email", "") or "").strip()) and bool(
        str(getattr(settings, "geo_shock_acled_password", "") or "").strip()
    )


def _normalize_acled_auth_state(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    access_token = _text_or_none(payload.get("access_token"))
    refresh_token = _text_or_none(payload.get("refresh_token"))
    try:
        access_expires_at = int(payload.get("access_expires_at") or 0)
    except (TypeError, ValueError):
        access_expires_at = 0
    if not access_token and not refresh_token:
        return None
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_expires_at": access_expires_at,
    }


def _get_acled_auth_state(ctx: GeoSanctionsShockContext) -> Optional[Dict[str, Any]]:
    getter = _dependencies(ctx).get_acled_auth_state
    if not callable(getter):
        return None
    return _normalize_acled_auth_state(getter())


def _store_acled_auth_state(ctx: GeoSanctionsShockContext, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_acled_auth_state(payload) or {"access_token": None, "refresh_token": None, "access_expires_at": 0}
    setter = _dependencies(ctx).store_acled_auth_state
    if callable(setter):
        setter(normalized)
    return normalized


def _build_acled_auth_state(token_payload: Dict[str, Any], *, fallback_refresh_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    access_token = _text_or_none(token_payload.get("access_token"))
    if not access_token:
        return None
    refresh_token = _text_or_none(token_payload.get("refresh_token")) or fallback_refresh_token
    try:
        expires_in = int(token_payload.get("expires_in") or 86400)
    except (TypeError, ValueError):
        expires_in = 86400
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "access_expires_at": _now_epoch() + max(60, expires_in) - 300,
    }


def _acled_login_with_password(ctx: GeoSanctionsShockContext) -> Optional[Dict[str, Any]]:
    settings = _dependencies(ctx).settings
    requests_lib = _dependencies(ctx).requests_lib
    token_url = str(getattr(settings, "geo_shock_acled_token_url", "") or "").strip()
    if requests_lib is None or not token_url or not _has_acled_credentials(settings):
        return None

    try:
        response = requests_lib.post(
            token_url,
            data={
                "username": settings.geo_shock_acled_email,
                "password": settings.geo_shock_acled_password,
                "grant_type": "password",
                "client_id": "acled",
                "scope": "authenticated",
            },
            timeout=20,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "polydata-runtime/1.0",
            },
        )
        response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else {}
        state = _build_acled_auth_state(payload or {})
        if state is None:
            return None
        return _store_acled_auth_state(ctx, state)
    except Exception:
        _dependencies(ctx).application.logger.exception("geo shock acled token fetch failed")
        return None


def _acled_refresh_access_token(ctx: GeoSanctionsShockContext, current_state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    settings = _dependencies(ctx).settings
    requests_lib = _dependencies(ctx).requests_lib
    token_url = str(getattr(settings, "geo_shock_acled_token_url", "") or "").strip()
    refresh_token = _text_or_none((current_state or {}).get("refresh_token"))
    if requests_lib is None or not token_url or not refresh_token:
        return _acled_login_with_password(ctx)

    try:
        response = requests_lib.post(
            token_url,
            data={
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "client_id": "acled",
            },
            timeout=20,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "polydata-runtime/1.0",
            },
        )
        if int(getattr(response, "status_code", 500) or 500) != 200:
            _dependencies(ctx).application.logger.warning("geo shock acled refresh failed status=%s falling back to password login", getattr(response, "status_code", None))
            return _acled_login_with_password(ctx)
        payload = response.json() if hasattr(response, "json") else {}
        state = _build_acled_auth_state(payload or {}, fallback_refresh_token=refresh_token)
        if state is None:
            return _acled_login_with_password(ctx)
        return _store_acled_auth_state(ctx, state)
    except Exception:
        _dependencies(ctx).application.logger.exception("geo shock acled refresh failed")
        return _acled_login_with_password(ctx)


def _fetch_acled_access_token(ctx: GeoSanctionsShockContext) -> Optional[str]:
    current_state = _get_acled_auth_state(ctx)
    access_token = _text_or_none((current_state or {}).get("access_token"))
    if not access_token:
        refreshed = _acled_login_with_password(ctx)
        return _text_or_none((refreshed or {}).get("access_token"))

    try:
        access_expires_at = int((current_state or {}).get("access_expires_at") or 0)
    except (TypeError, ValueError):
        access_expires_at = 0
    if _now_epoch() >= access_expires_at:
        refreshed = _acled_refresh_access_token(ctx, current_state)
        return _text_or_none((refreshed or {}).get("access_token"))
    return access_token


def _normalize_acled_item(raw: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    headline = _text_or_none(raw.get("notes"))
    event_type = _text_or_none(raw.get("event_type"))
    sub_event_type = _text_or_none(raw.get("sub_event_type"))
    actor1 = _text_or_none(raw.get("actor1"))
    actor2 = _text_or_none(raw.get("actor2"))
    country = _text_or_none(raw.get("country"))
    admin1 = _text_or_none(raw.get("admin1"))
    location = _text_or_none(raw.get("location"))
    if not headline:
        detail_parts = [part for part in (event_type, sub_event_type, actor1, actor2, country) if part]
        headline = " / ".join(detail_parts[:3])
    if not headline:
        return None

    try:
        fatality_count = int(raw.get("fatalities") or 0)
    except (TypeError, ValueError):
        fatality_count = 0

    tag_values = _unique([event_type or "", sub_event_type or ""])
    text_blob = " ".join(part for part in (headline, country, admin1, location, event_type, sub_event_type, actor1, actor2) if part)
    if fatality_count >= 20 or _has_keyword(text_blob, keywords=NUCLEAR_KEYWORDS):
        severity = "critical"
    elif fatality_count > 0 or _has_keyword(text_blob, keywords=MILITARY_KEYWORDS):
        severity = "warning"
    else:
        severity = "watch"

    targets = _target_hits(text_blob)
    occurred_at = _iso_or_none(raw.get("event_date"))
    summary_parts = _unique(
        [
            country or "",
            admin1 or "",
            sub_event_type or event_type or "",
            f"{fatality_count} fatalities" if fatality_count > 0 else "",
        ]
    )
    return {
        "id": f"acled:{raw.get('event_id_cnty') or index}",
        "kind": "conflict",
        "headline": headline,
        "summary": " / ".join(summary_parts[:3]) or "ACLED event",
        "source": "ACLED",
        "sourceUrl": None,
        "occurredAt": occurred_at,
        "severity": severity,
        "targetLabels": targets,
        "country": country or location or admin1,
        "tags": tag_values,
    }


def _fetch_acled_conflict_snapshot(ctx: GeoSanctionsShockContext) -> Dict[str, Any]:
    settings = _dependencies(ctx).settings
    requests_lib = _dependencies(ctx).requests_lib
    api_url = str(getattr(settings, "geo_shock_acled_api_url", "") or "").strip()
    if not api_url:
        return {"state": "missing-url", "items": [], "targetScores": {}, "hotspotCount": 0}
    if not _has_acled_credentials(settings):
        return {"state": "auth-missing", "items": [], "targetScores": {}, "hotspotCount": 0}
    if requests_lib is None:
        return {"state": "requests-missing", "items": [], "targetScores": {}, "hotspotCount": 0}

    token = _fetch_acled_access_token(ctx)
    if not token:
        return {"state": "auth-error", "items": [], "targetScores": {}, "hotspotCount": 0}

    date_floor = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    params = {
        "_format": "json",
        "limit": 40,
        "country": DEFAULT_ACLED_COUNTRY_FILTER,
        "event_date": date_floor,
        "event_date_where": ">=",
        "fields": "event_id_cnty|event_date|event_type|sub_event_type|country|admin1|location|actor1|actor2|fatalities|notes",
    }
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "polydata-runtime/1.0",
    }
    try:
        response = requests_lib.get(
            api_url,
            params=params,
            timeout=25,
            headers=headers,
        )
        if int(getattr(response, "status_code", 500) or 500) == 401:
            refreshed = _acled_refresh_access_token(ctx, _get_acled_auth_state(ctx))
            refreshed_token = _text_or_none((refreshed or {}).get("access_token"))
            if refreshed_token:
                response = requests_lib.get(
                    api_url,
                    params=params,
                    timeout=25,
                    headers={**headers, "Authorization": f"Bearer {refreshed_token}"},
                )
        if int(getattr(response, "status_code", 500) or 500) == 403:
            _dependencies(ctx).application.logger.warning("geo shock acled access denied")
            return {"state": "access-denied", "items": [], "targetScores": {}, "hotspotCount": 0}
        response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else {}
    except Exception:
        _dependencies(ctx).application.logger.exception("geo shock acled read failed")
        return {"state": "error", "items": [], "targetScores": {}, "hotspotCount": 0}

    rows = _coerce_conflict_rows(payload if isinstance(payload, list) else ((payload or {}).get("data") or payload))
    items: List[Dict[str, Any]] = []
    target_scores: Dict[str, int] = defaultdict(int)
    for index, row in enumerate(rows):
        item = _normalize_acled_item(row, index)
        if item is None:
            continue
        items.append(item)
        for target in item.get("targetLabels") or []:
            target_scores[target] += 2
    hotspot_count = len(_unique([item.get("country") or item.get("headline") or "" for item in items]))
    items.sort(
        key=lambda item: (
            _parse_datetime(item.get("occurredAt")) or datetime.min.replace(tzinfo=timezone.utc),
            SEVERITY_ORDER.get(str(item.get("severity")), 0),
        ),
        reverse=True,
    )
    return {
        "state": "ok" if items else "empty",
        "provider": "ACLED",
        "items": items[:12],
        "targetScores": dict(target_scores),
        "hotspotCount": hotspot_count,
    }


def _previous_conflict_snapshot(previous: Optional[Dict[str, Any]], *, provider: str = "cached") -> Dict[str, Any]:
    if not isinstance(previous, dict):
        return {"state": "error", "provider": provider, "items": [], "targetScores": {}, "hotspotCount": 0}
    previous_items = previous.get("items") if isinstance(previous.get("items"), list) else []
    items = [item for item in previous_items if isinstance(item, dict) and str(item.get("kind") or "").lower() == "conflict"]
    if not items:
        return {"state": "error", "provider": provider, "items": [], "targetScores": {}, "hotspotCount": 0}
    target_scores: Dict[str, int] = defaultdict(int)
    for item in items:
        for target in item.get("targetLabels") or []:
            target_scores[str(target)] += 1
    hotspot_count = len(_unique([item.get("country") or item.get("headline") or "" for item in items]))
    return {
        "state": "stale",
        "provider": provider,
        "items": items[:12],
        "targetScores": dict(target_scores),
        "hotspotCount": hotspot_count,
    }


def _successful_conflict_snapshot(snapshot: Dict[str, Any]) -> bool:
    return str(snapshot.get("state") or "").lower() in {"ok", "empty"}


def _conflict_snapshot_has_items(snapshot: Dict[str, Any]) -> bool:
    return str(snapshot.get("state") or "").lower() == "ok" and bool(snapshot.get("items"))


def _has_ucdp_token(settings: Any) -> bool:
    return bool(str(getattr(settings, "geo_shock_ucdp_access_token", "") or "").strip())


def _fetch_conflict_snapshot(ctx: GeoSanctionsShockContext, *, previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = _dependencies(ctx).settings.geo_shock_conflict_api_url
    if not url:
        ucdp_snapshot: Dict[str, Any] = {"state": "auth-missing", "provider": "UCDP", "items": [], "targetScores": {}, "hotspotCount": 0}
        if _has_ucdp_token(_dependencies(ctx).settings):
            ucdp_snapshot = _fetch_ucdp_conflict_snapshot(ctx)
            if _conflict_snapshot_has_items(ucdp_snapshot):
                return ucdp_snapshot
        if _has_acled_credentials(_dependencies(ctx).settings):
            acled_snapshot = _fetch_acled_conflict_snapshot(ctx)
            if _conflict_snapshot_has_items(acled_snapshot):
                return acled_snapshot
            if _successful_conflict_snapshot(acled_snapshot) and not _successful_conflict_snapshot(ucdp_snapshot):
                return acled_snapshot
        if _successful_conflict_snapshot(ucdp_snapshot):
            return ucdp_snapshot
        gdelt_snapshot = _fetch_gdelt_conflict_snapshot(ctx)
        if _successful_conflict_snapshot(gdelt_snapshot):
            return gdelt_snapshot
        stale_snapshot = _previous_conflict_snapshot(previous, provider=str(gdelt_snapshot.get("provider") or ucdp_snapshot.get("provider") or "cached"))
        if stale_snapshot.get("state") == "stale":
            return stale_snapshot
        return gdelt_snapshot if gdelt_snapshot.get("state") != "missing-url" else ucdp_snapshot
    http_json_get = _dependencies(ctx).http_json_get
    if not callable(http_json_get):
        return {"state": "requests-missing", "provider": "custom", "items": [], "targetScores": {}, "hotspotCount": 0}
    try:
        payload = http_json_get(
            url,
            timeout=15,
            headers={"Accept": "application/json", "User-Agent": "polydata-runtime/1.0"},
        )
    except Exception as exc:
        status_code = _exception_http_status(exc)
        if status_code == 429:
            return {"state": "rate-limited", "provider": "custom", "items": [], "targetScores": {}, "hotspotCount": 0}
        _dependencies(ctx).application.logger.exception("geo shock conflict fetch failed")
        return {"state": "error", "provider": "custom", "items": [], "targetScores": {}, "hotspotCount": 0}

    items: List[Dict[str, Any]] = []
    target_scores: Dict[str, int] = defaultdict(int)
    for index, row in enumerate(_coerce_conflict_rows(payload)):
        item = _normalize_conflict_item(row, index)
        if item is None:
            continue
        items.append(item)
        for target in item.get("targetLabels") or []:
            target_scores[target] += 2
    hotspot_count = len(_unique([item.get("country") or item.get("headline") or "" for item in items]))
    items.sort(
        key=lambda item: (
            _parse_datetime(item.get("occurredAt")) or datetime.min.replace(tzinfo=timezone.utc),
            SEVERITY_ORDER.get(str(item.get("severity")), 0),
        ),
        reverse=True,
    )
    return {
        "state": "ok",
        "provider": "custom",
        "items": items[:12],
        "targetScores": dict(target_scores),
        "hotspotCount": hotspot_count,
    }


def _merge_target_scores(*score_maps: Dict[str, int]) -> Dict[str, int]:
    combined: Dict[str, int] = defaultdict(int)
    for score_map in score_maps:
        for target, score in (score_map or {}).items():
            combined[target] += int(score or 0)
    return dict(combined)


def _top_targets(target_scores: Dict[str, int]) -> List[str]:
    ranked = sorted(target_scores.items(), key=lambda item: (-item[1], item[0]))
    return [label for label, score in ranked if score > 0][:3]


def _build_target_breakdown(items: List[Dict[str, Any]], target_scores: Dict[str, int]) -> List[Dict[str, Any]]:
    if not target_scores:
        return []

    latest_by_target: Dict[str, Dict[str, Any]] = {}
    for item in items:
        occurred = _parse_datetime(item.get("occurredAt")) or datetime.min.replace(tzinfo=timezone.utc)
        for label in item.get("targetLabels") or []:
            current = latest_by_target.get(label)
            current_occurred = _parse_datetime((current or {}).get("occurredAt")) or datetime.min.replace(tzinfo=timezone.utc)
            if current is None or occurred >= current_occurred:
                latest_by_target[label] = item

    ranked = sorted(target_scores.items(), key=lambda entry: (-int(entry[1] or 0), entry[0]))
    breakdown: List[Dict[str, Any]] = []
    for label, count in ranked[:3]:
        latest = latest_by_target.get(label) or {}
        breakdown.append(
            {
                "label": label,
                "count": int(count or 0),
                "latestHeadline": _text_or_none(latest.get("headline")),
                "latestOccurredAt": _iso_or_none(latest.get("occurredAt")),
                "latestSource": _text_or_none(latest.get("source")),
            }
        )
    return breakdown


def _sort_shock_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            _parse_datetime(item.get("occurredAt")) or datetime.min.replace(tzinfo=timezone.utc),
            SEVERITY_ORDER.get(str(item.get("severity")), 0),
        ),
        reverse=True,
    )


def _select_geo_shock_items(
    all_items: List[Dict[str, Any]],
    conflict_items: List[Dict[str, Any]],
    *,
    item_limit: int,
) -> List[Dict[str, Any]]:
    limit = max(3, min(int(item_limit or DEFAULT_ITEM_LIMIT), DEFAULT_ITEM_LIMIT))
    selected: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: Dict[str, Any]) -> None:
        key = str(item.get("id") or item.get("headline") or "")
        if not key or key in seen or len(selected) >= limit:
            return
        seen.add(key)
        selected.append(item)

    for item in _sort_shock_items(conflict_items):
        add(item)
    for item in _sort_shock_items(all_items):
        add(item)
    return selected


def _nuclear_risk(items: List[Dict[str, Any]], targets: List[str]) -> str:
    nuclear_items = [
        item for item in items
        if _has_keyword(item.get("headline"), item.get("summary"), " ".join(item.get("tags") or []), keywords=NUCLEAR_KEYWORDS)
    ]
    if len(nuclear_items) >= 2:
        return "critical"
    if nuclear_items or any(target in {"IRAN", "NORTH KOREA"} for target in targets):
        return "elevated"
    return "guarded"


def _military_feed_label(conflict_state: str, conflict_items: List[Dict[str, Any]]) -> str:
    if conflict_state == "ok" and conflict_items:
        return "active"
    if conflict_state == "ok":
        return "quiet"
    if conflict_state == "stale" and conflict_items:
        return "cached"
    if conflict_state == "missing-url":
        return "limited"
    if conflict_state == "rate-limited":
        return "limited"
    return "degraded"


def _payload_status(source_states: Dict[str, str], items: List[Dict[str, Any]]) -> str:
    states = list(source_states.values())
    if not states:
        return "degraded" if not items else "ok"
    if items and all(state == "ok" for state in states if state not in {"missing-url"}):
        return "ok" if all(state == "ok" for state in states) else "degraded"
    if items:
        return "degraded"
    if any(state == "ok" for state in states):
        return "empty"
    return "degraded"


def build_geo_sanctions_shock_seed_payload(
    ctx: GeoSanctionsShockContext,
    *,
    previous: Optional[Dict[str, Any]] = None,
    item_limit: int = DEFAULT_ITEM_LIMIT,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    previous_payload = previous or {}
    payload = _empty_payload(dependencies, status="degraded")
    ofac_snapshot = _fetch_ofac_snapshot(dependencies)
    notices_snapshot = _fetch_federal_register_snapshot(dependencies)
    conflict_snapshot = _fetch_conflict_snapshot(
        dependencies,
        previous=previous_payload,
    )

    source_states = {
        **(ofac_snapshot.get("states") or {}),
        "federalRegister": notices_snapshot.get("state") or "error",
        "conflictFeed": conflict_snapshot.get("state") or "error",
    }

    sanctions_items = [
        *(ofac_snapshot.get("focusEntries") or []),
        *(notices_snapshot.get("items") or []),
    ]
    conflict_items = [
        *(conflict_snapshot.get("items") or []),
    ]
    all_items = [*sanctions_items, *conflict_items]
    items = _select_geo_shock_items(
        all_items,
        [item for item in conflict_items if isinstance(item, dict)],
        item_limit=item_limit,
    )

    sanctions_target_scores = _merge_target_scores(
        ofac_snapshot.get("targetScores") or {},
        notices_snapshot.get("targetScores") or {},
    )
    country_risk_scores = _merge_target_scores(
        conflict_snapshot.get("targetScores") or {},
    )
    target_scores = _merge_target_scores(sanctions_target_scores, country_risk_scores)
    targets = _top_targets(target_scores)
    record_total = int(ofac_snapshot.get("recordCountTotal") or 0)
    previous_record_total = int(previous_payload.get("ofacRecordCountTotal") or 0)
    recent_notice_count = len(notices_snapshot.get("items") or [])
    new_sanctions_count = max(0, record_total - previous_record_total) if previous_record_total else recent_notice_count

    payload.update(
        {
            "generatedAt": dependencies.utc_now_iso(),
            "sourceUrl": dependencies.settings.geo_shock_source_url,
            "status": _payload_status(source_states, items),
            "sources": source_states,
            "conflictProvider": conflict_snapshot.get("provider"),
            "conflictState": conflict_snapshot.get("state"),
            "summary": {
                "hotspotCount": int(conflict_snapshot.get("hotspotCount") or 0),
                "newSanctionsCount": int(new_sanctions_count),
                "targetLabels": targets,
                "targetSummary": " / ".join(targets) if targets else "MONITORING",
                "nuclearRisk": _nuclear_risk(items, targets),
                "militaryFeed": _military_feed_label(str(conflict_snapshot.get("state") or ""), conflict_snapshot.get("items") or []),
            },
            "items": items,
            "targetBreakdown": _build_target_breakdown(all_items, target_scores),
            "sanctionsTargetBreakdown": _build_target_breakdown(
                sanctions_items,
                sanctions_target_scores,
            ),
            "countryRiskBreakdown": _build_target_breakdown(
                conflict_items,
                country_risk_scores,
            ),
            "linkedMarkets": [],
            "ofacRecordCountTotal": record_total,
            "publishDates": ofac_snapshot.get("publishDates") or [],
            "cacheMode": "seeded",
        }
    )
    return payload


def get_geo_sanctions_shock_snapshot(ctx: GeoSanctionsShockContext, limit: int = DEFAULT_ITEM_LIMIT) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    payload = _seeded_payload_from_cache(dependencies)
    if payload is None:
        payload = _seeded_fallback_payload(dependencies)
    return _with_limit(payload, limit)
