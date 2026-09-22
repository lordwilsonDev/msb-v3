# MSB-v3 Governance Hardening — Implementation Plan

**Source blueprint:** `docs/blueprints/governance-hardening.md` (post-forensic, capability-centric, UNKNOWN ≠ SAFE). **Note:** that file was never committed — the document now at that path is a **reconstruction written 2026-09-22** from in-tree artifacts, and says so itself. It is not this plan's antecedent.  
**Scope:** one controlled progression — deterministic boundary first, model layer only as challenger, fail-closed for unknown consequential from day 1.  
**Current baseline:** v0.4.2, PID 2500, port 8766, HEALTHY (2026-09-02).

---

## Phase status — reconciled 2026-09-22

Reconciled against the tree by direct inspection, not memory: every declared
deliverable path checked on disk, every declared test file run, and each
phase's green-gate evidence re-executed live. **Declared deliverables present:
17/33.** The 2026-09-02 ``Current baseline`` line above is the original
freeze; this block is the current state.

| Phase | Status |
|---|---|
| 0 — baseline freeze | ✅ DONE — re-verified live |
| 1 — capability registry | ✅ DONE |
| 2 — tool manifests | ⚠️ DONE except the executor wiring → **Open item 1** |
| 3 — capability resolver | ✅ DONE (library-only, as this phase required) |
| 4 — canonical decision object | ✅ DONE |
| 5 — shadow mode | ✅ MET (2026-09-22) — dataset regenerated from a real corpus; enforcement still not wired → **Open item 2** |
| 6 — tier-4 fail-closed | ✅ DONE — re-verified live |
| 7 — DAG closure | ❌ NOT STARTED — `agent/dag.py` carries no capability/closure code |
| 8–19 | ❌ NOT STARTED — no declared deliverable present; no equivalent implementation found under another name, with two partial traces: `RAG_CONFLICT` exists as a state constant in `governance/decision.py` (P11's vocabulary only, no conflict detection), and `scripts/production_gate.py` is a *different* gate, not P19's |

Two things in the "Throughout" section are also unbuilt: **all 15 declared
governance metrics are absent from `src/`**, and **invariants 003–010 are not
pinned as tests** (001 and 002 are). Beware a grep trap when checking the
latter — `tests/contracts/test_phase1_contract.py` pins `INVARIANT-003…006`,
but those belong to the earlier v0.3.x "Phase 1 release" and mean different
things.

### Open item 1 — Phase 2 stopped at the test

`tool_manifest.py` and `ToolManifestRegistry` exist, and the registry is
consumed by the resolver (`capability_resolver.py`) and the shadow recorder.
But the **execution path** references manifests only in a comment
(`agent/safety.py:110`) — nothing in `agent/`, `api/`, `harnesses/` or
`gateway/` loads one. Phase 2 task 4 says "Start as a test, **then wire into
the executor/gate**"; only the first half landed. The phase's green gate
("tool mismatch is detectable and blocked in test") is met, so this is a gap
against the task, not against the gate.

### Open item 2 — Phase 5's dataset had no producer (fixed); enforcement still unwired

**Dataset half resolved 2026-09-22.** The 794-record dataset was not the product
of a shadow run. Nothing in the repo deliberately produced it, and
`ShadowRecorder`'s default root is the live `runtime/governance-shadow/` — so
`tests/governance/test_shadow.py`, which built a bare `ShadowRecorder()` in six
places, wrote six records into the live dataset on every `make test`. Measured:
794 records holding **two** distinct requests under **one** `request_id` at
**one** disagreement class, accumulated over ~132 runs (2026-09-02 →
2026-09-22). The earlier "manual batch tool" reading of this was wrong — it was
test-suite pollution.

Fixed three ways: the suite now passes `shadow_root` in all nine constructions;
`scripts/probe_governance_shadow_corpus.py` is the deliberate producer (drives
the real recorder over the representative batch + the frozen gate corpus,
archives rather than appends, and fails loudly on a degenerate dataset); and the
polluted file is preserved as `shadow.jsonl.pre-20260922T081551`. The dataset is
now 68 records / 68 distinct requests, and
`docs/audits/governance-hardening-shadow-report.md` is rewritten from it.

Two findings the old dataset could not have produced:

- **The single-class result is structural, not a corpus problem.** Against the
  post-Phase-0 baseline, 65/68 records land in `OLD_BLOCK_NEW_NOT_BLOCK` — the
  old path BLOCKs every unregistered raw string, so no corpus can change it.
  The previous report's proposed fix ("run a larger representative corpus")
  would not have worked. The high-signal classes need the *pre-fix* baseline,
  which no longer exists in code, so the report reconstructs it and labels it as
  a reconstruction.
- **The resolver resolves 16/68 (24%)** — 0 of 15 obfuscated and 0 of 5
  multilingual claims. False-allow is 0/56, but that safety is carried by the
  UNKNOWN → BLOCK fallback, not by resolution. Wiring this resolver into the
  gate today would add no safety while still blocking everything but 16 entries.

**Still open:** `shadow.py` has no caller in the live path — nothing in `agent/`
invokes it — so it remains a batch producer rather than the instrumentation in
`handle()` that task 1 specifies (task 1 allowed "or a wrapper around the
gate"; that wrapper is not built). Raising resolver coverage is the precondition
for enforcement, and the producer script now makes coverage measurable per
change.

> Also noted (pre-existing, not caused by this reconciliation): the source
> blueprint cited on line 3, `docs/blueprints/governance-hardening.md`, **has
> never existed in this repo's history**. It was first cited by the same commit
> that added this file. Left uncorrected deliberately — rewiring the citation
> to an existing doc would be inventing a source.

---

## Guiding rules (from the blueprint, restated as implementation constraints)

1. **UNKNOWN ≠ SAFE.** The smallest behavioral change is the highest-value change. Do that first.
2. **Capability > keyword.** Capability is the unit of authority. Tool manifests declare capabilities; the gate checks capabilities, not tool names.
3. **Model proposes, policy authorizes.** The deterministic resolver is the boundary; MoIE/AIL is advisory only.
4. **Fail closed.** Unknown consequential → no execution. Tier-4 unknown → BLOCK immediately, no shadow data required.
5. **Test the invariant, not just the code.** Each phase ends in a green gate with falsifiable pass conditions.
6. **Shadow mode, not rewrite.** Instrument → observe → compare → enforce, in that order.

---

## Phase 0 — Freeze baseline + stop the bleed (0.5-1 day)

**Status:** DONE (2026-09-02); re-verified 2026-09-22 — the gate probes below reproduce the recorded evidence verbatim and `tests/agent/test_unknown_capability.py` passes 23/23.

**What was done:**

1. Baseline freeze doc written: `docs/audits/governance-hardening-baseline.md`.
2. Regression test written: `tests/agent/test_unknown_capability.py`.
3. Gate hardened in `src/msb_v3/agent/safety.py`:
   - `GateVerdict.action` now accepts `UNKNOWN` as a first-class value (alongside SAFE / REVIEW / BLOCK).
   - `ActionGate.tier_of()` returns a sentinel `_UNKNOWN_TIER = -1` for an unregistered capability (not the old default of `1`).
   - `ActionGate.is_registered(capability)` hook added so derived gates can ask the question directly.
   - UNKNOWN-capability policy encoded centrally in `ActionGate._unknown_disposition()`:
     - UNKNOWN + tainted → REVIEW
     - UNKNOWN + untainted → BLOCK
     - UNKNOWN + read-only reviewed path available via `_refuse_unknown_read_only()` (intentional, auditable exception)
   - `GateVerdict` docstring updated; SAFE path reason wording tightened to "registered capability, brakes clear" so the distinction is explicit.
4. Green gate run: `tests/agent/test_unknown_capability.py` passes (23 tests).

**Green gate evidence:**
- `ActionGate().gate('nuke')` → `GateVerdict(allowed=False, action='BLOCK', reason='capability not registered: nuke', tier=-1, tainted=False)`
- Same for semantically dangerous strings not in the registry: `rm -rf production`, `destroy the target`, etc.
- Known capabilities unchanged: `read_vault`=SAFE, `write_file`=SAFE, `vault_delete`=REVIEW, `send_message`=REVIEW, `financial`=BLOCK, `permissions`=BLOCK.
- Taint still escalates on UNKNOWN: `nuke(tainted=True)` → REVIEW.
- Existing safety + executor behavior preserved (live-surface-probe runs: SafeProvider research path OK, tainted write still REVIEW, blocked executor case still fails closed).
- Invariant-001 still pinned over the frozen corpus in `tests/contracts/test_gate_contract.py` (unchanged).

**Deliverables (already in tree):**
- `docs/audits/governance-hardening-baseline.md`
- `tests/agent/test_unknown_capability.py`
- `src/msb_v3/agent/safety.py` (UNKNOWN + unknown policy)

**Known limitation recorded here:** the current UNKNOWN disposition is BLOCK for the default unknown case. That is intentionally conservative. It may be too aggressive for read-only unknowns; the `_refuse_unknown_read_only()` hook exists for when the resolver wants to distinguish them later.

---

## Phase 1 — Capability Registry (1-2 days)

**Status:** DONE (2026-09-22) — `capability_registry.py`, `test_capability_registry.py` (16 pass) and `capability-registry-v1.md` all present; unknown capability resolves to `None`, not a default tier.

**Why:** capability is the unit of authority. Start by making the currently-implicit tier table an explicit, queryable registry.

**Tasks:**

1. Create `src/msb_v3/governance/capability_registry.py` (new)
   - `CapabilityRegistry`
   - Load initial entries from the existing `RISK_TIERS` + `TOOL_CAPABILITY` + any existing policy definitions.
   - Schema per capability: `capability_id, domain, tier, side_effect_class, reversibility, approval_required, taint_policy, enabled, description, version`
   - Lookup: `resolve(capability_id) -> Capability | None`

2. Make the registry the source of truth for tiers **in the gate**, but only after the registry is loaded and queryable. For now, keep `ActionGate.tier_of()` working, but have it consult the registry where possible. Be careful: do not introduce a failure mode where an unreadable registry silently grants SAFE.

3. Add registry tests:
   - `tests/governance/test_capability_registry.py` (new)
   - Known capabilities resolve. Unknown capability resolves to None.

4. Doc the initial registry contents:
   - `docs/governance/capability-registry-v1.md` (new)

**Phase 1 green gate:**
- All known capabilities represented in the registry.
- Unknown capability resolves to None, not to a default tier.
- Registry tests green.

**Deliverables:**
- `src/msb_v3/governance/capability_registry.py`
- `tests/governance/test_capability_registry.py`
- `docs/governance/capability-registry-v1.md`

---

## Phase 2 — Tool manifests (1 day)

**Status:** DONE except the executor wiring (2026-09-22) — **see Open item 1** above. `tool_manifest.py` + `test_tool_manifest.py` (20 pass) present and consumed by the resolver; the executor/gate half of task 4 never landed.

**Why:** tools must declare what they can do. Today's `TOOL_CAPABILITY` is a static dict in `safety.py`. Generalize it.

**Tasks:**

1. Create `src/msb_v3/governance/tool_manifest.py` (new)
   - `ToolManifest(tool_id, capabilities[], maximum_tier, approval_required, taint_policy, side_effects, enabled, version)`
   - Initial manifests from the existing `TOOL_CAPABILITY` mapping + the tools you know are governed today.

2. Add a manifest lookup:
   - `ToolManifestRegistry`
   - `lookup(tool_id) -> ToolManifest | None`
   - Unknown tool → None (this matters: an undeclared tool must not silently inherit SAFE).

3. Add tool-manifest tests:
   - `tests/governance/test_tool_manifest.py` (new)
   - Known tools resolve. Unknown tool → None. Manifest mismatch detectable.

4. Wire a first validation: before execution, tool's declared capabilities must include the capability being exercised. This is the "tool cannot execute a capability it does not declare" invariant. Start as a test, then wire into the executor/gate.

**Phase 2 green gate:**
- All governed tools declare capabilities.
- Tool mismatch is detectable and blocked in test.
- Unknown tool does not silently inherit SAFE.

**Deliverables:**
- `src/msb_v3/governance/tool_manifest.py`
- `tests/governance/test_tool_manifest.py`

---

## Phase 3 — Deterministic capability resolver V1 (1-2 days)

**Status:** DONE (2026-09-22) — `capability_resolver.py` + `test_capability_resolver.py` (26 pass). Still library-only as this phase required: no `agent/` or `api/` caller, only `governance/shadow.py`.

**Why:** you need a capability resolution step before the gate, so the system isn't gating on a raw string. V1 must be deterministic. No LLM authority.

**Tasks:**

1. Create `src/msb_v3/governance/capability_resolver.py` (new)
   - `CapabilityResolver`
   - Resolution sources in priority order:
     1. Explicit DAG capability annotation (none today; this is a future wiring task)
     2. Tool manifest
     3. Existing capability mapping (registry)
     4. Deterministic intent templates (start small; a few templates for known request shapes)
     5. Keyword/pattern signal (the existing MoIE templates become one signal, not the authority)
     6. Otherwise UNKNOWN
   - Output: `{resolved_capability, resolution_method, confidence, alternatives, ambiguous, risk_tier, reason}`
   - Confidence is **descriptive**, not authority. `confidence=1.0` means resolved by an explicit source; `confidence=0.0` means no match.

2. Add resolver tests:
   - `tests/governance/test_capability_resolver.py` (new)
   - Known tool + manifest → resolved, method="tool_manifest", confidence=1.0
   - Unknown tool → unresolved, method="none", confidence=0.0
   - Keyword-only signal → still unresolved unless a template matches

3. Do **not** wire the resolver into the live execution path yet. It's a library at this point.

**Phase 3 green gate:**
- Deterministic resolver works.
- No model authority in the resolver.
- Unknown remains UNKNOWN.

**Deliverables:**
- `src/msb_v3/governance/capability_resolver.py`
- `tests/governance/test_capability_resolver.py`

---

## Phase 4 — Canonical decision object (0.5 day)

**Status:** DONE (2026-09-22) — `decision.py` + `test_decision.py` (22 pass); UNKNOWN is a valid decision value.

**Why:** the gate, the receipt, and shadow mode all need a common governance contract.

**Tasks:**

1. Define the decision object in one place:
   - `src/msb_v3/governance/decision.py` (new)
   - `{decision, capability, tier, resolution_method, confidence, taint, authority, approval, policy, reason, evidence, alternatives, conflicts, verification_required}`
   - `decision` ∈ `{ALLOW, REVIEW, BLOCK, UNKNOWN}`

2. Add decision tests:
   - `tests/governance/test_decision.py` (new)

**Phase 4 green gate:**
- Canonical decision object exists.
- UNKNOWN is a valid decision value.

**Deliverables:**
- `src/msb_v3/governance/decision.py`
- `tests/governance/test_decision.py`

---

## Phase 5 — Shadow mode (2-3 days)

**Status:** MET (2026-09-22) — **see Open item 2** above. The dataset is regenerated from a real corpus (68 records, 68 distinct requests) by `scripts/probe_governance_shadow_corpus.py`, and the report is rewritten from those results. The recorder is still not wired into the live path.

**Why:** measure disagreements before you enforce. This is where you find out whether the deterministic resolver is producing useful signal or just noise.

**Tasks:**

1. Add shadow instrumentation:
   - In `handle()` (or a wrapper around the gate), run the old path and the new resolver in parallel.
   - Record: `request_id, request, old_actiongate_decision, old_tier, new_capability, resolution_method, new_tier, new_decision, taint, tool, disagreement, reason`

2. Persist shadow records:
   - `runtime/governance-shadow/shadow.jsonl` (gitignored)
   - One line per request.

3. Run a batch of representative requests:
   - The existing demo corpus + a few safe requests + a few dangerous requests.
   - Capture disagreements.

4. Classify disagreements:
   - OLD SAFE → NEW UNKNOWN
   - OLD SAFE → NEW HIGH-RISK
   - OLD BLOCK → NEW SAFE
   - OLD REVIEW → NEW BLOCK
   - etc.

5. Produce a disagreement report:
   - `docs/audits/governance-hardening-shadow-report.md` (new)

**Phase 5 green gate:**
- Shadow data collected.
- Disagreements classified.
- No enforcement yet.

**Deliverables:**
- Shadow instrumentation (in `handle()` or a wrapper)
- `runtime/governance-shadow/shadow.jsonl`
- `docs/audits/governance-hardening-shadow-report.md`

---

## Phase 6 — Tier-4 fail-closed (1 day)

**Status:** DONE (2026-09-22); re-verified live — UNKNOWN consequential → BLOCK, and Invariant-002 is pinned in `tests/agent/test_unknown_capability.py`.

**Why:** Invariant-002. Unknown consequential capability cannot execute. Start with tier 4; don't expand everywhere at once.

**Tasks:**

1. In the gate (or in a new capability-governance layer called by the gate), enforce:
   - If capability tier == 4 → require explicit authority.
   - If capability is UNKNOWN and the resolved/expected side effect is consequential → BLOCK.

2. Add the live regression test:
   - `tests/agent/test_unknown_capability.py` already has the nuke case. Extend it:
     - `ActionGate().gate("nuke")` → not SAFE (UNKNOWN or BLOCK).
     - A tier-4 unknown capability → BLOCK.
   - Keep Invariant-001 intact.

3. Re-run `make test`.

**Phase 6 green gate:**
- UNKNOWN consequential → no execution (BLOCK in test).
- Tier-4 unknown → blocked.
- Existing suite green.
- Invariant-001 intact.

**Deliverables:**
- Updated `src/msb_v3/agent/safety.py` (or new capability-governance layer)
- Updated `tests/agent/test_unknown_capability.py`

---

## Phase 7 — DAG capability annotation + closure (2-3 days)

**Why:** individual nodes may be low-risk; the mission may be consequential.

**Tasks:**

1. Give DAG nodes a capability field:
   - `src/msb_v3/agent/dag.py` — add `capability` to `Task` (or a parallel annotation).
   - Start with task's `required_capabilities` as the source.

2. Resolve node capabilities through the manifest + registry:
   - In the executor/gate path, resolve each node's capability, not just the raw tool name.

3. Compute mission capability closure:
   - Union of resolved node capabilities.
   - Add a configurable escalation rule:
     - If any node is tier-4 → mission is tier-4.
     - If closure contains financial.read + financial.write → mission is financial.transfer (example rule; make it explicit and testable).
   - Keep composition rules explicit and testable, not implicit.

4. Add closure tests:
   - `tests/agent/test_dag_capability_closure.py` (new)
   - Test: safe + safe + safe composing into consequential → detected.
   - Test: single low-risk node → not escalated.

**Phase 7 green gate:**
- DAG nodes have resolved capabilities.
- Mission closure computed.
- Composition escalation detected in test.

**Deliverables:**
- Updated `src/msb_v3/agent/dag.py`
- `tests/agent/test_dag_capability_closure.py`
- `docs/governance/dag-capability-closure.md` (new)

---

## Phase 8 — Tool escape tests (1 day)

**Why:** governance must follow capability, not interface.

**Tasks:**

1. For each consequential capability, attempt execution through alternate paths:
   - primary tool, alternate tool, shell, script, HTTP, n8n, filesystem, indirect tool.
2. Test that the same underlying capability receives the same governance decision.
3. Add the tests:
   - `tests/governance/test_tool_escape.py` (new)

**Phase 8 green gate:**
- Same capability → same governance decision across interfaces.

**Deliverables:**
- `tests/governance/test_tool_escape.py`

---

## Phase 9 — Taint integration (1 day)

**Why:** taint already exists, but capability decisions must incorporate it consistently.

**Tasks:**

1. Confirm taint propagation survives the new capability layer.
2. Unknown + tainted + consequential → BLOCK/REVIEW per policy.
3. Add tests:
   - Extend `tests/agent/test_safety.py` or add `tests/governance/test_taint_capability.py`.
   - Taint cannot silently disappear before consequential execution.

**Phase 9 green gate:**
- Taint incorporated into capability decisions.
- Taint cannot silently disappear.

**Deliverables:**
- Updated taint/gate wiring
- `tests/governance/test_taint_capability.py`

---

## Phase 10 — Memory governance (1-2 days)

**Why:** memory must provide context, not unbounded authority.

**Tasks:**

1. Add memory provenance fields:
   - `source, provenance, trust, verification, authority, timestamp, status`
   - States: UNVERIFIED, VERIFIED, CONTESTED, REVOKED, EXPIRED
2. Add the rule: memory does not carry standing authorization.
   - A recalled "operator approved X" is context, not a reusable grant.
   - Each consequential execution needs a fresh authority check.
3. Add memory tests:
   - `tests/governance/test_memory_governance.py` (new)

**Phase 10 green gate:**
- Memory provenance present.
- Memory cannot independently authorize execution.

**Deliverables:**
- Updated memory models
- `tests/governance/test_memory_governance.py`

---

## Phase 11 — RAG conflict governance (1-2 days)

**Why:** conflicting evidence must become explicit conflict, not silent consensus.

**Tasks:**

1. Add evidence states: SUPPORTED, UNSUPPORTED, CONTRADICTED, OUTDATED, UNKNOWN.
2. Add conflict detection:
   - If authoritative evidence for a capability conflicts → RAG_CONFLICT.
   - If conflict affects a consequential action → REVIEW/BLOCK.
3. Add RAG conflict tests:
   - `tests/governance/test_rag_conflict.py` (new)

**Phase 11 green gate:**
- RAG conflict is a state.
- Conflicting consequential evidence → REVIEW/BLOCK.

**Deliverables:**
- Updated retrieval/evidence wiring
- `tests/governance/test_rag_conflict.py`

---

## Phase 12 — Green-Gate expansion (1 day)

**Why:** verification must test more than "did the expected outcome occur?"

**Tasks:**

1. Expand verification to answer:
   - Was capability identified correctly?
   - Was risk classified correctly?
   - Was authority valid?
   - Was taint handled correctly?
   - Did intended execution occur?
   - Did unintended execution occur?
   - Was verification independent?
   - Was evidence sufficient?
   - Was there unresolved conflict?
   - Was the governance decision itself valid?
2. Add Green-Gate states: GREEN, GREEN-WITH-CONDITIONS, REVIEW, BLOCK, UNKNOWN, HUMAN-DECISION-REQUIRED.
3. Add verification tests:
   - `tests/governance/test_green_gate.py` (new)

**Phase 12 green gate:**
- Expanded Green-Gate states exist.
- Verification tests more than outcome.

**Deliverables:**
- Updated verification/green-gate wiring
- `tests/governance/test_green_gate.py`

---

## Phase 13 — AIL/MoIE challenge layer (1-2 days, only after deterministic boundary is stable)

**Why:** MoIE should challenge, not authorize.

**Tasks:**

1. Add MoIE inversion as advisory:
   - PRIMARY INTERPRETATION → MoIE INVERSION → "What else could this capability be?" → "How could this be abused?" → CHALLENGE RESULT.
2. Green-Gate determines whether the challenge changes the decision.
3. Run the MoIE experiment:
   - Deterministic only vs deterministic + MoIE.
   - Measure false-safe discoveries, false-positive increases, unknown detection, review increases, latency, model calls.
4. Add MoIE challenge tests:
   - `tests/governance/test_moie_challenge.py` (new)

**Phase 13 green gate:**
- MoIE challenges, does not authorize.
- MoIE experiment measured.

**Deliverables:**
- Updated MoIE wiring
- `tests/governance/test_moie_challenge.py`
- `docs/audits/governance-hardening-moe-experiment.md` (new)

---

## Phase 14 — Multi-model independence tests (1 day)

**Why:** consensus is not proof.

**Tasks:**

1. Use independent roles, not four models voting on "is this safe?":
   - MODEL A: capability interpretation.
   - MODEL B: hidden-danger search.
   - MODEL C: alternative interpretation.
   - MODEL D: bypass analysis.
2. Measure: agreement, disagreement, shared evidence, shared assumptions, model family, prompt overlap.
3. Add tests:
   - `tests/governance/test_multi_model_independence.py` (new)

**Phase 14 green gate:**
- Independent roles used.
- Correlation measurable.

**Deliverables:**
- `tests/governance/test_multi_model_independence.py`

---

## Phase 15 — Adversarial test corpus (2-3 days)

**Why:** FSSR needs a corpus. The existing gate_corpus is keyword-heavy. Expand it.

**Tasks:**

1. Build cases across: DIRECT, INDIRECT, SYNONYMS, METAPHOR, OBFUSCATION, MULTI-STEP, COMPOSITION, TOOL ALIAS, DAG ESCALATION, AMBIGUOUS, CONTRADICTORY, MALICIOUS, INCOMPLETE, IMPOSSIBLE.
2. "Delete production" must be tested alongside semantically equivalent forms.
3. Measure: true positive, true negative, false positive, false negative, unknown, review.
4. Add the corpus + tests:
   - `config/governance-hardening-corpus.json` (new)
   - `tests/governance/test_hardening_corpus.py` (new)

**Phase 15 green gate:**
- Expanded corpus exists.
- FSSR measurable.

**Deliverables:**
- `config/governance-hardening-corpus.json`
- `tests/governance/test_hardening_corpus.py`

---

## Phase 16 — Chaos + resource tests (1-2 days)

**Why:** governance must fail closed under stress, and disk/resource pressure must not silently degrade governance.

**Tasks:**

1. Kill services during planning, resolution, authorization, execution, verification, audit, memory mutation:
   - Ollama, Qdrant, SQLite, filesystem, network, n8n, audit writer, verification service.
2. Test fail-closed + recovery + evidence.
3. Resolve disk pressure:
   - Break down consumption.
   - Define retention.
   - Set thresholds: 85% WARNING, 90% HIGH, 95% CRITICAL, 99% EMERGENCY.
4. Add chaos tests:
   - Extend existing chaos tests or add `tests/governance/test_governance_chaos.py`.

**Phase 16 green gate:**
- Fail-closed under chaos.
- Disk thresholds defined.

**Deliverables:**
- Chaos tests
- `docs/governance/governance-resource-thresholds.md` (new)

---

## Phase 17 — MemoryStore cleanup + operational cleanup (1 day)

**Why:** technical debt before declaring the governance layer production-ready.

**Tasks:**

1. Migrate remaining MemoryStore call sites to MemoryFabricStore.
2. Run full test suite.
3. Confirm behavioral equivalence.
4. Fix disk pressure if still present.

**Phase 17 green gate:**
- MemoryStore deprecation resolved.
- Full suite green.
- Disk within thresholds.

**Deliverables:**
- Migrated call sites
- `make test` green

---

## Phase 18 — Policy versioning + change control + receipt upgrade (2 days)

**Why:** every decision must be reconstructable against the exact governance configuration that produced it.

**Tasks:**

1. Add policy versioning to every decision:
   - `policy_version, capability_registry_version, tool_manifest_version, resolver_version, verification_version`
2. Upgrade the receipt:
   - REQUESTED → NORMALIZED → CAPABILITY RESOLVED → RISK CLASSIFIED → TAINT ASSESSED → AUTHORITY CHECKED → APPROVAL CHECKED → TOOL AUTHORIZED → EXECUTED → OBSERVED → VERIFIED → AUDITED
3. Separate properties in the receipt:
   - `integrity_verified, semantic_correctness, execution_verified, authorization_verified, capability_resolution_verified`
4. Add change control:
   - Changes to capabilities, tiers, policies, tools, authority, taint rules, verification, resolver → CHANGE REQUEST with reason, evidence, risk, expected benefit, test plan, approval, rollback.
5. Add receipt + versioning tests:
   - `tests/evidence/test_governance_receipt.py` (new)

**Phase 18 green gate:**
- Policy versioning present.
- Receipt upgraded.
- Change control defined.

**Deliverables:**
- Updated receipt
- `tests/evidence/test_governance_receipt.py`
- `docs/governance/policy-versioning.md` (new)

---

## Phase 19 — Final production gate (1 day)

**Why:** a falsifiable definition of "governance-hardened."

**Tasks:**

1. Assert all of:
   - `ActionGate().gate("nuke")` returns UNKNOWN (not SAFE). [regression test]
   - Every governed tool has a ToolManifest. [inventory test]
   - Every DAG-executed tool call passes through capability resolution. [integration test]
   - Tier-4 unknown capability → BLOCK in a live-ish test. [integration test]
   - The receipt includes capability_resolution fields. [receipt test]
   - Shadow-mode disagreement dataset exists and is classified. [process evidence]
   - Invariant-001 intact. [corpus test]
   - Full suite green. [make test]

2. Write the final gate doc:
   - `docs/audits/governance-hardening-production-gate.md` (new)

**Phase 19 green gate:**
- All production-gate assertions pass.
- `make test` green.

**Deliverables:**
- `docs/audits/governance-hardening-production-gate.md`

---

## Throughout — observability + invariants

**Add governance metrics**, wired as you build each piece. (This list was previously attributed here to "blueprint §53" — but §53 is the *Steward* blueprint's health vector: nine project-state axes, not a metric set. The list is this plan's own. Corrected 2026-09-22.)
- `governance_requests_total`
- `capability_unknown_total`
- `capability_review_total`
- `capability_block_total`
- `capability_allow_total`
- `false_safe_regressions_total`
- `taint_review_total`
- `rag_conflict_total`
- `memory_conflict_total`
- `tool_escape_attempts_total`
- `moie_challenges_total`
- `moie_disagreements_total`
- `human_approval_total`
- `human_override_total`
- `verification_failures_total`

**Pin the invariants as tests:**
- Invariant-001: BLOCK → 0 model calls. (already pinned)
- Invariant-002: UNKNOWN consequential → no execution. (add as you build Phase 6)
- Invariant-003: model output cannot directly authorize a consequential capability.
- Invariant-004: every side-effecting tool call has a resolved capability.
- Invariant-005: every consequential capability has explicit authority requirements.
- Invariant-006: unknown tool capability cannot inherit SAFE.
- Invariant-007: taint cannot disappear without an explicit trusted transition.
- Invariant-008: memory cannot independently grant execution authority.
- Invariant-009: conflicting evidence cannot silently become consensus.
- Invariant-010: audit integrity does not equal semantic correctness.

---

## Ordering rules (from the blueprint)

- Do not skip ahead because a later component looks more sophisticated.
- Every phase ends in a green gate: IMPLEMENT → TEST → ADVERSARIAL TEST → RESOURCE CHECK → AUDIT CHECK → REVIEW → GREEN → NEXT PHASE.
- If any critical check fails → STOP. Do not stack new architecture on an unverified layer.

---

## What to do first (today)

1. Write baseline freeze doc.
2. Add the nuke regression test (fails under current code).
3. Make UNKNOWN a real decision in the gate (smallest safe behavioral change).
4. Re-run `make test`.

That's Phase 0. Everything else depends on it.