#!/usr/bin/env bash
# reclaim-port.sh — clear a stale, unsupervised port-holder before a
# supervisor starts its own child. Sourced by scripts/run.sh (set MSB_PORT
# and a `log()` function before sourcing) and unit-tested directly by
# scripts/test-ops.sh.
#
# Found 2026-09-12 (had been happening silently since 2026-08-22, ~110k
# occurrences): run.sh's supervised child always runs in the foreground, so
# by the time its while-loop is back at the top, any *previous* child has
# already exited — there is no legitimate way for something else to be
# bound to MSB_PORT at that point. If something is, it's an orphan: the
# standby/nohup path's pidfile tracks the SUPERVISOR script, never the app
# child, so if the supervisor's own process is ever killed directly
# (kill -9, a bad sleep/wake cycle, anything that doesn't go through its
# while-loop) its in-flight child survives, reparented to init, with no
# supervisor of its own. That orphan then permanently blocks every future
# launch attempt — including a properly launchd-managed one — from ever
# binding the port again, since blind retry-on-failure can never fix a
# resource conflict. Confirmed live: the supervised path retried every
# ~15s for 3 weeks straight against an orphan holding the port, and every
# attempt failed exactly the same way.
#
# Verifies the port-holder actually looks like an msb-v3 process (its
# command line contains "msb_v3") before touching it, so an unrelated
# service already using this port gets a loud warning instead of getting
# killed.

reclaim_stale_port() {
  command -v lsof >/dev/null 2>&1 || return 0
  local pid
  # `|| true` is load-bearing under `set -eo pipefail`: lsof exits non-zero
  # whenever nothing matches (the common case — port free), which would
  # otherwise abort the calling script on every normal startup.
  pid="$(lsof -nP -iTCP:"$MSB_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)" || true
  [ -n "$pid" ] || return 0
  local cmd
  cmd="$(ps -o command= -p "$pid" 2>/dev/null)" || true
  case "$cmd" in
    *msb_v3*)
      log "port $MSB_PORT already held by pid $pid ($cmd) with no supervisor of its own -- reclaiming"
      kill "$pid" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" 2>/dev/null || { log "reclaimed port $MSB_PORT from stale pid $pid"; return 0; }
        sleep 0.5
      done
      log "pid $pid did not exit on SIGTERM after 5s; sending SIGKILL"
      kill -9 "$pid" 2>/dev/null || true
      ;;
    *)
      log "WARNING: port $MSB_PORT is held by pid $pid ($cmd), which is not an" \
          "msb-v3 process -- leaving it alone; the bind below will likely fail"
      ;;
  esac
}
