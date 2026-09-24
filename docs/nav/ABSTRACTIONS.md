# Learned routes — msb-v3

Distilled by `nav.py distill` from real sessions. Every `route:` and `gate:` line is re-validated by `nav.py check`, so this file fails loudly when the system moves under it.

## Add or swap a model provider

- date: 2026-09-22
- trigger: add or swap a model provider
- system: msb-v3
- id: add-or-swap-a-model-provider
- route: src/msb_v3/agent/providers.py -> src/msb_v3/core/config.py -> src/msb_v3/core/identity.py -> tests/contracts/test_provider_plugins.py -> tests/contracts/test_interchangeability.py
- gate: make lint
- proof: 5 path(s) + 1 gate target(s) validated 2026-09-22 by nav.py distill; re-validated by nav.py check
- findings: none

## Make a documentation claim checkable by a gate

- date: 2026-09-22
- trigger: make a documentation claim checkable by a gate
- system: msb-v3
- id: make-a-documentation-claim-checkable-by-a-gate
- route: scripts/doc_records.py -> tests/docs/test_doc_records.py -> Makefile -> docs/what-msb-v3-is.md
- gate: make lint
- proof: 4 path(s) + 1 gate target(s) validated 2026-09-22 by nav.py distill; re-validated by nav.py check
- findings: none

## Cut and verify a release

- date: 2026-09-22
- trigger: cut and verify a release
- system: msb-v3
- id: cut-and-verify-a-release
- route: scripts/verify-release.sh -> src/msb_v3/core/identity.py
- gate: make verify-release, make lint
- proof: 2 path(s) + 2 gate target(s) validated 2026-09-22 by nav.py distill; re-validated by nav.py check
- findings: none

## Regenerate the blueprint citation index after editing docs

- date: 2026-09-22
- trigger: regenerate the blueprint citation index after editing docs
- system: msb-v3
- id: regenerate-the-blueprint-citation-index-after-editing-docs
- route: scripts/blueprint_index.py -> docs/blueprint-index.md -> tests/docs/test_blueprint_index.py
- gate: make lint
- proof: 3 path(s) + 1 gate target(s) validated 2026-09-22 by nav.py distill; re-validated by nav.py check
- findings: none

## Diagnose a red pre-push gate

- date: 2026-09-22
- trigger: diagnose a red pre-push gate
- system: msb-v3
- id: diagnose-a-red-pre-push-gate
- route: scripts/portability-check.sh -> tests/test_ci_runtime.py -> tests/test_release_versions.py
- gate: make portability
- proof: 3 path(s) + 1 gate target(s) validated 2026-09-22 by nav.py distill; re-validated by nav.py check
- findings: none
