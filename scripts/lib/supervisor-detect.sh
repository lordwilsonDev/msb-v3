#!/usr/bin/env bash
# supervisor-detect.sh — is a run.sh supervisor for THIS checkout running?
# Sourced by scripts/start.sh (set REPO first) and unit-tested directly by
# scripts/test-ops.sh, the same way lib/reclaim-port.sh is.
#
# Why this exists: the previous check was `pgrep -f 'msb-v3/scripts/run.sh'` —
# an ABSOLUTE-path needle that only matches the invocations start.sh itself
# makes (launchd and the nohup standby both use "$REPO/scripts/run.sh"). A
# supervisor started by hand, or by any tool, as `bash scripts/run.sh` from the
# repo root is invisible to it, so `start.sh stop` could report "not running"
# over a live supervisor and `start.sh start` could start a SECOND one. Two
# loops both call reclaim_stale_port(), which is the 2026-10-11 restart war
# (7,268 restarts in 29h). The single-instance lock in run.sh now prevents the
# damage, but a liveness check that cannot see a live supervisor is still wrong
# and still returns false "not running" answers.
#
# Matches the relative needle — hygiene/h02_restart_runner.py already had this
# right — and then verifies the process's working directory IS this checkout,
# so an unrelated scripts/run.sh in another directory cannot produce a false
# positive.

supervisor_pids() {
  command -v pgrep >/dev/null 2>&1 || return 1
  # `|| true` is load-bearing under `set -eo pipefail`: pgrep exits non-zero
  # whenever nothing matches (the common case), which would otherwise abort the
  # calling script on a plain "no supervisor running" answer.
  pgrep -f 'scripts/run\.sh' 2>/dev/null || true
}

supervisor_running() {
  local want pid cwd
  # `pwd -P`, not `pwd`: bash's pwd is LOGICAL, so on macOS it returns
  # /tmp/... while lsof reports the physical /private/tmp/... — comparing the
  # two never matches and every checkout under a symlinked path reads as
  # "not running". h02_restart_runner.py avoids this with Path.resolve().
  want="$(cd "${REPO:-.}" 2>/dev/null && pwd -P)" || return 1
  if ! command -v lsof >/dev/null 2>&1; then
    # Cannot verify the cwd. Report "running" for any match rather than risk a
    # false "not running", which is the direction that starts a duplicate.
    [ -n "$(supervisor_pids)" ] && return 0
    return 1
  fi
  for pid in $(supervisor_pids); do
    [ -n "$pid" ] || continue
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    [ -n "$cwd" ] || continue
    if [ "$(cd "$cwd" 2>/dev/null && pwd -P)" = "$want" ]; then
      return 0
    fi
  done
  return 1
}
