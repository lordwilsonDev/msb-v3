#!/usr/bin/env bash
set -euo pipefail

# llama.cpp (llama-server) for msb-v3 -- start/stop/status helper.
#
# The llama.cpp backend is the OPTIONAL alternate inference backend (the
# active backend is Ollama via MSB_ACTIVE_BACKEND). It is not launchd-
# managed by design: ollama is the primary backend, so llama-server is
# started on demand (standby pattern: nohup + pidfile, mirroring
# scripts/start.sh and the standby path of start-qdrant.sh).
#
# Health is a REAL JSON probe of /health -- llama-server answers 200 JSON
# when the model is loaded; a plain TCP connect is not enough (Apache httpd
# on :8080 answers HTML and must never count as llama.cpp up). This matches
# the /system/health deep check (FR-2.3).
#
# Usage:
#   scripts/start-llamacpp.sh            # start (default)
#   scripts/start-llamacpp.sh status     # pid + JSON health probe
#   scripts/start-llamacpp.sh stop       # kill pidfile pid

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
SERVER_BIN="${LLAMA_SERVER_BIN:-/opt/homebrew/bin/llama-server}"
PIDFILE="${MSB_LLAMACPP_PIDFILE:-$REPO/.artifacts/llamacpp.pid}"
LOGFILE="${MSB_LLAMACPP_LOG:-$REPO/logs/llamacpp.log}"
PORT="${LLAMA_CPP_PORT:-8080}"
# Weights provisioned in Phase 2 (close-out); override via env if moved.
MODEL="${LLAMA_CPP_MODEL:-$HOME/models/gemma-4-12b-it/gemma-4-12b-it-q4_k_m.gguf}"

mkdir -p "$REPO/.artifacts" "$REPO/logs"

log() { echo "[start-llamacpp] $*"; }

# JSON /health probe: 200 + application/json content-type = real llama-server.
healthy() {
  local out
  out=$(curl -sf -m 2 -o /dev/null -w "%{http_code} %{content_type}" "http://127.0.0.1:$PORT/health" 2>/dev/null || true)
  [[ "$out" == 200* && "$out" == *json* ]]
}

is_running() {
  [ -f "$PIDFILE" ] && ps -p "$(cat "$PIDFILE")" >/dev/null 2>&1
}

pidfile_pid() {
  cat "$PIDFILE" 2>/dev/null || echo ""
}

# A live pidfile pid that is NOT llama-server means a recycled pid: never signal it.
pid_is_server() {
  case "$(ps -p "$1" -o comm= 2>/dev/null || true)" in
    *llama-server*) return 0 ;;
    *) return 1 ;;
  esac
}

# A 7.4G model can take >1min to mmap+load on first boot; give it up to ~3min.
wait_healthy() {
  for ((i = 0; i < 360; i++)); do
    healthy && return 0
    sleep 0.5
  done
  return 1
}

cmd="${1:-start}"

case "$cmd" in
  status)
    if is_running; then
      pid=$(pidfile_pid)
      if healthy; then
        log "running pid=$pid port=$PORT HEALTHY (log=$LOGFILE)"
        exit 0
      fi
      log "running pid=$pid port=$PORT but /health not JSON-answering -- see $LOGFILE"
      exit 1
    fi
    log "not running (no live pid at $PIDFILE)"
    exit 1
    ;;
  stop)
    if ! is_running; then
      log "not running -- nothing to stop"
      exit 0
    fi
    pid=$(pidfile_pid)
    if ! pid_is_server "$pid"; then
      log "WARNING: pid=$pid is not llama-server -- refusing to signal; clearing stale pidfile"
      rm -f "$PIDFILE"
      exit 1
    fi
    log "stopping pid=$pid"
    kill "$pid" 2>/dev/null || true
    for ((i = 0; i < 20; i++)); do
      ps -p "$pid" >/dev/null 2>&1 || break
      sleep 0.25
    done
    if ps -p "$pid" >/dev/null 2>&1; then
      log "still alive after TERM -- sending KILL"
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
    log "stopped"
    ;;
  start)
    if [ ! -x "$SERVER_BIN" ]; then
      log "ERROR: llama-server not found at $SERVER_BIN" >&2
      exit 1
    fi
    if [ ! -f "$MODEL" ]; then
      log "ERROR: weights not provisioned at $MODEL -- download gemma-4-12b-it Q4_K_M first" >&2
      exit 1
    fi
    if healthy; then
      log "already running and HEALTHY on :$PORT"
      exit 1
    fi
    if is_running; then
      log "pid $(pidfile_pid) alive but unhealthy -- restarting"
      kill "$(pidfile_pid)" 2>/dev/null || true
      sleep 2
    fi
    nohup "$SERVER_BIN" -m "$MODEL" --host 127.0.0.1 --port "$PORT" > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    if wait_healthy; then
      log "started pid=$(pidfile_pid) log=$LOGFILE port=$PORT HEALTHY (JSON /health)"
    else
      log "ERROR: llama-server not healthy on :$PORT after 3min -- see $LOGFILE" >&2
      exit 1
    fi
    ;;
  *)
    log "usage: $0 [start|stop|status]" >&2
    exit 2
    ;;
esac
