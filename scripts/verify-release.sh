#!/usr/bin/env bash
# verify-release.sh — prove a tagged release from a virgin checkout, end to end.
#
# Mirrors the v0.2.3 manual verification: fresh-clone the tag from the remote,
# confirm the checkout is virgin (no tracked .env / runtime/), seed the
# research-runtime fixtures, boot a run-scoped msb-v3 FROM THE CLONE
# (scripts/ci-runtime.sh — random port, torn down on exit, never borrows a
# hand-managed :8766), and run the `-m "not live"` suite against it — failing
# on any test failure AND on any seeded-artifact skip ("requires seeded"),
# which would mean the research-runtime seeding workstream regressed.
#
#   bash scripts/verify-release.sh [TAG]                # default: v<pyproject version>
#   VERIFY_PYTEST_M='not live and not slow' bash scripts/verify-release.sh  # override selection
#   VERIFY_REMOTE=git@... bash scripts/verify-release.sh  # override the remote
#   EXPECTED_PASS=814 bash scripts/verify-release.sh    # strict pass-count assertion
#   VERIFY_KEEP=1 bash scripts/verify-release.sh        # keep the clone for debugging
#   VERIFY_CLONE_DIR=/tmp/x bash scripts/verify-release.sh  # fixed clone dir
#
# Exit 0 = release verified from a virgin checkout. Non-zero otherwise.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

REMOTE="${VERIFY_REMOTE:-$(git -C "$REPO" remote get-url origin 2>/dev/null || true)}"
[ -n "$REMOTE" ] || { echo "[verify-release] FAIL: no origin remote (set VERIFY_REMOTE)" >&2; exit 1; }

# Default tag = v<pyproject version> — run this right after the release bump.
VERSION="$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$REPO/pyproject.toml" | head -1)"
[ -n "$VERSION" ] || { echo "[verify-release] FAIL: could not read version from pyproject.toml" >&2; exit 1; }
TAG="${1:-${VERIFY_TAG:-v$VERSION}}"

# Only auto-remove a dir we created (mktemp). A user-supplied VERIFY_CLONE_DIR
# is never rm -rf'd — a typo pointing at a meaningful directory can't be
# destroyed (same rule as the portability gate's PORTABILITY_DEST).
CLONE_AUTO=0
if [ -z "${VERIFY_CLONE_DIR:-}" ]; then
  CLONE_DIR="$(mktemp -d /tmp/msb-verify-XXXX)"
  CLONE_AUTO=1
else
  CLONE_DIR="$VERIFY_CLONE_DIR"
fi
KEEP="${VERIFY_KEEP:-0}"

cleanup() {
  if [ "$KEEP" = "1" ]; then
    echo "[verify-release] kept clone at $CLONE_DIR"
  elif [ "$CLONE_AUTO" = "1" ]; then
    rm -rf "$CLONE_DIR"
    echo "[verify-release] removed temp clone"
  else
    echo "[verify-release] left user-supplied dir in place: $CLONE_DIR"
  fi
}
trap cleanup EXIT

echo "[verify-release] verifying tag $TAG from $REMOTE"

# Fail fast: the tag must exist on the remote (a local-only tag fails here,
# which is correct — a release that can't be fetched isn't a release).
if ! git ls-remote --tags "$REMOTE" "$TAG" | grep -q "refs/tags/$TAG"; then
  echo "[verify-release] FAIL: tag $TAG not found on $REMOTE (was it pushed?)" >&2
  exit 1
fi

git clone -q --depth 1 --branch "$TAG" --single-branch "$REMOTE" "$CLONE_DIR" \
  || { echo "[verify-release] FAIL: clone of $TAG failed" >&2; exit 1; }

# Virginality: a tracked .env or runtime/ means the repo leaked machine state
# (both are gitignored, so a clean repo never ships them).
if [ -f "$CLONE_DIR/.env" ]; then
  echo "[verify-release] FAIL: clone contains a tracked .env — secrets leak!" >&2
  exit 1
fi
if [ -d "$CLONE_DIR/runtime" ]; then
  echo "[verify-release] FAIL: clone contains tracked runtime/ — machine state leaked into the repo" >&2
  exit 1
fi
echo "[verify-release] virgin checkout confirmed (no .env, no runtime/)"

# Fast fail before the long suite: the version sources must agree in the
# clone (the v0.2.1 identity-drift incident).
if ! (cd "$CLONE_DIR" && "$PY" -m pytest tests/test_release_versions.py -q >/dev/null 2>&1); then
  echo "[verify-release] FAIL: version sources disagree in $TAG" >&2
  exit 1
fi
echo "[verify-release] version sources agree ($VERSION)"

# Seed the research-runtime fixtures, exactly as CI / the portability gate do
# before booting/judging a server. Seeds into $CLONE_DIR/runtime/research/.
if ! (cd "$CLONE_DIR" && bash scripts/seed-research-runtime.sh); then
  echo "[verify-release] FAIL: research-runtime seeding failed in the clone" >&2
  exit 1
fi

# --- Option A (PRODUCTION-CLOSURE-001 P1): the release gate owns its runtime.
# Boot a run-scoped msb-v3 FROM THE CLONE on a random port — no dependency on a
# hand-managed :8766 or on vars.MSB_BASE_URL. ci-runtime.sh only ever signals
# the PID it started; the temp dir is torn down on exit.
cd "$CLONE_DIR"
export PYTHONPATH="$CLONE_DIR/src"
export CI_SERVER_PYTHON="$PY"
# shellcheck source=/dev/null
source "$CLONE_DIR/scripts/ci-runtime.sh"
ci_runtime_init
# Point the scoped server at the seeded research root (ci_runtime_init defaults
# it to an empty temp dir; the fixtures live under the clone).
CI_SERVER_RESEARCH="$CLONE_DIR/runtime/research"
export CI_SERVER_RESEARCH
mkdir -p "$CI_SERVER_RESEARCH"
if ! ci_runtime_start_server; then
  echo "[verify-release] FAIL: scoped msb-v3 did not become healthy" >&2
  exit 1
fi
# ci_runtime_start_server installs its own EXIT trap — compose it with ours so
# both the scoped server AND the temp clone are cleaned up.
trap 'ci_runtime_cleanup; cleanup' EXIT
echo "[verify-release] scoped server healthy at $MSB_BASE_URL"

# Suite from the clone against the scoped server. Default selection excludes the
# `live` tier (real model generation — not a release gate; measured elsewhere).
# Override with VERIFY_PYTEST_M.
# -rfE: name failures AND errors in the short summary, so _emit_failures can
# surface them into the workflow log (scripts/test.sh defaults to -rs = skips
# only, which hid the real failing tests through v0.3.2 / v0.4.0 / v0.4.1).
PYTEST_SELECT=( -m "${VERIFY_PYTEST_M:-not live}" -rfE )
LOG="$(mktemp /tmp/msb-verify-suite-XXXXXX).log"
echo "[verify-release] running the suite (${PYTEST_SELECT[*]}) from the clone; log: $LOG ..."
set +e
(cd "$CLONE_DIR" && MSB_HOME="$CLONE_DIR" MSB_REPO="$CLONE_DIR" MSB_BASE_URL="$MSB_BASE_URL" \
  bash scripts/test.sh "${PYTEST_SELECT[@]}") >"$LOG" 2>&1
SUITE_RC=$?
set -e

SUMMARY="$(grep -E '^[0-9]+ (passed|failed)' "$LOG" | tail -1 || true)"
echo "[verify-release] suite summary: ${SUMMARY:-<no summary found>}"

# Surface the failing test IDs into THIS log. The full pytest output only
# exists in $LOG on the runner's disk; without this, a red release-verify in
# GitHub shows a count and nothing else (the 2026-08 debugging trap).
_emit_failures() {
  echo "[verify-release] failing tests (from $LOG):" >&2
  {
    grep -E '^(FAILED|ERROR) ' "$LOG" \
      || grep -E '^_{5,} .+ _{5,}$' "$LOG" \
      || grep -A60 'short test summary info' "$LOG" \
      || tail -60 "$LOG" \
      || true
  } | sed 's/^/[verify-release]   /' >&2
}

[ "$SUITE_RC" -eq 0 ] || { echo "[verify-release] FAIL: suite exited non-zero ($SUITE_RC) — see $LOG" >&2; _emit_failures; exit 1; }
[ -n "$SUMMARY" ] || { echo "[verify-release] FAIL: no suite summary line found — see $LOG" >&2; exit 1; }
case "$SUMMARY" in
  *failed*) echo "[verify-release] FAIL: test failures in the virgin-clone run — see $LOG" >&2; _emit_failures; exit 1 ;;
esac
if grep -q 'requires seeded' "$LOG"; then
  echo "[verify-release] FAIL: a seeded-artifact test SKIPPED — research-runtime seeding regressed:" >&2
  grep 'requires seeded' "$LOG" | head -3 >&2
  exit 1
fi

# Optional strict pass-count assertion (EXPECTED_PASS=<n>).
if [ -n "${EXPECTED_PASS:-}" ]; then
  PASSED="$(printf '%s' "$SUMMARY" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | head -1 || true)"
  if [ "$PASSED" != "$EXPECTED_PASS" ]; then
    echo "[verify-release] FAIL: expected $EXPECTED_PASS passed, got ${PASSED:-?} — see $LOG" >&2
    exit 1
  fi
  echo "[verify-release] pass count matches EXPECTED_PASS=$EXPECTED_PASS"
fi

echo "[verify-release] PASS: $TAG verified from a virgin checkout"
rm -f "$LOG"
