#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime multi-source fallback for content panels when DB content tables are unavailable."""

from __future__ import annotations

import threading
import time
import xml.etree.ElementTree as ET
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

import requests

from data_sources import CONTENT_TOPIC_REGISTRY, GEO_SHOCK_GDELT_DOC_API_URL, RSS_FEEDS, non_empty_feeds

DEFAULT_CACHE_TTL_SECONDS = 900
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_FEED_WORKERS = 10
MAX_ITEMS_PER_FEED = 25
MAX_DYNAMIC_ITEMS_PER_FEED = 20
ATOM_NS = "{http://www.w3.org/2005/Atom}"
GOOGLE_NEWS_RSS_TEMPLATE = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
SOURCE_TIER_BONUS = {
    "Reuters": 20,
    "AP News": 18,
    "BBC World": 16,
    "BBC Politics": 16,
    "Financial Times": 16,
    "Wall Street Journal": 16,
    "CNBC": 14,
    "Yahoo Finance": 10,
    "GDELT DOC": 12,
    "Tavily": 12,
    "Brave Search": 12,
    "SerpAPI": 12,
}


@dataclass
class RuntimeContentItem:
    id: str
    content_type: str
    source: str
    category: str
    title: str
    url: str
    published_at: Optional[str]
    summary: str
    provider: str = "rss"
    source_count: int = 1
    topic_id: str = ""


class RuntimeContentProvider:
    def __init__(
        self,
        *,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        feeds: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        self.cache_ttl_seconds = max(60, int(cache_ttl_seconds))
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.feeds = non_empty_feeds(feeds if feeds is not None else RSS_FEEDS)
        self._lock = threading.Lock()
        self._cache: Dict[str, Any] = {"fetched_at": 0.0, "items": []}
        self._related_cache: Dict[str, Any] = {}
        self._topic_cache: Dict[str, Any] = {}
        self.topics = list(CONTENT_TOPIC_REGISTRY)
        self._session = requests.Session()
        self._session.trust_env = str(os.environ.get("POLYDATA_RUNTIME_TRUST_ENV", "1")).strip().lower() not in {"0", "false", "no", "off"}
        self._session.headers.update(
            {
                "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
                "User-Agent": "polyData-runtime-content/1.0",
            }
        )

    def get_related_news(
        self,
        *,
        market_title: str,
        category: str,
        tags: List[str],
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        topic_ids = self.infer_market_topics(market_title=market_title, category=category, tags=tags)
        topic_items: List[RuntimeContentItem] = []
        for topic_id in topic_ids[:3]:
            topic_items.extend(self._payloads_to_items(self.get_topic_items(topic_id=topic_id, limit=32)))
        items = self._dedupe_items(topic_items)
        market_dynamic_enabled = str(os.environ.get("POLYDATA_CONTENT_MARKET_DYNAMIC", "0")).strip().lower() in {"1", "true", "yes", "on"}
        if market_dynamic_enabled and len(items) < limit:
            items = self._dedupe_items(
                [
                    *items,
                    *self._get_related_dynamic_items(market_title=market_title, category=category, tags=tags),
                ]
            )
        if not items:
            items = self._dedupe_items(self._get_cached_items())
        if not items:
            return []
        keywords = self._build_keywords(market_title=market_title, category=category, tags=tags)
        scored: List[tuple[int, RuntimeContentItem]] = []
        for item in items:
            score = self._score_item(item=item, keywords=keywords)
            if score <= 0 and self._is_broadly_relevant(item=item, category=category):
                score = 8
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda entry: (entry[0], entry[1].published_at or "", entry[1].title), reverse=True)
        return [
            self._to_payload(item, relevance_score=score)
            for _, item in scored[:limit]
        ]

    def get_topic_items(self, *, topic_id: str, limit: int = 24) -> List[Dict[str, Any]]:
        topic = self._topic_by_id(topic_id)
        if not topic:
            return []
        now = time.time()
        cache_key = str(topic.get("id") or topic_id)
        with self._lock:
            cached = self._topic_cache.get(cache_key)
            if cached and now - float(cached.get("fetched_at") or 0) < self.cache_ttl_seconds:
                items = list(cached.get("items") or [])
                return [self._to_payload(item) for item in items[:limit]]
        items = self._dedupe_items(
            [
                *self._topic_items_from_cached_feeds(topic),
                *self._fetch_topic_dynamic_items(topic),
                *self._fetch_topic_gdelt_items(topic),
            ]
        )
        keywords = self._topic_keywords(topic)
        scored = [(self._score_item(item=item, keywords=keywords), item) for item in items]
        scored.sort(key=lambda entry: (entry[0], entry[1].published_at or "", entry[1].title), reverse=True)
        ranked = self._rank_topic_items_with_type_mix(scored=scored, topic=topic, limit=limit)
        with self._lock:
            self._topic_cache[cache_key] = {"fetched_at": now, "items": ranked}
            if len(self._topic_cache) > 32:
                oldest_key = min(self._topic_cache, key=lambda key: float(self._topic_cache[key].get("fetched_at") or 0))
                self._topic_cache.pop(oldest_key, None)
        return [self._to_payload(item) for item in ranked[:limit]]

    def refresh_topics(self, *, topic_ids: Optional[List[str]] = None, limit_per_topic: int = 24) -> Dict[str, List[Dict[str, Any]]]:
        selected = topic_ids or [str(topic.get("id") or "") for topic in self.topics]
        payload: Dict[str, List[Dict[str, Any]]] = {}
        for topic_id in selected:
            topic_id = str(topic_id or "").strip()
            if not topic_id:
                continue
            payload[topic_id] = self.get_topic_items(topic_id=topic_id, limit=limit_per_topic)
        return payload

    def get_latest_items(self, *, limit: int = 8) -> List[Dict[str, Any]]:
        items = self._get_cached_items()
        if not items:
            return []
        ranked = sorted(
            items,
            key=lambda item: (
                item.published_at or "",
                item.title,
            ),
            reverse=True,
        )
        return [
            self._to_payload(item)
            for item in ranked[:limit]
        ]

    def infer_market_topics(self, *, market_title: str, category: str, tags: List[str]) -> List[str]:
        keywords = set(self._build_keywords(market_title=market_title, category=category, tags=tags))
        haystack = f"{market_title} {category} {' '.join(str(tag) for tag in tags)}".lower()
        scored: List[tuple[int, str]] = []
        for topic in self.topics:
            topic_id = str(topic.get("id") or "").strip()
            if not topic_id:
                continue
            score = 0
            for topic_category in topic.get("categories") or []:
                category_text = str(topic_category or "").lower()
                if category_text and category_text in haystack:
                    score += 18
            for keyword in topic.get("keywords") or []:
                text = str(keyword or "").lower()
                if not text:
                    continue
                if " " in text and text in haystack:
                    score += 20
                elif text in keywords or re.search(rf"\b{re.escape(text)}\b", haystack):
                    score += 12
            if score > 0:
                scored.append((score, topic_id))
        if not scored:
            scored.append((1, "prediction-markets"))
        scored.sort(reverse=True)
        topic_ids: List[str] = []
        for _, topic_id in scored:
            if topic_id not in topic_ids:
                topic_ids.append(topic_id)
        if "prediction-markets" not in topic_ids:
            topic_ids.append("prediction-markets")
        return topic_ids[:4]

    def _get_cached_items(self) -> List[RuntimeContentItem]:
        now = time.time()
        with self._lock:
            if now - self._cache["fetched_at"] < self.cache_ttl_seconds:
                return list(self._cache["items"])
        items = self._fetch_all_feeds()
        with self._lock:
            self._cache = {"fetched_at": now, "items": items}
        return items

    def _get_related_dynamic_items(self, *, market_title: str, category: str, tags: List[str]) -> List[RuntimeContentItem]:
        cache_key = "|".join([str(market_title or "").strip().lower(), str(category or "").strip().lower(), ",".join(sorted(str(tag).lower() for tag in tags if tag))])
        now = time.time()
        with self._lock:
            cached = self._related_cache.get(cache_key)
            if cached and now - float(cached.get("fetched_at") or 0) < self.cache_ttl_seconds:
                return list(cached.get("items") or [])

        items: List[RuntimeContentItem] = []
        queries = self._build_market_queries(market_title=market_title, category=category, tags=tags)
        provider_query = self._build_provider_query(market_title=market_title, category=category, tags=tags)
        if provider_query:
            items.extend(self._fetch_search_provider_items(query=provider_query, category=str(category or "Market")))

        def fetch_query(query: str) -> List[RuntimeContentItem]:
            url = GOOGLE_NEWS_RSS_TEMPLATE.format(query=quote_plus(query))
            try:
                response = self._session.get(url, timeout=self.timeout_seconds)
                response.raise_for_status()
                return self._parse_feed(
                    response.text,
                    source="Market Search",
                    category=str(category or "Market"),
                    max_items=MAX_DYNAMIC_ITEMS_PER_FEED,
                    provider="google-news",
                )
            except Exception:
                return []

        if queries:
            with ThreadPoolExecutor(max_workers=min(4, len(queries))) as executor:
                futures = [executor.submit(fetch_query, query) for query in queries]
                for future in as_completed(futures):
                    items.extend(future.result())
        items.extend(self._fetch_gdelt_items(market_title=market_title, category=category, tags=tags))
        deduped = self._dedupe_items(items)
        with self._lock:
            self._related_cache[cache_key] = {"fetched_at": now, "items": deduped}
            if len(self._related_cache) > 64:
                oldest_key = min(self._related_cache, key=lambda key: float(self._related_cache[key].get("fetched_at") or 0))
                self._related_cache.pop(oldest_key, None)
        return deduped

    def _topic_by_id(self, topic_id: str) -> Optional[Dict[str, Any]]:
        for topic in self.topics:
            if str(topic.get("id") or "") == str(topic_id or ""):
                return dict(topic)
        return None

    @staticmethod
    def _topic_keywords(topic: Dict[str, Any]) -> List[str]:
        values: List[str] = [str(topic.get("label") or ""), *(str(value or "") for value in (topic.get("keywords") or []))]
        keywords: List[str] = []
        for value in values:
            for piece in str(value or "").lower().replace("/", " ").replace("&", " ").replace(",", " ").split():
                cleaned = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", piece.strip())
                if len(cleaned) >= 2 and cleaned not in keywords:
                    keywords.append(cleaned)
        return keywords[:18]

    def _topic_items_from_cached_feeds(self, topic: Dict[str, Any]) -> List[RuntimeContentItem]:
        topic_id = str(topic.get("id") or "")
        matched: List[RuntimeContentItem] = []
        for item in self._get_cached_items():
            if self._item_matches_topic(item, topic):
                matched.append(replace(item, topic_id=topic_id, category=str(topic.get("label") or item.category)))
        return matched

    def _fetch_topic_dynamic_items(self, topic: Dict[str, Any]) -> List[RuntimeContentItem]:
        topic_id = str(topic.get("id") or "")
        category = str(topic.get("label") or topic_id or "Topic")
        items: List[RuntimeContentItem] = []
        queries = [str(query or "").strip() for query in (topic.get("queries") or []) if str(query or "").strip()]
        topic_search_enabled = str(os.environ.get("POLYDATA_CONTENT_TOPIC_SEARCH_PROVIDER", "1")).strip().lower() in {"1", "true", "yes", "on"}
        topic_media_search_enabled = str(os.environ.get("POLYDATA_CONTENT_TOPIC_MEDIA_SEARCH_PROVIDER", "1")).strip().lower() in {"1", "true", "yes", "on"}
        topic_research_enabled = str(os.environ.get("POLYDATA_CONTENT_TOPIC_RESEARCH_PROVIDER", "1")).strip().lower() in {"1", "true", "yes", "on"}
        supplemental_queries = self._topic_supplemental_queries(topic, queries)
        if topic_search_enabled and queries:
            items.extend(replace(item, topic_id=topic_id) for item in self._fetch_search_provider_items(query=queries[0], category=category))
        if topic_search_enabled and topic_media_search_enabled:
            for supplemental_query in supplemental_queries:
                items.extend(replace(item, topic_id=topic_id) for item in self._fetch_search_provider_items(query=supplemental_query, category=category))
        if topic_research_enabled:
            items.extend(replace(item, topic_id=topic_id) for item in self._fetch_topic_research_items(topic))

        def fetch_query(query: str) -> List[RuntimeContentItem]:
            url = GOOGLE_NEWS_RSS_TEMPLATE.format(query=quote_plus(query))
            try:
                response = self._session.get(url, timeout=self.timeout_seconds)
                response.raise_for_status()
                return self._parse_feed(
                    response.text,
                    source=f"Topic Search: {category}",
                    category=category,
                    max_items=MAX_DYNAMIC_ITEMS_PER_FEED,
                    provider="google-news-topic",
                    topic_id=topic_id,
                )
            except Exception:
                return []

        google_queries = [*queries, *supplemental_queries]
        if google_queries:
            with ThreadPoolExecutor(max_workers=min(6, len(google_queries))) as executor:
                futures = [executor.submit(fetch_query, query) for query in google_queries]
                for future in as_completed(futures):
                    items.extend(future.result())
        return items

    @staticmethod
    def _topic_supplemental_queries(topic: Dict[str, Any], base_queries: List[str]) -> List[str]:
        bases = [str(query or "").strip() for query in base_queries[:3] if str(query or "").strip()]
        if not bases:
            keywords = [str(value or "").strip() for value in (topic.get("keywords") or [])[:6] if str(value or "").strip()]
            bases = [" ".join(keywords).strip()]
        bases = [re.sub(r"\bwhen:\d+[dhmw]\b", "", base, flags=re.IGNORECASE).strip() for base in bases if base]
        if not bases:
            return []
        queries: List[str] = []
        for base in bases:
            queries.extend(
                [
                    f"site:youtube.com ({base}) analysis preview when:14d",
                    f"site:youtube.com ({base}) interview highlights when:14d",
                    f"site:youtu.be ({base}) analysis preview when:14d",
                    f"filetype:pdf ({base}) report outlook analysis when:60d",
                    f"filetype:pdf ({base}) injury report odds forecast when:60d",
                    f"({base}) research paper working paper study forecast when:60d",
                    f"site:arxiv.org ({base}) research paper when:365d",
                    f"site:ssrn.com ({base}) research paper when:365d",
                ]
            )
        deduped: List[str] = []
        for query in queries:
            if query not in deduped:
                deduped.append(query)
        return deduped[:18]

    def _fetch_topic_research_items(self, topic: Dict[str, Any]) -> List[RuntimeContentItem]:
        topic_id = str(topic.get("id") or "")
        category = str(topic.get("label") or topic_id or "Research")
        queries = self._topic_research_queries(topic)
        if not queries:
            return []
        items: List[RuntimeContentItem] = []
        with ThreadPoolExecutor(max_workers=min(4, len(queries) * 2)) as executor:
            futures = []
            for query in queries:
                futures.append(executor.submit(self._fetch_arxiv_research_items, query=query, category=category))
                futures.append(executor.submit(self._fetch_semantic_scholar_items, query=query, category=category))
            for future in as_completed(futures):
                items.extend(future.result())
        return items

    @staticmethod
    def _topic_research_queries(topic: Dict[str, Any]) -> List[str]:
        topic_id = str(topic.get("id") or "").strip().lower()
        presets = {
            "sports": ["sports analytics", "sports betting markets", "sports forecasting"],
            "esports": ["esports analytics", "esports performance prediction", "competitive gaming analytics"],
            "nba": ["basketball analytics", "NBA player performance prediction", "basketball forecasting"],
            "prediction-markets": ["prediction markets", "forecasting markets", "information aggregation markets"],
            "crypto": ["cryptocurrency market prediction", "crypto asset pricing", "blockchain market microstructure"],
            "finance": ["financial market forecasting", "asset pricing", "market microstructure"],
            "macro": ["macroeconomic forecasting", "inflation forecasting", "economic nowcasting"],
            "cpi": ["inflation forecasting", "consumer price index forecasting", "CPI nowcasting"],
            "oil-energy": ["oil price forecasting", "energy markets", "crude oil volatility"],
            "politics": ["political forecasting", "election forecasting", "public opinion forecasting"],
            "elections": ["election forecasting", "polling errors", "political prediction markets"],
            "geopolitics": ["geopolitical risk", "conflict forecasting", "war risk forecasting"],
            "tech": ["technology forecasting", "innovation diffusion", "technology adoption"],
            "ai": ["artificial intelligence forecasting", "large language models", "AI adoption"],
            "weather": ["weather forecasting", "extreme weather prediction", "climate risk"],
        }
        if topic_id in presets:
            return presets[topic_id]
        keywords = [str(value or "").strip() for value in (topic.get("keywords") or []) if str(value or "").strip()]
        cleaned = [re.sub(r"[^A-Za-z0-9 ]+", " ", keyword).strip() for keyword in keywords[:6]]
        cleaned = [keyword for keyword in cleaned if len(keyword) >= 3]
        return [" ".join(cleaned[:3])] if cleaned else []

    def _fetch_arxiv_research_items(self, *, query: str, category: str) -> List[RuntimeContentItem]:
        try:
            response = self._session.get(
                "https://export.arxiv.org/api/query",
                params={
                    "search_query": f"all:{query}",
                    "start": 0,
                    "max_results": 8,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            items = self._parse_feed(
                response.text,
                source="arXiv",
                category=category,
                max_items=8,
                provider="arxiv",
                max_age_days=3650,
            )
        except Exception:
            return []
        return [
            replace(item, content_type="research", source=f"arXiv: {query}")
            for item in items
            if self._research_item_matches_query(item=item, query=query)
        ]

    def _fetch_semantic_scholar_items(self, *, query: str, category: str) -> List[RuntimeContentItem]:
        try:
            response = self._session.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": query,
                    "limit": 8,
                    "fields": "title,url,abstract,year,publicationDate,venue",
                },
                timeout=self.timeout_seconds,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []
        items: List[RuntimeContentItem] = []
        for index, row in enumerate((payload or {}).get("data") or []):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            paper_id = str(row.get("paperId") or "").strip()
            url = str(row.get("url") or "").strip() or (f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else "")
            if not title or not url:
                continue
            summary = self._clean_summary(str(row.get("abstract") or ""))
            if self._looks_low_quality_item(title=title, summary=summary, url=url):
                continue
            published_at = self._parse_date(str(row.get("publicationDate") or ""))
            if not published_at and row.get("year"):
                published_at = f"{int(row['year']):04d}-01-01T00:00:00Z"
            venue = str(row.get("venue") or "").strip()
            source_label = f"Semantic Scholar: {venue}" if venue else "Semantic Scholar"
            item = RuntimeContentItem(
                id=f"semantic-scholar:{paper_id or url or index}",
                content_type="research",
                source=source_label,
                category=category,
                title=unescape(title),
                url=url,
                published_at=published_at,
                summary=summary,
                provider="semantic-scholar",
            )
            if self._research_item_matches_query(item=item, query=query):
                items.append(item)
        return items

    @staticmethod
    def _research_item_matches_query(*, item: RuntimeContentItem, query: str) -> bool:
        haystack = f"{item.title} {item.summary} {item.url}".lower()
        generic = {
            "analytics",
            "analysis",
            "forecasting",
            "prediction",
            "markets",
            "market",
            "performance",
            "competitive",
            "information",
            "aggregation",
            "adoption",
            "models",
            "model",
        }
        terms = [
            token
            for token in re.findall(r"[a-z0-9]+", str(query or "").lower())
            if len(token) >= 3 and token not in generic
        ]
        if not terms:
            terms = [token for token in re.findall(r"[a-z0-9]+", str(query or "").lower()) if len(token) >= 4]
        return any(term in haystack for term in terms)

    def _rank_topic_items_with_type_mix(
        self,
        *,
        scored: List[tuple[int, RuntimeContentItem]],
        topic: Dict[str, Any],
        limit: int,
    ) -> List[RuntimeContentItem]:
        eligible = [(score, item) for score, item in scored if score >= 20 or self._item_matches_topic(item, topic)]
        if not eligible:
            return []
        news_quota = min(max(4, int(limit) // 3), max(1, int(limit)))
        type_quota = min(max(3, int(limit) // 8), 10)
        selected: List[RuntimeContentItem] = []
        seen: set[str] = set()

        def add_item(item: RuntimeContentItem) -> None:
            key = self._dedupe_key(item)
            if key in seen:
                return
            selected.append(item)
            seen.add(key)

        for _, item in eligible:
            if item.content_type != "news":
                continue
            add_item(item)
            if sum(1 for selected_item in selected if selected_item.content_type == "news") >= news_quota:
                break
        for content_type in ("video", "report", "research"):
            count = 0
            for _, item in eligible:
                if item.content_type != content_type:
                    continue
                add_item(item)
                count += 1
                if count >= type_quota:
                    break
        for _, item in eligible:
            add_item(item)
            if len(selected) >= limit:
                break
        return selected[:limit]

    def _fetch_topic_gdelt_items(self, topic: Dict[str, Any]) -> List[RuntimeContentItem]:
        topic_id = str(topic.get("id") or "")
        if topic_id not in {"politics", "elections", "geopolitics", "macro", "cpi", "oil-energy"}:
            return []
        query = " ".join(str(keyword or "") for keyword in (topic.get("keywords") or [])[:8]).strip()
        if not query:
            return []
        return [
            replace(item, topic_id=topic_id, category=str(topic.get("label") or item.category))
            for item in self._fetch_gdelt_query_items(query=query, category=str(topic.get("label") or topic_id), maxrecords=10)
        ]

    def _item_matches_topic(self, item: RuntimeContentItem, topic: Dict[str, Any]) -> bool:
        topic_categories = {str(category or "").lower() for category in (topic.get("categories") or [])}
        item_category = str(item.category or "").lower()
        broad_categories = {"world", "us", "government"}
        if item_category in topic_categories and item_category not in broad_categories:
            return True
        haystack = f"{item.source} {item.category} {item.title} {item.summary}".lower()
        keyword_hits = 0
        for keyword in topic.get("keywords") or []:
            text = str(keyword or "").lower()
            if not text:
                continue
            if " " in text and text in haystack:
                return True
            if re.search(rf"\b{re.escape(text)}\b", haystack):
                keyword_hits += 1
        if item_category in topic_categories and keyword_hits >= 1:
            return True
        if keyword_hits >= 2:
            return True
        return False

    @staticmethod
    def _payloads_to_items(payloads: List[Dict[str, Any]]) -> List[RuntimeContentItem]:
        items: List[RuntimeContentItem] = []
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            title = str(payload.get("title") or "").strip()
            url = str(payload.get("url") or "").strip()
            if not title or not url:
                continue
            items.append(
                RuntimeContentItem(
                    id=str(payload.get("id") or url),
                    content_type=str(payload.get("contentType") or "news"),
                    source=str(payload.get("source") or "intel"),
                    category=str(payload.get("category") or "Topic"),
                    title=title,
                    url=url,
                    published_at=payload.get("publishedAt"),
                    summary=str(payload.get("summary") or ""),
                    provider=str(payload.get("provider") or "rss"),
                    source_count=int(payload.get("sourceCount") or 1),
                    topic_id=str(payload.get("topicId") or ""),
                )
            )
        return items

    def _fetch_search_provider_items(self, *, query: str, category: str) -> List[RuntimeContentItem]:
        providers = (
            ("tavily", self._fetch_tavily_items),
            ("brave", self._fetch_brave_items),
            ("serpapi", self._fetch_serpapi_items),
        )
        for _, fetcher in providers:
            items = fetcher(query=query, category=category)
            if items:
                return items
        return []

    def _fetch_tavily_items(self, *, query: str, category: str) -> List[RuntimeContentItem]:
        key = self._first_env_key("TAVILY_API_KEYS", "TAVILY_API_KEY", "POLYDATA_TAVILY_API_KEYS", "POLYDATA_TAVILY_API_KEY")
        if not key:
            return []
        try:
            response = self._session.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 8,
                    "include_answer": False,
                    "include_images": False,
                },
                timeout=self.timeout_seconds,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []
        items: List[RuntimeContentItem] = []
        for index, row in enumerate((payload or {}).get("results") or []):
            if not isinstance(row, dict):
                continue
            item = self._search_result_item(
                row=row,
                index=index,
                source="Tavily",
                provider="tavily",
                category=category,
                title_key="title",
                url_key="url",
                summary_key="content",
                published_key="published_date",
            )
            if item:
                items.append(item)
        return items

    def _fetch_brave_items(self, *, query: str, category: str) -> List[RuntimeContentItem]:
        key = self._first_env_key("BRAVE_API_KEYS", "BRAVE_API_KEY", "POLYDATA_BRAVE_API_KEYS", "POLYDATA_BRAVE_API_KEY")
        if not key:
            return []
        try:
            response = self._session.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": 8, "freshness": "pd"},
                timeout=self.timeout_seconds,
                headers={"Accept": "application/json", "X-Subscription-Token": key},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []
        items: List[RuntimeContentItem] = []
        for index, row in enumerate(((payload or {}).get("web") or {}).get("results") or []):
            if not isinstance(row, dict):
                continue
            item = self._search_result_item(
                row=row,
                index=index,
                source="Brave Search",
                provider="brave",
                category=category,
                title_key="title",
                url_key="url",
                summary_key="description",
                published_key="age",
            )
            if item:
                items.append(item)
        return items

    def _fetch_serpapi_items(self, *, query: str, category: str) -> List[RuntimeContentItem]:
        key = self._first_env_key("SERPAPI_API_KEYS", "SERPAPI_API_KEY", "POLYDATA_SERPAPI_API_KEYS", "POLYDATA_SERPAPI_API_KEY")
        if not key:
            return []
        try:
            response = self._session.get(
                "https://serpapi.com/search.json",
                params={"engine": "google_news", "q": query, "api_key": key, "num": 8},
                timeout=self.timeout_seconds,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []
        rows = (payload or {}).get("news_results") or (payload or {}).get("organic_results") or []
        items: List[RuntimeContentItem] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            item = self._search_result_item(
                row=row,
                index=index,
                source="SerpAPI",
                provider="serpapi",
                category=category,
                title_key="title",
                url_key="link",
                summary_key="snippet",
                published_key="date",
            )
            if item:
                items.append(item)
        return items

    def _search_result_item(
        self,
        *,
        row: Dict[str, Any],
        index: int,
        source: str,
        provider: str,
        category: str,
        title_key: str,
        url_key: str,
        summary_key: str,
        published_key: str,
    ) -> Optional[RuntimeContentItem]:
        title = str(row.get(title_key) or "").strip()
        url = str(row.get(url_key) or row.get("url") or "").strip()
        if not title or not url:
            return None
        summary = self._clean_summary(str(row.get(summary_key) or row.get("summary") or ""))
        if self._looks_low_quality_item(title=title, summary=summary, url=url):
            return None
        published_at = self._parse_date(str(row.get(published_key) or row.get("published_at") or ""))
        domain = urlparse(url).netloc
        source_label = f"{source}: {domain}" if domain else source
        return RuntimeContentItem(
            id=f"{provider}:{url or index}",
            content_type=self._infer_content_type(source=source_label, title=title, url=url),
            source=source_label,
            category=category,
            title=unescape(title),
            url=url,
            published_at=published_at,
            summary=summary,
            provider=provider,
        )

    @staticmethod
    def _first_env_key(*names: str) -> str:
        for name in names:
            raw = os.environ.get(name)
            if not raw:
                continue
            for part in str(raw).split(","):
                key = part.strip()
                if key:
                    return key
        return ""

    def _fetch_all_feeds(self) -> List[RuntimeContentItem]:
        items: List[RuntimeContentItem] = []
        if not self.feeds:
            return items

        def fetch_feed(feed: Dict[str, str]) -> List[RuntimeContentItem]:
            try:
                response = self._session.get(feed["url"], timeout=self.timeout_seconds)
                response.raise_for_status()
                return self._parse_feed(response.text, source=feed["source"], category=str(feed.get("category") or "News"))
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=min(MAX_FEED_WORKERS, len(self.feeds))) as executor:
            futures = [executor.submit(fetch_feed, feed) for feed in self.feeds]
            for future in as_completed(futures):
                items.extend(future.result())
        return self._dedupe_items(items)

    def _parse_feed(
        self,
        payload: str,
        *,
        source: str,
        category: str,
        max_items: int = MAX_ITEMS_PER_FEED,
        provider: str = "rss",
        topic_id: str = "",
        max_age_days: int = 14,
    ) -> List[RuntimeContentItem]:
        root = ET.fromstring(payload)
        parsed: List[RuntimeContentItem] = []
        rss_items = root.findall(".//item")
        atom_items = root.findall(f".//{ATOM_NS}entry") if not rss_items else []
        for item in (rss_items or atom_items)[:max_items]:
            title = self._find_text(item, "title")
            url = self._find_text(item, "link") or self._find_atom_link(item)
            summary = self._clean_summary(
                self._find_text(item, "description")
                or self._find_text(item, "summary")
                or self._find_text(item, "content")
                or self._find_text(item, f"{ATOM_NS}summary")
                or self._find_text(item, f"{ATOM_NS}content")
            )
            published_raw = (
                self._find_text(item, "pubDate")
                or self._find_text(item, "published")
                or self._find_text(item, "updated")
                or self._find_text(item, f"{ATOM_NS}published")
                or self._find_text(item, f"{ATOM_NS}updated")
            )
            published_at = None
            if published_raw:
                try:
                    published_at = parsedate_to_datetime(published_raw).astimezone().isoformat()
                except Exception:
                    published_at = None
            if not title or not url:
                continue
            title = unescape(title.strip())
            if published_at and not self._is_recent_published_at(published_at, max_age_days=max_age_days):
                continue
            if self._looks_low_quality_item(title=title, summary=summary, url=url):
                continue
            source_label = source
            feed_source = self._find_text(item, "source")
            if feed_source and provider.startswith("google-news"):
                source_label = f"{source}: {feed_source.strip()}"
            parsed.append(
                RuntimeContentItem(
                    id=f"{source_label}:{url}",
                    content_type=self._infer_content_type(source=source_label, title=title, url=url),
                    source=source_label,
                    category=category,
                    title=title,
                    url=url.strip(),
                    published_at=published_at,
                    summary=summary,
                    provider=provider,
                    topic_id=topic_id,
                )
            )
        return parsed

    def _fetch_gdelt_items(self, *, market_title: str, category: str, tags: List[str]) -> List[RuntimeContentItem]:
        query = self._build_gdelt_query(market_title=market_title, category=category, tags=tags)
        if not query:
            return []
        return self._fetch_gdelt_query_items(query=query, category=str(category or "GDELT"), maxrecords=10)

    def _fetch_gdelt_query_items(self, *, query: str, category: str, maxrecords: int = 10) -> List[RuntimeContentItem]:
        base_url = GEO_SHOCK_GDELT_DOC_API_URL or DEFAULT_GDELT_DOC_API_URL
        try:
            response = self._session.get(
                base_url,
                params={
                    "query": query,
                    "mode": "ArtList",
                    "format": "json",
                    "maxrecords": maxrecords,
                    "timespan": "3days",
                    "sort": "HybridRel",
                },
                timeout=max(self.timeout_seconds, 12),
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []
        items: List[RuntimeContentItem] = []
        for index, row in enumerate((payload or {}).get("articles") or []):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            url = str(row.get("url") or "").strip()
            if not title or not url:
                continue
            domain = str(row.get("domain") or urlparse(url).netloc or "GDELT").strip()
            published_at = None
            raw_date = str(row.get("seendate") or "").strip()
            if raw_date:
                published_at = self._parse_date(raw_date)
            items.append(
                RuntimeContentItem(
                    id=f"gdelt:{url or index}",
                    content_type=self._infer_content_type(source=domain, title=title, url=url),
                    source=f"GDELT DOC: {domain}",
                    category=category,
                    title=unescape(title),
                    url=url,
                    published_at=published_at,
                    summary=str(row.get("snippet") or domain or "").strip()[:280],
                    provider="gdelt",
                )
            )
        return items

    @staticmethod
    def _parse_date(value: str) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return parsedate_to_datetime(text).astimezone().isoformat()
        except Exception:
            pass
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed.replace(tzinfo=timezone.utc).astimezone().isoformat()
            except Exception:
                continue
        return None

    @staticmethod
    def _find_text(item: ET.Element, tag: str) -> str:
        node = item.find(tag)
        if node is None and not tag.startswith("{"):
            node = item.find(f"{ATOM_NS}{tag}")
        if node is None or node.text is None:
            return ""
        return node.text

    @staticmethod
    def _find_atom_link(item: ET.Element) -> str:
        for node in item.findall(f"{ATOM_NS}link"):
            href = str(node.attrib.get("href") or "").strip()
            if href:
                return href
        return ""

    @staticmethod
    def _clean_summary(value: str) -> str:
        text = unescape(str(value or "")).strip()
        if not text or text.lower() in {"null", "undefined", "none"}:
            return ""
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text.lower() in {"null", "undefined", "none"}:
            return ""
        return text[:280]

    @staticmethod
    def _looks_low_quality_item(*, title: str, summary: str, url: str) -> bool:
        text = f"{title} {summary} {url}".lower()
        if re.search(r"\([a-z0-9]{8,}\)", title, flags=re.IGNORECASE):
            return True
        if any(
            token in text
            for token in (
                "coupon",
                "promo code",
                "sponsored post",
                "press release distribution",
                "mshale",
                "instagram.com",
                "facebook.com",
                "tiktok.com",
                "reddit.com",
                "pandascore.co",
                "data & odds api",
                "sponsor a big esports team",
            )
        ):
            return True
        if "price prediction" in text and re.search(r"#(?:btc|eth|crypto)|crash news|important analysis", text):
            return True
        if len(str(title or "").split()) < 4:
            return True
        return False

    @staticmethod
    def _is_recent_published_at(value: str, *, max_age_days: int) -> bool:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except Exception:
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if parsed > now + timedelta(hours=6):
            return False
        return parsed >= now - timedelta(days=max_age_days)

    @staticmethod
    def _infer_content_type(*, source: str, title: str, url: str) -> str:
        text = f"{source} {title} {url}".lower()
        if re.search(r"youtube|youtu\.be|vimeo|twitch\.tv|video|watch\?|highlights?|interview|podcast", text):
            return "video"
        if re.search(r"\.pdf($|[?#])|annual-report|whitepaper|policy-paper|research-report|special-report|outlook|briefing|forecast|preview|injury report", text):
            return "report"
        if re.search(r"arxiv\.org|ssrn\.com|nber\.org|working paper|journal|research paper", text):
            return "research"
        return "news"

    @staticmethod
    def _source_tier_bonus(source: str) -> int:
        for label, bonus in SOURCE_TIER_BONUS.items():
            if label.lower() in str(source or "").lower():
                return bonus
        return 4

    def _score_item(self, *, item: RuntimeContentItem, keywords: List[str]) -> int:
        haystack = f"{item.source} {item.category} {item.title} {item.summary}".lower()
        hits = sum(1 for keyword in keywords if keyword and keyword in haystack)
        phrase_hits = sum(1 for keyword in keywords[:4] if keyword and re.search(rf"\b{re.escape(keyword)}\b", haystack))
        provider_bonus = 16 if item.provider in {"google-news", "gdelt"} else 0
        corroboration_bonus = min(12, max(0, item.source_count - 1) * 4)
        return hits * 12 + phrase_hits * 5 + provider_bonus + corroboration_bonus + self._source_tier_bonus(item.source)

    @staticmethod
    def _dedupe_key(item: RuntimeContentItem) -> str:
        parsed = urlparse(item.url)
        if parsed.netloc and parsed.path:
            return f"url:{parsed.netloc.lower()}{parsed.path.rstrip('/').lower()}"
        title = re.sub(r"\W+", " ", item.title.lower()).strip()
        return f"title:{title[:120]}"

    def _dedupe_items(self, items: List[RuntimeContentItem]) -> List[RuntimeContentItem]:
        by_key: Dict[str, RuntimeContentItem] = {}
        source_sets: Dict[str, set[str]] = {}
        for item in items:
            key = self._dedupe_key(item)
            source_sets.setdefault(key, set()).add(item.source)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = item
                continue
            existing_time = existing.published_at or ""
            item_time = item.published_at or ""
            if item.provider in {"google-news", "gdelt"} and existing.provider == "rss":
                by_key[key] = item
            elif item_time > existing_time:
                by_key[key] = item
        deduped = list(by_key.values())
        for item in deduped:
            item.source_count = max(1, len(source_sets.get(self._dedupe_key(item), set())))
        return deduped

    @staticmethod
    def _to_payload(item: RuntimeContentItem, relevance_score: Optional[int] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": item.id,
            "contentType": item.content_type,
            "source": item.source,
            "category": item.category,
            "title": item.title,
            "url": item.url,
            "publishedAt": item.published_at,
            "summary": item.summary,
            "provider": item.provider,
            "sourceCount": item.source_count,
            "topicId": item.topic_id,
        }
        if relevance_score is not None:
            payload["relevanceScore"] = relevance_score
        return payload

    @staticmethod
    def _is_broadly_relevant(*, item: RuntimeContentItem, category: str) -> bool:
        item_category = item.category.lower()
        market_category = str(category or "").lower()
        if item.source in {"Polymarket News", "Market Search"} or item.source.startswith("GDELT DOC"):
            return True
        if market_category and (market_category in item_category or item_category in market_category):
            return True
        if any(token in market_category for token in ("politic", "election")) and item_category in {"politics", "elections"}:
            return True
        if any(token in market_category for token in ("crypto", "bitcoin", "ethereum")) and item_category == "crypto":
            return True
        if any(token in market_category for token in ("sport", "nba", "nfl", "mlb", "nhl", "ufc")) and item_category == "sports":
            return True
        return False

    @staticmethod
    def _build_keywords(*, market_title: str, category: str, tags: List[str]) -> List[str]:
        base = [market_title, category, *tags]
        stopwords = {
            "about",
            "active",
            "after",
            "against",
            "before",
            "down",
            "from",
            "game",
            "games",
            "higher",
            "league",
            "market",
            "markets",
            "match",
            "over",
            "premier",
            "price",
            "above",
            "below",
            "close",
            "closes",
            "reach",
            "resolve",
            "than",
            "that",
            "this",
            "under",
            "versus",
            "will",
            "winner",
            "with",
            "yes",
        }
        keywords: List[str] = []
        for value in base:
            for piece in str(value or "").lower().replace("?", " ").replace(",", " ").split():
                cleaned = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", piece.strip())
                if len(cleaned) >= 3 and cleaned not in stopwords and cleaned not in keywords:
                    keywords.append(cleaned)
        return keywords[:16]

    def _build_market_queries(self, *, market_title: str, category: str, tags: List[str]) -> List[str]:
        title = re.sub(r"\s+", " ", str(market_title or "").replace("?", " ")).strip()
        keywords = self._build_keywords(market_title=market_title, category=category, tags=tags)
        category_text = str(category or "").strip()
        queries: List[str] = []
        keyword_text = " ".join(keywords[:6])
        if keyword_text:
            queries.append(f"{keyword_text} when:3d")
        if any(token in keywords for token in ("oil", "wti", "crude", "brent", "opec", "gas", "lng")):
            queries.append('WTI OR "crude oil" OR Brent OR OPEC when:3d')
        elif any(token in keywords for token in ("bitcoin", "btc", "ethereum", "eth", "crypto", "stablecoin")):
            queries.append("crypto bitcoin ethereum stablecoin market when:3d")
        elif any(token in keywords for token in ("nba", "nfl", "mlb", "nhl", "ufc", "tennis", "cricket")):
            queries.append(f"{keyword_text} injury lineup odds when:2d")
        if title:
            queries.append(f'{title[:140]} latest news when:3d')
        if keywords:
            queries.append(f'{" ".join(keywords[:7])} {category_text} prediction market when:3d'.strip())
        cleaned: List[str] = []
        for query in queries:
            query = re.sub(r"\s+", " ", query).strip()
            if query and query not in cleaned:
                cleaned.append(query)
        return cleaned[:3]

    def _build_provider_query(self, *, market_title: str, category: str, tags: List[str]) -> str:
        title = re.sub(r"\s+", " ", str(market_title or "").replace("?", " ")).strip()
        keywords = self._build_keywords(market_title=market_title, category=category, tags=tags)
        if title and len(title) <= 120:
            return f"{title} latest news"
        if keywords:
            return f'{" ".join(keywords[:8])} latest news'
        return str(category or "").strip()

    def _build_gdelt_query(self, *, market_title: str, category: str, tags: List[str]) -> str:
        keywords = self._build_keywords(market_title=market_title, category=category, tags=tags)
        query_terms = keywords[:8]
        category_text = str(category or "").strip().lower()
        enabled_category = any(
            token in category_text
            for token in ("politic", "election", "geopolitic", "war", "conflict", "macro", "energy", "oil", "commodity")
        )
        enabled_keyword = any(
            token in keywords
            for token in ("election", "trump", "biden", "war", "conflict", "ukraine", "russia", "iran", "israel", "oil", "wti", "crude", "brent", "opec", "inflation", "fed", "cpi")
        )
        if not (enabled_category or enabled_keyword):
            return ""
        if any(token in category_text for token in ("sport", "nba", "nfl", "mlb", "tennis", "cricket", "crypto", "tech")):
            return ""
        if any(token in keywords for token in ("bitcoin", "crypto", "nba", "nfl", "mlb", "tennis")):
            return ""
        if not query_terms:
            return ""
        return " ".join(query_terms)[:240]
