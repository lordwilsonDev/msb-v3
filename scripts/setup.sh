#!/usr/bin/env bash
# Reproducible rebuild from a fresh clone (host path — macOS + launchd).
# Stands the whole stack up: python deps, launchd agents (msb-v3, qdrant,
# backup), models, then a /health smoke. Idempotent — safe to re-run.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

[ -f MANIFEST.md ] || {
  echo "[setup] ERROR: MANIFEST.md not found — this does not look like the msb-v3 repo root" >&2
  exit 1
}

PY="${PY:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"
[ -x "$PY" ] || {
  echo "[setup] ERROR: python not found at $PY (set PY=/path/to/python)" >&2
  exit 1
}

echo "[setup] 1/6 repo verified (MANIFEST.md present at $REPO)"

echo "[setup] 2/6 installing python deps ($PY)"
"$PY" -m pip install -e . --quiet

echo "[setup] 3/6 launchd agents (msb-v3, qdrant, backup)"
mkdir -p "$HOME/Library/LaunchAgents"
for entry in msb-v3:com.lordwilson.msb-v3 qdrant:com.lordwilson.qdrant backup:com.lordwilson.msb-backup; do
  label="${entry%%:*}"
  agent_id="${entry##*:}"
  plist="scripts/launchd/$agent_id.plist"
  dst="$HOME/Library/LaunchAgents/$agent_id.plist"
  if [ ! -f "$plist" ]; then
    echo "[setup]   - $label: plist $plist missing — skipping (repo may not ship it)"
    continue
  fi
  cp "$plist" "$dst"
  if launchctl bootstrap "gui/$(id -u)" "$dst" 2>/dev/null; then
    echo "[setup]   - $label bootstrapped"
  else
    echo "[setup]   - $label already loaded or bootstrap declined (rc=$? — ok if loaded)"
  fi
done

echo "[setup] 4/6 qdrant health (:6333)"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -s -m 2 http://127.0.0.1:6333/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -s -m 3 http://127.0.0.1:6333/healthz >/dev/null 2>&1 || {
  echo "[setup] WARNING: qdrant not answering on :6333 yet (the launchd agent may still be starting)" >&2
}

echo "[setup] 5/6 models"
bash scripts/provision-models.sh

echo "[setup] 6/6 smoke: /health on :8766"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -s -m 2 http://127.0.0.1:8766/health >/dev/null 2>&1; then
    break
  fi
  sleep 3
done
curl -s -m 5 http://127.0.0.1:8766/health >/dev/null 2>&1 || {
  echo "[setup] ERROR: server not up after launchd bootstrap — check logs/server.log" >&2
  exit 1
}

echo "[setup] done — stack is up. Next: make governance-status, make webcheck."
