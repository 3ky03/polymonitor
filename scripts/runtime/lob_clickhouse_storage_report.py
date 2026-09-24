#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_scripts_root = Path(__file__).resolve().parents[1]
_project_root = _scripts_root.parent
for _path in (str(_project_root), str(_scripts_root)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from quant.orderbook.clickhouse_sink import LobClickHouseSettings, clickhouse_lob_storage_report, create_lob_clickhouse_schema


def _human_bytes(value: int | float) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TiB"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report ClickHouse storage used by high-frequency LOB tables")
    parser.add_argument("--ensure-schema", action="store_true", help="Create LOB ClickHouse tables before reporting")
    parser.add_argument("--json", action="store_true", help="Print raw JSON")
    parser.add_argument(
        "--max-retention-mib",
        type=float,
        default=0.0,
        help="Exit with code 2 if projected TTL footprint exceeds this many MiB",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    settings = LobClickHouseSettings()
    if args.ensure_schema:
        create_lob_clickhouse_schema(settings=settings)
    report = clickhouse_lob_storage_report(settings=settings)
    max_retention_bytes = float(args.max_retention_mib or 0.0) * 1024 * 1024
    over_limit = bool(max_retention_bytes and int(report.get("projectedRetentionBytes") or 0) > max_retention_bytes)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 2 if over_limit else 0
    print("LOB ClickHouse storage")
    print(f"enabled={report['enabled']} tiers={','.join(report['tiers'])} ttl_days={report['ttlDays']}")
    for table in report["tables"]:
        print(
            "{table}: rows={rows} rows_1h={rows1h} bytes={bytes} "
            "projected_day={day} projected_retention={retention}".format(
                table=table["table"],
                rows=table["rows"],
                rows1h=table["rows1h"],
                bytes=_human_bytes(table["bytesOnDisk"]),
                day=_human_bytes(table["projectedBytesPerDay"]),
                retention=_human_bytes(table["projectedRetentionBytes"]),
            )
        )
    print(f"total={_human_bytes(report['totalBytesOnDisk'])}")
    print(f"projected_per_day={_human_bytes(report['projectedBytesPerDay'])}")
    print(f"projected_retention={_human_bytes(report['projectedRetentionBytes'])}")
    if over_limit:
        print(f"status=over-limit max_retention={_human_bytes(max_retention_bytes)}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
