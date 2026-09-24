from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from .http_client import http_json_get


def parse_interval_minutes(interval: str) -> int:
    text = str(interval or "5m").strip().lower()
    match = re.fullmatch(r"(\d+)(m|h|d)", text)
    if not match:
        return 5
    value = max(1, int(match.group(1)))
    unit = match.group(2)
    if unit == "m":
        return value
    if unit == "h":
        return value * 60
    return value * 1440


def range_to_seconds(range_name: str) -> int:
    normalized = str(range_name or "1d").strip().lower()
    mapping = {
        "1h": 3600,
        "6h": 21600,
        "12h": 43200,
        "1d": 86400,
        "3d": 259200,
        "7d": 604800,
        "1w": 604800,
        "30d": 2592000,
        "1m": 2592000,
        "all": 30 * 86400,
    }
    return mapping.get(normalized, 86400)


def _price_scale_compatible(left: Optional[float], right: Optional[float]) -> bool:
    if left in (None, 0) or right in (None, 0):
        return False
    ratio = max(abs(float(left)), abs(float(right))) / max(min(abs(float(left)), abs(float(right))), 1e-12)
    return ratio <= 20


def get_yahoo_market_snapshot(
    ctx: dict,
    symbol: str,
    *,
    interval: str = "30m",
    range_name: str = "5d",
    ttl_seconds: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    cache_key = json.dumps({"symbol": symbol, "interval": interval, "range": range_name}, sort_keys=True, ensure_ascii=True)
    use_runtime_cache = ttl_seconds is None or int(ttl_seconds) > 5
    if use_runtime_cache:
        cached = ctx["get_cached_runtime_payload"]("yahoo-chart", cache_key)
        if cached is not None:
            return cached
    payload = http_json_get(
        ctx,
        f"{ctx['SETTINGS'].yahoo_chart_base_url.rstrip('/')}/{symbol}",
        params={"interval": interval, "range": range_name, "includePrePost": "false"},
        timeout=12,
        headers={"User-Agent": "polydata-runtime/1.0", "Accept": "application/json"},
    )
    result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        return None
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    quotes = ((((result.get("indicators") or {}).get("quote") or [None])[0]) or {})
    closes = quotes.get("close") or []
    points = []
    first_price = None
    last_price = None
    for index, timestamp in enumerate(timestamps):
        if index >= len(closes):
            break
        close = ctx["_safe_float"](closes[index])
        if close is None:
            continue
        if first_price is None:
            first_price = close
        last_price = close
        points.append(
            {
                "timestamp": datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "value": close,
            }
        )
    current = ctx["_safe_float"](meta.get("regularMarketPrice")) or last_price
    previous = ctx["_safe_float"](meta.get("chartPreviousClose")) or first_price
    if current is not None and previous is not None and not _price_scale_compatible(current, previous):
        previous = first_price if _price_scale_compatible(current, first_price) else None
    change_pct = None
    if current is not None and previous not in (None, 0):
        change_pct = ((current - float(previous)) / float(previous)) * 100
    snapshot = {
        "symbol": symbol,
        "price": current,
        "changePercent": round(change_pct, 2) if change_pct is not None else None,
        "currency": meta.get("currency"),
        "volume24h": ctx["_safe_float"](meta.get("regularMarketVolume")),
        "marketCap": ctx["_safe_float"](meta.get("marketCap")),
        "name": meta.get("symbol") or symbol,
        "points": points[-48:],
    }
    cache_ttl = max(1, int(ttl_seconds if ttl_seconds is not None else ctx["FINANCE_RUNTIME_TTL_SECONDS"]))
    if use_runtime_cache:
        return ctx["set_cached_runtime_payload"]("yahoo-chart", cache_key, snapshot, ttl_seconds=cache_ttl)
    return snapshot


def _fetch_clob_prices_history(ctx: dict, token_id: str, *, start_ts: int, end_ts: int, fidelity_minutes: int) -> List[Dict[str, Any]]:
    fidelity = int(max(1, fidelity_minutes))
    bucket_seconds = max(300, min(3600, fidelity * 60))
    bucketed_end_ts = int(end_ts) - (int(end_ts) % bucket_seconds)
    bucketed_start_ts = int(start_ts) - (int(start_ts) % bucket_seconds)
    cache_key = json.dumps(
        {
            "tokenId": str(token_id),
            "startTs": bucketed_start_ts,
            "endTs": bucketed_end_ts,
            "fidelity": fidelity,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    cached = ctx["get_cached_runtime_payload"]("clob-history", cache_key)
    if cached is not None:
        return cached

    session = ctx["get_clob_session"]()
    if session is None:
        return []

    response = session.get(
        f"{ctx['CLOB_API_BASE']}/prices-history",
        params={
            "market": str(token_id),
            "startTs": bucketed_start_ts,
            "endTs": bucketed_end_ts,
            "fidelity": fidelity,
        },
        timeout=ctx["CLOB_TIMEOUT_SECONDS"],
    )
    response.raise_for_status()
    payload = response.json() if response.content else {}
    history = payload.get("history", []) if isinstance(payload, dict) else []
    normalized: Dict[int, Decimal] = {}
    for item in history:
        if not isinstance(item, dict):
            continue
        try:
            timestamp = int(item.get("t"))
            price = Decimal(str(item.get("p")))
        except (TypeError, ValueError, InvalidOperation):
            continue
        if timestamp < bucketed_start_ts or timestamp > bucketed_end_ts:
            continue
        normalized[timestamp] = price
    rows = [{"timestamp": timestamp, "price": normalized[timestamp]} for timestamp in sorted(normalized)]
    return ctx["set_cached_runtime_payload"]("clob-history", cache_key, rows)


def _history_point_before_or_at(history: List[Dict[str, Any]], target_ts: int) -> Optional[Decimal]:
    last_value: Optional[Decimal] = None
    for row in history:
        row_ts = row.get("timestamp")
        price = row.get("price")
        if row_ts is None or price is None:
            continue
        if int(row_ts) > target_ts:
            break
        if isinstance(price, Decimal):
            last_value = price
        else:
            try:
                last_value = Decimal(str(price))
            except (InvalidOperation, ValueError, TypeError):
                continue
    return last_value


def _decimal_to_payload_text(ctx: dict, value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return ctx["format_trade_decimal"](value)


def get_market_clob_price_snapshot(ctx: dict, market: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not market:
        return None
    yes_token_id = str(market.get("yes_token_id") or "").strip()
    if not yes_token_id:
        return None
    market_id = market.get("id")
    cache_key = json.dumps({"marketId": market_id, "yesTokenId": yes_token_id}, sort_keys=True, ensure_ascii=True)
    cached = ctx["get_cached_runtime_payload"]("clob-price-snapshot", cache_key)
    if cached is not None:
        return cached

    now_ts = int(time.time())
    start_ts = now_ts - (3 * 86400)
    try:
        history = _fetch_clob_prices_history(ctx, yes_token_id, start_ts=start_ts, end_ts=now_ts, fidelity_minutes=15)
    except Exception:
        ctx["app"].logger.exception("clob price snapshot failed market_id=%s", market_id)
        return None
    if not history:
        return None

    latest = history[-1]
    latest_price = latest.get("price")
    if not isinstance(latest_price, Decimal):
        try:
            latest_price = Decimal(str(latest_price))
        except (InvalidOperation, ValueError, TypeError):
            latest_price = None
    if latest_price is None:
        return None

    price_1h = _history_point_before_or_at(history, now_ts - 3600)
    price_24h = _history_point_before_or_at(history, now_ts - 86400)

    def _change(current: Optional[Decimal], past: Optional[Decimal]) -> Optional[str]:
        if current is None or past is None:
            return None
        return ctx["format_trade_decimal"](current - past)

    payload = {
        "marketId": market_id,
        "localMarketId": market_id,
        "latestPrice": _decimal_to_payload_text(ctx, latest_price),
        "latestYesPrice": _decimal_to_payload_text(ctx, latest_price),
        "latestNoPrice": _decimal_to_payload_text(ctx, Decimal("1") - latest_price),
        "change1h": _change(latest_price, price_1h),
        "change24h": _change(latest_price, price_24h),
        "updatedAt": datetime.fromtimestamp(int(latest.get("timestamp")), tz=timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return ctx["set_cached_runtime_payload"]("clob-price-snapshot", cache_key, payload)


def get_market_clob_price_series(ctx: dict, market: Optional[Dict[str, Any]], range_name: str = "1d", interval: str = "5m") -> List[Dict[str, Any]]:
    if not market:
        return []
    yes_token_id = str(market.get("yes_token_id") or "").strip()
    if not yes_token_id:
        return []
    market_id = market.get("id")
    cache_key = json.dumps(
        {"marketId": market_id, "yesTokenId": yes_token_id, "range": range_name, "interval": interval},
        sort_keys=True,
        ensure_ascii=True,
    )
    cached = ctx["get_cached_runtime_payload"]("clob-price-series", cache_key)
    if cached is not None:
        return cached

    now_ts = int(time.time())
    step_minutes = parse_interval_minutes(interval)
    step_seconds = max(60, step_minutes * 60)
    range_seconds = max(step_seconds * 2, range_to_seconds(range_name))
    start_ts = now_ts - range_seconds
    fidelity = max(1, min(step_minutes, 60))

    try:
        if range_seconds > 14 * 86400:
            combined: Dict[int, Dict[str, Any]] = {}
            chunk_seconds = 13 * 86400
            chunk_start = start_ts
            while chunk_start < now_ts:
                chunk_end = min(now_ts, chunk_start + chunk_seconds)
                try:
                    for row in _fetch_clob_prices_history(
                        ctx,
                        yes_token_id,
                        start_ts=chunk_start,
                        end_ts=chunk_end,
                        fidelity_minutes=fidelity,
                    ):
                        row_ts = int(row.get("timestamp") or 0)
                        if row_ts:
                            combined[row_ts] = row
                except Exception:
                    ctx["app"].logger.warning(
                        "clob price series chunk failed market_id=%s start_ts=%s end_ts=%s",
                        market_id,
                        chunk_start,
                        chunk_end,
                        exc_info=True,
                    )
                chunk_start = chunk_end + 1
            history = [combined[key] for key in sorted(combined)]
        else:
            history = _fetch_clob_prices_history(ctx, yes_token_id, start_ts=start_ts, end_ts=now_ts, fidelity_minutes=fidelity)
    except Exception:
        ctx["app"].logger.exception("clob price series failed market_id=%s", market_id)
        return []
    if not history:
        return []

    points: List[Dict[str, Any]] = []
    history_index = 0
    latest_price: Optional[Decimal] = None
    aligned_start = start_ts - (start_ts % step_seconds)
    for current_ts in range(aligned_start, now_ts + 1, step_seconds):
        while history_index < len(history) and int(history[history_index].get("timestamp") or 0) <= current_ts:
            raw_price = history[history_index].get("price")
            if isinstance(raw_price, Decimal):
                latest_price = raw_price
            else:
                try:
                    latest_price = Decimal(str(raw_price))
                except (InvalidOperation, ValueError, TypeError):
                    latest_price = latest_price
            history_index += 1
        if latest_price is None:
            continue
        points.append(
            {
                "timestamp": datetime.fromtimestamp(current_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "yesPrice": _decimal_to_payload_text(ctx, latest_price),
                "noPrice": _decimal_to_payload_text(ctx, Decimal("1") - latest_price),
            }
        )

    if not points:
        for row in history[-400:]:
            raw_price = row.get("price")
            try:
                yes_price = raw_price if isinstance(raw_price, Decimal) else Decimal(str(raw_price))
            except (InvalidOperation, ValueError, TypeError):
                continue
            points.append(
                {
                    "timestamp": datetime.fromtimestamp(int(row.get("timestamp")), tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                    "yesPrice": _decimal_to_payload_text(ctx, yes_price),
                    "noPrice": _decimal_to_payload_text(ctx, Decimal("1") - yes_price),
                }
            )

    return ctx["set_cached_runtime_payload"]("clob-price-series", cache_key, points[-400:])


def get_gamma_active_market_filter(ctx: dict, *, ttl_seconds: int = 60, max_pages: int = 12) -> Dict[str, Any]:
    cache_key = json.dumps({"kind": "gamma-active-market-filter", "maxPages": int(max_pages)}, sort_keys=True, ensure_ascii=True)
    cached = ctx["get_cached_runtime_payload"]("gamma-active-market-filter", cache_key)
    if cached is not None:
        return cached

    condition_ids: set[str] = set()
    slugs: set[str] = set()
    limit = 100

    for page in range(max(1, int(max_pages))):
        payload = http_json_get(
            ctx,
            f"{ctx['SETTINGS'].gamma_api_base.rstrip('/')}/events",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": page * limit,
                "order": "volume24hr",
                "ascending": "false",
            },
            timeout=12,
            headers={"Accept": "application/json", "User-Agent": "polydata-runtime/1.0"},
        )
        events = payload if isinstance(payload, list) else ((payload or {}).get("events") or (payload or {}).get("data") or [])
        if not isinstance(events, list) or not events:
            break

        for event in events:
            if not isinstance(event, dict):
                continue
            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                if market.get("active") is False:
                    continue
                if market.get("closed") is True:
                    continue
                if market.get("acceptingOrders") is False:
                    continue
                condition_id = str(market.get("conditionId") or market.get("condition_id") or "").strip().lower()
                slug = str(market.get("slug") or market.get("market_slug") or "").strip().lower()
                if condition_id:
                    condition_ids.add(condition_id)
                if slug:
                    slugs.add(slug)
        if len(events) < limit:
            break

    payload = {
        "conditionIds": sorted(condition_ids),
        "slugs": sorted(slugs),
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return ctx["set_cached_runtime_payload"]("gamma-active-market-filter", cache_key, payload, ttl_seconds=ttl_seconds)
