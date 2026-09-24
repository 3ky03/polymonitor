from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
)

try:
    import requests
except ImportError:  # pragma: no cover - runtime watcher validates dependency.
    requests = None


FINANCE_EXTERNAL_NAMESPACE = "runtime:finance:external-sources"
FINANCE_EXTERNAL_CACHE_KEY = "v1"
FINANCE_EXTERNAL_TTL_SECONDS = 15 * 60

ETF_FLOW_SYMBOLS: Tuple[Tuple[str, str], ...] = (
    ("IBIT", "iShares Bitcoin Trust"),
    ("FBTC", "Fidelity Wise Origin Bitcoin Fund"),
    ("GBTC", "Grayscale Bitcoin Trust"),
    ("BITB", "Bitwise Bitcoin ETF"),
    ("ARKB", "ARK 21Shares Bitcoin ETF"),
    ("HODL", "VanEck Bitcoin Trust"),
    ("BRRR", "Valkyrie Bitcoin Fund"),
    ("EZBC", "Franklin Bitcoin ETF"),
    ("BTCO", "Invesco Galaxy Bitcoin ETF"),
    ("BTCW", "WisdomTree Bitcoin Fund"),
    ("ETHA", "iShares Ethereum Trust"),
    ("FETH", "Fidelity Ethereum Fund"),
)

TRADFI_PERP_SYMBOLS: Tuple[Tuple[str, str, str], ...] = (
    ("SPY-USDT-SWAP", "SPY-PERP", "index"),
    ("XAU-USDT-SWAP", "GOLD-PERP", "commodity"),
    ("CL-USDT-SWAP", "WTI-PERP", "commodity"),
    ("IWM-USDT-SWAP", "RUSSELL-ETF-PERP", "index"),
    ("EWJ-USDT-SWAP", "JAPAN-ETF-PERP", "index"),
    ("EWY-USDT-SWAP", "KOREA-ETF-PERP", "index"),
    ("NVDA-USDT-SWAP", "NVDA-PERP", "stock"),
    ("AMD-USDT-SWAP", "AMD-PERP", "stock"),
    ("AAPL-USDT-SWAP", "AAPL-PERP", "stock"),
    ("MSFT-USDT-SWAP", "MSFT-PERP", "stock"),
    ("AMZN-USDT-SWAP", "AMZN-PERP", "stock"),
    ("GOOGL-USDT-SWAP", "GOOGL-PERP", "stock"),
    ("META-USDT-SWAP", "META-PERP", "stock"),
    ("TSLA-USDT-SWAP", "TSLA-PERP", "stock"),
    ("COIN-USDT-SWAP", "COIN-PERP", "stock"),
    ("MSTR-USDT-SWAP", "MSTR-PERP", "stock"),
    ("HOOD-USDT-SWAP", "HOOD-PERP", "stock"),
    ("SPACEX-USDT-SWAP", "SPACEX-PERP", "private"),
)

TRADFI_REFERENCE_SYMBOLS: Tuple[Tuple[str, str, str], ...] = (
    ("ES=F", "S&P500-PERP", "index"),
    ("NQ=F", "NASDAQ-PERP", "index"),
    ("YM=F", "DOW-PERP", "index"),
    ("RTY=F", "RUSSELL-PERP", "index"),
    ("GC=F", "GOLD-PERP", "commodity"),
    ("CL=F", "WTI-PERP", "commodity"),
)

COT_MARKETS: Tuple[Tuple[str, str, str], ...] = (
    ("ES", "E-MINI S&P 500", "Equity index"),
    ("NQ", "NASDAQ-100", "Equity index"),
    ("BTC", "BITCOIN", "Crypto"),
    ("CL", "CRUDE OIL", "Energy"),
    ("GC", "GOLD", "Metals"),
)

STABLECOIN_SYMBOLS: Tuple[str, ...] = ("USDT", "USDC", "USDS", "DAI", "PYUSD", "FDUSD", "TUSD")


@dataclass(frozen=True)
class FinanceExternalSourceDependencies:
    settings: Any
    http_json_get: Callable[..., Any] | None
    http_json_post: Callable[..., Any] | None
    get_yahoo_market_snapshot: Callable[..., Any] | None
    get_cached_json: Callable[..., Any] | None
    snapshot_store: Any

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> FinanceExternalSourceDependencies:
        return cls(
            settings=resolve_optional_service_value(context, "SETTINGS"),
            http_json_get=resolve_optional_service_callable(
                context,
                "http_json_get",
            ),
            http_json_post=resolve_optional_service_callable(
                context,
                "http_json_post",
            ),
            get_yahoo_market_snapshot=resolve_optional_service_callable(
                context,
                "get_yahoo_market_snapshot",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
        )


FinanceExternalSourceContext = (
    Mapping[str, Any] | FinanceExternalSourceDependencies
)


def _dependencies(
    context: FinanceExternalSourceContext,
) -> FinanceExternalSourceDependencies:
    if isinstance(context, FinanceExternalSourceDependencies):
        return context
    return FinanceExternalSourceDependencies.from_context(context)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _setting(
    dependencies: FinanceExternalSourceDependencies,
    name: str,
) -> str:
    value = getattr(dependencies.settings, name, "") if dependencies.settings is not None else ""
    return str(value or "").strip()


def _source_url_from_template(template: str, placeholder: str = "{symbol}") -> str:
    text = str(template or "").strip()
    if not text:
        return ""
    return text.replace(f"/{placeholder}", "").replace(placeholder, "").rstrip("?&/")


def _pct_change(current: Any, previous: Any) -> Optional[float]:
    current_float = _safe_float(current)
    previous_float = _safe_float(previous)
    if current_float is None or previous_float in (None, 0):
        return None
    return ((current_float - float(previous_float)) / float(previous_float)) * 100


def _nested_usd(row: Dict[str, Any], key: str) -> Optional[float]:
    payload = row.get(key)
    if not isinstance(payload, dict):
        return None
    return _safe_float(payload.get("peggedUSD"))


def _http_get(
    dependencies: FinanceExternalSourceDependencies,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 12,
) -> Any:
    if dependencies.http_json_get is not None:
        try:
            return dependencies.http_json_get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "polydata-finance-seed/1.0"},
            )
        except Exception:
            pass
    if requests is None:
        raise RuntimeError("requests package is required")
    session = requests.Session()
    session.trust_env = True
    try:
        response = session.get(url, params=params, timeout=timeout, headers={"User-Agent": "polydata-finance-seed/1.0"})
        response.raise_for_status()
        return response.json() if response.content else None
    finally:
        session.close()


def _http_post(
    dependencies: FinanceExternalSourceDependencies,
    url: str,
    *,
    json_payload: Dict[str, Any],
    timeout: int = 12,
) -> Any:
    if dependencies.http_json_post is not None:
        try:
            return dependencies.http_json_post(
                url,
                json_payload=json_payload,
                timeout=timeout,
                headers={"User-Agent": "polydata-finance-seed/1.0"},
            )
        except Exception:
            pass
    if requests is None:
        raise RuntimeError("requests package is required")
    session = requests.Session()
    session.trust_env = True
    try:
        response = session.post(url, json=json_payload, timeout=timeout, headers={"User-Agent": "polydata-finance-seed/1.0"})
        response.raise_for_status()
        return response.json() if response.content else None
    finally:
        session.close()


def _fetch_yahoo_snapshot(
    dependencies: FinanceExternalSourceDependencies,
    symbol: str,
) -> Optional[Dict[str, Any]]:
    try:
        if dependencies.get_yahoo_market_snapshot is None:
            raise RuntimeError("get_yahoo_market_snapshot helper missing")
        quote = dependencies.get_yahoo_market_snapshot(
            symbol,
            interval="30m",
            range_name="5d",
            ttl_seconds=300,
        )
    except Exception:
        quote = None
    if isinstance(quote, dict) and quote.get("price") is not None:
        return quote
    payload = _http_get(
        dependencies,
        _setting(
            dependencies,
            "finance_yahoo_chart_url_template",
        ).format(symbol=symbol),
        params={"range": "5d", "interval": "1d"},
        timeout=12,
    )
    result = (payload.get("chart") or {}).get("result") if isinstance(payload, dict) else []
    chart = result[0] if isinstance(result, list) and result else {}
    quote_rows = (chart.get("indicators") or {}).get("quote") or []
    quote_data = quote_rows[0] if quote_rows and isinstance(quote_rows[0], dict) else {}
    closes = [value for value in (quote_data.get("close") or []) if value is not None]
    volumes = [value for value in (quote_data.get("volume") or []) if value is not None]
    if not closes:
        return None
    price = _safe_float(closes[-1])
    previous = _safe_float(closes[-2]) if len(closes) >= 2 else None
    return {
        "price": price,
        "changePercent": _pct_change(price, previous),
        "volume24h": _safe_float(volumes[-1]) if volumes else None,
    }


def read_finance_external_sources(
    ctx: FinanceExternalSourceContext,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    if dependencies.get_cached_json is not None:
        payload = dependencies.get_cached_json(
            FINANCE_EXTERNAL_NAMESPACE,
            FINANCE_EXTERNAL_CACHE_KEY,
        )
        if isinstance(payload, dict):
            return payload
    if dependencies.snapshot_store is not None:
        payload = dependencies.snapshot_store.get_stale(
            FINANCE_EXTERNAL_NAMESPACE,
            FINANCE_EXTERNAL_CACHE_KEY,
        )
        if isinstance(payload, dict):
            return payload
    return {}


def _source_status(items: Iterable[Any], *, degraded_if_empty: bool = True) -> str:
    count = len(list(items))
    if count:
        return "ok"
    return "degraded" if degraded_if_empty else "empty"


def fetch_hyperliquid_perp_source(
    ctx: FinanceExternalSourceContext,
) -> Dict[str, Any]:
    return _fetch_hyperliquid_perp_source(
        _dependencies(ctx),
    )


def _fetch_hyperliquid_perp_source(
    dependencies: FinanceExternalSourceDependencies,
) -> Dict[str, Any]:
    source_url = _setting(dependencies, "finance_hyperliquid_info_url")
    payload = _http_post(
        dependencies,
        source_url,
        json_payload={"type": "metaAndAssetCtxs"},
        timeout=15,
    )
    if not isinstance(payload, list) or len(payload) < 2:
        return {"status": "degraded", "items": [], "error": "unexpected Hyperliquid response"}
    universe = (payload[0] or {}).get("universe") or []
    asset_contexts = payload[1] or []
    wanted = {symbol for symbol, _name, _asset_class in TRADFI_PERP_SYMBOLS}
    rows: List[Dict[str, Any]] = []
    for meta, asset_ctx in zip(universe, asset_contexts):
        if not isinstance(meta, dict) or not isinstance(asset_ctx, dict):
            continue
        symbol = str(meta.get("name") or "").upper()
        if symbol not in wanted:
            continue
        rows.append(
            {
                "symbol": symbol,
                "display": f"{symbol}-PERP",
                "assetClass": next((asset_class for item_symbol, _name, asset_class in TRADFI_PERP_SYMBOLS if item_symbol == symbol), "crypto"),
                "markPx": _safe_float(asset_ctx.get("markPx")),
                "oraclePx": _safe_float(asset_ctx.get("oraclePx")),
                "spotPx": _safe_float(asset_ctx.get("midPx") or asset_ctx.get("markPx")),
                "basisBps": None,
                "funding": _safe_float(asset_ctx.get("funding")),
                "openInterest": _safe_float(asset_ctx.get("openInterest")),
                "dayNotional": _safe_float(asset_ctx.get("dayNtlVlm")),
                "source": "hyperliquid",
                "alerts": ["HYPER"],
            }
        )
    return {"status": _source_status(rows), "items": rows, "sourceUrl": source_url}


def fetch_okx_tradfi_perp_source(
    ctx: FinanceExternalSourceContext,
) -> Dict[str, Any]:
    return _fetch_okx_tradfi_perp_source(
        _dependencies(ctx),
    )


def _fetch_okx_tradfi_perp_source(
    dependencies: FinanceExternalSourceDependencies,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    source_url = _setting(dependencies, "finance_okx_market_ticker_url")
    for inst_id, display, asset_class in TRADFI_PERP_SYMBOLS:
        try:
            payload = _http_get(
                dependencies,
                source_url,
                params={"instId": inst_id},
                timeout=12,
            )
        except Exception:
            payload = None
        data = payload.get("data") if isinstance(payload, dict) else []
        ticker = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        mark = _safe_float(ticker.get("last"))
        open_24h = _safe_float(ticker.get("open24h"))
        change_pct = _pct_change(mark, open_24h)
        if mark is None:
            continue
        rows.append(
            {
                "symbol": inst_id,
                "display": display,
                "assetClass": asset_class,
                "markPx": mark,
                "oraclePx": None,
                "spotPx": mark,
                "basisBps": None,
                "funding": change_pct,
                "openInterest": None,
                "dayNotional": _safe_float(ticker.get("volCcy24h")),
                "changePercent": change_pct,
                "source": "okx-swap",
                "venue": "OKX",
                "alerts": ["OKX", "PERP"],
            }
        )
    return {"status": _source_status(rows), "items": rows, "sourceUrl": source_url}


def fetch_reference_tradfi_perp_source(
    ctx: FinanceExternalSourceContext,
) -> Dict[str, Any]:
    return _fetch_reference_tradfi_perp_source(
        _dependencies(ctx),
    )


def _fetch_reference_tradfi_perp_source(
    dependencies: FinanceExternalSourceDependencies,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for yahoo_symbol, display, asset_class in TRADFI_REFERENCE_SYMBOLS:
        quote = _fetch_yahoo_snapshot(dependencies, yahoo_symbol)
        if not isinstance(quote, dict):
            continue
        price = _safe_float(quote.get("price"))
        if price is None:
            continue
        rows.append(
            {
                "symbol": yahoo_symbol,
                "display": display,
                "assetClass": asset_class,
                "markPx": price,
                "oraclePx": price,
                "spotPx": price,
                "basisBps": 0.0,
                "funding": _safe_float(quote.get("changePercent")),
                "openInterest": None,
                "dayNotional": _safe_float(quote.get("volume24h")),
                "changePercent": _safe_float(quote.get("changePercent")),
                "source": "yahoo-reference",
                "venue": "REFERENCE",
                "alerts": ["REF", "PERP"],
            }
        )
    return {
        "status": _source_status(rows),
        "items": rows,
        "sourceUrl": _source_url_from_template(
            _setting(dependencies, "finance_yahoo_chart_url_template"),
        ),
    }


def fetch_etf_flow_source(
    ctx: FinanceExternalSourceContext,
) -> Dict[str, Any]:
    return _fetch_etf_flow_source(
        _dependencies(ctx),
    )


def _fetch_etf_flow_source(
    dependencies: FinanceExternalSourceDependencies,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for symbol, issuer in ETF_FLOW_SYMBOLS:
        quote = _fetch_yahoo_snapshot(dependencies, symbol)
        if not isinstance(quote, dict):
            continue
        change_pct = _safe_float(quote.get("changePercent"))
        volume = _safe_float(quote.get("volume24h"))
        price = _safe_float(quote.get("price"))
        flow_proxy = None
        if change_pct is not None and volume is not None and price is not None:
            flow_proxy = (change_pct / 100.0) * volume * price
        rows.append(
            {
                "symbol": symbol,
                "issuer": issuer,
                "price": price,
                "changePercent": change_pct,
                "volume": volume,
                "flowProxyUsd": flow_proxy,
                "source": "yahoo-flow-proxy",
            }
        )
    net_flow_proxy = sum(_safe_float(row.get("flowProxyUsd")) or 0.0 for row in rows)
    return {
        "status": _source_status(rows),
        "netFlowProxyUsd": net_flow_proxy,
        "items": sorted(rows, key=lambda item: abs(_safe_float(item.get("flowProxyUsd")) or 0.0), reverse=True),
        "sourceUrl": _source_url_from_template(
            _setting(dependencies, "finance_yahoo_chart_url_template"),
        ),
    }


def build_etf_flow_proxy_from_perps(
    ctx: FinanceExternalSourceContext,
    hyperliquid_payload: Dict[str, Any],
) -> Dict[str, Any]:
    return _build_etf_flow_proxy_from_perps(
        _dependencies(ctx),
        hyperliquid_payload,
    )


def _build_etf_flow_proxy_from_perps(
    dependencies: FinanceExternalSourceDependencies,
    hyperliquid_payload: Dict[str, Any],
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for item in hyperliquid_payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        if symbol not in {"BTC", "ETH"}:
            continue
        funding = _safe_float(item.get("funding")) or 0.0
        day_notional = _safe_float(item.get("dayNotional")) or 0.0
        rows.append(
            {
                "symbol": f"{symbol}-ETF",
                "issuer": f"{symbol} ETF demand proxy",
                "price": item.get("markPx"),
                "changePercent": None,
                "volume": day_notional,
                "flowProxyUsd": funding * day_notional,
                "source": "hyperliquid-etf-proxy",
            }
        )
    net_flow_proxy = sum(_safe_float(row.get("flowProxyUsd")) or 0.0 for row in rows)
    return {
        "status": "proxy" if rows else "degraded",
        "netFlowProxyUsd": net_flow_proxy,
        "items": rows,
        "sourceUrl": _setting(dependencies, "finance_hyperliquid_info_url"),
        "note": "Yahoo ETF quotes unavailable; using BTC/ETH perp funding notional as an ETF demand proxy.",
    }


def fetch_cot_source(
    ctx: FinanceExternalSourceContext,
) -> Dict[str, Any]:
    return _fetch_cot_source(
        _dependencies(ctx),
    )


def _fetch_cot_source(
    dependencies: FinanceExternalSourceDependencies,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    source_url = _setting(dependencies, "finance_cftc_legacy_cot_url")
    for symbol, query_term, asset_class in COT_MARKETS:
        try:
            payload = _http_get(
                dependencies,
                source_url,
                params={
                    "$limit": 1,
                    "$order": "report_date_as_yyyy_mm_dd DESC",
                    "$where": f"upper(market_and_exchange_names) like '%{query_term}%'",
                },
                timeout=15,
            )
        except Exception:
            payload = []
        row = payload[0] if isinstance(payload, list) and payload else {}
        if not isinstance(row, dict):
            continue
        noncomm_long = _safe_float(row.get("noncomm_positions_long_all"))
        noncomm_short = _safe_float(row.get("noncomm_positions_short_all"))
        open_interest = _safe_float(row.get("open_interest_all"))
        net = None
        net_pct_oi = None
        if noncomm_long is not None and noncomm_short is not None:
            net = noncomm_long - noncomm_short
            if open_interest not in (None, 0):
                net_pct_oi = (net / float(open_interest)) * 100
        rows.append(
            {
                "symbol": symbol,
                "assetClass": asset_class,
                "market": row.get("market_and_exchange_names"),
                "reportDate": row.get("report_date_as_yyyy_mm_dd"),
                "openInterest": open_interest,
                "nonCommercialLong": noncomm_long,
                "nonCommercialShort": noncomm_short,
                "nonCommercialNet": net,
                "netPctOpenInterest": net_pct_oi,
                "source": "cftc-cot",
            }
        )
    return {"status": _source_status(rows), "items": rows, "sourceUrl": source_url}


def fetch_stablecoin_source(
    ctx: FinanceExternalSourceContext,
) -> Dict[str, Any]:
    return _fetch_stablecoin_source(
        _dependencies(ctx),
    )


def _fetch_stablecoin_source(
    dependencies: FinanceExternalSourceDependencies,
) -> Dict[str, Any]:
    source_url = _setting(dependencies, "finance_defillama_stablecoins_url")
    payload = _http_get(
        dependencies,
        source_url,
        params={"includePrices": "true"},
        timeout=15,
    )
    assets = payload.get("peggedAssets") if isinstance(payload, dict) else []
    rows: List[Dict[str, Any]] = []
    seen_symbols = set()
    for asset in assets or []:
        if not isinstance(asset, dict):
            continue
        symbol = str(asset.get("symbol") or "").upper()
        if symbol not in STABLECOIN_SYMBOLS:
            continue
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        current = _nested_usd(asset, "circulating")
        prev_day = _nested_usd(asset, "circulatingPrevDay")
        prev_week = _nested_usd(asset, "circulatingPrevWeek")
        price = _safe_float(asset.get("price"))
        deviation_bps = None
        if price is not None:
            deviation_bps = (price - 1.0) * 10000
        rows.append(
            {
                "symbol": symbol,
                "name": asset.get("name"),
                "price": price,
                "deviationBps": deviation_bps,
                "supplyUsd": current,
                "change1dPct": _pct_change(current, prev_day),
                "change7dPct": _pct_change(current, prev_week),
                "pegMechanism": asset.get("pegMechanism"),
                "source": "defillama",
            }
        )
    rows.sort(key=lambda item: _safe_float(item.get("supplyUsd")) or 0.0, reverse=True)
    total_supply = sum(_safe_float(row.get("supplyUsd")) or 0.0 for row in rows)
    total_prev_week = None
    if rows:
        weighted = [
            (_safe_float(row.get("change7dPct")) or 0.0, _safe_float(row.get("supplyUsd")) or 0.0)
            for row in rows
        ]
        denominator = sum(weight for _change, weight in weighted)
        total_prev_week = sum(change * weight for change, weight in weighted) / denominator if denominator else None
    stressed = sum(1 for row in rows if abs(_safe_float(row.get("deviationBps")) or 0.0) >= 50)
    return {
        "status": _source_status(rows),
        "totalSupplyUsd": total_supply,
        "supplyChange7dPct": total_prev_week,
        "stressedCount": stressed,
        "items": rows,
        "sourceUrl": source_url,
    }


def build_finance_external_sources_payload(
    ctx: FinanceExternalSourceContext,
) -> Dict[str, Any]:
    return _build_finance_external_sources_payload(
        _dependencies(ctx),
    )


def _build_finance_external_sources_payload(
    dependencies: FinanceExternalSourceDependencies,
) -> Dict[str, Any]:
    sources: Dict[str, str] = {}
    errors: Dict[str, str] = {}

    def capture(
        name: str,
        builder: Callable[[FinanceExternalSourceDependencies], Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            payload = builder(dependencies)
            status = str(payload.get("status") or "unknown")
            sources[name] = status
            return payload
        except Exception as exc:
            sources[name] = "error"
            errors[name] = str(exc)
            return {"status": "error", "items": [], "error": str(exc)}

    hyperliquid = capture("hyperliquid", _fetch_hyperliquid_perp_source)
    okx_tradfi = capture("okxTradfiPerps", _fetch_okx_tradfi_perp_source)
    reference_tradfi = capture(
        "referenceTradfiPerps",
        _fetch_reference_tradfi_perp_source,
    )
    tradfi_items = []
    seen_tradfi = set()
    for source in (okx_tradfi, reference_tradfi):
        for item in source.get("items") or []:
            key = str(item.get("display") or item.get("symbol") or "")
            if key and key not in seen_tradfi:
                tradfi_items.append(item)
                seen_tradfi.add(key)
    tradfi_perps = {
        "status": _source_status(tradfi_items),
        "items": tradfi_items,
        "sourceUrl": _setting(dependencies, "finance_okx_market_ticker_url"),
        "sources": {"okx": okx_tradfi.get("status"), "reference": reference_tradfi.get("status")},
    }
    etf_flow = capture("etfFlow", _fetch_etf_flow_source)
    if not (etf_flow.get("items") or []):
        etf_flow = _build_etf_flow_proxy_from_perps(
            dependencies,
            hyperliquid,
        )
        sources["etfFlow"] = str(etf_flow.get("status") or "proxy")
    cot = capture("cot", _fetch_cot_source)
    stablecoin = capture("stablecoin", _fetch_stablecoin_source)
    # trade.xyz does not currently expose a documented public market-data endpoint
    # in the same way Hyperliquid does; keep a seeded source state so the panel can
    # distinguish "seeded proxy" from "seed pending".
    sources["tradexyz"] = "proxy" if hyperliquid.get("items") or etf_flow.get("items") else "degraded"

    ok_count = sum(1 for status in sources.values() if status in {"ok", "proxy"})
    status = "ok" if ok_count >= 3 and not errors else ("partial" if ok_count else "degraded")
    return {
        "generatedAt": utc_now_iso(),
        "status": status,
        "cacheMode": "seeded",
        "sources": sources,
        "errors": errors,
        "tradfiPerps": tradfi_perps,
        "etfFlow": etf_flow,
        "cot": cot,
        "stablecoin": stablecoin,
            "summary": {
            "perpCount": len(tradfi_perps.get("items") or []),
            "etfCount": len(etf_flow.get("items") or []),
            "cotCount": len(cot.get("items") or []),
            "stablecoinCount": len(stablecoin.get("items") or []),
            "stablecoinSupplyUsd": stablecoin.get("totalSupplyUsd"),
            "etfNetFlowProxyUsd": etf_flow.get("netFlowProxyUsd"),
        },
    }
