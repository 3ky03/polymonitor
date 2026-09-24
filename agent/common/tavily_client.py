from __future__ import annotations

from typing import Any

import requests

from .env import get_env, get_float_env, get_int_env
from .json_utils import compact_text


class TavilySearchClient:
    def __init__(self) -> None:
        self.api_key = get_env("TAVILY_API_KEY")
        self.search_url = get_env("POLYDATA_TAVILY_SEARCH_URL", "https://api.tavily.com/search")
        self.timeout = get_float_env("POLYDATA_TAVILY_TIMEOUT_SECONDS", 10.0)
        self.max_results = get_int_env("POLYDATA_TAVILY_MAX_RESULTS", 3)

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.search_url)

    def search(self, query: str) -> list[dict[str, str]]:
        if not self.configured or not query.strip():
            return []
        payload = {
            "api_key": self.api_key,
            "query": query.strip(),
            "search_depth": "basic",
            "max_results": self.max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        response = requests.post(self.search_url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        results = data.get("results") or []
        normalized: list[dict[str, str]] = []
        for item in results[: self.max_results]:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "title": compact_text(item.get("title"), 120),
                    "url": compact_text(item.get("url"), 260),
                    "content": compact_text(item.get("content"), 320),
                }
            )
        return normalized

