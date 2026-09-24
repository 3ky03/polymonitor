"""Bounded read-only MCP projection over canonical prediction-market services."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SERVER_VERSION = "0.1.0"
MCP_SCOPE = "mcp:read"
MAX_RESULT_BYTES = 256 * 1024


class McpProtocolError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True)
class McpDependencies:
    search_markets: Callable[..., dict[str, Any]]
    get_market_workspace: Callable[[int], dict[str, Any]]
    get_market_oracle: Callable[[int], dict[str, Any]]
    get_market_data_quality: Callable[[], dict[str, Any]]
    get_public_briefing: Callable[[str], dict[str, Any]]


def _object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise McpProtocolError(-32602, "arguments must be an object")
    return dict(value)


def _market_id(arguments: Mapping[str, Any]) -> int:
    try:
        value = int(str(arguments.get("market_id", "")))
    except (TypeError, ValueError) as exc:
        raise McpProtocolError(-32602, "market_id must be a positive integer") from exc
    if value < 1:
        raise McpProtocolError(-32602, "market_id must be a positive integer")
    return value


def _limit(arguments: Mapping[str, Any], name: str, default: int, maximum: int) -> int:
    try:
        value = int(arguments.get(name, default))
    except (TypeError, ValueError) as exc:
        raise McpProtocolError(-32602, f"{name} must be an integer") from exc
    return max(1, min(maximum, value))


def _pick(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {field: value.get(field) for field in fields if field in value}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


MARKET_FIELDS = (
    "id", "marketId", "localMarketId", "gammaMarketId", "title", "slug", "category",
    "status", "description", "endDate", "latestPrice", "latestYesPrice", "latestNoPrice",
    "change24h", "volume24h", "tradeCount24h", "completionStatus", "completionSource",
    "completionTime", "isTradingClosed", "isResolved", "isFinal",
)
PRICE_FIELDS = (
    "marketId", "localMarketId", "latestPrice", "latestYesPrice", "latestNoPrice",
    "change1h", "change24h", "volume24h", "tradeCount24h", "priceSource", "updatedAt",
)
ORACLE_FIELDS = (
    "marketId", "localMarketId", "conditionId", "questionId", "oracle", "currentStatus",
    "completionStatus", "settlementOutcome", "settlementSource", "isTradingClosed",
    "isResolved", "isFinal",
)
ORACLE_EVENT_FIELDS = (
    "id", "eventId", "eventStatus", "status", "blockNumber", "logIndex", "txHash",
    "transactionHash", "eventTime", "timestamp", "proposedOutcome", "outcome",
)
QUALITY_DIMENSION_FIELDS = (
    "id", "label", "status", "score", "weight", "value", "target", "detail",
    "observedAt", "source",
)
QUALITY_GAP_FIELDS = (
    "id", "severity", "status", "label", "detail", "count", "marketCount",
    "source", "observedAt",
)
QUALITY_MARKET_FIELDS = (
    "marketId", "title", "category", "completionStatus", "oracleStage",
    "observedAt", "reason", "severity",
)
WATERMARK_FIELDS = (
    "id", "label", "status", "source", "latestBlock", "latestObservation",
    "observedAt", "ageSeconds", "detail",
)
BRIEFING_MARKET_FIELDS = (
    "marketId", "title", "category", "latestPrice", "change24h", "volume24h",
    "tradeCount24h", "completionStatus", "oracleStage", "observedAt",
)
BRIEFING_SOURCE_FIELDS = ("kind", "markets", "oracle", "warning")
LEVEL_FIELDS = ("price", "size", "side")
SIDE_FIELDS = (
    "outcome", "side", "bookStatus", "bestBid", "bestAsk", "mid", "spread",
    "bidDepth", "askDepth", "depthTotal", "imbalance", "levelCountBid", "levelCountAsk",
    "snapshotTimestamp", "source",
)


def _sanitize_search(payload: Any) -> dict[str, Any]:
    items = _items(_mapping(payload).get("items"))
    return {"items": [_pick(item, MARKET_FIELDS) for item in items[:20]]}


def _sanitize_market(payload: Any) -> dict[str, Any]:
    value = _mapping(payload)
    evidence = _mapping(value.get("evidence"))
    diagnostics = _mapping(value.get("diagnostics"))
    return {
        "generatedAt": value.get("generatedAt"),
        "market": _pick(value.get("market"), MARKET_FIELDS),
        "selectedOutcome": _pick(value.get("selectedOutcome"), MARKET_FIELDS + ("label", "outcomeKey", "yesPrice", "noPrice")),
        "price": _pick(value.get("price"), PRICE_FIELDS),
        "health": _pick(value.get("health"), (
            "level", "marketId", "priceStatus", "oracleStatus", "lobStatus",
            "groupStatus", "servingStatus", "issues",
        )),
        "evidence": {
            "contractVersion": evidence.get("contractVersion"),
            "generatedAt": evidence.get("generatedAt"),
            "issues": _items(evidence.get("issues"))[:20],
        },
        "diagnostics": _pick(diagnostics, (
            "workspaceContract", "level", "issues", "identityStatus", "chartStatus",
            "oracleStatus", "oracleEventCount", "tradeCount", "hasPrice", "hasLobTokens",
        )),
    }


def _sanitize_oracle(payload: Any) -> dict[str, Any]:
    value = _mapping(payload)
    timeline = _items(value.get("timeline"))
    return {
        **_pick(value, ORACLE_FIELDS),
        "timeline": [_pick(item, ORACLE_EVENT_FIELDS) for item in timeline[:100]],
    }


def _sanitize_side(value: Any, depth: int) -> dict[str, Any]:
    side = _pick(value, SIDE_FIELDS)
    side_value = _mapping(value)
    if side_value:
        side["bids"] = [_pick(item, LEVEL_FIELDS) for item in _items(side_value.get("bids"))[:depth]]
        side["asks"] = [_pick(item, LEVEL_FIELDS) for item in _items(side_value.get("asks"))[:depth]]
    return side


def _sanitize_liquidity(payload: Any, depth: int) -> dict[str, Any]:
    value = _mapping(payload)
    lob = _mapping(value.get("lob"))
    return {
        "marketId": lob.get("marketId") or lob.get("localMarketId"),
        "marketTitle": lob.get("marketTitle"),
        "bookStatus": lob.get("bookStatus"),
        "source": lob.get("source"),
        "runtimeModel": lob.get("runtimeModel"),
        "snapshotSource": lob.get("snapshotSource"),
        "fetchedAt": lob.get("fetchedAt") or lob.get("updatedAt"),
        "yes": _sanitize_side(lob.get("yes"), depth),
        "no": _sanitize_side(lob.get("no"), depth),
    }


def _sanitize_quality(payload: Any) -> dict[str, Any]:
    value = _mapping(payload)
    oracle = _mapping(value.get("oracleLifecycle"))
    summary = _mapping(value.get("summary"))
    return {
        "contractVersion": value.get("contractVersion"),
        "generatedAt": value.get("generatedAt"),
        "status": value.get("status"),
        "score": value.get("score"),
        "summary": _pick(summary, (
            "marketCount", "pricedMarkets", "statusMarkets", "oracleEventCount",
            "activeGapCount", "freshWatermarks", "watermarkCount",
        )),
        "dimensions": [_pick(item, QUALITY_DIMENSION_FIELDS) for item in _items(value.get("dimensions"))[:20]],
        "gaps": [_pick(item, QUALITY_GAP_FIELDS) for item in _items(value.get("gaps"))[:20]],
        "gapMarkets": [_pick(item, QUALITY_MARKET_FIELDS) for item in _items(value.get("gapMarkets"))[:20]],
        "watermarks": [_pick(item, WATERMARK_FIELDS) for item in _items(value.get("watermarks"))[:20]],
        "oracleLifecycle": {
            "source": oracle.get("source"),
            "latestBlock": oracle.get("latestBlock"),
            "latestEventAt": oracle.get("latestEventAt"),
            "stages": [
                _pick(item, ("id", "label", "count"))
                for item in _items(oracle.get("stages"))[:10]
            ],
            "recentEvents": [
                _pick(item, ORACLE_EVENT_FIELDS + ("marketId", "marketTitle"))
                for item in _items(oracle.get("recentEvents"))[:20]
            ],
        },
    }


def _sanitize_briefing(payload: Any) -> dict[str, Any]:
    value = _mapping(payload)
    snapshot = _mapping(value.get("snapshot"))
    # workspaceLens intentionally remains excluded from MCP even though the public
    # web briefing renders a coarse panel lens.
    return {
        "title": value.get("title"),
        "createdAt": value.get("createdAt"),
        "expiresAt": value.get("expiresAt"),
        "snapshot": {
            "schema": snapshot.get("schema"),
            "generatedAt": snapshot.get("generatedAt"),
            "source": _pick(snapshot.get("source"), BRIEFING_SOURCE_FIELDS),
            "summary": _pick(snapshot.get("summary"), ("trackedMarkets", "topMarkets", "oracleAttention")),
            "trackedMarkets": [
                _pick(item, BRIEFING_MARKET_FIELDS)
                for item in _items(snapshot.get("trackedMarkets"))[:12]
            ],
            "topMarkets": [
                _pick(item, BRIEFING_MARKET_FIELDS)
                for item in _items(snapshot.get("topMarkets"))[:8]
            ],
            "oracleAttention": [
                _pick(item, BRIEFING_MARKET_FIELDS)
                for item in _items(snapshot.get("oracleAttention"))[:8]
            ],
        },
    }


def _sanitize_public_watchlist(payload: Any) -> dict[str, Any]:
    briefing = _sanitize_briefing(payload)
    snapshot = _mapping(briefing.get("snapshot"))
    return {
        "schema": "prediction-market-public-watchlist.v1",
        "briefingTitle": briefing.get("title"),
        "publishedAt": briefing.get("createdAt"),
        "expiresAt": briefing.get("expiresAt"),
        "generatedAt": snapshot.get("generatedAt"),
        "summary": {"trackedMarkets": len(snapshot.get("trackedMarkets") or [])},
        "markets": snapshot.get("trackedMarkets") or [],
        "source": "revocable-public-briefing",
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "search_markets",
        "description": "Search canonical Polymarket markets by title, slug, category, or tag.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 120},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
        }, "required": ["query"], "additionalProperties": False},
    },
    {
        "name": "get_market_overview",
        "description": "Read one canonical market overview with price, lifecycle health, and evidence diagnostics.",
        "inputSchema": {"type": "object", "properties": {
            "market_id": {"type": "integer", "minimum": 1},
        }, "required": ["market_id"], "additionalProperties": False},
    },
    {
        "name": "get_oracle_lifecycle",
        "description": "Read the bounded request, proposal, dispute, and settlement timeline for one market.",
        "inputSchema": {"type": "object", "properties": {
            "market_id": {"type": "integer", "minimum": 1},
        }, "required": ["market_id"], "additionalProperties": False},
    },
    {
        "name": "get_market_liquidity",
        "description": "Read a bounded YES/NO order-book snapshot without exposing LOB controls or private runtime state.",
        "inputSchema": {"type": "object", "properties": {
            "market_id": {"type": "integer", "minimum": 1},
            "depth": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
        }, "required": ["market_id"], "additionalProperties": False},
    },
    {
        "name": "get_data_quality",
        "description": "Read canonical identity, price, Oracle and synchronization coverage with active gaps.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_public_briefing",
        "description": "Read an unexpired, non-revoked public briefing capability link without private workspace layout.",
        "inputSchema": {"type": "object", "properties": {
            "public_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{32}$"},
        }, "required": ["public_id"], "additionalProperties": False},
    },
    {
        "name": "get_public_watchlist_snapshot",
        "description": "Read only tracked markets deliberately published inside a public briefing capability link.",
        "inputSchema": {"type": "object", "properties": {
            "public_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{32}$"},
        }, "required": ["public_id"], "additionalProperties": False},
    },
]


def _public_id(arguments: Mapping[str, Any]) -> str:
    value = str(arguments.get("public_id") or "").strip()
    if len(value) != 32 or not all(character.isalnum() or character in "_-" for character in value):
        raise McpProtocolError(-32602, "public_id must be a 32-character public briefing identifier")
    return value


def call_tool(name: str, arguments: Any, dependencies: McpDependencies) -> dict[str, Any]:
    params = _object(arguments)
    if name == "search_markets":
        query = str(params.get("query") or "").strip()
        if not query or len(query) > 120:
            raise McpProtocolError(-32602, "query must contain 1-120 characters")
        result = _sanitize_search(dependencies.search_markets(query, limit=_limit(params, "limit", 10, 20)))
    elif name == "get_market_overview":
        result = _sanitize_market(dependencies.get_market_workspace(_market_id(params)))
    elif name == "get_oracle_lifecycle":
        result = _sanitize_oracle(dependencies.get_market_oracle(_market_id(params)))
    elif name == "get_market_liquidity":
        result = _sanitize_liquidity(
            dependencies.get_market_workspace(_market_id(params)),
            _limit(params, "depth", 10, 20),
        )
    elif name == "get_data_quality":
        result = _sanitize_quality(dependencies.get_market_data_quality())
    elif name == "get_public_briefing":
        result = _sanitize_briefing(dependencies.get_public_briefing(_public_id(params)))
    elif name == "get_public_watchlist_snapshot":
        result = _sanitize_public_watchlist(dependencies.get_public_briefing(_public_id(params)))
    else:
        raise McpProtocolError(-32602, f"Unknown tool: {name}")

    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        raise McpProtocolError(-32603, "Tool result exceeded the 256 KiB response budget")
    return {
        "content": [{"type": "text", "text": encoded}],
        "structuredContent": result,
        "isError": False,
    }


def server_card(endpoint: str) -> dict[str, Any]:
    return {
        "name": "polymonitor",
        "kind": "product",
        "description": "Read-only canonical Polymarket market, Oracle, liquidity, quality, briefing and published Watchlist data.",
        "version": MCP_SERVER_VERSION,
        "url": endpoint,
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "transport": {"type": "streamableHttp", "endpoint": endpoint},
        "capabilities": {"tools": True},
        "authentication": {
            "type": "api_key",
            "header": "Authorization",
            "scheme": "Bearer",
            "scope": MCP_SCOPE,
        },
        "tools": [{"name": item["name"], "description": item["description"]} for item in TOOL_DEFINITIONS],
        "privacy": {
            "readOnly": True,
            "excluded": [
                "private workspace layouts", "private Watchlists", "notes", "alert rules",
                "credentials", "sessions", "administrator operations", "arbitrary API proxying",
            ],
        },
    }


def dispatch(method: str, params: Any, dependencies: McpDependencies) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "polymonitor", "version": MCP_SERVER_VERSION},
            "instructions": (
                "Prediction-market tools are read-only and bounded. Data-bearing tools/call requests "
                "require a Bearer API key with mcp:read. Public Watchlist data exists only inside an "
                "unexpired, non-revoked public briefing capability link."
            ),
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOL_DEFINITIONS}
    if method == "tools/call":
        body = _object(params)
        name = str(body.get("name") or "").strip()
        if not name:
            raise McpProtocolError(-32602, "tools/call requires a tool name")
        return call_tool(name, body.get("arguments"), dependencies)
    raise McpProtocolError(-32601, f"Method not found: {method}")
