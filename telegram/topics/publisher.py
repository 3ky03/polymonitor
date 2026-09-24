from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

from .api_client import PolyDataApiClient, resolve_polydata_api_base
from .catalog import CATALOG_VERSION, SERVER_RUNTIME_PANEL_IDS
from .client import TelegramClient
from .config import TelegramSettings, load_settings
from .formatters import format_all_snapshots_with_stats
from .models import MessageCandidate
from .state import PublishState, state_lock


PANEL_ENDPOINTS = {
    "latest-content": ("/content/latest", {"limit": 8}),
    "alpha-signal": ("/runtime/signals/alpha", {"limit": 8}),
    "new-market-signals": ("/runtime/markets/new-signals", {"limit": 12}),
    "polymarket-macro-map": ("/runtime/macro/polymarket-map", {"limit": 12}),
    "cpi-release-command-center": ("/runtime/macro/cpi-release-command-center", {"limit": 12}),
    "nba-scoreboard": ("/runtime/sports/nba", {"limit": 10}),
    "nba-intel": ("/runtime/sports/nba-intel", {"limit": 12}),
    "espn-matchup-predictor": ("/runtime/sports/nba-matchup-predictor", {"limit": 8}),
    "worldcup-intel": ("/runtime/sports/worldcup-intel", {"limit": 24}),
    "global-weather-map": ("/runtime/weather/global-map", {"limit": 34}),
    "weather-news": ("/runtime/weather/news", {"limit": 24}),
}
ALL_PANELS_MAX_WORKERS = 4
ALL_PANELS_REQUEST_TIMEOUT_SECONDS = 10


TARGET_PANELS = {
    "all": tuple(PANEL_ENDPOINTS),
    "news": ("latest-content",),
    "alpha": ("alpha-signal", "new-market-signals"),
    "macro": ("polymarket-macro-map", "cpi-release-command-center"),
    "nba": ("nba-scoreboard", "nba-intel", "espn-matchup-predictor"),
    "worldcup": ("worldcup-intel",),
    "weather": ("global-weather-map", "weather-news"),
    "all-panels": (*SERVER_RUNTIME_PANEL_IDS, "latest-content", "worldcup-intel"),
}


@dataclass
class PublishResult:
    fetched: int = 0
    candidates: int = 0
    sent: int = 0
    previewed: int = 0
    primed: int = 0
    skipped_seen: int = 0
    skipped_unconfigured: int = 0
    failed_sends: int = 0
    fetch_failed: int = 0
    dry_run: bool = False
    api_base: str = ""
    api_healthy: bool = False
    format_specialized: int = 0
    format_generic: int = 0
    format_empty: int = 0
    format_aggregate: int = 0
    format_market_scoped: int = 0
    format_browser_only: int = 0
    format_non_pushable: int = 0
    format_unsupported: int = 0
    format_errors: int = 0
    catalog_primed: bool = False


def _write_heartbeat(*, phase: str, result: PublishResult | None = None, error: str = "") -> None:
    raw_path = str(os.environ.get("POLYDATA_TELEGRAM_HEARTBEAT_PATH") or "").strip()
    if not raw_path:
        return
    path = Path(raw_path).expanduser()
    payload = {
        "timestamp": int(time.time()),
        "phase": phase,
        "error": str(error or "")[:500],
        "result": result.__dict__ if result is not None else None,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        print(f"[telegram-publisher] WARN heartbeat write failed path={path} error={exc}", file=sys.stderr)


def fetch_snapshots(api: PolyDataApiClient, *, target: str) -> Dict[str, Dict]:
    if target == "all-panels":
        runtime_snapshots: Dict[str, Dict] = {}

        def fetch_runtime_panel(panel_id: str) -> tuple[str, Dict | None, int]:
            panel_api = api.isolated(timeout_seconds=ALL_PANELS_REQUEST_TIMEOUT_SECONDS)
            try:
                envelope = panel_api.get_json(
                    "/v1/runtime/panels",
                    params={"ids": panel_id, "limit": 12},
                    timeout_seconds=ALL_PANELS_REQUEST_TIMEOUT_SECONDS,
                )
                data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
                panels = data.get("panels") if isinstance(data.get("panels"), dict) else {}
                errors = envelope.get("errors") if isinstance(envelope.get("errors"), list) else []
                payload = panels.get(panel_id)
                return panel_id, payload if isinstance(payload, dict) else None, len(errors)
            except Exception as exc:
                print(
                    "[telegram-publisher] WARN all-panels panel failed "
                    f"panel={panel_id} error_class={exc.__class__.__name__}",
                    file=sys.stderr,
                )
                return panel_id, None, 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=ALL_PANELS_MAX_WORKERS) as executor:
            for panel_id, payload, error_count in executor.map(fetch_runtime_panel, SERVER_RUNTIME_PANEL_IDS):
                if payload is not None:
                    runtime_snapshots[panel_id] = payload
                if error_count:
                    print(
                        "[telegram-publisher] WARN all-panels panel partial "
                        f"panel={panel_id} returned={payload is not None} errors={error_count}",
                        file=sys.stderr,
                    )

        snapshots: Dict[str, Dict] = {
            panel_id: runtime_snapshots[panel_id]
            for panel_id in SERVER_RUNTIME_PANEL_IDS
            if panel_id in runtime_snapshots
        }
        for panel_id in ("latest-content", "worldcup-intel"):
            path, params = PANEL_ENDPOINTS[panel_id]
            try:
                snapshots[panel_id] = api.get_json(path, params=params)
            except Exception as exc:
                print(
                    f"[telegram-publisher] WARN fetch failed panel={panel_id} error_class={exc.__class__.__name__}",
                    file=sys.stderr,
                )
        return snapshots

    panel_ids = TARGET_PANELS.get(target, TARGET_PANELS["all"])
    snapshots: Dict[str, Dict] = {}
    for panel_id in panel_ids:
        path, params = PANEL_ENDPOINTS[panel_id]
        try:
            snapshots[panel_id] = api.get_json(path, params=params)
        except Exception as exc:
            print(
                f"[telegram-publisher] WARN fetch failed panel={panel_id} error_class={exc.__class__.__name__}",
                file=sys.stderr,
            )
    return snapshots


def _candidate_targets(candidate: MessageCandidate, settings: TelegramSettings) -> Iterable[str]:
    for target in candidate.targets():
        config = settings.topic_config(target)
        if config.enabled:
            yield target


def _all_panels_fetch_complete(snapshots: Dict[str, Dict]) -> bool:
    expected = set(TARGET_PANELS["all-panels"])
    return expected.issubset(snapshots) and len(snapshots) == len(expected)


def publish_candidates(
    candidates: List[MessageCandidate],
    *,
    settings: TelegramSettings,
    state: PublishState,
    telegram: TelegramClient,
    dry_run: bool = False,
    prime: bool = False,
) -> PublishResult:
    result = PublishResult(candidates=len(candidates), dry_run=dry_run)
    for candidate in candidates:
        targets = list(candidate.targets()) if prime or dry_run else list(_candidate_targets(candidate, settings))
        if not targets:
            result.skipped_unconfigured += 1
            continue
        for target in targets:
            if state.seen(target, candidate.dedupe_key):
                result.skipped_seen += 1
                continue
            config = settings.topic_config(target)
            if dry_run:
                print(json.dumps({"target": target, "dedupeKey": candidate.dedupe_key, "text": candidate.text}, ensure_ascii=False))
                result.previewed += 1
                result.sent += 1
                continue
            if prime:
                state.mark(target, candidate.dedupe_key)
                result.primed += 1
                continue
            else:
                try:
                    telegram.send_message(
                        chat_id=config.chat_id,
                        text=candidate.text,
                        message_thread_id=config.message_thread_id,
                        disable_web_page_preview=not candidate.link_preview,
                        disable_notification=settings.disable_notification,
                        reply_markup=candidate.reply_markup,
                    )
                except Exception as exc:
                    result.failed_sends += 1
                    print(
                        f"[telegram-publisher] WARN send failed target={target} dedupe={candidate.dedupe_key} error_class={exc.__class__.__name__}",
                        file=sys.stderr,
                    )
                    continue
                state.mark(target, candidate.dedupe_key)
                result.sent += 1
    if not dry_run:
        state.save()
    return result


def run_once(
    *,
    settings: TelegramSettings,
    target: str,
    dry_run: bool = False,
    prime: bool = False,
    api_base_override: str = "",
) -> PublishResult:
    api_candidates = (api_base_override.rstrip("/"),) if api_base_override else settings.polydata_api_candidates
    resolution = resolve_polydata_api_base(api_candidates, timeout_seconds=min(5, settings.request_timeout_seconds))
    if not resolution.healthy:
        checked = ", ".join(resolution.checked) or "(none)"
        print(f"[telegram-publisher] WARN no healthy polyData API found; using {resolution.base_url or '(empty)'} after checking {checked}", file=sys.stderr)
    api = PolyDataApiClient(base_url=resolution.base_url or settings.polydata_api_base, timeout_seconds=settings.request_timeout_seconds)
    telegram = TelegramClient(
        bot_token=settings.bot_token,
        api_base=settings.telegram_api_base,
        timeout_seconds=settings.request_timeout_seconds,
    )
    snapshots = fetch_snapshots(api, target=target)
    candidates, format_stats = format_all_snapshots_with_stats(snapshots)
    with state_lock(settings.state_path):
        state = PublishState(settings.state_path)
        auto_prime = target == "all-panels" and not dry_run and not state.catalog_primed(CATALOG_VERSION)
        effective_prime = bool(prime or auto_prime)
        result = publish_candidates(
            candidates,
            settings=settings,
            state=state,
            telegram=telegram,
            dry_run=dry_run,
            prime=effective_prime,
        )
        all_panels_complete = _all_panels_fetch_complete(snapshots)
        if target == "all-panels" and effective_prime and all_panels_complete:
            state.mark_catalog_primed(CATALOG_VERSION)
            state.save()
            result.catalog_primed = True
    result.fetched = len(snapshots)
    result.fetch_failed = len(TARGET_PANELS.get(target, TARGET_PANELS["all"])) - len(snapshots)
    result.api_base = api.base_url
    result.api_healthy = resolution.healthy
    result.format_specialized = format_stats["specialized"]
    result.format_generic = format_stats["generic"]
    result.format_empty = format_stats["empty"]
    result.format_aggregate = format_stats["aggregate"]
    result.format_market_scoped = format_stats["market_scoped"]
    result.format_browser_only = format_stats["browser_only"]
    result.format_non_pushable = format_stats["non_pushable"]
    result.format_unsupported = format_stats["unsupported"]
    result.format_errors = format_stats["format_errors"]
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish polyData runtime panel snapshots to Telegram channels")
    parser.add_argument("--watch", action="store_true", help="Run continuously")
    parser.add_argument("--once", action="store_true", help="Run one publish cycle and exit")
    parser.add_argument("--target", choices=sorted(TARGET_PANELS), default="all")
    parser.add_argument("--interval", type=int, default=None, help="Watch interval in seconds")
    parser.add_argument("--api-base", default="", help="Override polyData API base URL for this run")
    parser.add_argument("--dry-run", action="store_true", help="Print outgoing messages without calling Telegram")
    parser.add_argument(
        "--prime",
        action="store_true",
        help="Mark current candidates as seen without sending them; all-panels also primes automatically on its first live run",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    settings = load_settings()
    dry_run = bool(args.dry_run or settings.dry_run)
    if not dry_run and not args.prime and not settings.bot_token:
        print("POLYDATA_TELEGRAM_BOT_TOKEN is required. Use --dry-run or --prime to run without sending.", file=sys.stderr)
        return 2

    interval = max(15, int(args.interval or settings.watch_interval_seconds))
    _write_heartbeat(phase="starting")
    while True:
        _write_heartbeat(phase="cycle-start")
        try:
            result = run_once(settings=settings, target=args.target, dry_run=dry_run, prime=bool(args.prime), api_base_override=str(args.api_base or ""))
            print(json.dumps(result.__dict__, ensure_ascii=True), file=sys.stderr)
            _write_heartbeat(phase="cycle-complete", result=result)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[telegram-publisher] ERROR {exc}", file=sys.stderr)
            _write_heartbeat(phase="cycle-error", error=str(exc))
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
