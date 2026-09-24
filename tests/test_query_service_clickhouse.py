from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from api.services import query_service


class QueryServiceClickHouseTestCase(unittest.TestCase):
    def test_recent_trades_raises_when_clickhouse_enabled_but_unavailable(self):
        ctx = {"get_existing_trade_read_source": lambda: None}
        with patch.object(query_service.clickhouse_orderfilled_service, "get_recent_trades", return_value=None), patch.object(
            query_service.clickhouse_orderfilled_service,
            "clickhouse_orderfilled_enabled",
            return_value=True,
        ), patch.dict(os.environ, {"POLYDATA_ORDERFILLED_CLICKHOUSE_FALLBACK_ON_UNAVAILABLE": "0"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "ClickHouse OrderFilled read is enabled but unavailable"):
                query_service.get_recent_trades(ctx, limit=3)


if __name__ == "__main__":
    unittest.main()
