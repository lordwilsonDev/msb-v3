#!/usr/bin/env bash
# check-env-drift.sh — warn when the live .env drifts from the locked template.
#
# .env.example declares the deployment contract. Policy:
#   - CONTRACT VARS: keys whose example value is NON-EMPTY (MSB_PORT,
#     OPENAI_FRONTIER_URL, OLLAMA_MODEL, ...). The live .env must carry the
#     same value — a mismatch, or a contract var missing from .env entirely
#     (config silently falls back to a default), is drift.
#   - SECRET/OPTIONAL PLACEHOLDERS: keys whose example value is EMPTY
#     (OPENAI_API_KEY, MSB_OPERATOR_TOKEN, WEBUI_SECRET_KEY, ...). The live
#     .env is expected to hold a real value or leave them unset, so they are
#     NEVER compared and NEVER printed. Only a masked presence note is shown.
#   - CONDITIONAL VARS: a small allowlist (TENCENT_COS_ENDPOINT/REGION) whose
#     absence is legitimate (only used when STORAGE_PROVIDER=s3). If present
#     they are still value-checked.
#   - SECRET DENY-LIST: keys in SECRET_KEYS are NEVER value-printed, even if a
#     future .env.example edit gave them a non-empty default (the guard would
#     compare silently and report only the key). Invariant: secrets are
#     withheld by name, not by whether the template value is empty.
#
#   bash scripts/check-env-drift.sh                    # warn only; exit 0 always
#   bash scripts/check-env-drift.sh --fail             # exit 1 on any mismatch
#   bash scripts/check-env-drift.sh --selftest         # run embedded fixtures
#   bash scripts/check-env-drift.sh --template-secrets-block
#                                                     # + fail if the template
#                                                     # itself ships a value for
#                                                     # any SECRET_KEYS entry
#   bash scripts/check-env-drift.sh [ENV] [EXAMPLE]    # custom paths (tests)
#
# Missing .env -> "skipped" notice, exit 0: fresh clones / CI have no .env,
# and that is a legitimate state, not drift. Missing .env.example -> FAIL.
# Runs from the portability gate (scripts/portability-check.sh) before push;
# set PORTABILITY_FAIL_ON_DRIFT=1 there to make drift block the push.
set -uo pipefail

FAIL=0
SELFTEST=0
TEMPLATE_SECRETS_BLOCK=0
ENV_FILE=""
EXAMPLE_FILE=""
for arg in "$@"; do
  case "$arg" in
    --fail) FAIL=1 ;;
    --selftest) SELFTEST=1 ;;
    --template-secrets-block) TEMPLATE_SECRETS_BLOCK=1 ;;
    *)
      if [ -z "$ENV_FILE" ]; then ENV_FILE="$arg"; else EXAMPLE_FILE="$arg"; fi
      ;;
  esac
done

REPO="${MSB_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" || exit 1
ENV_FILE="${ENV_FILE:-$REPO/.env}"
EXAMPLE_FILE="${EXAMPLE_FILE:-$REPO/.env.example}"

# Vars whose absence from .env is legitimate (conditional features). Keep the
# list minimal + documented; everything else non-empty is a hard contract var.
CONDITIONAL_MISSING_OK=(TENCENT_COS_ENDPOINT TENCENT_COS_REGION)

# Secret keys: compared silently at most, never value-printed. Keep in sync
# with the empty-value placeholders in .env.example.
SECRET_KEYS=(OPENAI_API_KEY MSB_OPERATOR_TOKEN WEBUI_SECRET_KEY TENCENT_COS_SECRET_ID TENCENT_COS_SECRET_KEY)

_is_secret() {
  local k="$1" s
  for s in "${SECRET_KEYS[@]}"; do
    [ "$s" = "$k" ] && return 0
  done
  return 1
}

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# Parse "KEY=value" lines (skip section markers like [TEMPLATE], comments,
# blanks, and any line without '='). Split on the FIRST '=' so values
# containing '=' survive. Prints "key<tab>value" per line, trimmed.
parse() {
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    case "$line" in
      *=*) ;;
      *) continue ;;
    esac
    key="$(trim "${line%%=*}")"
    value="$(trim "${line#*=}")"
    [ -n "$key" ] && printf '%s\t%s\n' "$key" "$value"
  done <"$1"
}

# --- embedded fixtures for --selftest (machine-checkable close) -------------
selftest() {
  local tmp; tmp="$(mktemp -d /tmp/env-drift-XXXX)"
  local ex="$tmp/example.env" en="$tmp/env.env"
  cat >"$ex" <<'EOF'
[TEMPLATE]
# contract vars
MSB_PORT=8766
OLLAMA_MODEL=qwen3:8b
OPENAI_FRONTIER_URL=https://api.deepseek.com/v1
# conditional — absent from env is legitimate
TENCENT_COS_ENDPOINT=https://cos.ap-guangzhou.myqcloud.com
# secret placeholders
OPENAI_API_KEY=
MSB_OPERATOR_TOKEN=
EOF
  cat >"$en" <<'EOF'
MSB_PORT=8766
OLLAMA_MODEL=qwen3:8b
OPENAI_FRONTIER_URL=https://api.deepseek.com/v1
OPENAI_API_KEY=sk-whatever-123
EOF
  local fail=0 out
  # 1. clean env (conditional var absent) -> no warnings
  out="$(bash "${BASH_SOURCE[0]}" --fail "$en" "$ex")" || fail=1
  case "$out" in *WARN*) fail=1 ;; esac
  # 2. mismatched contract var -> warning + --fail exit 1
  sed 's/OLLAMA_MODEL=qwen3:8b/OLLAMA_MODEL=qwen3:16b/' "$en" >"$tmp/bad.env"
  out="$(bash "${BASH_SOURCE[0]}" --fail "$tmp/bad.env" "$ex")" || [ $? -eq 1 ] || fail=1
  case "$out" in *"OLLAMA_MODEL differs"*) ;; *) fail=1 ;; esac
  # 3. missing contract var -> warning
  grep -v OPENAI_FRONTIER_URL "$en" >"$tmp/missing.env"
  out="$(bash "${BASH_SOURCE[0]}" --fail "$tmp/missing.env" "$ex")" || [ $? -eq 1 ] || fail=1
  case "$out" in *"missing from .env"*) ;; *) fail=1 ;; esac
  # 4. missing .env -> skip, exit 0
  bash "${BASH_SOURCE[0]}" "$tmp/nope.env" "$ex" >/dev/null || fail=1
  # 5. secret value never printed
  out="$(bash "${BASH_SOURCE[0]}" --fail "$en" "$ex")"
  case "$out" in *sk-whatever*) fail=1 ;; esac
  # 6. secret with a NON-EMPTY locked default: compared silently, both values
  #    withheld (guards the deny-list invariant, not just empty placeholders)
  cat >"$tmp/sec.example" <<'EOF'
MSB_OPERATOR_TOKEN=sekrit-default-xyz
EOF
  cat >"$tmp/sec.env" <<'EOF'
MSB_OPERATOR_TOKEN=sk-actual-999
EOF
  out="$(bash "${BASH_SOURCE[0]}" --fail "$tmp/sec.env" "$tmp/sec.example")" || [ $? -eq 1 ] || fail=1
  case "$out" in *"MSB_OPERATOR_TOKEN differs"*) ;; *) fail=1 ;; esac
  case "$out" in *sekrit-default-xyz*|*sk-actual-999*) fail=1 ;; esac
  # 7. missing .env.example -> FAIL (exit 1)
  bash "${BASH_SOURCE[0]}" "$en" "$tmp/nope.example" >/dev/null 2>&1 && fail=1
  # 8. template leaking a secret value -> --template-secrets-block exits 1,
  #    names the key, and never prints the leaked value
  cat >"$tmp/leak.example" <<'EOF'
OPENAI_API_KEY=sk-committed-by-mistake-42
EOF
  out="$(bash "${BASH_SOURCE[0]}" --fail --template-secrets-block "$tmp/leak.example" "$tmp/leak.example")" || [ $? -eq 1 ] || fail=1
  case "$out" in *"carries a value for secret OPENAI_API_KEY"*) ;; *) fail=1 ;; esac
  case "$out" in *sk-committed-by-mistake-42*) fail=1 ;; esac
  rm -rf "$tmp"
  if [ "$fail" -eq 0 ]; then
    echo "[env-drift] selftest PASS"
    exit 0
  fi
  echo "[env-drift] selftest FAIL"
  exit 1
}
[ "$SELFTEST" = "1" ] && selftest

if [ ! -f "$ENV_FILE" ]; then
  echo "[env-drift] no $ENV_FILE — skipping (fresh clone / CI state is not drift)"
  exit 0
fi
if [ ! -f "$EXAMPLE_FILE" ]; then
  echo "[env-drift] FAIL: locked template $EXAMPLE_FILE missing"
  exit 1
fi

example_keys=()
example_vals=()
while IFS=$'\t' read -r key value; do
  example_keys+=("$key")
  example_vals+=("$value")
done < <(parse "$EXAMPLE_FILE")

seen_flags=()
warn_count=0
masked=()

# Template leak guard: a non-empty value for any SECRET_KEYS entry in the
# committed template ships that value to every checkout. CI (harness-gate.yml)
# generates .env from .env.example and runs --fail --template-secrets-block to
# enforce this — the one drift check that is NOT self-referential when the
# env is generated from the template. Values are never printed.
if [ "$TEMPLATE_SECRETS_BLOCK" = "1" ]; then
  for i in "${!example_keys[@]}"; do
    [ -n "${example_vals[$i]}" ] || continue
    if _is_secret "${example_keys[$i]}"; then
      echo "[env-drift] WARN: .env.example carries a value for secret ${example_keys[$i]} (leak risk — template must ship placeholders only)"
      warn_count=$((warn_count + 1))
    fi
  done
fi

while IFS=$'\t' read -r key value; do
  found=0
  for i in "${!example_keys[@]}"; do
    if [ "${example_keys[$i]}" = "$key" ]; then
      found=1
      seen_flags[$i]=1
      ex="${example_vals[$i]}"
      if [ -n "$ex" ]; then
        if [ "$value" != "$ex" ]; then
          if _is_secret "$key"; then
            # Never print secret values — compare silently, report the key.
            echo "[env-drift] WARN: $key differs (secret — values withheld)"
          else
            echo "[env-drift] WARN: $key differs — live='${value}' locked='${ex}'"
          fi
          warn_count=$((warn_count + 1))
        fi
      else
        # secret/optional placeholder that IS set — masked presence note only.
        masked+=("$key")
      fi
      break
    fi
  done
  # Vars in .env but not in the template are intentional extras, not drift.
done < <(parse "$ENV_FILE")

# Contract vars (non-empty locked default) missing from .env entirely.
for i in "${!example_keys[@]}"; do
  [ "${seen_flags[$i]:-0}" = "1" ] && continue
  ex="${example_vals[$i]}"
  [ -n "$ex" ] || continue
  key="${example_keys[$i]}"
  skip=0
  for c in "${CONDITIONAL_MISSING_OK[@]}"; do
    [ "$c" = "$key" ] && skip=1
  done
  [ "$skip" = "1" ] && continue
  if _is_secret "$key"; then
    echo "[env-drift] WARN: $key missing from .env (locked default applies — secret, withheld)"
  else
    echo "[env-drift] WARN: $key missing from .env (locked default '${ex}' would apply silently)"
  fi
  warn_count=$((warn_count + 1))
done

if [ "$warn_count" -gt 0 ]; then
  echo "[env-drift] $warn_count contract var(s) drift from the locked .env.example"
  if [ "$FAIL" = "1" ]; then
    exit 1
  fi
  echo "[env-drift] warn-only — use --fail / PORTABILITY_FAIL_ON_DRIFT=1 to block"
  exit 0
fi

if [ "${#masked[@]}" -gt 0 ]; then
  echo "[env-drift] secrets present (never compared, masked): ${masked[*]}"
fi
echo "[env-drift] clean — all contract vars match the locked .env.example"
exit 0
