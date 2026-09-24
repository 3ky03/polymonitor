from __future__ import annotations

from typing import Any, Dict, Optional

from api.services.worldcup.schedule import OPENFOOTBALL_2026_URL, WORLD_CUP_CITIES, utc_now_iso


def normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    cities = payload.get("cities") if isinstance(payload.get("cities"), list) else WORLD_CUP_CITIES
    weather = payload.get("weather") if isinstance(payload.get("weather"), list) else []
    news = payload.get("news") if isinstance(payload.get("news"), list) else []
    rosters = payload.get("rosters") if isinstance(payload.get("rosters"), list) else []
    odds = payload.get("odds") if isinstance(payload.get("odds"), list) else []
    market_linker = payload.get("marketLinker") if isinstance(payload.get("marketLinker"), dict) else {}
    bookmaker_linker = payload.get("bookmakerLinker") if isinstance(payload.get("bookmakerLinker"), dict) else {}
    result_linker = payload.get("resultLinker") if isinstance(payload.get("resultLinker"), dict) else {}
    return {
        "generatedAt": str(payload.get("generatedAt") or utc_now_iso()),
        "cacheMode": str(payload.get("cacheMode") or "remote"),
        "tournament": payload.get("tournament") if isinstance(payload.get("tournament"), dict) else {
            "id": "fifa-world-cup-2026",
            "name": "FIFA World Cup 2026",
            "startsAt": "2026-06-11T19:00:00Z",
            "endsAt": "2026-07-19T19:00:00Z",
            "timezone": "Asia/Shanghai",
        },
        "cities": cities,
        "matches": matches,
        "news": news,
        "weather": weather,
        "rosters": rosters,
        "odds": odds,
        "intelligence": payload.get("intelligence") if isinstance(payload.get("intelligence"), dict) else None,
        "source": str(payload.get("source") or "World Cup verified dashboard"),
        "sourceUrl": str(payload.get("sourceUrl") or OPENFOOTBALL_2026_URL),
        "providerStates": payload.get("providerStates") if isinstance(payload.get("providerStates"), dict) else {},
        "marketLinker": market_linker,
        "bookmakerLinker": bookmaker_linker,
        "resultLinker": result_linker,
        "summary": {
            "cities": len(cities),
            "matches": len(matches),
            "news": len(news),
            "weatherCities": len(weather),
            "rosters": len(rosters),
            "odds": len(odds),
            "oddsCandidates": int(market_linker.get("candidates") or 0),
            "oddsMatched": int(market_linker.get("matched") or len(odds)),
            "bookmakerEvents": int(bookmaker_linker.get("events") or 0),
            "bookmakerMatched": int(bookmaker_linker.get("matched") or 0),
            "scoreEvents": int(result_linker.get("completed") or 0),
            "scoreMatched": int(result_linker.get("matched") or 0),
        },
    }


def has_generated_fallback_artifacts(payload: Dict[str, Any]) -> bool:
    cache_mode = str(payload.get("cacheMode") or "").lower()
    source = str(payload.get("source") or "").lower()
    provider_states = payload.get("providerStates") if isinstance(payload.get("providerStates"), dict) else {}
    odds = payload.get("odds") if isinstance(payload.get("odds"), list) else []
    weather = payload.get("weather") if isinstance(payload.get("weather"), list) else []
    return (
        cache_mode in {"seed", "seeded"}
        or "dashboard seed" in source
        or "fallback" in source
        or "dashboardSeed" in provider_states
        or any(str(row.get("provider") or "") == "Model consensus watch" for row in odds if isinstance(row, dict))
        or any(str(row.get("source") or "") == "seed-estimate" for row in weather if isinstance(row, dict))
    )


def fallback_worldcup_dashboard_payload(exc: Exception, cached: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if cached:
        return {**cached, "status": "stale", "error": exc.__class__.__name__}
    return normalize_payload(
        {
            "generatedAt": utc_now_iso(),
            "cacheMode": "source-required",
            "matches": [],
            "cities": WORLD_CUP_CITIES,
            "weather": [],
            "news": [],
            "rosters": [],
            "odds": [],
            "providerStates": {"worldcupDashboard": f"error:{exc.__class__.__name__}"},
        }
    )
