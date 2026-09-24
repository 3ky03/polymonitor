from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from zoneinfo import ZoneInfo

from data_sources import JIN10_FLASH_API_URL, JIN10_FLASH_DETAIL_BASE_URL, JIN10_LIVE_URL

DEFAULT_FLASH_API_URL = os.environ.get("POLYDATA_JIN10_FLASH_API_URL", JIN10_FLASH_API_URL).strip()
DEFAULT_FLASH_CHANNEL = os.environ.get("POLYDATA_JIN10_FLASH_CHANNEL", "-8200").strip()
DEFAULT_FLASH_APP_ID = os.environ.get("POLYDATA_JIN10_APP_ID", "SO1EJGmNgCtmpcPF").strip()
DEFAULT_FLASH_VERSION = os.environ.get("POLYDATA_JIN10_VERSION", "1.0.0").strip()
DEFAULT_FLASH_DETAIL_BASE_URL = os.environ.get("POLYDATA_JIN10_FLASH_DETAIL_BASE_URL", JIN10_FLASH_DETAIL_BASE_URL).strip()
DEFAULT_LIVE_URL = os.environ.get("POLYDATA_JIN10_LIVE_URL", JIN10_LIVE_URL).strip()
DEFAULT_REQUEST_TIMEOUT_SECONDS = 12
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def iso_now() -> str:
    return datetime.now(tz=SHANGHAI_TZ).astimezone().isoformat()


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = TAG_RE.sub("", text)
    text = SPACE_RE.sub(" ", text).strip()
    return text


def parse_timestamp(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI_TZ)
    except ValueError:
        return None
    return parsed.isoformat()


def normalize_item(
    item: Any,
    *,
    detail_base_url: str = DEFAULT_FLASH_DETAIL_BASE_URL,
    live_url: str = DEFAULT_LIVE_URL,
) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    if bool((item.get("extras") or {}).get("ad")):
        return None

    data = item.get("data") or {}
    remarks = item.get("remark") or []
    title = clean_text((data or {}).get("title"))
    content = clean_text((data or {}).get("content"))
    vip_title = clean_text((data or {}).get("vip_title"))
    headline = title or content or vip_title
    if not headline:
        return None

    summary = content if content and content != headline else ""
    asset_hints = [
        clean_text(remark.get("title"))
        for remark in remarks
        if isinstance(remark, dict) and clean_text(remark.get("title"))
    ]

    return {
        "id": str(item.get("id") or headline),
        "timestamp": parse_timestamp(item.get("time")),
        "headline": headline,
        "summary": summary,
        "source": clean_text((data or {}).get("source")) or "Jin10",
        "url": f"{detail_base_url.rstrip('/')}/{item.get('id')}" if item.get("id") and detail_base_url else live_url,
        "important": bool(item.get("important")),
        "locked": bool((data or {}).get("lock")),
        "vipLevel": (data or {}).get("vip_level"),
        "assetHints": asset_hints[:3],
        "channelIds": [int(channel) for channel in (item.get("channel") or []) if str(channel).isdigit()],
    }


def _item_key(item: Dict[str, Any]) -> str:
    return str(item.get("id") or item.get("headline") or "")


def select_panel_items(candidates: List[Dict[str, Any]], *, limit: int) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []

    selected_keys: List[str] = []
    seen_keys: set[str] = set()

    def _append(item: Dict[str, Any]) -> None:
        key = _item_key(item)
        if not key or key in seen_keys:
            return
        seen_keys.add(key)
        selected_keys.append(key)

    # Prefer retaining important flashes, then fill with the newest remaining rows.
    for item in candidates:
        if item.get("important"):
            _append(item)
    for item in candidates:
        _append(item)
        if len(selected_keys) >= limit:
            break

    selected_key_set = set(selected_keys[:limit])
    selected_items: List[Dict[str, Any]] = []
    appended_keys: set[str] = set()
    for item in candidates:
        key = _item_key(item)
        if key not in selected_key_set or key in appended_keys:
            continue
        appended_keys.add(key)
        selected_items.append(item)
        if len(selected_items) >= limit:
            break
    return selected_items


def fetch_jin10_panel_payload(
    *,
    limit: int = 24,
    api_url: str = DEFAULT_FLASH_API_URL,
    channel: str = DEFAULT_FLASH_CHANNEL,
    app_id: str = DEFAULT_FLASH_APP_ID,
    version: str = DEFAULT_FLASH_VERSION,
    detail_base_url: str = DEFAULT_FLASH_DETAIL_BASE_URL,
    live_url: str = DEFAULT_LIVE_URL,
    timeout: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    requests_lib: Any = None,
) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "polydata-jin10-panel/1.0",
        "x-app-id": app_id,
        "x-version": version,
    }
    candidate_target = max(limit * 2, 24)
    max_pages = max(2, min(6, (candidate_target // 20) + 2))
    cursor = datetime.now(tz=SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    candidates: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    last_cursor = ""
    close_client = False
    client = requests_lib
    if client is None or hasattr(client, "Session"):
        client = requests.Session()
        client.trust_env = False
        close_client = True

    try:
        for _ in range(max_pages):
            response = client.get(
                api_url,
                params={"channel": channel, "max_time": cursor},
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") if isinstance(payload, dict) else []
            if not rows:
                break
            for row in rows or []:
                normalized = normalize_item(row, detail_base_url=detail_base_url, live_url=live_url)
                if normalized is None:
                    continue
                key = _item_key(normalized)
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                candidates.append(normalized)
            if len(candidates) >= candidate_target:
                break
            next_cursor = str((rows[-1] or {}).get("time") or "").strip()
            if not next_cursor or next_cursor == cursor or next_cursor == last_cursor:
                break
            last_cursor = cursor
            cursor = next_cursor
    finally:
        if close_client:
            client.close()

    items = select_panel_items(candidates, limit=limit)
    return {
        "generatedAt": iso_now(),
        "source": "jin10-flash",
        "sourceUrl": live_url,
        "status": "ok",
        "items": items,
    }
