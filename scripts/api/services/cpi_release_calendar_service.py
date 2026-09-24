from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
    resolve_service_value,
)


CPI_CALENDAR_SNAPSHOT_NAMESPACE = "snapshot:macro:cpi-release-calendar"
CPI_CALENDAR_CACHE_KEY = "panel-v1"
DEFAULT_ITEM_LIMIT = 8
EASTERN_TZ = ZoneInfo("America/New_York")

MONTHS: Dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

BLS_2026_FALLBACK_ROWS: Dict[str, tuple[tuple[str, str, str], ...]] = {
    "cpi": (
        ("November 2025", "Dec. 18, 2025", "08:30 AM"),
        ("December 2025", "Jan. 13, 2026", "08:30 AM"),
        ("January 2026", "Feb. 13, 2026", "08:30 AM"),
        ("February 2026", "Mar. 11, 2026", "08:30 AM"),
        ("March 2026", "Apr. 10, 2026", "08:30 AM"),
        ("April 2026", "May 12, 2026", "08:30 AM"),
        ("May 2026", "Jun. 10, 2026", "08:30 AM"),
        ("June 2026", "Jul. 14, 2026", "08:30 AM"),
        ("July 2026", "Aug. 12, 2026", "08:30 AM"),
        ("August 2026", "Sep. 11, 2026", "08:30 AM"),
        ("September 2026", "Oct. 14, 2026", "08:30 AM"),
        ("October 2026", "Nov. 10, 2026", "08:30 AM"),
        ("November 2026", "Dec. 10, 2026", "08:30 AM"),
    ),
    "nfp": (
        ("November 2025", "Dec. 16, 2025", "08:30 AM"),
        ("December 2025", "Jan. 09, 2026", "08:30 AM"),
        ("January 2026", "Feb. 11, 2026", "08:30 AM"),
        ("February 2026", "Mar. 06, 2026", "08:30 AM"),
        ("March 2026", "Apr. 03, 2026", "08:30 AM"),
        ("April 2026", "May 08, 2026", "08:30 AM"),
        ("May 2026", "Jun. 05, 2026", "08:30 AM"),
        ("June 2026", "Jul. 02, 2026", "08:30 AM"),
        ("July 2026", "Aug. 07, 2026", "08:30 AM"),
        ("August 2026", "Sep. 04, 2026", "08:30 AM"),
        ("September 2026", "Oct. 02, 2026", "08:30 AM"),
        ("October 2026", "Nov. 06, 2026", "08:30 AM"),
        ("November 2026", "Dec. 04, 2026", "08:30 AM"),
    ),
}


@dataclass(frozen=True)
class CpiReleaseCalendarDependencies:
    settings: Any
    application: Any
    http_text_get: Callable[..., str]
    beautiful_soup: Any
    get_polymarket_macro_map_snapshot: Callable[..., Any] | None
    utc_now_iso: Callable[..., Any] | None
    snapshot_store: Any
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> CpiReleaseCalendarDependencies:
        return cls(
            settings=resolve_service_value(context, "SETTINGS"),
            application=resolve_optional_service_value(context, "app"),
            http_text_get=resolve_service_callable(
                context,
                "http_text_get",
            ),
            beautiful_soup=resolve_optional_service_value(
                context,
                "BeautifulSoup",
            ),
            get_polymarket_macro_map_snapshot=resolve_optional_service_callable(
                context,
                "get_polymarket_macro_map_snapshot",
            ),
            utc_now_iso=resolve_optional_service_callable(
                context,
                "utc_now_iso",
            ),
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
        )


CpiReleaseCalendarContext = (
    Mapping[str, Any] | CpiReleaseCalendarDependencies
)


def _dependencies(
    context: CpiReleaseCalendarContext,
) -> CpiReleaseCalendarDependencies:
    if isinstance(context, CpiReleaseCalendarDependencies):
        return context
    return CpiReleaseCalendarDependencies.from_context(context)


def _utc_now_iso(
    dependencies: CpiReleaseCalendarDependencies,
) -> str:
    if dependencies.utc_now_iso is not None:
        return str(dependencies.utc_now_iso())
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_ts(value: Any) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _html_to_lines(
    dependencies: CpiReleaseCalendarDependencies,
    html: str,
) -> List[str]:
    soup_factory = dependencies.beautiful_soup
    if soup_factory is not None:
        soup = soup_factory(html, "html.parser")
        text = soup.get_text("\n", strip=True)
    else:
        text = re.sub(r"<[^>]+>", "\n", html)
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _lines_to_text(lines: Iterable[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _month_number(raw: str) -> int:
    key = str(raw or "").strip().strip(".").lower()
    return MONTHS.get(key, 0)


def _parse_et_datetime(month_token: str, day: str, year: str, time_text: str) -> Optional[str]:
    month = _month_number(month_token)
    if not month:
        return None
    time_match = re.match(r"^(\d{1,2}):(\d{2})\s*([AP]M)$", str(time_text or "").strip(), re.I)
    if not time_match:
        return None
    hour = int(time_match.group(1))
    minute = int(time_match.group(2))
    if time_match.group(3).upper() == "PM" and hour != 12:
        hour += 12
    if time_match.group(3).upper() == "AM" and hour == 12:
        hour = 0
    local_dt = datetime(int(year), month, int(day), hour, minute, tzinfo=EASTERN_TZ)
    return local_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_release_date_text(raw: str) -> Optional[tuple[str, str, str]]:
    match = re.match(
        r"^(Jan\.?|Feb\.?|Mar\.?|Apr\.?|May|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)\s+(\d{1,2}),\s+(\d{4})$",
        str(raw or "").strip(),
    )
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def _fallback_bls_schedule(*, kind: str, title: str, source: str, url: str) -> List[Dict[str, Any]]:
    rows = BLS_2026_FALLBACK_ROWS.get(kind, ())
    items: List[Dict[str, Any]] = []
    for reference_month, release_date, release_time in rows:
        date_parts = _parse_release_date_text(release_date)
        if not date_parts:
            continue
        release_at = _parse_et_datetime(date_parts[0], date_parts[1], date_parts[2], release_time)
        if not release_at:
            continue
        items.append(
            {
                "id": f"{kind}-{reference_month.lower().replace(' ', '-')}",
                "kind": kind,
                "title": title,
                "referencePeriod": reference_month,
                "releaseAt": release_at,
                "releaseTimeEt": release_time,
                "source": f"{source} fallback",
                "sourceUrl": url,
                "marketRelevance": "CPI bucket" if kind == "cpi" else "Fed / labor / unemployment",
            }
        )
    return items


def _parse_bls_schedule(
    ctx: CpiReleaseCalendarContext,
    *,
    url: str,
    kind: str,
    title: str,
    source: str,
) -> tuple[List[Dict[str, Any]], str]:
    dependencies = _dependencies(ctx)
    try:
        html = dependencies.http_text_get(
            url,
            timeout=12,
            headers={
                "Accept": "text/html",
                "User-Agent": "polydata-cpi-calendar/1.0",
            },
        )
    except Exception:
        fallback = _fallback_bls_schedule(kind=kind, title=title, source=source, url=url)
        if fallback:
            return fallback, "fallback"
        raise
    if "Access Denied" in str(html or ""):
        return _fallback_bls_schedule(kind=kind, title=title, source=source, url=url), "fallback"
    lines = _html_to_lines(dependencies, str(html or ""))
    text = _lines_to_text(lines)
    pattern = re.compile(
        r"([A-Z][a-z]+)\s+(\d{4})\s+"
        r"(Jan\.?|Feb\.?|Mar\.?|Apr\.?|May|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)\s+"
        r"(\d{1,2}),\s+(\d{4})\s+(\d{1,2}:\d{2}\s+[AP]M)"
    )
    items: List[Dict[str, Any]] = []
    for match in pattern.finditer(text):
        reference_month = f"{match.group(1)} {match.group(2)}"
        release_at = _parse_et_datetime(match.group(3), match.group(4), match.group(5), match.group(6))
        if not release_at:
            continue
        items.append(
            {
                "id": f"{kind}-{reference_month.lower().replace(' ', '-')}",
                "kind": kind,
                "title": title,
                "referencePeriod": reference_month,
                "releaseAt": release_at,
                "releaseTimeEt": match.group(6).upper(),
                "source": source,
                "sourceUrl": url,
                "marketRelevance": "CPI bucket" if kind == "cpi" else "Fed / labor / unemployment",
            }
        )
    if items:
        return items, "ok"
    fallback = _fallback_bls_schedule(kind=kind, title=title, source=source, url=url)
    return fallback, "fallback" if fallback else "empty"


def _parse_date_without_year(raw: str, base_year: int) -> Optional[tuple[int, int, int]]:
    match = re.match(r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})$", raw)
    if not match:
        return None
    return base_year, _month_number(match.group(1)), int(match.group(2))


def _parse_bea_schedule(
    ctx: CpiReleaseCalendarContext,
    *,
    url: str,
) -> tuple[List[Dict[str, Any]], str]:
    dependencies = _dependencies(ctx)
    html = dependencies.http_text_get(
        url,
        timeout=12,
        headers={
            "Accept": "text/html",
            "User-Agent": "polydata-cpi-calendar/1.0",
        },
    )
    lines = _html_to_lines(dependencies, str(html or ""))
    now = datetime.fromtimestamp(
        _parse_iso_ts(_utc_now_iso(dependencies))
        or datetime.now(timezone.utc).timestamp(),
        timezone.utc,
    )
    items: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines):
        date_parts = _parse_date_without_year(line, now.year)
        if not date_parts:
            continue
        time_text = lines[idx + 1] if idx + 1 < len(lines) else ""
        if not re.match(r"^\d{1,2}:\d{2}\s+[AP]M$", time_text, re.I):
            continue
        title = ""
        for candidate in lines[idx + 2 : idx + 9]:
            if "Personal Income and Outlays" in candidate:
                title = candidate
                break
        if not title:
            continue
        year, month, day = date_parts
        release_at = _parse_et_datetime(datetime(year, month, day).strftime("%B"), str(day), str(year), time_text)
        reference = title.split(",", 1)[1].strip() if "," in title else None
        items.append(
            {
                "id": f"pce-{year}-{month:02d}-{day:02d}",
                "kind": "pce",
                "title": "Personal Income and Outlays / PCE",
                "referencePeriod": reference,
                "releaseAt": release_at,
                "releaseTimeEt": time_text.upper(),
                "source": "BEA Release Schedule",
                "sourceUrl": url,
                "marketRelevance": "PCE / Core PCE / Fed",
            }
        )
    return items, "ok" if items else "empty"


def _parse_fomc_schedule(
    ctx: CpiReleaseCalendarContext,
    *,
    url: str,
) -> tuple[List[Dict[str, Any]], str]:
    dependencies = _dependencies(ctx)
    html = dependencies.http_text_get(
        url,
        timeout=12,
        headers={
            "Accept": "text/html",
            "User-Agent": "polydata-cpi-calendar/1.0",
        },
    )
    lines = _html_to_lines(dependencies, str(html or ""))
    items: List[Dict[str, Any]] = []
    now_year = datetime.fromtimestamp(
        _parse_iso_ts(_utc_now_iso(dependencies))
        or datetime.now(timezone.utc).timestamp(),
        timezone.utc,
    ).year
    current_year: Optional[int] = None
    current_month: Optional[str] = None
    for line in lines:
        year_match = re.match(r"^(\d{4}) FOMC Meetings$", line)
        if year_match:
            current_year = int(year_match.group(1))
            current_month = None
            continue
        if current_year is None:
            continue
        if re.match(r"^\d{4} FOMC Meetings$", line):
            break
        if _month_number(line):
            current_month = line
            continue
        date_match = re.match(r"^(\d{1,2})(?:-(\d{1,2}))?\*?$", line)
        if not date_match or not current_month or current_year < now_year or current_year > now_year + 1:
            continue
        end_day = int(date_match.group(2) or date_match.group(1))
        start_day = int(date_match.group(1))
        month = _month_number(current_month)
        release_at = datetime(current_year, month, end_day, 14, 0, tzinfo=EASTERN_TZ).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        range_label = f"{current_month} {start_day}-{end_day}" if start_day != end_day else f"{current_month} {end_day}"
        items.append(
            {
                "id": f"fomc-{current_year}-{month:02d}-{start_day:02d}",
                "kind": "fomc",
                "title": "FOMC decision",
                "referencePeriod": f"{range_label}, {current_year}",
                "releaseAt": release_at,
                "releaseTimeEt": "02:00 PM",
                "source": "Federal Reserve FOMC Calendar",
                "sourceUrl": url,
                "marketRelevance": "Fed decision / rates",
            }
        )
    return items, "ok" if items else "empty"


def _baseline_from_macro_map(
    dependencies: CpiReleaseCalendarDependencies,
) -> Dict[str, Any]:
    if dependencies.get_polymarket_macro_map_snapshot is None:
        return {"status": "unavailable", "label": "No Polymarket map snapshot"}
    try:
        payload = dependencies.get_polymarket_macro_map_snapshot(limit=20)
    except Exception:
        return {"status": "error", "label": "Polymarket map unavailable"}
    candidates: List[Dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or "cpi" not in (item.get("categoryIds") or []):
            continue
        for outcome in item.get("topOutcomes") or []:
            if isinstance(outcome, dict) and outcome.get("yesPrice") is not None:
                candidates.append({"item": item, "outcome": outcome})
    if not candidates:
        return {"status": "empty", "label": "No active CPI bucket market"}
    winner = max(candidates, key=lambda row: float(row["outcome"].get("yesPrice") or 0))
    outcome = winner["outcome"]
    item = winner["item"]
    return {
        "status": "market-implied",
        "label": outcome.get("label") or "Top CPI outcome",
        "probability": outcome.get("yesPrice"),
        "marketTitle": item.get("title"),
        "marketSlug": item.get("slug"),
        "source": "Polymarket Macro Market Map",
    }


def _event_risk(next_event: Optional[Dict[str, Any]], now_ts: float) -> Dict[str, Any]:
    if not next_event:
        return {"signal": "CALENDAR WARMING", "risk": "unknown", "hoursToEvent": None}
    event_ts = _parse_iso_ts(next_event.get("releaseAt"))
    hours = (event_ts - now_ts) / 3600 if event_ts and now_ts else None
    if hours is not None and hours <= 48:
        return {"signal": "EVENT RISK HIGH", "risk": "high", "hoursToEvent": round(hours, 1)}
    if hours is not None and hours <= 24 * 7:
        return {"signal": "EVENT RISK BUILDING", "risk": "medium", "hoursToEvent": round(hours, 1)}
    return {"signal": "CALENDAR WATCH", "risk": "low", "hoursToEvent": round(hours, 1) if hours is not None else None}


def _summary(events: List[Dict[str, Any]], baseline: Dict[str, Any], now_ts: float) -> Dict[str, Any]:
    upcoming = [event for event in events if _parse_iso_ts(event.get("releaseAt")) >= now_ts - 3600]
    next_event = upcoming[0] if upcoming else None
    risk = _event_risk(next_event, now_ts)
    return {
        "nextEvent": next_event,
        "nextCpi": next((event for event in upcoming if event.get("kind") == "cpi"), None),
        "nextPce": next((event for event in upcoming if event.get("kind") == "pce"), None),
        "nextNfp": next((event for event in upcoming if event.get("kind") == "nfp"), None),
        "nextFomc": next((event for event in upcoming if event.get("kind") == "fomc"), None),
        "signal": risk["signal"],
        "risk": risk["risk"],
        "hoursToEvent": risk["hoursToEvent"],
        "baselineLabel": baseline.get("label"),
        "baselineProbability": baseline.get("probability"),
        "consensusStatus": "optional-unavailable",
    }


def _empty_payload(
    dependencies: CpiReleaseCalendarDependencies,
    *,
    status: str = "warming",
    sources: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    now_iso = _utc_now_iso(dependencies)
    return {
        "generatedAt": now_iso,
        "source": "BLS / BEA / Federal Reserve / Polymarket",
        "sourceUrl": getattr(
            dependencies.settings,
            "cpi_calendar_source_url",
            "",
        ),
        "status": status,
        "sources": sources or {},
        "summary": _summary(
            [],
            {"status": "unavailable", "label": "No baseline"},
            _parse_iso_ts(now_iso),
        ),
        "baseline": {"status": "unavailable", "label": "No Polymarket baseline"},
        "consensus": {"status": "optional-unavailable", "label": "Consensus feed not configured"},
        "items": [],
    }


def build_cpi_release_calendar_payload(
    ctx: CpiReleaseCalendarContext,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    settings = dependencies.settings
    source_states: Dict[str, str] = {}
    events: List[Dict[str, Any]] = []

    source_jobs = (
        ("blsCpi", lambda: _parse_bls_schedule(ctx, url=settings.cpi_calendar_bls_cpi_url, kind="cpi", title="Consumer Price Index", source="BLS CPI Release Calendar")),
        ("blsEmployment", lambda: _parse_bls_schedule(ctx, url=settings.cpi_calendar_bls_employment_url, kind="nfp", title="Employment Situation", source="BLS Employment Situation Calendar")),
        ("beaPce", lambda: _parse_bea_schedule(ctx, url=settings.cpi_calendar_bea_schedule_url)),
        ("fomc", lambda: _parse_fomc_schedule(ctx, url=settings.cpi_calendar_fomc_url)),
    )
    for key, builder in source_jobs:
        try:
            rows, state = builder()
            events.extend(rows)
            source_states[key] = state
        except Exception as exc:
            source_states[key] = "error"
            logger = getattr(dependencies.application, "logger", None)
            if logger is not None:
                logger.exception("cpi release calendar source failed source=%s error=%s", key, exc)

    baseline = _baseline_from_macro_map(dependencies)
    source_states["pmktBaseline"] = str(baseline.get("status") or "unknown")
    now_iso = _utc_now_iso(dependencies)
    now_ts = _parse_iso_ts(now_iso)
    unique_events = {str(event.get("id") or len(events)): event for event in events if event.get("releaseAt")}
    items = sorted(unique_events.values(), key=lambda event: _parse_iso_ts(event.get("releaseAt")))
    status = "ok" if items and any(value == "ok" for value in source_states.values()) else ("degraded" if items else "warming")
    return {
        "generatedAt": now_iso,
        "source": "BLS / BEA / Federal Reserve / Polymarket",
        "sourceUrl": getattr(settings, "cpi_calendar_source_url", ""),
        "status": status,
        "sources": source_states,
        "summary": _summary(items, baseline, now_ts),
        "baseline": baseline,
        "consensus": {"status": "optional-unavailable", "label": "High-quality consensus is usually paid; panel uses PMKT implied baseline."},
        "items": items,
    }


def normalize_cpi_release_calendar_payload(
    payload: Any,
    *,
    ctx: CpiReleaseCalendarContext,
    limit: int = DEFAULT_ITEM_LIMIT,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    if not isinstance(payload, dict):
        return _empty_payload(
            dependencies,
            status="invalid",
            sources={"payload": "invalid"},
        )
    result = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    items = [item for item in (result.get("items") or []) if isinstance(item, dict)]
    now_iso = _utc_now_iso(dependencies)
    now_ts = _parse_iso_ts(now_iso)
    items.sort(key=lambda event: _parse_iso_ts(event.get("releaseAt")))
    visible = [item for item in items if _parse_iso_ts(item.get("releaseAt")) >= now_ts - 3600]
    if not visible:
        visible = items
    max_items = max(1, min(int(limit or DEFAULT_ITEM_LIMIT), 20))
    result["items"] = visible[:max_items]
    result["summary"] = result.get("summary") if isinstance(result.get("summary"), dict) else _summary(items, result.get("baseline") or {}, now_ts)
    result["baseline"] = result.get("baseline") if isinstance(result.get("baseline"), dict) else {"status": "unavailable"}
    result["consensus"] = result.get("consensus") if isinstance(result.get("consensus"), dict) else {"status": "optional-unavailable"}
    result["generatedAt"] = str(result.get("generatedAt") or now_iso)
    result["source"] = str(result.get("source") or "BLS / BEA / Federal Reserve / Polymarket")
    result["sourceUrl"] = str(
        result.get("sourceUrl")
        or getattr(
            dependencies.settings,
            "cpi_calendar_source_url",
            "",
        )
    )
    result["status"] = str(result.get("status") or ("ok" if result["items"] else "warming"))
    return result


def _with_cache_mode(payload: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    return {**payload, "cacheMode": str(cache_mode)}


def _read_seeded_snapshot(
    dependencies: CpiReleaseCalendarDependencies,
    *,
    ttl_seconds: int,
) -> Optional[Dict[str, Any]]:
    if dependencies.get_cached_json is not None:
        redis_payload = dependencies.get_cached_json(
            CPI_CALENDAR_SNAPSHOT_NAMESPACE,
            CPI_CALENDAR_CACHE_KEY,
        )
        if isinstance(redis_payload, dict):
            if dependencies.snapshot_store is not None:
                dependencies.snapshot_store.set(
                    CPI_CALENDAR_SNAPSHOT_NAMESPACE,
                    CPI_CALENDAR_CACHE_KEY,
                    redis_payload,
                    ttl_seconds,
                )
            return _with_cache_mode(redis_payload, "redis-seed")

    snapshot_store = dependencies.snapshot_store
    if snapshot_store is None:
        return None
    sqlite_payload = snapshot_store.get(CPI_CALENDAR_SNAPSHOT_NAMESPACE, CPI_CALENDAR_CACHE_KEY)
    if isinstance(sqlite_payload, dict):
        if dependencies.set_cached_json is not None:
            dependencies.set_cached_json(
                CPI_CALENDAR_SNAPSHOT_NAMESPACE,
                CPI_CALENDAR_CACHE_KEY,
                sqlite_payload,
                ttl_seconds,
            )
        return _with_cache_mode(sqlite_payload, "sqlite-seed")
    stale_payload = snapshot_store.get_stale(CPI_CALENDAR_SNAPSHOT_NAMESPACE, CPI_CALENDAR_CACHE_KEY)
    if isinstance(stale_payload, dict):
        if dependencies.set_cached_json is not None:
            dependencies.set_cached_json(
                CPI_CALENDAR_SNAPSHOT_NAMESPACE,
                CPI_CALENDAR_CACHE_KEY,
                stale_payload,
                min(60, ttl_seconds),
            )
        return _with_cache_mode(stale_payload, "stale-seed")
    return None


def _store_live_build_snapshot(
    dependencies: CpiReleaseCalendarDependencies,
    payload: Dict[str, Any],
    *,
    ttl_seconds: int,
) -> None:
    if dependencies.snapshot_store is not None:
        dependencies.snapshot_store.set(
            CPI_CALENDAR_SNAPSHOT_NAMESPACE,
            CPI_CALENDAR_CACHE_KEY,
            payload,
            ttl_seconds,
        )
    if dependencies.set_cached_json is not None:
        dependencies.set_cached_json(
            CPI_CALENDAR_SNAPSHOT_NAMESPACE,
            CPI_CALENDAR_CACHE_KEY,
            payload,
            ttl_seconds,
        )


def get_cpi_release_calendar_snapshot(
    ctx: CpiReleaseCalendarContext,
    limit: int = DEFAULT_ITEM_LIMIT,
    *,
    allow_live_build: bool = True,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    ttl_seconds = max(
        300,
        int(
            getattr(
                dependencies.settings,
                "cpi_calendar_ttl_seconds",
                3600,
            )
            or 3600
        ),
    )
    seeded_payload = _read_seeded_snapshot(
        dependencies,
        ttl_seconds=ttl_seconds,
    )
    if seeded_payload is not None:
        return normalize_cpi_release_calendar_payload(
            seeded_payload,
            ctx=dependencies,
            limit=limit,
        )
    if not allow_live_build:
        return normalize_cpi_release_calendar_payload(
            {
                "status": "warming",
                "cacheMode": "seed-miss",
                "sources": {},
                "items": [],
            },
            ctx=dependencies,
            limit=limit,
        )
    payload = _with_cache_mode(build_cpi_release_calendar_payload(ctx), "live-build")
    if payload.get("items"):
        _store_live_build_snapshot(
            dependencies,
            payload,
            ttl_seconds=ttl_seconds,
        )
    return normalize_cpi_release_calendar_payload(
        payload,
        ctx=dependencies,
        limit=limit,
    )
