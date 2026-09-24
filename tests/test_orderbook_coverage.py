from decimal import Decimal
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quant.orderbook.coverage import CoverageSelectionContext, build_coverage_target, classify_priority_topic, select_orderbook_coverage_targets


def test_classifies_priority_topics_from_category_tags_and_title():
    assert classify_priority_topic({"title": "2026 FIFA World Cup winner", "tags": ["sports"]}) is None
    assert classify_priority_topic(
        {"market_id": 44, "title": "Mexico vs South Africa - FIFA World Cup 2026 winner", "tags": ["sports"]},
        context=CoverageSelectionContext(frozenset({44}), frozenset()),
    ) == "worldcup"
    assert classify_priority_topic({"category": "crypto", "title": "Bitcoin above 100k?"}) == "crypto"
    assert classify_priority_topic({"category": "crypto", "title": "BTC Up or Down 5m"}) is None
    assert classify_priority_topic({"category": "crypto", "title": "Will Solana hit $200?"}) is None
    assert classify_priority_topic({"tags": '["politics", "election"]', "title": "Senate control?"}) == "politics"
    assert classify_priority_topic({"category": "weather", "title": "Rain in NYC?"}) is None
    assert classify_priority_topic({"title": "ICC T20 World Cup, Women: India vs Pakistan"}) is None


def test_build_coverage_target_assigns_hot_sampling_for_active_worldcup_match():
    target = build_coverage_target({
        "market_id": 101,
        "market_slug": "mexico-vs-south-africa-fifa-world-cup-2026",
        "market_title": "Mexico vs South Africa - FIFA World Cup 2026 winner",
        "category": "sports",
        "tags": ["world-cup", "soccer"],
        "yes_token_id": "yes",
        "no_token_id": "no",
        "volume_24h": "60000",
        "trade_count_24h": 220,
    }, context=CoverageSelectionContext(frozenset({101}), frozenset()))

    assert target is not None
    assert target.topic == "worldcup"
    assert target.tier == "hot"
    assert target.sample_interval_seconds == 15
    assert target.retention_days == 14
    assert target.priority_score > Decimal("0")


def test_active_worldcup_match_is_at_least_warm_when_volume_is_low():
    target = build_coverage_target({
        "market_id": 102,
        "market_slug": "mexico-vs-south-africa-fifa-world-cup-2026-low-volume",
        "market_title": "Mexico vs South Africa - FIFA World Cup 2026 total",
        "category": "sports",
        "tags": ["world-cup", "soccer"],
        "yes_token_id": "yes",
        "no_token_id": "no",
        "volume_24h": "0",
        "trade_count_24h": 0,
    }, context=CoverageSelectionContext(frozenset({102}), frozenset()))

    assert target is not None
    assert target.topic == "worldcup"
    assert target.tier == "warm"
    assert target.sample_interval_seconds == 60
    assert target.retention_days == 14


def test_active_worldcup_slug_matches_same_match_derivatives_only():
    context = CoverageSelectionContext(
        frozenset(),
        frozenset({"czech republic vs south africa"}),
        frozenset({"fifwc-cze-rsa-2026-06-18"}),
    )

    same_match = build_coverage_target({
        "market_id": 103,
        "market_slug": "fifwc-cze-rsa-2026-06-18-spread-away-4pt5",
        "market_title": "Spread: South Africa (-4.5)",
        "category": "sports",
        "tags": ["world-cup"],
        "yes_token_id": "yes",
        "no_token_id": "no",
    }, context=context)
    future_match = build_coverage_target({
        "market_id": 104,
        "market_slug": "fifwc-rsa-kr-2026-06-24-spread-home-5pt5",
        "market_title": "Spread: South Africa (-5.5)",
        "category": "sports",
        "tags": ["world-cup"],
        "yes_token_id": "yes",
        "no_token_id": "no",
    }, context=context)

    assert same_match is not None
    assert same_match.market_id == 103
    assert future_match is None


def test_select_coverage_targets_respects_topic_filter_and_limits():
    rows = [
        {
            "market_id": 1,
            "market_title": "Bitcoin above $100,000?",
            "category": "crypto",
            "yes_token_id": "y1",
            "no_token_id": "n1",
            "trade_count_24h": 300,
        },
        {
            "market_id": 2,
            "market_title": "Election winner",
            "category": "politics",
            "yes_token_id": "y2",
            "no_token_id": "n2",
            "trade_count_24h": 100,
        },
    ]

    targets = select_orderbook_coverage_targets(rows, global_limit=10, topics=["crypto"])

    assert [target.market_id for target in targets] == [1]


def test_select_coverage_targets_fairly_mixes_requested_topics():
    rows = [
        {
            "market_id": 1,
            "market_title": "US election winner",
            "category": "politics",
            "yes_token_id": "y1",
            "no_token_id": "n1",
            "volume_24h": "200000",
        },
        {
            "market_id": 2,
            "market_title": "Another election market",
            "category": "politics",
            "yes_token_id": "y2",
            "no_token_id": "n2",
            "volume_24h": "150000",
        },
        {
            "market_id": 3,
            "market_title": "Bitcoin above 100k?",
            "category": "crypto",
            "yes_token_id": "y3",
            "no_token_id": "n3",
            "volume_24h": "1000",
        },
    ]

    targets = select_orderbook_coverage_targets(rows, global_limit=2, topics=["crypto", "politics"])

    assert [target.topic for target in targets] == ["crypto", "politics"]


def test_select_coverage_targets_uses_active_worldcup_context_and_crypto_shape():
    rows = [
        {
            "market_id": 10,
            "market_title": "FIFA World Cup 2026 winner",
            "category": "sports",
            "yes_token_id": "y10",
            "no_token_id": "n10",
            "volume_24h": "100000",
        },
        {
            "market_id": 11,
            "market_title": "Mexico vs South Africa - FIFA World Cup 2026 winner",
            "category": "sports",
            "yes_token_id": "y11",
            "no_token_id": "n11",
            "volume_24h": "50000",
        },
        {
            "market_id": 12,
            "market_title": "BTC Up or Down 5m",
            "category": "crypto",
            "yes_token_id": "y12",
            "no_token_id": "n12",
            "volume_24h": "999999",
        },
        {
            "market_id": 13,
            "market_title": "What price will Ethereum hit in June?",
            "category": "crypto",
            "yes_token_id": "y13",
            "no_token_id": "n13",
            "volume_24h": "1000",
        },
    ]

    targets = select_orderbook_coverage_targets(
        rows,
        global_limit=10,
        context=CoverageSelectionContext(frozenset({11}), frozenset({"mexico vs south africa"})),
    )

    assert [target.market_id for target in targets] == [11, 13]
