from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


_repo_root = Path(__file__).resolve().parents[2]
_scripts_root = _repo_root / "scripts"
for candidate in (_repo_root, _scripts_root):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "vs",
    "will",
    "win",
    "yes",
    "no",
    "market",
    "polymarket",
}
DERIVATIVE_MARKET_TERMS = {
    "btts",
    "corner",
    "corners",
    "draw",
    "exact",
    "first",
    "goal",
    "goals",
    "half",
    "halftime",
    "ou",
    "over",
    "player",
    "points",
    "prop",
    "props",
    "score",
    "spread",
    "total",
    "under",
}
EVENT_SUFFIXES = (
    "-player-props",
    "-more-markets",
    "-exact-score",
    "-halftime-result",
    "-first-team-to-score",
    "-total-corners",
)
GENERIC_MATCHUP_CONTEXT_TOKENS = {
    "2026",
    "area",
    "city",
    "cup",
    "estadio",
    "fifa",
    "field",
    "group",
    "match",
    "round",
    "stadium",
    "world",
}

DB_FAILURE_COOLDOWN_SECONDS = 60
_db_disabled_until = 0.0


@dataclass(frozen=True)
class MarketLink:
    url: str
    title: str
    slug: str = ""
    event_slug: str = ""
    matched_by: str = ""
    score: float = 0.0


def telegram_market_linking_enabled() -> bool:
    raw = os.environ.get("POLYDATA_TELEGRAM_MARKET_LINKING_ENABLED", "true")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _db_available() -> bool:
    return time.monotonic() >= _db_disabled_until


def _mark_db_failure() -> None:
    global _db_disabled_until
    _db_disabled_until = time.monotonic() + DB_FAILURE_COOLDOWN_SECONDS


def _prepare_db_timeout() -> None:
    timeout = str(os.environ.get("POLYDATA_TELEGRAM_MARKET_LINK_DB_TIMEOUT_SECONDS", "2") or "2")
    os.environ.setdefault("PGCONNECT_TIMEOUT", timeout)
    os.environ.setdefault("POLYMARKET_MYSQL_CONNECT_TIMEOUT", timeout)


def market_url_from_row(row: Dict[str, Any]) -> str:
    slug = str(row.get("slug") or "").strip()
    event_slug = str(row.get("event_slug") or row.get("eventSlug") or "").strip()
    if event_slug and slug and event_slug != slug:
        return f"https://polymarket.com/event/{event_slug}/{slug}"
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    if slug:
        return f"https://polymarket.com/event/{slug}"
    return ""


def resolve_market_link(item: Dict[str, Any], *, title: str = "", extra_text: Iterable[Any] = ()) -> Optional[MarketLink]:
    if not telegram_market_linking_enabled() or not _db_available() or not isinstance(item, dict):
        return None
    exact = _resolve_exact(
        _first_text(item.get("localMarketId"), item.get("marketId")),
        _first_text(item.get("gammaMarketId"), item.get("gamma_market_id"), item.get("gammaId")),
        _first_text(item.get("conditionId"), item.get("condition_id")),
        _first_text(item.get("marketSlug"), item.get("slug")),
        _first_text(item.get("eventSlug"), item.get("event_slug")),
    )
    if exact is not None:
        return exact

    search_text = " ".join(
        part
        for part in (
            str(title or "").strip(),
            _first_text(item.get("marketTitle"), item.get("question"), item.get("eventTitle"), item.get("name"), item.get("label")),
            *(str(value or "").strip() for value in extra_text),
        )
        if part
    )
    return _search_market(search_text)


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _row_to_link(row: Dict[str, Any], *, matched_by: str, score: float = 1.0) -> Optional[MarketLink]:
    url = market_url_from_row(row)
    title = str(row.get("title") or row.get("event_title") or row.get("slug") or "").strip()
    if not url or not title:
        return None
    return MarketLink(
        url=url,
        title=title,
        slug=str(row.get("slug") or "").strip(),
        event_slug=str(row.get("event_slug") or "").strip(),
        matched_by=matched_by,
        score=score,
    )


@lru_cache(maxsize=2048)
def _resolve_exact(local_or_market_id: str, gamma_market_id: str, condition_id: str, slug: str, event_slug: str) -> Optional[MarketLink]:
    if not any((local_or_market_id, gamma_market_id, condition_id, slug, event_slug)):
        return None
    try:
        _prepare_db_timeout()
        from db import dict_from_row, get_connection
    except Exception:
        _mark_db_failure()
        return None

    clauses: list[str] = []
    params: list[Any] = []
    if local_or_market_id:
        if local_or_market_id.isdigit():
            clauses.append("id = ?")
            params.append(int(local_or_market_id))
        clauses.append("gamma_market_id = ?")
        params.append(local_or_market_id)
    if gamma_market_id and gamma_market_id != local_or_market_id:
        clauses.append("gamma_market_id = ?")
        params.append(gamma_market_id)
    if condition_id:
        clauses.append("lower(condition_id) = lower(?)")
        params.append(condition_id)
    if slug:
        clauses.append("slug = ?")
        params.append(slug)
    if event_slug:
        clauses.append("event_slug = ?")
        params.append(event_slug)
    if not clauses:
        return None

    sql = f"""
        SELECT id, gamma_market_id, event_slug, event_title, slug, condition_id, title, category, end_date, created_at
        FROM markets
        WHERE {' OR '.join(f'({clause})' for clause in clauses)}
        ORDER BY created_at DESC NULLS LAST, id DESC
        LIMIT 1
    """
    conn = None
    try:
        conn = get_connection(readonly=True)
        row = conn.execute(sql, tuple(params)).fetchone()
        return _row_to_link(dict_from_row(row), matched_by="exact") if row else None
    except Exception:
        _mark_db_failure()
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@lru_cache(maxsize=2048)
def _search_market(text: str) -> Optional[MarketLink]:
    raw_text = str(text or "").strip()
    query = _normalize_text(text)
    tokens = _tokens(query)
    if len(tokens) < 2:
        return None
    try:
        _prepare_db_timeout()
        from db import dict_from_row, get_connection
    except Exception:
        _mark_db_failure()
        return None

    terms = [token for token in tokens if not token.isdigit()][:8]
    if len(terms) < 2:
        return None
    clauses: list[str] = []
    params: list[Any] = []
    for term in terms:
        like = f"%{term}%"
        clauses.append("(lower(title) LIKE ? OR lower(event_title) LIKE ? OR lower(slug) LIKE ? OR lower(event_slug) LIKE ? OR lower(category) LIKE ?)")
        params.extend([like, like, like, like, like])
    sql = f"""
        SELECT id, gamma_market_id, event_slug, event_title, slug, condition_id, title, category, end_date, created_at
        FROM markets
        WHERE {' OR '.join(clauses)}
        ORDER BY
            CASE WHEN end_date IS NULL OR end_date > now() THEN 0 ELSE 1 END,
            created_at DESC NULLS LAST,
            id DESC
        LIMIT 1000
    """
    conn = None
    try:
        conn = get_connection(readonly=True)
        rows = [dict_from_row(row) for row in conn.execute(sql, tuple(params)).fetchall()]
        matchup_terms = _matchup_core_terms(raw_text)
        if len(matchup_terms) >= 2:
            matchup_clauses: list[str] = []
            matchup_params: list[Any] = []
            for term in matchup_terms[:6]:
                like = f"%{term}%"
                matchup_clauses.append("(lower(title) LIKE ? OR lower(event_title) LIKE ? OR lower(slug) LIKE ? OR lower(event_slug) LIKE ?)")
                matchup_params.extend([like, like, like, like])
            matchup_sql = f"""
                SELECT id, gamma_market_id, event_slug, event_title, slug, condition_id, title, category, end_date, created_at
                FROM markets
                WHERE {' AND '.join(matchup_clauses)}
                ORDER BY
                    CASE WHEN end_date IS NULL OR end_date > now() THEN 0 ELSE 1 END,
                    created_at DESC NULLS LAST,
                    id DESC
                LIMIT 300
            """
            seen_ids = {str(row.get("id") or "") for row in rows}
            for row in conn.execute(matchup_sql, tuple(matchup_params)).fetchall():
                data = dict_from_row(row)
                row_id = str(data.get("id") or "")
                if row_id and row_id in seen_ids:
                    continue
                rows.append(data)
                if row_id:
                    seen_ids.add(row_id)
    except Exception:
        _mark_db_failure()
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    best: tuple[float, Dict[str, Any]] | None = None
    for row in rows:
        score = _match_score(query, row)
        if best is None or score > best[0]:
            best = (score, row)
    if best is None:
        return None
    if _is_plain_matchup_query(raw_text):
        event_link = _infer_matchup_event_link(raw_text, rows)
        if event_link is not None:
            return event_link
    score, row = best
    min_score = float(os.environ.get("POLYDATA_TELEGRAM_MARKET_LINK_MIN_SCORE", "0.58") or 0.58)
    if score < min_score:
        return None
    return _row_to_link(row, matched_by="text", score=score)


def _normalize_text(value: str) -> str:
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = text.replace("o/u", " total ")
    text = text.replace("over/under", " total ")
    text = text.replace("1h", " half ")
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: str) -> list[str]:
    tokens = []
    for token in _normalize_text(value).split():
        if (len(token) < 2 and not token.isdigit()) or token in STOPWORDS:
            continue
        tokens.append(token)
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def _match_score(query: str, row: Dict[str, Any]) -> float:
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return 0.0
    title_event_text = _normalize_text(" ".join(str(row.get(key) or "") for key in ("title", "event_title")))
    slug_text = _normalize_text(" ".join(str(row.get(key) or "") for key in ("slug", "event_slug", "category")))
    market_text = " ".join(part for part in (title_event_text, slug_text) if part)
    market_tokens = set(_tokens(market_text))
    if not market_tokens:
        return 0.0
    overlap = query_tokens & market_tokens
    recall = len(overlap) / max(1, len(query_tokens))
    precision = len(overlap) / max(1, len(market_tokens))
    score = (recall * 0.75) + (precision * 0.25)
    if query and query in market_text:
        score += 0.35
    title = _normalize_text(str(row.get("title") or ""))
    if title and title in query:
        score += 0.25
    query_tokens_lower = set(_tokens(query))
    market_tokens_lower = set(_tokens(title_event_text))
    market_derivative_terms = DERIVATIVE_MARKET_TERMS & set(_tokens(market_text))
    query_derivative_terms = DERIVATIVE_MARKET_TERMS & query_tokens_lower
    if market_derivative_terms and not query_derivative_terms:
        score -= 0.35
    if market_derivative_terms and query_derivative_terms and not (market_derivative_terms & query_derivative_terms):
        score -= 0.45
    query_numbers = {token for token in query_tokens_lower if token.isdigit()}
    market_numbers = {token for token in set(_tokens(market_text)) if token.isdigit()}
    if query_numbers and not query_numbers.issubset(market_numbers):
        score -= 0.25
    if len(query_tokens_lower & market_tokens_lower) < min(2, len(query_tokens_lower)):
        score -= 0.2
    return max(0.0, min(score, 1.0))


def _is_plain_matchup_query(raw_text: str) -> bool:
    lowered = f" {str(raw_text or '').lower()} "
    has_matchup_separator = any(separator in lowered for separator in (" vs ", " vs. ", " @ ", " at "))
    if not has_matchup_separator:
        return False
    raw_derivative_patterns = ("o/u", "over/under", "spread", "total", "goal", "corner", "score", "prop", "first half", "1h", "halftime")
    if any(pattern in lowered for pattern in raw_derivative_patterns):
        return False
    return not bool(DERIVATIVE_MARKET_TERMS & set(_tokens(lowered)))


def _matchup_core_terms(raw_text: str) -> list[str]:
    parts = _matchup_sides(raw_text)
    if not parts:
        return []
    terms: list[str] = []
    for side in parts[:2]:
        terms.extend(side)
    return terms


def _matchup_sides(raw_text: str) -> list[list[str]]:
    text = str(raw_text or "").lower()
    text = re.split(r":|\|| - ", text, maxsplit=1)[0]
    if " vs. " in text:
        parts = text.split(" vs. ", 1)
    elif " vs " in text:
        parts = text.split(" vs ", 1)
    elif " @ " in text:
        parts = text.split(" @ ", 1)
    elif " at " in text:
        parts = text.split(" at ", 1)
    else:
        return []
    sides: list[list[str]] = []
    for part in parts[:2]:
        tokens = [
            token
            for token in _tokens(part)
            if token not in DERIVATIVE_MARKET_TERMS
            and token not in GENERIC_MATCHUP_CONTEXT_TOKENS
            and not token.isdigit()
        ]
        if tokens:
            sides.append(tokens[:3])
    return sides if len(sides) == 2 else []


def _infer_matchup_event_link(raw_text: str, rows: list[Dict[str, Any]]) -> Optional[MarketLink]:
    sides = _matchup_sides(raw_text)
    if len(sides) != 2:
        return None
    side_sets = [set(side) for side in sides]
    query_tokens = set().union(*side_sets)
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        base = _event_base_slug(row)
        if not base:
            continue
        row_tokens = set(_tokens(" ".join(str(row.get(key) or "") for key in ("title", "event_title", "slug", "event_slug"))))
        if not all(side and side.issubset(row_tokens) for side in side_sets):
            continue
        entry = grouped.setdefault(base, {"count": 0, "row": row})
        entry["count"] += 1
    if not grouped:
        return None
    base, entry = max(grouped.items(), key=lambda item: int(item[1]["count"]))
    row = entry["row"]
    title = str(row.get("event_title") or row.get("title") or raw_text).strip()
    for suffix in EVENT_SUFFIXES:
        title = title.replace(suffix.replace("-", " ").title(), "").strip()
    title = title.rstrip("-: ").strip()
    return MarketLink(
        url=f"https://polymarket.com/event/{base}",
        title=title or raw_text,
        event_slug=base,
        matched_by="matchup-event",
        score=0.95,
    )


def _event_base_slug(row: Dict[str, Any]) -> str:
    for value in (row.get("event_slug"), row.get("slug")):
        text = str(value or "").strip()
        if not text:
            continue
        for suffix in EVENT_SUFFIXES:
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        match = re.match(r"^([a-z0-9]+-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2})", text)
        if match:
            return match.group(1)
        if text:
            return text
    return ""
