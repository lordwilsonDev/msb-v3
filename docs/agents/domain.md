# Domain Docs

Single-context layout — one `CONTEXT.md` and one `docs/adr/` directory at the repo root.

## Files

- `CONTEXT.md` — shared language / glossary for this repo. Keep it concise; one term, one definition. Update it inline when the domain model changes.
- `docs/adr/` — Architecture Decision Records. One file per decision. Name files with a date prefix: `YYYY-MM-DD-<short-slug>.md`.

## Rules

- Skills that touch domain concepts (`grill-with-docs`, `domain-modeling`, `codebase-design`, `to-spec`) will read and write these files.
- Don't put ADRs under feature folders; keep them co-located in `docs/adr/` so agents can find them.
- When in doubt, update `CONTEXT.md` before refactoring code — shared terminology prevents misalignment.
