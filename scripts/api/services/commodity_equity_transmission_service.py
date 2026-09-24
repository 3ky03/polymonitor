from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
    resolve_service_callable,
)


PANEL_ID = "commodity-equity-transmission"


@dataclass(frozen=True)
class CommodityEquityTransmissionDependencies:
    get_market_group_snapshot: Callable[..., Any] | None
    commodity_symbols: Sequence[Any]
    search_markets: Callable[..., Any] | None
    application: Any
    utc_now_iso: Callable[..., Any]

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> CommodityEquityTransmissionDependencies:
        return cls(
            get_market_group_snapshot=resolve_optional_service_callable(
                context,
                "get_market_group_snapshot",
            ),
            commodity_symbols=resolve_optional_service_value(
                context,
                "COMMODITY_SYMBOLS",
                (),
            ),
            search_markets=resolve_optional_service_callable(
                context,
                "search_markets",
            ),
            application=resolve_optional_service_value(
                context,
                "app",
            ),
            utc_now_iso=resolve_service_callable(
                context,
                "utc_now_iso",
            ),
        )

    @property
    def logger(self) -> Any:
        return getattr(self.application, "logger", None)


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _change_label(value: Optional[float]) -> str:
    if value is None:
        return "--"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _tone_for_change(value: Optional[float]) -> str:
    if value is None:
        return "neutral"
    if value > 0.15:
        return "up"
    if value < -0.15:
        return "down"
    return "neutral"


def _exposure(
    ticker: str,
    name: str,
    role: str,
    direction: str,
    score: float,
    confidence: str,
    basis: str,
    market: str = "US",
) -> Dict[str, Any]:
    impact = "benefits from input price up" if direction == "positive" else (
        "cost pressure when input rises" if direction == "negative" else "margin depends on spread"
    )
    return {
        "ticker": ticker,
        "name": name,
        "market": market,
        "role": role,
        "direction": direction,
        "score": score,
        "impactLabel": impact,
        "confidence": confidence,
        "basis": basis,
    }


CHAIN_SPECS: List[Dict[str, Any]] = [
    {
        "id": "oil-refined-products",
        "commodityIds": ["oil", "brent", "gasoline"],
        "chainLabel": "Oil / refined products -> energy equities, airlines, logistics",
        "demandRegime": "inflation + travel demand",
        "lagLabel": "0-4w lag",
        "confidence": "high",
        "formula": "WTI/Brent move * fuel-cost exposure * pricing power; refiners stay spread-driven.",
        "winners": [
            _exposure("XOM", "Exxon Mobil", "producer", "positive", 0.78, "high", "upstream revenue mix"),
            _exposure("CVX", "Chevron", "producer", "positive", 0.72, "high", "upstream revenue mix"),
            _exposure("OXY", "Occidental", "producer", "positive", 0.69, "medium", "oil beta + revenue mix"),
        ],
        "losers": [
            _exposure("DAL", "Delta Air Lines", "consumer", "negative", -0.55, "high", "jet fuel cost share"),
            _exposure("UAL", "United Airlines", "consumer", "negative", -0.52, "high", "jet fuel cost share"),
            _exposure("FDX", "FedEx", "consumer", "negative", -0.31, "medium", "fuel + surcharge lag"),
        ],
        "spreadWatch": [
            _exposure("VLO", "Valero", "spread", "spread", 0.18, "high", "crack spread, not crude alone"),
            _exposure("MPC", "Marathon Petroleum", "spread", "spread", 0.16, "high", "crack spread, not crude alone"),
        ],
        "marketQueries": ["oil price", "gas prices", "inflation"],
    },
    {
        "id": "copper-electrification",
        "commodityIds": ["copper"],
        "chainLabel": "Copper -> miners, electrification hardware, EV cost pressure",
        "demandRegime": "grid + China demand",
        "lagLabel": "1-8w lag",
        "confidence": "high",
        "formula": "Copper move * metal input share * contract pass-through; miners benefit directly.",
        "winners": [
            _exposure("FCX", "Freeport-McMoRan", "producer", "positive", 0.82, "high", "copper revenue exposure"),
            _exposure("SCCO", "Southern Copper", "producer", "positive", 0.79, "high", "copper revenue exposure"),
            _exposure("TECK", "Teck Resources", "producer", "positive", 0.44, "medium", "diversified copper exposure"),
        ],
        "losers": [
            _exposure("TSLA", "Tesla", "consumer", "negative", -0.22, "medium", "EV wiring + power electronics"),
            _exposure("GM", "General Motors", "consumer", "negative", -0.18, "medium", "auto materials basket"),
            _exposure("ETN", "Eaton", "consumer", "negative", -0.12, "low", "pass-through partially offsets"),
        ],
        "spreadWatch": [
            _exposure("WIRE", "Encore Wire", "spread", "spread", 0.12, "medium", "wire spread and inventory timing"),
        ],
        "marketQueries": ["copper", "electric vehicles", "China economy"],
    },
    {
        "id": "aluminum-lightweighting",
        "commodityIds": ["aluminum"],
        "chainLabel": "Aluminum -> smelters, auto light-weighting, packaging",
        "demandRegime": "power cost + industrial demand",
        "lagLabel": "2-10w lag",
        "confidence": "medium",
        "formula": "Aluminum move * material share * power-cost offset; smelters need energy spread.",
        "winners": [
            _exposure("AA", "Alcoa", "producer", "positive", 0.72, "high", "primary aluminum exposure"),
            _exposure("CENX", "Century Aluminum", "producer", "positive", 0.62, "medium", "aluminum spot beta"),
            _exposure("RIO", "Rio Tinto", "producer", "positive", 0.22, "medium", "diversified aluminum segment"),
        ],
        "losers": [
            _exposure("F", "Ford", "consumer", "negative", -0.20, "medium", "auto material cost basket"),
            _exposure("BALL", "Ball Corp", "consumer", "negative", -0.16, "medium", "packaging input cost"),
            _exposure("BA", "Boeing", "consumer", "negative", -0.10, "low", "contract and backlog lag"),
        ],
        "spreadWatch": [
            _exposure("ARNC", "Arconic", "spread", "spread", 0.10, "low", "rolled product spread"),
        ],
        "marketQueries": ["aluminum", "manufacturing", "auto sales"],
    },
    {
        "id": "lithium-ev-batteries",
        "commodityIds": ["lithium"],
        "chainLabel": "Lithium -> miners, battery cost, EV gross margin",
        "demandRegime": "EV demand + contract resets",
        "lagLabel": "1-2q lag",
        "confidence": "medium",
        "formula": "Lithium proxy move * battery-material share * contract lag * EV pricing power.",
        "winners": [
            _exposure("ALB", "Albemarle", "producer", "positive", 0.76, "high", "lithium revenue exposure"),
            _exposure("SQM", "Sociedad Quimica y Minera", "producer", "positive", 0.66, "medium", "lithium segment exposure"),
            _exposure("LIT", "Global X Lithium ETF", "producer", "positive", 0.52, "medium", "basket proxy"),
        ],
        "losers": [
            _exposure("TSLA", "Tesla", "consumer", "negative", -0.30, "medium", "battery material cost"),
            _exposure("RIVN", "Rivian", "consumer", "negative", -0.28, "medium", "battery cost and pricing power"),
            _exposure("GM", "General Motors", "consumer", "negative", -0.20, "low", "battery JV and contract lag"),
        ],
        "spreadWatch": [
            _exposure("CATL", "CATL", "spread", "spread", 0.14, "medium", "cell price minus material basket", market="CN"),
        ],
        "marketQueries": ["Tesla", "electric vehicles", "lithium"],
    },
    {
        "id": "natgas-power-feedstock",
        "commodityIds": ["natgas", "ttf"],
        "chainLabel": "Natural gas -> E&P, LNG, chemicals, power-intensive AI load",
        "demandRegime": "weather + LNG + power load",
        "lagLabel": "0-6w lag",
        "confidence": "medium",
        "formula": "Gas move * feedstock/power exposure * regulated pass-through * weather regime.",
        "winners": [
            _exposure("EQT", "EQT", "producer", "positive", 0.74, "high", "US gas production exposure"),
            _exposure("LNG", "Cheniere Energy", "producer", "positive", 0.42, "medium", "LNG spread and volumes"),
            _exposure("AR", "Antero Resources", "producer", "positive", 0.48, "medium", "gas + NGL exposure"),
        ],
        "losers": [
            _exposure("DOW", "Dow", "consumer", "negative", -0.24, "medium", "chemical feedstock and power"),
            _exposure("LYB", "LyondellBasell", "consumer", "negative", -0.20, "medium", "feedstock spread"),
            _exposure("VRT", "Vertiv", "consumer", "negative", -0.08, "low", "data-center power-cost second order"),
        ],
        "spreadWatch": [
            _exposure("CE", "Celanese", "spread", "spread", 0.12, "medium", "chemical product spread"),
        ],
        "marketQueries": ["natural gas", "electricity prices", "AI data centers"],
    },
]


def _commodity_rows(
    dependencies: CommodityEquityTransmissionDependencies,
) -> Dict[str, Dict[str, Any]]:
    try:
        if dependencies.get_market_group_snapshot is None:
            raise RuntimeError("get_market_group_snapshot helper missing")
        payload = dependencies.get_market_group_snapshot(
            dependencies.commodity_symbols,
            kind="commodities",
        )
    except Exception:
        if dependencies.logger is not None:
            dependencies.logger.exception(
                "commodity transmission failed to load commodities snapshot"
            )
        payload = {"items": []}
    rows = {}
    for item in payload.get("items") or []:
        if isinstance(item, dict) and item.get("id"):
            rows[str(item["id"])] = item
    return rows


def _first_quote(rows: Dict[str, Dict[str, Any]], ids: Iterable[str]) -> Optional[Dict[str, Any]]:
    for item_id in ids:
        row = rows.get(item_id)
        if row is not None:
            return row
    return None


def _quote_item(row: Dict[str, Any]) -> Dict[str, Any]:
    change = _safe_float(row.get("changePercent"))
    return {
        "id": str(row.get("id") or row.get("symbol") or ""),
        "label": str(row.get("label") or row.get("symbol") or "COMMODITY"),
        "symbol": str(row.get("symbol") or ""),
        "price": _safe_float(row.get("price")),
        "changePct": change,
        "changeLabel": _change_label(change),
        "tone": _tone_for_change(change),
    }


def _linked_markets(
    dependencies: CommodityEquityTransmissionDependencies,
    queries: Iterable[str],
) -> List[Dict[str, Any]]:
    linked: List[Dict[str, Any]] = []
    seen = set()
    for query in queries:
        query_text = str(query or "").strip()
        if not query_text:
            continue
        found = False
        if dependencies.search_markets is not None:
            try:
                payload = (
                    dependencies.search_markets(
                        query_text,
                        limit=2,
                    )
                    or {}
                )
                for item in payload.get("items") or []:
                    market_id = item.get("id") or item.get("localMarketId") or item.get("slug")
                    if market_id in seen:
                        continue
                    seen.add(market_id)
                    linked.append(
                        {
                            "id": item.get("id") or item.get("localMarketId"),
                            "title": item.get("title"),
                            "slug": item.get("slug"),
                            "query": query_text,
                            "source": "search",
                        }
                    )
                    found = True
                    if len(linked) >= 2:
                        return linked
            except Exception:
                if dependencies.logger is not None:
                    dependencies.logger.exception(
                        "commodity transmission linked market search failed query=%s",
                        query_text,
                    )
        if not found and query_text not in seen:
            seen.add(query_text)
            linked.append({"query": query_text, "title": query_text, "source": "query"})
        if len(linked) >= 2:
            return linked
    return linked


def _build_chain(
    dependencies: CommodityEquityTransmissionDependencies,
    spec: Dict[str, Any],
    rows: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    quote = _first_quote(rows, spec["commodityIds"])
    change = _safe_float(quote.get("changePercent")) if quote else None
    commodity_id = str(spec["commodityIds"][0])
    tone = "watch" if change is None else ("up" if change > 0.15 else "down" if change < -0.15 else "neutral")
    return {
        "id": spec["id"],
        "commodityId": commodity_id,
        "chainLabel": spec["chainLabel"],
        "shockLabel": _change_label(change) if change is not None else "MODEL",
        "shockPct": change,
        "tone": tone,
        "demandRegime": spec["demandRegime"],
        "lagLabel": spec["lagLabel"],
        "confidence": spec["confidence"],
        "formula": spec["formula"],
        "winners": spec["winners"],
        "losers": spec["losers"],
        "spreadWatch": spec["spreadWatch"],
        "linkedMarkets": _linked_markets(
            dependencies,
            spec.get("marketQueries") or [],
        ),
    }


def get_commodity_equity_transmission_snapshot(
    ctx: Mapping[str, Any],
    limit: int = 8,
) -> Dict[str, Any]:
    dependencies = CommodityEquityTransmissionDependencies.from_context(ctx)
    rows = _commodity_rows(dependencies)
    commodity_ids = []
    for spec in CHAIN_SPECS:
        commodity_ids.extend(spec["commodityIds"])
    commodities = [
        _quote_item(rows[item_id])
        for item_id in dict.fromkeys(commodity_ids)
        if item_id in rows
    ]
    chains = [
        _build_chain(dependencies, spec, rows)
        for spec in CHAIN_SPECS
    ]
    chains.sort(key=lambda item: abs(_safe_float(item.get("shockPct")) or 0.0), reverse=True)
    limit_value = max(1, min(int(limit or 8), 12))
    chains = chains[:limit_value]

    live_count = sum(1 for item in commodities if item.get("changePct") is not None)
    top = chains[0] if chains else None
    top_change = _safe_float(top.get("shockPct")) if top else None
    status = "ok" if live_count >= 3 else ("partial" if live_count > 0 else "model")
    signal = (
        f"{top['chainLabel']} is the top live transmission chain"
        if top and top_change is not None
        else "Curated commodity-to-equity map awaiting live commodity quotes"
    )

    return {
        "generatedAt": dependencies.utc_now_iso(),
        "panelId": PANEL_ID,
        "source": "Yahoo Finance commodity snapshot + curated exposure map",
        "cacheMode": "runtime-compose",
        "status": status,
        "summary": {
            "signal": signal,
            "signalLabel": "COMMODITY SHOCK MAP",
            "bias": "model" if status == "model" else "mixed",
            "chainCount": len(chains),
            "liveCommodityCount": live_count,
            "topShockLabel": top["commodityId"].upper() if top else None,
            "topShockChangeLabel": _change_label(top_change),
            "positiveCount": sum(len(item.get("winners") or []) for item in chains),
            "negativeCount": sum(len(item.get("losers") or []) for item in chains),
            "spreadCount": sum(len(item.get("spreadWatch") or []) for item in chains),
        },
        "commodities": commodities[:10],
        "transmissions": chains,
        "sources": {
            "commodities": "existing commodities-watch runtime snapshot",
            "exposures": "curated commodity-to-equity exposure map",
            "linkedMarkets": "local Polymarket market search when available",
        },
    }
