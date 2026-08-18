#!/usr/bin/env bash
set -uo pipefail

# ci-policy-gate -- CI gate for the MoIE detection policy.
#
# Validates config/risk_templates.json with the same fail-closed loader the
# engine uses at import, then FAILS THE BUILD if detection coverage drifts
# from the pinned baseline (MSB-GATE-EVAL-001: 17/8/8/23, precision 0.68,
# recall 0.425). A policy edit that changes what the gate blocks must land
# together with the updated pins in tests/contracts/test_gate_contract.py
# and tests/contracts/test_phase2_calibration.py — a policy change can
# never slip through silently.
#
# Usage:
#   bash scripts/ci-policy-gate.sh
#
# Exit: 0 = policy valid and coverage MATCHES baseline;
#       1 = validation failure (missing/corrupt/incomplete policy);
#       2 = strict drift (coverage moved from pinned baseline);
#       3 = environment/usage error.
#
# Env:
#   MSB_REPO        repo root (default: this script's parent/..)
#   MSB_PYTHON      python binary (default: python3)
#   MSB_RISK_POLICY_PATH  optional candidate policy to gate instead of the
#                   committed config/risk_templates.json (diffed against it)
#   MSB_STRICT      set to 0 to downgrade drift from a failure to a
#                   warning (exit 0) — for dry-run/development only

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 3
PY="${MSB_PYTHON:-python3}"

if [ ! -f "$REPO/config/risk_templates.json" ]; then
  echo "[ci-policy-gate] ERROR: repo policy not found at $REPO/config/risk_templates.json (set MSB_REPO)" >&2
  exit 3
fi

cd "$REPO" || exit 3
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$REPO/src"

# MSB_RISK_POLICY_PATH is an ENGINE knob: msb_v3.moie's module-level policy
# load honors it (fail-closed — a corrupt policy must prevent boot), so a
# bad env value would blow up the package import before the CLI's own
# validator runs. The CLI takes candidates via --policy instead: unset the
# env so the package imports against the committed policy, then pass the
# candidate through the flag (validated cleanly by cmd_policy).
CANDIDATE="${MSB_RISK_POLICY_PATH:-}"
unset MSB_RISK_POLICY_PATH

ARGS=(policy --strict)
if [ -n "$CANDIDATE" ]; then
  ARGS+=(--policy "$CANDIDATE")
fi

if [ "${MSB_STRICT:-1}" = "0" ]; then
  ARGS+=(--json)
  "$PY" -m msb_v3.moie "${ARGS[@]}" || true
  echo "[ci-policy-gate] MSB_STRICT=0 — drift downgraded to a warning (dry-run only)"
  exit 0
fi

exec "$PY" -m msb_v3.moie "${ARGS[@]}"
