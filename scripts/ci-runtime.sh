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
  mkdir -p "$CI_SERVER_RESEARCH"
  export CI_SERVER_HOST CI_SERVER_PORT CI_SERVER_DB CI_SERVER_RESEARCH
  echo "[ci-runtime] dir=$CI_RUNTIME_DIR port=$CI_SERVER_PORT db=$CI_SERVER_DB"
}

ci_runtime_start_server() {
  : "${CI_RUNTIME_DIR:?call ci_runtime_init first}"
  CI_SERVER_PYTHON="${CI_SERVER_PYTHON:-${MSB_PYTHON:-python3}}"
  export CI_SERVER_PYTHON
  env MSB_HOST="$CI_SERVER_HOST" MSB_PORT="$CI_SERVER_PORT" \
      MSB_DB_PATH="$CI_SERVER_DB" MSB_RESEARCH_ROOT="$CI_SERVER_RESEARCH" \
      "$CI_SERVER_PYTHON" -m msb_v3 >"$CI_RUNTIME_DIR/server.log" 2>&1 &
  CI_SERVER_PID=$!
  printf '%s\n' "$CI_SERVER_PID" >"$CI_RUNTIME_DIR/server.pid"
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
