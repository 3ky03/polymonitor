from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from api.services import worldcup_dashboard_service


PANEL_ID = "world-cup-match-ops"
WORLD_CUP_MATCH_OPS_SNAPSHOT_NAMESPACE = "snapshot:sports:world-cup-match-ops"
WORLD_CUP_MATCH_OPS_CACHE_KEY = "panel-v1"
DEFAULT_LIMIT = 12
DEFAULT_TTL_SECONDS = 300

_LIVE_REFRESH_LOCK = threading.Lock()
_LIVE_REFRESHING: set[str] = set()


def _utc_now_iso(ctx: dict | None = None) -> str:
    if ctx:
        getter = ctx.get("utc_now_iso")
        if callable(getter):
            return getter()
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _minutes_until(value: Any, *, now: Optional[datetime] = None) -> Optional[int]:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    anchor = now or datetime.now(timezone.utc)
    return int((parsed - anchor).total_seconds() // 60)


def _status_rank(match: Dict[str, Any], *, now: datetime) -> tuple[int, str]:
    status = str(match.get("status") or "").lower()
    kickoff = _parse_iso(match.get("kickoffUtc"))
    if status in {"in", "live", "in_progress", "halftime"}:
        return (0, str(match.get("kickoffUtc") or ""))
    if kickoff and kickoff >= now:
        return (1, str(match.get("kickoffUtc") or ""))
    return (2, str(match.get("kickoffUtc") or ""))


def _weather_by_city(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    weather = payload.get("weather") if isinstance(payload.get("weather"), list) else []
    by_city: Dict[str, Dict[str, Any]] = {}
    for row in weather:
        if not isinstance(row, dict):
            continue
        city_id = str(row.get("cityId") or row.get("id") or "").strip()
        city = str(row.get("city") or "").strip().lower()
        if city_id:
            by_city[city_id] = row
        if city:
            by_city[city] = row
    return by_city


def _odds_by_match(payload: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    odds = payload.get("odds") if isinstance(payload.get("odds"), list) else []
    by_match: Dict[str, List[Dict[str, Any]]] = {}
    for row in odds:
        if not isinstance(row, dict):
            continue
        match_id = str(row.get("matchId") or "").strip()
        if match_id:
            by_match.setdefault(match_id, []).append(row)
    return by_match


def _weather_risk(weather: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not weather:
        return {"level": "unknown", "score": 0, "label": "WEATHER SOURCE REQUIRED"}
    temp = _float_first(weather, "temperature", "temperatureC", "temp")
    precip = _float_first(weather, "precipitation", "precipitationProbability", "precipitationMm")
    wind = _float_first(weather, "wind", "windSpeed", "windKph")
    score = int(max(0.0, min(100.0, (18 if temp and temp >= 29 else 6) + (precip or 0) * 0.35 + (wind or 0) * 0.7)))
    if score >= 55:
        return {"level": "high", "score": score, "label": "WEATHER WATCH"}
    if score >= 25:
        return {"level": "watch", "score": score, "label": "VENUE CHECK"}
    return {"level": "low", "score": score, "label": "LOW WEATHER RISK"}


def _float_first(row: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        try:
            value = row.get(key)
            if value is not None and value != "":
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _match_item(match: Dict[str, Any], *, weather: Optional[Dict[str, Any]], odds: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    risk = _weather_risk(weather)
    markets = []
    for row in odds[:4]:
        markets.append(
            {
                "marketId": row.get("marketId") or row.get("id"),
                "slug": row.get("marketSlug") or row.get("slug"),
                "question": row.get("marketTitle") or row.get("title"),
                "marketUrl": row.get("marketUrl") or row.get("tradeUrl"),
                "matchScore": 0.86,
                "matchReasons": ["team", "world-cup", "date"],
            }
        )
    return {
        "id": str(match.get("id") or match.get("matchId") or ""),
        "topic": "world-cup",
        "entity": f"{match.get('homeTeam') or 'TBD'} vs {match.get('awayTeam') or 'TBD'}",
        "country": str(match.get("country") or ""),
        "team": str(match.get("homeTeam") or ""),
        "eventTime": str(match.get("kickoffUtc") or ""),
        "sourceUrl": str(match.get("sourceUrl") or worldcup_dashboard_service.OPENFOOTBALL_2026_URL),
        "confidence": 0.88 if match.get("kickoffUtc") else 0.66,
        "homeTeam": match.get("homeTeam"),
        "awayTeam": match.get("awayTeam"),
        "score": {
            "home": match.get("homeScore"),
            "away": match.get("awayScore"),
        },
        "matchStatus": match.get("status"),
        "stage": match.get("stage"),
        "group": match.get("group"),
        "round": match.get("round"),
        "kickoffUtc": match.get("kickoffUtc"),
        "kickoffLocal": match.get("kickoffLocal"),
        "kickoffBeijing": match.get("kickoffBeijing"),
        "minutesUntilKickoff": _minutes_until(match.get("kickoffUtc"), now=now),
        "venue": match.get("venue"),
        "cityId": match.get("cityId"),
        "city": match.get("city"),
        "weatherRisk": risk,
        "weather": weather or {},
        "broadcastSources": [],
        "marketLinked": bool(match.get("marketLinked") or odds),
        "oddsLinked": bool(match.get("oddsLinked") or odds),
        "relatedPolymarketMarketIds": [market.get("marketId") for market in markets if market.get("marketId")],
        "markets": markets,
        "evidence": {
            "schedule": "openfootball/worldcup.json",
            "weather": weather.get("source") if weather else None,
            "odds": [row.get("provider") or row.get("source") for row in odds[:4]],
        },
    }


def build_world_cup_match_ops_payload(ctx: dict, *, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    dashboard_getter = ctx.get("get_worldcup_dashboard_snapshot")
    dashboard = dashboard_getter() if callable(dashboard_getter) else worldcup_dashboard_service.get_worldcup_dashboard_snapshot(ctx)
    if not isinstance(dashboard, dict):
        dashboard = {}
    matches = [row for row in dashboard.get("matches") or [] if isinstance(row, dict)]
    now = datetime.now(timezone.utc)
    weather_map = _weather_by_city(dashboard)
    odds_map = _odds_by_match(dashboard)
    ranked_matches = sorted(matches, key=lambda row: _status_rank(row, now=now))
    items = []
    for match in ranked_matches:
        city_key = str(match.get("cityId") or "").strip()
        weather = weather_map.get(city_key) or weather_map.get(str(match.get("city") or "").strip().lower())
        odds = odds_map.get(str(match.get("id") or ""), [])
        items.append(_match_item(match, weather=weather, odds=odds, now=now))
    limited = items[: max(1, min(int(limit or DEFAULT_LIMIT), 80))]
    provider_states = dashboard.get("providerStates") if isinstance(dashboard.get("providerStates"), dict) else {}
    status = "ok" if limited and len(matches) >= 64 else "degraded" if limited else "empty"
    return {
        "panelId": PANEL_ID,
        "generatedAt": _utc_now_iso(ctx),
        "status": status,
        "cacheMode": "live-build",
        "freshness": "live" if status == "ok" else "degraded" if limited else "warming",
        "source": "World Cup dashboard evidence layer",
        "sourceUrl": worldcup_dashboard_service.OPENFOOTBALL_2026_URL,
        "sources": {
            "worldcupDashboard": provider_states or {"status": dashboard.get("status") or "unknown"},
            "schedule": provider_states.get("schedule") or "unknown",
            "weather": provider_states.get("weather") or "unknown",
            "odds": provider_states.get("odds") or "unknown",
        },
        "summary": {
            "total": len(items),
            "returned": len(limited),
            "linkedMarkets": len([item for item in items if item.get("marketLinked")]),
            "weatherWatch": len([item for item in items if (item.get("weatherRisk") or {}).get("level") in {"high", "watch"}]),
            "nextKickoffAt": limited[0].get("kickoffUtc") if limited else None,
            "nextMatch": limited[0].get("entity") if limited else None,
        },
        "items": limited,
    }


def _empty_payload(ctx: dict, *, status: str = "warming", cache_mode: str = "warming") -> Dict[str, Any]:
    return {
        "panelId": PANEL_ID,
        "generatedAt": _utc_now_iso(ctx),
        "status": status,
        "cacheMode": cache_mode,
        "freshness": "warming",
        "source": "World Cup dashboard evidence layer",
        "sourceUrl": worldcup_dashboard_service.OPENFOOTBALL_2026_URL,
        "sources": {},
        "summary": {"total": 0, "returned": 0, "linkedMarkets": 0, "weatherWatch": 0, "nextKickoffAt": None, "nextMatch": None},
        "items": [],
    }


def normalize_world_cup_match_ops_payload(payload: Any, *, ctx: dict, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _empty_payload(ctx, status="invalid", cache_mode="invalid")
    result = json.loads(json.dumps(payload, ensure_ascii=True, default=str))
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    result["items"] = items[: max(1, min(int(limit or DEFAULT_LIMIT), 80))]
    result["panelId"] = str(result.get("panelId") or PANEL_ID)
    result["generatedAt"] = str(result.get("generatedAt") or _utc_now_iso(ctx))
    result["status"] = str(result.get("status") or ("ok" if result["items"] else "warming"))
    result["cacheMode"] = str(result.get("cacheMode") or "seeded")
    result["freshness"] = str(result.get("freshness") or ("live" if result["status"] == "ok" else "degraded"))
    result["source"] = str(result.get("source") or "World Cup dashboard evidence layer")
    result["sourceUrl"] = str(result.get("sourceUrl") or worldcup_dashboard_service.OPENFOOTBALL_2026_URL)
    result["sources"] = result.get("sources") if isinstance(result.get("sources"), dict) else {}
    if not isinstance(result.get("summary"), dict):
        result["summary"] = _empty_payload(ctx)["summary"]
    result["summary"] = {**_empty_payload(ctx)["summary"], **result["summary"], "returned": len(result["items"])}
    return result


def _with_cache_mode(payload: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    freshness = "stale" if "stale" in cache_mode else "seeded"
    return {**payload, "cacheMode": cache_mode, "freshness": payload.get("freshness") or freshness}


def _read_seeded_snapshot(ctx: dict) -> Optional[Dict[str, Any]]:
    reader = ctx.get("get_cached_json")
    if callable(reader):
        payload = reader(WORLD_CUP_MATCH_OPS_SNAPSHOT_NAMESPACE, WORLD_CUP_MATCH_OPS_CACHE_KEY)
        if isinstance(payload, dict):
            return _with_cache_mode(payload, "redis-seed")
    store = ctx.get("SNAPSHOT_STORE")
    if store is None:
        return None
    payload = store.get(WORLD_CUP_MATCH_OPS_SNAPSHOT_NAMESPACE, WORLD_CUP_MATCH_OPS_CACHE_KEY)
    if isinstance(payload, dict):
        return _with_cache_mode(payload, "sqlite-seed")
    stale = store.get_stale(WORLD_CUP_MATCH_OPS_SNAPSHOT_NAMESPACE, WORLD_CUP_MATCH_OPS_CACHE_KEY)
    if isinstance(stale, dict):
        return _with_cache_mode(stale, "stale-seed")
    return None


def _store_live(ctx: dict, payload: Dict[str, Any], *, ttl_seconds: int) -> None:
    store = ctx.get("SNAPSHOT_STORE")
    if store is not None:
        store.set(WORLD_CUP_MATCH_OPS_SNAPSHOT_NAMESPACE, WORLD_CUP_MATCH_OPS_CACHE_KEY, payload, ttl_seconds)
    setter = ctx.get("set_cached_json")
    if callable(setter):
        setter(WORLD_CUP_MATCH_OPS_SNAPSHOT_NAMESPACE, WORLD_CUP_MATCH_OPS_CACHE_KEY, payload, ttl_seconds)


def _schedule_live_refresh(ctx: dict, *, limit: int, ttl_seconds: int, reason: str) -> bool:
    refresh_key = f"{WORLD_CUP_MATCH_OPS_SNAPSHOT_NAMESPACE}:{WORLD_CUP_MATCH_OPS_CACHE_KEY}"
    with _LIVE_REFRESH_LOCK:
        if refresh_key in _LIVE_REFRESHING:
            return False
        _LIVE_REFRESHING.add(refresh_key)

    def refresh() -> None:
        logger = getattr(ctx.get("app"), "logger", None)
        try:
            payload = {**build_world_cup_match_ops_payload(ctx, limit=limit), "cacheMode": "live-build"}
            if payload.get("items"):
                _store_live(ctx, payload, ttl_seconds=ttl_seconds)
            elif logger is not None and hasattr(logger, "warning"):
                logger.warning("world cup match ops refresh skipped empty payload reason=%s", reason)
        except Exception:
            if logger is not None:
                logger.exception("world cup match ops refresh failed reason=%s", reason)
        finally:
            with _LIVE_REFRESH_LOCK:
                _LIVE_REFRESHING.discard(refresh_key)

    thread = threading.Thread(target=refresh, name="world-cup-match-ops-refresh", daemon=True)
    thread.start()
    return True


def get_world_cup_match_ops_snapshot(ctx: dict, limit: int = DEFAULT_LIMIT, *, allow_live_build: bool = True) -> Dict[str, Any]:
    ttl_seconds = max(120, int(os.environ.get("POLYDATA_WORLD_CUP_MATCH_OPS_TTL_SECONDS", DEFAULT_TTL_SECONDS) or DEFAULT_TTL_SECONDS))
    seeded = _read_seeded_snapshot(ctx)
    if seeded is not None:
        if allow_live_build and seeded.get("cacheMode") == "stale-seed":
            _schedule_live_refresh(ctx, limit=limit, ttl_seconds=ttl_seconds, reason="stale-seed")
        return normalize_world_cup_match_ops_payload(seeded, ctx=ctx, limit=limit)
    if not allow_live_build:
        return normalize_world_cup_match_ops_payload(_empty_payload(ctx, cache_mode="seed-miss"), ctx=ctx, limit=limit)
    scheduled = _schedule_live_refresh(ctx, limit=limit, ttl_seconds=ttl_seconds, reason="seed-miss")
    mode = "seed-miss-refreshing" if scheduled else "seed-miss-refresh-inflight"
    return normalize_world_cup_match_ops_payload(_empty_payload(ctx, cache_mode=mode), ctx=ctx, limit=limit)
