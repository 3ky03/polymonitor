#!/usr/bin/env python3
"""Render a frontend-like multi-outcome event chart using prices-history."""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import requests

from scripts.data_sources import POLYMARKET_CLOB_API_BASE, POLYMARKET_GAMMA_API_BASE

UTC = timezone.utc
DEFAULT_OUTPUT_DIR = Path("runtime_outputs/tradeV2")
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 4
GAMMA_API_BASE = POLYMARKET_GAMMA_API_BASE
CLOB_API_BASE = POLYMARKET_CLOB_API_BASE


def _safe_json_dumps(payload: Any) -> str:
    def _default_serializer(value: Any) -> Any:
        if isinstance(value, (datetime, pd.Timestamp)):
            return pd.Timestamp(value).isoformat()
        return str(value)

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_default_serializer,
    )


def write_json_payload(payload: dict[str, Any], output_path: Path | str) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return output


def _request_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    if last_error is None:
        raise RuntimeError("request failed without exception")
    raise last_error


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "polyData-priceHistory/1.0",
        }
    )
    return session


def _extract_candidate_label(question: str) -> str:
    text = (question or "").strip()
    match = re.match(r"Will\s+(.+?)\s+win\b", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text[:48] if text else "Unknown"


def _fetch_event(session: requests.Session, slug: str) -> dict[str, Any]:
    payload = _request_json(
        session,
        f"{GAMMA_API_BASE}/events",
        params={"slug": slug},
    )
    if not payload:
        raise RuntimeError(f"event not found for slug={slug}")
    return payload[0]


def _yes_token_for_market(market: dict[str, Any]) -> tuple[str | None, str]:
    outcomes = market.get("outcomes") or []
    token_ids = market.get("clobTokenIds") or []
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:  # noqa: BLE001
            outcomes = []
    if isinstance(token_ids, str):
        try:
            token_ids = json.loads(token_ids)
        except Exception:  # noqa: BLE001
            token_ids = []
    if len(outcomes) >= 1 and len(token_ids) >= 1:
        if str(outcomes[0]).lower() == "yes":
            return str(token_ids[0]), str(outcomes[0])
    if token_ids:
        return str(token_ids[0]), str(outcomes[0]) if outcomes else "token0"
    return None, ""


def _fetch_prices_history(
    session: requests.Session,
    token_id: str,
    *,
    start_ts: int,
    end_ts: int,
    fidelity: int,
) -> list[dict[str, Any]]:
    payload = _request_json(
        session,
        f"{CLOB_API_BASE}/prices-history",
        params={
            "market": token_id,
            "startTs": int(start_ts),
            "endTs": int(end_ts),
            "fidelity": int(fidelity),
        },
    )
    history = payload.get("history", []) if isinstance(payload, dict) else []
    return [item for item in history if start_ts <= int(item.get("t", 0)) <= end_ts]


def fetch_segmented_prices_history(
    session: requests.Session,
    token_id: str,
    *,
    start_ts: int,
    end_ts: int,
    fidelity: int = 5,
    segment_days: int = 14,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cursor_start = int(start_ts)
    segment_seconds = segment_days * 86400

    while cursor_start <= end_ts:
        cursor_end = min(end_ts, cursor_start + segment_seconds - 1)
        try:
            history = _fetch_prices_history(
                session,
                token_id,
                start_ts=cursor_start,
                end_ts=cursor_end,
                fidelity=fidelity,
            )
        except Exception:  # noqa: BLE001
            history = []
        rows.extend(history)
        cursor_start = cursor_end + 1

    if not rows:
        return pd.DataFrame(columns=["timestamp", "price", "datetime"])

    frame = pd.DataFrame(rows).rename(columns={"t": "timestamp", "p": "price"})
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "price"]).sort_values("timestamp").drop_duplicates(
        subset=["timestamp"], keep="last"
    )
    frame = frame[(frame["timestamp"] >= start_ts) & (frame["timestamp"] <= end_ts)].reset_index(drop=True)
    frame["datetime"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
    return frame


def build_continuous_price_series(
    history: pd.DataFrame,
    *,
    start_ts: int,
    end_ts: int,
    interval: str = "5min",
) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame(columns=["timestamp", "datetime", "price"])
    base = history.copy()
    base["datetime"] = pd.to_datetime(base["timestamp"], unit="s", utc=True)
    grid = pd.date_range(
        start=pd.to_datetime(start_ts, unit="s", utc=True).floor(interval),
        end=pd.to_datetime(end_ts, unit="s", utc=True).floor(interval),
        freq=interval,
        tz=UTC,
    )
    series = (
        base.set_index("datetime")[["price"]]
        .sort_index()
        .reindex(base.set_index("datetime").index.union(grid))
        .sort_index()
        .ffill()
        .reindex(grid)
        .reset_index()
        .rename(columns={"index": "datetime"})
    )
    series["timestamp"] = series["datetime"].astype("int64") // 10**9
    return series[["timestamp", "datetime", "price"]]


def plot_multi_price_history_lines(
    price_frames: dict[str, pd.DataFrame],
    output_path: Path | str,
    *,
    title: str | None = None,
    figure_width: float = 18,
    figure_height: float = 7,
) -> None:
    if not price_frames:
        raise ValueError("price frames are empty")
    output = Path(output_path)

    fig, ax = plt.subplots(figsize=(figure_width, figure_height))
    palette = [
        "#87b8ff",
        "#356dff",
        "#f4b400",
        "#ff7a1a",
        "#34a853",
        "#d93025",
        "#7e57c2",
        "#00acc1",
    ]

    for index, (label, frame) in enumerate(price_frames.items()):
        working = frame.copy()
        working["datetime"] = pd.to_datetime(working["datetime"], utc=True)
        working["price"] = pd.to_numeric(working["price"], errors="coerce") * 100.0
        working = working.dropna(subset=["datetime", "price"])
        if working.empty:
            continue
        color = palette[index % len(palette)]
        ax.plot(working["datetime"], working["price"], color=color, linewidth=2.0, label=label)
        ax.scatter(working["datetime"].iloc[-1], working["price"].iloc[-1], color=color, s=42, zorder=3)

    ax.set_title(title or "Event Price History")
    ax.set_ylabel("Probability (%)")
    ax.set_ylim(0, 100)
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
    formatter = mdates.ConciseDateFormatter(locator, tz=UTC)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    ax.legend(loc="upper left", ncol=4, frameon=False, fontsize=10, handlelength=2.5, columnspacing=1.0)
    fig.autofmt_xdate()
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a Polymarket frontend-like event chart")
    parser.add_argument("--event-slug", required=True, help="Gamma event slug")
    parser.add_argument("--days", type=int, default=30, help="lookback window")
    parser.add_argument("--fidelity", type=int, default=5, help="prices-history fidelity in minutes")
    parser.add_argument("--top-n", type=int, default=4, help="keep only the top N outcomes by latest price")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="output directory")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    session = _build_session()
    event = _fetch_event(session, args.event_slug)
    markets = event.get("markets") or []
    end_dt = datetime.now(tz=UTC)
    start_dt = end_dt - timedelta(days=args.days)
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    line_frames: dict[str, pd.DataFrame] = {}
    series_payload: dict[str, list[dict[str, Any]]] = {}
    outcome_summaries: list[dict[str, Any]] = []

    for market in markets:
        token_id, token_side = _yes_token_for_market(market)
        if not token_id:
            continue
        label = _extract_candidate_label(str(market.get("question") or market.get("title") or market.get("slug") or ""))
        history = fetch_segmented_prices_history(
            session,
            token_id,
            start_ts=start_ts,
            end_ts=end_ts,
            fidelity=args.fidelity,
        )
        if history.empty:
            continue
        series = build_continuous_price_series(
            history,
            start_ts=start_ts,
            end_ts=end_ts,
            interval=f"{args.fidelity}min",
        )
        series = series.dropna(subset=["price"]).reset_index(drop=True)
        if series.empty:
            continue
        line_frames[label] = series
        series_payload[label] = json.loads(series.to_json(orient="records", date_format="iso"))
        outcome_summaries.append(
            {
                "label": label,
                "condition_id": market.get("conditionId"),
                "token_id": token_id,
                "token_side": token_side,
                "points": int(len(series)),
                "last_price": float(series["price"].iloc[-1]),
            }
        )

    if not line_frames:
        raise RuntimeError("no outcome price series available for this event")

    outcome_summaries = sorted(outcome_summaries, key=lambda item: item["last_price"], reverse=True)
    selected_labels = [item["label"] for item in outcome_summaries[: max(1, args.top_n)]]
    line_frames = {label: line_frames[label] for label in selected_labels if label in line_frames}
    outcome_summaries = [item for item in outcome_summaries if item["label"] in selected_labels]
    series_payload = {label: series_payload[label] for label in selected_labels if label in series_payload}

    png_path = output_dir / f"event_frontend_like_{args.event_slug}.png"
    json_path = output_dir / f"event_frontend_like_{args.event_slug}.json"

    plot_multi_price_history_lines(
        line_frames,
        png_path,
        title=str(event.get("title") or event.get("slug") or args.event_slug).strip(),
        figure_width=18,
        figure_height=7,
    )
    write_json_payload(
        {
            "meta": {
                "event_slug": args.event_slug,
                "event_title": event.get("title"),
                "lookback_days": args.days,
                "fidelity": args.fidelity,
                "top_n": args.top_n,
                "source": "prices-history",
            },
            "outcomes": outcome_summaries,
            "series": series_payload,
        },
        json_path,
    )
    print(_safe_json_dumps({"png": str(png_path), "json": str(json_path), "outcomes": len(outcome_summaries)}))


if __name__ == "__main__":
    main()
