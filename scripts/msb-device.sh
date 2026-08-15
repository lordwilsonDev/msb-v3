#!/usr/bin/env bash
set -euo pipefail

# Convenience entry for the signed-device CLI — works from any directory.
# Sources .env (pairing code / operator token / VESTA_LOOPBACK_URL) and runs
# the client with the repo's Python and PYTHONPATH. Symlinked onto PATH as
# `msb-device` (see ~/bin/msb-device).

# Resolve the real script location even when invoked through a symlink
# (e.g. ~/bin/msb-device -> scripts/msb-device.sh).
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
REPO="$(cd -P "$(dirname "$SOURCE")/.." >/dev/null 2>&1 && pwd)" || exit 1
PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"

set -a
[ -f "$REPO/.env" ] && . "$REPO/.env"
set +a

export PYTHONPATH="$REPO/src:~/.local/lib/msb-v3"
exec "$PY" "$REPO/scripts/device-client.py" "$@"
