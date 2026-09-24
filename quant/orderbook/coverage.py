"""Coverage policy for deciding which markets deserve live order book attention."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


PRIORITY_TOPICS: tuple[str, ...] = ("worldcup", "crypto", "politics")
DEFAULT_TOPIC_LIMITS: dict[str, int] = {"worldcup": 80, "crypto": 80, "politics": 120}
DEFAULT_GLOBAL_LIMIT = 250

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "worldcup": (
        "worldcup",
        "world cup",
        "world-cup",
        "fifa",
        "2026 fifa",
        "2026-fifa",
        "soccer world cup",
        "fifa-world-cup",
    ),
    "crypto": (
        "crypto",
        "bitcoin",
        "btc",
        "ethereum",
        "eth",
        "solana",
        "sol",
        "xrp",
        "doge",
        "defi",
        "stablecoin",
        "binance",
        "coinbase",
        "token",
        "hack",
    ),
    "politics": (
        "politics",
        "election",
        "elections",
        "trump",
        "biden",
        "president",
        "senate",
        "congress",
        "supreme court",
        "geopolitics",
        "diplomacy",
        "iran",
        "israel",
        "china",
        "taiwan",
        "ukraine",
        "russia",
        "tariff",
        "sanction",
    ),
}

WORLDCUP_EXCLUDE_KEYWORDS: tuple[str, ...] = ("icc", "t20", "cricket")
CRYPTO_ASSET_KEYWORDS: tuple[str, ...] = ("bitcoin", "btc", "ethereum", "eth")
CRYPTO_TARGET_KEYWORDS: tuple[str, ...] = (" above ", " hit ", "will hit", "price will", "what price", "when will")
CRYPTO_EXCLUDE_KEYWORDS: tuple[str, ...] = ("up or down", "updown", " 5m", "-5m", "5-minute", " 15m", "-15m", "15-minute")
TOPIC_BASE_SCORE: dict[str, int] = {"worldcup": 3200, "crypto": 2800, "politics": 2600}


@dataclass(frozen=True)
class CoverageSelectionContext:
    active_worldcup_market_ids: frozenset[int] = field(default_factory=frozenset)
    active_worldcup_terms: frozenset[str] = field(default_factory=frozenset)
    active_worldcup_slugs: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class OrderBookCoverageTarget:
    market_id: int
    market_slug: str
    market_title: str
    category: str
    tags: tuple[str, ...]
    yes_token_id: str
    no_token_id: str
    volume_24h: Decimal
    trade_count_24h: int
    topic: str
    tier: str
    priority_score: Decimal
    sample_interval_seconds: int
    retention_days: int
    reason: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "marketId": self.market_id,
            "marketSlug": self.market_slug,
            "marketTitle": self.market_title,
            "category": self.category,
            "tags": list(self.tags),
            "yesTokenId": self.yes_token_id,
            "noTokenId": self.no_token_id,
            "volume24h": _decimal_text(self.volume_24h),
            "tradeCount24h": self.trade_count_24h,
            "topic": self.topic,
            "tier": self.tier,
            "priorityScore": _decimal_text(self.priority_score),
            "sampleIntervalSeconds": self.sample_interval_seconds,
            "retentionDays": self.retention_days,
            "reason": self.reason,
        }


def classify_priority_topic(row: dict[str, Any], *, context: CoverageSelectionContext | None = None) -> str | None:
    text = _row_search_text(row)
    for topic in PRIORITY_TOPICS:
        if topic == "worldcup":
            if any(keyword in text for keyword in WORLDCUP_EXCLUDE_KEYWORDS):
                continue
            if not _is_active_worldcup_market(row, text, context):
                continue
        if topic == "crypto" and not _is_trackable_crypto_market(text):
            continue
        if any(keyword in text for keyword in TOPIC_KEYWORDS[topic]):
            return topic
    return None


def build_coverage_target(row: dict[str, Any], *, context: CoverageSelectionContext | None = None) -> OrderBookCoverageTarget | None:
    topic = classify_priority_topic(row, context=context)
    if topic is None:
        return None
    market_id = _int_or_none(row.get("market_id") or row.get("id"))
    yes_token_id = str(row.get("yes_token_id") or row.get("yesTokenId") or "").strip()
    no_token_id = str(row.get("no_token_id") or row.get("noTokenId") or "").strip()
    if market_id is None or not yes_token_id or not no_token_id:
        return None
    volume_24h = _decimal(row.get("volume_24h") or row.get("volume24h") or 0)
    trade_count_24h = int(_int_or_none(row.get("trade_count_24h") or row.get("tradeCount24h")) or 0)
    tier, sample_interval_seconds, retention_days = _sampling_policy(volume_24h, trade_count_24h)
    if topic == "worldcup" and tier == "cold":
        tier, sample_interval_seconds, retention_days = "warm", 60, 14
    priority_score = _priority_score(topic, volume_24h, trade_count_24h, tier)
    tags = tuple(_parse_tags(row.get("tags")))
    return OrderBookCoverageTarget(
        market_id=market_id,
        market_slug=str(row.get("market_slug") or row.get("slug") or ""),
        market_title=str(row.get("market_title") or row.get("title") or ""),
        category=str(row.get("category") or ""),
        tags=tags,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        volume_24h=volume_24h,
        trade_count_24h=trade_count_24h,
        topic=topic,
        tier=tier,
        priority_score=priority_score,
        sample_interval_seconds=sample_interval_seconds,
        retention_days=retention_days,
        reason=f"{topic}:{tier}",
    )


def select_orderbook_coverage_targets(
    rows: Iterable[dict[str, Any]],
    *,
    topic_limits: dict[str, int] | None = None,
    global_limit: int = DEFAULT_GLOBAL_LIMIT,
    topics: Iterable[str] | None = None,
    context: CoverageSelectionContext | None = None,
) -> list[OrderBookCoverageTarget]:
    topic_allow = {topic for topic in (topics or PRIORITY_TOPICS) if topic in PRIORITY_TOPICS}
    limits = {**DEFAULT_TOPIC_LIMITS, **(topic_limits or {})}
    buckets: dict[str, list[OrderBookCoverageTarget]] = {topic: [] for topic in topic_allow}
    for row in rows:
        target = build_coverage_target(row, context=context)
        if target is None or target.topic not in topic_allow:
            continue
        buckets[target.topic].append(target)

    ranked_by_topic: dict[str, list[OrderBookCoverageTarget]] = {}
    for topic in PRIORITY_TOPICS:
        if topic not in buckets:
            continue
        ranked_by_topic[topic] = sorted(
            buckets[topic],
            key=lambda item: (item.priority_score, item.trade_count_24h, item.volume_24h, -item.market_id),
            reverse=True,
        )[: max(0, int(limits.get(topic, 0)))]

    selected: list[OrderBookCoverageTarget] = []
    seen_markets: set[int] = set()
    cursors = {topic: 0 for topic in ranked_by_topic}
    while len(selected) < max(0, int(global_limit)):
        progressed = False
        for topic in PRIORITY_TOPICS:
            ranked = ranked_by_topic.get(topic)
            if not ranked:
                continue
            idx = cursors.get(topic, 0)
            while idx < len(ranked) and ranked[idx].market_id in seen_markets:
                idx += 1
            cursors[topic] = idx
            if idx >= len(ranked):
                continue
            target = ranked[idx]
            selected.append(target)
            seen_markets.add(target.market_id)
            cursors[topic] = idx + 1
            progressed = True
            if len(selected) >= max(0, int(global_limit)):
                break
        if not progressed:
            break
    return selected


def summarize_coverage_targets(targets: Iterable[OrderBookCoverageTarget]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "topics": {topic: 0 for topic in PRIORITY_TOPICS},
        "tiers": {"hot": 0, "warm": 0, "cold": 0},
        "tokenCount": 0,
    }
    target_list = list(targets)
    for target in target_list:
        summary["topics"][target.topic] = int(summary["topics"].get(target.topic, 0)) + 1
        summary["tiers"][target.tier] = int(summary["tiers"].get(target.tier, 0)) + 1
    summary["marketCount"] = len(target_list)
    summary["tokenCount"] = len(target_list) * 2
    return summary


def _is_active_worldcup_market(row: dict[str, Any], text: str, context: CoverageSelectionContext | None) -> bool:
    if context is None:
        return False
    market_id = _int_or_none(row.get("market_id") or row.get("id"))
    if market_id is not None and market_id in context.active_worldcup_market_ids:
        return True
    slug = str(row.get("market_slug") or row.get("slug") or "").strip().lower()
    if slug and any(active_slug and slug.startswith(active_slug) for active_slug in context.active_worldcup_slugs):
        return True
    if " vs " not in text and "spread:" not in text and "total" not in text:
        return False
    return any(term and term in text for term in context.active_worldcup_terms)


def _is_trackable_crypto_market(text: str) -> bool:
    padded = f" {text} "
    if any(keyword in padded for keyword in CRYPTO_EXCLUDE_KEYWORDS):
        return False
    if not any(keyword in padded for keyword in CRYPTO_ASSET_KEYWORDS):
        return False
    return any(keyword in padded for keyword in CRYPTO_TARGET_KEYWORDS)


def _sampling_policy(volume_24h: Decimal, trade_count_24h: int) -> tuple[str, int, int]:
    if trade_count_24h >= 200 or volume_24h >= Decimal("50000"):
        return "hot", 15, 14
    if trade_count_24h >= 25 or volume_24h >= Decimal("5000"):
        return "warm", 60, 14
    return "cold", 300, 7


def _priority_score(topic: str, volume_24h: Decimal, trade_count_24h: int, tier: str) -> Decimal:
    base = Decimal(TOPIC_BASE_SCORE.get(topic, 0))
    trade_component = Decimal(min(max(trade_count_24h, 0), 1000)) * Decimal("2.5")
    volume_component = min(max(volume_24h, Decimal("0")), Decimal("250000")) / Decimal("100")
    tier_bonus = {"hot": Decimal("500"), "warm": Decimal("150"), "cold": Decimal("0")}.get(tier, Decimal("0"))
    return base + trade_component + volume_component + tier_bonus


def _row_search_text(row: dict[str, Any]) -> str:
    tags = " ".join(_parse_tags(row.get("tags")))
    fields = (
        row.get("category"),
        tags,
        row.get("market_title") or row.get("title"),
        row.get("market_slug") or row.get("slug"),
        row.get("event_title"),
        row.get("event_slug"),
    )
    return " ".join(str(value or "").lower().replace("_", "-") for value in fields)


def _parse_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [part.strip() for part in text.replace("{", "").replace("}", "").split(",") if part.strip()]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _decimal(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value or "0"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")
    return parsed if parsed.is_finite() else Decimal("0")


def _int_or_none(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return "0" if text == "-0" else text
