#!/usr/bin/env bash
# Run-scoped runtime helpers for CI. This file never discovers or terminates
# an unrelated listener: cleanup only uses the PID recorded by start_server.
set -euo pipefail

ci_runtime_init() {
  : "${RUNNER_TEMP:=/tmp}"
  CI_RUNTIME_DIR="${CI_RUNTIME_DIR:-$(mktemp -d "$RUNNER_TEMP/msb-v3-ci-XXXXXX")}"
  mkdir -p "$CI_RUNTIME_DIR"
  export CI_RUNTIME_DIR
  export MSB_HOST="${MSB_HOST:-127.0.0.1}"
  if [ -z "${MSB_PORT:-}" ]; then
    # Ask the kernel for an available port without touching any existing one.
    MSB_PORT="$(python - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
  fi
  export MSB_PORT
  # CI ownership is strict: inherited developer paths are never reused.
  export MSB_DB_PATH="$CI_RUNTIME_DIR/msb.db"
  export MSB_RESEARCH_ROOT="$CI_RUNTIME_DIR/research"
  mkdir -p "$MSB_RESEARCH_ROOT"
  echo "[ci-runtime] dir=$CI_RUNTIME_DIR port=$MSB_PORT db=$MSB_DB_PATH"
}

ci_runtime_start_server() {
  : "${CI_RUNTIME_DIR:?call ci_runtime_init first}"
  : "${CI_SERVER_PYTHON:-${MSB_PYTHON:-python}}"
  "$CI_SERVER_PYTHON" -m msb_v3 >"$CI_RUNTIME_DIR/server.log" 2>&1 &
  CI_SERVER_PID=$!
  printf '%s\n' "$CI_SERVER_PID" >"$CI_RUNTIME_DIR/server.pid"
  export CI_SERVER_PID
  trap 'ci_runtime_cleanup' EXIT
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${MSB_PORT}/health"; then
      echo "[ci-runtime] server healthy pid=$CI_SERVER_PID port=$MSB_PORT"
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
