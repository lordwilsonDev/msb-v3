# MSB v3 — Consolidated Outstanding Worklist

**Owner:** Wilson · **Author:** Buffy · **Date:** 2026-08-16 · **Purpose:** every outstanding item in one place, each with a destination (wire/cut/park), an owner, and exit evidence. Checked items are DONE with evidence. This is the master tracker for the Convergence-to-12 program.

Legend: ✅ done · 🟡 in progress · 🔴 not started · ⏸ blocked on operator/hardware/infra (documented, not code)

---

## A. Governance (M2 — in progress)

| # | Item | Destination | Exit evidence | Status |
|---|---|---|---|---|
| A1 | Bypass regression suite (direct invocation, alternate callers, replay/retry) | WIRE | `tests/governance/test_bypass.py` (13 tests) green | ✅ d44eb20 |
| A2 | `ACTIONGATE_DECISIONS` metrics (allowed/denied/indeterminate/failed) | WIRE | metric registered + asserted in tests | ✅ d44eb20 |
| A3 | MCP `chat` surface is governed (not a thin proxy) | VERIFY+WIRE | `tests/api/test_mcp_chat_governance.py` — chat harness registers tools through `register_governed_tools`; denied tool → no side effect; unknown tools never registered | ✅ |
| A4 | Gate verdict recorded in MCP audit chain | WIRE | `_audit_append` carries explicit `verdict` (allowed/denied/approval-required/unknown/error); asserted in `test_governed_tool_loop.py` + `test_mcp_chat_governance.py` | ✅ |
| A5 | Live-loop composition test (one request through the whole spine) | WIRE | `tests/live/test_live_loop.py` (3 tests, opt-in `MSB_LIVE_TESTS=1`): auth refusal before anything runs, governed MCP call leaves verdict-bearing audit record, gate denial leaves evidence, replay surface reachable | ✅ |
| A6 | Vault mutations governed on the MCP surface (live-test follow-on) | WIRE | 2026-08-17 live run proved `vault_write` executed with only auth; the five mutations now route through `_run_governed` (grant + capability + contained executor + verdict audit), `vault_append/patch/delete/move` gained ToolDefs + confined executors; denial + granted-execution + confinement tests in `test_mcp_security.py` | ✅ |

## B. Convergence (M3 residual)

| # | Item | Destination | Exit evidence | Status |
|---|---|---|---|---|
| B1 | `.gitignore` `artifacts/smi018/` (untracked claim_report.json) | CUT | no untracked files; git status clean | ✅ |
| B2 | README/MANIFEST must not advertise parked items | VERIFY | grep clean (multimodal/stage-0/personal_intelligence/core-health absent) | ✅ (verified M3) |
| B3 | `docs/task-contract-v1.md`, `triumvirate*.md`, `conversation-*-v1.md` each live or parked | VERIFY | checked; task-contract + envelope live, triumvirate multimodal labeled stub | ✅ (verified M3) |
| B4 | Metrics must not count stub calls | VERIFY | multimodal gate already excludes; no other stub path found | ✅ (verified M3) |

## C. Factory dogfood (M4)

| # | Item | Destination | Exit evidence | Status |
|---|---|---|---|---|
| C1 | One real MSB change through the factory (generate → review → verify → merge decision) | WIRE | `tests/factory/test_factory_dogfood.py` — real patch + worktree + pytest, MERGED verdict, evidence chain covers classify→plan→review→verify | ✅ |
| C2 | Diverse reviewer real (builder∉reviewers, model identity recorded) | WIRE | panel runs distinct reviewer models recorded on `Review.reviewer_models`; builder-as-reviewer fails closed (BLOCKED) | ✅ |
| C3 | Seeded defect caught by the reviewer | WIRE | seeded BLOCK verdict → factory BLOCKED with finding; CONCERN surfaced, never a silent merge | ✅ |
| C4 | No abandoned worktrees | VERIFY | `git worktree list` shows only main | ✅ H1 (M3) |

## D. Reliability & Adversarial Proof (M5)

| # | Item | Destination | Exit evidence | Status |
|---|---|---|---|---|
| D1 | Failure matrix implemented (11 modes) | WIRE | `tests/chaos/test_failure_matrix.py` (11 tests, all hermetic): model down / invalid output / timeout / permission denial / duplicate / partial completion / stale evidence / corrupted state / retry exhaustion / prompt injection / conflicting instructions | ✅ |
| D2 | No silent unsafe continuation | WIRE | each mode asserts a visible terminal state (FAIL/BLOCK/REVIEW/skip) — never an invisible pass | ✅ |
| D3 | Recovery bounded (retries/timeouts/escalation measurable) | WIRE | retry exhaustion pinned at 3 attempts; timeout fails task + skips downstream; partial completion reports successes + skips | ✅ |
| D4 | Security cases (prompt injection, authority confusion) | WIRE | injection taint escalates write to REVIEW; conflicting-instruction rules (taint>approval, tier>approve) fail-safe | ✅ |
| D5 | Soak run report | PARK | documented limitation — needs long-running stack; deferred to M6 trial | ⏸ |

## E. Personal production trial (M6) — operator-gated

| # | Item | Destination | Exit evidence | Status |
|---|---|---|---|---|
| E1 | Operating-ledger template committed | WIRE | `docs/blueprints/convergence-to-12/operating-ledger.md` — task/baseline/MSB-time/intervention/outcome/failure/evidence columns + monthly rollup + M6 decision gate | ✅ |
| E2 | ≥30 days real usage + measurable value | OPERATOR | Wilson runs the ledger for 30–60 days | ⏸ |

## F. Trust boundary (security review #1–#9)

| # | Item | Destination | Exit evidence | Status |
|---|---|---|---|---|
| F1 | Signing backend seam (hardware-ready) | WIRE | `uac/signing.py` + algorithm-agnostic anchor — ✅ done | ✅ 474ad84 |
| F2 | Off-box append-only notary | WIRE | `uac/notary.py` + `scripts/notarize/verify` + launchd plist — ✅ done | ✅ 48afb9a |
| F3 | RFC 3161 timestamping | WIRE | `uac/timestamping.py` + tests — ✅ done | ✅ 48afb9a |
| F4 | Append-only storage triggers + hardened repair() | WIRE | audit_chain triggers + repair auth/notary-refusal — ✅ done | ✅ b41c0d6 |
| F5 | Verify-before-trust (chain+anchor) on consequential actions | WIRE | `verify_trustworthy()` + vesta refuse-on-tamper — ✅ done | ✅ b41c0d6 |
| F6 | Device signature bound into audit record | WIRE | `signed_proof` in approval.decided — ✅ done | ✅ b41c0d6 |
| F7 | Canonicalization (JCS) | PARK | documented: deferred to versioned migration (would rewrite all hashes) | ⏸ |
| F8 | Merkle receipts | PARK | documented limitation (hash chain, not tree) | ⏸ |
| F9 | Actual Secure Enclave / YubiKey key migration | OPERATOR | provision hardware key, flip `MSB_CHAIN_ANCHOR_BACKEND` | ⏸ |
| F10 | Truly off-box WORM notary sink (rclone → object-lock/second machine) | OPERATOR | infra decision + credentials | ⏸ |

## G. Remaining milestones (calendar/operator-gated)

| # | Item | Destination | Exit evidence | Status |
|---|---|---|---|---|
| G1 | M7 independent user validation | OPERATOR | 2–3 users complete a task unaided | ⏸ |
| G2 | M8 public release | OPERATOR | auditable claims + demo + quantified results | ⏸ |

## H. Release-gate sequence (2026-08-17, core-loop verified)

| # | Item | Destination | Exit evidence | Status |
|---|---|---|---|---|
| H1 | Canonical task live through /agent/handle | DONE | run `dbb-20260817T015727-07143`, verdict PASS, hash `30a7ccc192e0bbb6`, replay consistent (15 events), spine 3 vertebrae, chain 19 records — `artifacts/core-loop/run1/` | ✅ |
| H2 | Three verdict cases live | DONE | SAFE read-only PASS (5 hits) · unapproved write FAIL GateReview + POLICY_CHECKED DENIED ×3, no mutation · kill-switch FAIL GateBlocked, no mutation — `artifacts/core-loop/case-*/` | ✅ |
| H3 | Failure matrix rerun | DONE | 11/11 modes bounded/visible (model down, timeout, retry exhaustion, injection, tamper…) + bypass 13/13 | ✅ |
| H4 | Semantic retrieval fallback | DONE | `FabricRetrievalRouter` degrades empty episodic → vector with `fallback_from`; live re-run PASS 5 hits (was 0) | ✅ |
| H5 | Reliability metrics | DONE | `msb_v3_task_retries_total` + `msb_v3_task_recoveries_total` added + tested; queries/latency/verdicts already live | ✅ |
| H6 | Daily backup + restore test | DONE | launchd job loaded (03:00) · backup 19 DBs notarized · restore over corrupted runtime: chain `valid: True` 9,858 rec | ✅ |
| H7 | Factory dogfood (one real change) | DONE | 2 runs, pipeline failed closed NEEDS_WORK; seeded defect MISSED by live 0.5B/8B reviewers (real finding) — `artifacts/factory-dogfood/` | ✅ |
| H9 | Reviewer hardening (H7 follow-up) | DONE | Root cause: new files produced EMPTY diffs (OSError skip) + single-model panels never ran the coherence lens + prompt truncated diff at 2000 chars. Fixed all three; 4 regression tests; live runs 3-4 confirm diff reaches reviewer | ✅ |
| H10 | Docs-only changes skip the test suite (H7 merge blocker) | DONE | `is_docs_only_change` + classified `TestEvidence(skipped=True, reason)` (distinct from UNVERIFIED); verifier + verdict gate accept it; live run 7: **MERGED in 24s** (was 328s timeout); coherence scan narrowed to past-tense forms (noun/gerund false positive fixed) | ✅ |
| H11 | Intent over-grant fixed (Entry 002 follow-up) | DONE | `_forbids_write` hard floor in `interpret_intent`: explicit no-write directive strips self-granted `write_file`, beats completion, `write_suppressed` visible; live re-run of the exact over-grant request → `['read_vault']`, PASS | ✅ |
| H8 | Operating ledger opened | DONE | 15 dated entries — `operating-ledger-entries.md` | ✅ |

---

## Next actions (this session, in order)
1. B1 (.gitignore) — quick.
2. A3/A4 (MCP chat governance) + A5 (live-loop test).
3. D1–D4 (failure matrix).
4. C1–C3 (factory dogfood).
5. E1 (ledger template).
6. H1–H8 (release-gate sequence, 2026-08-17) — all done, see table above.

## I. v0.3.0-rc1 release-candidate sequence (2026-08-17)

| # | Item | Destination | Exit evidence | Status |
|---|---|---|---|---|
| I1 | Version 0.3.0 (all sources agree) | DONE | pyproject/`__version__`/identity/MANIFEST/paseo CLIENT_INFO → 0.3.0; `test_version_sources_agree` green | ✅ |
| I2 | RC baseline doc | DONE | `docs/releases/v0.3.0-rc1-baseline.md` — commit, lock hashes, models, env, skip inventory w/ opt-in commands, limitations | ✅ |
| I3 | Complete verdict fixtures (full records) | DONE | re-ran SAFE/tainted/kill live; each case now has `response.txt` + `replay.json` (consistent/legal, FAILED states replay) + `audit.json` (16/22/18 records); README documents all 4 cases | ✅ |
| I4 | Retrieval validation battery | DONE | +3 tests: single-token query, irrelevant query honesty (no fake fallback), empty query no-crash; tightened degrade rule (only when routed domain excludes vector) | ✅ |
| I5 | Metrics run-report generator | DONE | `scripts/run-report.py` → p50/p95 from latency histogram + queries/verdicts/router/retries/recoveries; artifact `run-report-20260817.json` | ✅ |
| I6 | Release packaging | DONE | `MSB-v3-RELEASE.md` (impl/experimental/v4 split) + `failure-report-v0.3.0-rc1.md` + `v4-parking-lot.md` + `independent-user-validation.md` (setup guide + protocol + observer template) | ✅ |
| I7 | Freeze policy sharpened | DONE | v3-contract exceptions section now names the parking lot + evidence-justified exit rule | ✅ |
| I8 | Tag + push + CI | NEXT | tag `v0.3.0-rc1` after battery; push; watch 3 CI gates | ⏳ |
