#!/usr/bin/env bash
# Run-scoped runtime helpers for CI. This file never discovers or terminates
# an unrelated listener: cleanup only uses the PID recorded by start_server.
set -euo pipefail

ci_runtime_init() {
  : "${RUNNER_TEMP:=/tmp}"
  CI_RUNTIME_DIR="${CI_RUNTIME_DIR:-$(mktemp -d "$RUNNER_TEMP/msb-v3-ci-XXXXXX")}"
  mkdir -p "$CI_RUNTIME_DIR"
  export CI_RUNTIME_DIR
  # Scoped server config. These are NOT exported into the caller's shell:
  # exporting MSB_PORT/MSB_DB_PATH/MSB_RESEARCH_ROOT redirects every test's
  # Settings (a free port leaked in and broke `assert settings.port ==`
  # <default>). The start_server step passes them to the `python -m msb_v3`
  # subprocess only; the suite learns the endpoint from MSB_BASE_URL.
  CI_SERVER_HOST="${MSB_HOST:-127.0.0.1}"
  if [ -z "${CI_SERVER_PORT:-}" ]; then
    # Ask the kernel for an available port without touching any existing one.
    CI_SERVER_PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
  fi
  CI_SERVER_DB="$CI_RUNTIME_DIR/msb.db"
  CI_SERVER_RESEARCH="$CI_RUNTIME_DIR/research"
  # The memory fabric is its own SQLite file with its own env override
  # (`core/config.py` memory_fabric_db_path), and its default is *relative* —
  # "data/memory_fabric/memory.db", resolved against the server's CWD. Left
  # unredirected, a run-scoped server still opens the deployment's memory
  # fabric, so the isolation this script exists to provide stopped at that DB.
  CI_SERVER_MEMORY_DB="$CI_RUNTIME_DIR/memory_fabric/memory.db"
  # Repo root from THIS script's location, not the caller's cwd: verify-release.sh
  # sources this file from a clone's parent directory.
  _CI_RUNTIME_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  CI_SERVER_ENV_FILE="${CI_SERVER_ENV_FILE:-$_CI_RUNTIME_REPO/.env}"
  mkdir -p "$CI_SERVER_RESEARCH" "$CI_RUNTIME_DIR/memory_fabric"
  export CI_SERVER_HOST CI_SERVER_PORT CI_SERVER_DB CI_SERVER_RESEARCH \
         CI_SERVER_MEMORY_DB CI_SERVER_ENV_FILE
  echo "[ci-runtime] dir=$CI_RUNTIME_DIR port=$CI_SERVER_PORT db=$CI_SERVER_DB memory_db=$CI_SERVER_MEMORY_DB"
}

ci_runtime_start_server() {
  : "${CI_RUNTIME_DIR:?call ci_runtime_init first}"
  CI_SERVER_PYTHON="${CI_SERVER_PYTHON:-${MSB_PYTHON:-python3}}"
  export CI_SERVER_PYTHON
  # `python -m msb_v3` alone does not boot on a checkout that has a .env: the
  # app fails closed (`SecretRedactionUnarmed`) when .env exists but no
  # configured secret name resolved, and only scripts/run.sh sources it. CI
  # never noticed because a virgin clone has no .env (portability staging
  # excludes it on purpose) — so the run-scoped server only ever started where
  # there was nothing to load. Load it the way run.sh does, inside a subshell:
  # the redirects passed to `env` still win over any same-named key in .env,
  # and nothing reaches the caller's shell (this script's whole discipline).
  (
    set -a
    # shellcheck disable=SC1090
    [ -f "$CI_SERVER_ENV_FILE" ] && . "$CI_SERVER_ENV_FILE"
    set +a
    exec env MSB_HOST="$CI_SERVER_HOST" MSB_PORT="$CI_SERVER_PORT" \
        MSB_DB_PATH="$CI_SERVER_DB" MSB_RESEARCH_ROOT="$CI_SERVER_RESEARCH" \
        MSB_MEMORY_FABRIC_DB_PATH="$CI_SERVER_MEMORY_DB" \
        "$CI_SERVER_PYTHON" -m msb_v3
  ) >"$CI_RUNTIME_DIR/server.log" 2>&1 &
  CI_SERVER_PID=$!
  printf '%s\n' "$CI_SERVER_PID" >"$CI_RUNTIME_DIR/server.pid"
  # Record the stores the server ACTUALLY got. A test that has to restart it
  # (tests/security/test_cold_state_verification.py SIGKILLs and respawns) cannot
  # otherwise know what the caller configured — verify-release.sh overrides
  # CI_SERVER_RESEARCH to the clone's seeded fixtures, and a respawn that assumed
  # the default temp layout came back serving an empty research root, which
  # silently failed the two seeded-harness tests. These are not exported: the
  # pytest shell must keep default Settings (see the note in ci_runtime_init).
  {
    printf 'MSB_DB_PATH=%s\n' "$CI_SERVER_DB"
    printf 'MSB_RESEARCH_ROOT=%s\n' "$CI_SERVER_RESEARCH"
    printf 'MSB_MEMORY_FABRIC_DB_PATH=%s\n' "$CI_SERVER_MEMORY_DB"
  } >"$CI_RUNTIME_DIR/server.env"
  export CI_SERVER_PID
  # The one msb-v3 env var the suite is allowed to inherit: where the server is.
  export MSB_BASE_URL="http://127.0.0.1:${CI_SERVER_PORT}"
  trap 'ci_runtime_cleanup' EXIT
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "$MSB_BASE_URL/health"; then
      echo "[ci-runtime] server healthy pid=$CI_SERVER_PID port=$CI_SERVER_PORT"
      return 0
    fi
    if ! kill -0 "$CI_SERVER_PID" 2>/dev/null; then
      echo "[ci-runtime] server exited; log follows" >&2
      cat "$CI_RUNTIME_DIR/server.log" >&2 || true
      return 1
    fi
    sleep 1
  done
  echo "[ci-runtime] server did not become healthy; log follows" >&2
  cat "$CI_RUNTIME_DIR/server.log" >&2 || true
  return 1
}

ci_runtime_cleanup() {
  [ -n "${CI_RUNTIME_DIR:-}" ] || return 0
  if [ -f "$CI_RUNTIME_DIR/server.pid" ]; then
    pid="$(cat "$CI_RUNTIME_DIR/server.pid")"
    case "$pid" in
      ''|*[!0-9]*) ;;
      *)
        if kill -0 "$pid" 2>/dev/null; then
          kill "$pid" 2>/dev/null || true
          for _ in $(seq 1 10); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.2
          done
          kill -9 "$pid" 2>/dev/null || true
        fi
        ;;
    esac
  fi
  rm -rf "$CI_RUNTIME_DIR"
}
