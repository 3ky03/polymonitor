#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 polymarket_indexer.db 是否覆盖 2024-09-04 至 2026-02-22 中任意日期的市场数据。
对区间内若干采样日期查询，验证是否存在 created_at 落在该日的市场。
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))
from db import DEFAULT_DB_PATH, get_backend, get_connection
DEFAULT_DB = DEFAULT_DB_PATH


def test_coverage(db_path: str = DEFAULT_DB) -> None:
    start = datetime(2024, 9, 4, tzinfo=timezone.utc)
    end = datetime(2026, 2, 22, tzinfo=timezone.utc)

    if get_backend() == "sqlite" and not Path(db_path).exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = get_connection(db_path)
    cur = conn.cursor()

    # 采样：区间首尾 + 每月 1 号和 15 号
    sample_dates = [start, end]
    for year in (2024, 2025, 2026):
        for month in range(1, 13):
            for day in (1, 15):
                try:
                    d = datetime(year, month, day, tzinfo=timezone.utc)
                    if start <= d <= end:
                        sample_dates.append(d)
                except ValueError:
                    pass
    sample_dates = sorted(set(sample_dates))

    missing = []
    found = []
    for dt in sample_dates:
        day_start = dt.strftime("%Y-%m-%d")
        day_end = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
        cur.execute(
            """
            SELECT COUNT(*) FROM markets
            WHERE created_at >= ? AND created_at < ?
            """,
            (day_start, day_end),
        )
        cnt = cur.fetchone()[0]
        if cnt > 0:
            found.append((day_start, cnt))
        else:
            missing.append(day_start)

    conn.close()

    print("=== 市场数据覆盖测试 (2024-09-04 至 2026-02-22) ===\n")
    print(f"采样日期数: {len(sample_dates)}")
    print(f"有数据的日期: {len(found)}")
    print(f"无数据的日期: {len(missing)}\n")

    if missing:
        print("无数据的采样日期:")
        for d in missing:
            print(f"  - {d}")
        print()

    print("部分有数据的采样日期及数量:")
    for day, cnt in found[:15]:
        print(f"  {day}: {cnt} 个市场")
    if len(found) > 15:
        print(f"  ... 共 {len(found)} 个日期有数据")
    print()

    if not missing:
        print("结论: 所有采样日期均有数据，区间内覆盖良好。")
    else:
        pct = 100.0 * len(found) / len(sample_dates)
        print(f"结论: 采样日期中 {pct:.1f}% 有数据，{len(missing)} 个日期无数据。")


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    test_coverage(db)
