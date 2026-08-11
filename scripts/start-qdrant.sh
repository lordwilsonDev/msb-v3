#!/usr/bin/env bash
set -euo pipefail

# Qdrant for msb-v3 -- start/stop/status helper.
#
# Two operating modes, detected automatically:
#   1. LAUNCHD-MANAGED (default on this machine): the LaunchAgent
#      com.lordwilson.qdrant runs the qdrant binary DIRECTLY with KeepAlive,
#      so a mid-session crash is restarted automatically. start/stop/status
#      map to launchctl bootstrap/bootout/print -- the script and launchd
#      never fight over qdrant's lifecycle. start/stop stay symmetric: stop
#      unloads the agent, start reloads it (bootstrap falls back to standby
#      in non-GUI sessions such as SSH).
#   2. STANDBY (agent not loaded / not loadable): nohup + pidfile, mirroring
#      scripts/start.sh. Used pre-login, over SSH, or without the agent.
#
# Storage gotcha (both modes): this qdrant build has NO --storage-path flag --
# it resolves storage as ./storage relative to its WORKING DIRECTORY. The
# tenant collections (incl. tenant_wilson-vault, ~5.4k chunks) live at
# $REPO/storage, so qdrant must run with cwd=$REPO. The LaunchAgent's
# WorkingDirectory does this; the standby path cd's into $REPO.
#
# Usage:
#   scripts/start-qdrant.sh            # start (default)
#   scripts/start-qdrant.sh status     # manager/pid + healthz probe
#   scripts/start-qdrant.sh stop       # unload agent, or kill pidfile pid

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
QDRANT_BIN="${QDRANT_BIN:-$HOME/.local/bin/qdrant}"
PIDFILE="${MSB_QDRANT_PIDFILE:-$REPO/.artifacts/qdrant.pid}"
LOGFILE="${MSB_QDRANT_LOG:-$REPO/logs/qdrant.log}"
PORT="${QDRANT_PORT:-6333}"
AGENT_LABEL="com.lordwilson.qdrant"
AGENT_PLIST="$REPO/scripts/launchd/$AGENT_LABEL.plist"

mkdir -p "$REPO/.artifacts" "$REPO/logs"

log() { echo "[start-qdrant] $*"; }

is_managed() {
  launchctl print "gui/$(id -u)/$AGENT_LABEL" >/dev/null 2>&1
}

healthy() {
  curl -sf -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1
}

# --- standby (pidfile) helpers -------------------------------------------------

is_running() {
  [ -f "$PIDFILE" ] && ps -p "$(cat "$PIDFILE")" >/dev/null 2>&1
}

pidfile_pid() {
  cat "$PIDFILE" 2>/dev/null || echo ""
}

# A live pidfile pid that is NOT qdrant means a recycled pid: never signal it.
pid_is_qdrant() {
  case "$(ps -p "$1" -o comm= 2>/dev/null || true)" in
    *qdrant*) return 0 ;;
    *) return 1 ;;
  esac
}

wait_healthy() {
  # 15s window: qdrant loads ~70 collections; a cold boot can exceed 6s.
  for ((i = 0; i < 30; i++)); do
    healthy && return 0
    sleep 0.5
  done
  return 1
}

cmd="${1:-start}"

case "$cmd" in
  status)
    if is_managed; then
      if healthy; then
        log "launchd-managed ($AGENT_LABEL) -- running HEALTHY (log=$LOGFILE)"
        exit 0
      fi
      log "launchd-managed ($AGENT_LABEL) -- job loaded but /healthz not responding (KeepAlive should be restarting it)"
      exit 1
    fi
    if is_running; then
      pid=$(pidfile_pid)
      if healthy; then
        log "running pid=$pid port=$PORT HEALTHY (log=$LOGFILE)"
        exit 0
      fi
      log "running pid=$pid port=$PORT but NOT responding on /healthz"
      exit 1
    fi
    log "not running (no live pid at $PIDFILE)"
    exit 1
    ;;
  stop)
    if is_managed; then
      log "stopping launchd-managed qdrant (unloading $AGENT_LABEL)"
      launchctl bootout "gui/$(id -u)/$AGENT_LABEL"
      # Wait until the PROCESS is gone AND the port is down (up to 10s). The
      # port can drop while the process is still flushing storage, and an
      # immediate restart then panics with WAL WouldBlock (storage lock held).
      for ((i = 0; i < 20; i++)); do
        if ! pgrep -x qdrant >/dev/null 2>&1 && ! healthy; then
          break
        fi
        sleep 0.5
      done
      if pgrep -x qdrant >/dev/null 2>&1 || healthy; then
        log "WARNING: qdrant still alive after bootout -- not stopped cleanly" >&2
        exit 1
      fi
      log "stopped (agent unloaded)"
      exit 0
    fi
    if ! is_running; then
      log "not running -- nothing to stop"
      exit 0
    fi
    pid=$(pidfile_pid)
    if ! pid_is_qdrant "$pid"; then
      log "WARNING: pid=$pid is not qdrant -- refusing to signal; clearing stale pidfile"
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
    if [ ! -x "$QDRANT_BIN" ]; then
      log "ERROR: qdrant binary not found at $QDRANT_BIN" >&2
      exit 1
    fi
    if [ ! -d "$REPO/storage/collections" ]; then
      log "ERROR: no storage at $REPO/storage -- qdrant has nothing to serve" >&2
      exit 1
    fi
    if healthy; then
      log "already running (launchd or standby) -- HEALTHY"
      exit 1
    fi
    if is_managed; then
      # Job loaded but unhealthy (or mid-restart): nudge a clean reload. The
      # reload is NOT atomic (bootout then bootstrap), so a bootstrap failure
      # must not abort with the agent unloaded and qdrant down -- fall back to
      # the standby path below instead.
      log "launchd-managed but unhealthy -- reloading agent"
      launchctl bootout "gui/$(id -u)/$AGENT_LABEL" 2>/dev/null || true
      if launchctl bootstrap "gui/$(id -u)" "$AGENT_PLIST" 2>/dev/null; then
        if wait_healthy; then
          log "started under launchd ($AGENT_LABEL) HEALTHY"
          exit 0
        fi
        log "ERROR: bootstrap loaded but qdrant not healthy on :$PORT -- see $LOGFILE" >&2
        exit 1
      fi
      log "WARNING: agent reload failed -- falling back to standby"
      # fall through to the standby section below
    fi
    # Not managed. If the agent plist exists, restore supervision first so
    # stop/start stay symmetric (stop unloads, start reloads); bootstrap
    # fails cleanly in non-GUI sessions (SSH) and we fall back to standby.
    if [ -f "$AGENT_PLIST" ]; then
      if launchctl bootstrap "gui/$(id -u)" "$AGENT_PLIST" 2>/dev/null; then
        if wait_healthy; then
          log "started under launchd ($AGENT_LABEL) HEALTHY"
          exit 0
        fi
        log "ERROR: bootstrap loaded but qdrant not healthy on :$PORT -- see $LOGFILE" >&2
        exit 1
      fi
      log "agent not loadable in this session -- falling back to standby"
    fi
    # Standby path: cwd MUST be $REPO (storage resolves as ./storage). Retry
    # a few times: a freshly killed previous instance can briefly hold the
    # storage WAL lock, and a too-early start panics (WAL WouldBlock).
    if is_running; then
      log "already running pid=$(pidfile_pid) -- 'status' to check, 'stop' first to restart"
      exit 1
    fi
    started=""
    for attempt in 1 2 3; do
      (
        cd "$REPO"
        nohup "$QDRANT_BIN" > "$LOGFILE" 2>&1 &
        echo $! > "$PIDFILE"
      )
      pid=$(pidfile_pid)
      if wait_healthy; then
        started=1
        break
      fi
      log "attempt $attempt: qdrant pid=$pid not healthy -- killing and retrying (see $LOGFILE)"
      kill "$pid" 2>/dev/null || true
      sleep 3
    done
    if [ -n "$started" ]; then
      log "started pid=$pid log=$LOGFILE port=$PORT HEALTHY"
    else
      log "ERROR: qdrant not healthy on :$PORT after 3 attempts -- see $LOGFILE" >&2
      exit 1
    fi
    ;;
  *)
    log "usage: $0 [start|stop|status]" >&2
    exit 2
    ;;
esac
