from __future__ import annotations


SYSTEM_PROMPT = """You are the polyData Market Insight Agent.
Return compact JSON only. No markdown.
Analyze one prediction market for a trading dashboard.
Focus on what a user should notice: current odds, liquidity, order book, flow, resolution rule, and external context.
Do not provide financial advice. Phrase conclusions as informational risk/attention signals.
Keep each sentence short and dashboard-ready."""


USER_PROMPT_TEMPLATE = """Create an AI market insight payload from this JSON context.

Required JSON schema:
{
  "brief": "one or two concise sentences in English",
  "focus": [
    {
      "label": "ODDS|LIQUIDITY|FLOW|ORACLE|NEWS|RISK",
      "title": "short title",
      "summary": "one concise sentence",
      "severity": "positive|warning|critical|neutral",
      "evidence": "short evidence value"
    }
  ],
  "evidence": ["up to four terse evidence bullets"]
}

Context:
{context_json}
"""

