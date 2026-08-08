# Cross-Agent Commit Verification Hook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block `git commit` (across every agent tool that supports pre-tool-call hooks) when a staged `docs/**/*.md` file contains an `smi-018-claim` block whose referenced files/tests don't actually exist — closing the timing gap that let the `dd66dd3` incident land (SMI-018 already detects this, but only in CI, after the commit already exists).

**Architecture:** One new shell script, `~/.agents/hooks/require-verified-claims.sh`, built to the exact stdin/stdout contract already proven by the sibling `~/.agents/hooks/deny-dangerous.sh` script. It runs `scripts/verify_claims.py` synchronously inside the hook, before the commit is allowed to complete, and is wired into every tool that already has (or should have) `deny-dangerous.sh` wired in: Hermes (real, working config), Claude Code (config exists but the `hooks` key is currently empty — re-wiring both scripts is part of this work), and Codex/Cursor (no hook config files exist yet on this machine).

**Tech Stack:** Bash, `jq` (already a hard dependency of the sibling script), `git`, `python3` (to invoke `verify_claims.py`, which is pure stdlib per SMI-018's own constraints).

## Global Constraints

- The new script must use the exact same stdin/stdout contract as `~/.agents/hooks/deny-dangerous.sh`: command via `.tool_input.command // .toolInput.command // .command`, `cwd` via `.cwd // empty`, `MODE="${1:-exitcode}"`, block = `exit 2` + stderr (default) or `{"permission":"deny",...}` JSON (cursor mode), allow = silent `exit 0` (default) or `{"permission":"allow"}` (cursor mode).
- Fail open (allow) on: missing `jq`; command doesn't contain `git commit` (substring/regex match, not exact-equality — must catch `git add -A && git commit ...` chains); `cwd` empty or not a git repo; `scripts/verify_claims.py` absent from the repo root; nothing under `docs/**` in `git diff --cached --name-only`; `verify_claims.py` exits with anything other than 0 (clean) or 1 (real failures, report written) — specifically, exit 2 (bad docs_root) or a crash both fail open, since they mean "couldn't determine," not "determined unsafe."
- Fail closed (block) only when `verify_claims.py` exits 1 with a non-empty `failures` list in its report.
- Zero new dependencies beyond what `deny-dangerous.sh` already requires (`jq`).
- Full design rationale: `docs/superpowers/specs/2026-08-08-cross-agent-commit-verification-hook-design.md`.
- Codex CLI is currently **broken** on this machine (`codex --help` fails with `ENOENT` — the vendor binary for this platform is missing from the npm install). Cursor has no CLI on `PATH` at all (GUI app). Neither can be live-tested from this session — their tasks are explicitly scoped to "best-effort correct config, not live-verified," and must say so plainly rather than claim verification that didn't happen.

---

### Task 1: Build and test `require-verified-claims.sh` standalone

**Files:**
- Create: `~/.agents/hooks/require-verified-claims.sh`
- Create: `~/.agents/hooks/test-require-verified-claims.sh` (new file, mirrors `~/.agents/hooks/test-guard.sh`'s `check()` pattern rather than modifying that file, since it's specifically documented as "Test harness for deny-dangerous.sh" — a separate harness for a separate script keeps each test file single-purpose)

**Interfaces:**
- Produces: the hook script itself, invocable as `echo '<json>' | require-verified-claims.sh [cursor]`, exit 0/2 or JSON on stdout per the Global Constraints contract above. No other task depends on internal functions — every later task treats this script as an opaque binary invoked by each tool's hook mechanism.

- [ ] **Step 1: Write the failing tests**

Create `~/.agents/hooks/test-require-verified-claims.sh`:

```bash
#!/bin/bash
# Test harness for require-verified-claims.sh.
# Builds a real throwaway git repo with a real scripts/verify_claims.py
# and exercises the hook against real `git commit` scenarios -- not
# synthetic JSON alone, since the hook's actual logic depends on real
# git state (staged files) and a real verify_claims.py subprocess run.
# Usage: ~/.agents/hooks/test-require-verified-claims.sh

set -uo pipefail

HOOK="$HOME/.agents/hooks/require-verified-claims.sh"
pass=0
fail=0

# --- fixture: a throwaway repo with a real (copied) verify_claims.py ---
FIXTURE_ROOT=$(mktemp -d -t require-verified-claims-fixture.XXXXXX)
cleanup() { rm -rf "$FIXTURE_ROOT"; }
trap cleanup EXIT

setup_repo() {
  local repo="$1"
  mkdir -p "$repo/scripts" "$repo/docs"
  cp "$HOME/msb-v3/scripts/verify_claims.py" "$repo/scripts/verify_claims.py"
  (cd "$repo" && git init -q && git config user.email t@t.com && git config user.name t)
}

check() { # $1 = expected: block|allow, $2 = repo dir, $3 = command string
  local expected="$1" repo="$2" cmd="$3" rc out verdict

  jq -cn --arg c "$cmd" --arg cwd "$repo" '{tool_input:{command:$c},cwd:$cwd}' | "$HOOK" >/dev/null 2>&1
  rc=$?
  if [ "$rc" -eq 2 ]; then verdict="block"; else verdict="allow"; fi
  if [ "$verdict" = "$expected" ]; then
    pass=$((pass+1))
  else
    fail=$((fail+1))
    echo "FAIL [claude/codex] expected=$expected got=$verdict repo=$repo : $cmd"
  fi

  out=$(jq -cn --arg c "$cmd" --arg cwd "$repo" '{command:$c,cwd:$cwd}' | "$HOOK" cursor 2>/dev/null)
  case "$out" in
    *'"deny"'*) verdict="block" ;;
    *'"allow"'*) verdict="allow" ;;
    *) verdict="invalid-output" ;;
  esac
  if [ "$verdict" = "$expected" ]; then
    pass=$((pass+1))
  else
    fail=$((fail+1))
    echo "FAIL [cursor] expected=$expected got=$verdict repo=$repo : $cmd"
  fi
}

# ---- case: passing claim -> allow ----
REPO="$FIXTURE_ROOT/passing"
setup_repo "$REPO"
cat > "$REPO/docs/claim.md" <<'EOF'
```smi-018-claim
id: real-thing
status: implemented
files:
  - scripts/verify_claims.py
```
EOF
(cd "$REPO" && git add -A)
check allow "$REPO" "git commit -m test"

# ---- case: claim with a missing file -> block ----
REPO="$FIXTURE_ROOT/failing"
setup_repo "$REPO"
cat > "$REPO/docs/claim.md" <<'EOF'
```smi-018-claim
id: fake-thing
status: implemented
files:
  - does_not_exist.py
```
EOF
(cd "$REPO" && git add -A)
check block "$REPO" "git commit -m test"
check block "$REPO" "git add -A && git commit -m test"

# ---- case: failing claim exists but NOT staged -> allow ----
REPO="$FIXTURE_ROOT/unstaged"
setup_repo "$REPO"
cat > "$REPO/docs/claim.md" <<'EOF'
```smi-018-claim
id: fake-thing
status: implemented
files:
  - does_not_exist.py
```
EOF
(cd "$REPO" && git add scripts/verify_claims.py)
check allow "$REPO" "git commit -m test"

# ---- case: no scripts/verify_claims.py in repo -> allow ----
REPO="$FIXTURE_ROOT/no-tooling"
mkdir -p "$REPO/docs"
(cd "$REPO" && git init -q && git config user.email t@t.com && git config user.name t)
cat > "$REPO/docs/claim.md" <<'EOF'
```smi-018-claim
id: fake-thing
status: implemented
files:
  - does_not_exist.py
```
EOF
(cd "$REPO" && git add -A)
check allow "$REPO" "git commit -m test"

# ---- case: not a git commit command -> allow ----
REPO="$FIXTURE_ROOT/passing"
check allow "$REPO" "git status"
check allow "$REPO" "ls -la"

echo ""
echo "passed: $pass, failed: $fail"
[ "$fail" -eq 0 ] || exit 1
```

- [ ] **Step 2: Run the test harness to verify it fails**

Run: `chmod +x ~/.agents/hooks/test-require-verified-claims.sh && ~/.agents/hooks/test-require-verified-claims.sh`
Expected: script fails immediately with `No such file or directory` — `require-verified-claims.sh` doesn't exist yet.

- [ ] **Step 3: Write the hook script**

Create `~/.agents/hooks/require-verified-claims.sh`:

```bash
#!/bin/bash
# Global commit-verification guard.
# Blocks `git commit` when a staged docs/**/*.md file contains an
# smi-018-claim block whose referenced files/tests don't actually exist.
# Fails open on anything we can't determine (missing jq, cwd not a git
# repo, no scripts/verify_claims.py in that repo, no docs/** staged,
# verify_claims.py itself erroring) -- the only fail-closed condition is
# verify_claims.py running successfully and reporting real failures.
# Full rationale (the dd66dd3 incident): msb-v3's
# docs/superpowers/specs/2026-08-08-cross-agent-commit-verification-hook-design.md
#
# Used by (same pattern as deny-dangerous.sh):
#   Claude Code  ~/.claude/settings.json  PreToolUse (matcher Bash)
#   Codex        ~/.codex/hooks.json      PreToolUse (matcher Bash)
#   Cursor       ~/.cursor/hooks.json     beforeShellExecution (arg: cursor)
#   Hermes       ~/.hermes/config.yaml    hooks.pre_tool_call
#
# stdin:  hook JSON. Claude/Codex put the command at .tool_input.command,
#         Cursor at .command. cwd at .cwd for all.
# Block:  default mode -> exit 2 + reason on stderr (Claude/Codex contract).
#         "cursor" mode -> {"permission":"deny",...} JSON on stdout, exit 0.
# Allow:  default mode -> exit 0, silent. cursor mode -> {"permission":"allow"}.

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
MODE="${1:-exitcode}"

allow() {
  [ "$MODE" = "cursor" ] && printf '{"permission":"allow"}\n'
  exit 0
}

block() {
  local msg="$1"
  if [ "$MODE" = "cursor" ]; then
    jq -cn --arg m "$msg" '{permission:"deny", user_message:"Commit blocked: an unverified claim in docs/.", agent_message:$m}'
    exit 0
  fi
  echo "$msg" >&2
  exit 2
}

command -v jq >/dev/null 2>&1 || allow

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // .toolInput.command // .command // empty' 2>/dev/null)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)

[ -z "$CMD" ] && allow
printf '%s\n' "$CMD" | grep -qE 'git[[:space:]]+commit' || allow
[ -z "$CWD" ] && allow

REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null) || allow
VERIFY_SCRIPT="$REPO_ROOT/scripts/verify_claims.py"
[ -f "$VERIFY_SCRIPT" ] || allow

STAGED_DOCS=$(git -C "$REPO_ROOT" diff --cached --name-only 2>/dev/null | grep -c '^docs/')
[ "${STAGED_DOCS:-0}" -gt 0 ] 2>/dev/null || allow

REPORT_PATH=$(mktemp -t verify_claims_report.XXXXXX.json)
python3 "$VERIFY_SCRIPT" "$REPO_ROOT/docs" --report-path "$REPORT_PATH" >/dev/null 2>&1
code=$?

if [ "$code" -ne 0 ]; then
  if [ "$code" -ne 1 ] || [ ! -f "$REPORT_PATH" ]; then
    rm -f "$REPORT_PATH"
    allow
  fi
fi

FAILURES=$(jq -c '.failures // []' "$REPORT_PATH" 2>/dev/null)
rm -f "$REPORT_PATH"

if [ -z "$FAILURES" ] || [ "$FAILURES" = "[]" ]; then
  allow
fi

MSG=$(printf '%s' "$FAILURES" | jq -r '
  ["This commit includes a claim in docs/ that does not check out:"] +
  (map(
    "- id=" + (.id // "unknown") + " doc=" + .doc +
    (if (.error // "") != "" then " error=" + .error else "" end) +
    (if (.missing_files // []) != [] then " missing_files=" + (.missing_files | join(", ")) else "" end) +
    (if (.missing_tests // []) != [] then " missing_tests=" + (.missing_tests | join(", ")) else "" end)
  )) +
  ["Either create what is missing in this repo, or remove this claim block if it describes work from a different project."]
  | join("\n")
')

block "$MSG"
```

- [ ] **Step 4: Run the test harness to verify it passes**

Run: `chmod +x ~/.agents/hooks/require-verified-claims.sh && ~/.agents/hooks/test-require-verified-claims.sh`
Expected: `passed: 14, failed: 0` — 7 `check` calls above (1 passing + 2 failing + 1 unstaged + 1 no-tooling + 2 not-a-commit), each producing 2 assertions (claude/codex shape + cursor shape) = 14. If the printed count differs from 14, first recount the actual `check` calls in the file on disk before assuming the script is wrong — the arithmetic here was verified by `grep -c "^check " ` against the file, not computed by hand.

- [ ] **Step 5: Commit**

```bash
cd ~/.agents/hooks
git init -q 2>/dev/null || true
```

Note: `~/.agents/hooks/` is not currently a git repository (confirmed during design). Do not initialize git history for it as part of this plan unless Wilson asks — these files are personal machine config, not a project artifact, and versioning them is a separate decision. Skip any `git add`/`git commit` for this task; the files themselves, created and executable, are the deliverable. Verify with:

```bash
ls -la ~/.agents/hooks/require-verified-claims.sh ~/.agents/hooks/test-require-verified-claims.sh
```

Expected: both present, `require-verified-claims.sh` executable (`-rwxr-xr-x`).

---

### Task 2: Wire into Hermes, verify live

**Files:**
- Modify: `~/.hermes/config.yaml` (single line addition to the existing `hooks.pre_tool_call` list)

**Interfaces:**
- Consumes: `~/.agents/hooks/require-verified-claims.sh` from Task 1 (must exist and be executable).

- [ ] **Step 1: Back up the config**

```bash
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak-pre-verify-hook-$(date +%Y%m%d%H%M%S)
```

- [ ] **Step 2: Add the hook entry**

The existing block (confirmed present at this session) reads:

```yaml
hooks_auto_accept: true
hooks:
  pre_tool_call:
    - command: /Users/lordwilson/.agents/hooks/deny-dangerous.sh
```

Change it to:

```yaml
hooks_auto_accept: true
hooks:
  pre_tool_call:
    - command: /Users/lordwilson/.agents/hooks/deny-dangerous.sh
    - command: /Users/lordwilson/.agents/hooks/require-verified-claims.sh
```

Use a targeted edit (e.g. a Python `ruamel.yaml`/manual line-splice, or a careful text edit) that touches only these lines — `config.yaml` is a large (8KB+), sensitive file (contains provider API keys elsewhere in the same file per its restricted permissions); do not rewrite the whole file with a generic YAML dump, which could reformat or reorder unrelated sensitive content. A simple line-anchored insertion (find the `deny-dangerous.sh` line, insert the new line immediately after it, preserving every other byte) is the safe approach.

- [ ] **Step 3: Verify the YAML is still valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('/Users/lordwilson/.hermes/config.yaml'))" && echo "valid YAML"
```

Expected: `valid YAML`, no exception.

- [ ] **Step 4: Live end-to-end verification through Hermes**

This is the real test — reproduce the actual `dd66dd3` shape and confirm Hermes itself now gets blocked, not just the shell harness. Hermes needs to be running (it has a gateway process; confirm via `cat ~/.hermes/gateway.pid` and `ps -p $(cat ~/.hermes/gateway.pid)`) and its config reloaded — check whether Hermes hot-reloads `config.yaml` or needs a restart (inspect `~/.hermes/logs/gateway.log` after touching the file for a reload message, or consult Hermes's own restart mechanism if hot-reload isn't evident within a reasonable wait).

Once the new hook is confirmed active: through an actual Hermes session (not simulated), attempt a `git commit` in a throwaway test repo (not `msb-v3` — use a fresh `mktemp -d` repo with a copied `scripts/verify_claims.py` and a deliberately-false claim, mirroring Task 1's fixture) and confirm Hermes reports the commit was blocked, surfacing the hook's message. Then attempt a passing-claim commit in the same repo and confirm it succeeds. Document both outcomes (the literal blocked/allowed result) before marking this task done — a hook that only passes its own shell harness but was never triggered through the real tool doesn't meet this plan's acceptance bar.

- [ ] **Step 5: Report done**

No git commit for this step (see Task 1 Step 5's note — `~/.hermes/` isn't a project repo either). The verified, working config change and the live test transcript are the deliverable.

---

### Task 3: Wire into Claude Code via the update-config skill, verify live

**Files:**
- Modify: `~/.claude/settings.json` (via the `update-config` skill, not hand-edited — that skill exists specifically to apply the correct current hook schema)

**Interfaces:**
- Consumes: `~/.agents/hooks/require-verified-claims.sh` and `~/.agents/hooks/deny-dangerous.sh` (both must exist; `deny-dangerous.sh` already does, `require-verified-claims.sh` from Task 1).

- [ ] **Step 1: Invoke the update-config skill**

Ask it to configure a `PreToolUse` hook for the `Bash` matcher in `~/.claude/settings.json`, running both `~/.agents/hooks/deny-dangerous.sh` and `~/.agents/hooks/require-verified-claims.sh` in sequence (deny-dangerous first, since a catastrophic-command check should short-circuit before a slower verification check runs). State plainly to the skill that `settings.json` currently has an empty `hooks` key (confirmed this session) — this is a net-new addition, not an edit to existing hook config.

- [ ] **Step 2: Verify the resulting config is valid JSON and matches intent**

```bash
python3 -c "import json; d=json.load(open('/Users/lordwilson/.claude/settings.json')); print(json.dumps(d.get('hooks', {}), indent=2))"
```

Expected: a non-empty `PreToolUse` structure referencing both scripts for the `Bash` matcher. Confirm this by eye against what Step 1 actually produced — don't assume, read the real output.

- [ ] **Step 3: Live end-to-end verification through this Claude Code session**

This hook will apply to the current session's own Bash tool calls once active (Claude Code hook config typically takes effect on next tool call, not requiring a full session restart — confirm this assumption is true by direct observation in this step, not by assuming). Using the same throwaway-repo fixture pattern as Task 1/2 (a fresh `mktemp -d` repo, copied `scripts/verify_claims.py`, a deliberately false claim staged), attempt `git commit` via this session's own Bash tool and confirm it's actually blocked with the expected message. Then commit a passing claim in the same repo and confirm it succeeds. Both outcomes must be observed directly, not assumed from the config being syntactically correct.

- [ ] **Step 4: Report done**

Document both live results (blocked case + allowed case, with the actual tool output shown) as the deliverable for this task.

---

### Task 4: Research and wire Codex + Cursor (best-effort, not live-verified)

**Files:**
- Create: `~/.codex/hooks.json`
- Create: `~/.cursor/hooks.json`

**Interfaces:**
- Consumes: `~/.agents/hooks/require-verified-claims.sh` and `~/.agents/hooks/deny-dangerous.sh` from Task 1 (already exists).

**Important constraint carried from Global Constraints:** Codex CLI is broken on this machine (`ENOENT` on its vendor binary) and Cursor has no local CLI at all. **Neither can be live-tested from this session.** This task's acceptance bar is different from Tasks 2-3: best-effort correct configuration, backed by real research (not memory/guessing), explicitly reported as unverified rather than claimed as working.

- [ ] **Step 1: Research Codex's real hook config schema**

Use WebSearch for the current, real schema (e.g. "OpenAI Codex CLI hooks.json PreToolUse schema"). Cross-check against anything findable in the installed npm package itself:

```bash
find /opt/homebrew/lib/node_modules/@openai/codex -iname "*hook*" 2>/dev/null
```

If the package ships schema/docs/example config, prefer that over web search results (more authoritative, matches the exact installed version). Do not write `~/.codex/hooks.json` until a real schema source has been found and read — if no authoritative source can be found, stop and report this back rather than guessing at a plausible-looking structure.

- [ ] **Step 2: Write `~/.codex/hooks.json`**

Following whatever schema Step 1 actually found, configure a hook for `git commit`-capable shell tool calls pointing at both `~/.agents/hooks/deny-dangerous.sh` and `~/.agents/hooks/require-verified-claims.sh`. Validate the file is syntactically valid JSON:

```bash
python3 -m json.tool ~/.codex/hooks.json
```

- [ ] **Step 3: Research Cursor's real hook config schema**

Same approach as Step 1, for Cursor (e.g. "Cursor editor hooks.json beforeShellExecution schema"). `deny-dangerous.sh`'s own header comment references a `cursor` invocation mode and a `beforeShellExecution` event name as prior art — treat that as a hint pointing at the right search terms, not as confirmed-correct schema on its own (it's a comment written by/for a previous setup, not verified fresh here).

- [ ] **Step 4: Write `~/.cursor/hooks.json`**

Same pattern as Step 2, validated the same way.

- [ ] **Step 5: Report status honestly**

State plainly, for both tools: what schema source was used (link/package path), that the resulting JSON is syntactically valid, and that **neither has been live-verified** — Wilson should test both himself next time he opens Codex (once its broken install is fixed) or Cursor, or ask a future session to verify once those tools are reachable. Do not report this task as fully "done" in the same sense as Tasks 2-3; report it as "configured, unverified" explicitly.

---

## Post-plan verification

- [ ] All four tools (Hermes, Claude Code confirmed live; Codex, Cursor configured but unverified) reference both `deny-dangerous.sh` and `require-verified-claims.sh`.
- [ ] `~/.agents/hooks/test-require-verified-claims.sh` passes in full.
- [ ] The exact `dd66dd3` scenario — a claim block for files that don't exist in the target repo, staged under `docs/**`, then `git commit` — is confirmed blocked live through at least Hermes and Claude Code (per Tasks 2 and 3's Step 4/3 respectively).
- [ ] A normal code-only commit (nothing under `docs/**` staged) in the same fixture repos is confirmed unaffected, live, not just via the shell harness.
- [ ] Every acceptance criterion in `docs/superpowers/specs/2026-08-08-cross-agent-commit-verification-hook-design.md`'s "Acceptance criteria" section is satisfied or explicitly marked as the honest exception (Codex/Cursor unverified, with the reason stated).
