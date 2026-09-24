# Tests

This directory is the public entrypoint for future automated tests.

## Planned Coverage

- API smoke tests for `/health`, `/system/health`, `/bootstrap`, and market endpoints.
- Contract tests for backend payloads consumed by `webpage/src/types.ts`.
- Pipeline tests for market, trade, oracle, LOB, and analytics transformations.
- Frontend build and panel registry checks.

## Current Manual Checks

```bash
cd webpage && npm run build
python scripts/api_server.py --help
make status
```

No test framework is required for this first architecture skeleton.
