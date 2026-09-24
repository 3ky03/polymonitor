from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from api.services.worldcup.common import (
    headers as _headers,
    safe_float as _safe_float,
    team_tokens as _team_tokens,
    tokenize as _tokenize,
    utc_now_iso as _utc_now_iso,
)
from api.services.worldcup.odds.probability import ordered_outcomes, snapshot_outcomes_from_probabilities

POLYMARKET_GAMMA_API_BASE = "https://gamma-api.polymarket.com"


def new_market_linker_stats(*, scan_limit: int, scheduled_count: int) -> Dict[str, Any]:
    return {
        "version": "worldcup-market-linker-v3",
        "scanLimit": scan_limit,
        "scheduledMatches": scheduled_count,
        "matchesScanned": 0,
        "queries": 0,
        "candidates": 0,
        "matched": 0,
        "sources": {},
        "rejections": {
            "no-candidates": 0,
            "missing-team": 0,
            "inactive": 0,
            "not-worldcup": 0,
            "not-match-market": 0,
            "duplicate": 0,
        },
    }


def _market_text(row: Dict[str, Any]) -> str:
    values = [
        row.get("title"),
        row.get("question"),
        row.get("marketTitle"),
        row.get("eventTitle"),
        row.get("slug"),
        row.get("eventSlug"),
        row.get("description"),
        row.get("groupItemTitle"),
        " ".join(str(value or "") for value in _safe_list(row.get("outcomes"))),
    ]
    for child in _safe_list(row.get("outcomeMarkets")):
        if isinstance(child, dict):
            values.extend([child.get("title"), child.get("slug"), child.get("eventTitle"), child.get("eventSlug")])
    return " ".join(str(value or "") for value in values).lower()


def _market_primary_text(row: Dict[str, Any]) -> str:
    values = [
        row.get("title"),
        row.get("question"),
        row.get("marketTitle"),
        row.get("slug"),
    ]
    return " ".join(str(value or "") for value in values).lower()


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _safe_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _polymarket_url(row: Dict[str, Any]) -> str:
    for key in ("marketUrl", "eventUrl", "url"):
        value = str(row.get(key) or "").strip()
        if value.startswith("http"):
            return value
    event_slug = str(row.get("eventSlug") or "").strip()
    slug = str(row.get("slug") or row.get("eventSlug") or "").strip()
    if event_slug and slug and slug != event_slug:
        return f"https://polymarket.com/event/{event_slug}/{slug}"
    if slug:
        return f"https://polymarket.com/event/{slug}"
    title = str(row.get("title") or row.get("question") or "").strip()
    return f"https://polymarket.com/search?query={quote_plus(title)}" if title else ""


def _serving_market_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = [item for item in _safe_json_list(row.get("outcomes")) if isinstance(item, dict)]
    top_outcomes = [item for item in _safe_json_list(row.get("top_outcomes")) if isinstance(item, dict)]
    priced = top_outcomes or outcomes
    labels: List[str] = []
    prices: List[Any] = []
    token_ids: List[Any] = []
    for outcome in priced:
        label = outcome.get("label") or outcome.get("title") or outcome.get("outcomeKey") or outcome.get("name")
        price = outcome.get("yesPrice")
        if label in (None, "") or price in (None, ""):
            continue
        labels.append(str(label))
        prices.append(price)
        token_ids.append(outcome.get("yesTokenId") or outcome.get("tokenId") or "")
    return {
        "title": row.get("title"),
        "question": row.get("title"),
        "marketTitle": row.get("title"),
        "eventTitle": row.get("event_title"),
        "eventSlug": row.get("event_slug"),
        "eventId": row.get("event_id"),
        "slug": row.get("event_slug"),
        "gammaMarketId": row.get("default_gamma_market_id"),
        "conditionId": row.get("default_condition_id"),
        "outcomes": labels,
        "outcomePrices": prices,
        "clobTokenIds": token_ids,
        "volume24hr": row.get("volume_24h"),
        "volume24h": row.get("volume_24h"),
        "active": not bool(row.get("is_trading_closed")),
        "closed": bool(row.get("is_trading_closed")),
        "source": "local-db",
    }


def _market_table_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    clob_token_ids = _safe_json_list(row.get("clob_token_ids"))
    yes_token_id = str(row.get("yes_token_id") or (clob_token_ids[0] if clob_token_ids else "") or "").strip()
    no_token_id = str(row.get("no_token_id") or (clob_token_ids[1] if len(clob_token_ids) > 1 else "") or "").strip()
    latest_yes = row.get("latest_yes_price")
    latest_price = row.get("latest_price")
    return {
        "id": row.get("id"),
        "localMarketId": row.get("id"),
        "title": row.get("title"),
        "question": row.get("title"),
        "marketTitle": row.get("title"),
        "eventTitle": row.get("event_title"),
        "eventSlug": row.get("event_slug"),
        "eventId": row.get("event_id"),
        "slug": row.get("slug"),
        "gammaMarketId": row.get("gamma_market_id"),
        "conditionId": row.get("condition_id"),
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "clobTokenIds": clob_token_ids or [token for token in (yes_token_id, no_token_id) if token],
        "latestPrice": latest_price,
        "latestYesPrice": latest_yes if latest_yes not in (None, "") else latest_price,
        "latestNoPrice": row.get("latest_no_price"),
        "outcomes": ["YES", "NO"],
        "outcomePrices": [latest_yes if latest_yes not in (None, "") else latest_price]
        if (latest_yes not in (None, "") or latest_price not in (None, ""))
        else [],
        "volume24hr": row.get("volume_24h"),
        "volume24h": row.get("volume_24h"),
        "active": not bool(row.get("is_trading_closed")),
        "closed": bool(row.get("is_trading_closed")),
        "source": "local-db-market",
    }


_WORLDCUP_MARKET_SLUG_RE = re.compile(r"^(fifwc-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2})-(.+)$")


def _worldcup_market_slug_base(slug: Any) -> str:
    match = _WORLDCUP_MARKET_SLUG_RE.match(str(slug or "").strip().lower())
    return match.group(1) if match else ""


def _moneyline_bundle_title(children: List[Dict[str, Any]], slug_base: str) -> str:
    for child in children:
        title = str(child.get("title") or "")
        draw_match = re.search(r"Will\s+(.+?)\s+vs\.?\s+(.+?)\s+end in a draw\?", title, flags=re.IGNORECASE)
        if draw_match:
            return f"{draw_match.group(1).strip()} vs. {draw_match.group(2).strip()} - Match Winner"
    return f"{slug_base} - Match Winner"


def _moneyline_bundle_candidate(slug_base: str, children: List[Dict[str, Any]]) -> Dict[str, Any]:
    children = sorted(children, key=lambda row: str(row.get("slug") or ""))
    return {
        "id": slug_base,
        "title": _moneyline_bundle_title(children, slug_base),
        "question": _moneyline_bundle_title(children, slug_base),
        "marketTitle": _moneyline_bundle_title(children, slug_base),
        "eventTitle": _moneyline_bundle_title(children, slug_base),
        "eventSlug": slug_base,
        "slug": slug_base,
        "outcomes": [],
        "outcomePrices": [],
        "outcomeMarkets": children,
        "volume24hr": sum(_safe_float(child.get("volume24h") or child.get("volume24hr")) or 0 for child in children),
        "volume24h": sum(_safe_float(child.get("volume24h") or child.get("volume24hr")) or 0 for child in children),
        "active": any(child.get("active") is not False for child in children),
        "closed": all(bool(child.get("closed")) for child in children),
        "source": "local-db-moneyline",
    }


_TEAM_SLUG_CODES = {
    "argentina": "arg",
    "australia": "aus",
    "belgium": "bel",
    "bosnia & herzegovina": "bih",
    "bosnia and herzegovina": "bih",
    "brazil": "bra",
    "canada": "can",
    "colombia": "col",
    "croatia": "cro",
    "curaçao": "cur",
    "curacao": "cur",
    "czech republic": "cze",
    "denmark": "den",
    "ecuador": "ecu",
    "england": "eng",
    "france": "fra",
    "germany": "ger",
    "ghana": "gha",
    "haiti": "hai",
    "iran": "irn",
    "italy": "ita",
    "ivory coast": "civ",
    "japan": "jpn",
    "mexico": "mex",
    "morocco": "mar",
    "netherlands": "nld",
    "new zealand": "nzl",
    "nigeria": "nga",
    "norway": "nor",
    "paraguay": "par",
    "portugal": "por",
    "qatar": "qat",
    "scotland": "sco",
    "senegal": "sen",
    "south africa": "rsa",
    "south korea": "kor",
    "spain": "esp",
    "sweden": "swe",
    "switzerland": "sui",
    "tunisia": "tun",
    "turkey": "tur",
    "usa": "usa",
    "united states": "usa",
    "uruguay": "uru",
}


def _team_slug_code(team: Any) -> str:
    text = str(team or "").strip().lower().replace("&", "and")
    return _TEAM_SLUG_CODES.get(text, re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:3])


def _worldcup_match_slug_base_from_match(match: Dict[str, Any]) -> str:
    home = _team_slug_code(match.get("homeTeam"))
    away = _team_slug_code(match.get("awayTeam"))
    date = str(match.get("kickoffUtc") or "")[:10]
    if not home or not away or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return ""
    return f"fifwc-{home}-{away}-{date}"


def _candidate_event_payload(item: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(event, dict) and event:
        return event
    events = item.get("events")
    if isinstance(events, list):
        for row in events:
            if isinstance(row, dict):
                return row
    return {}


def _market_is_inactive(row: Dict[str, Any]) -> bool:
    if row.get("active") is False:
        return True
    if row.get("closed") is True or row.get("archived") is True:
        return True
    return False


def _canonical_outcome_label(match: Dict[str, Any], label: Any, *, allow_yes_no: bool = False) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    lower = text.lower()
    home = str(match.get("homeTeam") or "").strip()
    away = str(match.get("awayTeam") or "").strip()
    if lower in {"draw", "tie", "x"} or "draw" in lower:
        return "Draw"
    if allow_yes_no and lower == "yes":
        return text
    if allow_yes_no and lower == "no":
        return text
    home_tokens = _team_tokens(home)
    away_tokens = _team_tokens(away)
    label_tokens = _team_tokens(text) | set(_tokenize(text))

    def _score(team_name: str, team_token_set: set[str]) -> int:
        team_lower = str(team_name or "").lower()
        if not team_lower:
            return 0
        if lower == team_lower:
            return 100
        score = len(team_token_set & label_tokens)
        if team_lower in lower or lower in team_lower:
            score += 4
        return score

    home_score = _score(home, home_tokens)
    away_score = _score(away, away_tokens)
    if home_score > away_score and home_score > 0:
        return home
    if away_score > home_score and away_score > 0:
        return away
    return text


def _match_outcome_hint(match: Dict[str, Any], row: Dict[str, Any]) -> str:
    candidates = [
        row.get("groupItemTitle"),
        row.get("outcome"),
        row.get("name"),
        row.get("title"),
        row.get("question"),
        row.get("marketTitle"),
    ]
    for value in candidates:
        label = _canonical_outcome_label(match, value)
        if label in {str(match.get("homeTeam") or ""), str(match.get("awayTeam") or ""), "Draw"}:
            return label
    return ""


def _candidate_outcome_text(row: Dict[str, Any]) -> str:
    values: List[Any] = [
        row.get("groupItemTitle"),
        row.get("outcome"),
        row.get("name"),
    ]
    values.extend(_safe_list(row.get("outcomes")))
    return " ".join(str(value or "") for value in values).lower()


def _worldcup_market_reject_reason(match: Dict[str, Any], row: Dict[str, Any]) -> str:
    if _market_is_inactive(row):
        return "inactive"
    text = _market_text(row)
    primary_text = _market_primary_text(row)
    event_match_text = " ".join(
        str(row.get(key) or "") for key in ("title", "question", "marketTitle", "slug", "eventTitle", "eventSlug")
    ).lower()
    tokens = set(_tokenize(primary_text))
    event_tokens = set(_tokenize(event_match_text))
    outcome_text = _candidate_outcome_text(row)
    outcome_tokens = set(_tokenize(outcome_text))
    home_tokens = _team_tokens(match.get("homeTeam"))
    away_tokens = _team_tokens(match.get("awayTeam"))
    home_text = str(match.get("homeTeam") or "").lower()
    away_text = str(match.get("awayTeam") or "").lower()
    primary_home_hit = bool(home_tokens & tokens) or home_text in primary_text
    primary_away_hit = bool(away_tokens & tokens) or away_text in primary_text
    event_home_hit = bool(home_tokens & event_tokens) or home_text in event_match_text
    event_away_hit = bool(away_tokens & event_tokens) or away_text in event_match_text
    outcome_home_hit = bool(home_tokens & outcome_tokens) or home_text in outcome_text
    outcome_away_hit = bool(away_tokens & outcome_tokens) or away_text in outcome_text
    primary_has_match = primary_home_hit and primary_away_hit
    event_has_match = event_home_hit and event_away_hit
    outcome_hint = _match_outcome_hint(match, row)
    outcome_has_match = (outcome_home_hit and outcome_away_hit) or bool(outcome_hint)
    if _safe_list(row.get("outcomeMarkets")):
        child_hints = {
            _match_outcome_hint(match, child)
            for child in _safe_list(row.get("outcomeMarkets"))
            if isinstance(child, dict)
        }
        if {home_text and str(match.get("homeTeam") or ""), "Draw", away_text and str(match.get("awayTeam") or "")} - {""} <= child_hints:
            outcome_has_match = True
            primary_has_match = True
    if not (primary_has_match or (event_has_match and outcome_has_match) or (outcome_home_hit and outcome_away_hit)):
        return "missing-team"
    worldcup_hit = any(term in text for term in ("world cup", "fifa", "fifwc", "wc2026", "2026"))
    if not worldcup_hit:
        return "not-worldcup"
    prop_terms = (
        "spread",
        " o/u ",
        "over/under",
        "total",
        "corners",
        "first team to score",
        "first-to-score",
        "exact score",
        "halftime",
        "player props",
        "goals-",
        " goals",
        "both teams to score",
        "btts",
        "cards",
    )
    outright_terms = (
        "group a last place",
        "group a second place",
        "group a winner",
        "highest-scoring team",
        "highest scoring team",
        "fair play award",
        "award winner",
    )
    market_terms = ("winner", "winning", "win", "match result", "moneyline", "draw", "beat", "advance")
    if any(term in primary_text for term in outright_terms):
        return "not-match-market"
    if not _safe_list(row.get("outcomeMarkets")) and any(term in f" {primary_text} {text} " for term in prop_terms):
        if not any(term in primary_text for term in market_terms):
            return "not-match-market"
    if event_has_match and not outcome_has_match:
        if not any(term in primary_text for term in market_terms):
            return "not-match-market"
    return ""


def _worldcup_market_score(match: Dict[str, Any], row: Dict[str, Any]) -> int:
    if _worldcup_market_reject_reason(match, row):
        return 0
    text = _market_text(row)
    score = 80
    child_hints = {
        _match_outcome_hint(match, child)
        for child in _safe_list(row.get("outcomeMarkets"))
        if isinstance(child, dict)
    }
    expected = {str(match.get("homeTeam") or ""), "Draw", str(match.get("awayTeam") or "")}
    if expected <= child_hints:
        score += 60
    if "draw" in text or "winner" in text or "moneyline" in text:
        score += 8
    if _match_outcome_hint(match, row):
        score += 6
    if len(_extract_probability_rows(match, row)) >= 2:
        score += 10
    kickoff = str(match.get("kickoffUtc") or "")[:10]
    if kickoff and kickoff in text:
        score += 5
    return score


def _extract_probability_rows(match: Dict[str, Any], row: Dict[str, Any]) -> List[Dict[str, Any]]:
    labels = _safe_list(row.get("outcomes"))
    prices = _safe_list(row.get("outcomePrices"))
    if not prices:
        latest = row.get("latestYesPrice") if row.get("latestYesPrice") not in (None, "") else row.get("latestPrice")
        if latest not in (None, ""):
            prices = [latest]
            labels = labels or ["YES"]
    hint = _match_outcome_hint(match, row)
    if hint and prices:
        return [{"outcome": hint, "price": prices[0], "marketUrl": _polymarket_url(row)}]
    rows: List[Dict[str, Any]] = []
    for index, label in enumerate(labels):
        if index >= len(prices):
            continue
        outcome = _canonical_outcome_label(match, label)
        if outcome not in {str(match.get("homeTeam") or ""), str(match.get("awayTeam") or ""), "Draw"}:
            continue
        rows.append({"outcome": outcome, "price": prices[index], "marketUrl": _polymarket_url(row)})
    return rows


def _ordered_probability_rows(match: Dict[str, Any], probabilities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_outcome = {
        str(row.get("outcome") or row.get("name") or "").lower(): row
        for row in probabilities
        if isinstance(row, dict)
    }
    ordered: List[Dict[str, Any]] = []
    for label in (str(match.get("homeTeam") or ""), "Draw", str(match.get("awayTeam") or "")):
        row = by_outcome.pop(label.lower(), None)
        if row:
            ordered.append(row)
    ordered.extend(by_outcome.values())
    return ordered


def _bundle_probability_rows(ctx: Dict[str, Any], match: Dict[str, Any], row: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_outcome: Dict[str, Dict[str, Any]] = {}
    for child in _safe_list(row.get("outcomeMarkets")):
        if not isinstance(child, dict):
            continue
        child_rows = _extract_probability_rows(match, child)
        if not child_rows and str(child.get("slug") or "").endswith("-draw") and _safe_list(child.get("outcomePrices")):
            child_rows = [{"outcome": "Draw", "price": _safe_list(child.get("outcomePrices"))[0], "marketUrl": _polymarket_url(child)}]
        for probability in child_rows:
            outcome = str(probability.get("outcome") or "")
            if outcome and outcome not in by_outcome:
                by_outcome[outcome] = probability
    return _ordered_probability_rows(match, list(by_outcome.values()))


def _best_probability_rows(match: Dict[str, Any], candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_outcome: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        for probability in _extract_probability_rows(match, candidate):
            outcome = str(probability.get("outcome") or "")
            if outcome and outcome not in by_outcome:
                by_outcome[outcome] = probability
    rows = _ordered_probability_rows(match, list(by_outcome.values()))
    return rows if len(rows) >= 2 else []


def _clob_snapshot(ctx: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    latest_yes = row.get("latestYesPrice") if row.get("latestYesPrice") not in (None, "") else row.get("latestPrice")
    return {
        "latestYesPrice": latest_yes,
        "latestNoPrice": row.get("latestNoPrice"),
        "clobTokenIds": row.get("clobTokenIds"),
    }


def _normalize_market_candidate(item: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    event = _candidate_event_payload(item, event)
    title = str(item.get("question") or item.get("title") or event.get("title") or "").strip()
    slug = str(item.get("slug") or event.get("slug") or "").strip()
    event_slug = str(event.get("slug") or item.get("eventSlug") or "").strip()
    token_ids = _safe_list(item.get("clobTokenIds") or item.get("clob_token_ids"))
    return {
        **item,
        "title": title,
        "marketTitle": title,
        "slug": slug,
        "eventTitle": event.get("title") or item.get("eventTitle"),
        "eventSlug": event_slug,
        "eventId": event.get("id") or item.get("eventId"),
        "gammaMarketId": item.get("id") or item.get("gamma_market_id"),
        "conditionId": item.get("conditionId") or item.get("condition_id"),
        "groupItemTitle": item.get("groupItemTitle"),
        "clobTokenIds": token_ids,
        "yes_token_id": item.get("yes_token_id") or item.get("yesTokenId") or (token_ids[0] if token_ids else ""),
        "no_token_id": item.get("no_token_id") or item.get("noTokenId") or (token_ids[1] if len(token_ids) > 1 else ""),
        "marketUrl": _polymarket_url({**event, **item, "slug": slug or event_slug, "eventSlug": event_slug}),
        "source": "gamma",
    }


def _gamma_moneyline_bundle_search(ctx: Dict[str, Any], match: Dict[str, Any]) -> List[Dict[str, Any]]:
    getter = ctx.get("http_json_get")
    if not callable(getter):
        return []
    slug_base = _worldcup_match_slug_base_from_match(match)
    if not slug_base:
        return []
    settings = ctx.get("SETTINGS")
    base_url = str(getattr(settings, "gamma_api_base", "") or POLYMARKET_GAMMA_API_BASE).rstrip("/")
    suffixes = [_team_slug_code(match.get("homeTeam")), "draw", _team_slug_code(match.get("awayTeam"))]
    children: List[Dict[str, Any]] = []
    for suffix in suffixes:
        if not suffix:
            continue
        try:
            payload = getter(f"{base_url}/markets", params={"slug": f"{slug_base}-{suffix}"}, timeout=8, headers=_headers())
        except Exception:
            continue
        items = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                children.append(_normalize_market_candidate(item))
                break
    if len(children) < 2:
        return []
    return [{**_moneyline_bundle_candidate(slug_base, children), "source": "gamma-moneyline"}]


def _gamma_search(ctx: Dict[str, Any], query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    getter = ctx.get("http_json_get")
    if not callable(getter):
        return []
    settings = ctx.get("SETTINGS")
    base_url = str(getattr(settings, "gamma_api_base", "") or POLYMARKET_GAMMA_API_BASE).rstrip("/")
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for path in ("/markets", "/events"):
        try:
            payload = getter(f"{base_url}{path}", params={"q": query, "limit": limit}, timeout=8, headers=_headers())
        except Exception:
            continue
        items = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            nested_markets = item.get("markets") if isinstance(item.get("markets"), list) else []
            candidates = nested_markets if nested_markets else [item]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                normalized = _normalize_market_candidate(candidate, item if nested_markets else None)
                key = str(normalized.get("slug") or normalized.get("marketTitle") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                rows.append(normalized)
                if len(rows) >= limit:
                    return rows
    return rows


def _search_market_sources(ctx: Dict[str, Any], query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    rows = _local_market_search(ctx, query, limit=limit)
    if rows:
        return rows[:limit]
    return _gamma_search(ctx, query, limit=limit)


def _gamma_scan_active(ctx: Dict[str, Any], *, limit: int = 120) -> List[Dict[str, Any]]:
    getter = ctx.get("http_json_get")
    if not callable(getter):
        return []
    settings = ctx.get("SETTINGS")
    base_url = str(getattr(settings, "gamma_api_base", "") or POLYMARKET_GAMMA_API_BASE).rstrip("/")
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    param_sets = [
        {"active": "true", "closed": "false", "limit": limit, "offset": 0, "order": "id", "ascending": "false"},
        {"active": "true", "closed": "false", "limit": limit, "offset": 0, "order": "volume24hr", "ascending": "false"},
        {"active": "true", "closed": "false", "limit": limit, "offset": 0, "q": "world cup"},
        {"active": "true", "closed": "false", "limit": limit, "offset": 0, "q": "fifa"},
        {"active": "true", "closed": "false", "limit": limit, "offset": 0, "q": "soccer"},
        {"active": "true", "closed": "false", "limit": limit, "offset": 0, "tag_slug": "soccer"},
        {"active": "true", "closed": "false", "limit": limit, "offset": 0, "tag_slug": "sports"},
    ]
    for path in ("/events", "/markets"):
        for params in param_sets:
            try:
                payload = getter(f"{base_url}{path}", params=params, timeout=10, headers=_headers())
            except Exception:
                continue
            items = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                nested_markets = item.get("markets") if isinstance(item.get("markets"), list) else []
                candidates = nested_markets if nested_markets else [item]
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    normalized = _normalize_market_candidate(candidate, item if nested_markets else None)
                    key = str(normalized.get("slug") or normalized.get("gammaMarketId") or normalized.get("marketTitle") or "")
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    rows.append({**normalized, "source": "gamma-scan"})
    return rows


def _significant_market_search_tokens(query: Any) -> List[str]:
    stop = {"and", "the", "vs", "v", "world", "cup", "fifa", "soccer", "football", "match", "market"}
    tokens: List[str] = []
    for token in _tokenize(query):
        if token in stop or len(token) < 2:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens[:8]


def _local_event_serving_search(ctx: Dict[str, Any], query: str, *, limit: int) -> List[Dict[str, Any]]:
    query_all = ctx.get("query_all")
    if not callable(query_all):
        return []
    tokens = _significant_market_search_tokens(query)
    search_text = """
        (
          COALESCE(title, '') || ' ' ||
          COALESCE(event_slug, '') || ' ' ||
          COALESCE(event_title, '') || ' ' ||
          COALESCE(category, '') || ' ' ||
          COALESCE(CAST(tags AS TEXT), '') || ' ' ||
          COALESCE(CAST(outcomes AS TEXT), '') || ' ' ||
          COALESCE(CAST(top_outcomes AS TEXT), '')
        )
    """
    if tokens:
        token_filter = " AND ".join(f"{search_text} ILIKE ?" for _ in tokens)
        params: List[Any] = [f"%{token}%" for token in tokens]
    else:
        token_filter = f"{search_text} ILIKE ?"
        params = [f"%{query}%"]
    try:
        rows = query_all(
            f"""
            SELECT
              event_id, event_slug, event_title, title, category, tags,
              volume_24h, outcome_count, default_market_id,
              default_condition_id, default_gamma_market_id,
              top_outcomes, outcomes, completion_status,
              is_trading_closed, active_rank, updated_at
            FROM event_market_serving
            WHERE outcome_count > 0
              AND is_trading_closed = FALSE
              AND completion_status NOT IN ('SETTLED', 'CANCELLED', 'CLOSED_UNRESOLVED')
              AND ({token_filter})
            ORDER BY active_rank DESC NULLS LAST, volume_24h DESC NULLS LAST, updated_at DESC NULLS LAST
            LIMIT ?
            """,
            [*params, int(max(limit, 16))],
        )
    except Exception:
        return []
    return [{**_serving_market_candidate(row), "source": "local-db"} for row in rows if isinstance(row, dict)]


def _local_moneyline_bundle_search(ctx: Dict[str, Any], query: str, *, limit: int) -> List[Dict[str, Any]]:
    query_all = ctx.get("query_all")
    if not callable(query_all):
        return []
    tokens = _significant_market_search_tokens(query)
    if not tokens:
        return []
    search_text = """
        (
          COALESCE(m.title, '') || ' ' ||
          COALESCE(m.slug, '') || ' ' ||
          COALESCE(m.event_slug, '') || ' ' ||
          COALESCE(m.event_title, '') || ' ' ||
          COALESCE(CAST(m.tags AS TEXT), '')
        )
    """
    token_filter = " OR ".join(f"{search_text} ILIKE ?" for _ in tokens)
    params: List[Any] = [f"%{token}%" for token in tokens]
    try:
        rows = query_all(
            f"""
            SELECT
              m.id, m.gamma_market_id, m.slug, m.condition_id, m.yes_token_id,
              m.no_token_id, m.clob_token_ids, m.title, m.category, m.tags,
              m.end_date, m.event_id, m.event_slug, m.event_title,
              p.latest_price, p.latest_yes_price, p.latest_no_price,
              s.completion_status, s.is_trading_closed
            FROM markets m
            LEFT JOIN market_latest_prices p ON p.market_id = m.id
            LEFT JOIN market_status_snapshot s ON s.market_id = m.id
            WHERE COALESCE(s.is_trading_closed, FALSE) = FALSE
              AND COALESCE(s.completion_status, 'OPEN') NOT IN ('SETTLED', 'CANCELLED', 'CLOSED_UNRESOLVED')
              AND (m.slug ILIKE ? OR m.slug ILIKE ? OR COALESCE(m.event_slug, '') ILIKE ?)
              AND ({token_filter})
            ORDER BY m.end_date ASC NULLS LAST, m.id DESC
            LIMIT ?
            """,
            ["%fifwc%", "%wc2026%", "%fifwc%", *params, int(max(limit * 20, 120))],
        )
    except Exception:
        return []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = _market_table_candidate(row)
        slug_base = _worldcup_market_slug_base(candidate.get("slug"))
        if not slug_base:
            continue
        slug = str(candidate.get("slug") or "").lower()
        title = str(candidate.get("title") or "").lower()
        if not ("end in a draw" in title or re.match(r"^will .+ win on \d{4}-\d{2}-\d{2}\?$", title)):
            continue
        groups.setdefault(slug_base, []).append(candidate)
    bundles: List[Dict[str, Any]] = []
    for slug_base, children in groups.items():
        if len(children) < 2:
            continue
        bundles.append(_moneyline_bundle_candidate(slug_base, children))
    return bundles[:limit]


def _local_market_search(ctx: Dict[str, Any], query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    moneyline_rows = _local_moneyline_bundle_search(ctx, query, limit=limit)
    if moneyline_rows:
        return moneyline_rows[:limit]
    local_rows = _local_event_serving_search(ctx, query, limit=limit)
    if local_rows:
        seen: set[str] = set()
        deduped: List[Dict[str, Any]] = []
        for row in local_rows:
            key = str(row.get("slug") or row.get("id") or row.get("marketTitle") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(row)
            if len(deduped) >= limit:
                return deduped

    getter = ctx.get("get_markets_payload")
    if not callable(getter):
        return []
    try:
        payload = getter(status="active", query=query, page=1, page_size=limit)
    except Exception:
        return []
    items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else []
    rows: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            rows.append({**_normalize_market_candidate(item), "source": "local-markets"})
    return rows[:limit]


def link_worldcup_markets(
    ctx: Dict[str, Any],
    matches: List[Dict[str, Any]],
    *,
    settings_scan_limit: int,
) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    if not matches:
        return [], "empty", new_market_linker_stats(scan_limit=0, scheduled_count=0)
    scan_limit = min(settings_scan_limit, 12)
    scheduled = [match for match in matches if str(match.get("status") or "") != "finished"][: max(1, scan_limit)]
    stats = new_market_linker_stats(scan_limit=scan_limit, scheduled_count=len(scheduled))
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    scan_candidates: List[Dict[str, Any]] = []
    for match in scheduled:
        stats["matchesScanned"] += 1
        home = str(match.get("homeTeam") or "").strip()
        away = str(match.get("awayTeam") or "").strip()
        if not home or not away or home == "TBD" or away == "TBD":
            continue
        queries = [
            f"{home} {away} world cup",
            f"{home} vs {away} fifa world cup",
            f"{away} {home} world cup",
        ]
        best: Optional[Dict[str, Any]] = None
        best_score = 0
        accepted: List[Dict[str, Any]] = []
        match_candidates = 0
        direct_candidates = _gamma_moneyline_bundle_search(ctx, match)
        for query_index, query in enumerate(queries):
            stats["queries"] += 1
            candidates = _search_market_sources(ctx, query, limit=8)
            if query_index == 0 and direct_candidates:
                candidates = [*direct_candidates, *candidates]
            if query_index == 0 and scan_candidates:
                candidates = [*candidates, *scan_candidates]
            match_candidates += len(candidates)
            stats["candidates"] += len(candidates)
            for candidate in candidates:
                source = str(candidate.get("source") or "unknown")
                stats["sources"][source] = int(stats["sources"].get(source) or 0) + 1
                reason = _worldcup_market_reject_reason(match, candidate)
                if reason:
                    stats["rejections"][reason] = int(stats["rejections"].get(reason) or 0) + 1
                    continue
                score = _worldcup_market_score(match, candidate)
                accepted.append(candidate)
                if score > best_score:
                    best = candidate
                    best_score = score
            if best_score >= 140 and str((best or {}).get("source") or "").startswith("local-db-moneyline"):
                break
        if not best or best_score <= 0:
            if match_candidates <= 0:
                stats["rejections"]["no-candidates"] = int(stats["rejections"].get("no-candidates") or 0) + 1
            continue
        key = str(best.get("slug") or best.get("marketTitle") or match.get("id"))
        if key in seen:
            stats["rejections"]["duplicate"] = int(stats["rejections"].get("duplicate") or 0) + 1
            continue
        seen.add(key)
        outcomes = _safe_list(best.get("outcomes"))
        outcome_prices = _safe_list(best.get("outcomePrices"))
        clob = {}
        if _safe_list(best.get("outcomeMarkets")):
            probabilities = _bundle_probability_rows(ctx, match, best)
            outcomes = [row.get("outcome") for row in probabilities]
            outcome_prices = [row.get("price") for row in probabilities]
        else:
            clob = _clob_snapshot(ctx, best)
            if not outcome_prices and clob.get("latestYesPrice") is not None:
                outcome_prices = [clob.get("latestYesPrice")]
                outcomes = outcomes or ["YES"]
            probabilities = _best_probability_rows(match, accepted) or [
                {"outcome": str(label), "price": price, "marketUrl": _polymarket_url(best)}
                for label, price in zip(outcomes, outcome_prices)
            ]
        snapshot_outcomes = ordered_outcomes(match, snapshot_outcomes_from_probabilities(probabilities))
        market_url = _polymarket_url(best)
        if len(probabilities) > 1 and str(best.get("eventSlug") or "").strip():
            market_url = f"https://polymarket.com/event/{str(best.get('eventSlug')).strip()}"
        rows.append(
            {
                "id": f"{match.get('id')}:polymarket",
                "matchId": match.get("id"),
                "homeTeam": home,
                "awayTeam": away,
                "kickoffUtc": match.get("kickoffUtc"),
                "title": best.get("marketTitle") or best.get("title"),
                "marketTitle": best.get("marketTitle") or best.get("title"),
                "slug": best.get("slug"),
                "eventTitle": best.get("eventTitle"),
                "eventSlug": best.get("eventSlug"),
                "eventId": best.get("eventId"),
                "gammaMarketId": best.get("gammaMarketId"),
                "conditionId": best.get("conditionId"),
                "marketUrl": market_url,
                "tradeUrl": market_url,
                "outcomes": snapshot_outcomes,
                "rawOutcomes": outcomes,
                "outcomePrices": outcome_prices,
                "probabilities": probabilities,
                "clobTokenIds": best.get("clobTokenIds"),
                "clob": clob or None,
                "source": best.get("source") or "polymarket-gamma",
                "provider": "Polymarket local/Gamma/CLOB",
                "providerType": "prediction_market",
                "marketType": "moneyline",
                "generatedAt": _utc_now_iso(),
                "confidence": min(99, best_score),
            }
        )
        stats["matched"] += 1
        match["marketLinked"] = True
        match["oddsLinked"] = bool(outcome_prices)
    return rows, "ok" if rows else "empty", stats
