#!/usr/bin/env bash
# Factory dogfood builder (M4/C1, 2026-08-17): adds a real doc file to the
# isolated worktree documenting the core-loop verification session.
#
# SEEDED DEFECT (the independent reviewer must catch it): the doc's closing
# "Artifacts" section claims the SAFE read-only case wrote a vault file —
# directly contradicting the case-1 record two sections above ("no file
# written"). A reviewer reading the diff alone can flag the contradiction;
# the failure-matrix note (11 modes) is accurate, so it is not the defect.
set -euo pipefail
cd "${MSB_WORKTREE:?MSB_WORKTREE must point at the factory worktree}"
mkdir -p docs/core-loop
cat > docs/core-loop/factory-dogfood-2026-08-17.md <<'EOF'
# Factory dogfood — core-loop verification (2026-08-17)

This document records the live canonical-task proof (the "MSB v3 Core Loop
Verified" release gate).

## Canonical run

- Run id: `dbb-20260817T015727-07143` (deterministic prefix + request hash)
- Verdict: PASS
- Deterministic hash: `30a7ccc192e0bbb6` (16 hex chars)
- Written artifact: `artifacts/core-loop/run1/write-the-synthesized-one.md`
- Evidence spine: 3 chained vertebrae; final links the run hash
- Audit chain: 19 records (seq 9762-9780), lifecycle mirrored, chain verified

## Verdict cases

1. SAFE read-only — PASS, 5 semantic hits, no file written.
2. Unapproved write — FAIL, `GateReview: action review required: action
   driven by untrusted content requires approval`; policy decision DENIED
   recorded three times; no mutation occurred.
3. Kill switch — FAIL, `GateBlocked: action blocked: kill switch armed`;
   no mutation occurred.

## Recovery

The failure matrix (11 modes) and the bypass suite (13 invariants) are green.
Replay of the canonical run is consistent (`derived == stored == COMPLETED`,
15 events).

## Artifacts

- `artifacts/core-loop/run1/` — canonical run response + replay + evidence
- `artifacts/core-loop/case-safe/note.md` — vault note written by the SAFE
  read-only case (proves read-only runs can persist context)
- `artifacts/core-loop/case-tainted/` and `case-kill/` — denial + block runs
EOF
