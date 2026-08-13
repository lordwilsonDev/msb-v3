#!/usr/bin/env bash
# seed-research-runtime.sh — materialize committed research-runtime fixtures
# into a checkout's runtime/ so the research harness suite runs from a fresh
# checkout instead of skipping on missing machine state.
#
# Slug-agnostic: every `tests/fixtures/research_runtime/<slug>/` dir is copied
# into `runtime/research/<slug>/` (per-file, never clobbering existing files).
# A fixture dir can hold any runtime artifact the harness/API consumes — e.g.
# `<slug>_evidence_ledger.json` (claims review) or `STATUS.json` + `.bak`
# (ralph-loop status). Adding a seeded slug is a data-only change: drop a
# fixture dir, no script edit, no wiring change.
#
# runtime/ is gitignored machine state (produced by real research runs), so a
# fresh checkout or a portability staging copy has no artifacts and the
# harness tests skip. The server reads the same repo-relative paths
# (MSB_RESEARCH_ROOT default), so seeding BEFORE the server boots makes both
# the tests' skip checks and the served artifacts work everywhere: ubuntu CI,
# the factory gate, and the portability foreign copy.
#
#   bash scripts/seed-research-runtime.sh [ROOT]        # ROOT defaults to repo root
#   MSB_RESEARCH_FIXTURES=/x bash scripts/... [ROOT]    # override fixture source (tests)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${1:-$REPO}"
FIXTURES_ROOT="${MSB_RESEARCH_FIXTURES:-$REPO/tests/fixtures/research_runtime}"

# No-op guard: a seeder that silently seeds nothing is worse than none — it
# would let the harness tests skip again without a trace in the gate (same
# failure mode as the 2026-08 paths-filter@v3 dead-output bug).
[ -d "$FIXTURES_ROOT" ] || { echo "[seed-runtime] FAIL: fixtures root missing: $FIXTURES_ROOT" >&2; exit 1; }
SLUG_COUNT=$(find "$FIXTURES_ROOT" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
[ "$SLUG_COUNT" -gt 0 ] || { echo "[seed-runtime] FAIL: no fixture slug dirs in $FIXTURES_ROOT" >&2; exit 1; }

for src_dir in "$FIXTURES_ROOT"/*/; do
  [ -d "$src_dir" ] || continue
  slug="$(basename "$src_dir")"
  [ -n "$slug" ] || continue
  dest_dir="$ROOT/runtime/research/$slug"
  mkdir -p "$dest_dir"
  for fixture in "$src_dir"*; do
    [ -f "$fixture" ] || continue
    name="$(basename "$fixture")"
    # Never clobber: an existing file is REAL machine state (produced by
    # actual research runs) — a bare invocation with ROOT defaulting to the
    # repo root must not wipe it. Every wired path seeds into a fresh
    # checkout or an empty staging copy, so this guard breaks nothing.
    if [ -f "$dest_dir/$name" ]; then
      echo "[seed-runtime] present, not clobbering: $dest_dir/$name"
      continue
    fi
    cp "$fixture" "$dest_dir/$name"
    echo "[seed-runtime] seeded $dest_dir/$name"
  done
done
