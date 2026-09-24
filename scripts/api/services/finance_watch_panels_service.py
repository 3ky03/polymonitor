from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlencode, urlparse
from xml.etree import ElementTree

from api.context import (
    resolve_optional_service_callable,
    resolve_optional_service_value,
)

try:
    import requests
except ImportError:  # pragma: no cover - watcher validates dependency.
    requests = None

from api.services import crypto_funding_service, finance_external_sources_service


FINANCE_WATCH_CACHE_KEY = "v1"
FINANCE_WATCH_TTL_SECONDS = 10 * 60
FINANCE_WATCH_PANEL_IDS = (
    "defi-yield-monitor",
    "defi-security-watch",
    "crypto-perp-funding",
    "tradfi-perp-radar",
    "ipo-news-watch",
    "broker-research-watch",
    "global-index-monitor",
    "crypto-fear-greed",
    "crypto-etf-flow",
    "stablecoin-monitor",
    "blockchain-policy-news",
)

GLOBAL_INDEX_SYMBOLS = (
    ("S&P 500", "^GSPC", "US"),
    ("NASDAQ", "^IXIC", "US"),
    ("DOW", "^DJI", "US"),
    ("RUSSELL", "^RUT", "US"),
    ("SHANGHAI", "000001.SS", "CN"),
    ("HANG SENG", "^HSI", "HK"),
    ("NIKKEI", "^N225", "JP"),
    ("NIFTY", "^NSEI", "IN"),
    ("EURO STOXX", "^STOXX50E", "EU"),
    ("FTSE", "^FTSE", "UK"),
    ("DAX", "^GDAXI", "DE"),
    ("CAC", "^FCHI", "FR"),
)

CRYPTO_PERP_ORDER = (
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "DOGE",
    "ADA",
    "AVAX",
    "LINK",
    "TRX",
    "DOT",
    "BCH",
    "SUI",
    "NEAR",
    "APT",
)

RELIABLE_DEFI_PROJECTS = {
    "aave-v3": 120,
    "aave": 118,
    "pendle": 116,
    "uniswap-v3": 114,
    "uniswap-v4": 112,
    "uniswap": 110,
    "curve-dex": 108,
    "curve": 108,
    "compound-v3": 106,
    "compound": 104,
    "morpho-blue": 102,
    "morpho": 100,
    "makerdao": 98,
    "sky-lending": 96,
    "ether.fi": 94,
    "lido": 92,
}

TRADFI_PERP_CLASS_PRIORITY = {"INDEX": 4, "COMMODITY": 3, "STOCK": 2, "PRIVATE": 1}
TRADFI_PERP_DISPLAY_PRIORITY = {
    "SPY-PERP": 100,
    "S&P500-PERP": 98,
    "RUSSELL-PERP": 96,
    "RUSSELL-ETF-PERP": 95,
    "DOW-PERP": 94,
    "NASDAQ-PERP": 92,
    "GOLD-PERP": 90,
    "WTI-PERP": 88,
}
FNG_WEIGHTS = {
    "sentiment": 0.10,
    "volatility": 0.10,
    "positioning": 0.15,
    "trend": 0.10,
    "breadth": 0.10,
    "momentum": 0.10,
    "liquidity": 0.15,
    "credit": 0.10,
    "macro": 0.05,
    "crossAsset": 0.05,
}

NEWS_QUERIES = {
    "defi-security-watch": '("DeFi" OR "crypto protocol") (exploit OR hack OR vulnerability OR attack OR audit OR governance risk)',
    "ipo-news-watch": 'IPO OR "S-1" OR "F-1" OR "files for listing" OR "public listing" OR "listing rumor"',
    "blockchain-policy-news": '("crypto bill" OR "stablecoin legislation" OR "SEC crypto" OR "CFTC crypto" OR "exchange enforcement" OR "tokenization regulation")',
}

BROKER_RESEARCH_SYMBOLS = (
    ("NVDA", "Nvidia", "AI"),
    ("AMD", "AMD", "AI"),
    ("MSFT", "Microsoft", "AI"),
    ("AAPL", "Apple", "MEGA"),
    ("AMZN", "Amazon", "MEGA"),
    ("GOOGL", "Alphabet", "MEGA"),
    ("META", "Meta", "MEGA"),
    ("TSLA", "Tesla", "EV"),
    ("COIN", "Coinbase", "CRYPTO"),
    ("MSTR", "MicroStrategy", "CRYPTO"),
    ("HOOD", "Robinhood", "CRYPTO"),
    ("MARA", "MARA", "MINER"),
    ("RIOT", "Riot", "MINER"),
    ("IBIT", "iShares Bitcoin Trust", "ETF"),
    ("ETHA", "iShares Ethereum Trust", "ETF"),
    ("GBTC", "Grayscale Bitcoin Trust", "ETF"),
    ("SPY", "SPDR S&P 500 ETF", "ETF"),
    ("QQQ", "Invesco QQQ", "ETF"),
    ("XOM", "Exxon Mobil", "ENERGY"),
    ("CVX", "Chevron", "ENERGY"),
    ("GLD", "SPDR Gold Shares", "GOLD"),
    ("USO", "United States Oil Fund", "OIL"),
)

BROKER_NAMES = (
    "Morgan Stanley",
    "Goldman Sachs",
    "JPMorgan",
    "JP Morgan",
    "Bank of America",
    "BofA",
    "Citigroup",
    "Citi",
    "UBS",
    "Deutsche Bank",
    "Wells Fargo",
    "Barclays",
    "Bernstein",
    "Benchmark",
    "Daiwa",
    "Evercore",
    "Jefferies",
    "Mizuho",
    "Melius",
    "Wedbush",
    "Needham",
    "Piper Sandler",
    "RBC",
    "TD Cowen",
    "Oppenheimer",
    "Raymond James",
    "Stifel",
    "Cantor Fitzgerald",
    "KeyBanc",
    "Truist",
    "HSBC",
    "Loop Capital",
    "Rosenblatt",
)


@dataclass(frozen=True)
class FinanceWatchDependencies:
    settings: Any
    http_json_get: Callable[..., Any] | None
    http_text_get: Callable[..., Any] | None
    get_yahoo_market_snapshot: Callable[..., Any] | None
    snapshot_store: Any
    get_cached_json: Callable[..., Any] | None
    set_cached_json: Callable[..., Any] | None
    get_snapshot_payload: Callable[..., Any] | None
    get_crypto_funding_watch_snapshot: Callable[..., Any] | None
    crypto_funding: crypto_funding_service.CryptoFundingDependencies
    external_sources: finance_external_sources_service.FinanceExternalSourceDependencies

    @classmethod
    def from_context(
        cls,
        context: Mapping[str, Any],
    ) -> FinanceWatchDependencies:
        return cls(
            settings=resolve_optional_service_value(context, "SETTINGS"),
            http_json_get=resolve_optional_service_callable(
                context,
                "http_json_get",
            ),
            http_text_get=resolve_optional_service_callable(
                context,
                "http_text_get",
            ),
            get_yahoo_market_snapshot=resolve_optional_service_callable(
                context,
                "get_yahoo_market_snapshot",
            ),
            snapshot_store=resolve_optional_service_value(
                context,
                "SNAPSHOT_STORE",
            ),
            get_cached_json=resolve_optional_service_callable(
                context,
                "get_cached_json",
            ),
            set_cached_json=resolve_optional_service_callable(
                context,
                "set_cached_json",
            ),
            get_snapshot_payload=resolve_optional_service_callable(
                context,
                "get_snapshot_payload",
            ),
            get_crypto_funding_watch_snapshot=(
                resolve_optional_service_callable(
                    context,
                    "get_crypto_funding_watch_snapshot",
                )
            ),
            crypto_funding=(
                crypto_funding_service.CryptoFundingDependencies.from_context(
                    context,
                )
            ),
            external_sources=(
                finance_external_sources_service.FinanceExternalSourceDependencies.from_context(
                    context,
                )
            ),
        )


FinanceWatchContext = Mapping[str, Any] | FinanceWatchDependencies


def _dependencies(
    context: FinanceWatchContext,
) -> FinanceWatchDependencies:
    if isinstance(context, FinanceWatchDependencies):
        return context
    return FinanceWatchDependencies.from_context(context)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def finance_watch_namespace(panel_id: str) -> str:
    return f"runtime:finance:{panel_id}"


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _compact_source(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:34] if text else "source"


def _strip_html(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _field_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(part for part in (_field_text(item) for item in value[:4]) if part)
    if isinstance(value, dict):
        for key in ("name", "displayName", "analystName", "value", "title", "text", "label"):
            if value.get(key) not in (None, ""):
                return _field_text(value.get(key))
        return ""
    return _strip_html(value)


def _setting(ctx: FinanceWatchContext, name: str) -> str:
    dependencies = _dependencies(ctx)
    value = getattr(dependencies.settings, name, "") if dependencies.settings is not None else ""
    return str(value or "").strip()


def _setting_tuple(
    ctx: FinanceWatchContext,
    name: str,
) -> tuple[str, ...]:
    dependencies = _dependencies(ctx)
    value = getattr(dependencies.settings, name, ()) if dependencies.settings is not None else ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    try:
        return tuple(str(part).strip() for part in value if str(part).strip())
    except TypeError:
        return ()


def _setting_bool(ctx: FinanceWatchContext, name: str) -> bool:
    dependencies = _dependencies(ctx)
    value = getattr(dependencies.settings, name, False) if dependencies.settings is not None else False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _tone(value: Any, *, inverted: bool = False) -> str:
    number = _safe_float(value)
    if number is None:
        return "neutral"
    if inverted:
        number = -number
    if number > 0:
        return "up"
    if number < 0:
        return "down"
    return "neutral"


def _http_json_get(
    ctx: FinanceWatchContext,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 12,
) -> Any:
    dependencies = _dependencies(ctx)
    if dependencies.http_json_get is not None:
        try:
            return dependencies.http_json_get(
                url,
                params=params,
                timeout=timeout,
                headers={
                    "User-Agent": "polydata-finance-watch/1.0",
                    "Accept": "application/json",
                },
            )
        except Exception:
            pass
    if requests is None:
        raise RuntimeError("requests package is required")
    session = requests.Session()
    session.trust_env = True
    try:
        response = session.get(url, params=params, timeout=timeout, headers={"User-Agent": "polydata-finance-watch/1.0", "Accept": "application/json"})
        response.raise_for_status()
        return response.json() if response.content else None
    finally:
        session.close()


def _http_text_get(
    ctx: FinanceWatchContext,
    url: str,
    *,
    timeout: int = 12,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    dependencies = _dependencies(ctx)
    request_headers = {"User-Agent": "polydata-finance-watch/1.0", "Accept": "application/rss+xml,application/xml,text/xml"}
    if headers:
        request_headers.update(headers)
    if dependencies.http_text_get is not None:
        try:
            return dependencies.http_text_get(
                url,
                timeout=timeout,
                headers=request_headers,
            )
        except Exception:
            pass
    if requests is None:
        raise RuntimeError("requests package is required")
    session = requests.Session()
    session.trust_env = True
    try:
        response = session.get(url, timeout=timeout, headers=request_headers)
        response.raise_for_status()
        return response.text
    finally:
        session.close()


def _news_url(ctx: FinanceWatchContext, query: str) -> str:
    # URL is supplied through POLYDATA_FINANCE_GOOGLE_NEWS_RSS_URL.
    return f"{_setting(ctx, 'finance_google_news_rss_url')}?{urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})}"


def _fetch_yahoo_snapshot(
    ctx: FinanceWatchContext,
    symbol: str,
    *,
    interval: str = "30m",
    range_name: str = "5d",
) -> Optional[Dict[str, Any]]:
    dependencies = _dependencies(ctx)
    try:
        if dependencies.get_yahoo_market_snapshot is None:
            raise RuntimeError("get_yahoo_market_snapshot helper missing")
        quote = dependencies.get_yahoo_market_snapshot(
            symbol,
            interval=interval,
            range_name=range_name,
            ttl_seconds=300,
        )
    except Exception:
        quote = None
    if isinstance(quote, dict) and quote.get("price") is not None:
        return quote
    payload = _http_json_get(
        ctx,
        _setting(ctx, "finance_yahoo_chart_url_template").format(symbol=symbol),
        params={"range": range_name, "interval": "1d" if range_name != "1d" else "5m"},
        timeout=12,
    )
    result = (payload.get("chart") or {}).get("result") if isinstance(payload, dict) else []
    chart = result[0] if isinstance(result, list) and result else {}
    quote_rows = (chart.get("indicators") or {}).get("quote") or []
    quote_data = quote_rows[0] if quote_rows and isinstance(quote_rows[0], dict) else {}
    timestamps = chart.get("timestamp") or []
    closes = [value for value in (quote_data.get("close") or []) if value is not None]
    volumes = [value for value in (quote_data.get("volume") or []) if value is not None]
    if not closes:
        return None
    price = _safe_float(closes[-1])
    previous = _safe_float(closes[-2]) if len(closes) >= 2 else None
    points = []
    start = max(0, len(closes) - 260)
    for offset, value in enumerate(closes[start:]):
        index = start + offset
        timestamp = timestamps[index] if index < len(timestamps) else None
        points.append({"timestamp": timestamp, "value": value})
    return {
        "price": price,
        "changePercent": ((price - previous) / previous * 100) if price is not None and previous not in (None, 0) else None,
        "volume24h": _safe_float(volumes[-1]) if volumes else None,
        "points": points,
    }


def _fetch_yahoo_closes(
    ctx: FinanceWatchContext,
    symbol: str,
    *,
    range_name: str = "1y",
) -> Dict[str, Any]:
    snapshot = _fetch_yahoo_snapshot(ctx, symbol, interval="1d", range_name=range_name)
    points = [point for point in (snapshot or {}).get("points", []) if isinstance(point, dict)]
    closes = [_safe_float(point.get("value")) for point in points]
    return {**(snapshot or {}), "closes": [value for value in closes if value is not None]}


def _fetch_fred_observations(
    ctx: FinanceWatchContext,
    series_id: str,
) -> List[Dict[str, Any]]:
    try:
        text = _http_text_get(ctx, _setting(ctx, "finance_fred_csv_url_template").format(series_id=series_id), timeout=10)
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for line in text.splitlines()[1:]:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2 or parts[1] in {"", "."}:
            continue
        value = _safe_float(parts[1])
        if value is not None:
            rows.append({"date": parts[0], "value": value})
    return rows


def _fred_latest(rows: List[Dict[str, Any]]) -> Optional[float]:
    return _safe_float(rows[-1].get("value")) if rows else None


def _fred_back(rows: List[Dict[str, Any]], periods: int) -> Optional[float]:
    if not rows:
        return None
    index = max(0, len(rows) - 1 - periods)
    return _safe_float(rows[index].get("value"))


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _roc(values: List[float], period: int) -> Optional[float]:
    if len(values) < period + 1:
        return None
    previous = values[-period - 1]
    current = values[-1]
    return ((current - previous) / previous) * 100 if previous else None


def _sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / len(window)


def _rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for idx in range(len(values) - period, len(values)):
        delta = values[idx] - values[idx - 1]
        if delta > 0:
            gains += delta
        else:
            losses += abs(delta)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - (100 / (1 + rs))


def _score_label(score: Any) -> str:
    value = _safe_float(score)
    if value is None:
        return "Neutral"
    if value <= 20:
        return "Extreme Fear"
    if value <= 40:
        return "Fear"
    if value <= 60:
        return "Neutral"
    if value <= 80:
        return "Greed"
    return "Extreme Greed"


def _score_tone(score: Any) -> str:
    value = _safe_float(score)
    if value is None:
        return "neutral"
    if value >= 60:
        return "up"
    if value <= 40:
        return "down"
    return "watch"


def _fetch_barchart_last_price(
    ctx: FinanceWatchContext,
    symbol: str,
) -> Optional[float]:
    try:
        html = _http_text_get(ctx, _setting(ctx, "finance_barchart_quote_url_template").format(symbol=symbol), timeout=10)
    except Exception:
        return None
    block_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', html)
    block = block_match.group(1) if block_match else html
    match = re.search(r'"lastPrice"\s*:\s*"?([\d.]+)"?', block)
    return _safe_float(match.group(1)) if match else None


def _fetch_cnn_fng(
    ctx: FinanceWatchContext,
) -> Optional[Dict[str, Any]]:
    if requests is None:
        return None
    session = requests.Session()
    session.trust_env = True
    try:
        response = session.get(
            _setting(ctx, "finance_cnn_fng_url"),
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Referer": _setting(ctx, "finance_cnn_fng_referer_url"),
            },
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None
    finally:
        session.close()
    score = _safe_float(data.get("score") or (data.get("fear_and_greed") or {}).get("score"))
    label = data.get("rating") or (data.get("fear_and_greed") or {}).get("rating")
    return {"score": round(score), "label": str(label or _score_label(score))} if score is not None else None


def _fetch_aaii_sentiment(
    ctx: FinanceWatchContext,
) -> Optional[Dict[str, float]]:
    try:
        html = _http_text_get(ctx, _setting(ctx, "finance_aaii_sentiment_url"), timeout=10)
    except Exception:
        return None
    values = [_safe_float(match.group(1)) for match in re.finditer(r'<td[^>]*class="tableTxt"[^>]*>([\d.]+)%', html)]
    values = [value for value in values if value is not None]
    if len(values) < 3:
        return None
    return {"bull": float(values[0]), "bear": float(values[2])}


def _parse_rss_items(xml_text: str, *, panel_id: str, limit: int) -> List[Dict[str, Any]]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    rows: List[Dict[str, Any]] = []
    for item in root.findall(".//item")[: max(1, limit * 2)]:
        title = _strip_html(item.findtext("title"))
        description = _strip_html(item.findtext("description"))
        source = _compact_source(item.findtext("source") or "Google News")
        link = item.findtext("link")
        published_raw = item.findtext("pubDate")
        published_at = None
        if published_raw:
            try:
                published_at = parsedate_to_datetime(published_raw).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            except (TypeError, ValueError):
                published_at = None
        tags = _news_tags(panel_id, title, description)
        rows.append(
            {
                "id": f"{panel_id}:{len(rows)}:{hash(title)}",
                "label": _headline_entity(title, source),
                "symbol": source.upper()[:14],
                "title": title,
                "summary": description,
                "source": source,
                "url": link,
                "publishedAt": published_at,
                "tags": tags,
                "tone": "down" if any(tag in {"HACK", "EXPLOIT", "ALERT", "ENFORCE"} for tag in tags) else "neutral",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _headline_entity(title: str, source: str) -> str:
    cleaned = re.split(r"\s[-|]\s", title or "")[0].strip()
    words = cleaned.split()
    if len(words) >= 2:
        return " ".join(words[:3])[:28]
    return cleaned[:28] or source


def _news_tags(panel_id: str, title: str, description: str) -> List[str]:
    text = f"{title} {description}".lower()
    pairs = (
        ("HACK", ("hack", "hacked", "drain")),
        ("EXPLOIT", ("exploit", "attack", "breach")),
        ("ALERT", ("vulnerability", "warning", "risk")),
        ("AUDIT", ("audit", "auditor")),
        ("S-1", ("s-1", "s1")),
        ("F-1", ("f-1", "f1")),
        ("IPO", ("ipo", "initial public")),
        ("LISTING", ("listing", "go public")),
        ("RUMOR", ("rumor", "reportedly")),
        ("BILL", ("bill", "legislation", "lawmakers")),
        ("SEC", ("sec", "securities and exchange")),
        ("CFTC", ("cftc",)),
        ("COURT", ("court", "judge", "lawsuit")),
        ("ENFORCE", ("enforcement", "charged", "settlement")),
        ("STABLE", ("stablecoin",)),
        ("GOV", ("governance", "proposal", "dao")),
        ("BRIDGE", ("bridge", "cross-chain", "cross chain")),
    )
    tags = [label for label, needles in pairs if any(needle in text for needle in needles)]
    if panel_id == "defi-security-watch":
        return (tags or ["ALERT"])[:3]
    if panel_id == "ipo-news-watch":
        return (tags or ["IPO"])[:3]
    return (tags or ["POLICY"])[:3]


def _broker_research_query() -> str:
    symbols = " OR ".join(symbol for symbol, _name, _theme in BROKER_RESEARCH_SYMBOLS[:18])
    return f'("price target" OR upgrade OR downgrade OR "initiates coverage" OR "top pick" OR reiterates) ({symbols}) when:4d'


def _find_broker_symbol(text: str) -> Optional[tuple[str, str, str]]:
    normalized = f" {text.upper()} "
    for symbol, name, theme in BROKER_RESEARCH_SYMBOLS:
        if re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", normalized):
            return symbol, name, theme
        name_tokens = [token for token in re.split(r"\W+", name.upper()) if len(token) >= 4]
        if name_tokens and all(token in normalized for token in name_tokens[:2]):
            return symbol, name, theme
    return None


def _find_broker_name(text: str, fallback: str) -> str:
    lowered = text.lower()
    for broker in BROKER_NAMES:
        if broker.lower() in lowered:
            return "JPMorgan" if broker == "JP Morgan" else broker
    return fallback or "Research"


def _coalesce_text(*values: Any) -> str:
    for value in values:
        text = _field_text(value)
        if text:
            return text
    return ""


def _coalesce_url(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text.startswith("http://") or text.startswith("https://"):
            return text
    return ""


def _absolute_url(base_url: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text or text.startswith("#") or text.lower().startswith(("javascript:", "mailto:", "tel:")):
        return ""
    return urljoin(base_url, text)


def _extract_anchor_links(html: str, base_url: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for match in re.finditer(r"<a\b[^>]*?\bhref=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</a>", html, flags=re.IGNORECASE):
        href = _absolute_url(base_url, unescape(match.group(1)))
        if not href:
            continue
        label = _strip_html(match.group(2))
        rows.append({"url": href, "label": label})
    return rows


def _research_headers() -> Dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _rss_links(xml_text: str) -> List[Dict[str, str]]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    rows: List[Dict[str, str]] = []
    for item in root.findall(".//item"):
        url = _coalesce_url(item.findtext("link"), item.findtext("guid"))
        if not url:
            continue
        rows.append({"url": url, "label": _strip_html(item.findtext("title"))})
    return rows


def _extract_html_title(html: str) -> str:
    patterns = (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<h1[^>]*>([\s\S]*?)</h1>",
        r"<title[^>]*>([\s\S]*?)</title>",
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return _strip_html(match.group(1))
    return ""


def _extract_html_summary(html: str) -> str:
    patterns = (
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
        r"<p[^>]*>([\s\S]*?)</p>",
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return _strip_html(match.group(1))
    return ""


def _extract_html_published_at(html: str) -> Optional[str]:
    patterns = (
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<time[^>]+datetime=["\']([^"\']+)["\']',
        r"\b(\d{4}-\d{2}-\d{2}T[\d:]+(?:Z|[+-]\d{2}:\d{2})?)\b",
        r"\b(\d{2}/\d{2}/\d{4})\b",
        r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return _published_iso(match.group(1))
    return None


def _extract_first_pdf_url(html: str, base_url: str) -> str:
    for anchor in _extract_anchor_links(html, base_url):
        url = anchor["url"]
        label = anchor.get("label") or ""
        if ".pdf" in url.lower() or "download pdf" in label.lower() or label.lower().strip() == "pdf":
            return url
    match = re.search(r'https?://[^"\']+?\.pdf(?:\?[^"\']*)?', html, flags=re.IGNORECASE)
    return match.group(0) if match else ""


def _symbol_from_text(text: str) -> str:
    candidates = (
        r"^\s*([A-Z][A-Z0-9.]{1,7})\s*:",
        r"\b(?:NASDAQ|NYSE|NYSEAMERICAN|AMEX|OTC|LON|AIM|TSX|TSXV|ASX|PAR|EPA|FRA|ETR)\s*:\s*([A-Z][A-Z0-9.]{1,7})\b",
        r"\(([A-Z][A-Z0-9.]{1,7})\)",
    )
    blocked = {"PDF", "CEO", "CFO", "FDA", "IPO", "ETF", "USA", "USD", "AI", "Q1", "Q2", "Q3", "Q4"}
    for pattern in candidates:
        match = re.search(pattern, text)
        if match:
            symbol = match.group(1).upper().strip(".")
            if symbol not in blocked and 1 < len(symbol) <= 8:
                return symbol
    return ""


def _normalize_security_code(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).upper().strip()
    if not text:
        return ""
    match = re.search(r"([A-Z]{0,5}\d{3,6}(?:\.[A-Z]{1,4})?|[A-Z][A-Z0-9.:-]{1,11})", text)
    if not match:
        return ""
    code = match.group(1).strip(".:-")
    blocked = {"HTTP", "HTTPS", "REPORT", "RESEARCH", "PDF"}
    return "" if code in blocked else code[:14]


def _broker_number(value: Any) -> Optional[float]:
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    return _safe_float(value)


def _dict_value(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _published_iso(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}", text):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return parsedate_to_datetime(text).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return None


NEWS_OR_AGGREGATOR_DOMAINS = (
    "google.com",
    "news.google.com",
    "benzinga.com",
    "marketbeat.com",
    "thefly.com",
    "tipranks.com",
    "streetinsider.com",
    "investing.com",
    "zacks.com",
    "seekingalpha.com",
    "yahoo.com",
    "finance.yahoo.com",
    "bloomberg.com",
    "reuters.com",
    "cnbc.com",
    "marketwatch.com",
    "barrons.com",
    "wsj.com",
    "fool.com",
)


def _is_news_or_aggregator_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    if not host:
        return True
    return any(host == domain or host.endswith(f".{domain}") for domain in NEWS_OR_AGGREGATOR_DOMAINS)


def _is_original_research_url(url: str, *, allowed_domains: tuple[str, ...] = ()) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    host = (urlparse(url).netloc or "").lower()
    if allowed_domains and any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
        return True
    return not _is_news_or_aggregator_url(url)


def _has_known_broker(text: str) -> bool:
    lowered = text.lower()
    return any(broker.lower() in lowered for broker in BROKER_NAMES)


def _broker_action(text: str) -> tuple[str, str]:
    lowered = text.lower()
    if any(word in lowered for word in ("downgrade", "downgrades", "cut to sell", "lowered to underperform")):
        return "DOWNGRADE", "down"
    if any(word in lowered for word in ("upgrade", "upgrades", "raised to buy", "raised to outperform")):
        return "UPGRADE", "up"
    if any(word in lowered for word in ("initiates coverage", "initiate coverage", "started coverage", "starts coverage")):
        return "INITIATE", "watch"
    if any(word in lowered for word in ("top pick", "best idea", "conviction list")):
        return "TOP PICK", "up"
    if any(word in lowered for word in ("raises price target", "raised price target", "boosts price target", "lifts price target", "price target raised", "price target increased")) or re.search(r"\braises\b.{0,40}\bprice target\b", lowered):
        return "PT RAISE", "up"
    if any(word in lowered for word in ("cuts price target", "cut price target", "lowers price target", "lowered price target", "price target cut", "price target lowered")) or re.search(r"\blowers\b.{0,40}\bprice target\b", lowered):
        return "PT CUT", "down"
    if any(word in lowered for word in ("reiterates", "maintains", "keeps")):
        return "REITERATE", "neutral"
    return "NOTE", "neutral"


def _is_broker_research_candidate(text: str, source: str, action: str, target: Optional[float]) -> bool:
    lowered = text.lower()
    product_noise = ("gpu upgrade", "upgrade your", "memory upgrade", "software upgrade", "processor", "driver update", "windows upgrade")
    if any(needle in lowered for needle in product_noise):
        return False
    if target is not None or _has_known_broker(text):
        return True
    source_lower = source.lower()
    if any(needle in source_lower for needle in ("marketbeat", "benzinga", "thefly", "tipranks", "streetinsider", "investing.com")):
        return action in {"UPGRADE", "DOWNGRADE", "INITIATE", "TOP PICK", "PT RAISE", "PT CUT"} and any(needle in lowered for needle in ("analyst", "rating", "price target", "coverage"))
    return False


def _extract_target_prices(text: str) -> tuple[Optional[float], Optional[float]]:
    cleaned = re.sub(r"\s+", " ", text)
    target = None
    previous = None
    target_patterns = (
        r"(?:price target|pt)[^$]{0,48}?\b(?:to|at|of|:)\s*\$([0-9][0-9,]*(?:\.\d+)?)",
        r"\bto\s+\$([0-9][0-9,]*(?:\.\d+)?)\s+from\s+\$([0-9][0-9,]*(?:\.\d+)?)",
        r"\$([0-9][0-9,]*(?:\.\d+)?)\s+(?:price target|pt)",
    )
    for pattern in target_patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if not match:
            continue
        target = _safe_float(match.group(1).replace(",", ""))
        if len(match.groups()) >= 2:
            previous = _safe_float(match.group(2).replace(",", ""))
        break
    if previous is None:
        from_match = re.search(r"\bfrom\s+\$([0-9][0-9,]*(?:\.\d+)?)", cleaned, flags=re.IGNORECASE)
        previous = _safe_float(from_match.group(1).replace(",", "")) if from_match else None
    return target, previous


def _broker_priority(action: str, upside: Optional[float], target_change: Optional[float], published_at: Optional[str], symbol: str) -> float:
    action_score = {
        "UPGRADE": 42,
        "DOWNGRADE": 42,
        "INITIATE": 34,
        "TOP PICK": 32,
        "PT RAISE": 26,
        "PT CUT": 26,
        "REITERATE": 12,
    }.get(action, 8)
    recency = 0.0
    if published_at:
        try:
            age_hours = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(published_at.replace("Z", "+00:00"))).total_seconds() / 3600)
            recency = max(0.0, 30.0 - age_hours)
        except ValueError:
            recency = 0.0
    importance = 16 if symbol in {"NVDA", "AMD", "MSFT", "TSLA", "COIN", "MSTR", "AAPL", "META"} else 8
    return action_score + recency + importance + min(22.0, abs(upside or 0.0)) + min(18.0, abs(target_change or 0.0))


def _broker_research_row(
    ctx: FinanceWatchContext,
    *,
    title: str,
    summary: str,
    report_url: str,
    source: str,
    published_at: Optional[str],
    quote_cache: Dict[str, Dict[str, Any]],
    explicit_symbol: str = "",
    explicit_company: str = "",
    explicit_target: Any = None,
    explicit_previous_target: Any = None,
    explicit_action: str = "",
    explicit_rating: str = "",
    explicit_analyst: str = "",
) -> Optional[Dict[str, Any]]:
    text = f"{title} {summary} {explicit_symbol}"
    match = _find_broker_symbol(text)
    if match:
        symbol, company, theme = match
    else:
        symbol = _normalize_security_code(explicit_symbol) or _symbol_from_text(f"{explicit_symbol} {title} {summary}")
        if not symbol:
            return None
        company = explicit_company or symbol
        theme = "RESEARCH"
    if explicit_company:
        company = explicit_company
    title = title or f"{symbol} broker research"
    summary = summary or title
    broker = _find_broker_name(text, source)
    action, action_tone = _broker_action(f"{explicit_action} {text}")
    target, previous_target = _extract_target_prices(text)
    target = _broker_number(explicit_target) if explicit_target not in (None, "") else target
    previous_target = _broker_number(explicit_previous_target) if explicit_previous_target not in (None, "") else previous_target
    target_change = ((target - previous_target) / previous_target * 100) if target is not None and previous_target not in (None, 0) else None
    rating_label = _coalesce_text(explicit_rating, explicit_action, action).upper()
    if rating_label in {"NOTE", "REPORT"}:
        rating_label = "REPORT"
    tags = [rating_label, "REPORT", theme]
    if target_change is not None:
        tags[0] = "PT RAISE" if target_change > 0 else "PT CUT" if target_change < 0 else action
    tone = "up" if action_tone == "up" else "down" if action_tone == "down" else "watch" if action_tone == "watch" else "neutral"
    target_label = f"PT {_format_price(target)}" if target is not None else "PT --"
    analyst = _coalesce_text(explicit_analyst)
    summary_bits = [broker, company]
    if analyst:
        summary_bits.append(analyst)
    if rating_label and rating_label != "REPORT":
        summary_bits.append(rating_label.title())
    if target is not None:
        summary_bits.append(target_label)
    if previous_target is not None:
        summary_bits.append(f"from {_format_price(previous_target)}")
    return {
        "id": f"broker-research:{symbol}:{abs(hash(report_url or title))}",
        "label": symbol,
        "symbol": company.upper()[:16],
        "title": title,
        "summary": " | ".join(summary_bits),
        "source": broker,
        "url": report_url,
        "publishedAt": published_at,
        "metric": target,
        "metricLabel": rating_label,
        "metricUnit": "RATING",
        "secondary": target,
        "secondaryLabel": target_label,
        "change": target_change,
        "changeLabel": f"{target_change:+.1f}% PT" if target_change is not None else None,
        "tags": tags[:3],
        "tone": tone,
        "points": [],
        "company": company,
        "institution": broker,
        "analyst": analyst,
        "rating": rating_label,
        "targetPriceLabel": target_label,
        "previousTargetPriceLabel": f"FROM {_format_price(previous_target)}" if previous_target is not None else None,
        "reportPageLabel": "Read report",
        "_priority": _broker_priority(action, None, target_change, published_at, symbol),
    }


def _extract_json_research_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data", "reports", "research", "analystActions", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _candidate_links_for_provider(provider: str, html: str, source_url: str, limit: int) -> List[Dict[str, str]]:
    provider_key = provider.lower()
    rows: List[Dict[str, str]] = []
    seen = set()
    for anchor in _extract_anchor_links(html, source_url):
        url = anchor["url"]
        label = anchor.get("label") or ""
        lower_url = url.lower()
        lower_label = label.lower()
        keep = False
        if provider_key == "edison":
            keep = (
                "edisongroup.com" in lower_url
                and ("/research/" in lower_url or "/insight/" in lower_url or ".pdf" in lower_url)
                and "/equity-research" not in lower_url
            )
        elif provider_key.startswith("zacks"):
            keep = (
                "scr.zacks.com" in lower_url
                and ("/news/news-details/" in lower_url or "/files/news/" in lower_url or ".pdf" in lower_url)
            )
        elif provider_key in {"water tower", "watertower"}:
            keep = (
                "watertowerresearch.com" in lower_url
                and ("/research" in lower_url or "/content/" in lower_url or "/reports/" in lower_url or ".pdf" in lower_url)
                and "login" not in lower_url
            )
        elif provider_key in {"eastmoney", "choice"}:
            keep = (
                ("eastmoney.com" in lower_url or "dfcfw.com" in lower_url)
                and any(word in lower_url or word in lower_label for word in ("report", "research", "yanbao", "研报", "评级", "目标价"))
            )
        else:
            keep = any(word in lower_url or word in lower_label for word in ("research", "report", "pdf"))
        if keep and url not in seen:
            rows.append(anchor)
            seen.add(url)
        if len(rows) >= limit:
            break
    return rows


def _open_research_sources(
    ctx: FinanceWatchContext,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    sources = (
        ("Eastmoney", _setting(ctx, "finance_broker_research_eastmoney_url"), ("eastmoney.com", "eastmoney.com.cn", "dfcfw.com")),
        ("Choice", _setting(ctx, "finance_broker_research_choice_url"), ("eastmoney.com", "eastmoney.com.cn", "dfcfw.com")),
        ("Edison", _setting(ctx, "finance_broker_research_edison_url"), ("edisongroup.com",)),
        ("Zacks SCR", _setting(ctx, "finance_broker_research_zacks_url"), ("scr.zacks.com",)),
        ("Water Tower", _setting(ctx, "finance_broker_research_water_tower_url"), ("watertowerresearch.com",)),
    )
    return tuple((name, url, domains) for name, url, domains in sources if url)


def _build_broker_rows_from_open_sources(
    ctx: FinanceWatchContext,
    *,
    limit: int,
    rows: List[Dict[str, Any]],
    sources: Dict[str, str],
    quote_cache: Dict[str, Dict[str, Any]],
    seen_urls: set,
) -> None:
    max_candidates_per_source = max(4, min(10, limit))
    for provider, source_url, allowed_domains in _open_research_sources(ctx):
        source_key = provider
        try:
            listing_html = _http_text_get(ctx, source_url, timeout=16, headers=_research_headers())
        except Exception:
            sources[source_key] = "error"
            continue
        stripped_listing = listing_html.lstrip()
        candidates = _rss_links(stripped_listing) if stripped_listing.startswith("<?xml") or stripped_listing.startswith("<rss") else _candidate_links_for_provider(provider, listing_html, source_url, max_candidates_per_source)
        candidates = candidates[:max_candidates_per_source]
        parsed_count = 0
        for candidate in candidates:
            candidate_url = candidate["url"]
            detail_html = ""
            report_url = candidate_url
            keep_detail_page = provider.lower() in {"eastmoney", "choice"}
            if ".pdf" not in candidate_url.lower():
                try:
                    detail_html = _http_text_get(ctx, candidate_url, timeout=12, headers=_research_headers())
                except Exception:
                    detail_html = ""
                pdf_url = "" if keep_detail_page else (_extract_first_pdf_url(detail_html, candidate_url) if detail_html else "")
                if pdf_url:
                    report_url = pdf_url
            if not _is_original_research_url(report_url, allowed_domains=allowed_domains) or report_url in seen_urls:
                continue
            page_text = detail_html or listing_html
            title = _extract_html_title(page_text)
            if title.lower().strip() in {"news details", "zacks small cap research - news details"}:
                title = ""
            title = title or candidate.get("label") or f"{provider} research report"
            summary = _extract_html_summary(page_text)
            published_at = _extract_html_published_at(page_text)
            symbol = _symbol_from_text(f"{title} {summary} {candidate_url} {report_url}")
            row = _broker_research_row(
                ctx,
                title=title,
                summary=summary,
                report_url=report_url,
                source=provider,
                published_at=published_at,
                quote_cache=quote_cache,
                explicit_symbol=symbol,
            )
            if row:
                rows.append(row)
                seen_urls.add(report_url)
                parsed_count += 1
            if len(rows) >= max(limit * 2, 18):
                break
        sources[source_key] = "ok" if parsed_count else ("empty" if candidates else "no-links")


def _build_broker_rows_from_configured_sources(
    ctx: FinanceWatchContext,
    limit: int,
) -> tuple[List[Dict[str, Any]], Dict[str, str]]:
    rows: List[Dict[str, Any]] = []
    sources: Dict[str, str] = {}
    quote_cache: Dict[str, Dict[str, Any]] = {}
    seen_urls = set()
    for source_url in _setting_tuple(ctx, "finance_broker_research_feed_urls"):
        source_key = urlparse(source_url).netloc or source_url
        try:
            raw_text = _http_text_get(ctx, source_url, timeout=16)
        except Exception:
            sources[source_key] = "error"
            continue
        parsed_count = 0
        stripped = raw_text.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                sources[source_key] = "parse-error"
                continue
            for item in _extract_json_research_items(payload):
                report_url = _coalesce_url(
                    _dict_value(
                        item,
                        "reportPageUrl",
                        "eastmoneyUrl",
                        "choiceUrl",
                        "originalUrl",
                        "sourceUrl",
                        "pageUrl",
                        "detailUrl",
                        "researchUrl",
                        "url",
                        "link",
                        "reportUrl",
                        "pdfUrl",
                        "documentUrl",
                    )
                )
                if not _is_original_research_url(report_url) or report_url in seen_urls:
                    continue
                row = _broker_research_row(
                    ctx,
                    title=_coalesce_text(_dict_value(item, "title", "headline", "reportTitle", "reportName", "researchTitle", "name")),
                    summary=_coalesce_text(_dict_value(item, "summary", "abstract", "description", "body")),
                    report_url=report_url,
                    source=_coalesce_text(_dict_value(item, "institution", "researchInstitution", "orgName", "orgSName", "broker", "brokerName", "firm", "source", "publisher", "provider")),
                    published_at=_published_iso(_dict_value(item, "publishedAt", "published", "publishDate", "publishTime", "reportDate", "noticeDate", "date", "createdAt", "time")),
                    quote_cache=quote_cache,
                    explicit_symbol=_coalesce_text(_dict_value(item, "symbol", "ticker", "ric", "securityCode", "secuCode", "stockCode", "code")),
                    explicit_company=_coalesce_text(_dict_value(item, "company", "companyName", "securityName", "stockName", "secuName")),
                    explicit_target=_dict_value(item, "targetPrice", "priceTarget", "pt"),
                    explicit_previous_target=_dict_value(item, "previousTargetPrice", "previousPriceTarget", "previousPt"),
                    explicit_action=_coalesce_text(_dict_value(item, "action", "ratingAction", "recommendation", "rating")),
                    explicit_rating=_coalesce_text(_dict_value(item, "rating", "ratingName", "investmentRating", "recommendation")),
                    explicit_analyst=_coalesce_text(_dict_value(item, "analyst", "analystName", "analysts", "author", "researcher", "researchAuthors")),
                )
                if row:
                    rows.append(row)
                    seen_urls.add(report_url)
                    parsed_count += 1
        else:
            try:
                root = ElementTree.fromstring(raw_text)
            except ElementTree.ParseError:
                sources[source_key] = "parse-error"
                continue
            for item in root.findall(".//item")[: max(20, limit * 5)]:
                report_url = _coalesce_url(item.findtext("link"), item.findtext("guid"))
                if not _is_original_research_url(report_url) or report_url in seen_urls:
                    continue
                row = _broker_research_row(
                    ctx,
                    title=_strip_html(item.findtext("title")),
                    summary=_strip_html(item.findtext("description")),
                    report_url=report_url,
                    source=_compact_source(item.findtext("source") or source_key),
                    published_at=_published_iso(item.findtext("pubDate")),
                    quote_cache=quote_cache,
                )
                if row:
                    rows.append(row)
                    seen_urls.add(report_url)
                    parsed_count += 1
        sources[source_key] = "ok" if parsed_count else "empty"
    _build_broker_rows_from_open_sources(ctx, limit=limit, rows=rows, sources=sources, quote_cache=quote_cache, seen_urls=seen_urls)
    rows.sort(key=lambda row: _safe_float(row.get("_priority")) or 0.0, reverse=True)
    for row in rows:
        row.pop("_priority", None)
    return rows[:limit], sources


def _build_broker_research_news_fallback(
    ctx: FinanceWatchContext,
    limit: int,
) -> Dict[str, Any]:
    try:
        xml_text = _http_text_get(ctx, _news_url(ctx, _broker_research_query()), timeout=14)
    except Exception as exc:
        return _payload("broker-research-watch", title="BROKER RESEARCH", items=[], status="error", sources={"googleNews": "error"}, summary={"error": str(exc)})
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return _payload("broker-research-watch", title="BROKER RESEARCH", items=[], status="empty", sources={"googleNews": "parse-error"})

    rows: List[Dict[str, Any]] = []
    seen_titles = set()
    quote_cache: Dict[str, Dict[str, Any]] = {}
    for item in root.findall(".//item")[: max(12, limit * 4)]:
        title = _strip_html(item.findtext("title"))
        description = _strip_html(item.findtext("description"))
        source = _compact_source(item.findtext("source") or "Google News")
        text = f"{title} {description}"
        match = _find_broker_symbol(text)
        if not match or title in seen_titles:
            continue
        seen_titles.add(title)
        symbol, company, theme = match
        broker = _find_broker_name(text, source)
        action, action_tone = _broker_action(text)
        target, previous_target = _extract_target_prices(text)
        if not _is_broker_research_candidate(text, source, action, target):
            continue
        if symbol not in quote_cache:
            quote_cache[symbol] = _fetch_yahoo_snapshot(ctx, symbol, interval="30m", range_name="5d") or {}
        quote = quote_cache.get(symbol) or {}
        current_price = _safe_float(quote.get("price"))
        upside = ((target - current_price) / current_price * 100) if target is not None and current_price not in (None, 0) else None
        target_change = ((target - previous_target) / previous_target * 100) if target is not None and previous_target not in (None, 0) else None
        published_raw = item.findtext("pubDate")
        published_at = None
        if published_raw:
            try:
                published_at = parsedate_to_datetime(published_raw).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            except (TypeError, ValueError):
                published_at = None
        tags = [action]
        if target_change is not None:
            price_target_tag = "PT RAISE" if target_change > 0 else "PT CUT" if target_change < 0 else "PT"
            if price_target_tag not in tags:
                tags.append(price_target_tag)
        tags.append(theme)
        tone = "up" if (upside is not None and upside > 0) or action_tone == "up" else "down" if (upside is not None and upside < 0) or action_tone == "down" else "watch" if action_tone == "watch" else "neutral"
        target_label = f"PT {_format_price(target)}" if target is not None else (_format_price(current_price) if current_price is not None else "--")
        metric_label = f"{upside:+.1f}%" if upside is not None else target_label
        summary_bits = [broker, action.replace("PT ", "target ")]
        if target is not None:
            summary_bits.append(f"target {target_label}")
        if previous_target is not None:
            summary_bits.append(f"from {_format_price(previous_target)}")
        if current_price is not None:
            summary_bits.append(f"spot {_format_price(current_price)}")
        rows.append(
            {
                "id": f"broker-research:{symbol}:{abs(hash(title))}",
                "label": symbol,
                "symbol": company.upper()[:16],
                "title": title,
                "summary": " | ".join(summary_bits),
                "source": broker,
                "url": item.findtext("link"),
                "publishedAt": published_at,
                "metric": upside,
                "metricLabel": metric_label,
                "metricUnit": "UPSIDE",
                "secondary": target,
                "secondaryLabel": target_label,
                "change": target_change,
                "changeLabel": f"{target_change:+.1f}% PT" if target_change is not None else (_format_pct(quote.get("changePercent")) if quote.get("changePercent") is not None else None),
                "tags": tags[:3],
                "tone": tone,
                "points": quote.get("points") or [],
                "_priority": _broker_priority(action, upside, target_change, published_at, symbol),
            }
        )
        if len(rows) >= max(limit * 2, 18):
            break
    rows.sort(key=lambda row: _safe_float(row.get("_priority")) or 0.0, reverse=True)
    for row in rows:
        row.pop("_priority", None)
    upgrades = sum(1 for row in rows if "UPGRADE" in (row.get("tags") or []))
    cuts = sum(1 for row in rows if any(tag in {"DOWNGRADE", "PT CUT"} for tag in (row.get("tags") or [])))
    return _payload(
        "broker-research-watch",
        title="BROKER RESEARCH",
        items=rows[:limit],
        summary={"upgrades": upgrades, "cuts": cuts, "topSymbol": rows[0]["label"] if rows else None, "rankBy": "rating action + target revision + recency"},
        sources={"googleNewsFallback": "ok" if rows else "empty", "yahoo": "ok" if quote_cache else "empty"},
    )


def build_broker_research_payload(
    ctx: FinanceWatchContext,
    limit: int,
) -> Dict[str, Any]:
    rows, sources = _build_broker_rows_from_configured_sources(ctx, limit)
    if rows:
        upgrades = sum(1 for row in rows if "UPGRADE" in (row.get("tags") or []))
        cuts = sum(1 for row in rows if any(tag in {"DOWNGRADE", "PT CUT"} for tag in (row.get("tags") or [])))
        return _payload(
            "broker-research-watch",
            title="BROKER RESEARCH",
            items=rows,
            summary={"upgrades": upgrades, "cuts": cuts, "topSymbol": rows[0]["label"], "rankBy": "original report + target revision + recency"},
            sources=sources or {"brokerResearchFeeds": "empty"},
        )
    if _setting_bool(ctx, "finance_broker_research_news_fallback"):
        return _build_broker_research_news_fallback(ctx, limit)
    return _payload(
        "broker-research-watch",
        title="BROKER RESEARCH",
        items=[],
        status="empty",
        summary={
            "reason": "No original broker research metadata source produced rows",
            "requiredEnv": "POLYDATA_FINANCE_BROKER_RESEARCH_FEED_URLS or POLYDATA_FINANCE_BROKER_RESEARCH_EASTMONEY_URL / POLYDATA_FINANCE_BROKER_RESEARCH_CHOICE_URL",
        },
        sources=sources or {"brokerResearchFeeds": "missing"},
    )


def _read_finance_external(
    ctx: FinanceWatchContext,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    payload = finance_external_sources_service.read_finance_external_sources(
        dependencies.external_sources,
    )
    if isinstance(payload, dict) and payload:
        return payload
    try:
        return finance_external_sources_service.build_finance_external_sources_payload(
            dependencies.external_sources,
        )
    except Exception:
        return {}


def _payload(panel_id: str, *, title: str, items: List[Dict[str, Any]], summary: Optional[Dict[str, Any]] = None, sources: Optional[Dict[str, Any]] = None, status: Optional[str] = None, generated_at: Optional[str] = None) -> Dict[str, Any]:
    return {
        "generatedAt": generated_at or utc_now_iso(),
        "status": status or ("ok" if items else "empty"),
        "cacheMode": "live-build",
        "panelId": panel_id,
        "title": title,
        "sources": sources or {},
        "summary": {"count": len(items), **(summary or {})},
        "items": items,
    }


def build_defi_yields_payload(
    ctx: FinanceWatchContext,
    limit: int,
) -> Dict[str, Any]:
    raw = _http_json_get(ctx, _setting(ctx, "finance_defillama_yields_url"), timeout=16)
    pools = raw.get("data") if isinstance(raw, dict) else []
    rows: List[Dict[str, Any]] = []
    for pool in pools or []:
        if not isinstance(pool, dict):
            continue
        tvl = _safe_float(pool.get("tvlUsd"))
        apy = _safe_float(pool.get("apy"))
        project = str(pool.get("project") or "Protocol")
        project_key = project.lower().strip()
        protocol_score = RELIABLE_DEFI_PROJECTS.get(project_key, 0)
        if tvl is None or apy is None or tvl < 5_000_000 or apy <= 0 or apy > 60:
            continue
        if protocol_score <= 0 and tvl < 50_000_000:
            continue
        symbol = str(pool.get("symbol") or pool.get("underlyingTokens") or "").upper()
        stable_bonus = 18 if pool.get("stablecoin") else 0
        moderate_apy_bonus = max(0.0, 30.0 - abs(float(apy) - 8.0))
        risk_penalty = 45 if apy > 30 else 18 if apy > 18 else 0
        reliability = protocol_score + stable_bonus + math.log10(max(tvl, 1.0)) * 8 + moderate_apy_bonus - risk_penalty
        tags = ["TVL"]
        if pool.get("stablecoin"):
            tags.append("STABLE")
        if project_key in RELIABLE_DEFI_PROJECTS:
            tags.append(project.split("-")[0].upper()[:8])
        if apy >= 25:
            tags.append("RISK")
        rows.append(
            {
                "id": str(pool.get("pool") or f"{project}:{symbol}"),
                "label": project.title(),
                "symbol": symbol[:18],
                "metric": apy,
                "metricLabel": f"{apy:.2f}%",
                "metricUnit": "APY",
                "secondary": tvl,
                "secondaryLabel": _format_usd(tvl),
                "change": _safe_float(pool.get("apyPct30D") or pool.get("apyPct7D")),
                "tags": tags[:4],
                "tone": "up" if apy < 18 else "watch",
                "reliabilityScore": reliability,
            }
        )
    rows.sort(key=lambda item: (_safe_float(item.get("reliabilityScore")) or 0.0, _safe_float(item.get("secondary")) or 0.0), reverse=True)
    return _payload("defi-yield-monitor", title="DEFI YIELDS", items=rows[:limit], summary={"topLabel": rows[0]["label"] if rows else None, "rankBy": "protocol reliability + TVL"}, sources={"defillamaYields": "ok" if rows else "empty"})


def build_news_payload(
    ctx: FinanceWatchContext,
    panel_id: str,
    title: str,
    limit: int,
) -> Dict[str, Any]:
    query = NEWS_QUERIES[panel_id]
    xml_text = _http_text_get(ctx, _news_url(ctx, query), timeout=16)
    rows = _parse_rss_items(xml_text, panel_id=panel_id, limit=limit)
    return _payload(panel_id, title=title, items=rows, summary={"query": query}, sources={"googleNewsRss": "ok" if rows else "empty"})


def build_crypto_perps_payload(
    ctx: FinanceWatchContext,
    limit: int,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    requested_limit = max(limit, len(CRYPTO_PERP_ORDER), 24)
    if dependencies.get_crypto_funding_watch_snapshot is not None:
        base = dependencies.get_crypto_funding_watch_snapshot(
            limit=requested_limit,
        )
    else:
        base = crypto_funding_service.get_crypto_funding_watch_snapshot(
            dependencies.crypto_funding,
            limit=requested_limit,
        )
    asset_map = {str(asset.get("asset") or asset.get("symbol") or "").upper(): asset for asset in (base.get("assets") or []) if isinstance(asset, dict)}
    ordered_assets = [asset_map[symbol] for symbol in CRYPTO_PERP_ORDER if symbol in asset_map]
    extras = [asset for symbol, asset in asset_map.items() if symbol not in CRYPTO_PERP_ORDER]
    rows: List[Dict[str, Any]] = []
    for asset in (ordered_assets + extras)[:limit]:
        if not isinstance(asset, dict):
            continue
        funding = _safe_float(asset.get("consensusFundingPercent") if asset.get("consensusFundingPercent") is not None else asset.get("maxAbsFundingPercent"))
        bias = str(asset.get("bias") or "mixed")
        signed = funding
        if funding is not None and bias == "shorts-pay":
            signed = -abs(funding)
        elif funding is not None:
            signed = abs(funding)
        quotes = [quote for quote in (asset.get("quotes") or []) if isinstance(quote, dict)]
        mark = next((_safe_float(quote.get("markPrice")) for quote in quotes if _safe_float(quote.get("markPrice")) is not None), None)
        exchange = str(quotes[0].get("exchange") or "PERP") if quotes else "PERP"
        rows.append(
            {
                "id": str(asset.get("symbol") or asset.get("asset")),
                "label": str(asset.get("asset") or asset.get("symbol") or "Perp"),
                "symbol": str(asset.get("symbol") or "").upper(),
                "metric": signed,
                "metricLabel": f"{signed:+.4f}%" if signed is not None else "--",
                "metricUnit": "FUND",
                "secondary": mark,
                "secondaryLabel": _format_price(mark),
                "tags": [exchange.upper()[:8], _bias_tag(bias)],
                "tone": "down" if signed and signed < 0 else ("up" if signed and signed > 0 else "neutral"),
            }
        )
    return _payload("crypto-perp-funding", title="CRYPTO PERPS", items=rows, summary={"venues": len(base.get("venues") or [])}, sources=base.get("sources") if isinstance(base.get("sources"), dict) else {"funding": base.get("status") or "ok"})


def build_tradfi_perps_payload(
    ctx: FinanceWatchContext,
    limit: int,
    external: Dict[str, Any],
) -> Dict[str, Any]:
    source = external.get("tradfiPerps") if isinstance(external.get("tradfiPerps"), dict) else {}
    rows: List[Dict[str, Any]] = []
    for item in source.get("items") or []:
        if not isinstance(item, dict):
            continue
        mark = _safe_float(item.get("markPx"))
        oracle = _safe_float(item.get("oraclePx"))
        basis_bps = _safe_float(item.get("basisBps"))
        change = _safe_float(item.get("changePercent") if item.get("changePercent") is not None else item.get("funding"))
        if basis_bps is None and mark is not None and oracle not in (None, 0):
            basis_bps = ((mark - float(oracle)) / float(oracle)) * 10000
        asset_class = str(item.get("assetClass") or "perp").upper()
        venue = str(item.get("venue") or item.get("source") or "PERP").upper()
        rows.append(
            {
                "id": str(item.get("symbol") or item.get("display")),
                "label": str(item.get("display") or f"{item.get('symbol')}-PERP"),
                "symbol": asset_class,
                "metric": mark,
                "metricLabel": _format_price(mark),
                "metricUnit": "MARK",
                "secondary": basis_bps,
                "secondaryLabel": f"{basis_bps:+.0f} bps" if basis_bps is not None and abs(basis_bps) > 0 else (_format_usd(item.get("dayNotional")) if item.get("dayNotional") is not None else "--"),
                "change": change,
                "changeLabel": _format_pct(change),
                "tags": [venue[:8], asset_class],
                "tone": _tone(change if change is not None else basis_bps),
            }
        )
    rows.sort(
        key=lambda item: (
            TRADFI_PERP_DISPLAY_PRIORITY.get(str(item.get("label") or "").upper(), 0),
            TRADFI_PERP_CLASS_PRIORITY.get(str(item.get("symbol") or "").upper(), 0),
            abs(_safe_float(item.get("change")) or _safe_float(item.get("secondary")) or 0.0),
        ),
        reverse=True,
    )
    return _payload("tradfi-perp-radar", title="TRADFI PERPS", items=rows[:limit], sources={"financeExternal": source.get("status") or "seed"})


def build_global_indices_payload(
    ctx: FinanceWatchContext,
    limit: int,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for label, symbol, region in GLOBAL_INDEX_SYMBOLS[:limit]:
        snapshot = _fetch_yahoo_snapshot(ctx, symbol, interval="30m", range_name="5d")
        if not isinstance(snapshot, dict):
            continue
        change = _safe_float(snapshot.get("changePercent"))
        rows.append(
            {
                "id": symbol,
                "label": label,
                "symbol": region,
                "metric": snapshot.get("price"),
                "metricLabel": _format_price(snapshot.get("price")),
                "metricUnit": "IDX",
                "change": change,
                "changeLabel": f"{change:+.2f}%" if change is not None else "--",
                "points": snapshot.get("points") or [],
                "tags": [region],
                "tone": _tone(change),
            }
        )
    avg = sum(_safe_float(row.get("change")) or 0.0 for row in rows) / len(rows) if rows else None
    return _payload("global-index-monitor", title="GLOBAL INDICES", items=rows, summary={"riskTone": "RISK ON" if avg and avg > 0 else "RISK OFF" if avg and avg < 0 else "MIXED", "avgChange": avg}, sources={"yahoo": "ok" if rows else "empty"})


def build_fear_greed_payload(
    ctx: FinanceWatchContext,
    limit: int,
) -> Dict[str, Any]:
    raw = _http_json_get(ctx, _setting(ctx, "finance_alternative_fng_url"), params={"limit": 2, "format": "json"}, timeout=12)
    data = (raw.get("data") or []) if isinstance(raw, dict) else []
    item = data[0] if data and isinstance(data[0], dict) else {}
    previous = data[1] if len(data) > 1 and isinstance(data[1], dict) else {}
    crypto_score = _safe_float(item.get("value"))
    crypto_prev_score = _safe_float(previous.get("value"))
    cnn = _fetch_cnn_fng(ctx)
    aaii = _fetch_aaii_sentiment(ctx)
    put_call = _fetch_barchart_last_price(ctx, "%24CPC")
    pct_above_200d = _fetch_barchart_last_price(ctx, "%24S5TH")
    yahoo = {symbol: _fetch_yahoo_closes(ctx, symbol, range_name="1y") for symbol in ("^GSPC", "^VIX", "^VIX3M", "^SKEW", "GLD", "TLT", "HYG", "SPY", "RSP", "DX-Y.NYB")}
    fred = {series: _fetch_fred_observations(ctx, series) for series in ("BAMLH0A0HYM2", "BAMLC0A0CM", "DGS10", "FEDFUNDS", "T10Y2Y", "UNRATE", "M2SL", "WALCL", "SOFR")}
    vix = _safe_float(yahoo.get("^VIX", {}).get("price")) or _fred_latest(_fetch_fred_observations(ctx, "VIXCLS"))
    vix3m = _safe_float(yahoo.get("^VIX3M", {}).get("price"))
    skew = _safe_float(yahoo.get("^SKEW", {}).get("price"))
    spx_closes = [float(value) for value in yahoo.get("^GSPC", {}).get("closes", [])]
    spy_closes = [float(value) for value in yahoo.get("SPY", {}).get("closes", [])]
    rsp_closes = [float(value) for value in yahoo.get("RSP", {}).get("closes", [])]
    hyg_closes = [float(value) for value in yahoo.get("HYG", {}).get("closes", [])]
    tlt_closes = [float(value) for value in yahoo.get("TLT", {}).get("closes", [])]
    gld_closes = [float(value) for value in yahoo.get("GLD", {}).get("closes", [])]
    dxy_closes = [float(value) for value in yahoo.get("DX-Y.NYB", {}).get("closes", [])]

    def category(name: str, score_value: float, weight: float, degraded: bool = False) -> Dict[str, Any]:
        rounded = round(_clamp(score_value))
        return {
            "id": name,
            "label": _category_label(name),
            "score": rounded,
            "weight": weight,
            "contribution": round(rounded * weight, 1),
            "tone": _score_tone(rounded),
            "degraded": degraded,
        }

    sentiment_score = cnn.get("score") if cnn else crypto_score if crypto_score is not None else 50
    if aaii and cnn:
        bull_score = _clamp((aaii["bull"] / 60.0) * 100)
        bear_score = 100 - _clamp((aaii["bear"] / 55.0) * 100)
        sentiment_score = float(cnn["score"]) * 0.4 + bull_score * 0.3 + bear_score * 0.3
    vix_score = 50 if vix is None else _clamp(100 - ((float(vix) - 12) / 23) * 100)
    term_score = 50 if not vix or not vix3m else (70 if float(vix) / float(vix3m) < 1 else 30)
    positioning_score = 50
    if put_call is not None or skew is not None:
        pc_score = _clamp(100 - (((put_call or 0.9) - 0.7) / 0.6) * 100)
        skew_score = _clamp(100 - (((skew or 125) - 100) / 50) * 100)
        positioning_score = pc_score * 0.6 + skew_score * 0.4
    trend_score = 50
    if spx_closes:
        price = spx_closes[-1]
        above = sum(1 for avg in (_sma(spx_closes, 20), _sma(spx_closes, 50), _sma(spx_closes, 200)) if avg is not None and price > avg)
        dist_200 = ((price - _sma(spx_closes, 200)) / _sma(spx_closes, 200)) if _sma(spx_closes, 200) else 0
        trend_score = (above / 3) * 50 + _clamp(dist_200 * 500 + 50) * 0.5
    rsp_spy = ((_roc(rsp_closes, 30) or 0) - (_roc(spy_closes, 30) or 0)) if rsp_closes and spy_closes else None
    breadth_score = pct_above_200d if pct_above_200d is not None else _clamp((rsp_spy or 0) * 10 + 50)
    momentum_score = _clamp((_roc(spx_closes, 20) or 0) * 10 + 50) * 0.5 + _clamp(((_rsi(spx_closes) or 50) - 30) / 40 * 100) * 0.5
    m2_latest, m2_back = _fred_latest(fred["M2SL"]), _fred_back(fred["M2SL"], 52)
    walcl_latest, walcl_back = _fred_latest(fred["WALCL"]), _fred_back(fred["WALCL"], 4)
    m2_yoy = ((m2_latest - m2_back) / m2_back * 100) if m2_latest and m2_back else None
    fed_bs_mom = ((walcl_latest - walcl_back) / walcl_back * 100) if walcl_latest and walcl_back else None
    sofr = _fred_latest(fred["SOFR"])
    liquidity_score = (_clamp((m2_yoy or 0) * 5 + 50) * 0.4) + (_clamp((fed_bs_mom or 0) * 20 + 50) * 0.3) + (_clamp(100 - (sofr or 4.0) * 15) * 0.3)
    hy_spread = _fred_latest(fred["BAMLH0A0HYM2"])
    ig_spread = _fred_latest(fred["BAMLC0A0CM"])
    hy_score = 50 if hy_spread is None else _clamp(100 - ((hy_spread - 2.0) / 8.0) * 100)
    ig_score = 50 if ig_spread is None else _clamp(100 - ((ig_spread - 0.4) / 2.6) * 100)
    credit_score = hy_score * 0.55 + ig_score * 0.45
    fed_rate = _fred_latest(fred["FEDFUNDS"])
    curve = _fred_latest(fred["T10Y2Y"])
    unrate = _fred_latest(fred["UNRATE"])
    macro_score = (_clamp(100 - (fed_rate or 4.0) * 15) * 0.3) + ((60 + (curve or 0) * 20 if (curve or 0) > 0 else 40 + (curve or 0) * 40) * 0.4) + (_clamp(100 - ((unrate or 4.0) - 3.5) * 20) * 0.3)
    gold_signal = 30 if (_roc(gld_closes, 30) or 0) > (_roc(spy_closes, 30) or 0) else 70
    bond_signal = 30 if (_roc(tlt_closes, 30) or 0) > (_roc(spy_closes, 30) or 0) else 70
    dxy_signal = 40 if (_roc(dxy_closes, 30) or 0) > 0 else 60
    cross_asset_score = (gold_signal + bond_signal + dxy_signal) / 3
    category_rows = [
        category("sentiment", float(sentiment_score), FNG_WEIGHTS["sentiment"], degraded=not bool(aaii)),
        category("volatility", vix_score * 0.7 + term_score * 0.3, FNG_WEIGHTS["volatility"], degraded=vix is None),
        category("positioning", positioning_score, FNG_WEIGHTS["positioning"], degraded=put_call is None),
        category("trend", trend_score, FNG_WEIGHTS["trend"], degraded=not bool(spx_closes)),
        category("breadth", float(breadth_score), FNG_WEIGHTS["breadth"], degraded=pct_above_200d is None),
        category("momentum", momentum_score, FNG_WEIGHTS["momentum"], degraded=not bool(spx_closes)),
        category("liquidity", liquidity_score, FNG_WEIGHTS["liquidity"], degraded=m2_yoy is None),
        category("credit", credit_score, FNG_WEIGHTS["credit"], degraded=hy_spread is None),
        category("macro", _clamp(macro_score), FNG_WEIGHTS["macro"], degraded=fed_rate is None),
        category("crossAsset", cross_asset_score, FNG_WEIGHTS["crossAsset"], degraded=not bool(spy_closes)),
    ]
    score = round(sum(float(row["score"]) * float(row["weight"]) for row in category_rows), 1)
    if not any(not row.get("degraded") for row in category_rows):
        score = crypto_score
    previous_score = None
    dependencies = _dependencies(ctx)
    if dependencies.snapshot_store is not None:
        prior = dependencies.snapshot_store.get_stale(
            finance_watch_namespace("crypto-fear-greed"),
            FINANCE_WATCH_CACHE_KEY,
        )
        if isinstance(prior, dict) and (prior.get("summary") or {}).get("categories"):
            previous_score = _safe_float((prior.get("headline") or {}).get("score"))
    delta = score - previous_score if score is not None and previous_score is not None else None
    label = _score_label(score).upper()
    regime = _fear_greed_regime(score)
    drivers = []
    crypto_drivers = (
        ("BTC", "BTC-USD", "MOMENTUM", False),
        ("ETH", "ETH-USD", "MOMENTUM", False),
        ("SOL", "SOL-USD", "MOMENTUM", False),
        ("BNB", "BNB-USD", "MOMENTUM", False),
    )
    macro_drivers = (
        ("SPX", "^GSPC", "TREND", False),
        ("VIX", "^VIX", "VOL", True),
        ("HYG", "HYG", "CREDIT", False),
        ("TLT", "TLT", "RATES", True),
        ("GLD", "GLD", "HAVEN", True),
        ("DXY", "DX-Y.NYB", "USD", True),
        ("RSP", "RSP", "BREADTH", False),
        ("SPY", "SPY", "EQUITY", False),
    )
    for label_name, symbol, tag, inverted in crypto_drivers + macro_drivers:
        quote = yahoo.get(symbol) if symbol in yahoo else _fetch_yahoo_snapshot(ctx, symbol, interval="5m", range_name="1d")
        if isinstance(quote, dict):
            change = _safe_float(quote.get("changePercent"))
            tone_value = _tone((-change if inverted and change is not None else change))
            drivers.append(
                {
                    "id": symbol,
                    "label": label_name,
                    "symbol": tag,
                    "metricLabel": _format_price(quote.get("price")),
                    "change": change,
                    "changeLabel": _format_pct(change),
                    "tags": [tag],
                    "tone": tone_value,
                }
            )
    header_metrics = [
        {"label": "VIX", "value": _format_plain(vix, digits=2), "tone": "down" if vix and vix >= 25 else "up" if vix and vix <= 16 else "watch"},
        {"label": "HY Spread", "value": f"{hy_spread:.2f}%" if hy_spread is not None else "--", "tone": "down" if hy_spread and hy_spread >= 5 else "up"},
        {"label": "10Y Yield", "value": f"{_fred_latest(fred['DGS10']):.2f}%" if _fred_latest(fred["DGS10"]) is not None else "--", "tone": "neutral"},
        {"label": "P/C Ratio", "value": _format_plain(put_call, digits=2), "tone": "down" if put_call and put_call >= 1 else "up" if put_call else "neutral"},
        {"label": "% > 200d", "value": f"{pct_above_200d:.1f}%" if pct_above_200d is not None else "--", "tone": "up" if pct_above_200d and pct_above_200d >= 50 else "down"},
        {"label": "CNN F&G", "value": str(cnn.get("score")) if cnn else "--", "tone": _score_tone(cnn.get("score") if cnn else None)},
        {"label": "AAII Bull", "value": f"{aaii['bull']:.1f}%" if aaii else "--", "tone": "up" if aaii and aaii["bull"] >= 35 else "down" if aaii else "neutral"},
        {"label": "AAII Bear", "value": f"{aaii['bear']:.1f}%" if aaii else "--", "tone": "down" if aaii and aaii["bear"] >= 35 else "up" if aaii else "neutral"},
        {"label": "Fed Rate", "value": f"{fed_rate:.2f}%" if fed_rate is not None else "--", "tone": "neutral"},
    ]
    payload = _payload(
        "crypto-fear-greed",
        title="FEAR & GREED",
        items=drivers[:limit],
        summary={"score": score, "classification": label, "headerMetrics": header_metrics, "categories": category_rows, "cryptoScore": crypto_score, "cryptoPreviousScore": crypto_prev_score},
        sources={"alternativeMe": "ok" if crypto_score is not None else "empty", "cnn": "ok" if cnn else "empty", "aaii": "ok" if aaii else "empty", "fred": "ok" if hy_spread is not None else "empty"},
        status="ok" if score is not None else "empty",
    )
    payload["headline"] = {"label": label, "score": score, "previousScore": previous_score, "delta": delta, "regime": regime, "tone": "up" if score and score >= 55 else ("down" if score and score <= 45 else "neutral")}
    return payload


def build_crypto_etf_payload(
    ctx: FinanceWatchContext,
    limit: int,
    external: Dict[str, Any],
) -> Dict[str, Any]:
    source = external.get("etfFlow") if isinstance(external.get("etfFlow"), dict) else {}
    rows = []
    for item in source.get("items") or []:
        if not isinstance(item, dict):
            continue
        flow = _safe_float(item.get("flowProxyUsd"))
        change = _safe_float(item.get("changePercent"))
        symbol = str(item.get("symbol") or "ETF")
        issuer = str(item.get("issuer") or ("BTC ETF" if symbol not in {"ETHA", "FETH"} else "ETH ETF"))
        rows.append(
            {
                "id": symbol,
                "label": symbol,
                "symbol": _short_etf_issuer(issuer),
                "issuer": issuer,
                "metric": flow,
                "metricLabel": _format_usd(flow),
                "metricUnit": "FLOW",
                "secondary": item.get("volume"),
                "secondaryLabel": _format_volume(item.get("volume")),
                "change": change,
                "changeLabel": _format_pct(change),
                "tags": ["INFLOW" if flow and flow > 0 else "OUTFLOW" if flow and flow < 0 else "PROXY"],
                "tone": _tone(flow),
            }
        )
    total_volume = sum(_safe_float(row.get("secondary")) or 0.0 for row in rows)
    inflows = sum(1 for row in rows if (_safe_float(row.get("metric")) or 0.0) > 0)
    outflows = sum(1 for row in rows if (_safe_float(row.get("metric")) or 0.0) < 0)
    return _payload("crypto-etf-flow", title="CRYPTO ETF", items=rows[:limit], summary={"netFlowProxyUsd": source.get("netFlowProxyUsd"), "totalVolume": total_volume, "inflowCount": inflows, "outflowCount": outflows}, sources={"financeExternal": source.get("status") or "seed"})


def build_stablecoin_payload(
    ctx: FinanceWatchContext,
    limit: int,
    external: Dict[str, Any],
) -> Dict[str, Any]:
    source = external.get("stablecoin") if isinstance(external.get("stablecoin"), dict) else {}
    rows = []
    for item in source.get("items") or []:
        if not isinstance(item, dict):
            continue
        deviation = _safe_float(item.get("deviationBps"))
        change = _safe_float(item.get("change7dPct"))
        rows.append(
            {
                "id": str(item.get("symbol") or item.get("name")),
                "label": str(item.get("symbol") or "Stablecoin"),
                "symbol": str(item.get("name") or "SUPPLY")[:18],
                "metric": item.get("price"),
                "metricLabel": f"{_format_price(item.get('price'), digits=4)}  SUPPLY {_format_usd(item.get('supplyUsd'))}",
                "metricUnit": "PEG",
                "secondary": item.get("supplyUsd"),
                "secondaryLabel": None,
                "change": change,
                "changeLabel": _format_pct(change),
                "tags": ["WATCH"] if abs(deviation or 0.0) >= 20 else [],
                "tone": "down" if abs(deviation or 0.0) >= 20 else _tone(change),
            }
        )
    return _payload("stablecoin-monitor", title="STABLECOINS", items=rows[:limit], summary={"totalSupplyUsd": source.get("totalSupplyUsd"), "supplyChange7dPct": source.get("supplyChange7dPct"), "stressedCount": source.get("stressedCount")}, sources={"financeExternal": source.get("status") or "seed"})


def build_finance_watch_panel_payload(
    ctx: FinanceWatchContext,
    panel_id: str,
    limit: int = 10,
    external: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    limit = max(3, min(36, int(limit or 10)))
    if panel_id == "defi-yield-monitor":
        return build_defi_yields_payload(dependencies, limit)
    if panel_id == "defi-security-watch":
        return build_news_payload(
            dependencies,
            panel_id,
            "DEFI SECURITY",
            limit,
        )
    if panel_id == "crypto-perp-funding":
        return build_crypto_perps_payload(dependencies, limit)
    if panel_id == "tradfi-perp-radar":
        return build_tradfi_perps_payload(
            dependencies,
            limit,
            external or _read_finance_external(dependencies),
        )
    if panel_id == "ipo-news-watch":
        return build_news_payload(
            dependencies,
            panel_id,
            "IPO NEWS",
            limit,
        )
    if panel_id == "broker-research-watch":
        return build_broker_research_payload(dependencies, limit)
    if panel_id == "global-index-monitor":
        return build_global_indices_payload(dependencies, limit)
    if panel_id == "crypto-fear-greed":
        return build_fear_greed_payload(dependencies, limit)
    if panel_id == "crypto-etf-flow":
        return build_crypto_etf_payload(
            dependencies,
            limit,
            external or _read_finance_external(dependencies),
        )
    if panel_id == "stablecoin-monitor":
        return build_stablecoin_payload(
            dependencies,
            limit,
            external or _read_finance_external(dependencies),
        )
    if panel_id == "blockchain-policy-news":
        return build_news_payload(
            dependencies,
            panel_id,
            "CHAIN POLICY",
            limit,
        )
    raise KeyError(f"unknown finance watch panel: {panel_id}")


def build_all_finance_watch_panel_payloads(
    ctx: FinanceWatchContext,
    limit: int = 24,
) -> Dict[str, Dict[str, Any]]:
    dependencies = _dependencies(ctx)
    external = _read_finance_external(dependencies)
    payloads: Dict[str, Dict[str, Any]] = {}
    for panel_id in FINANCE_WATCH_PANEL_IDS:
        try:
            payloads[panel_id] = build_finance_watch_panel_payload(
                dependencies,
                panel_id,
                limit=limit,
                external=external,
            )
        except Exception as exc:
            payloads[panel_id] = _payload(panel_id, title=panel_id.upper(), items=[], status="error", sources={"builder": "error"}, summary={"error": str(exc)})
    return payloads


def _read_seeded_snapshot(
    ctx: FinanceWatchContext,
    panel_id: str,
    ttl_seconds: int,
) -> Optional[Dict[str, Any]]:
    dependencies = _dependencies(ctx)
    namespace = finance_watch_namespace(panel_id)
    if dependencies.get_cached_json is not None:
        redis_payload = dependencies.get_cached_json(
            namespace,
            FINANCE_WATCH_CACHE_KEY,
        )
        if isinstance(redis_payload, dict):
            if dependencies.snapshot_store is None:
                raise RuntimeError("SNAPSHOT_STORE dependency missing")
            dependencies.snapshot_store.set(
                namespace,
                FINANCE_WATCH_CACHE_KEY,
                redis_payload,
                ttl_seconds,
            )
            return {**redis_payload, "cacheMode": str(redis_payload.get("cacheMode") or "redis-seed")}
    if dependencies.snapshot_store is None:
        return None
    sqlite_payload = dependencies.snapshot_store.get(
        namespace,
        FINANCE_WATCH_CACHE_KEY,
    )
    if isinstance(sqlite_payload, dict):
        if dependencies.set_cached_json is not None:
            dependencies.set_cached_json(
                namespace,
                FINANCE_WATCH_CACHE_KEY,
                sqlite_payload,
                ttl_seconds,
            )
        return {**sqlite_payload, "cacheMode": str(sqlite_payload.get("cacheMode") or "sqlite-seed")}
    stale_payload = dependencies.snapshot_store.get_stale(
        namespace,
        FINANCE_WATCH_CACHE_KEY,
    )
    if isinstance(stale_payload, dict):
        return {**stale_payload, "cacheMode": "stale-seed"}
    return None


def _trim_payload(payload: Dict[str, Any], limit: int) -> Dict[str, Any]:
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    return {**payload, "items": items[: max(0, int(limit or 10))], "summary": {**(payload.get("summary") if isinstance(payload.get("summary"), dict) else {}), "count": min(len(items), max(0, int(limit or 10))), "totalCount": len(items)}}


def get_finance_watch_panel_snapshot(
    ctx: FinanceWatchContext,
    panel_id: str,
    limit: int = 10,
) -> Dict[str, Any]:
    dependencies = _dependencies(ctx)
    limit = max(3, min(36, int(limit or 10)))
    ttl_seconds = FINANCE_WATCH_TTL_SECONDS
    seeded = _read_seeded_snapshot(
        dependencies,
        panel_id,
        ttl_seconds,
    )
    if seeded is not None:
        return _trim_payload(seeded, limit)

    def _builder() -> Dict[str, Any]:
        return build_finance_watch_panel_payload(
            dependencies,
            panel_id,
            limit=max(limit, 24),
        )

    if dependencies.get_snapshot_payload is not None:
        payload = dependencies.get_snapshot_payload(
            finance_watch_namespace(panel_id),
            FINANCE_WATCH_CACHE_KEY,
            _builder,
            ttl_seconds=ttl_seconds,
        )
    else:
        payload = _builder()
    return _trim_payload(payload if isinstance(payload, dict) else {}, limit)


def _format_usd(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    sign = "-" if number < 0 else ""
    number = abs(number)
    for suffix, divisor in (("T", 1_000_000_000_000), ("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if number >= divisor:
            return f"{sign}${number / divisor:.1f}{suffix}"
    return f"{sign}${number:.0f}"


def _format_volume(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    sign = "-" if number < 0 else ""
    number = abs(number)
    for suffix, divisor in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if number >= divisor:
            return f"{sign}{number / divisor:.1f}{suffix}"
    return f"{sign}{number:.0f}"


def _format_price(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    if abs(number) < 1:
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return f"{number:,.{digits}f}".rstrip("0").rstrip(".")


def _format_plain(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def _format_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    return f"{number:+.2f}%"


def _short_time(value: Any) -> str:
    if not value:
        return "--"
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "--"
    delta = timestamp - datetime.now(timezone.utc)
    hours = int(delta.total_seconds() // 3600)
    if hours > 0:
        return f"{hours}h reset"
    minutes = int(delta.total_seconds() // 60)
    return f"{max(0, minutes)}m reset"


def _bias_tag(value: str) -> str:
    if value == "longs-pay":
        return "LONGS PAY"
    if value == "shorts-pay":
        return "SHORTS PAY"
    return "MIXED"


def _fear_greed_regime(score: Any) -> str:
    value = _safe_float(score)
    if value is None:
        return "NEUTRAL"
    if value <= 20:
        return "CRISIS / RISK-OFF"
    if value <= 35:
        return "STRESSED / DEFENSIVE"
    if value <= 50:
        return "FRAGILE / HEDGED"
    if value <= 65:
        return "STABLE / NORMAL"
    return "STRONG / RISK-ON"


def _category_label(value: str) -> str:
    labels = {
        "sentiment": "Sentiment",
        "volatility": "Volatility",
        "positioning": "Positioning",
        "trend": "Trend",
        "breadth": "Breadth",
        "momentum": "Momentum",
        "liquidity": "Liquidity",
        "credit": "Credit",
        "macro": "Macro",
        "crossAsset": "Cross-Asset",
    }
    return labels.get(value, value)


def _short_etf_issuer(value: Any) -> str:
    issuer = str(value or "").strip()
    lowered = issuer.lower()
    if "ishares" in lowered:
        return "BlackRock"
    if "fidelity" in lowered:
        return "Fidelity"
    if "grayscale" in lowered:
        return "Grayscale"
    if "bitwise" in lowered:
        return "Bitwise"
    if "ark" in lowered or "21shares" in lowered:
        return "ARK/21Shares"
    if "vaneck" in lowered:
        return "VanEck"
    if "franklin" in lowered:
        return "Franklin"
    if "invesco" in lowered:
        return "Invesco"
    if "valkyrie" in lowered:
        return "Valkyrie"
    if "wisdomtree" in lowered:
        return "WisdomTree"
    return issuer or "ETF"
