#!/usr/bin/env bash
set -euo pipefail

# msb-v3 server -- start/stop/status helper.
#
# Two operating modes, detected automatically:
#   1. LAUNCHD-MANAGED (default on this machine): the LaunchAgent
#      com.lordwilson.msb-v3 runs scripts/run.sh (the app supervisor) with
#      KeepAlive, so the server starts at login and mid-session crashes
#      auto-restart. start/stop/status map to launchctl bootstrap/bootout/
#      print -- the script and launchd never fight over the server lifecycle.
#      stop unloads the agent, start reloads it, so the cycle is symmetric
#      (bootstrap falls back to standby in non-GUI sessions such as SSH).
#   2. STANDBY (agent not loaded / not loadable): nohup + pidfile, the old
#      behavior. The pidfile tracks the run.sh SUPERVISOR (which itself
#      respawns the app), never the app child.
#
# The plist source of truth lives in the repo at
# scripts/launchd/com.lordwilson.msb-v3.plist; the installed copy is
# ~/Library/LaunchAgents/ (keep the two in sync).
#
# Usage:
#   scripts/start.sh            # start (default)
#   scripts/start.sh status     # manager/pid + /health probe
#   scripts/start.sh stop       # unload agent, or stop the standby supervisor

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
PIDFILE="$REPO/.artifacts/msb-v3.pid"
LOGFILE="$REPO/logs/msb-v3.log"
SCRIPT="$REPO/scripts/run.sh"
PORT="${MSB_PORT:-8766}"
AGENT_LABEL="com.lordwilson.msb-v3"
# The repo holds a path-neutral TEMPLATE (__MSB_REPO__ placeholders); the
# agent that launchctl actually loads is the INSTALLED copy rendered from it
# with THIS checkout's path (see render_plist). Never bootstrap the template
# directly — a committed absolute path made the agent un-startable on any
# other machine (M7 dry-run catch, 2026-08-17).
TEMPLATE_PLIST="$REPO/scripts/launchd/$AGENT_LABEL.plist"
AGENT_PLIST="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"

mkdir -p "$REPO/.artifacts" "$REPO/logs"

# Does the installed plist point at a DIFFERENT checkout than $REPO?
# Returns 0 (foreign) when the plist exists and embeds a path other than this
# checkout's; 1 (ours / absent) otherwise.
plist_foreign_checkout() {
  [ -f "$AGENT_PLIST" ] || return 1
  grep -qF "$REPO" "$AGENT_PLIST" && return 1
  return 0
}

# Render the repo template into the installed LaunchAgents copy, substituting
# the actual checkout path. Called before any bootstrap so the agent points
# at THIS clone regardless of where it lives.
render_plist() {
  [ -f "$TEMPLATE_PLIST" ] || return 0
  # The launchd LABEL is machine-global, not checkout-scoped: if the installed
  # plist belongs to a different checkout AND that agent is still loaded, a
  # render+bootstrap here would bootout the OTHER instance and hijack the
  # label (M7 dry-run #2 catch). Changing MSB_PORT does NOT avoid this -- the
  # collision is the label, not the port. Refuse loudly instead of displacing
  # a live foreign agent.
  if plist_foreign_checkout && is_managed; then
    other=$(grep -oE '<string>[^<]*/scripts/run\.sh</string>' "$AGENT_PLIST" | head -1 | sed -E 's#</?string>##g')
    log "ERROR: launchd label $AGENT_LABEL is already managed by another checkout ($other)" >&2
    log "  refusing to displace it. Stop that instance first (its own scripts/start.sh stop)," >&2
    log "  or run THIS checkout in standby instead: MSB_PORT=<free> nohup bash $SCRIPT &" >&2
    exit 1
  fi
  mkdir -p "$HOME/Library/LaunchAgents"
  sed "s|__MSB_REPO__|$REPO|g" "$TEMPLATE_PLIST" > "$AGENT_PLIST"
}

log() { echo "[start.sh] $*"; }

is_managed() {
  launchctl print "gui/$(id -u)/$AGENT_LABEL" >/dev/null 2>&1
}

healthy() {
  curl -sf -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1
}

# --- standby (pidfile) helpers -------------------------------------------------

is_running() {
  [ -f "$PIDFILE" ] && ps -p "$(cat "$PIDFILE")" >/dev/null 2>&1
}

pidfile_pid() {
  cat "$PIDFILE" 2>/dev/null || echo ""
}

# A live pidfile pid that is NOT a run.sh supervisor means a recycled pid:
# never signal it.
pid_is_runsh() {
  case "$(ps -p "$1" -o args= 2>/dev/null || true)" in
    *scripts/run.sh*) return 0 ;;  # absolute or relative invocation
    *) return 1 ;;
  esac
}

# Stop a standby supervisor AND its app child. Order matters: run.sh's loop
# respawns the app within 2s of the CHILD dying, so child-first would schedule
# a respawn that survives the supervisor's own death and boots later as an
# orphan. Capture the child, kill the SUPERVISOR first (loop dies, no respawn
# is ever scheduled), then TERM the captured child (now orphaned).
kill_supervisor_tree() {
  local pid="$1"
  local child
  child=$(pgrep -P "$pid" 2>/dev/null || true)
  kill "$pid" 2>/dev/null || true
  if [ -n "$child" ]; then
    log "stopping app child $child"
    kill $child 2>/dev/null || true
  fi
}

wait_healthy() {
  # 15s window: app startup (imports + uvicorn boot) is usually 2-5s.
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
        log "launchd-managed ($AGENT_LABEL) -- running HEALTHY (launchd log=logs/gateway.out.log)"
        exit 0
      fi
      log "launchd-managed ($AGENT_LABEL) -- job loaded but /health not responding (KeepAlive should be restarting it)"
      exit 1
    fi
    if is_running; then
      pid=$(pidfile_pid)
      if healthy; then
        log "running pid=$pid port=$PORT HEALTHY (log=$LOGFILE)"
        exit 0
      fi
      log "running pid=$pid port=$PORT but NOT responding on /health"
      exit 1
    fi
    log "not running (no live pid at $PIDFILE)"
    exit 1
    ;;
  stop)
    if is_managed; then
      log "stopping launchd-managed msb-v3 (unloading $AGENT_LABEL)"
      launchctl bootout "gui/$(id -u)/$AGENT_LABEL"
      # Wait until the supervisor is gone AND the port is down (up to 10s).
      # The port can drop while the app is still flushing, and an immediate
      # restart then hits the bind race.
      for ((i = 0; i < 20; i++)); do
        if ! pgrep -f 'msb-v3/scripts/run.sh' >/dev/null 2>&1 && ! healthy; then
          break
        fi
        sleep 0.5
      done
      if pgrep -f 'msb-v3/scripts/run.sh' >/dev/null 2>&1 || healthy; then
        log "WARNING: msb-v3 still alive after bootout -- not stopped cleanly" >&2
        exit 1
      fi
      rm -f "$PIDFILE"  # clear any stale pidfile from a previous standby era
      log "stopped (agent unloaded)"
      exit 0
    fi
    if ! is_running; then
      log "not running -- nothing to stop"
      exit 0
    fi
    pid=$(pidfile_pid)
    if ! pid_is_runsh "$pid"; then
      log "WARNING: pid=$pid is not a run.sh supervisor -- refusing to signal; clearing stale pidfile"
      rm -f "$PIDFILE"
      exit 1
    fi
    log "stopping pid=$pid"
    kill_supervisor_tree "$pid"
    # Wait until the supervisor is gone AND the port is down. A quick single
    # check here races run.sh's 2s respawn / a still-booting app and could
    # report "stopped" while an orphan comes up seconds later.
    for ((i = 0; i < 20; i++)); do
      if ! ps -p "$pid" >/dev/null 2>&1 && ! healthy; then
        break
      fi
      if healthy && ! ps -p "$pid" >/dev/null 2>&1; then
        # Supervisor dead but something still serves: an orphaned app child.
        log "port $PORT still up after supervisor death -- killing leftover app"
        child=$(pgrep -f 'python -m msb_v3' 2>/dev/null || true)
        [ -n "$child" ] && kill -9 $child 2>/dev/null || true
      fi
      sleep 0.5
    done
    if ps -p "$pid" >/dev/null 2>&1; then
      log "supervisor still alive after TERM -- sending KILL"
      kill -9 "$pid" 2>/dev/null || true
    fi
    if healthy; then
      log "WARNING: port $PORT still serving after stop -- not stopped cleanly" >&2
      exit 1
    fi
    rm -f "$PIDFILE"
    log "stopped"
    ;;
  start)
    if healthy; then
      log "already running (launchd or standby) -- HEALTHY"
      exit 1
    fi
    # Render the installed plist BEFORE the is_managed/bootstrap checks below
    # (both bootstrap paths use $AGENT_PLIST).
    render_plist
    if is_managed; then
      # Job loaded but unhealthy (or mid-restart): nudge a clean reload. The
      # reload is NOT atomic (bootout then bootstrap), so a bootstrap failure
      # must not abort with the agent unloaded and the server down -- fall
      # back to the standby path below instead.
      log "launchd-managed but unhealthy -- reloading agent"
      launchctl bootout "gui/$(id -u)/$AGENT_LABEL" 2>/dev/null || true
      if launchctl bootstrap "gui/$(id -u)" "$AGENT_PLIST" 2>/dev/null; then
        if wait_healthy; then
          log "started under launchd ($AGENT_LABEL) HEALTHY"
          exit 0
        fi
        log "ERROR: bootstrap loaded but msb-v3 not healthy on :$PORT -- see logs/gateway.err.log" >&2
        exit 1
      fi
      log "WARNING: agent reload failed -- falling back to standby"
      # fall through to the standby section below
    fi
    # Not managed. If the plist exists, restore supervision first so
    # stop/start stay symmetric (stop unloads, start reloads); bootstrap
    # fails cleanly in non-GUI sessions (SSH) and we fall back to standby.
    if [ -f "$AGENT_PLIST" ]; then
      if launchctl bootstrap "gui/$(id -u)" "$AGENT_PLIST" 2>/dev/null; then
        if wait_healthy; then
          log "started under launchd ($AGENT_LABEL) HEALTHY"
          exit 0
        fi
        log "ERROR: bootstrap loaded but msb-v3 not healthy on :$PORT -- see logs/gateway.err.log" >&2
        exit 1
      fi
      log "agent not loadable in this session -- falling back to standby"
    fi
    # Standby path: nohup the run.sh supervisor. run.sh's own loop absorbs
    # the bind race (a too-early start exits; the loop retries in 2s), but
    # retry the whole launch a few times anyway for a fresh-kill window.
    if is_running; then
      log "already running pid=$(pidfile_pid) -- 'status' to check, 'stop' first to restart"
      exit 1
    fi
    started=""
    for attempt in 1 2 3; do
      nohup bash "$SCRIPT" > "$LOGFILE" 2>&1 &
      echo $! > "$PIDFILE"
      pid=$(pidfile_pid)
      if wait_healthy; then
        started=1
        break
      fi
      log "attempt $attempt: supervisor pid=$pid not healthy -- killing and retrying (see $LOGFILE)"
      kill_supervisor_tree "$pid"
      sleep 3
    done
    if [ -n "$started" ]; then
      log "started pid=$pid log=$LOGFILE port=$PORT HEALTHY"
    else
      log "ERROR: msb-v3 not healthy on :$PORT after 3 attempts -- see $LOGFILE" >&2
      exit 1
    fi
    ;;
  *)
    log "usage: $0 [start|stop|status]" >&2
    exit 2
    ;;
esac
