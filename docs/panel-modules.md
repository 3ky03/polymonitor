# Panel Modules

polyData dashboard panels are registered through frontend and backend manifests so new panels can be added without editing the dashboard shell.

## Design Intent

The panel system is intentionally moving away from "large central scripts" and toward small, explicit modules. A panel should own its data access, render entrypoint, metadata, refresh behavior, and backend snapshot logic. Central files should only compose manifests; they should not accumulate panel-specific business logic.

This keeps the public dashboard stable while making internal development easier:

- Public API paths stay stable, especially existing `/runtime/...` routes.
- The default workspace keeps the same visible panel behavior unless a product change explicitly says otherwise.
- `App.tsx` should not gain new per-panel `useState`, `fetchRuntimeX`, or `setRuntimeX` wiring.
- Backend route files should not grow one route function per panel; runtime panel routes are registered from the backend manifest.
- A new panel should be reviewable as a small frontend module plus, when needed, a small backend runtime module.

The current design uses explicit registry lists rather than filesystem auto-discovery. That is deliberate: Vite and Python deployments are easier to reason about when imports are static, and failures are caught at build/test time. The price is one small registry addition on the frontend and one on the backend; the benefit is predictable public deployment behavior.

## Frontend

Each panel owns a module under `webpage/src/panels/modules/<panel-id>/` and exports `panel`.

Runtime panels declare `fetchData` and `refresh.tier`; the dashboard loads those through the generic runtime store instead of adding per-panel `useState` and refresh code in `App.tsx`.

Minimal runtime panel shape:

```ts
export const panel = runtimePanelFromRenderer(renderers, {
  id: 'example-panel',
  title: 'Example Panel',
  eyebrow: 'example',
  description: 'What this panel shows.',
  defaultEnabled: true,
}, {
  tier: 'slow',
  fetchData: () => fetchExamplePanel(),
});
```

For a new frontend panel:

1. Create `webpage/src/panels/modules/<panel-id>/index.ts`.
2. Put panel-specific UI in that directory when writing new UI. Compatibility wrappers may reuse existing grouped renderers during migration.
3. Add the module to `webpage/src/panels/modules/index.ts`.
4. Do not add panel-specific state or refresh code to `App.tsx`.

## Backend

Runtime API panels are registered in `scripts/api/runtime_panels/registry.py`. Each module under `scripts/api/runtime_panels/modules/` declares `PANEL_ID`, `ROUTE`, limit bounds, and `get_snapshot`.

The Flask route layer uses `scripts/api/routes/runtime_panels.py` to register all runtime routes while preserving existing API paths.

Minimal backend runtime module shape:

```py
PANEL_ID = "example-panel"
ROUTE = "/runtime/example/panel"
DEFAULT_LIMIT = 8
MIN_LIMIT = 1
MAX_LIMIT = 20


def get_snapshot(ctx: dict, *, limit: int = DEFAULT_LIMIT) -> dict:
    ...
```

For a new backend runtime panel:

1. Create `scripts/api/runtime_panels/modules/<panel_name>.py`.
2. Register that module in `scripts/api/runtime_panels/registry.py`.
3. Keep the existing public API path stable if replacing an old endpoint.
4. Do not add another one-off runtime route file unless it is not a panel API.

## Guardrails

- Build after frontend panel changes: `cd webpage && npm run build`.
- Run backend registry and affected runtime tests after backend changes.
- Keep old compatibility paths working during refactors.
- Avoid moving visual behavior and architecture in the same change; split UI redesign from module decomposition.
- If a panel needs shared helpers, put reusable code under `webpage/src/panels/shared/` or an appropriate backend service module instead of coupling unrelated panels together.
