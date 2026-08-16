#!/usr/bin/env bash
# Idempotent MSB_OPERATOR_TOKEN setup (Phase 3 operator auth).
#
#   bash scripts/set-operator-token.sh        # generate + write if absent
#   bash scripts/set-operator-token.sh status  # report set/unset (no secret)
#
# The token gates the /governance and /flywheel control endpoints (bearer).
# Without it the control surface is closed (503) — fail-closed, same as the
# /v1 adapter without OPENAI_API_KEY. CLI tools are unaffected.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"
ENV_FILE=".env"

is_set() {
  grep -qE '^MSB_OPERATOR_TOKEN=.+$' "$ENV_FILE" 2>/dev/null
}

case "${1:-set}" in
  status)
    if is_set; then
      echo "[operator-token] set"
    else
      echo "[operator-token] unset — run: bash scripts/set-operator-token.sh"
    fi
    exit 0
    ;;
  set) ;;
  *) echo "usage: $0 [set|status]" >&2; exit 2 ;;
esac

$PY - "$ENV_FILE" <<'EOF'
import secrets
import sys
from pathlib import Path

env = Path(sys.argv[1])
text = env.read_text() if env.exists() else ""
if any(
    line.startswith("MSB_OPERATOR_TOKEN=")
    and line[len("MSB_OPERATOR_TOKEN=") :].strip() != ""
    for line in text.splitlines()
):
    print("[operator-token] already set — leaving .env unchanged")
    raise SystemExit(0)

token = secrets.token_urlsafe(32)
lines = text.splitlines()
replaced = False
for i, line in enumerate(lines):
    if line.startswith("MSB_OPERATOR_TOKEN="):
        lines[i] = f"MSB_OPERATOR_TOKEN={token}"
        replaced = True
        break
if not replaced:
    lines.append("# Operator bearer token for the /governance + /flywheel control surfaces (Phase 3).")
    lines.append(f"MSB_OPERATOR_TOKEN={token}")
env.write_text("\n".join(lines) + "\n")
print("[operator-token] generated and written to .env")
EOF
