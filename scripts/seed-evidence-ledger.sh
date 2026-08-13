#!/usr/bin/env bash
# seed-evidence-ledger.sh — materialize the committed evidence-ledger fixture
# into a checkout's runtime/ so the claims-review test runs instead of skipping.
#
# tests/test_harness.py::test_research_assistant_claims_review requires
# runtime/research/sovereign-ai-orchestration/sovereign-ai-orchestration_evidence_ledger.json.
# runtime/ is gitignored machine state (produced by real research runs), so a
# fresh checkout or a portability staging copy has no ledger and the test
# skips. The server reads that same repo-relative path (MSB_RESEARCH_ROOT
# default), so seeding BEFORE the server boots makes both the test's skip
# check and the /claims/review 200 path work everywhere: ubuntu CI, the
# factory gate, and the portability foreign copy.
#
# The fixture mirrors the exact shape the live machine produces (empty
# evidence/claims — claims/review returns 200 with updated=0).
#
#   bash scripts/seed-evidence-ledger.sh [ROOT]   # ROOT defaults to repo root
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${1:-$REPO}"
FIXTURE="$REPO/tests/fixtures/evidence_ledgers/sovereign-ai-orchestration_evidence_ledger.json"
DEST_DIR="$ROOT/runtime/research/sovereign-ai-orchestration"
LEDGER="sovereign-ai-orchestration_evidence_ledger.json"

[ -f "$FIXTURE" ] || { echo "[seed-ledger] FAIL: fixture missing: $FIXTURE" >&2; exit 1; }
mkdir -p "$DEST_DIR"
cp "$FIXTURE" "$DEST_DIR/$LEDGER"
echo "[seed-ledger] seeded $DEST_DIR/$LEDGER"
