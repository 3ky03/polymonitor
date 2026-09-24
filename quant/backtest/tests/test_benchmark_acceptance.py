from __future__ import annotations

from quant.backtest.runners import benchmark_acceptance
from quant.backtest.runners.benchmark import BacktestBenchmarkResult


def _benchmark_result(*, status: str = "completed", market_count: int = 3) -> BacktestBenchmarkResult:
    return BacktestBenchmarkResult(
        benchmark_id=None,
        status=status,
        universe_type="preset",
        universe_name="nba_2024_25_moneyline",
        market_count=market_count,
        strategy_name="favorite_hold_v1",
        parameters={"limit": market_count},
        profiles={"requested": ["fast:realistic"]},
        summary={
            "fast_raw_rows": 120,
            "accurate_raw_rows": 450,
            "status_mismatches": 2,
            "pnl_diff_abs_total": "1.25",
            "fast_total_pnl": "3.50",
            "accurate_total_pnl": "2.25",
            "timing": {
                "fast_db_query_sec": 0.12,
                "accurate_db_query_sec": 0.44,
                "total_runtime_sec": 0.90,
            },
            "fill_quality": {
                "fill_rate": "0.67",
                "no_fill_rate": "0.33",
                "partial_fill_rate": "0",
            },
        },
        data_version="test",
        rows=[],
        profile_results=[],
    )


def test_nba_acceptance_report_summarizes_cases(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        return _benchmark_result(market_count=kwargs["limit"])

    monkeypatch.setattr(benchmark_acceptance, "run_nba_fast_accurate_benchmark", fake_run)

    report = benchmark_acceptance.run_nba_acceptance_report(
        limits=(2,),
        profile_groups=(("fast:realistic",), ("fast:realistic", "accurate:realistic")),
        force_block_replay_backfill=True,
    )

    assert report.status == "completed"
    assert len(report.cases) == 2
    assert [call["limit"] for call in calls] == [2, 2]
    assert [call["profile_keys"] for call in calls] == [
        ("fast:realistic",),
        ("fast:realistic", "accurate:realistic"),
    ]
    assert all(call["force_block_replay_backfill"] is True for call in calls)
    assert report.summary["case_count"] == 2
    assert report.summary["completed_count"] == 2
    assert report.summary["failed_count"] == 0
    assert report.summary["fast_raw_rows_total"] == 240
    assert report.summary["accurate_raw_rows_total"] == 900
    assert report.summary["status_mismatches_total"] == 4
    assert report.summary["pnl_diff_abs_total"] == "2.5"
    assert report.cases[0].fill_rate == "0.67"


def test_nba_acceptance_report_records_failed_cases(monkeypatch):
    def fake_run(**kwargs):
        if kwargs["profile_keys"] == ("fast:stress",):
            raise RuntimeError("boom")
        return _benchmark_result()

    monkeypatch.setattr(benchmark_acceptance, "run_nba_fast_accurate_benchmark", fake_run)

    report = benchmark_acceptance.run_nba_acceptance_report(
        limits=(1,),
        profile_groups=(("fast:realistic",), ("fast:stress",)),
    )

    assert report.status == "failed"
    assert report.summary["completed_count"] == 1
    assert report.summary["failed_count"] == 1
    assert report.summary["failed_cases"] == [
        {"case_key": "nba_2024_25_moneyline:1:fast:stress", "error": "boom"}
    ]
    assert report.cases[1].status == "failed"
    assert report.cases[1].error == "boom"


def test_acceptance_cli_exits_nonzero_on_failure(monkeypatch, capsys):
    def fake_report(**_kwargs):
        return benchmark_acceptance.AcceptanceReport(
            status="failed",
            universe="nba_2024_25_moneyline",
            strategy="favorite_hold_v1",
            limits=(1,),
            profile_groups=(("fast:realistic",),),
            started_at_epoch=0.0,
            total_runtime_sec=0.1,
            cases=[],
            summary={"case_count": 1, "completed_count": 0, "failed_count": 1},
        )

    monkeypatch.setattr(benchmark_acceptance, "run_nba_acceptance_report", fake_report)

    exit_code = benchmark_acceptance.main(["--limits", "1"])

    assert exit_code == 1
    assert "acceptance failed" in capsys.readouterr().out
