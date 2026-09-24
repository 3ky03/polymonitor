from __future__ import annotations

import math
import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
)

from . import finance_external_sources_service


DEFAULT_MARKET_ATLAS_LIMIT = 16
DEFAULT_EQUITY_EVENT_LIMIT = 12
DEFAULT_TRADFI_PERP_LIMIT = 12
DEFAULT_LIQUIDITY_REGIME_LIMIT = 12
FINANCE_PANEL_TTL_SECONDS = 60


FINANCE_CATEGORY_RULES: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("crypto", "Crypto", ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "doge", "crypto", "stablecoin", "usdt", "usdc", "etf")),
    ("stocks", "Stocks", ("stock", "stocks", "shares", "equity", "nasdaq", "nyse", "s&p", "sp500", "dow", "nvda", "nvidia", "tesla", "tsla", "apple", "aapl", "microsoft", "msft", "amazon", "amzn", "google", "googl", "meta", "coinbase", "robinhood", "hood", "mstr", "microstrategy")),
    ("ipo", "IPO", ("ipo", "initial public offering", "go public", "direct listing", "s-1", "f-1", "kraken", "stripe", "databricks", "spac")),
    ("rates", "Rates", ("fed", "fomc", "rate cut", "rate hike", "interest rate", "cpi", "inflation", "pce", "treasury", "yield")),
    ("commodity", "Commodity", ("oil", "gas", "gold", "silver", "copper", "wti", "brent", "natural gas", "commodity")),
    ("company-action", "Company Action", ("earnings", "revenue", "eps", "filing", "sec", "insider", "buyback", "split", "dividend", "merger", "acquisition")),
)

WATCHLIST: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("NVDA", "Nvidia", ("nvda", "nvidia")),
    ("TSLA", "Tesla", ("tsla", "tesla")),
    ("MSTR", "MicroStrategy", ("mstr", "microstrategy", "strategy")),
    ("COIN", "Coinbase", ("coin", "coinbase")),
    ("HOOD", "Robinhood", ("hood", "robinhood")),
    ("AVGO", "Broadcom", ("avgo", "broadcom")),
    ("AMD", "AMD", ("amd", "advanced micro devices")),
    ("PLTR", "Palantir", ("pltr", "palantir")),
    ("NFLX", "Netflix", ("nflx", "netflix")),
    ("GME", "GameStop", ("gme", "gamestop")),
    ("AAPL", "Apple", ("aapl", "apple")),
    ("MSFT", "Microsoft", ("msft", "microsoft")),
    ("AMZN", "Amazon", ("amzn", "amazon")),
    ("GOOGL", "Alphabet", ("googl", "google", "alphabet")),
    ("META", "Meta", ("meta", "facebook")),
    ("SPY", "S&P 500", ("spy", "s&p", "sp500", "s&p 500")),
    ("QQQ", "Nasdaq 100", ("qqq", "nasdaq")),
)

TRADFI_PERP_SYMBOLS: Tuple[str, ...] = (
    "NVDA", "TSLA", "MSTR", "COIN", "HOOD", "AVGO", "AMD", "PLTR", "GME",
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "SPY", "QQQ", "GOLD", "OIL",
    "BTC", "ETH", "SOL",
)


@dataclass(frozen=True)
class FinancePanelDependencies:
    application: Any
    utc_now_iso: Callable[..., Any] | None
    get_market_groups_payload: Callable[..., Any] | None
    get_snapshot_payload: Callable[..., Any] | None
    get_yahoo_market_snapshot: Callable[..., Any] | None
    get_crypto_funding_watch_snapshot: Callable[..., Any] | None
    external_sources: finance_external_sources_service.FinanceExternalSourceDependencies

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> FinancePanelDependencies:
        return cls(
            application=resolve_optional_service_value(context, "app"),
            utc_now_iso=resolve_optional_service_callable(
                context,
                "utc_now_iso",
            ),
            get_market_groups_payload=resolve_optional_service_callable(
                context,
                "get_market_groups_payload",
            ),
            get_snapshot_payload=resolve_optional_service_callable(
                context,
                "get_snapshot_payload",
            ),
            get_yahoo_market_snapshot=resolve_optional_service_callable(
                context,
                "get_yahoo_market_snapshot",
            ),
            get_crypto_funding_watch_snapshot=resolve_optional_service_callable(
                context,
                "get_crypto_funding_watch_snapshot",
            ),
            external_sources=(
                finance_external_sources_service.FinanceExternalSourceDependencies.from_context(
                    context,
                )
            ),
        )

    @property
    def logger(self) -> Any:
        return getattr(self.application, "logger", None)


def _now(dependencies: FinancePanelDependencies) -> str:
    if dependencies.utc_now_iso is None:
        return ""
    try:
        return str(dependencies.utc_now_iso())
    except Exception:
        return ""


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _safe_int(value: Any) -> int:
    number = _safe_float(value)
    if number is None:
        return 0
    return int(number)


def _text_for_market(group: Dict[str, Any]) -> str:
    parts: List[str] = [
        str(group.get("title") or ""),
        str(group.get("category") or ""),
        " ".join(str(tag) for tag in (group.get("tags") or [])),
    ]
    for outcome in (group.get("topOutcomes") or group.get("outcomes") or [])[:4]:
        if isinstance(outcome, dict):
            parts.append(str(outcome.get("title") or outcome.get("label") or ""))
    return " ".join(parts).lower()


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    lowered = text.lower()
    for term in terms:
        normalized = term.lower().strip()
        if not normalized:
            continue
        if re.fullmatch(r"[a-z0-9]{2,5}", normalized):
            if re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", lowered):
                return True
            continue
        if normalized in lowered:
            return True
    return False


def _finance_category(group: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    text = _text_for_market(group)
    for category_id, label, terms in FINANCE_CATEGORY_RULES:
        if _contains_any(text, terms):
            return category_id, label
    return None


def _matched_entities(text: str) -> List[Tuple[str, str]]:
    matches: List[Tuple[str, str]] = []
    lowered = text.lower()
    for symbol, company, terms in WATCHLIST:
        if _contains_any(lowered, terms):
            matches.append((symbol, company))
    return matches


def _top_outcome(group: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = group.get("topOutcomes") or group.get("outcomes") or []
    for outcome in outcomes:
        if isinstance(outcome, dict):
            return outcome
    return {}


def _coverage_flags(group: Dict[str, Any], category_id: str) -> List[str]:
    text = _text_for_market(group)
    coverage = set()
    if _top_outcome(group).get("marketId") or _top_outcome(group).get("gammaMarketId"):
        coverage.add("clob")
    if group.get("endDate"):
        coverage.add("oracle")
    if _matched_entities(text):
        coverage.add("quote")
    if "earnings" in text or "eps" in text or "revenue" in text:
        coverage.add("earn")
    if category_id == "ipo" or "sec" in text or "filing" in text or "s-1" in text:
        coverage.add("sec")
    if category_id in {"stocks", "commodity", "crypto"} or "perp" in text or "hyperliquid" in text or "trade.xyz" in text:
        coverage.add("perp")
    if "etf" in text or "bitcoin" in text or "ethereum" in text:
        coverage.add("etf")
    if _safe_float(group.get("volume24h")) and (_safe_float(group.get("volume24h")) or 0) > 50000:
        coverage.add("flow")
    return [flag for flag in ("quote", "earn", "sec", "perp", "etf", "clob", "oracle", "flow") if flag in coverage]


def _linked_market_from_group(group: Dict[str, Any], category_id: Optional[str] = None) -> Dict[str, Any]:
    top = _top_outcome(group)
    coverage = _coverage_flags(group, category_id or (_finance_category(group) or ("other", "Other"))[0])
    return {
        "marketId": top.get("marketId") or group.get("defaultMarketId"),
        "title": group.get("title"),
        "probability": top.get("yesPrice"),
        "change24h": top.get("change24h"),
        "volume24h": top.get("volume24h") or group.get("volume24h"),
        "liquidity": group.get("liquidity"),
        "spread": group.get("spread"),
        "endDate": group.get("endDate"),
        "category": category_id or ((_finance_category(group) or ("other", "Other"))[0]),
        "coverage": coverage,
    }


def _load_finance_groups(
    dependencies: FinancePanelDependencies,
    *,
    page_size: int = 180,
) -> List[Dict[str, Any]]:
    try:
        if dependencies.get_market_groups_payload is None:
            raise RuntimeError("get_market_groups_payload helper missing")
        payload = dependencies.get_market_groups_payload(
            query="",
            page=1,
            page_size=page_size,
            sort="volume",
        )
    except Exception:
        if dependencies.logger is not None:
            dependencies.logger.exception("finance market group snapshot failed")
        payload = {}
    items = [item for item in (payload or {}).get("items", []) if isinstance(item, dict)]
    finance_items = [item for item in items if _finance_category(item)]
    if finance_items:
        return finance_items
    return [
        item
        for item in items
        if _contains_any(_text_for_market(item), ("market", "rate", "bitcoin", "stock", "oil", "gold", "fed"))
    ][: max(20, page_size // 3)]


def _cached(
    dependencies: FinancePanelDependencies,
    namespace: str,
    cache_key: str,
    builder: Callable[[], Dict[str, Any]],
    ttl_seconds: int = FINANCE_PANEL_TTL_SECONDS,
) -> Dict[str, Any]:
    if dependencies.get_snapshot_payload is not None:
        return dependencies.get_snapshot_payload(
            namespace,
            cache_key,
            builder,
            ttl_seconds=ttl_seconds,
        )
    return builder()


def _external_sources(
    dependencies: FinancePanelDependencies,
) -> Dict[str, Any]:
    return finance_external_sources_service.read_finance_external_sources(
        dependencies.external_sources,
    )


def get_finance_market_atlas_snapshot(
    ctx: Mapping[str, Any],
    limit: int = DEFAULT_MARKET_ATLAS_LIMIT,
) -> Dict[str, Any]:
    return _get_finance_market_atlas_snapshot(
        FinancePanelDependencies.from_context(ctx),
        limit=limit,
    )


def _get_finance_market_atlas_snapshot(
    dependencies: FinancePanelDependencies,
    *,
    limit: int = DEFAULT_MARKET_ATLAS_LIMIT,
) -> Dict[str, Any]:
    limit = max(4, min(40, int(limit or DEFAULT_MARKET_ATLAS_LIMIT)))

    def _builder() -> Dict[str, Any]:
        groups = _load_finance_groups(dependencies)
        rows: List[Dict[str, Any]] = []
        category_map: Dict[str, Dict[str, Any]] = {}
        for group in groups:
            category = _finance_category(group) or ("other", "Other")
            category_id, category_label = category
            row = _linked_market_from_group(group, category_id)
            row["categoryLabel"] = category_label
            row["topReason"] = ", ".join((row.get("coverage") or [])[:3]) or "PMKT activity"
            row["gapScore"] = abs(_safe_float(row.get("change24h")) or 0.0) + min((_safe_float(row.get("volume24h")) or 0.0) / 1000000.0, 2.0)
            rows.append(row)
            bucket = category_map.setdefault(
                category_id,
                {
                    "id": category_id,
                    "label": category_label,
                    "activeCount": 0,
                    "volume24h": 0.0,
                    "coverage": set(),
                    "topTitle": None,
                },
            )
            bucket["activeCount"] += 1
            bucket["volume24h"] += _safe_float(row.get("volume24h")) or 0.0
            bucket["coverage"].update(row.get("coverage") or [])
            row_volume = _safe_float(row.get("volume24h")) or 0.0
            bucket_top_volume = _safe_float(bucket.get("topVolume24h")) or 0.0
            if not bucket["topTitle"] or row_volume > bucket_top_volume:
                bucket["topTitle"] = row.get("title")
                bucket["topVolume24h"] = row.get("volume24h")

        rows.sort(key=lambda item: (_safe_float(item.get("volume24h")) or 0.0, _safe_float(item.get("gapScore")) or 0.0), reverse=True)
        categories = []
        for bucket in category_map.values():
            categories.append(
                {
                    "id": bucket["id"],
                    "label": bucket["label"],
                    "activeCount": bucket["activeCount"],
                    "volume24h": bucket["volume24h"],
                    "topTitle": bucket.get("topTitle"),
                    "coverage": sorted(bucket["coverage"]),
                }
            )
        categories.sort(key=lambda item: (_safe_int(item.get("activeCount")), _safe_float(item.get("volume24h")) or 0.0), reverse=True)
        top = rows[0] if rows else None
        return {
            "generatedAt": _now(dependencies),
            "status": "ok" if rows else "warming",
            "cacheMode": "snapshot",
            "sources": {"gamma": "ok" if groups else "empty", "clob": "linked", "coverage": "derived"},
            "summary": {
                "activeCount": len(rows),
                "categoryCount": len(categories),
                "topCategory": categories[0]["label"] if categories else None,
                "topDislocation": top,
                "coverageCount": len({flag for row in rows for flag in (row.get("coverage") or [])}),
            },
            "categories": categories[:8],
            "items": rows[:limit],
        }

    return _cached(
        dependencies,
        "runtime:finance:market-atlas",
        f"v2:{limit}",
        _builder,
    )


def _quote_snapshots(
    dependencies: FinancePanelDependencies,
    symbols: List[str],
) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}

    def _load(symbol: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        try:
            if dependencies.get_yahoo_market_snapshot is None:
                raise RuntimeError("get_yahoo_market_snapshot helper missing")
            return symbol, dependencies.get_yahoo_market_snapshot(
                symbol,
                interval="30m",
                range_name="5d",
                ttl_seconds=60,
            )
        except Exception:
            if dependencies.logger is not None:
                dependencies.logger.exception(
                    "finance quote snapshot failed symbol=%s",
                    symbol,
                )
            return symbol, None

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(symbols)))) as executor:
        futures = [executor.submit(_load, symbol) for symbol in symbols]
        for future in as_completed(futures):
            symbol, row = future.result()
            if isinstance(row, dict):
                rows[symbol] = row
    return rows


def get_equity_event_command_snapshot(
    ctx: Mapping[str, Any],
    limit: int = DEFAULT_EQUITY_EVENT_LIMIT,
) -> Dict[str, Any]:
    return _get_equity_event_command_snapshot(
        FinancePanelDependencies.from_context(ctx),
        limit=limit,
    )


def _get_equity_event_command_snapshot(
    dependencies: FinancePanelDependencies,
    *,
    limit: int = DEFAULT_EQUITY_EVENT_LIMIT,
) -> Dict[str, Any]:
    limit = max(4, min(24, int(limit or DEFAULT_EQUITY_EVENT_LIMIT)))

    def _builder() -> Dict[str, Any]:
        groups = _load_finance_groups(dependencies)
        links_by_symbol: Dict[str, List[Dict[str, Any]]] = {symbol: [] for symbol, _, _ in WATCHLIST}
        for group in groups:
            text = _text_for_market(group)
            for symbol, _company, _terms in WATCHLIST:
                if _contains_any(text, _terms):
                    links_by_symbol.setdefault(symbol, []).append(_linked_market_from_group(group, "stocks"))

        symbols = [symbol for symbol, links in links_by_symbol.items() if links][:limit]
        if len(symbols) < limit:
            symbols.extend([symbol for symbol, _, _ in WATCHLIST if symbol not in symbols][: max(0, limit - len(symbols))])
        quotes = _quote_snapshots(dependencies, symbols[:limit])
        rows: List[Dict[str, Any]] = []
        for symbol, company, terms in WATCHLIST:
            linked = links_by_symbol.get(symbol, [])
            quote = quotes.get(symbol, {})
            if not linked and not quote:
                continue
            event_type = "LINKED"
            event_tone = "watch" if linked else "neutral"
            badges = ["QUOTE"] if quote else []
            if linked:
                badges.append("PMKT")
            text = " ".join(str(market.get("title") or "") for market in linked).lower()
            if _contains_any(text, ("earnings", "eps", "revenue")):
                event_type = "EARN"
                badges.append("EARN")
            if _contains_any(text, ("ipo", "s-1", "filing", "sec")):
                event_type = "FILING"
                badges.append("SEC")
            if symbol in {"MSTR", "COIN", "HOOD"}:
                badges.append("CRYPTO-LINK")
            top_link = max(linked, key=lambda item: _safe_float(item.get("volume24h")) or 0.0, default={})
            rows.append(
                {
                    "symbol": symbol,
                    "company": company,
                    "price": quote.get("price"),
                    "change1d": quote.get("changePercent"),
                    "volume24h": quote.get("volume24h"),
                    "nextEvent": top_link.get("title") or f"{company} market watch",
                    "nextEventAt": top_link.get("endDate"),
                    "eventType": event_type,
                    "eventTone": event_tone,
                    "badges": badges[:5],
                    "linkedMarkets": linked[:4],
                    "pmktGapScore": sum(abs(_safe_float(item.get("change24h")) or 0.0) for item in linked) + len(linked) * 0.25,
                }
            )
        rows.sort(key=lambda item: (_safe_float(item.get("pmktGapScore")) or 0.0, abs(_safe_float(item.get("change1d")) or 0.0)), reverse=True)
        top = rows[0] if rows else None
        return {
            "generatedAt": _now(dependencies),
            "status": "ok" if rows else "warming",
            "sources": {"quotes": "ok" if quotes else "warming", "earnings": "marker", "filings": "marker", "pmkt": "ok" if groups else "empty"},
            "summary": {
                "trackedCount": len(rows),
                "catalystCount": sum(1 for item in rows if item.get("linkedMarkets")),
                "topSymbol": top.get("symbol") if top else None,
                "signal": "EQUITY WATCH" if rows else "EQUITY WARMING",
            },
            "items": rows[:limit],
        }

    return _cached(
        dependencies,
        "runtime:finance:equity-event-command",
        f"v3:{limit}",
        _builder,
    )


def get_onchain_tradfi_perp_radar_snapshot(
    ctx: Mapping[str, Any],
    limit: int = DEFAULT_TRADFI_PERP_LIMIT,
) -> Dict[str, Any]:
    return _get_onchain_tradfi_perp_radar_snapshot(
        FinancePanelDependencies.from_context(ctx),
        limit=limit,
    )


def _get_onchain_tradfi_perp_radar_snapshot(
    dependencies: FinancePanelDependencies,
    *,
    limit: int = DEFAULT_TRADFI_PERP_LIMIT,
) -> Dict[str, Any]:
    limit = max(4, min(24, int(limit or DEFAULT_TRADFI_PERP_LIMIT)))

    def _builder() -> Dict[str, Any]:
        external = _external_sources(dependencies)
        equity_payload = _get_equity_event_command_snapshot(
            dependencies,
            limit=limit,
        )
        rows: List[Dict[str, Any]] = []
        try:
            if dependencies.get_crypto_funding_watch_snapshot is None:
                raise RuntimeError("get_crypto_funding_watch_snapshot helper missing")
            funding_payload = dependencies.get_crypto_funding_watch_snapshot(limit=8)
        except Exception:
            funding_payload = {}
        crypto_assets = [asset for asset in (funding_payload or {}).get("assets", []) if isinstance(asset, dict)]
        seeded_perps = [
            item
            for item in (((external.get("tradfiPerps") or {}).get("items")) or [])
            if isinstance(item, dict)
        ]

        for item in seeded_perps:
            symbol = str(item.get("symbol") or "").upper()
            if symbol not in TRADFI_PERP_SYMBOLS:
                continue
            funding = _safe_float(item.get("funding"))
            rows.append(
                {
                    "symbol": symbol,
                    "display": item.get("display") or f"{symbol}-PERP",
                    "assetClass": item.get("assetClass") or ("crypto" if symbol in {"BTC", "ETH", "SOL"} else "stock"),
                    "markPx": item.get("markPx"),
                    "oraclePx": item.get("oraclePx"),
                    "spotPx": item.get("spotPx"),
                    "basisBps": item.get("basisBps"),
                    "funding": funding,
                    "openInterest": item.get("openInterest"),
                    "dayNotional": item.get("dayNotional"),
                    "compositeScore": abs(funding or 0.0) * 1000 + min((_safe_float(item.get("dayNotional")) or 0.0) / 100000000.0, 5.0),
                    "alerts": list(dict.fromkeys((item.get("alerts") or []) + ["SEEDED"])),
                    "linkedMarkets": [],
                }
            )

        for item in (equity_payload.get("items") or [])[:limit]:
            symbol = str(item.get("symbol") or "").upper()
            if symbol not in TRADFI_PERP_SYMBOLS:
                continue
            change = _safe_float(item.get("change1d"))
            rows.append(
                {
                    "symbol": symbol,
                    "display": f"{symbol}-PERP",
                    "assetClass": "stock" if symbol not in {"SPY", "QQQ"} else "index",
                    "markPx": item.get("price"),
                    "oraclePx": item.get("price"),
                    "spotPx": item.get("price"),
                    "basisBps": None,
                    "funding": None,
                    "openInterest": None,
                    "dayNotional": item.get("volume24h"),
                    "compositeScore": abs(change or 0.0) + (_safe_float(item.get("pmktGapScore")) or 0.0),
                    "alerts": ["PMKT LINK"] + (["STOCK MOVE"] if change and abs(change) >= 2.0 else []),
                    "linkedMarkets": item.get("linkedMarkets") or [],
                }
            )

        for asset in crypto_assets:
            symbol = str(asset.get("asset") or asset.get("symbol") or "").upper()
            if symbol not in TRADFI_PERP_SYMBOLS:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "display": f"{symbol}-PERP",
                    "assetClass": "crypto",
                    "markPx": None,
                    "oraclePx": None,
                    "spotPx": None,
                    "basisBps": None,
                    "funding": asset.get("consensusFundingPercent"),
                    "openInterest": asset.get("openInterest"),
                    "dayNotional": asset.get("dayNotional"),
                    "compositeScore": abs(_safe_float(asset.get("maxAbsFundingPercent")) or 0.0),
                    "alerts": [str(asset.get("bias") or "FUNDING").upper()],
                    "linkedMarkets": [],
                }
            )

        seen = set()
        unique_rows = []
        for row in sorted(rows, key=lambda item: _safe_float(item.get("compositeScore")) or 0.0, reverse=True):
            symbol = row.get("symbol")
            if symbol in seen:
                continue
            seen.add(symbol)
            unique_rows.append(row)
        top = unique_rows[0] if unique_rows else None
        external_sources = external.get("sources") if isinstance(external.get("sources"), dict) else {}
        return {
            "generatedAt": _now(dependencies),
            "status": "ok" if unique_rows else "warming",
            "sources": {
                "hyperliquid": external_sources.get("hyperliquid") or ("funding" if crypto_assets else "warming"),
                "tradexyz": external_sources.get("tradexyz") or ("proxy" if seeded_perps else "warming"),
                "quotes": "ok" if equity_payload.get("items") else "warming",
                "pmkt": "linked",
            },
            "summary": {
                "assetCount": len(unique_rows),
                "alertCount": sum(len(item.get("alerts") or []) for item in unique_rows),
                "topSymbol": top.get("symbol") if top else None,
                "signal": "PERP RADAR" if unique_rows else "PERP WARMING",
            },
            "items": unique_rows[:limit],
        }

    return _cached(
        dependencies,
        "runtime:finance:onchain-tradfi-perp-radar",
        f"v3:{limit}",
        _builder,
        ttl_seconds=45,
    )


def get_finance_liquidity_regime_snapshot(
    ctx: Mapping[str, Any],
    limit: int = DEFAULT_LIQUIDITY_REGIME_LIMIT,
) -> Dict[str, Any]:
    return _get_finance_liquidity_regime_snapshot(
        FinancePanelDependencies.from_context(ctx),
        limit=limit,
    )


def _get_finance_liquidity_regime_snapshot(
    dependencies: FinancePanelDependencies,
    *,
    limit: int = DEFAULT_LIQUIDITY_REGIME_LIMIT,
) -> Dict[str, Any]:
    limit = max(4, min(24, int(limit or DEFAULT_LIQUIDITY_REGIME_LIMIT)))

    def _builder() -> Dict[str, Any]:
        external = _external_sources(dependencies)
        atlas = _get_finance_market_atlas_snapshot(
            dependencies,
            limit=max(limit, 16),
        )
        perp = _get_onchain_tradfi_perp_radar_snapshot(
            dependencies,
            limit=limit,
        )
        markets = [item for item in (atlas.get("items") or []) if isinstance(item, dict)]
        cot_items = [item for item in (((external.get("cot") or {}).get("items")) or []) if isinstance(item, dict)]
        etf_items = [item for item in (((external.get("etfFlow") or {}).get("items")) or []) if isinstance(item, dict)]
        stablecoin_items = [item for item in (((external.get("stablecoin") or {}).get("items")) or []) if isinstance(item, dict)]
        volume = sum(_safe_float(item.get("volume24h")) or 0.0 for item in markets)
        flow_rows = [item for item in markets if (_safe_float(item.get("volume24h")) or 0.0) > 25000]
        perp_alerts = sum(len(item.get("alerts") or []) for item in (perp.get("items") or []))
        etf_net_flow = _safe_float((external.get("etfFlow") or {}).get("netFlowProxyUsd"))
        stablecoin_supply_change = _safe_float((external.get("stablecoin") or {}).get("supplyChange7dPct"))
        cot_avg_net = None
        cot_nets = [_safe_float(item.get("netPctOpenInterest")) for item in cot_items]
        cot_nets = [item for item in cot_nets if item is not None]
        if cot_nets:
            cot_avg_net = sum(cot_nets) / len(cot_nets)
        external_score = 0
        if etf_net_flow is not None:
            external_score += 4 if etf_net_flow > 0 else -4
        if stablecoin_supply_change is not None:
            external_score += max(-5, min(5, stablecoin_supply_change * 2))
        if cot_avg_net is not None:
            external_score += max(-5, min(5, cot_avg_net / 5))
        regime_score = min(100, max(0, int(35 + min(volume / 50000.0, 35) + len(flow_rows) * 3 + min(perp_alerts * 2, 12) + external_score)))
        if regime_score >= 70:
            label = "RISK-ON"
            signal = "LIQUIDITY IMPROVING"
        elif regime_score >= 52:
            label = "FRAGILE"
            signal = "LIQUIDITY MIXED"
        else:
            label = "STRESS"
            signal = "LIQUIDITY THIN"
        components = [
            {"key": "pmktDepth", "label": "PMKT Volume", "value": volume, "tone": "ok" if volume else "watch", "detail": f"{len(markets)} finance markets"},
            {"key": "perpOi", "label": "Perp Alerts", "value": perp_alerts, "tone": "watch" if perp_alerts else "neutral", "detail": "funding/OI proxy"},
            {"key": "cot", "label": "COT Positioning", "value": cot_avg_net, "tone": "ok" if cot_items else "missing", "detail": f"{len(cot_items)} CFTC rows" if cot_items else "seed pending"},
            {"key": "etfFlow", "label": "ETF Flow", "value": etf_net_flow, "tone": "ok" if etf_net_flow and etf_net_flow > 0 else ("watch" if etf_net_flow is not None else "missing"), "detail": f"{len(etf_items)} ETF proxies" if etf_items else "seed pending"},
            {"key": "stablecoin", "label": "Stablecoin", "value": stablecoin_supply_change, "tone": "ok" if stablecoin_items else "missing", "detail": f"{len(stablecoin_items)} pegs" if stablecoin_items else "seed pending"},
        ]
        rows: List[Dict[str, Any]] = []
        for market in markets[:limit]:
            rows.append(
                {
                    "id": str(market.get("marketId") or market.get("title") or ""),
                    "label": market.get("title"),
                    "source": "PMKT",
                    "signal": "FLOW" if (_safe_float(market.get("volume24h")) or 0.0) > 25000 else "WATCH",
                    "value": market.get("volume24h"),
                    "tone": "ok" if (_safe_float(market.get("volume24h")) or 0.0) > 25000 else "neutral",
                    "linkedMarket": market,
                }
            )
        for item in cot_items[:3]:
            rows.append(
                {
                    "id": f"cot:{item.get('symbol')}",
                    "label": item.get("market") or f"{item.get('symbol')} COT positioning",
                    "source": "COT",
                    "signal": "POSITIONING",
                    "value": item.get("netPctOpenInterest"),
                    "tone": "ok" if (_safe_float(item.get("netPctOpenInterest")) or 0.0) >= 0 else "watch",
                    "linkedMarket": None,
                }
            )
        for item in etf_items[:3]:
            rows.append(
                {
                    "id": f"etf:{item.get('symbol')}",
                    "label": f"{item.get('symbol')} ETF flow proxy",
                    "source": "ETF",
                    "signal": "FLOW",
                    "value": item.get("flowProxyUsd"),
                    "tone": "ok" if (_safe_float(item.get("flowProxyUsd")) or 0.0) >= 0 else "watch",
                    "linkedMarket": None,
                }
            )
        for item in stablecoin_items[:2]:
            rows.append(
                {
                    "id": f"stablecoin:{item.get('symbol')}",
                    "label": f"{item.get('symbol')} stablecoin supply / peg",
                    "source": "STBL",
                    "signal": "PEG",
                    "value": item.get("change7dPct"),
                    "tone": "ok" if abs(_safe_float(item.get("deviationBps")) or 0.0) < 50 else "watch",
                    "linkedMarket": None,
                }
            )
        external_sources = external.get("sources") if isinstance(external.get("sources"), dict) else {}
        return {
            "generatedAt": _now(dependencies),
            "status": "ok" if rows else "warming",
            "sources": {
                "pmkt": "ok" if markets else "empty",
                "perp": perp.get("status") or "warming",
                "cot": external_sources.get("cot") or ("ok" if cot_items else "seed-miss"),
                "etf": external_sources.get("etfFlow") or ("ok" if etf_items else "seed-miss"),
                "stablecoin": external_sources.get("stablecoin") or ("ok" if stablecoin_items else "seed-miss"),
            },
            "summary": {
                "regimeLabel": label,
                "regimeScore": regime_score,
                "alertCount": sum(1 for row in rows if row.get("signal") == "FLOW") + perp_alerts,
                "signal": signal,
            },
            "components": components,
            "items": rows[:limit],
        }

    return _cached(
        dependencies,
        "runtime:finance:liquidity-regime",
        f"v3:{limit}",
        _builder,
        ttl_seconds=45,
    )
