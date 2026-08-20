#!/usr/bin/env bash
# prereq-check.sh — the "make doctor" health sweep for running msb-v3.
#
# Checks every prerequisite documented in docs/PREREQUISITES.md and prints
# a PASS/FAIL/WARN verdict per item. Exit code:
#   0 = all critical prerequisites present
#   1 = at least one critical prerequisite missing (see CRITICAL items)
# Warnings (WARN) never fail the exit code — they are things you should
# configure before going far, not blockers to running the server.
#
#   bash scripts/prereq-check.sh
#   MSB_REPO=/path/to/msb-v3 bash scripts/prereq-check.sh
set -uo pipefail

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

PASS=0
FAIL=0
WARN=0

verdict() { # status label detail
  case "$1" in
    PASS) PASS=$((PASS + 1)); echo "  PASS  $2" ;;
    FAIL) FAIL=$((FAIL + 1)); echo "  FAIL  $2 — $3" ;;
    WARN) WARN=$((WARN + 1)); echo "  WARN  $2 — $3" ;;
  esac
}

echo "== msb-v3 prerequisites ($REPO)"

# --- python ---------------------------------------------------------------
if [ -x "$PY" ]; then
  "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null \
    && verdict PASS "python $("$PY" --version 2>&1 | awk '{print $2}')" \
    || verdict FAIL "python" "at least 3.11 required (got $("$PY" --version 2>&1 | awk '{print $2}'))"
else
  verdict FAIL "python" "$PY not found (set MSB_PYTHON or install 3.11+)"
fi

if [ "$PY" != "python" ] && [ -x "$PY" ]; then
  "$PY" -c 'import fastapi, uvicorn, httpx, prometheus_client' 2>/dev/null \
    && verdict PASS "python deps (fastapi/uvicorn/httpx)" \
    || verdict WARN "python deps" "run: pip install -e \"$REPO[dev]\""
fi

# --- database --------------------------------------------------------------
if [ -f "$REPO/data/msb_v3.db" ]; then
  verdict PASS "database (data/msb_v3.db)"
else
  verdict WARN "database" "data/msb_v3.db missing — first boot creates it"
fi

# --- ollama (local model backend) -----------------------------------------
if curl -sf -m 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  verdict PASS "ollama (http://127.0.0.1:11434)"
  MODEL="${OLLAMA_MODEL:-qwen3:8b}"
  if curl -sf -m 3 "http://127.0.0.1:11434/api/tags" 2>/dev/null | grep -q "\"$MODEL\""; then
    verdict PASS "ollama model $MODEL"
  else
    verdict WARN "ollama model $MODEL" "not pulled — run: ollama pull $MODEL"
  fi
else
  verdict FAIL "ollama" "not reachable at http://127.0.0.1:11434 (install + start Ollama)"
fi

# --- qdrant (vector store for the vault RAG) -------------------------------
if curl -sf -m 3 http://127.0.0.1:6333/healthz >/dev/null 2>&1; then
  verdict PASS "qdrant (http://127.0.0.1:6333)"
else
  verdict FAIL "qdrant" "not reachable at http://127.0.0.1:6333 (needed for vault search)"
fi

# --- docker (optional: open-webui / compose stack) --------------------------
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  verdict PASS "docker"
elif command -v docker >/dev/null 2>&1; then
  verdict WARN "docker" "CLI present but daemon not running"
else
  verdict WARN "docker" "not installed (optional — only needed for the compose stack)"
fi

# --- n8n (automation brain target) -----------------------------------------
if curl -sf -m 3 http://127.0.0.1:5678/healthz >/dev/null 2>&1; then
  verdict PASS "n8n (http://127.0.0.1:5678)"
  if [ -n "${N8N_API_KEY:-}" ]; then
    verdict PASS "n8n API key"
  else
    verdict WARN "n8n API key" "N8N_API_KEY unset — automations can't create workflows (create in n8n: Settings → API)"
  fi
else
  verdict WARN "n8n" "not reachable at http://127.0.0.1:5678 (optional until you use the automation brain)"
fi

# --- keys ------------------------------------------------------------------
[ -n "${DEEPSEEK_API_KEY:-}" ] && verdict PASS "DEEPSEEK_API_KEY" || verdict WARN "DEEPSEEK_API_KEY" "unset — the wake agent / automation brain falls back to local"
[ -n "${OPENAI_API_KEY:-}" ] && verdict PASS "OPENAI_API_KEY" || verdict WARN "OPENAI_API_KEY" "unset — /v1 adapter closed (fail-closed by design)"
[ -n "${MSB_OPERATOR_TOKEN:-}" ] && verdict PASS "MSB_OPERATOR_TOKEN" || verdict WARN "MSB_OPERATOR_TOKEN" "unset — /cron, /wake, /automation control surfaces closed"
[ -n "${MCP_BRIDGE_SECRET:-}" ] && verdict PASS "MCP_BRIDGE_SECRET" || verdict WARN "MCP_BRIDGE_SECRET" "unset — live-auth gate disabled (dev mode)"

# --- disk ------------------------------------------------------------------
# Measure the volume that actually holds the repo (on macOS, "/" is the
# sealed system volume — the real data volume is where the checkout lives).
FREE_GB=$(df -k "$REPO" | awk 'NR==2 {printf "%.1f", $4/1048576}')
USED_PCT=$(df -k "$REPO" | awk 'NR==2 {gsub("%","",$5); print $5}')
if [ "${USED_PCT:-0}" -ge 95 ]; then
  verdict FAIL "disk" "${USED_PCT}% used (${FREE_GB}Gi free) — below the 5% headroom floor"
elif [ "${USED_PCT:-0}" -ge 85 ]; then
  verdict WARN "disk" "${USED_PCT}% used (${FREE_GB}Gi free) — approaching the 85% warn threshold"
else
  verdict PASS "disk (${USED_PCT}% used, ${FREE_GB}Gi free)"
fi

echo
echo "== verdict: $PASS pass, $WARN warn, $FAIL fail"
[ "$FAIL" -eq 0 ]
