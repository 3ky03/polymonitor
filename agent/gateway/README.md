# Agent Gateway

Run this on the development machine that can resolve and reach `gpt-api.hkust-gz.edu.cn`.

```bash
export POLYDATA_AGENT_GATEWAY_TOKEN="<shared-secret>"
python -m agent.gateway.app
```

Expose it to GCP with a reverse SSH tunnel:

```bash
ssh -N -R 127.0.0.1:18700:127.0.0.1:18700 "${POLYDATA_AGENT_GATEWAY_REMOTE}"
```

On GCP, configure:

```bash
POLYDATA_AGENT_GATEWAY_BASE_URL=http://127.0.0.1:18700
POLYDATA_AGENT_GATEWAY_URL=http://127.0.0.1:18700/agent/market-insights
POLYDATA_AGENT_GATEWAY_TOKEN=<shared-secret>
```

If the gateway or tunnel is unavailable, the GCP API falls back to its local deterministic/search fallback.
