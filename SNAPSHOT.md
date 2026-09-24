# Production Source Snapshot

Captured on 2026-09-24. This is an independent source publication, not a fork
of the development repository's commit history.

## Included Source

- Backend, agents, runtime collectors, configuration templates, deployment
  templates, and available tests were copied from the GCP application's
  working files, including deployed hotfixes that were not committed there.
- The frontend was recovered from the source revision identified by the
  running site's build ID. The older frontend checkout beside the backend
  was not used as a substitute for the deployed frontend.
- Website repository links point to `https://github.com/3ky03/polymonitor`.
  A personal development path in a setup example was replaced with a generic
  path. Links to the identity-bearing publication are omitted from this copy.
  Third-party license and attribution notices are preserved.

## Excluded Material

No original development history, personal commit identities, environment
secrets, host credentials, database contents, runtime state, logs, local audit
artifacts, private notes, installed dependencies, or compiled frontend assets
are published. Production GitHub Actions deployment configuration is not
included. Public configuration examples are templates, not working credentials.

## Running the Snapshot

Use the pinned Node version in `.nvmrc`. For the frontend, run `npm ci` and
`npm run build` in `webpage/`. For the Python API, consult
[`docs/development.md`](docs/development.md), the dependency lockfile in
`scripts/requirements.lock.txt`, and the environment templates.

The application requires separately provisioned databases, upstream data
services, and credentials. This repository is not a database export or a
self-contained offline demo. Backend and frontend source originate from the
same running deployment but need not share one historical Git revision.

## Privacy Boundary

The publication omits the original Git history and uses a pseudonymous commit
identity. It does not promise that previously public project names, website
content, papers, or independently archived material cannot be correlated.
