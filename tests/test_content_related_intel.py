from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import content_service
from runtime.content_runtime import RuntimeContentItem, RuntimeContentProvider


class ContentRelatedIntelTestCase(unittest.TestCase):
    def test_related_content_payload_uses_snapshot_cache(self):
        snapshot_calls = []
        builder_calls = []

        def snapshot_getter(namespace, cache_key, builder, *, ttl_seconds):
            snapshot_calls.append((namespace, cache_key, ttl_seconds))
            return builder()

        ctx = {
            "get_snapshot_payload": snapshot_getter,
            "get_related_content_by_market_id": lambda market_id, limit=8: builder_calls.append((market_id, limit)) or {"items": [{"id": "n1"}]},
            "table_exists": lambda table_name: table_name in {"content_items", "content_links"},
            "query_one": lambda sql, params=(): {"links": 2, "link_created_at": "2026-06-01T00:00:00Z", "item_updated_at": "2026-06-01T00:01:00Z"},
        }

        payload = content_service.get_related_content_payload(ctx, 42, limit=20)

        self.assertEqual({"items": [{"id": "n1"}]}, payload)
        self.assertEqual([(42, 20)], builder_calls)
        self.assertEqual(1, len(snapshot_calls))
        self.assertEqual("snapshot:content:related", snapshot_calls[0][0])
        self.assertEqual(300, snapshot_calls[0][2])
        self.assertIn('"marketId": 42', snapshot_calls[0][1])

    def test_topic_ranking_keeps_news_when_other_intel_types_are_plentiful(self):
        provider = RuntimeContentProvider(feeds=[])
        topic = {"id": "sports", "label": "Sports", "keywords": ["nba", "player"], "categories": ["Sports"]}

        scored = []
        for index in range(8):
            scored.append((
                90 - index,
                RuntimeContentItem(
                    id=f"research-{index}",
                    content_type="research",
                    source="arXiv",
                    category="Sports",
                    title=f"NBA player performance research paper {index}",
                    url=f"https://arxiv.org/abs/{index}",
                    published_at=f"2026-06-0{index + 1}T00:00:00Z",
                    summary="nba player model",
                ),
            ))
        for index in range(5):
            scored.append((
                45 - index,
                RuntimeContentItem(
                    id=f"news-{index}",
                    content_type="news",
                    source="ESPN",
                    category="Sports",
                    title=f"NBA player lineup news {index}",
                    url=f"https://example.com/news/{index}",
                    published_at=f"2026-06-0{index + 1}T01:00:00Z",
                    summary="nba player update",
                ),
            ))

        ranked = provider._rank_topic_items_with_type_mix(scored=scored, topic=topic, limit=10)

        self.assertGreaterEqual(sum(1 for item in ranked if item.content_type == "news"), 3)
        self.assertLessEqual(len(ranked), 10)

    def test_generic_study_or_analysis_titles_remain_news(self):
        self.assertEqual(
            "news",
            RuntimeContentProvider._infer_content_type(
                source="ESPN",
                title="NBA player performance study changes matchup odds",
                url="https://example.com/nba-player-performance-study",
            ),
        )
        self.assertEqual(
            "research",
            RuntimeContentProvider._infer_content_type(
                source="arXiv",
                title="Regret minimization with adaptive opponents",
                url="https://arxiv.org/abs/1234.5678",
            ),
        )


if __name__ == "__main__":
    unittest.main()
