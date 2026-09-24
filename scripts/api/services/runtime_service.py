from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
    resolve_service_value,
)


@dataclass(frozen=True)
class RuntimeServiceDependencies:
    settings: Any
    application: Any
    get_yahoo_market_snapshot: Callable[..., Any]
    crypto_coingecko_ids: Dict[str, str]
    http_json_get: Callable[..., Any]
    safe_float: Callable[..., Any] | None
    utc_now_iso: Callable[..., Any]
    finance_runtime_ttl_seconds: int | None
    sports_runtime_ttl_seconds: int | None
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    snapshot_store: Any
    requests_lib: Any
    beautiful_soup: Any
    get_snapshot_payload: Callable[..., Any] | None

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> RuntimeServiceDependencies:
        return cls(
            settings=resolve_service_value(context, "SETTINGS"),
            application=resolve_service_value(context, "app"),
            get_yahoo_market_snapshot=resolve_service_callable(
                context,
                "get_yahoo_market_snapshot",
            ),
            crypto_coingecko_ids=resolve_service_value(
                context,
                "CRYPTO_COINGECKO_IDS",
                {},
            ),
            http_json_get=resolve_service_callable(
                context,
                "http_json_get",
            ),
            safe_float=resolve_optional_service_callable(
                context,
                "_safe_float",
            ),
            utc_now_iso=resolve_service_callable(
                context,
                "utc_now_iso",
            ),
            finance_runtime_ttl_seconds=resolve_service_value(
                context,
                "FINANCE_RUNTIME_TTL_SECONDS",
            ),
            sports_runtime_ttl_seconds=resolve_service_value(
                context,
                "SPORTS_RUNTIME_TTL_SECONDS",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
            requests_lib=resolve_optional_service_value(
                context,
                "requests",
            ),
            beautiful_soup=resolve_optional_service_value(
                context,
                "BeautifulSoup",
            ),
            get_snapshot_payload=resolve_optional_service_callable(
                context,
                "get_snapshot_payload",
            ),
        )


RuntimeServiceContext = Mapping[str, Any] | RuntimeServiceDependencies


def _dependencies(
    context: RuntimeServiceContext,
) -> RuntimeServiceDependencies:
    if isinstance(context, RuntimeServiceDependencies):
        return context
    return RuntimeServiceDependencies.from_context(context)


def build_market_group_cache_key(items: List[tuple[str, str, str]], *, kind: str) -> str:
    return json.dumps(
        {
            "kind": kind,
            "symbols": [symbol for _, _, symbol in items],
            "snapshotVersion": 3 if kind == "crypto" else 2,
        },
        sort_keys=True,
        ensure_ascii=True,
    )


def normalize_market_group_payload(payload: Any, *, kind: str, limit: Optional[int] = None, generated_at: str | None = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"kind": kind, "items": [], "generatedAt": str(generated_at or ""), "status": "invalid"}
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    if limit is not None:
        items = items[: max(0, int(limit))]
    return {
        **payload,
        "kind": str(payload.get("kind") or kind),
        "items": items,
        "generatedAt": str(payload.get("generatedAt") or generated_at or ""),
        "status": str(payload.get("status") or ("ok" if items else "empty")),
    }


def fetch_live_market_group_payload(ctx: RuntimeServiceContext, items: List[tuple[str, str, str]], *, kind: str) -> Dict[str, Any]:
    rows_by_symbol: Dict[str, Dict[str, Any]] = {}

    def _load_row(entry: tuple[str, str, str]) -> tuple[str, Optional[Dict[str, Any]]]:
        key, label, symbol = entry
        is_crypto = kind == "crypto"
        try:
            snapshot = _dependencies(ctx).get_yahoo_market_snapshot(
                symbol,
                interval="5m" if is_crypto else "30m",
                range_name="1d" if is_crypto else "5d",
                # Market group watcher freshness should reflect a real source fetch
                # each run; seeded Redis/SQLite handles serving cache for readers.
                ttl_seconds=5,
            )
        except Exception:
            _dependencies(ctx).application.logger.exception("yahoo snapshot failed symbol=%s", symbol)
            snapshot = None
        if not snapshot:
            return symbol, None
        return symbol, {
            "id": key,
            "label": label,
            "symbol": symbol,
            "price": snapshot.get("price"),
            "changePercent": snapshot.get("changePercent"),
            "currency": snapshot.get("currency"),
            "volume24h": snapshot.get("volume24h"),
            "points": snapshot.get("points") or [],
        }

    max_workers = min(8, max(1, len(items)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_load_row, item) for item in items]
        for future in as_completed(futures):
            symbol, row = future.result()
            if row is not None:
                rows_by_symbol[symbol] = row

    rows = [rows_by_symbol[symbol] for _, _, symbol in items if symbol in rows_by_symbol]
    if kind == "crypto" and len(rows) < len(items):
        try:
            ids = [_dependencies(ctx).crypto_coingecko_ids[symbol] for _, _, symbol in items if symbol in _dependencies(ctx).crypto_coingecko_ids]
            payload = _dependencies(ctx).http_json_get(
                f"{_dependencies(ctx).settings.coingecko_base_url.rstrip('/')}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": ",".join(ids),
                    "sparkline": "true",
                    "price_change_percentage": "24h",
                },
                timeout=12,
                headers={"User-Agent": "polydata-runtime/1.0", "Accept": "application/json"},
            ) or []
            by_id = {str(item.get("id")): item for item in payload if isinstance(item, dict)}
            yahoo_rows = {str(item.get("symbol")): item for item in rows if isinstance(item, dict)}
            merged_rows = []
            for key, label, symbol in items:
                existing = yahoo_rows.get(symbol)
                if existing:
                    merged_rows.append(existing)
                    continue
                coin = by_id.get(_dependencies(ctx).crypto_coingecko_ids.get(symbol, ""))
                if not coin:
                    continue
                spark = (((coin.get("sparkline_in_7d") or {}).get("price")) or [])[-48:]
                points = [
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "value": _dependencies(ctx).safe_float(value),
                    }
                    for value in spark
                    if _dependencies(ctx).safe_float(value) is not None
                ]
                merged_rows.append(
                    {
                        "id": key,
                        "label": label,
                        "symbol": symbol,
                        "price": _dependencies(ctx).safe_float(coin.get("current_price")),
                        "changePercent": _dependencies(ctx).safe_float(coin.get("price_change_percentage_24h")),
                        "currency": "USD",
                        "marketCap": _dependencies(ctx).safe_float(coin.get("market_cap")),
                        "volume24h": _dependencies(ctx).safe_float(coin.get("total_volume")),
                        "points": points,
                    }
                )
            rows = merged_rows
        except Exception:
            _dependencies(ctx).application.logger.exception("coingecko crypto fallback failed")
    return normalize_market_group_payload({"kind": kind, "items": rows, "generatedAt": _dependencies(ctx).utc_now_iso()}, kind=kind)


def get_market_group_snapshot(ctx: RuntimeServiceContext, items: List[tuple[str, str, str]], *, kind: str) -> Dict[str, Any]:
    ttl_seconds = 10 if kind == "crypto" else _dependencies(ctx).finance_runtime_ttl_seconds
    cache_key = build_market_group_cache_key(items, kind=kind)
    namespace = f"snapshot:markets:{kind}"
    seeded_payload = _read_seeded_snapshot(ctx, namespace=namespace, cache_key=cache_key, ttl_seconds=ttl_seconds)
    if seeded_payload is not None:
        return normalize_market_group_payload(seeded_payload, kind=kind, generated_at=_dependencies(ctx).utc_now_iso())

    payload = _with_cache_mode(fetch_live_market_group_payload(ctx, items, kind=kind), "live-fallback")
    return _store_seed_fallback(ctx, namespace=namespace, cache_key=cache_key, payload=payload, ttl_seconds=ttl_seconds)


NBA_SCOREBOARD_NAMESPACE = "snapshot:sports:nba"
NBA_INTEL_NAMESPACE = "snapshot:sports:nba-intel"
NBA_MATCHUP_PREDICTOR_NAMESPACE = "snapshot:sports:nba-matchup-predictor"


def build_nba_scoreboard_cache_key(limit: int = 10) -> str:
    return json.dumps({"limit": limit}, sort_keys=True, ensure_ascii=True)


def build_nba_intel_cache_key(limit: int = 12) -> str:
    return json.dumps({"limit": limit}, sort_keys=True, ensure_ascii=True)


def build_nba_matchup_predictor_cache_key(limit: int = 8) -> str:
    return json.dumps({"limit": limit}, sort_keys=True, ensure_ascii=True)


def _with_cache_mode(payload: Dict[str, Any], cache_mode: str) -> Dict[str, Any]:
    return {**payload, "cacheMode": str(payload.get("cacheMode") or cache_mode)}


def _read_seeded_snapshot(ctx: RuntimeServiceContext, *, namespace: str, cache_key: str, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    reader = _dependencies(ctx).get_cached_json
    if callable(reader):
        redis_payload = reader(namespace, cache_key)
        if isinstance(redis_payload, dict):
            _dependencies(ctx).snapshot_store.set(namespace, cache_key, redis_payload, ttl_seconds)
            return _with_cache_mode(redis_payload, "redis-seed")

    snapshot_store = _dependencies(ctx).snapshot_store
    if snapshot_store is None:
        return None
    sqlite_payload = snapshot_store.get(namespace, cache_key)
    if isinstance(sqlite_payload, dict):
        setter = _dependencies(ctx).set_cached_json
        if callable(setter):
            setter(namespace, cache_key, sqlite_payload, ttl_seconds)
        return _with_cache_mode(sqlite_payload, "sqlite-seed")
    stale_payload = snapshot_store.get_stale(namespace, cache_key)
    if isinstance(stale_payload, dict):
        setter = _dependencies(ctx).set_cached_json
        if callable(setter):
            setter(namespace, cache_key, stale_payload, min(15, ttl_seconds))
        return _with_cache_mode(stale_payload, "stale-seed")
    return None


def _store_seed_fallback(ctx: RuntimeServiceContext, *, namespace: str, cache_key: str, payload: Dict[str, Any], ttl_seconds: int) -> Dict[str, Any]:
    snapshot_store = _dependencies(ctx).snapshot_store
    if snapshot_store is not None:
        snapshot_store.set(namespace, cache_key, payload, ttl_seconds)
    setter = _dependencies(ctx).set_cached_json
    if callable(setter):
        setter(namespace, cache_key, payload, ttl_seconds)
    return payload


def normalize_nba_scoreboard_payload(payload: Any, *, limit: int = 10, generated_at: str | None = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"items": [], "generatedAt": str(generated_at or ""), "status": "invalid"}
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)][:limit]
    return {
        **payload,
        "items": items,
        "generatedAt": str(payload.get("generatedAt") or generated_at or ""),
        "status": str(payload.get("status") or ("ok" if items else "empty")),
        "source": str(payload.get("source") or "ESPN NBA Scoreboard"),
    }


def fetch_live_nba_scoreboard_payload(ctx: RuntimeServiceContext, limit: int = 10) -> Dict[str, Any]:
    payload = _dependencies(ctx).http_json_get(
        f"{_dependencies(ctx).settings.espn_nba_base_url.rstrip('/')}/scoreboard",
        params={"limit": limit},
        timeout=12,
    ) or {}
    events = payload.get("events") or []
    games = []
    for event in events[:limit]:
        competitions = event.get("competitions") or []
        competition = competitions[0] if competitions else {}
        competitors = competition.get("competitors") or []
        away = next((item for item in competitors if item.get("homeAway") == "away"), None)
        home = next((item for item in competitors if item.get("homeAway") == "home"), None)
        status = (((competition.get("status") or {}).get("type")) or {})
        games.append(
            {
                "id": event.get("id"),
                "name": event.get("shortName") or event.get("name"),
                "status": status.get("description") or status.get("detail"),
                "state": status.get("state"),
                "tipoff": event.get("date"),
                "homeTeam": ((home or {}).get("team") or {}).get("displayName"),
                "awayTeam": ((away or {}).get("team") or {}).get("displayName"),
                "homeScore": (home or {}).get("score"),
                "awayScore": (away or {}).get("score"),
                "broadcast": (((competition.get("broadcasts") or [None])[0]) or {}).get("names", [None])[0],
            }
        )
    return normalize_nba_scoreboard_payload({"items": games, "generatedAt": _dependencies(ctx).utc_now_iso()}, limit=limit)


def get_nba_scoreboard_snapshot(ctx: RuntimeServiceContext, limit: int = 10) -> Dict[str, Any]:
    ttl_seconds = int(_dependencies(ctx).sports_runtime_ttl_seconds)
    cache_key = build_nba_scoreboard_cache_key(limit=limit)
    seeded_payload = _read_seeded_snapshot(ctx, namespace=NBA_SCOREBOARD_NAMESPACE, cache_key=cache_key, ttl_seconds=ttl_seconds)
    if seeded_payload is None and int(limit or 0) != 10:
        seeded_payload = _read_seeded_snapshot(
            ctx,
            namespace=NBA_SCOREBOARD_NAMESPACE,
            cache_key=build_nba_scoreboard_cache_key(limit=10),
            ttl_seconds=ttl_seconds,
        )
    if seeded_payload is not None:
        return normalize_nba_scoreboard_payload(seeded_payload, limit=limit, generated_at=_dependencies(ctx).utc_now_iso())

    payload = _with_cache_mode(fetch_live_nba_scoreboard_payload(ctx, limit=limit), "live-fallback")
    return _store_seed_fallback(ctx, namespace=NBA_SCOREBOARD_NAMESPACE, cache_key=cache_key, payload=payload, ttl_seconds=ttl_seconds)


def _runtime_float(ctx: RuntimeServiceContext, value: Any) -> Optional[float]:
    safe_float = _dependencies(ctx).safe_float
    if callable(safe_float):
        return safe_float(value)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _espn_stat_value(ctx: RuntimeServiceContext, stats: List[Dict[str, Any]], name: str) -> Optional[float]:
    for stat in stats:
        if stat.get("name") == name:
            return _runtime_float(ctx, stat.get("value"))
    return None


def normalize_nba_matchup_predictor_payload(payload: Any, *, limit: int = 8, generated_at: str | None = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"items": [], "generatedAt": str(generated_at or ""), "source": "ESPN Matchup Predictor", "status": "invalid"}
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)][:limit]
    return {
        **payload,
        "items": items,
        "generatedAt": str(payload.get("generatedAt") or generated_at or ""),
        "source": str(payload.get("source") or "ESPN Matchup Predictor"),
        "status": str(payload.get("status") or ("ok" if items else "empty")),
    }


def fetch_live_nba_matchup_predictor_payload(ctx: RuntimeServiceContext, limit: int = 8) -> Dict[str, Any]:
    scoreboard = _dependencies(ctx).http_json_get(
        f"{_dependencies(ctx).settings.espn_nba_base_url.rstrip('/')}/scoreboard",
        params={"limit": limit},
        timeout=12,
    ) or {}
    events = scoreboard.get("events") or []
    items_by_event_id: Dict[str, Dict[str, Any]] = {}

    def _load_event(event: Dict[str, Any]) -> tuple[str, Optional[Dict[str, Any]]]:
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            return "", None
        competitions = event.get("competitions") or []
        competition = competitions[0] if competitions else {}
        competition_id = str(competition.get("id") or event_id).strip()
        competitors = competition.get("competitors") or []
        away = next((item for item in competitors if item.get("homeAway") == "away"), None)
        home = next((item for item in competitors if item.get("homeAway") == "home"), None)
        status = (((competition.get("status") or {}).get("type")) or {})
        try:
            predictor = _dependencies(ctx).http_json_get(
                (
                    _dependencies(ctx).settings.espn_core_nba_base_url.rstrip("/")
                    + f"/events/{event_id}/competitions/{competition_id}/predictor"
                ),
                params={"lang": "en", "region": "us"},
                timeout=8,
                headers={"User-Agent": "polydata-runtime/1.0", "Accept": "application/json"},
            ) or {}
        except Exception:
            _dependencies(ctx).application.logger.exception("nba matchup predictor fetch failed event_id=%s", event_id)
            return event_id, None

        away_stats = ((predictor.get("awayTeam") or {}).get("statistics") or [])
        home_stats = ((predictor.get("homeTeam") or {}).get("statistics") or [])
        away_projection = _espn_stat_value(ctx, away_stats, "gameProjection")
        home_projection = _espn_stat_value(ctx, home_stats, "gameProjection")
        if away_projection is None and home_projection is not None:
            away_projection = max(0.0, min(100.0, 100.0 - home_projection))
        if home_projection is None and away_projection is not None:
            home_projection = max(0.0, min(100.0, 100.0 - away_projection))

        away_expected = _espn_stat_value(ctx, away_stats, "teamExpectedPts")
        home_expected = _espn_stat_value(ctx, home_stats, "teamExpectedPts")
        if away_expected is None:
            away_expected = _espn_stat_value(ctx, home_stats, "oppExpectedPts")
        if home_expected is None:
            home_expected = _espn_stat_value(ctx, away_stats, "oppExpectedPts")

        projected_margin = _espn_stat_value(ctx, away_stats, "teamPredPtDiff")
        if projected_margin is None:
            home_margin = _espn_stat_value(ctx, home_stats, "teamPredPtDiff")
            projected_margin = -home_margin if home_margin is not None else None

        matchup_quality = _espn_stat_value(ctx, away_stats, "matchupQuality")
        if matchup_quality is None:
            matchup_quality = _espn_stat_value(ctx, home_stats, "matchupQuality")

        if away_projection is None and home_projection is None and matchup_quality is None:
            return event_id, None

        return event_id, {
            "eventId": event_id,
            "name": event.get("name") or predictor.get("name"),
            "shortName": event.get("shortName") or predictor.get("shortName"),
            "tipoff": event.get("date"),
            "state": status.get("state"),
            "status": status.get("description") or status.get("detail"),
            "awayTeam": ((away or {}).get("team") or {}).get("displayName"),
            "homeTeam": ((home or {}).get("team") or {}).get("displayName"),
            "awayWinProbability": away_projection,
            "homeWinProbability": home_projection,
            "matchupQuality": matchup_quality,
            "projectedMargin": projected_margin,
            "awayExpectedPoints": away_expected,
            "homeExpectedPoints": home_expected,
            "lastModified": predictor.get("lastModified"),
        }

    max_workers = min(6, max(1, len(events[:limit])))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_load_event, event) for event in events[:limit]]
        for future in as_completed(futures):
            event_id, item = future.result()
            if event_id and item is not None:
                items_by_event_id[event_id] = item

    ordered_items = [
        items_by_event_id[str(event.get("id"))]
        for event in events[:limit]
        if str(event.get("id")) in items_by_event_id
    ]
    return normalize_nba_matchup_predictor_payload(
        {
            "items": ordered_items,
            "generatedAt": _dependencies(ctx).utc_now_iso(),
            "source": "ESPN Matchup Predictor",
        },
        limit=limit,
    )


def get_nba_matchup_predictor_snapshot(ctx: RuntimeServiceContext, limit: int = 8) -> Dict[str, Any]:
    ttl_seconds = int(_dependencies(ctx).sports_runtime_ttl_seconds)
    cache_key = build_nba_matchup_predictor_cache_key(limit=limit)
    seeded_payload = _read_seeded_snapshot(ctx, namespace=NBA_MATCHUP_PREDICTOR_NAMESPACE, cache_key=cache_key, ttl_seconds=ttl_seconds)
    if seeded_payload is None and int(limit or 0) != 8:
        seeded_payload = _read_seeded_snapshot(
            ctx,
            namespace=NBA_MATCHUP_PREDICTOR_NAMESPACE,
            cache_key=build_nba_matchup_predictor_cache_key(limit=8),
            ttl_seconds=ttl_seconds,
        )
    if seeded_payload is not None:
        return normalize_nba_matchup_predictor_payload(seeded_payload, limit=limit, generated_at=_dependencies(ctx).utc_now_iso())

    payload = _with_cache_mode(fetch_live_nba_matchup_predictor_payload(ctx, limit=limit), "live-fallback")
    return _store_seed_fallback(ctx, namespace=NBA_MATCHUP_PREDICTOR_NAMESPACE, cache_key=cache_key, payload=payload, ttl_seconds=ttl_seconds)


def normalize_nba_intel_payload(payload: Any, *, limit: int = 12, generated_at: str | None = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"items": [], "lineups": [], "generatedAt": str(generated_at or ""), "status": "invalid"}
    news_items = [item for item in (payload.get("items") or []) if isinstance(item, dict)][:limit]
    lineups = [item for item in (payload.get("lineups") or []) if isinstance(item, dict)][: min(limit, 8)]
    return {
        **payload,
        "items": news_items,
        "lineups": lineups,
        "generatedAt": str(payload.get("generatedAt") or generated_at or ""),
        "status": str(payload.get("status") or ("ok" if news_items or lineups else "empty")),
        "source": str(payload.get("source") or "ESPN NBA Intel"),
    }


def fetch_live_nba_intel_payload(ctx: RuntimeServiceContext, limit: int = 12) -> Dict[str, Any]:
    news_items: List[Dict[str, Any]] = []
    lineup_items: List[Dict[str, Any]] = []
    try:
        payload = _dependencies(ctx).http_json_get(
            f"{_dependencies(ctx).settings.espn_nba_base_url.rstrip('/')}/news",
            timeout=12,
            headers={"User-Agent": "polydata-runtime/1.0", "Accept": "application/json"},
        ) or {}
        for article in (payload.get("articles") or [])[:limit]:
            headline = str(article.get("headline") or "").strip()
            if not headline:
                continue
            source_node = article.get("source") or {}
            source = source_node.get("name") if isinstance(source_node, dict) else None
            links = article.get("links") or {}
            web_link = ((links.get("web") or {}).get("href")) if isinstance(links, dict) else None
            news_items.append(
                {
                    "headline": headline,
                    "description": (article.get("description") or article.get("story") or "")[:280] or None,
                    "publishedAt": article.get("published") or article.get("lastModified"),
                    "url": web_link,
                    "source": source or "ESPN",
                    "type": "news",
                }
            )
    except Exception:
        _dependencies(ctx).application.logger.exception("nba intel news fetch failed")

    try:
        lineup_date = datetime.now(timezone.utc).strftime("%Y%m%d")
        nba_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{_dependencies(ctx).settings.nba_official_base_url.rstrip('/')}/",
            "Origin": _dependencies(ctx).settings.nba_official_base_url.rstrip("/"),
            "x-nba-stats-origin": "stats",
            "x-nba-stats-token": "true",
        }
        payload = _dependencies(ctx).http_json_get(
            f"{_dependencies(ctx).settings.nba_lineups_base_url.rstrip('/')}/00_daily_lineups_{lineup_date}.json",
            timeout=8,
            headers=nba_headers,
        ) or {}
        for game in (payload.get("games") or [])[: min(limit, 8)]:
            home_team = ((game.get("homeTeam") or {}).get("teamName")) or ((game.get("homeTeam") or {}).get("teamTricode"))
            away_team = ((game.get("awayTeam") or {}).get("teamName")) or ((game.get("awayTeam") or {}).get("teamTricode"))
            starters: List[Dict[str, Any]] = []
            for bucket_key, side_label in (("homePlayers", "HOME"), ("awayPlayers", "AWAY")):
                for player in (game.get(bucket_key) or []):
                    player_name = str(player.get("playerName") or "").strip()
                    if not player_name:
                        continue
                    starters.append(
                        {
                            "side": side_label,
                            "playerName": player_name,
                            "position": player.get("position") or "",
                            "lineupStatus": player.get("lineupStatus") or player.get("rosterStatus") or "",
                            "timestamp": player.get("timestamp"),
                        }
                    )
            lineup_items.append(
                {
                    "gameId": game.get("gameId"),
                    "label": f"{away_team or 'Away'} @ {home_team or 'Home'}",
                    "status": game.get("gameStatusText") or game.get("gameStatus"),
                    "starters": starters[:10],
                }
            )
    except Exception:
        _dependencies(ctx).application.logger.exception("nba intel lineup fetch failed")
    if not lineup_items:
        try:
            scoreboard = fetch_live_nba_scoreboard_payload(ctx, limit=min(limit, 8))
            for game in (scoreboard.get("items") or [])[: min(limit, 8)]:
                lineup_items.append(
                    {
                        "gameId": game.get("id"),
                        "label": f"{game.get('awayTeam') or 'Away'} @ {game.get('homeTeam') or 'Home'}",
                        "status": game.get("status") or game.get("state"),
                        "starters": [],
                        "sourceMode": "scoreboard-fallback",
                    }
                )
        except Exception:
            _dependencies(ctx).application.logger.exception("nba intel scoreboard lineup fallback failed")
    return normalize_nba_intel_payload({"items": news_items, "lineups": lineup_items, "generatedAt": _dependencies(ctx).utc_now_iso()}, limit=limit)


def get_nba_intel_snapshot(ctx: RuntimeServiceContext, limit: int = 12) -> Dict[str, Any]:
    ttl_seconds = int(_dependencies(ctx).sports_runtime_ttl_seconds)
    cache_key = build_nba_intel_cache_key(limit=limit)
    seeded_payload = _read_seeded_snapshot(ctx, namespace=NBA_INTEL_NAMESPACE, cache_key=cache_key, ttl_seconds=ttl_seconds)
    if seeded_payload is None and int(limit or 0) != 12:
        seeded_payload = _read_seeded_snapshot(
            ctx,
            namespace=NBA_INTEL_NAMESPACE,
            cache_key=build_nba_intel_cache_key(limit=12),
            ttl_seconds=ttl_seconds,
        )
    if seeded_payload is not None:
        return normalize_nba_intel_payload(seeded_payload, limit=limit, generated_at=_dependencies(ctx).utc_now_iso())

    payload = _with_cache_mode(fetch_live_nba_intel_payload(ctx, limit=limit), "live-fallback")
    return _store_seed_fallback(ctx, namespace=NBA_INTEL_NAMESPACE, cache_key=cache_key, payload=payload, ttl_seconds=ttl_seconds)


INFLATION_NOWCAST_NAMESPACE = "snapshot:macro:inflation-nowcast"
INFLATION_NOWCAST_CACHE_KEY = "latest"


def normalize_inflation_nowcast_payload(payload: Any, *, ctx: RuntimeServiceContext, generated_at: str | None = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    has_data = bool(payload.get("monthOverMonth") or payload.get("yearOverYear") or payload.get("quarterly"))
    return {
        **payload,
        "monthOverMonth": payload.get("monthOverMonth"),
        "yearOverYear": payload.get("yearOverYear"),
        "quarterly": payload.get("quarterly") if isinstance(payload.get("quarterly"), list) else [],
        "generatedAt": str(payload.get("generatedAt") or generated_at or _dependencies(ctx).utc_now_iso()),
        "source": str(payload.get("source") or "Cleveland Fed Inflation Nowcasting"),
        "url": str(payload.get("url") or _dependencies(ctx).settings.cleveland_fed_nowcast_url),
        "status": str(payload.get("status") or ("ok" if has_data else "empty")),
    }


def fetch_live_inflation_nowcast_payload(ctx: RuntimeServiceContext) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "monthOverMonth": None,
        "yearOverYear": None,
        "quarterly": [],
        "generatedAt": _dependencies(ctx).utc_now_iso(),
        "source": "Cleveland Fed Inflation Nowcasting",
        "url": _dependencies(ctx).settings.cleveland_fed_nowcast_url,
    }
    if _dependencies(ctx).requests_lib is None or _dependencies(ctx).beautiful_soup is None:
        return normalize_inflation_nowcast_payload(payload, ctx=ctx)
    try:
        response = _dependencies(ctx).requests_lib.get(
            payload["url"],
            timeout=15,
            headers={"User-Agent": "polydata-runtime/1.0", "Accept": "text/html,application/xhtml+xml"},
        )
        response.raise_for_status()
        soup = _dependencies(ctx).beautiful_soup(response.text, "html.parser")
        for table in soup.find_all("table"):
            caption = table.find("caption")
            caption_text = " ".join(caption.get_text(" ", strip=True).split()).lower() if caption else ""
            headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
            rows: List[Dict[str, str]] = []
            for tr in table.find_all("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if not cells or len(cells) != len(headers):
                    continue
                rows.append({headers[index]: cells[index] for index in range(len(headers))})
            if not rows:
                continue
            if "month-over-month percent change" in caption_text:
                payload["monthOverMonth"] = rows[0]
            elif "year-over-year percent change" in caption_text:
                payload["yearOverYear"] = rows[0]
            elif "quarterly annualized percent change" in caption_text:
                payload["quarterly"] = rows[:4]
    except Exception:
        _dependencies(ctx).application.logger.exception("inflation nowcast fetch failed")
    return normalize_inflation_nowcast_payload(payload, ctx=ctx)


def get_inflation_nowcast_snapshot(ctx: RuntimeServiceContext) -> Dict[str, Any]:
    ttl_seconds = max(_dependencies(ctx).finance_runtime_ttl_seconds, 1800)
    seeded_payload = _read_seeded_snapshot(
        ctx,
        namespace=INFLATION_NOWCAST_NAMESPACE,
        cache_key=INFLATION_NOWCAST_CACHE_KEY,
        ttl_seconds=ttl_seconds,
    )
    if seeded_payload is not None:
        return normalize_inflation_nowcast_payload(seeded_payload, ctx=ctx, generated_at=_dependencies(ctx).utc_now_iso())

    def _builder() -> Dict[str, Any]:
        return fetch_live_inflation_nowcast_payload(ctx)

    if _dependencies(ctx).snapshot_store is None and callable(_dependencies(ctx).get_snapshot_payload):
        return _dependencies(ctx).get_snapshot_payload(INFLATION_NOWCAST_NAMESPACE, INFLATION_NOWCAST_CACHE_KEY, _builder, ttl_seconds=ttl_seconds)

    payload = _with_cache_mode(fetch_live_inflation_nowcast_payload(ctx), "live-fallback")
    return _store_seed_fallback(
        ctx,
        namespace=INFLATION_NOWCAST_NAMESPACE,
        cache_key=INFLATION_NOWCAST_CACHE_KEY,
        payload=payload,
        ttl_seconds=ttl_seconds,
    )
