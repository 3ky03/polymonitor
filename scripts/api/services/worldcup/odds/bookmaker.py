from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from api.services.worldcup.common import headers, parse_iso, team_tokens, utc_now_iso
from api.services.worldcup.odds.probability import ordered_outcomes

WORLDCUP_ODDS_SPORT_KEY = "soccer_fifa_world_cup"
WORLDCUP_ODDS_REGIONS = "us,uk,eu,au"
DEFAULT_WORLDCUP_ODDS_MARKETS = ("h2h", "spreads", "totals")
SUPPORTED_WORLDCUP_ODDS_MARKETS = {
    "h2h",
    "spreads",
    "totals",
    "btts",
    "draw_no_bet",
    "double_chance",
}
BOOKMAKER_MARKET_TYPES = {
    "h2h": "moneyline",
    "spreads": "spread",
    "totals": "total_goals",
    "btts": "both_teams_to_score",
    "draw_no_bet": "draw_no_bet",
    "double_chance": "double_chance",
}


def safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def mean(values: Iterable[float]) -> Optional[float]:
    rows = [value for value in values if value == value]
    return sum(rows) / len(rows) if rows else None


def _configured_worldcup_odds_markets(settings: Any) -> str:
    raw = str(getattr(settings, "worldcup_odds_markets", "") or "").strip()
    selected = []
    for item in raw.split(","):
        key = item.strip().lower()
        if key and key in SUPPORTED_WORLDCUP_ODDS_MARKETS and key not in selected:
            selected.append(key)
    if not selected:
        selected = list(DEFAULT_WORLDCUP_ODDS_MARKETS)
    return ",".join(selected)


def _canonical_market_outcome_label(match: Dict[str, Any], market_key: str, label: Any) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if market_key in {"h2h", "spreads", "draw_no_bet"}:
        return _canonical_outcome_label(match, text)
    if market_key == "totals":
        if lower.startswith("over"):
            return "Over"
        if lower.startswith("under"):
            return "Under"
    if market_key == "btts":
        if lower in {"yes", "y"}:
            return "Yes"
        if lower in {"no", "n"}:
            return "No"
    return text


def _outcome_bucket_key(row: Dict[str, Any]) -> str:
    point = row.get("point")
    point_text = "" if point is None else f":{float(point):g}"
    return f"{row.get('name')}{point_text}"


def build_bookmaker_market_outcomes(
    match: Dict[str, Any],
    event: Dict[str, Any],
    requested_market_key: str,
    canonical_outcome_label: Callable[[Dict[str, Any], Any], str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    bookmaker_rows: List[Dict[str, Any]] = []
    for bookmaker in event.get("bookmakers") or []:
        if not isinstance(bookmaker, dict):
            continue
        book_key = str(bookmaker.get("key") or "").strip()
        book_title = str(bookmaker.get("title") or bookmaker.get("key") or "Book").strip()
        book_market_keys: List[str] = []
        for market in bookmaker.get("markets") or []:
            if not isinstance(market, dict):
                continue
            current_market_key = str(market.get("key") or "").strip()
            if current_market_key:
                book_market_keys.append(current_market_key)
            if current_market_key != requested_market_key:
                continue
            raw_rows = []
            for outcome in market.get("outcomes") or []:
                if not isinstance(outcome, dict):
                    continue
                price = safe_float(outcome.get("price"))
                name = _canonical_market_outcome_label(match, requested_market_key, outcome.get("name"))
                point = safe_float(outcome.get("point"))
                if not name or price is None or price <= 1:
                    continue
                raw_rows.append({"name": name, "price": price, "point": point})
            overround = sum(1 / row["price"] for row in raw_rows)
            if overround <= 0:
                continue
            book_outcomes: List[Dict[str, Any]] = []
            for row in raw_rows:
                implied = (1 / row["price"]) / overround * 100
                bucket = buckets.setdefault(_outcome_bucket_key(row), {"name": row["name"], "point": row["point"], "prices": [], "probabilities": [], "books": []})
                bucket["prices"].append(row["price"])
                bucket["probabilities"].append(implied)
                bucket["books"].append(book_title)
                book_row = {
                    "name": row["name"],
                    "decimalOdds": round(row["price"], 3),
                    "impliedProbability": round(implied, 2),
                    "marketKey": requested_market_key,
                }
                if row["point"] is not None:
                    book_row["point"] = round(float(row["point"]), 3)
                book_outcomes.append(book_row)
            if book_outcomes:
                bookmaker_rows.append(
                    {
                        "key": book_key,
                        "title": book_title,
                        "lastUpdate": bookmaker.get("last_update"),
                        "markets": sorted(set(book_market_keys)),
                        "outcomes": ordered_outcomes(match, book_outcomes),
                    }
                )
    outcomes: List[Dict[str, Any]] = []
    for bucket in buckets.values():
        implied = mean([float(value) for value in bucket.get("probabilities") or []])
        prices = [float(value) for value in bucket.get("prices") or []]
        if implied is None or not prices:
            continue
        outcome_row = {
            "name": bucket.get("name"),
            "decimalOdds": round(mean(prices) or max(prices), 3),
            "bestDecimalOdds": round(max(prices), 3),
            "impliedProbability": round(implied, 2),
            "bookCount": len(prices),
            "source": "bookmaker-consensus",
            "marketKey": requested_market_key,
        }
        if bucket.get("point") is not None:
            outcome_row["point"] = round(float(bucket["point"]), 3)
        outcomes.append(outcome_row)
    if requested_market_key == "h2h":
        return ordered_outcomes(match, outcomes), bookmaker_rows
    return sorted(outcomes, key=lambda row: (float(row.get("point") or 0), str(row.get("name") or ""))), bookmaker_rows


def build_bookmaker_h2h_outcomes(
    match: Dict[str, Any],
    event: Dict[str, Any],
    canonical_outcome_label: Callable[[Dict[str, Any], Any], str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    return build_bookmaker_market_outcomes(match, event, "h2h", canonical_outcome_label)


def _canonical_outcome_label(match: Dict[str, Any], label: Any) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    lower = text.lower()
    home = str(match.get("homeTeam") or "").strip()
    away = str(match.get("awayTeam") or "").strip()
    if lower in {"draw", "tie", "x"} or "draw" in lower:
        return "Draw"
    home_tokens = team_tokens(home)
    away_tokens = team_tokens(away)
    label_tokens = team_tokens(text)

    def score(team_name: str, team_token_set: set[str]) -> int:
        team_lower = str(team_name or "").lower()
        if not team_lower:
            return 0
        if lower == team_lower:
            return 100
        value = len(team_token_set & label_tokens)
        if team_lower in lower or lower in team_lower:
            value += 4
        return value

    home_score = score(home, home_tokens)
    away_score = score(away, away_tokens)
    if home_score > away_score and home_score > 0:
        return home
    if away_score > home_score and away_score > 0:
        return away
    return text


def _team_match_score(left: Any, right: Any) -> int:
    left_text = str(left or "").strip().lower()
    right_text = str(right or "").strip().lower()
    if not left_text or not right_text:
        return 0
    if left_text == right_text:
        return 4
    left_tokens = team_tokens(left_text)
    right_tokens = team_tokens(right_text)
    overlap = left_tokens & right_tokens
    if not overlap:
        return 0
    return 3 if len(overlap) >= min(len(left_tokens), len(right_tokens), 2) else 1


def _bookmaker_event_score(match: Dict[str, Any], event: Dict[str, Any]) -> int:
    home_score = _team_match_score(match.get("homeTeam"), event.get("home_team"))
    away_score = _team_match_score(match.get("awayTeam"), event.get("away_team"))
    reversed_home_score = _team_match_score(match.get("homeTeam"), event.get("away_team"))
    reversed_away_score = _team_match_score(match.get("awayTeam"), event.get("home_team"))
    team_score = max(home_score + away_score, reversed_home_score + reversed_away_score)
    if team_score < 4:
        return 0
    match_time = parse_iso(match.get("kickoffUtc"))
    event_time = parse_iso(event.get("commence_time"))
    time_score = 0
    if match_time and event_time:
        delta_hours = abs((match_time - event_time).total_seconds()) / 3600
        if delta_hours <= 2:
            time_score = 8
        elif delta_hours <= 8:
            time_score = 4
        elif match_time.date() == event_time.date():
            time_score = 2
    return team_score * 4 + time_score


def _bookmaker_error_state(exc: Exception) -> Tuple[str, Dict[str, Any]]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    error_code = ""
    message = ""
    if response is not None:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error_code = str(payload.get("error_code") or payload.get("code") or "").strip()
                message = str(payload.get("message") or payload.get("error") or "").strip()
        except Exception:
            try:
                message = str(getattr(response, "text", "") or "").strip()
            except Exception:
                message = ""
    normalized_code = error_code.upper()
    normalized_message = message.lower()
    if normalized_code == "OUT_OF_USAGE_CREDITS" or "usage quota" in normalized_message:
        return "quota-exhausted", {"httpStatus": status_code, "errorCode": error_code or "OUT_OF_USAGE_CREDITS"}
    if normalized_code in {"MISSING_KEY", "NO_API_KEY"} or "api key is missing" in normalized_message:
        return "missing-key", {"httpStatus": status_code, "errorCode": error_code or "MISSING_KEY"}
    if normalized_code in {"INVALID_KEY", "UNAUTHORIZED"} or status_code in {401, 403}:
        return "unauthorized", {"httpStatus": status_code, "errorCode": error_code or "UNAUTHORIZED"}
    if normalized_code in {"RATE_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS"} or status_code == 429:
        return "rate-limited", {"httpStatus": status_code, "errorCode": error_code or "RATE_LIMIT_EXCEEDED"}
    return "error", {"httpStatus": status_code, "errorCode": error_code or exc.__class__.__name__}


def fetch_bookmaker_events(ctx: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    getter = ctx.get("http_json_get")
    settings = ctx.get("SETTINGS")
    api_key = str(getattr(settings, "the_odds_api_key", "") or "").strip()
    if not callable(getter) or not api_key:
        return [], "missing-key", {"sportKey": WORLDCUP_ODDS_SPORT_KEY, "events": 0, "matched": 0}
    base_url = str(getattr(settings, "the_odds_api_base_url", "") or "https://api.the-odds-api.com").rstrip("/")
    markets = _configured_worldcup_odds_markets(settings)
    try:
        payload = getter(
            f"{base_url}/v4/sports/{WORLDCUP_ODDS_SPORT_KEY}/odds/",
            params={
                "apiKey": api_key,
                "regions": WORLDCUP_ODDS_REGIONS,
                "markets": markets,
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
            timeout=12,
            headers=headers(),
        )
    except Exception as exc:
        state, error_stats = _bookmaker_error_state(exc)
        return [], state, {"sportKey": WORLDCUP_ODDS_SPORT_KEY, "markets": markets.split(","), "events": 0, "matched": 0, **error_stats}
    events = [item for item in (payload if isinstance(payload, list) else []) if isinstance(item, dict)]
    return events, "ok" if events else "empty", {"sportKey": WORLDCUP_ODDS_SPORT_KEY, "markets": markets.split(","), "events": len(events), "matched": 0}


def link_bookmaker_odds(ctx: Dict[str, Any], matches: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    events, state, stats = fetch_bookmaker_events(ctx)
    if not events or not matches:
        return [], state, stats
    generated_at = utc_now_iso()
    rows: List[Dict[str, Any]] = []
    used_events: set[str] = set()
    for match in matches:
        if str(match.get("status") or "") == "finished":
            continue
        best: Optional[Dict[str, Any]] = None
        best_score = 0
        for event in events:
            event_id = str(event.get("id") or event.get("commence_time") or "")
            if event_id in used_events:
                continue
            score = _bookmaker_event_score(match, event)
            if score > best_score:
                best = event
                best_score = score
        if not best or best_score <= 0:
            continue
        event_id = str(best.get("id") or best.get("commence_time") or "")
        used_events.add(event_id)
        event_rows = 0
        requested_markets = stats.get("markets") if isinstance(stats.get("markets"), list) else list(DEFAULT_WORLDCUP_ODDS_MARKETS)
        for market_key in requested_markets:
            outcomes, bookmakers = build_bookmaker_market_outcomes(match, best, str(market_key), _canonical_outcome_label)
            if not outcomes:
                continue
            book_count = max((int(outcome.get("bookCount") or 0) for outcome in outcomes), default=0)
            rows.append(
                {
                    "id": f"{match.get('id')}:bookmaker-{market_key}",
                    "matchId": match.get("id"),
                    "homeTeam": match.get("homeTeam"),
                    "awayTeam": match.get("awayTeam"),
                    "kickoffUtc": match.get("kickoffUtc"),
                    "provider": "The Odds API consensus",
                    "providerType": "online_bookmaker",
                    "marketKey": market_key,
                    "marketType": BOOKMAKER_MARKET_TYPES.get(str(market_key), str(market_key)),
                    "outcomes": outcomes,
                    "generatedAt": generated_at,
                    "source": "the-odds-api",
                    "sourceUrl": str(getattr(ctx.get("SETTINGS"), "the_odds_source_url", "") or "https://the-odds-api.com/"),
                    "bookmakerCount": book_count,
                    "bookmakers": bookmakers,
                    "eventId": event_id,
                    "commenceTime": best.get("commence_time"),
                    "confidence": min(99, best_score),
                }
            )
            event_rows += 1
        if event_rows:
            match["oddsLinked"] = True
    stats["matched"] = len({str(row.get("matchId")) for row in rows if row.get("matchId")})
    stats["snapshots"] = len(rows)
    return rows, "ok" if rows else state, stats
