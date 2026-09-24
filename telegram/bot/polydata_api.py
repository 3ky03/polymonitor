from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Optional

import requests


WORLDCUP_QUERY_ALIASES = {
    "墨西哥": "Mexico",
    "南非": "South Africa",
    "韩国": "South Korea",
    "捷克": "Czech Republic",
    "美国": "USA",
    "加拿大": "Canada",
    "巴西": "Brazil",
    "摩洛哥": "Morocco",
    "德国": "Germany",
    "荷兰": "Netherlands",
    "日本": "Japan",
    "英格兰": "England",
    "法国": "France",
    "西班牙": "Spain",
    "葡萄牙": "Portugal",
}


def _unique_urls(values: Iterable[str]) -> tuple[str, ...]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = str(value or "").strip().rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return tuple(urls)


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(value or ""))


def _expand_worldcup_query(query: str) -> str:
    expanded = str(query or "").strip()
    for alias, value in WORLDCUP_QUERY_ALIASES.items():
        expanded = expanded.replace(alias, value)
    return " ".join(expanded.split())


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _safe_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            parsed = __import__("json").loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _event_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    events = item.get("events")
    if isinstance(events, list):
        for row in events:
            if isinstance(row, dict):
                return row
    return {}


def _polymarket_url(item: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> str:
    event = event or {}
    for key in ("marketUrl", "eventUrl", "url"):
        value = str(item.get(key) or event.get(key) or "").strip()
        if value.startswith("http"):
            return value
    slug = str(item.get("slug") or event.get("slug") or "").strip()
    event_slug = str(event.get("slug") or item.get("eventSlug") or "").strip()
    if event_slug and slug and slug != event_slug:
        return f"https://polymarket.com/event/{event_slug}/{slug}"
    if slug:
        return f"https://polymarket.com/event/{slug}"
    title = str(item.get("question") or item.get("title") or event.get("title") or "").strip()
    return f"https://polymarket.com/search?query={title.replace(' ', '+')}" if title else ""


class PolyDataBotApi:
    def __init__(self, *, base_url: str, timeout_seconds: int = 12, base_urls: Optional[Iterable[str]] = None) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.base_urls = _unique_urls((self.base_url, *(base_urls or ())))
        self.timeout_seconds = max(1, int(timeout_seconds or 12))
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"Accept": "application/json", "User-Agent": "polydata-telegram-bot/1.0"})
        self.worldcup_local_market_search_enabled = _env_bool("POLYDATA_TELEGRAM_WORLDCUP_LOCAL_MARKET_SEARCH", False)

    def get_json(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.base_urls:
            raise RuntimeError("POLYDATA_TELEGRAM_BOT_POLYDATA_API_BASE is required")
        last_error: Optional[Exception] = None
        for base_url in self.base_urls:
            try:
                response = self.session.get(f"{base_url}{path}", params=params or {}, timeout=self.timeout_seconds)
                if response.status_code == 404:
                    return {"error": "not_found", "_status": 404}
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {"items": payload if isinstance(payload, list) else []}
            except requests.RequestException as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("No polyData API base URLs configured")

    def search_markets(self, query: str, *, limit: int = 5) -> Dict[str, Any]:
        cleaned = str(query or "").strip()
        if cleaned.isdigit():
            return self.get_json(f"/markets/{int(cleaned)}/detail")
        if cleaned and "/" not in cleaned and " " not in cleaned and len(cleaned) > 10 and not cleaned.startswith("0x"):
            slug_payload = self.get_json(f"/markets/{cleaned}")
            if not slug_payload.get("error"):
                return {"items": [slug_payload]}
        return self.get_json("/markets", params={"q": cleaned, "pageSize": limit})

    def gamma_search_markets(self, query: str, *, limit: int = 8) -> Dict[str, Any]:
        base_url = str(os.environ.get("POLYDATA_GAMMA_API_BASE") or "https://gamma-api.polymarket.com").rstrip("/")
        rows: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for path in ("/markets", "/events"):
            try:
                response = self.session.get(f"{base_url}{path}", params={"q": query, "limit": limit}, timeout=self.timeout_seconds)
                response.raise_for_status()
                payload = response.json()
            except requests.RequestException:
                continue
            items = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                event = _event_payload(item)
                nested_markets = item.get("markets") if isinstance(item.get("markets"), list) else []
                candidates = nested_markets if nested_markets else [item]
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    event_source = item if nested_markets else event
                    title = str(candidate.get("question") or candidate.get("title") or event_source.get("title") or "").strip()
                    slug = str(candidate.get("slug") or event_source.get("slug") or "").strip()
                    event_slug = str(event_source.get("slug") or candidate.get("eventSlug") or "").strip()
                    key = slug or title
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    rows.append({
                        **candidate,
                        "title": title,
                        "marketTitle": title,
                        "slug": slug,
                        "eventTitle": event_source.get("title"),
                        "eventSlug": event_slug,
                        "marketUrl": _polymarket_url(candidate, event_source),
                        "source": "gamma",
                    })
                    if len(rows) >= limit:
                        return {"items": rows}
        return {"items": rows}

    def gamma_scan_active_markets(self, *, limit: int = 120) -> Dict[str, Any]:
        base_url = str(os.environ.get("POLYDATA_GAMMA_API_BASE") or "https://gamma-api.polymarket.com").rstrip("/")
        rows: list[Dict[str, Any]] = []
        seen: set[str] = set()
        param_sets = (
            {"active": "true", "closed": "false", "limit": limit, "offset": 0, "order": "id", "ascending": "false"},
            {"active": "true", "closed": "false", "limit": limit, "offset": 0, "order": "volume24hr", "ascending": "false"},
            {"active": "true", "closed": "false", "limit": limit, "offset": 0, "q": "world cup"},
            {"active": "true", "closed": "false", "limit": limit, "offset": 0, "q": "fifa"},
            {"active": "true", "closed": "false", "limit": limit, "offset": 0, "q": "soccer"},
            {"active": "true", "closed": "false", "limit": limit, "offset": 0, "tag_slug": "soccer"},
            {"active": "true", "closed": "false", "limit": limit, "offset": 0, "tag_slug": "sports"},
        )
        for path in ("/events", "/markets"):
            for params in param_sets:
                try:
                    response = self.session.get(f"{base_url}{path}", params=params, timeout=self.timeout_seconds)
                    response.raise_for_status()
                    payload = response.json()
                except requests.RequestException:
                    continue
                items = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else []
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    event = _event_payload(item)
                    nested_markets = item.get("markets") if isinstance(item.get("markets"), list) else []
                    candidates = nested_markets if nested_markets else [item]
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            continue
                        event_source = item if nested_markets else event
                        title = str(candidate.get("question") or candidate.get("title") or event_source.get("title") or "").strip()
                        slug = str(candidate.get("slug") or event_source.get("slug") or "").strip()
                        key = slug or str(candidate.get("id") or "") or title
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        rows.append(
                            {
                                **candidate,
                                "title": title,
                                "marketTitle": title,
                                "slug": slug,
                                "eventTitle": event_source.get("title"),
                                "eventSlug": event_source.get("slug") or candidate.get("eventSlug"),
                                "marketUrl": _polymarket_url(candidate, event_source),
                                "source": "gamma-scan",
                            }
                        )
        return {"items": rows}

    def worldcup_market_search(self, query: str, *, limit: int = 8) -> Dict[str, Any]:
        variants = []
        cleaned = str(query or "").strip()
        if cleaned:
            expanded = _expand_worldcup_query(cleaned)
            for base in (expanded, cleaned):
                if base and base not in variants:
                    variants.extend([base, f"world cup {base}", f"fifa world cup {base}", f"{base} winner"])
        rows: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for variant in variants:
            payloads: list[Dict[str, Any]] = []
            if self.worldcup_local_market_search_enabled and not _contains_cjk(variant):
                try:
                    payloads.append(self.search_markets(variant, limit=limit))
                except requests.RequestException:
                    pass
            try:
                payloads.append(self.gamma_search_markets(variant, limit=limit))
            except requests.RequestException:
                pass
            for payload in payloads:
                for item in payload.get("items") if isinstance(payload.get("items"), list) else []:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("slug") or item.get("id") or item.get("title") or item.get("marketTitle") or "")
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    rows.append(item)
                    if len(rows) >= limit:
                        return {"items": rows}
        try:
            scan_payload = self.gamma_scan_active_markets(limit=120)
        except requests.RequestException:
            scan_payload = {"items": []}
        expanded = _expand_worldcup_query(cleaned)
        wanted_terms = {part.lower() for part in expanded.replace("vs", " ").split() if part.strip()}
        for item in scan_payload.get("items") if isinstance(scan_payload.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            text = " ".join(str(item.get(key) or "") for key in ("title", "marketTitle", "question", "eventTitle", "slug", "eventSlug", "groupItemTitle")).lower()
            outcomes = " ".join(str(value or "") for value in _safe_json_list(item.get("outcomes"))).lower()
            hit_count = sum(1 for term in wanted_terms if term in text or term in outcomes)
            if hit_count < max(1, min(2, len(wanted_terms))):
                continue
            key = str(item.get("slug") or item.get("id") or item.get("title") or item.get("marketTitle") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(item)
            if len(rows) >= limit:
                return {"items": rows}
        return {"items": rows}

    def alpha_signals(self, *, limit: int = 5) -> Dict[str, Any]:
        return self.get_json("/runtime/signals/alpha", params={"limit": limit})

    def crypto_markets(self) -> Dict[str, Any]:
        return self.get_json("/runtime/markets/crypto")

    def wallet_summary(self, address: str, *, days: int = 30) -> Dict[str, Any]:
        return self.get_json(f"/analytics/addresses/{address}", params={"days": days})

    def wallet_trades(self, address: str, *, limit: int = 5) -> Dict[str, Any]:
        return self.get_json(f"/analytics/addresses/{address}/trades", params={"limit": limit})

    def pnl(self, address: str) -> Dict[str, Any]:
        return self.get_json(f"/bot/pnl/{address}")

    def worldcup_dashboard(self) -> Dict[str, Any]:
        return self.get_json("/runtime/worldcup/dashboard")

    def worldcup_intel(self, *, limit: int = 24) -> Dict[str, Any]:
        return self.get_json("/runtime/sports/worldcup-intel", params={"limit": limit})
