from __future__ import annotations

import json
from typing import Any, Dict

from api.services.worldcup.payload import normalize_payload

WORLDCUP_PANEL_NAMESPACE = "snapshot:sports:worldcup-panel"

WORLD_CUP_PANEL_IDS = (
    "calendar",
    "match-control",
    "win-probability",
    "line-movement",
    "odds-source-coverage",
    "venue-risk",
    "market-board",
    "group-advance",
    "team-power",
    "injury-load",
    "match-tempo",
    "odds-liquidity",
    "ref-cards",
    "travel-load",
    "news-impact",
    "news",
    "team-status",
    "lineup-board",
    "match-model",
    "group-table",
    "media-wire",
    "host-venue",
    "venue-ref",
    "source-audit",
)

CORE_PANEL_IDS = {
    "calendar",
    "win-probability",
    "line-movement",
    "odds-source-coverage",
    "market-board",
    "group-advance",
    "odds-liquidity",
    "match-model",
    "group-table",
    "source-audit",
}


def panel_ttl_seconds(panel_id: str, core_ttl: int, live_ttl: int) -> int:
    return int(core_ttl if panel_id in CORE_PANEL_IDS else live_ttl)


def _selected_match(payload: Dict[str, Any]) -> Dict[str, Any]:
    matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    upcoming = [match for match in matches if isinstance(match, dict) and str(match.get("status") or "") != "finished"]
    return (upcoming or [match for match in matches if isinstance(match, dict)] or [{}])[0]


def _match_id(payload: Dict[str, Any]) -> str:
    return str(_selected_match(payload).get("id") or "")


def _selected_odds(payload: Dict[str, Any], match_id: str) -> list[Dict[str, Any]]:
    rows = payload.get("odds") if isinstance(payload.get("odds"), list) else []
    return [row for row in rows if isinstance(row, dict) and str(row.get("matchId") or "") == match_id]


def _selected_news(payload: Dict[str, Any], match: Dict[str, Any]) -> list[Dict[str, Any]]:
    rows = payload.get("news") if isinstance(payload.get("news"), list) else []
    teams = {str(match.get("homeTeam") or "").lower(), str(match.get("awayTeam") or "").lower()}
    match_id = str(match.get("id") or "")
    selected = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_teams = {str(team).lower() for team in row.get("teams", []) if team}
        if str(row.get("matchId") or "") == match_id or teams.intersection(row_teams):
            selected.append(row)
    return selected or rows[:6]


def _panel_data(panel_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    match = _selected_match(payload)
    match_id = str(match.get("id") or "")
    odds = _selected_odds(payload, match_id)
    common = {
        "selectedMatchId": match_id,
        "selectedMatch": match,
        "providerStates": payload.get("providerStates") if isinstance(payload.get("providerStates"), dict) else {},
    }
    if panel_id in {"calendar", "group-advance", "group-table", "match-control", "match-model"}:
        return {**common, "matches": payload.get("matches", []), "cities": payload.get("cities", []), "resultLinker": payload.get("resultLinker", {})}
    if panel_id in {"win-probability", "line-movement", "market-board", "odds-liquidity"}:
        return {**common, "odds": odds, "allOdds": payload.get("odds", []), "marketLinker": payload.get("marketLinker", {}), "bookmakerLinker": payload.get("bookmakerLinker", {})}
    if panel_id == "odds-source-coverage":
        return {**common, "summary": payload.get("summary", {}), "marketLinker": payload.get("marketLinker", {}), "bookmakerLinker": payload.get("bookmakerLinker", {})}
    if panel_id in {"news", "news-impact", "media-wire", "ref-cards", "venue-ref"}:
        return {**common, "news": _selected_news(payload, match), "intelligence": payload.get("intelligence")}
    if panel_id in {"venue-risk", "travel-load", "host-venue"}:
        return {**common, "weather": payload.get("weather", []), "cities": payload.get("cities", []), "matches": payload.get("matches", [])}
    if panel_id in {"team-power", "injury-load", "team-status", "lineup-board"}:
        return {**common, "rosters": payload.get("rosters", []), "news": _selected_news(payload, match), "odds": odds}
    if panel_id == "match-tempo":
        return {**common, "weather": payload.get("weather", []), "odds": odds, "resultLinker": payload.get("resultLinker", {})}
    if panel_id == "source-audit":
        return {
            **common,
            "summary": payload.get("summary", {}),
            "source": payload.get("source"),
            "sourceUrl": payload.get("sourceUrl"),
            "marketLinker": payload.get("marketLinker", {}),
            "bookmakerLinker": payload.get("bookmakerLinker", {}),
            "resultLinker": payload.get("resultLinker", {}),
        }
    return common


def build_worldcup_panel_payloads(dashboard_payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    payload = normalize_payload(dashboard_payload or {})
    base = {
        "generatedAt": payload.get("generatedAt"),
        "cacheMode": "seeded",
        "workspace": "worldcup",
        "source": payload.get("source"),
        "sourceUrl": payload.get("sourceUrl"),
    }
    return {
        panel_id: {
            **base,
            "panelId": panel_id,
            "data": _panel_data(panel_id, payload),
        }
        for panel_id in WORLD_CUP_PANEL_IDS
    }


def store_worldcup_panel_payloads(ctx: Dict[str, Any], dashboard_payload: Dict[str, Any], *, core_ttl: int, live_ttl: int) -> Dict[str, Dict[str, Any]]:
    panels = build_worldcup_panel_payloads(dashboard_payload)
    store = ctx.get("SNAPSHOT_STORE")
    setter = ctx.get("set_cached_json")
    for panel_id, payload in panels.items():
        ttl_seconds = panel_ttl_seconds(panel_id, core_ttl, live_ttl)
        if store is not None:
            store.set(WORLDCUP_PANEL_NAMESPACE, panel_id, payload, ttl_seconds)
        if callable(setter):
            setter(WORLDCUP_PANEL_NAMESPACE, panel_id, payload, ttl_seconds)
        else:
            redis_client = ctx.get("redis_client") or ctx.get("REDIS_CLIENT")
            redis_prefix = str(ctx.get("redis_prefix") or ctx.get("REDIS_PREFIX") or "")
            if redis_client is not None:
                redis_client.set(f"{redis_prefix}{WORLDCUP_PANEL_NAMESPACE}:{panel_id}", json.dumps(payload, ensure_ascii=True, default=str), ex=ttl_seconds)
    return panels
