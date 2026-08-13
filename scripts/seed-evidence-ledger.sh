#!/usr/bin/env bash
# seed-evidence-ledger.sh — materialize committed evidence-ledger fixtures
# into a checkout's runtime/ so the research harness suite runs from a fresh
# checkout instead of skipping on missing machine state.
#
# Slug-agnostic: every `tests/fixtures/evidence_ledgers/<slug>_evidence_ledger.json`
# is seeded into `runtime/research/<slug>/<slug>_evidence_ledger.json`. Adding
# a seeded slug is a data-only change (drop a fixture JSON) — no script edit,
# no wiring change.
#
# runtime/ is gitignored machine state (produced by real research runs), so a
# fresh checkout or a portability staging copy has no ledgers and
# tests/test_harness.py::test_research_assistant_claims_review skips. The
# server reads the same repo-relative path (MSB_RESEARCH_ROOT default), so
# seeding BEFORE the server boots makes both the test's skip check and the
# /claims/review 200 path work everywhere: ubuntu CI, the factory gate, and
# the portability foreign copy.
#
#   bash scripts/seed-evidence-ledger.sh [ROOT]        # ROOT defaults to repo root
#   MSB_LEDGER_FIXTURES=/x bash scripts/... [ROOT]     # override fixture source (tests)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${1:-$REPO}"
FIXTURES_DIR="${MSB_LEDGER_FIXTURES:-$REPO/tests/fixtures/evidence_ledgers}"

# No-op guard: a seeder that silently seeds nothing is worse than none — it
# would let the claims-review test skip again without a trace in the gate
# (same failure mode as the 2026-08 paths-filter@v3 dead-output bug).
[ -d "$FIXTURES_DIR" ] || { echo "[seed-ledger] FAIL: fixtures dir missing: $FIXTURES_DIR" >&2; exit 1; }
FIXTURE_COUNT=$(find "$FIXTURES_DIR" -maxdepth 1 -name '*_evidence_ledger.json' | wc -l | tr -d ' ')
[ "$FIXTURE_COUNT" -gt 0 ] || { echo "[seed-ledger] FAIL: no evidence-ledger fixtures in $FIXTURES_DIR" >&2; exit 1; }

for fixture in "$FIXTURES_DIR"/*_evidence_ledger.json; do
  [ -e "$fixture" ] || continue
  slug="$(basename "$fixture" _evidence_ledger.json)"
  [ -n "$slug" ] || continue   # guard a pathological bare `_evidence_ledger.json`
  dest_dir="$ROOT/runtime/research/$slug"
  dest="$dest_dir/${slug}_evidence_ledger.json"
  mkdir -p "$dest_dir"
  # Never clobber: an existing ledger is REAL machine state (produced by actual
  # research runs) — a bare invocation with ROOT defaulting to the repo root
  # must not wipe it. Every wired path seeds into a fresh checkout or an empty
  # staging copy, so this guard breaks nothing.
  if [ -f "$dest" ]; then
    echo "[seed-ledger] present, not clobbering: $dest"
    continue
  fi
  cp "$fixture" "$dest"
  echo "[seed-ledger] seeded $dest"
done
