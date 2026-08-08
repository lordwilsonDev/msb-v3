# Cross-agent commit verification hook — design

Status: approved (design), 2026-08-08
Scope note: unlike SMI-018 and `verify_build`, the artifacts this design
produces live **outside** `msb-v3` — a new shell script under
`~/.agents/hooks/`, plus edits to `~/.claude/settings.json`,
`~/.codex/hooks.json`, `~/.cursor/hooks.json`, and `~/.hermes/config.yaml`.
This spec lives in `msb-v3` because it directly extends SMI-018's lineage
and was produced investigating an `msb-v3` incident, but nothing here is
`msb-v3`-specific — it protects any repo that adopts the same
`smi-018-claim` pattern.

## Origin

Forensic investigation (2026-08-08) of the `dd66dd3` incident — a commit
fabricating "Phase 2" work on `msb-v3`'s `main` branch — traced the exact
cause to a Hermes Agent session (`20260806_000400_8f5508`, model
`stepfun/step-3.7-flash:free`) that: at `14:03:23` located the real work in
a different repo (`~/sovereign-agent-factory`), at `14:03:29` **explicitly
confirmed** the content did not exist in `msb-v3`, then committed
equivalent content there anyway at `14:03:53` — 24 seconds later. SMI-018
(`scripts/verify_claims.py`) already existed and would have caught this,
but only runs in CI, asynchronously, after a commit already lands. The gap
is timing, not detection capability.

## Purpose

Close that timing gap: run SMI-018-style verification **synchronously,
before a commit is allowed to complete**, for any agent tool that supports
pre-tool-call hooks. Reuse `~/.agents/hooks/` — a cross-agent hook
directory Wilson already built for `deny-dangerous.sh` (a catastrophic-command
guard) — rather than building a new, tool-specific mechanism.

## A finding that changed this design's scope

`deny-dangerous.sh`'s own header comment claims it's wired into Claude
Code, Codex, and Cursor. Checking the actual configs: `~/.claude/settings.json`
and `~/.claude/settings.local.json` both have **no `hooks` key at all**;
`~/.codex/hooks.json` and `~/.cursor/hooks.json` **don't exist**. Only
Hermes has real, confirmed wiring (`~/.hermes/config.yaml`'s
`hooks.pre_tool_call` list, verified via `~/.hermes/shell-hooks-allowlist.json`'s
approval record). So the "cross-agent" guard has, in practice, only ever
protected Hermes — the one tool that went on to cause the incident this
design responds to. Re-wiring the dead configs is therefore in scope, not
a separate follow-up.

## Non-goals

- Not a general "run tests before every commit" gate. Scoped tightly to
  the actual failure mode found: false claims in `docs/**/*.md`. A repo
  without `scripts/verify_claims.py` is completely unaffected.
- Not a replacement for CI. SMI-018's CI job stays as the backstop for
  anything that reaches a push without going through a hooked local tool
  (e.g., a direct `git push` from an unhooked environment).
- Not adding new dependencies. The hook needs `jq` (already required by
  `deny-dangerous.sh`) and `git`/`python3` on `PATH`.

## Hook script: `~/.agents/hooks/require-verified-claims.sh`

Same contract as `deny-dangerous.sh`, so every tool already wired to that
script works with zero per-tool special-casing:

- **Input:** hook JSON on stdin. Command via
  `.tool_input.command // .toolInput.command // .command` (same fallback
  chain `deny-dangerous.sh` uses for Claude/Codex/Grok/Cursor). `cwd` via
  `.cwd // empty` — confirmed present in the hook JSON (`deny-dangerous.sh`'s
  own test harness constructs `{tool_input:{command:$c},cwd:"/tmp"}`).
- **Mode:** `MODE="${1:-exitcode}"`. Default mode blocks via `exit 2` +
  reason on stderr. `cursor` mode blocks via `{"permission":"deny",...}`
  JSON on stdout, exit 0. Allow is silent `exit 0` (default) or
  `{"permission":"allow"}` (cursor).
- **`export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"`**
  — copied verbatim from `deny-dangerous.sh` for consistency.

### Decision logic

```
no jq available?                          -> allow (can't inspect command)
command doesn't contain `git commit`?      -> allow
                                               (substring/regex match against
                                               the full command string, same
                                               as deny-dangerous.sh's own
                                               grep -qE approach -- catches
                                               `git add -A && git commit ...`
                                               chains, not just a bare
                                               standalone `git commit`)
cwd doesn't resolve to a git repo?         -> allow
                                               (git -C "$cwd" rev-parse --show-toplevel)
scripts/verify_claims.py absent at root?   -> allow (repo hasn't opted in)
nothing under docs/** in
  `git diff --cached --name-only`?         -> allow (zero latency on code-only commits)
verify_claims.py crashes / can't run?      -> allow (environment problem,
                                               fail open -- matches
                                               deny-dangerous.sh's own
                                               "can't determine -> don't
                                               break agents" philosophy)
verify_claims.py runs, report has
  non-empty "failures"?                    -> BLOCK
verify_claims.py runs clean?               -> allow, silent
```

**Fail-open vs. fail-closed, stated explicitly:** every condition that
means "we couldn't determine whether this is safe" fails open (allow).
The only condition that fails closed is "we determined, for real, that a
claim in this commit doesn't check out." This mirrors `deny-dangerous.sh`'s
own stated principle exactly — a new safety mechanism that can itself
become a new source of breakage (blocking unrelated commits because of an
environment hiccup) would be worse than not having it.

### Block message

Built directly from `verify_claims.py`'s own JSON report — for each entry
in `failures`: the claim `id`, the `doc` it's in, and its
`missing_files`/`missing_tests`. Followed by one closing line: *"Either
create what's missing in this repo, or remove this claim block if it
describes work from a different project."* This is deliberately written
to give an agent like the one that produced `dd66dd3` a real next action,
not just a denial — the incident's actual failure was choosing "commit
anyway" over either of those two honest options.

## Per-tool wiring

| Tool | Current state | Action |
|---|---|---|
| Hermes | Real, working (`hooks.pre_tool_call` in `config.yaml`) | Append `require-verified-claims.sh` alongside the existing `deny-dangerous.sh` entry. `hooks_auto_accept: true` already set, no extra approval friction. |
| Claude Code | `hooks` key absent from both `settings.json` and `settings.local.json` | Add both `deny-dangerous.sh` (re-wiring) and the new hook to `PreToolUse` for the `Bash` matcher, via the `update-config` skill rather than hand-written JSON — that skill exists specifically to apply the current correct schema. |
| Codex | `~/.codex/hooks.json` doesn't exist | Create it. Schema not yet confirmed from memory — verify against Codex's actual hook documentation at implementation time rather than guessing. |
| Cursor | `~/.cursor/hooks.json` doesn't exist | Create it. Same caveat as Codex — verify the real schema before writing, don't assume. |

## Testing

Extend `~/.agents/hooks/test-guard.sh`'s existing pattern (same `check()`
harness, both payload shapes) with new cases: real repo + passing claim →
allow; claim with a missing file → block, message contains the claim id
and the missing path; no `docs/**` staged → allow even with a failing
claim sitting unstaged elsewhere; repo without `scripts/verify_claims.py`
→ allow; command isn't `git commit` at all → allow.

Beyond the shell-harness tests: one live end-to-end check per newly-wired
tool (Hermes and Claude Code at minimum) — actually trigger the hook for
real through each tool, the way `verify_build` was verified for real
earlier today rather than trusting a mocked test alone. A hook that only
passes its own test harness but was never actually triggered through the
real tool is exactly the kind of unverified "should work" claim this
entire day has been about eliminating.

## Acceptance criteria

- [ ] `require-verified-claims.sh` passes its `test-guard.sh`-style cases.
- [ ] Re-running the exact `dd66dd3` scenario (a claim block for files
      that don't exist in the target repo, staged under `docs/**`, then
      `git commit`) is blocked, live, through at least one real tool.
- [ ] A normal code-only commit (no `docs/**` staged) in the same repo is
      unaffected — verified live, not just by the shell harness.
- [ ] Hermes, Claude Code, Codex, and Cursor all have both
      `deny-dangerous.sh` and `require-verified-claims.sh` wired in,
      confirmed by triggering each live where practical.
