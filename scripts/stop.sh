#!/usr/bin/env bash
set -euo pipefail

# msb-v3 server stop -- thin wrapper. All the logic (launchd-aware: bootout
# when the agent is loaded, pidfile-tree kill in standby mode) lives in
# scripts/start.sh so the two can never disagree.
exec bash "$(dirname "$0")/start.sh" stop "$@"
