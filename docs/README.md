# polyData Public Docs

This directory contains public-facing engineering documentation for polyData.

Private notes, credentials, deployment journals, and non-public research should stay in `document/`, which is intentionally ignored by Git.

## Documents

- `architecture.md` - current architecture and target boundaries.
- `development.md` - local development, build, and verification commands.
- `updates.md` - daily public progress log and reusable update template.
- `../deploy/README.md` - deployment templates for local `systemd` runtime and remote frontend hosting.

## Documentation Rules

- Keep docs free of secrets, private infrastructure details, and local-only credentials.
- Prefer stable architecture and workflow information over one-off notes.
- When implementation boundaries change, update the relevant public doc in the same change.
