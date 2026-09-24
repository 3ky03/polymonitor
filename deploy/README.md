# Deployment Templates

This directory contains public deployment templates and operational examples.

Private deployment notes, machine-specific commands, and secrets should stay in
`document/`.

## Included templates

- `systemd/` - local long-running service templates for the API and sync jobs
- `nginx/` - reverse proxy examples for serving the dashboard and proxying API traffic

## Current recommendation

For local production-style runtime on one machine, use the `deploy/systemd/`
templates and keep PostgreSQL and Redis managed outside this repo.

For remote frontend hosting, prefer CI-built `webpage/dist` deployment to a
static directory such as `/var/www/polydata` instead of cloning the repo and
building on the server.

The private step-by-step setup for the current machine lives in
`document/deploy.md`.
