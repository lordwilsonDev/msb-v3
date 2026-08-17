#!/usr/bin/env bash
# Factory dogfood builder (M4/C1, 2026-08-17) — CLEAN variant: adds a real
# doc file to the isolated worktree with NO seeded contradiction, so the
# docs-only change can reach a MERGED verdict end-to-end.
set -euo pipefail
cd "${MSB_WORKTREE:?MSB_WORKTREE must point at the factory worktree}"
mkdir -p docs/core-loop
cat > docs/core-loop/factory-dogfood-clean.md <<'EOF'
# Factory dogfood — clean docs-only change (2026-08-17)

This document records the docs-only merge proof: a change that touches only
documentation must skip the full test suite (classified skip, recorded with
a reason) and reach MERGED without running — and timing out on — tests it
cannot affect.

## Contents

- Canonical run: `dbb-20260817T015727-07143`, verdict PASS.
- Verdict cases: read-only PASS, unapproved write FAIL, kill switch FAIL.
- Recovery: failure matrix (11 modes) and bypass suite (13 invariants) green.

## Note

A docs-only change cannot break code, so the full suite is not required by
policy. The skip is recorded in the evidence chain with the reason — it is
NOT the honest UNVERIFIED of a change that should have run tests and did not.
EOF
