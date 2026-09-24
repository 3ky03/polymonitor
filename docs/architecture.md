# polyData Architecture

polyData is a Polymarket data indexing and analysis project. It combines local data pipelines, a Flask API, and a Vite/Preact dashboard.

## Current Shape

```text
polyData/
  scripts/      Python API, data pipelines, DB helpers, and operational scripts
  webpage/      Vite/Preact dashboard
  database/     schema notes and sample/reference data
  data/         runtime/generated data
  runtime_logs/ local process logs
  docs/         public documentation
  todos/        public engineering roadmap items
```

The current runtime flow is:

```text
Polymarket / chain / runtime sources
  -> Python sync jobs
  -> PostgreSQL / local caches
  -> Flask API
  -> Vite/Preact dashboard
```

## Target Boundaries

The long-term direction is to separate product code from command wrappers:

```text
api/        Flask app, routes, services, API config
core/       shared DB access, domain logic, clients, serialization
pipelines/  market, trade, oracle, LOB, and analytics workers
scripts/    thin CLI wrappers and operational helpers only
webpage/    frontend dashboard
deploy/     public deployment templates
tests/      API, pipeline, and frontend verification entrypoints
```

## Design Principles

- Keep `document/` private and out of public docs.
- Keep existing runtime entrypoints compatible during refactors.
- Move shared business logic into `core/` before moving callers.
- Prefer small compatibility wrappers over one large directory migration.
- Add verification before changing deployment or data pipeline behavior.
