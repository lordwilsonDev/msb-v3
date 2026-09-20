#!/usr/bin/env bash
# Identity-shadow rollout configuration (Deliverable 02 §10 / K22).
#
#   bash scripts/set-identity-shadow-rollout.sh status             # report current state
#   bash scripts/set-identity-shadow-rollout.sh set                # write the observation config
#   bash scripts/set-identity-shadow-rollout.sh grant-vault-write  # ACCESS WIDENING — needs sign-off
#
# Why this is a script and not a hand edit: .env is gitignored, so the change
# would be invisible to review — and one of these keys WIDENS ACCESS. An
# idempotent write path with a status read-out is the same convention
# scripts/set-operator-token.sh already uses for this file.
#
# The keys it manages:
#
#   MSB_CHAT_ACTOR_ID, MSB_MCP_ACTOR_ID
#       Make each live surface assert an actor, so K22 criterion 1 ("every live
#       surface carries an actor") can be evaluated at all. Both ids are
#       deliberately NOT registered in the agent registry: criterion 4 ("the
#       identity is justified as a grant") must stay an operator decision, so
#       the observed verdicts are EXPECTED to be rejections.
#
#   MSB_MCP_GRANTED_CAPABILITIES
#       Empty (what `set` writes) = the bridge is READ-ONLY and every vault
#       mutation is refused fail-closed at the capability gate. That is the
#       shipped default and the state this repo ran in until 2026-09-20.
#
#       `grant-vault-write` sets it to vault.write — an ACCESS WIDENING, kept as
#       its own named command precisely so it can never happen as a side effect
#       of configuring observation. Any holder of MCP_BRIDGE_SECRET can then
#       write the vault, and the bridge's actor is the socket peer, so such a
#       write cannot be attributed to a caller. Reverted 2026-09-20 (JOB-022):
#       it had no consumer — no workflow called /mcp/proxy at all — and it
#       existed only so the identity observation point, which sits AFTER the
#       capability gate, could be reached. Authority widened to render an
#       instrument readable is not evidence, it is contamination; see
#       ai-workspace/job-board/done/JOB-022-bridge-grant-decision/.
#
# MSB_MCP_ACTOR_ID and MSB_MCP_GRANTED_CAPABILITIES are read at IMPORT time
# (api/mcp_bridge.py), so a running server must be restarted to pick them up:
#
#   scripts/start.sh stop && scripts/start.sh start
#
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"
ENV_FILE=".env"

case "${1:-status}" in
  status|set|grant-vault-write) ;;
  *) echo "usage: $0 [status|set|grant-vault-write]" >&2; exit 2 ;;
esac

$PY - "$ENV_FILE" "$1" <<'EOF'
import sys
from pathlib import Path

# Faces of the surface: which identity each live surface asserts. Required for
# criterion 1 to be evaluable at all, so both modes write them.
IDENTITY = {
    "MSB_CHAT_ACTOR_ID": "chat.agent",
    "MSB_MCP_ACTOR_ID": "mcp-bridge.agent",
}

GRANT_KEY = "MSB_MCP_GRANTED_CAPABILITIES"
# The desired grant per mode. `set` demands EMPTY: absence of a capability is
# the fail-closed state, and a config command that silently grants is how a
# widening gets mistaken for configuration.
GRANT = {"set": "", "grant-vault-write": "vault.write"}

HEADER = "# Identity-shadow rollout — see scripts/set-identity-shadow-rollout.sh"
WIDENING = "# can now write the vault and cannot be attributed to a caller"

env = Path(sys.argv[1])
mode = sys.argv[2]
lines = env.read_text().splitlines() if env.exists() else []
keys = list(IDENTITY) + [GRANT_KEY]
current: dict[str, str] = {}
for line in lines:
    for key in keys:
        if line.startswith(f"{key}="):
            current[key] = line.split("=", 1)[1].strip()

if mode == "status":
    for key, want in IDENTITY.items():
        got = current.get(key, "")
        if key not in current:
            print(f"[identity-shadow] {key} absent (expected {want})")
        elif got == want:
            print(f"[identity-shadow] {key}={got} ok")
        else:
            print(f"[identity-shadow] {key}={got or '<empty>'} DIFFERS (expected {want})")
    grant = current.get(GRANT_KEY, "")
    if GRANT_KEY not in current:
        print(f"[identity-shadow] {GRANT_KEY} absent (= read-only, the fail-closed default)")
    elif grant:
        print(f"[identity-shadow] {GRANT_KEY}={grant} — ACCESS WIDENING IN FORCE ({WIDENING.lstrip('# ')})")
    else:
        print(f"[identity-shadow] {GRANT_KEY}= empty — bridge read-only (fail-closed)")
    raise SystemExit(0)

wanted = dict(IDENTITY)
wanted[GRANT_KEY] = GRANT[mode]

changed: list[str] = []
missing: list[str] = []
for key, want in wanted.items():
    if key not in current:
        missing.append(key)
        continue
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            if line != f"{key}={want}":
                lines[i] = f"{key}={want}"
                changed.append(key)
            break

if missing:
    if HEADER not in lines:
        lines.append(HEADER)
    for key in missing:
        lines.append(f"{key}={wanted[key]}")
        changed.append(key)

if not changed:
    print("[identity-shadow] already configured — leaving .env unchanged")
    raise SystemExit(0)

env.write_text("\n".join(lines) + "\n")
print("[identity-shadow] wrote: " + ", ".join(changed))
if mode == "grant-vault-write":
    print(f"[identity-shadow] WARNING: {WIDENING.lstrip('# ')}")
    print("[identity-shadow] restart required: scripts/start.sh stop && scripts/start.sh start")
EOF
