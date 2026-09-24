"""Acceptance benchmark report for OrderFilled-first replay performance."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
import time
from typing import Any

from .benchmark import BacktestBenchmarkResult, run_nba_fast_accurate_benchmark


DEFAULT_LIMITS = (50, 100, 500)
DEFAULT_PROFILE_GROUPS = (
    ("fast:realistic",),
    ("fast:stress",),
    ("accurate:realistic",),
    ("fast:realistic", "accurate:realistic"),
)


@dataclass(frozen=True)
class AcceptanceCaseResult:
    case_key: str
    universe: str
    limit: int
    profiles: tuple[str, ...]
    status: str
    market_count: int
    fast_raw_rows: int
    accurate_raw_rows: int
    selector_sec: float
    fast_db_query_sec: float
    accurate_db_query_sec: float
    total_runtime_sec: float
    status_mismatches: int
    pnl_diff_abs_total: str
    fast_total_pnl: str
    accurate_total_pnl: str
    fill_rate: str
    no_fill_rate: str
    partial_fill_rate: str
    error: str | None = None


@dataclass(frozen=True)
class AcceptanceReport:
    status: str
    universe: str
    strategy: str
    limits: tuple[int, ...]
    profile_groups: tuple[tuple[str, ...], ...]
    started_at_epoch: float
    total_runtime_sec: float
    cases: list[AcceptanceCaseResult]
    summary: dict[str, Any]


def run_nba_acceptance_report(
    *,
    limits: tuple[int, ...] = DEFAULT_LIMITS,
    profile_groups: tuple[tuple[str, ...], ...] = DEFAULT_PROFILE_GROUPS,
    persist_conn: Any | None = None,
    force_block_replay_backfill: bool = False,
) -> AcceptanceReport:
    """Run the standard NBA benchmark acceptance suite.

    The report intentionally summarizes runner behavior and performance. It
    does not decide whether a strategy is profitable.
    """

    started_epoch = time.time()
    started = time.perf_counter()
    cases: list[AcceptanceCaseResult] = []
    for limit in limits:
        for profiles in profile_groups:
            case_key = f"nba_2024_25_moneyline:{limit}:{'+'.join(profiles)}"
            try:
                result = run_nba_fast_accurate_benchmark(
                    limit=int(limit),
                    persist_conn=persist_conn,
                    force_block_replay_backfill=force_block_replay_backfill,
                    profile_keys=tuple(profiles),
                )
                cases.append(_case_from_result(case_key, limit=int(limit), profiles=tuple(profiles), result=result))
            except Exception as exc:
                cases.append(
                    AcceptanceCaseResult(
                        case_key=case_key,
                        universe="nba_2024_25_moneyline",
                        limit=int(limit),
                        profiles=tuple(profiles),
                        status="failed",
                        market_count=0,
                        fast_raw_rows=0,
                        accurate_raw_rows=0,
                        selector_sec=0.0,
                        fast_db_query_sec=0.0,
                        accurate_db_query_sec=0.0,
                        total_runtime_sec=0.0,
                        status_mismatches=0,
                        pnl_diff_abs_total="0",
                        fast_total_pnl="0",
                        accurate_total_pnl="0",
                        fill_rate="0",
                        no_fill_rate="0",
                        partial_fill_rate="0",
                        error=str(exc),
                    )
                )
    total_runtime = round(time.perf_counter() - started, 6)
    status = "completed" if all(case.status == "completed" for case in cases) else "failed"
    return AcceptanceReport(
        status=status,
        universe="nba_2024_25_moneyline",
        strategy="favorite_hold_v1",
        limits=tuple(int(item) for item in limits),
        profile_groups=tuple(tuple(group) for group in profile_groups),
        started_at_epoch=started_epoch,
        total_runtime_sec=total_runtime,
        cases=cases,
        summary=_summary(cases, total_runtime_sec=total_runtime),
    )


def _case_from_result(case_key: str, *, limit: int, profiles: tuple[str, ...], result: BacktestBenchmarkResult) -> AcceptanceCaseResult:
    timing = result.summary.get("timing") or {}
    fill_quality = result.summary.get("fill_quality") or {}
    return AcceptanceCaseResult(
        case_key=case_key,
        universe=result.universe_name,
        limit=limit,
        profiles=profiles,
        status=result.status,
        market_count=int(result.market_count),
        fast_raw_rows=int(result.summary.get("fast_raw_rows") or 0),
        accurate_raw_rows=int(result.summary.get("accurate_raw_rows") or 0),
        selector_sec=float(timing.get("selector_sec") or 0.0),
        fast_db_query_sec=float(timing.get("fast_db_query_sec") or 0.0),
        accurate_db_query_sec=float(timing.get("accurate_db_query_sec") or 0.0),
        total_runtime_sec=float(timing.get("total_runtime_sec") or 0.0),
        status_mismatches=int(result.summary.get("status_mismatches") or 0),
        pnl_diff_abs_total=str(result.summary.get("pnl_diff_abs_total") or "0"),
        fast_total_pnl=str(result.summary.get("fast_total_pnl") or "0"),
        accurate_total_pnl=str(result.summary.get("accurate_total_pnl") or "0"),
        fill_rate=str(fill_quality.get("fill_rate") or "0"),
        no_fill_rate=str(fill_quality.get("no_fill_rate") or "0"),
        partial_fill_rate=str(fill_quality.get("partial_fill_rate") or "0"),
        error=None,
    )


def _summary(cases: list[AcceptanceCaseResult], *, total_runtime_sec: float) -> dict[str, Any]:
    completed = [case for case in cases if case.status == "completed"]
    failed = [case for case in cases if case.status != "completed"]
    return {
        "case_count": len(cases),
        "completed_count": len(completed),
        "failed_count": len(failed),
        "limits": sorted({case.limit for case in cases}),
        "profile_groups": ["+".join(case.profiles) for case in cases],
        "total_runtime_sec": total_runtime_sec,
        "max_case_runtime_sec": max((case.total_runtime_sec for case in completed), default=0.0),
        "selector_sec_total": round(sum(case.selector_sec for case in completed), 6),
        "fast_raw_rows_total": sum(case.fast_raw_rows for case in completed),
        "accurate_raw_rows_total": sum(case.accurate_raw_rows for case in completed),
        "status_mismatches_total": sum(case.status_mismatches for case in completed),
        "pnl_diff_abs_total": _decimal_text(sum((Decimal(str(case.pnl_diff_abs_total or "0")) for case in completed), Decimal("0"))),
        "failed_cases": [{"case_key": case.case_key, "error": case.error} for case in failed],
    }


def _decimal_text(value: Decimal | int | str) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(decimal_value.normalize(), "f")


def _parse_limits(value: str) -> tuple[int, ...]:
    return tuple(int(item.strip()) for item in str(value).split(",") if item.strip())


def _parse_profile_groups(value: str) -> tuple[tuple[str, ...], ...]:
    groups = []
    for group in str(value).split(";"):
        profiles = tuple(item.strip() for item in group.split(",") if item.strip())
        if profiles:
            groups.append(profiles)
    return tuple(groups)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limits", default=",".join(str(item) for item in DEFAULT_LIMITS))
    parser.add_argument("--profile-groups", default=";".join(",".join(group) for group in DEFAULT_PROFILE_GROUPS))
    parser.add_argument("--force-block-replay-backfill", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run_nba_acceptance_report(
        limits=_parse_limits(args.limits),
        profile_groups=_parse_profile_groups(args.profile_groups),
        force_block_replay_backfill=bool(args.force_block_replay_backfill),
    )
    payload = asdict(report)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    else:
        print(
            f"acceptance {report.status}: cases={report.summary['case_count']} "
            f"completed={report.summary['completed_count']} failed={report.summary['failed_count']} "
            f"runtime={report.total_runtime_sec}s"
        )
    return 0 if report.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
