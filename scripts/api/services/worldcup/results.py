from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from api.services.worldcup.common import headers, parse_iso, safe_int, team_tokens, utc_now_iso


ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
DEFAULT_SCOREBOARD_DATES = "20260611-20260719"


def _competition(event: Dict[str, Any]) -> Dict[str, Any]:
    competitions = event.get("competitions") if isinstance(event.get("competitions"), list) else []
    return competitions[0] if competitions and isinstance(competitions[0], dict) else {}


def _status_type(event: Dict[str, Any], competition: Dict[str, Any]) -> Dict[str, Any]:
    status = competition.get("status") if isinstance(competition.get("status"), dict) else event.get("status")
    status = status if isinstance(status, dict) else {}
    return status.get("type") if isinstance(status.get("type"), dict) else {}


def _score_value(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return safe_int(value, default=-1) if str(value).strip() != "" else None


def _team_name(competitor: Dict[str, Any]) -> str:
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    return str(team.get("displayName") or team.get("shortDisplayName") or competitor.get("displayName") or "").strip()


def _home_away_competitors(event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    competition = _competition(event)
    competitors = competition.get("competitors") if isinstance(competition.get("competitors"), list) else []
    home = next((row for row in competitors if isinstance(row, dict) and row.get("homeAway") == "home"), None)
    away = next((row for row in competitors if isinstance(row, dict) and row.get("homeAway") == "away"), None)
    if (home is None or away is None) and len(competitors) >= 2:
        rows = [row for row in competitors if isinstance(row, dict)]
        home = home or rows[0]
        away = away or rows[1]
    return home, away


def _event_group(event: Dict[str, Any]) -> str:
    competition = _competition(event)
    note = str(competition.get("altGameNote") or event.get("shortName") or "")
    match = re.search(r"\bGroup\s+([A-L])\b", note, re.IGNORECASE)
    return f"Group {match.group(1).upper()}" if match else ""


def _is_completed(event: Dict[str, Any]) -> bool:
    competition = _competition(event)
    status_type = _status_type(event, competition)
    if bool(status_type.get("completed")):
        return True
    return str(status_type.get("state") or "").lower() == "post"


def _extract_espn_result(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    home, away = _home_away_competitors(event)
    if not home or not away:
        return None
    home_score = _score_value(home.get("score"))
    away_score = _score_value(away.get("score"))
    if home_score is None or away_score is None or home_score < 0 or away_score < 0:
        return None
    competition = _competition(event)
    status_type = _status_type(event, competition)
    event_date = str(competition.get("date") or event.get("date") or "")
    return {
        "eventId": str(event.get("id") or competition.get("id") or ""),
        "date": event_date,
        "group": _event_group(event),
        "homeTeam": _team_name(home),
        "awayTeam": _team_name(away),
        "homeScore": home_score,
        "awayScore": away_score,
        "completed": _is_completed(event),
        "status": str(status_type.get("shortDetail") or status_type.get("description") or status_type.get("name") or ""),
        "source": "ESPN scoreboard",
        "sourceUrl": str((event.get("links") or [{}])[0].get("href") if isinstance(event.get("links"), list) and event.get("links") else ""),
    }


def fetch_espn_scoreboard_results(ctx: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    getter = ctx.get("http_json_get")
    if not callable(getter):
        return [], {"source": "ESPN scoreboard", "state": "source-required", "events": 0, "completed": 0}
    try:
        payload = getter(
            ESPN_SCOREBOARD_URL,
            params={"dates": DEFAULT_SCOREBOARD_DATES, "limit": 300},
            timeout=12,
            headers=headers(),
        )
    except Exception as exc:
        return [], {"source": "ESPN scoreboard", "state": f"error:{exc.__class__.__name__}", "events": 0, "completed": 0}
    events = payload.get("events") if isinstance(payload, dict) else []
    results = [_extract_espn_result(event) for event in events if isinstance(event, dict)]
    rows = [row for row in results if row]
    completed = [row for row in rows if row.get("completed")]
    state = "ok" if completed else "empty" if events else "source-required"
    return rows, {"source": "ESPN scoreboard", "state": state, "events": len(events or []), "completed": len(completed)}


def _team_match(left: Any, right: Any) -> bool:
    left_tokens = team_tokens(left)
    right_tokens = team_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    return bool(overlap) or left_tokens <= right_tokens or right_tokens <= left_tokens


def _time_delta_minutes(match: Dict[str, Any], result: Dict[str, Any]) -> Optional[float]:
    match_time = parse_iso(match.get("kickoffUtc"))
    result_time = parse_iso(result.get("date"))
    if not match_time or not result_time:
        return None
    return abs((match_time - result_time).total_seconds()) / 60


def _alignment(match: Dict[str, Any], result: Dict[str, Any]) -> Optional[str]:
    if _team_match(match.get("homeTeam"), result.get("homeTeam")) and _team_match(match.get("awayTeam"), result.get("awayTeam")):
        return "direct"
    if _team_match(match.get("homeTeam"), result.get("awayTeam")) and _team_match(match.get("awayTeam"), result.get("homeTeam")):
        return "flipped"
    return None


def _best_match(matches: List[Dict[str, Any]], result: Dict[str, Any], used_ids: set[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    best: Tuple[int, Optional[Dict[str, Any]], Optional[str]] = (0, None, None)
    for match in matches:
        match_id = str(match.get("id") or "")
        if match_id in used_ids:
            continue
        alignment = _alignment(match, result)
        if not alignment:
            continue
        delta = _time_delta_minutes(match, result)
        if delta is not None and delta > 36 * 60:
            continue
        score = 1000
        if delta is not None:
            score -= int(delta)
        if result.get("group") and result.get("group") == match.get("group"):
            score += 50
        if alignment == "direct":
            score += 20
        if score > best[0]:
            best = (score, match, alignment)
    return best[1], best[2]


def merge_match_results(matches: List[Dict[str, Any]], results: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    next_matches = [dict(match) for match in matches]
    used_ids: set[str] = set()
    matched = 0
    completed = [row for row in results if row.get("completed")]
    updated_at = utc_now_iso()
    for result in completed:
        match, alignment = _best_match(next_matches, result, used_ids)
        if not match or not alignment:
            continue
        if alignment == "direct":
            home_score = result.get("homeScore")
            away_score = result.get("awayScore")
        else:
            home_score = result.get("awayScore")
            away_score = result.get("homeScore")
        match.update(
            {
                "homeScore": home_score,
                "awayScore": away_score,
                "status": "finished",
                "scoreSource": result.get("source") or "ESPN scoreboard",
                "scoreSourceUrl": result.get("sourceUrl") or "",
                "scoreEventId": result.get("eventId") or "",
                "scoreStatus": result.get("status") or "FT",
                "scoreUpdatedAt": updated_at,
            }
        )
        used_ids.add(str(match.get("id") or ""))
        matched += 1
    state = "ok" if matched else "empty" if completed else "source-required"
    return next_matches, {
        "source": "ESPN scoreboard",
        "state": state,
        "completed": len(completed),
        "matched": matched,
        "unmatched": max(0, len(completed) - matched),
    }


def merge_espn_scoreboard_results(ctx: Dict[str, Any], matches: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    results, fetch_stats = fetch_espn_scoreboard_results(ctx)
    next_matches, merge_stats = merge_match_results(matches, results)
    state = merge_stats.get("state") if merge_stats.get("matched") else fetch_stats.get("state")
    return next_matches, {**fetch_stats, **merge_stats, "state": state}
