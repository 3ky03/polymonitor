#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sync and inspect the NBA-only Polymarket market catalog."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from db import add_db_cli_args, configure_db_from_args, describe_db_target, get_connection, init_schema
from market.market_discovery import (
    GAMMA_API_BASE,
    _attach_event_meta_to_market,
    batch_upsert_markets,
    normalize_market_from_gamma,
)

from NBA.common import (
    DEFAULT_DATA_ROOT,
    MARKET_CATALOG_COLUMNS,
    TOKEN_CATALOG_COLUMNS,
    ensure_data_root,
    iso_now,
    load_catalog_state,
    load_market_catalog_rows,
    parse_json_list,
    safe_json_dumps,
    write_catalog_artifacts,
)

GAMMA_SPORTS_URL = f"{GAMMA_API_BASE}/sports"
GAMMA_TAGS_URL = f"{GAMMA_API_BASE}/tags"
GAMMA_EVENTS_URL = f"{GAMMA_API_BASE}/events"
NBA_TAG_SLUG = "nba"
HTTP_TIMEOUT_SECONDS = 30
MATCHUP_PATTERN = re.compile(r"\s(?:vs\.?|at|@)\s", re.IGNORECASE)
MATCHUP_EXTRACT_PATTERN = re.compile(
    r"(?P<team_a>[A-Za-z0-9][A-Za-z0-9.'& -]*?)\s+(?:vs\.?|at|@)\s+(?P<team_b>[A-Za-z0-9][A-Za-z0-9.'& -]*)",
    re.IGNORECASE,
)
PLAYOFF_KEYWORDS = ("playoff", "playoffs")
GENERIC_TAG_SLUGS = {
    "sports",
    "nba",
    "basketball",
    "games",
    "nba-playoffs",
    "2026-nba-playoffs",
    "overunder",
}


def _build_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=4,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "polyData-nba-market-catalog/1.0",
        }
    )
    return session


def _fetch_json(session: requests.Session, url: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
    response = session.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _parse_tag_ids(value: Any) -> List[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def discover_nba_filter(session: requests.Session) -> Dict[str, Any]:
    sports = _fetch_json(session, GAMMA_SPORTS_URL)
    if not isinstance(sports, list):
        raise RuntimeError("Unexpected /sports response while discovering NBA metadata")
    nba_sport = next((item for item in sports if str(item.get("sport") or "").strip().lower() == "nba"), None)
    if not nba_sport:
        raise RuntimeError("Could not find sport=nba in Polymarket /sports metadata")

    sport_tag_ids = _parse_tag_ids(nba_sport.get("tags"))
    tags = _fetch_json(session, GAMMA_TAGS_URL)
    tag_lookup: Dict[str, Dict[str, Any]] = {}
    if isinstance(tags, list):
        for item in tags:
            slug = str(item.get("slug") or "").strip().lower()
            if slug:
                tag_lookup[slug] = item

    primary = tag_lookup.get(NBA_TAG_SLUG)
    if primary and sport_tag_ids and str(primary.get("id")) not in sport_tag_ids:
        primary = None

    return {
        "sport": "nba",
        "sport_tags": sport_tag_ids,
        "tag_slug": NBA_TAG_SLUG,
        "primary_tag_id": str(primary.get("id")) if primary else None,
        "primary_tag_label": str(primary.get("label") or "NBA") if primary else "NBA",
        "series": str(nba_sport.get("series") or ""),
    }


def fetch_active_nba_events(session: requests.Session, *, tag_slug: str = NBA_TAG_SLUG) -> List[Dict[str, Any]]:
    offset = 0
    events: List[Dict[str, Any]] = []
    while True:
        payload = _fetch_json(
            session,
            GAMMA_EVENTS_URL,
            params={
                "tag_slug": tag_slug,
                "active": "true",
                "closed": "false",
                "limit": 100,
                "offset": offset,
            },
        )
        if isinstance(payload, dict):
            batch = payload.get("events") or []
        elif isinstance(payload, list):
            batch = payload
        else:
            batch = []
        if not batch:
            break
        for event in batch:
            if isinstance(event, dict):
                events.append(event)
        if len(batch) < 100:
            break
        offset += len(batch)
    return events


def is_head_to_head_event(event: Dict[str, Any]) -> bool:
    return extract_matchup_pair(event) is not None


def _normalize_team_name(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def _extract_matchup_pair_from_text(text: str) -> Optional[Tuple[str, str]]:
    text = str(text or "").strip()
    if not text:
        return None
    if text.lower().startswith("will "):
        return None
    match = MATCHUP_EXTRACT_PATTERN.search(text)
    if not match:
        return None
    team_a = _normalize_team_name(match.group("team_a"))
    team_b = _normalize_team_name(match.group("team_b"))
    if not team_a or not team_b or team_a == team_b:
        return None
    return tuple(sorted((team_a, team_b)))


def extract_matchup_pair(event: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    title = str(event.get("title") or event.get("name") or "").strip()
    pair = _extract_matchup_pair_from_text(title)
    if pair:
        return pair

    tags = event.get("tags") or []
    team_tags = []
    if isinstance(tags, list):
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            slug = str(tag.get("slug") or "").strip().lower()
            if not slug or slug in GENERIC_TAG_SLUGS or any(keyword in slug for keyword in PLAYOFF_KEYWORDS):
                continue
            label = str(tag.get("label") or slug).strip()
            if label:
                team_tags.append(_normalize_team_name(label))
    unique_team_tags = sorted({item for item in team_tags if item})
    if len(unique_team_tags) == 2:
        return tuple(unique_team_tags)

    markets = event.get("markets") or []
    if isinstance(markets, list):
        for market in markets:
            if not isinstance(market, dict):
                continue
            pair = _extract_matchup_pair_from_text(market.get("question") or market.get("title") or "")
            if pair:
                return pair
    return None


def is_explicit_playoff_event(event: Dict[str, Any]) -> bool:
    text_parts = [
        str(event.get("title") or event.get("name") or ""),
        str(event.get("slug") or ""),
        str(event.get("description") or ""),
    ]
    markets = event.get("markets") or []
    if isinstance(markets, list):
        for market in markets[:3]:
            if not isinstance(market, dict):
                continue
            text_parts.append(str(market.get("question") or market.get("title") or ""))
            text_parts.append(str(market.get("description") or ""))
    haystack = " ".join(text_parts).lower()
    if any(keyword in haystack for keyword in PLAYOFF_KEYWORDS):
        return True

    tags = event.get("tags") or []
    if isinstance(tags, list):
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            slug = str(tag.get("slug") or "").strip().lower()
            label = str(tag.get("label") or "").strip().lower()
            if any(keyword in slug for keyword in PLAYOFF_KEYWORDS) or any(keyword in label for keyword in PLAYOFF_KEYWORDS):
                return True
    return False


def filter_playoff_head_to_head_events(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidate_events = [event for event in events if isinstance(event, dict) and is_head_to_head_event(event)]
    explicit_playoff_pairs = {
        pair
        for event in candidate_events
        if is_explicit_playoff_event(event)
        for pair in [extract_matchup_pair(event)]
        if pair
    }

    filtered: List[Dict[str, Any]] = []
    for event in candidate_events:
        if is_explicit_playoff_event(event):
            filtered.append(event)
            continue
        pair = extract_matchup_pair(event)
        if pair and pair in explicit_playoff_pairs:
            filtered.append(event)
    return filtered


def is_head_to_head_market(event: Dict[str, Any], market: Dict[str, Any]) -> bool:
    question = str(market.get("question") or market.get("title") or "").strip()
    slug = str(market.get("slug") or "").strip()
    combined = f"{question} {slug}".lower()
    event_pair = extract_matchup_pair(event)
    market_pair = _extract_matchup_pair_from_text(question) or _extract_matchup_pair_from_text(slug.replace("-", " "))

    if not event_pair:
        return False

    team_a, team_b = event_pair
    mentions_both_teams = team_a in combined and team_b in combined
    pair_matches = market_pair == event_pair if market_pair else mentions_both_teams
    if not pair_matches:
        return False

    if "who will win series" in combined:
        return True

    if "1h" in combined or "2h" in combined or "3q" in combined or "4q" in combined:
        return False
    if "spread" in combined or "o/u" in combined or "total games" in combined:
        return False

    if "moneyline" in combined:
        return True

    # Canonical game winner markets often use the bare matchup title without a suffix.
    return ":" not in question and market_pair == event_pair


def collect_normalized_nba_markets(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized_by_condition: Dict[str, Dict[str, Any]] = {}
    for event in events:
        markets = event.get("markets") or []
        if not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict):
                continue
            if not is_head_to_head_market(event, market):
                continue
            _attach_event_meta_to_market(market, event)
            norm = normalize_market_from_gamma(market)
            if not norm:
                continue
            condition_id = str(norm.get("condition_id") or "").strip()
            if not condition_id:
                continue
            normalized_by_condition[condition_id] = norm
    return list(normalized_by_condition.values())


def _load_db_rows_by_condition_ids(conn, condition_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ids = [str(condition_id) for condition_id in condition_ids if str(condition_id).strip()]
    if not ids:
        return {}
    rows: Dict[str, Dict[str, Any]] = {}
    cursor = conn.cursor()
    chunk_size = 500
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start : start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cursor.execute(
            f"""
            SELECT id, condition_id, slug, title, yes_token_id, no_token_id, clob_token_ids,
                   tags, enable_neg_risk, end_date
            FROM markets
            WHERE condition_id IN ({placeholders})
            """,
            chunk,
        )
        for row in cursor.fetchall():
            record = row.as_dict() if hasattr(row, "as_dict") else dict(row)
            rows[str(record["condition_id"])] = record
    return rows


def merge_market_catalog_rows(
    previous_rows: Sequence[Dict[str, Any]],
    current_db_rows: Dict[str, Dict[str, Any]],
    active_condition_ids: Sequence[str],
    *,
    seen_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    seen_at = seen_at or iso_now()
    previous_by_condition = {
        str(row.get("condition_id") or "").strip(): dict(row)
        for row in previous_rows
        if str(row.get("condition_id") or "").strip()
    }
    active_set = {str(condition_id).strip() for condition_id in active_condition_ids if str(condition_id).strip()}
    merged: List[Dict[str, Any]] = []
    all_condition_ids = sorted(set(previous_by_condition) | set(current_db_rows))
    for condition_id in all_condition_ids:
        previous = previous_by_condition.get(condition_id, {})
        current = current_db_rows.get(condition_id, {})
        if not previous and not current:
            continue
        current_active = 1 if condition_id in active_set else 0
        discovered_at = str(previous.get("discovered_at") or seen_at)
        last_seen_at = seen_at if current_active else str(previous.get("last_seen_at") or seen_at)
        row = {
            "market_id": int(current.get("id") or previous.get("market_id") or 0),
            "condition_id": condition_id,
            "slug": str(current.get("slug") or previous.get("slug") or ""),
            "title": str(current.get("title") or previous.get("title") or ""),
            "yes_token_id": str(current.get("yes_token_id") or previous.get("yes_token_id") or ""),
            "no_token_id": str(current.get("no_token_id") or previous.get("no_token_id") or ""),
            "clob_token_ids": safe_json_dumps(parse_json_list(current.get("clob_token_ids") or previous.get("clob_token_ids"))),
            "tags": str(current.get("tags") or previous.get("tags") or "[]"),
            "enable_neg_risk": int(current.get("enable_neg_risk") or previous.get("enable_neg_risk") or 0),
            "active": current_active,
            "end_date": str(current.get("end_date") or previous.get("end_date") or ""),
            "discovered_at": discovered_at,
            "last_seen_at": last_seen_at,
        }
        merged.append({column: row.get(column) for column in MARKET_CATALOG_COLUMNS})
    merged.sort(key=lambda item: (-int(item["active"]), str(item["end_date"] or ""), int(item["market_id"] or 0)))
    return merged


def build_token_catalog_rows(market_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    token_rows: List[Dict[str, Any]] = []
    for row in market_rows:
        market_id = int(row.get("market_id") or 0)
        condition_id = str(row.get("condition_id") or "")
        slug = str(row.get("slug") or "")
        title = str(row.get("title") or "")
        yes_token_id = str(row.get("yes_token_id") or "")
        no_token_id = str(row.get("no_token_id") or "")
        token_ids = parse_json_list(row.get("clob_token_ids"))
        if not token_ids:
            token_ids = [token for token in (yes_token_id, no_token_id) if token]
        for index, token_id in enumerate(token_ids):
            outcome = f"OUTCOME_{index}"
            if token_id == yes_token_id:
                outcome = "YES"
            elif token_id == no_token_id:
                outcome = "NO"
            token_rows.append(
                {
                    "market_id": market_id,
                    "condition_id": condition_id,
                    "slug": slug,
                    "title": title,
                    "token_id": token_id,
                    "outcome": outcome,
                    "outcome_index": index,
                    "active": int(row.get("active") or 0),
                    "end_date": str(row.get("end_date") or ""),
                    "discovered_at": str(row.get("discovered_at") or ""),
                    "last_seen_at": str(row.get("last_seen_at") or ""),
                }
            )
    token_rows.sort(key=lambda item: (int(item["market_id"]), int(item["outcome_index"])))
    return [{column: row.get(column) for column in TOKEN_CATALOG_COLUMNS} for row in token_rows]


def sync_nba_markets(*, data_root: Path | str = DEFAULT_DATA_ROOT, db_path: Optional[str] = None) -> Dict[str, Any]:
    root = ensure_data_root(data_root)
    session = _build_session()
    filter_info = discover_nba_filter(session)
    all_events = fetch_active_nba_events(session, tag_slug=str(filter_info["tag_slug"]))
    events = filter_playoff_head_to_head_events(all_events)
    normalized_markets = collect_normalized_nba_markets(events)
    active_condition_ids = [str(item.get("condition_id") or "") for item in normalized_markets]

    conn = get_connection(db_path)
    try:
        init_schema(conn=conn, db_path=db_path or "")
        upserted = batch_upsert_markets(conn, normalized_markets) if normalized_markets else 0
        previous_rows = load_market_catalog_rows(root)
        current_rows = _load_db_rows_by_condition_ids(
            conn,
            set(active_condition_ids) | {str(row.get("condition_id") or "") for row in previous_rows},
        )
    finally:
        conn.close()

    seen_at = iso_now()
    market_rows = merge_market_catalog_rows(previous_rows, current_rows, active_condition_ids, seen_at=seen_at)
    token_rows = build_token_catalog_rows(market_rows)
    state_payload = {
        "last_sync_at": seen_at,
        "tag_slug": filter_info["tag_slug"],
        "primary_tag_id": filter_info["primary_tag_id"],
        "primary_tag_label": filter_info["primary_tag_label"],
        "sport_tags": filter_info["sport_tags"],
        "series": filter_info["series"],
        "nba_event_count": len(all_events),
        "active_event_count": len(events),
        "active_market_count": sum(int(row.get("active") or 0) for row in market_rows),
        "catalog_market_count": len(market_rows),
        "catalog_token_count": len(token_rows),
        "db_target": describe_db_target(),
        "upserted_markets": upserted,
    }
    write_catalog_artifacts(root, market_rows, token_rows, state_payload)
    return state_payload


def _resolve_market_rows_for_print(data_root: Path | str, *, active_only: bool) -> List[Dict[str, Any]]:
    rows = load_market_catalog_rows(data_root)
    if active_only:
        rows = [row for row in rows if int(row.get("active") or 0) == 1]
    return rows


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NBA-only Polymarket market catalog")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_cmd = sub.add_parser("sync-nba-markets", help="Refresh the NBA-only market catalog")
    add_db_cli_args(sync_cmd)
    sync_cmd.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))

    list_cmd = sub.add_parser("list-nba-markets", help="Print market rows from the NBA catalog")
    list_cmd.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    list_cmd.add_argument("--active-only", action="store_true")
    list_cmd.add_argument("--limit", type=int, default=0)
    return parser


def command_sync(args: argparse.Namespace) -> int:
    configure_db_from_args(args)
    summary = sync_nba_markets(data_root=args.data_root, db_path=getattr(args, "sqlite_path", None))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_list(args: argparse.Namespace) -> int:
    rows = _resolve_market_rows_for_print(args.data_root, active_only=bool(args.active_only))
    if args.limit and args.limit > 0:
        rows = rows[: int(args.limit)]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "sync-nba-markets":
        return command_sync(args)
    if args.command == "list-nba-markets":
        return command_list(args)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
