#!/usr/bin/env bash
set -euo pipefail

# Weekly snapshot of the msb-v3 code into Wilson's Obsidian vault:
#   ~/Documents/Vault/Backups/msb-v3-<CODE>/
# Each run generates a FRESH backup code (11 uppercase alphanumerics, same
# shape as the manual fasfa-HEA71PXTGSX3 backup), copies the working tree
# (runtime state, secrets, and caches excluded; git history recorded in the
# manifest instead), writes a BACKUP-MANIFEST.md, verifies the snapshot is
# structurally complete AND restorable (full test suite from an extracted
# copy), then prunes old snapshots past MSB_BACKUP_KEEP.
#
# Driven weekly by the LaunchAgent com.lordwilson.msb-vault-backup
# (template: scripts/launchd/com.lordwilson.msb-vault-backup.plist).
#
# Overrides: MSB_VAULT, MSB_BACKUP_LABEL, MSB_BACKUP_KEEP,
#            MSB_BACKUP_VERIFY (0 disables restore verification),
#            MSB_PYTHON (interpreter for the restore-verify test run)

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VAULT="${MSB_VAULT:-$HOME/Documents/Vault}"
BACKUPS_DIR="$VAULT/Backups"
LABEL="${MSB_BACKUP_LABEL:-msb-v3}"
KEEP="${MSB_BACKUP_KEEP:-8}"
VERIFY="${MSB_BACKUP_VERIFY:-1}"
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"
LOG="$REPO/logs/vault-backup.log"

mkdir -p "$BACKUPS_DIR" "$REPO/logs"

log() { echo "[vault-backup] $(date '+%F %T') $*" | tee -a "$LOG"; }

# Fresh backup code each run (never reuse): 11 uppercase alphanumerics.
# head exits after 11 bytes, SIGPIPE-ing tr -- wrap the pipeline so
# pipefail doesn't kill the script (exit 141).
CODE="$( { LC_ALL=C tr -dc 'A-Z0-9' < /dev/urandom | head -c 11; } 2>/dev/null || true )"
DEST="$BACKUPS_DIR/$LABEL-$CODE"

if [ -e "$DEST" ]; then
  log "ERROR: $DEST already exists (code collision) -- aborting"
  exit 1
fi

# Same excludes as the manual fasfa-HEA71PXTGSX3 backup: no .env secrets,
# no runtime state (storage/data/logs/var/runtime/snapshots/artifacts), no
# build cache (apps/iphone/.build), no Python caches. .git is excluded too:
# the snapshot records the source commit in its manifest instead (a nested
# repo inside the vault's own git repo would only be committed as a gitlink).
#
# NOTE: the runtime-state excludes are ANCHORED with a leading slash (repo
# root only). Unanchored 'runtime/' silently swallowed src/msb_v3/runtime/
# (the agent runtime store module) and broke restored checkouts with
# ModuleNotFoundError: msb_v3.runtime — caught by the restore test.
rsync -a \
  --exclude='.git/' \
  --exclude='.env' \
  --exclude='/storage/' \
  --exclude='/data/' \
  --exclude='/logs/' \
  --exclude='/var/' \
  --exclude='/runtime/' \
  --exclude='/snapshots/' \
  --exclude='/artifacts/' \
  --exclude='/.artifacts/' \
  --exclude='apps/iphone/.build/' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.coverage' \
  --exclude='.venv/' \
  --exclude='*.pyc' \
  "$REPO/" "$DEST/"

HEAD="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  echo "# Code Backup — $LABEL · $CODE"
  echo
  echo "- **Backup code:** $CODE"
  echo "- **Label:** $LABEL"
  echo "- **Source:** $REPO"
  echo "- **Backed up:** $(date '+%Y-%m-%d')"
  echo "- **Source git HEAD:** \`$HEAD\`"
  echo
  echo "Working-tree snapshot (source, tests, docs, scripts, config, apps)."
  echo "Excluded: \`.git/\` (history; HEAD recorded above), \`.env\` (secrets),"
  echo "\`storage/\`, \`data/\`, \`logs/\`, \`var/\`, \`runtime/\`, \`snapshots/\`,"
  echo "\`artifacts/\`, \`apps/iphone/.build/\`, Python caches. Exclude list"
  echo "lives in \`scripts/vault-backup.sh\`."
} > "$DEST/BACKUP-MANIFEST.md"

# Post-backup integrity check: every snapshot must be structurally complete
# before it is declared good. The critical guard is src/msb_v3/runtime — an
# unanchored rsync exclude once silently dropped it and the damage only
# surfaced on restore (ModuleNotFoundError: msb_v3.runtime). A snapshot that
# fails is deleted on the spot and the run exits non-zero so the failure is
# loud, never a quiet broken backup.
REQUIRED_PATHS=(
  "src/msb_v3/runtime"     # agent runtime store module (regression guard)
  "src/msb_v3/api/app.py"  # API entrypoint import chain
  "BACKUP-MANIFEST.md"     # manifest written alongside the snapshot
)

verify_snapshot() {
  local rel missing=()
  for rel in "${REQUIRED_PATHS[@]}"; do
    [ -e "$DEST/$rel" ] || missing+=("$rel")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    log "ERROR: snapshot $DEST failed integrity check — missing: ${missing[*]}"
    rm -rf "$DEST"
    log "ERROR: removed invalid snapshot $DEST; backup aborted"
    return 1
  fi
  log "integrity OK: ${#REQUIRED_PATHS[@]} required paths present in $DEST"
}

verify_snapshot || exit 1

# Restore verification: extract the snapshot to a temp dir and run the full
# test suite from it — the definitive proof the backup is restorable. The
# unanchored-exclude bug only surfaced when a restored copy was imported, so
# structure checks alone are not enough. A failing restore deletes the
# snapshot and aborts the run (never ship a backup that can't come back).
# Skip with MSB_BACKUP_VERIFY=0 (e.g. a quick manual snapshot).
verify_restore() {
  local tmp out rc
  tmp="$(mktemp -d /tmp/msb-v3-restore-XXXXXX)" || {
    log "ERROR: restore check: could not create temp dir"; return 1; }
  out="$tmp/pytest.out"
  cp -a "$DEST/." "$tmp/" 2>/dev/null || {
    log "ERROR: restore check: could not extract $DEST"; rm -rf "$tmp"; return 1; }
  # launchd runs with a minimal PATH (/usr/bin:/bin:...), so subprocesses
  # that resolve `python3` (the fake-secenclave tool's shebang, the CLI
  # receipt roundtrip) would hit the system Python and miss cryptography /
  # msb_ledger. Prepend the interpreter's own bin dir so the verify run
  # sees the same environment the suite is meant to run in.
  if (cd "$tmp" && MSB_HOME="$tmp" PATH="$(dirname "$PY"):$PATH" "$PY" -m pytest -q tests/ >"$out" 2>&1); then
    rc=0
  else
    rc=1
    log "ERROR: restore check FAILED for $DEST — test suite not green from restored copy; tail:"
    tail -6 "$out" | while IFS= read -r l; do log "  $l"; done
  fi
  rm -rf "$tmp"
  return "$rc"
}

if [ "$VERIFY" = "1" ]; then
  verify_restore || {
    rm -rf "$DEST"
    log "ERROR: removed invalid snapshot $DEST; backup aborted"
    exit 1
  }
  log "restore OK: test suite green from restored copy of $DEST"
fi

log "backup complete: $DEST (code $CODE, HEAD $HEAD, size $(du -sh "$DEST" | cut -f1))"

# Retention: keep the newest KEEP snapshots, prune the rest. (The manual
# fasfa-* backup has a different label and is never touched.)
#
# The prune CANNOT enumerate $BACKUPS_DIR: on this machine a launchd-spawned
# process gets EPERM on readdir of the vault's Backups folder (macOS TCC
# protects ~/Documents), so ls/glob/find there fail. Direct-path ops (read,
# append, rename, rm of a known path) DO work, so the index file below is the
# source of truth: every run appends its DEST, drops entries whose dir is
# gone, prunes the oldest past KEEP, and rewrites the index. Unindexed
# snapshots are never pruned (fail-safe: keep rather than delete).
# The whole step is best-effort: a retention failure must never fail the
# backup itself.
INDEX="$BACKUPS_DIR/.backup-index"

prune_retention() {
  [ -f "$INDEX" ] || : > "$INDEX"
  local -a entries=()
  local line path i total
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    path="${line%%|*}"
    [ -d "$path" ] || continue   # drop stale entries for deleted snapshots
    entries+=("$line")
  done < "$INDEX"
  entries+=("$DEST|$(date '+%F %T')")
  total=${#entries[@]}
  for ((i = 0; i < total - KEEP; i++)); do
    old="${entries[$i]%%|*}"
    log "pruning $old"
    rm -rf "$old" || log "WARN: could not prune $old"
  done
  if [ "$total" -gt "$KEEP" ]; then
    printf '%s\n' "${entries[@]:$((total - KEEP))}" > "$INDEX.tmp"
  else
    printf '%s\n' "${entries[@]}" > "$INDEX.tmp"
  fi
  mv "$INDEX.tmp" "$INDEX"
}

prune_retention || log "WARN: retention step failed"
