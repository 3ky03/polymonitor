#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket 市场元数据库完整性验证脚本

对 markets 表执行三维健康检查：
1. 宏观概览与边界检查
2. 微观一致性检查（脏数据排查）
3. 时间序列连续性检查（断层排查）
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_scripts_root = Path(__file__).resolve().parent.parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))
from db import DEFAULT_DB_PATH, get_backend, get_connection

# 目标时间段
TARGET_START = "2024-09-01"


def _sep(title: str, char: str = "=") -> None:
    w = 70
    print(char * w)
    print(f" {title}")
    print(char * w)


def _warn(msg: str) -> None:
    print(f"  ⚠  [警告] {msg}")


def _ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def run_verify(db_path: str) -> None:
    if get_backend() == "sqlite" and not Path(db_path).exists():
        print(f"错误: 数据库文件不存在: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = get_connection(db_path)
    cur = conn.cursor()

    # -------------------------------------------------------------------------
    # 1. 宏观概览与边界检查
    # -------------------------------------------------------------------------
    _sep("1. 宏观概览与边界检查", "=")

    cur.execute("SELECT COUNT(*) FROM markets")
    total = cur.fetchone()[0]
    print(f"\n  总记录数: {total:,}")

    cur.execute(
        "SELECT MIN(created_at), MAX(created_at) FROM markets WHERE created_at IS NOT NULL"
    )
    row = cur.fetchone()
    min_ts, max_ts = row[0], row[1]
    print(f"  created_at 最早: {min_ts or 'N/A'}")
    print(f"  created_at 最晚: {max_ts or 'N/A'}")

    # 目标时间段覆盖检查
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if min_ts and min_ts[:10] <= TARGET_START and (not max_ts or max_ts[:10] >= now_str[:10]):
        _ok(f"目标时间段 ({TARGET_START} 至今) 已覆盖")
    elif min_ts and max_ts:
        if min_ts[:10] > TARGET_START:
            _warn(f"数据起始于 {min_ts[:10]}，晚于目标起点 {TARGET_START}（可能缺少前期数据）")
        if max_ts[:10] < now_str[:10]:
            _warn(f"数据截止于 {max_ts[:10]}，未覆盖至今日 {now_str}")
    else:
        _warn("created_at 存在大量空值，无法判断时间覆盖")

    # -------------------------------------------------------------------------
    # 2. 微观一致性检查（脏数据排查）
    # -------------------------------------------------------------------------
    _sep("2. 微观一致性检查（脏数据排查）", "=")

    # 2.1 唯一性校验
    cur.execute(
        """
        SELECT condition_id, COUNT(*) as cnt
        FROM markets
        GROUP BY condition_id
        HAVING cnt > 1
        """
    )
    dupes = cur.fetchall()
    dup_count = sum(r[1] for r in dupes)
    dup_rows = len(dupes)
    if dup_rows == 0:
        _ok("condition_id 唯一性: 无重复")
    else:
        _warn(f"condition_id 存在 {dup_rows} 个重复值，涉及 {dup_count} 条记录")

    # 2.2 骨架完整性校验
    cur.execute(
        """
        SELECT
            SUM(CASE WHEN question_id IS NULL OR TRIM(question_id) = '' THEN 1 ELSE 0 END),
            SUM(CASE WHEN oracle IS NULL OR TRIM(oracle) = '' THEN 1 ELSE 0 END),
            SUM(CASE WHEN yes_token_id IS NULL OR TRIM(yes_token_id) = '' THEN 1 ELSE 0 END),
            SUM(CASE WHEN no_token_id IS NULL OR TRIM(no_token_id) = '' THEN 1 ELSE 0 END)
        FROM markets
        """
    )
    q_id_n, oracle_n, yes_n, no_n = cur.fetchone()
    q_id_n = q_id_n or 0
    oracle_n = oracle_n or 0
    yes_n = yes_n or 0
    no_n = no_n or 0

    print("\n  关键字段空值统计:")
    all_zero = q_id_n == 0 and oracle_n == 0 and yes_n == 0 and no_n == 0
    for name, n in [("question_id", q_id_n), ("oracle", oracle_n),
                    ("yes_token_id", yes_n), ("no_token_id", no_n)]:
        status = "✓" if n == 0 else "⚠"
        print(f"    {status} {name}: {n:,} 条为空")
    if all_zero:
        _ok("骨架完整性: 关键字段无空值")
    else:
        _warn("骨架完整性: 存在空值，建议排查或清洗")

    # -------------------------------------------------------------------------
    # 3. 时间序列连续性检查（断层排查）
    # -------------------------------------------------------------------------
    _sep("3. 时间序列连续性检查（断层排查）", "=")

    cur.execute(
        """
        SELECT
            DATE(SUBSTR(created_at, 1, 10)) AS day,
            COUNT(*) AS cnt
        FROM markets
        WHERE created_at IS NOT NULL AND created_at != ''
        GROUP BY day
        ORDER BY day
        """
    )
    daily = cur.fetchall()

    if not daily:
        _warn("无有效 created_at，无法进行时间序列检查")
    else:
        days = [r[0] for r in daily]
        counts = [r[1] for r in daily]
        avg = sum(counts) / len(counts) if counts else 0
        threshold = avg * 0.2  # 低于均值 20% 视为异常偏低

        low_days = [(d, c) for d, c in daily if c < threshold and c < avg]
        low_days.sort(key=lambda x: x[1])

        print(f"\n  有效天数: {len(days)}")
        print(f"  日均新增市场数: {avg:.1f}")
        print(f"  异常阈值 (均值 20%): {threshold:.1f}")

        # 数量最少的前 10 天
        top10_low = sorted(daily, key=lambda x: x[1])[:10]
        print("\n  单日新增最少的前 10 天 (疑似漏抓断层):")
        for day, cnt in top10_low:
            flag = "⚠" if cnt < threshold else " "
            print(f"    {flag} {day}: {cnt} 个市场")
        if low_days:
            _warn(f"共有 {len(low_days)} 天低于异常阈值，建议复核爬取日志")
        else:
            _ok("时间序列无明显断层")

    conn.close()

    # -------------------------------------------------------------------------
    # 报告结尾
    # -------------------------------------------------------------------------
    _sep("数据体检报告完成", "-")
    print(f"  数据库: {db_path}")
    print(f"  生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="验证 Polymarket 市场元数据库的完整性与数据质量"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        metavar="PATH",
        help=f"SQLite 数据库文件路径 (默认: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()
    run_verify(args.db)


if __name__ == "__main__":
    main()
