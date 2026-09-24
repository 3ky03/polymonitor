from __future__ import annotations

import unittest
from unittest.mock import patch


class AgentGatewayBudgetTestCase(unittest.TestCase):
    def test_gateway_budget_disabled_bypasses_project_budget_gate(self):
        with patch.dict(
            "os.environ",
            {
                "POLYDATA_AGENT_ENABLED": "true",
                "POLYDATA_AGENT_LOCAL_ONLY": "true",
                "POLYDATA_AGENT_GATEWAY_TOKEN": "",
                "POLYDATA_AGENT_GATEWAY_BUDGET_DISABLED": "true",
            },
            clear=False,
        ):
            from agent.gateway.app import create_app

            app = create_app()
            client = app.test_client()
            with patch("agent.gateway.app.claim_agent_live_call", return_value=(False, {"enabled": True, "remaining": 0})), patch(
                "agent.gateway.app.build_market_wide_insight",
                return_value={"status": "live", "brief": "ok"},
            ):
                response = client.post("/agent/market-wide-insights", json={"lens": "overview"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "live")
        self.assertEqual(payload["dailyBudget"]["enabled"], False)
        self.assertNotEqual(payload.get("cacheStatus"), "budget-fallback")


if __name__ == "__main__":
    unittest.main()
