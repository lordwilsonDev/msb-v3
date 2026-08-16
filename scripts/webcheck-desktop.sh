#!/usr/bin/env bash
set -euo pipefail

# webcheck-desktop -- one-command BROWSER verification of client-facing HTML
# deliverables on the Desktop.
#
# Renders each deliverable in a REAL browser (system Chrome driven by
# Playwright via ~/bin/webcheck.py -- no ego-browser, no headless hacks):
#   Botpress_Integration_Demo.html   BLACK SWAN LABZ pricing/service demo
#   julie 1.html                     client deliverable
#   Mixboard12345678.html            client deliverable (lives at ~/ root,
#                                    not on the Desktop)
#
# For each file: page is loaded from file://, visible text + screenshot
# written under artifacts/webcheck-desktop-<ts>/, and console errors / failed
# requests captured. Exit code is non-zero if any file fails to load or
# reports real page errors. Known-benign noise is whitelisted via --ignore:
# the GTM service-worker iframe (Gemini exports) and Mixboard's blob: origin
# restriction -- both are artifacts of opening web exports from disk, not
# defects in the deliverable. Add more via IGNORE_EXTRA.
#
# Missing files are reported as MISS (not silently skipped) so a renamed
# deliverable can't fall out of the gate.
#
# Usage:
#   make webcheck-desktop                 # Desktop + ~/ root
#   DESKTOP=/some/dir make webcheck-desktop   # other location
#   IGNORE_EXTRA="x.js,y.js" make webcheck-desktop  # extra ignores

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"
WEBCHECK="$HOME/bin/webcheck.py"
DESKTOP="${DESKTOP:-$HOME/Desktop}"

# Deliverables: "display name|path|resolved file". Files are looked up first
# on the Desktop, then the home root (Mixboard historically lives at ~/).
find_file() {
  local name="$1" f
  for dir in "$DESKTOP" "$HOME"; do
    f="$dir/$name"
    [ -f "$f" ] && { printf '%s' "$f"; return 0; }
  done
  return 1
}

OUT="$REPO/artifacts/webcheck-desktop-$(date +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"

pass=0
fail=0
miss=0

# Extra ignores via env (repeatable, comma-separated substrings, applied to
# every deliverable).
#   IGNORE_EXTRA="analytics.js,some-other.noise" make webcheck-desktop
EXTRA_IGNORES=()
if [ -n "${IGNORE_EXTRA:-}" ]; then
  IFS=',' read -r -a EXTRA_IGNORES <<< "$IGNORE_EXTRA"
fi
# Build the arg list once. Empty entries are DROPPED: a bare `--ignore=`
# matches every URL/message and would silently disable the whole gate.
# Length-guarded so an empty array never expands under `set -u` on stock
# macOS bash 3.2 (which errors on empty "${arr[@]}" expansion).
EXTRA_ARGS=()
if [ "${#EXTRA_IGNORES[@]}" -gt 0 ]; then
  for ig in "${EXTRA_IGNORES[@]}"; do
    [ -n "$ig" ] && EXTRA_ARGS+=(--ignore "$ig")
  done
fi

check() {
  local name="$1" url="$2"
  shift 2
  if "$PY" "$WEBCHECK" check "$url" "$@" --shot "$OUT/$name.png" \
      >"$OUT/$name.log" 2>&1; then
    echo "ok   $name"
    pass=$((pass + 1))
  else
    echo "FAIL $name"
    fail=$((fail + 1))
  fi
  sed -E 's/^(BODY|CONSOLE|SHOT|FAILED|LOAD)/     \1/' "$OUT/$name.log" | head -6
}

# name, filename, then per-deliverable --ignore substrings (known-benign
# noise specific to that file). IMPORTANT: only add ignores for embed/CDN
# artifacts that don't affect the deliverable's content. A file whose OWN
# assets are missing (e.g. a *_files/ sidecar dir that was never saved)
# must stay FAILING so it can't be shipped broken.
check_deliverable() {
  local name="$1" filename="$2" path
  shift 2
  if ! path=$(find_file "$filename"); then
    echo "MISS $name ($filename not on Desktop or ~/)"
    miss=$((miss + 1))
    return
  fi
  # file:// URLs with spaces must be %20-encoded for the browser.
  local url="file://${path// /%20}"
  if [ "${#EXTRA_ARGS[@]}" -gt 0 ]; then
    check "$name" "$url" "$@" "${EXTRA_ARGS[@]}"
  else
    check "$name" "$url" "$@"
  fi
}

# julie_1 is a Gemini chat export: the only failures are Google-hosted
# external assets that can't load from file:// -- the chat content itself
# renders fine, so ignore those hosts. NOTE: googleusercontent.com also
# serves real user content (chat avatars / generated images), so a broken
# content image on that host would be silently ignored too -- acceptable for
# this text-chat deliverable, but don't copy this ignore to image-heavy files.
check_deliverable botpress_demo "Botpress_Integration_Demo.html"
check_deliverable julie_1 "julie 1.html" \
  --ignore "googletagmanager.com/static/service_worker" \
  --ignore "fonts.gstatic.com" --ignore "www.gstatic.com" \
  --ignore "googleusercontent.com" --ignore "RotateCookies"
# mixboard deliberately gets NO ignores: Mixboard12345678.html references a
# Mixboard12345678_files/ sidecar that is missing (8 files incl. images) --
# a real defect that must fail the gate.
check_deliverable mixboard "Mixboard12345678.html"

echo "passed=$pass failed=$fail missing=$miss (artifacts: $OUT)"
[ "$fail" -eq 0 ] && [ "$miss" -eq 0 ]
