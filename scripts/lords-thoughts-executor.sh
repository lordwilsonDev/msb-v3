#!/bin/bash
# lords-thoughts-executor.sh — plan runner with fail-closed command hardening.
#
# Executes pending plan files from the lords-thoughts plans dir. Each `- run:`
# step must be ONE allowlisted executable with plain arguments:
#   - the executable must be in ALLOWED_CMDS (and installed)
#   - the command must contain NO shell metacharacters (`; & | < > \` $( )`) —
#     no chaining, pipes, redirection, or command substitution
#   - the command must not match any BLOCKED_PATTERNS (never-operations)
# Anything else is REFUSED: the step fails, the plan is marked failed and
# archived, and the reason is logged. Fail-closed: we never guess.
#
# NOTE: allowlisted interpreters (python3/node) are Turing-complete — the
# allowlist is a guardrail layer, NOT a sandbox. Keep the plans dir
# write-protected from untrusted sources (that is the real security boundary).
#
# Overrides (for tests / alternative deployments):
#   LT_PLANS_DIR / LT_ARCHIVE_DIR / LT_LOGS_DIR
#   LT_ALLOWED_CMDS  (space-separated list of executables)
set -u

PLANS_DIR="${LT_PLANS_DIR:-$HOME/Documents/Vault/lords-thoughts/plans}"
ARCHIVE_DIR="${LT_ARCHIVE_DIR:-$HOME/Documents/Vault/lords-thoughts/archive}"
LOGS_DIR="${LT_LOGS_DIR:-$HOME/Documents/Vault/lords-thoughts/logs}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
LOG_FILE="$LOGS_DIR/$TIMESTAMP-exec.md"

mkdir -p "$LOGS_DIR" "$ARCHIVE_DIR"

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Executables a plan step may invoke. Everything else is refused. Extend via
# LT_ALLOWED_CMDS if a plan legitimately needs a tool not listed here.
IFS=' ' read -r -a ALLOWED <<< "${LT_ALLOWED_CMDS:-cat ls find grep rg sed awk head tail wc sort uniq cut tr echo printf jq python3 python node git curl wget mkdir touch cp mv date uuidgen shasum sha256sum md5 basename dirname}"

# Never-operations: matched as substrings against the raw command. These are
# refused even if the executable were allowlisted (defense in depth).
BLOCKED_PATTERNS=(
  'sudo' 'su ' 'passwd' 'chmod 777' 'chown'
  'dd if=' 'mkfs' 'diskutil' 'fdisk'
  'shutdown' 'reboot' 'halt' 'poweroff'
  'kill -9' 'pkill' 'killall'
  'git push' 'git reset --hard' 'git clean'
  'launchctl bootout' 'launchctl unload'
  'rm -rf /' 'rm -r /' 'rm -fr /' 'rm -f /' 'rm -rf ~' 'rm -rf $HOME'
  ':()'
)

# ── SAFETY CHECKS ─────────────────────────────────────────────────────────────
# Prints "OK" and returns 0 when the command is safe to eval; otherwise prints
# the refusal reason and returns 1.
check_command() {
  local cmd="$1"
  local exe="" rest=""
  read -r exe rest <<< "$cmd"
  if [ -z "$exe" ]; then
    echo "empty command"
    return 1
  fi

  local pat
  for pat in "${BLOCKED_PATTERNS[@]}"; do
    if printf '%s' "$cmd" | grep -Fq -- "$pat"; then
      echo "blocked pattern: $pat"
      return 1
    fi
  done

  # No shell chaining / pipes / redirection / command substitution / backticks.
  if printf '%s' "$cmd" | grep -Eq '[;&|`<>]|\$\('; then
    echo "shell metacharacters are not allowed (no chaining, pipes, redirection, or substitution)"
    return 1
  fi

  local allowed=0 a
  for a in "${ALLOWED[@]}"; do
    [ "$a" = "$exe" ] && allowed=1
  done
  if [ "$allowed" -ne 1 ]; then
    echo "executable not allowlisted: $exe"
    return 1
  fi

  if ! command -v "$exe" >/dev/null 2>&1; then
    echo "executable not installed: $exe"
    return 1
  fi

  echo "OK"
  return 0
}

# ── STEP RUNNER ───────────────────────────────────────────────────────────────
run_step() {
  local line="$1"
  local step_num="$2"

  if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*run:[[:space:]]*(.+)$ ]]; then
    local cmd="${BASH_REMATCH[1]}"
    local verdict
    verdict=$(check_command "$cmd")
    if [ "$verdict" != "OK" ]; then
      echo "- Step $step_num: REFUSED ($verdict): \`$cmd\`" >> "$LOG_FILE"
      echo "  -> plan failed closed; fix the step before it will run" >> "$LOG_FILE"
      echo "" >> "$LOG_FILE"
      return 1
    fi
    echo "- Step $step_num: running command: $cmd" >> "$LOG_FILE"
    output=$(eval "$cmd" 2>&1)
    rc=$?
    echo "- Step $step_num: exit code: $rc" >> "$LOG_FILE"
    echo '```' >> "$LOG_FILE"
    echo "$output" >> "$LOG_FILE"
    echo '```' >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"
    return $rc
  elif [[ "$line" =~ ^[[:space:]]*-[[:space:]]*wait:[[:space:]]*(.+)$ ]]; then
    local dur="${BASH_REMATCH[1]}"
    if [[ "$dur" =~ ^[0-9]+[smhd]?$ ]]; then
      echo "- Step $step_num: waiting $dur" >> "$LOG_FILE"
      sleep "$dur"
      echo "- Step $step_num: wait complete" >> "$LOG_FILE"
    else
      echo "- Step $step_num: REFUSED (invalid wait duration: $dur)" >> "$LOG_FILE"
      return 1
    fi
    echo "" >> "$LOG_FILE"
    return 0
  elif [[ "$line" =~ ^[[:space:]]*-[[:space:]]*log:[[:space:]]*(.+)$ ]]; then
    local msg="${BASH_REMATCH[1]}"
    echo "- Step $step_num: $msg" >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"
    return 0
  else
    echo "- Step $step_num: skipping unsupported: $line" >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"
    return 0
  fi
}

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
processed=0
failed=0

if [ -z "$(ls -A "$PLANS_DIR" 2>/dev/null)" ]; then
  # No plans in the queue — skip writing a log file (avoids 5-minute log spam).
  exit 0
fi

for plan in "$PLANS_DIR"/*.md; do
  [ -f "$plan" ] || continue
  filename=$(basename "$plan")

  status=$(grep -m1 '^status:' "$plan" | sed 's/status:[[:space:]]*//' | tr -d '\r')
  if [ "$status" != "pending" ]; then
    continue
  fi

  processed=$((processed + 1))

  echo "# Lords Thoughts Executor Log — $TIMESTAMP" > "$LOG_FILE"
  echo "" >> "$LOG_FILE"
  echo "## Plan: $filename" >> "$LOG_FILE"
  echo "- status: pending → in_progress" >> "$LOG_FILE"
  echo "- started: $TIMESTAMP" >> "$LOG_FILE"
  echo "" >> "$LOG_FILE"

  # Update plan status (portable in-place edit: `sed -i ''` is BSD-only and
  # breaks on GNU sed/Linux — sed to a temp file, then mv over).
  sed 's/^status:.*/status: in_progress/' "$plan" > "$plan.tmp" && mv "$plan.tmp" "$plan"

  # Execute steps
  step_num=0
  failed_steps=0
  while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]*-[[:space:]]+(run|wait|log): ]]; then
      step_num=$((step_num + 1))
      run_step "$line" "$step_num"
      rc=$?
      if [ $rc -ne 0 ]; then
        failed_steps=$((failed_steps + 1))
      fi
    fi
  done < "$plan"

  if [ $failed_steps -eq 0 ]; then
    sed 's/^status:.*/status: completed/' "$plan" > "$plan.tmp" && mv "$plan.tmp" "$plan"
    echo "- finished: $TIMESTAMP" >> "$LOG_FILE"
    echo "- result: success" >> "$LOG_FILE"
    mv "$plan" "$ARCHIVE_DIR/"
  else
    sed 's/^status:.*/status: failed/' "$plan" > "$plan.tmp" && mv "$plan.tmp" "$plan"
    echo "- finished: $TIMESTAMP" >> "$LOG_FILE"
    echo "- result: failed ($failed_steps step(s) failed)" >> "$LOG_FILE"
    mv "$plan" "$ARCHIVE_DIR/"
  fi

  echo "" >> "$LOG_FILE"
done

if [ $processed -eq 0 ]; then
  # All plans already in_progress/completed — skip writing a log file.
  :
fi
