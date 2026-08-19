#!/usr/bin/env bash
set -euo pipefail

# cache-trim.sh — weekly trim of the regenerable caches that refill the disk
# fastest.
#
# On this machine the measured daily growth is ~1G/day, dominated by caches
# (Google/Chrome, pnpm store, ollama blobs, hermit, Citro Labs, SiriTTS).
# All six are safe to clear — regenerable on demand, and the same set the
# manual disk cleanup removes. Each dir is skipped unless it is >= MIN_MB
# (default 10M) so tiny caches are left alone; pnpm store prune drops only
# orphaned packages (never referenced ones, so verify installs stay fast).
# Every action is logged.
#
# Driven weekly (Sun 06:40) by com.lordwilson.cache-trim — deliberately the
# slot right before the disk-health check (06:45) so that check measures
# post-trim usage instead of alerting on the week's cache accumulation.
#
# Overrides (testing): MSB_CACHE_DIRS (space-separated), MSB_CACHE_MIN_MB,
# MSB_CACHE_LOG.

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${MSB_CACHE_LOG:-$REPO/logs/cache-trim.log}"
MIN_MB="${MSB_CACHE_MIN_MB:-10}"
mkdir -p "$(dirname "$LOG")"
log() { echo "[cache-trim] $(date '+%F %T') $*" | tee -a "$LOG"; }

DIRS=(
  "$HOME/Library/Caches/Google"
  "$HOME/Library/Caches/hermit"
  "$HOME/Library/Caches/Citro Labs"
  "$HOME/Library/Caches/SiriTTS"
  "$HOME/Library/Caches/pnpm"
  "$HOME/Library/Caches/ollama"
)
if [ -n "${MSB_CACHE_DIRS:-}" ]; then
  DIRS=()
  read -ra DIRS <<< "$MSB_CACHE_DIRS"
fi

trimmed=0
for d in "${DIRS[@]}"; do
  [ -d "$d" ] || continue
  size_mb="$(( $(du -sk "$d" 2>/dev/null | cut -f1) / 1024 ))"
  [ "${size_mb:-0}" -ge "$MIN_MB" ] || continue
  # some caches (hermit) are root-owned/read-only — make sure we can clear
  chmod -R u+w "$d" 2>/dev/null || true
  rm -rf "$d"/* "$d"/.[!.]* 2>/dev/null || true
  trimmed=$((trimmed + 1))
  log "trimmed $d (${size_mb}M)"
done

# Orphaned packages only — referenced deps are kept, so the dsh/msb verify
# installs in /tmp stay fast. Never fails the run (a store hiccup is not a
# cache-trim failure worth alerting on).
if command -v pnpm >/dev/null 2>&1; then
  if pnpm store prune >/dev/null 2>&1; then
    log "pnpm store pruned (orphans only)"
  else
    log "WARN: pnpm store prune failed"
  fi
fi

[ "$trimmed" -eq 0 ] && log "nothing to trim (all under ${MIN_MB}M)"
log "done ($trimmed dirs trimmed)"
