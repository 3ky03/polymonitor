from __future__ import annotations

from typing import Any, Dict, Optional

from api.services import worldcup_intel_service
from api.services.worldcup.common import utc_now_iso
from api.services.worldcup.odds.bookmaker import WORLDCUP_ODDS_SPORT_KEY, link_bookmaker_odds
from api.services.worldcup.odds.polymarket import link_worldcup_markets, new_market_linker_stats
from api.services.worldcup.payload import normalize_payload
from api.services.worldcup.results import merge_espn_scoreboard_results
from api.services.worldcup.schedule import OPENFOOTBALL_2026_URL, WORLD_CUP_CITIES, fetch_schedule_source, normalize_matches


def build_worldcup_dashboard_payload(
    ctx: Dict[str, Any],
    *,
    include_intel: bool = True,
    include_live_market_links: bool = True,
) -> Dict[str, Any]:
    core = build_worldcup_core_payload(ctx, include_live_market_links=include_live_market_links)
    live = build_worldcup_live_payload(ctx, include_intel=include_intel)
    return merge_worldcup_core_live_payload(core, live)


def _external_odds_states(settings: Any) -> Dict[str, str]:
    return {
        "theRundown": "configured" if str(getattr(settings, "the_rundown_api_key", "") or "").strip() else "missing-key",
        "apiFootball": "configured" if str(getattr(settings, "api_football_api_key", "") or "").strip() else "missing-key",
        "betfair": "configured" if str(getattr(settings, "betfair_app_key", "") or "").strip() else "missing-key",
        "matchbook": "configured" if str(getattr(settings, "matchbook_api_username", "") or "").strip() else "missing-key",
    }


def _tournament(matches: list[Dict[str, Any]]) -> Dict[str, Any]:
    starts_at = matches[0]["kickoffUtc"] if matches else "2026-06-11T19:00:00Z"
    ends_at = matches[-1]["kickoffUtc"] if matches else "2026-07-19T19:00:00Z"
    return {
        "id": "fifa-world-cup-2026",
        "name": "FIFA World Cup 2026",
        "startsAt": starts_at,
        "endsAt": ends_at,
        "timezone": "Asia/Shanghai",
    }


def build_worldcup_core_payload(
    ctx: Dict[str, Any],
    *,
    include_live_market_links: bool = True,
) -> Dict[str, Any]:
    generated_at = utc_now_iso()
    settings = ctx.get("SETTINGS")
    source_matches, schedule_source = fetch_schedule_source(ctx)
    matches = normalize_matches(source_matches)
    if include_live_market_links:
        bookmaker_odds, bookmaker_state, bookmaker_linker = link_bookmaker_odds(ctx, matches)
        configured_scan_limit = int(getattr(settings, "worldcup_market_link_scan_limit", 12) or 12)
        market_odds, market_state, market_linker = link_worldcup_markets(ctx, matches, settings_scan_limit=configured_scan_limit)
        odds = [*bookmaker_odds, *market_odds]
        odds_state = "ok" if odds else market_state if market_state != "empty" else bookmaker_state
    else:
        odds = []
        odds_state = "deferred"
        bookmaker_linker = {"sportKey": WORLDCUP_ODDS_SPORT_KEY, "events": 0, "matched": 0, "mode": "deferred"}
        market_linker = {
            **new_market_linker_stats(scan_limit=0, scheduled_count=sum(1 for match in matches if str(match.get("status") or "") != "finished")),
            "mode": "deferred",
            "reason": "live-market-linking-disabled-for-request",
        }
    return normalize_payload(
        {
            "generatedAt": generated_at,
            "cacheMode": "core" if matches else "source-required",
            "tournament": _tournament(matches),
            "cities": WORLD_CUP_CITIES,
            "matches": matches,
            "news": [],
            "weather": [],
            "rosters": [],
            "odds": odds,
            "marketLinker": market_linker,
            "intelligence": None,
            "source": f"{schedule_source} / bookmaker + Polymarket odds core",
            "sourceUrl": OPENFOOTBALL_2026_URL,
            "providerStates": {
                "schedule": "ok" if len(matches) >= 100 else "source-required",
                "matchResults": "deferred-live-seed",
                "worldcupIntel": "deferred-live-seed",
                "weather": "deferred-live-seed",
                "odds": odds_state,
                "bookmakerOdds": bookmaker_state if include_live_market_links else "deferred",
                **_external_odds_states(settings),
                "rosters": "source-required",
            },
            "bookmakerLinker": bookmaker_linker,
            "resultLinker": {"source": "ESPN scoreboard", "state": "deferred-live-seed", "matched": 0, "completed": 0},
        }
    )


def build_worldcup_live_payload(
    ctx: Dict[str, Any],
    *,
    include_intel: bool = True,
) -> Dict[str, Any]:
    generated_at = utc_now_iso()
    source_matches, schedule_source = fetch_schedule_source(ctx)
    matches = normalize_matches(source_matches)
    matches, result_linker = merge_espn_scoreboard_results(ctx, matches)
    intel: Optional[Dict[str, Any]] = None
    if include_intel:
        try:
            intel = worldcup_intel_service.get_worldcup_intel_snapshot(ctx, limit=120)
        except Exception as exc:
            intel = {"status": "error", "cacheMode": "source-required", "error": exc.__class__.__name__, "news": [], "weather": [], "signals": []}
    weather = intel.get("weather") if isinstance(intel, dict) and isinstance(intel.get("weather"), list) else []
    intel_news = intel.get("news") if isinstance(intel, dict) and isinstance(intel.get("news"), list) else []
    news = intel_news[:24]
    return normalize_payload(
        {
            "generatedAt": generated_at,
            "cacheMode": "live",
            "tournament": _tournament(matches),
            "cities": WORLD_CUP_CITIES,
            "matches": matches,
            "news": news,
            "weather": weather,
            "rosters": [],
            "odds": [],
            "marketLinker": {"mode": "core-seed-only", "matched": 0, "candidates": 0},
            "intelligence": intel,
            "source": f"{schedule_source} / {result_linker.get('source') if result_linker.get('matched') else 'score source pending'} / {intel.get('source') if isinstance(intel, dict) else 'runtime intel deferred'}",
            "sourceUrl": OPENFOOTBALL_2026_URL,
            "providerStates": {
                "schedule": "ok" if len(matches) >= 100 else "source-required",
                "matchResults": str(result_linker.get("state") or "source-required"),
                "worldcupIntel": str((intel or {}).get("status") or "unknown"),
                "weather": "ok" if weather else "empty",
                "odds": "core-seed-only",
                "bookmakerOdds": "core-seed-only",
                "rosters": "source-required",
            },
            "bookmakerLinker": {"sportKey": WORLDCUP_ODDS_SPORT_KEY, "events": 0, "matched": 0, "mode": "core-seed-only"},
            "resultLinker": result_linker,
        }
    )


_LIVE_MATCH_FIELDS = {
    "homeScore",
    "awayScore",
    "status",
    "minute",
    "scoreSource",
    "scoreSourceUrl",
    "scoreEventId",
    "scoreStatus",
    "scoreUpdatedAt",
}


def merge_worldcup_core_live_payload(core: Dict[str, Any], live: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    core_payload = normalize_payload(core or {})
    live_payload = normalize_payload(live or {})
    live_by_id = {str(match.get("id") or ""): match for match in live_payload.get("matches", []) if isinstance(match, dict)}
    merged_matches: list[Dict[str, Any]] = []
    for match in core_payload.get("matches", []):
        merged = dict(match)
        live_match = live_by_id.get(str(match.get("id") or ""))
        if isinstance(live_match, dict):
            for field in _LIVE_MATCH_FIELDS:
                if field in live_match:
                    merged[field] = live_match[field]
        merged_matches.append(merged)
    if not merged_matches and live_payload.get("matches"):
        merged_matches = [dict(match) for match in live_payload.get("matches", []) if isinstance(match, dict)]

    core_states = core_payload.get("providerStates") if isinstance(core_payload.get("providerStates"), dict) else {}
    live_states = live_payload.get("providerStates") if isinstance(live_payload.get("providerStates"), dict) else {}
    provider_states = {**core_states, **live_states}
    if core_states.get("odds") and live_states.get("odds") == "core-seed-only":
        provider_states["odds"] = core_states.get("odds")
    if core_states.get("bookmakerOdds") and live_states.get("bookmakerOdds") == "core-seed-only":
        provider_states["bookmakerOdds"] = core_states.get("bookmakerOdds")

    return normalize_payload(
        {
            **core_payload,
            "generatedAt": live_payload.get("generatedAt") or core_payload.get("generatedAt"),
            "cacheMode": "remote",
            "matches": merged_matches,
            "news": live_payload.get("news") or core_payload.get("news") or [],
            "weather": live_payload.get("weather") or core_payload.get("weather") or [],
            "intelligence": live_payload.get("intelligence") or core_payload.get("intelligence"),
            "providerStates": provider_states,
            "resultLinker": live_payload.get("resultLinker") or core_payload.get("resultLinker"),
            "source": f"{core_payload.get('source') or 'World Cup core'} / {live_payload.get('source') or 'World Cup live'}",
        }
    )
