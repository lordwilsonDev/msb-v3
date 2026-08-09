# Handoff — read this first

Session stopped mid-work (context window exhausted). Everything below is
real, verified state as of 2026-08-08, not speculation. Delete this file
once you've read it and folded its open items into your own tracking.

## What today built (all real, tested, mostly done)

1. **SMI-018** (`scripts/verify_claims.py`) — a CI gate that checks
   structured claim blocks in docs against real files on disk. Fully
   built, tested, reviewed. **NOT MERGED TO MAIN** — only exists on branch
   `smi-018-implementation` at
   `.claude/worktrees/smi-018-implementation`. The `finishing-a-development-branch`
   3-option menu (merge/PR/keep-as-is) was presented and never answered —
   work moved on to other things. **Do this first**, most other things
   depend on it (see below).

2. **`verify_build`** — an MCP tool in `src/msb_v3/api/mcp_bridge.py`
   (committed to main) that any process can call to verify a build claim
   before it's echoed anywhere. Done, live, verified.

3. **Cross-agent commit-verification hook** — the big unfinished piece.
   Full details, exact findings, exact next action:
   `.superpowers/sdd/2026-08-08-cross-agent-commit-verification-hook/progress.md`
   — read the section headed `=== SESSION STOPPED HERE ===` near the
   bottom. Short version: 4 tasks built and reviewed (a real hook script
   at `~/.agents/hooks/require-verified-claims.sh`, wired into Hermes,
   Claude Code, Codex, Cursor), but the **final whole-branch review found
   the hook cannot actually fire through a real Hermes gateway session**
   (Hermes sends the hook its own gateway-process cwd, not the real
   session's cwd — a Hermes-side bug, not this session's). Wilson already
   said yes to patching Hermes's own source to fix it (via
   AskUserQuestion, last confirmed decision). That fix — plus two smaller
   related bugs (one stale claim anywhere blocks all docs commits; a
   `cd <repo> && git commit` chain bypasses detection) — was about to be
   dispatched as the one allowed final-review fix wave when the session
   had to stop. **This is the actual next action**, not a new task.

## Also from today, resolved

- Found and fixed 3 real path-traversal vulnerabilities in
  `mcp_bridge.py`, `business/registry.py`, `api/tenants.py` — committed.
- Found and fixed a supervisor bug in `scripts/run.sh` (crash-recovery
  loop never actually recovered) — committed, live server confirmed
  healthy.
- Investigated and fully explained the `dd66dd3` incident (a fabricated
  "Phase 2" commit on `main` from earlier the same day) — traced to a
  specific Hermes session, root cause fully documented in
  `docs/audits/smi-017-forensic-review/RECONCILIATION.md`.
- **Disk was at 99% full** — cleared to healthy (~8GB freed from safe
  caches only). Not an issue anymore, just noting in case it recurs.
- `~/.claude.json`'s `msb-v3` MCP server entry was missing
  `MCP_BRIDGE_SECRET` — fixed (backed up first).

## Do not re-litigate

- The `docs/audits/phase2_architecture_audit/` docs on `main` (from
  commit `dd66dd3`) are confirmed fabricated — don't trust anything in
  them. The real, verified audit is
  `docs/audits/smi-017-forensic-review/`.
- There may still be an unidentified, occasionally-active concurrent
  process touching this repo (separate from Hermes, separate from you) —
  seen dropping uncommitted files under `docs/audits/` earlier today. If
  you see unfamiliar uncommitted changes, don't assume they're yours or
  safe — verify before trusting, same as everything else today.
