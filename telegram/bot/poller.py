from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from typing import Iterable

from .alerts import due_alert_replies, utc_now_text
from .client import TelegramBotClient
from .commands import handle_command
from .config import BotSettings, load_settings
from .models import BotReply
from .polydata_api import PolyDataBotApi
from .router import parse_update
from .state import BotState
from telegram.topics.api_client import resolve_polydata_api_base


BOT_COMMANDS = [
    {"command": "worldcup", "description": "World Cup overview"},
    {"command": "matches", "description": "World Cup matches"},
    {"command": "match", "description": "Match detail"},
    {"command": "team", "description": "Team schedule and intel"},
    {"command": "venue", "description": "Venue and weather"},
    {"command": "news", "description": "World Cup news"},
    {"command": "odds", "description": "Polymarket odds"},
    {"command": "market", "description": "Search Polymarket markets"},
    {"command": "help", "description": "Command help"},
]


def _iter_replies(updates: Iterable[dict], *, settings: BotSettings, state: BotState, api: PolyDataBotApi) -> Iterable[tuple[int, int | str, BotReply]]:
    for update in updates:
        request = parse_update(update)
        if request is None:
            continue
        if not settings.chat_allowed(request.chat_id, request.user_id):
            yield request.update_id, request.chat_id, BotReply("⚠️ 当前 chat 未授权使用此 bot。")
            continue
        if state.rate_limited(chat_id=request.chat_id, user_id=request.user_id, limit=settings.rate_limit_per_minute):
            yield request.update_id, request.chat_id, BotReply("⚠️ 查询太频繁了，请稍后再试。")
            continue
        reply = handle_command(request, api, state=state)
        state.record_user(chat_id=request.chat_id, user_id=request.user_id)
        missed = any(marker in reply.text for marker in ("没有找到", "暂未直接匹配", "no listed match market", "服务暂时不可用"))
        state.record_query(
            chat_id=request.chat_id,
            user_id=request.user_id,
            command=request.command,
            args=request.args,
            matched=not missed,
        )
        if request.callback_query_id:
            reply = replace(reply, callback_query_id=request.callback_query_id)
        yield request.update_id, request.chat_id, reply


def run_once(
    *,
    settings: BotSettings,
    state: BotState,
    telegram: TelegramBotClient,
    api: PolyDataBotApi,
    dry_run: bool = False,
    limit: int = 50,
) -> int:
    updates = telegram.get_updates(offset=state.offset, timeout_seconds=settings.long_poll_timeout_seconds, limit=limit)
    handled = 0
    for update_id, chat_id, reply in _iter_replies(updates, settings=settings, state=state, api=api):
        if dry_run:
            print(json.dumps({"chatId": chat_id, "text": reply.text}, ensure_ascii=False))
        else:
            if reply.callback_query_id:
                try:
                    telegram.answer_callback_query(callback_query_id=reply.callback_query_id)
                except Exception:
                    pass
            telegram.send_message(
                chat_id=str(chat_id),
                text=reply.text,
                disable_web_page_preview=not reply.link_preview,
                reply_markup=reply.reply_markup,
            )
        if not dry_run:
            state.mark_update(update_id)
        handled += 1
    if handled and not dry_run:
        state.save()
    return handled


def check_alerts(
    *,
    settings: BotSettings,
    state: BotState,
    telegram: TelegramBotClient,
    api: PolyDataBotApi,
    dry_run: bool = False,
) -> int:
    now = time.time()
    if now - state.last_alert_check_ts < settings.alert_check_interval_seconds:
        return 0
    sent = 0
    for chat_id, alert_id, reply, price in due_alert_replies(state, api):
        if dry_run:
            print(json.dumps({"chatId": chat_id, "alertId": alert_id, "text": reply.text}, ensure_ascii=False))
        else:
            telegram.send_message(chat_id=str(chat_id), text=reply.text, disable_web_page_preview=True)
        state.mark_alert_triggered(alert_id=alert_id, timestamp=utc_now_text(), price=price)
        sent += 1
    state.mark_alert_check(now)
    state.save()
    return sent


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the interactive polyData Telegram bot")
    parser.add_argument("--once", action="store_true", help="Process one getUpdates batch and exit")
    parser.add_argument("--dry-run", action="store_true", help="Print replies instead of sending Telegram messages")
    parser.add_argument("--limit", type=int, default=50, help="Maximum updates per getUpdates call")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    settings = load_settings()
    dry_run = bool(args.dry_run or settings.dry_run)
    if not dry_run and not settings.bot_token:
        print("POLYDATA_TELEGRAM_BOT_TOKEN is required. Use --dry-run for local command tests.", file=sys.stderr)
        return 2
    state = BotState(settings.state_path)
    telegram = TelegramBotClient(
        bot_token=settings.bot_token,
        api_base=settings.telegram_api_base,
        timeout_seconds=settings.request_timeout_seconds,
    )
    if not dry_run:
        try:
            telegram.set_my_commands(BOT_COMMANDS)
        except Exception as exc:
            print(f"[telegram-bot] WARN setMyCommands failed: {exc}", file=sys.stderr)
    resolution = resolve_polydata_api_base(settings.polydata_api_candidates, timeout_seconds=min(5, settings.request_timeout_seconds))
    if not resolution.healthy:
        checked = ", ".join(resolution.checked) or "(none)"
        print(f"[telegram-bot] WARN no healthy polyData API found; using {resolution.base_url or settings.polydata_api_base} after checking {checked}", file=sys.stderr)
    api = PolyDataBotApi(
        base_url=resolution.base_url or settings.polydata_api_base,
        base_urls=settings.polydata_api_candidates,
        timeout_seconds=settings.request_timeout_seconds,
    )
    while True:
        try:
            handled = run_once(settings=settings, state=state, telegram=telegram, api=api, dry_run=dry_run, limit=args.limit)
            alerts_sent = check_alerts(settings=settings, state=state, telegram=telegram, api=api, dry_run=dry_run)
            print(json.dumps({"handled": handled, "alertsSent": alerts_sent, "offset": state.offset, "dryRun": dry_run}, ensure_ascii=True), file=sys.stderr)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"[telegram-bot] ERROR {exc}", file=sys.stderr)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
