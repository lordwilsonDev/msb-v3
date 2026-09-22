# MSB-v3 Governance Hardening — Blueprint

> ## ⚠️ Status: RECONSTRUCTED 2026-09-22 — this is not the original document
>
> **No original ever existed in this repository.** `docs/blueprints/governance-hardening.md`
> was cited as a source by two tracked documents but was never committed — verified
> against all history (`git log --all --diff-filter=AD -- docs/blueprints/governance-hardening.md`
> returns nothing, and the path was never tracked under any other name).
>
> This file was written **after the fact** to close that dangling citation. It is a
> reconstruction assembled from artifacts that *do* exist in the tree, and every
> substantive claim below carries its source. It is **not** the plan's antecedent,
> must not be read as one, and must not be backdated: `PLAN.md` was committed
> 2026-09-02, three weeks before this file.
>
> The original's section numbering could not be recovered, so **no section numbers
> are asserted here.** One citation in `PLAN.md` that appears to reference this
> blueprint by number (§53) in fact resolves to a *different* blueprint — see
> §"Citation corrections".

---

## 0. How to read this

**Citing documents identified by inspecting the tree, not memory:**

| Document | Where | What it says |
|---|---|---|
| `PLAN.md` | header, "Source blueprint" | "**Source blueprint:** `docs/blueprints/governance-hardening.md` (post-forensic, capability-centric, UNKNOWN ≠ SAFE)." |
| `PLAN.md` | "Guiding rules" | "Guiding rules (from the blueprint, restated as implementation constraints)" — 7 rules |
| `docs/audits/governance-hardening-baseline.md` | header | "**Source blueprint:** `docs/blueprints/governance-hardening.md`" |
| `docs/audits/governance-hardening-baseline.md` | "What changes first" | "See `PLAN.md` → Phase 0:" |

*(Cited by heading rather than line number deliberately: line numbers in a living document
rot — the two `PLAN.md` citations above were at lines 3 and 13 when this file was written,
and the second moved within the same session.)*

**What "post-forensic" points at.** `docs/audits/forensic-grill-2026-09-02.md` —
dated the same day as PLAN.md's baseline — is a 33-question, 1575-line grill whose
answers are tagged `EVIDENCE` / `INFERENCE` / `HYPOTHESIS` / `UNKNOWN`. It is the
only document in the tree that fits "post-forensic", and its topics map onto the
phase plan almost one-to-one (see §"Program structure"). It is treated here as the
blueprint's evidentiary basis.

**Provenance rule for this file.** Anything a reader could act on is traceable. Where
a statement is this document's own synthesis rather than a restatement of a source,
it is marked ***(synthesis)***.

**One part is a reconstruction of a restatement.** The seven guiding rules below survive
*only* as `PLAN.md`'s restatement of them ("Guiding rules (from the blueprint, restated
as implementation constraints)"). There is no independent surviving copy of the
blueprint's own wording, so those sections reconstruct rules from a document that had
already paraphrased them. They are faithful to `PLAN.md`; they cannot be faithful to a
wording nobody can read.

**Cross-references here are by name, not number.** This file is unnumbered by design
(see "Citation corrections"), so it refers to its own sections by title. Every `§N` below
belongs to a *different* document.

---

## 1. The problem this program exists to solve

From `docs/audits/forensic-grill-2026-09-02.md`:

- **The gate is a keyword pre-filter, not a security boundary.** Its measured
  performance over the frozen corpus is **precision 0.68 / recall 0.425** — it misses
  more than half of dangerous inputs. Pinned in `tests/contracts/test_gate_contract.py`.
- **"Proven safe" was not established.** The `forensic-grill` §1.4 states plainly that the
  system does *not* prove the governance layer catches all dangerous actions.
- **The specific defect that started the program.** An unregistered capability
  silently defaulted to **tier 1 = SAFE**. `ActionGate().gate("nuke")` returned
  `SAFE / tier 1`. The failure was not that the gate was permissive — it was that
  *unknown* was represented as a value on the same axis as *authorised*.
- **The epistemic stance.** The grill tags every answer, and `UNKNOWN` is a first-class
  tag. The blueprint inherits that: UNKNOWN is a state to be surfaced and handled, never
  silently mapped to a benign one.

Evidence for the pre-fix state is preserved in
`docs/audits/governance-hardening-baseline.md` (baseline freeze, 2026-09-02), which is
deliberately kept as the pre-fix record rather than updated.

---

## 2. First principle — UNKNOWN ≠ SAFE

**The rule.** An unrecognised capability is not low-risk; it is unknown. Unknown is
never SAFE by default, and never Tier 1.

**What it forbids.** Defaulting an unregistered capability to a benign disposition;
treating "not in the table" as "allowed".

**Where it landed.** `_UNKNOWN_TIER = -1` (deliberately not `1`, "the old default that
hid the gap" — `src/msb_v3/agent/safety.py`), `UNKNOWN` accepted as a first-class
`GateVerdict.action`, and `ActionGate.is_registered()` so a derived gate can ask the
question directly.Documented in `docs/governance/canonical-decision-object-v1.md`: *"UNKNOWN is a first-class decision value. It is not a tier. It is not SAFE. It is not ALLOW."*

**Verified live 2026-09-22** — reproduced from the committed gate:

```
nuke / rm -rf production / destroy the target   BLOCK   tier=-1   allowed=False
read_vault SAFE t1 · write_file SAFE t2 · vault_delete REVIEW t3 · send_message REVIEW t3
financial BLOCK t4 · permissions BLOCK t4 · nuke(tainted_inputs=True) REVIEW
```

---

## 3. Second principle — capability > keyword

**The rule.** Capability is the unit of authority. The gate checks capabilities, not
tool names and not substrings.

**Why.** The keyword gate's recall of 0.425 is a *property of the approach*, not a bug
to patch. Counting tokens cannot be made to recognise intent, and a keyword gate is
cheap to spell around. The unit of authority therefore moves up a level: resolve the
request to a *capability*, then authorise the capability.

**The chain it implies** *(synthesis — the four components are each documented; the
framing as one chain is this document's)*:

| Step | Component | Spec |
|---|---|---|
| 1 | Inventory of capabilities | `docs/governance/capability-registry-v1.md` |
| 2 | Which tool may exercise which capability | `docs/governance/tool-manifest-v1.md` |
| 3 | Deterministic request → capability resolution | `docs/governance/capability-resolver-v1.md` |
| 4 | One decision contract every consumer shares | `docs/governance/canonical-decision-object-v1.md` |
| 5 | Enforcement (the boundary that makes unauthorised execution impossible) | `docs/governance/authority-model.md` |

The bridge invariant that made 1 and 2 safe to build: each is first a **mirror** of the
existing tables (`RISK_TIERS`, `TOOL_CAPABILITY`), with mirror-equality *tested, not
assumed*, so introducing the registry could not silently change what the gate allowed.

---

## 4. Third principle — model proposes, policy authorizes

**The rule.** The deterministic resolver is the boundary. Model output — including MoIE
inversion and AIL — is **advisory only** and carries no authority.

**Where it comes from.** `forensic-grill` §8 ("MODEL COMPROMISE") asks what happens if the
model is wrong *on purpose*; `forensic-grill` §9 ("ZERO-MODEL SAFETY") asks what survives
with no model at all. The blueprint's answer is structural: the model's output is an input
to resolution, never a verdict.

**What it forbids.** Confidence as permission. `docs/governance/capability-resolver-v1.md` is explicit:
*"`confidence=1.0` means the request matched an explicit source; it does not mean the
request is safe."* The resolver proposes; the gate decides.

---

## 5. Fourth principle — fail closed

**The rule.** Unknown consequential → no execution. For a request that cannot be
resolved, the disposition is fixed and deterministic:

| Situation | Disposition |
|---|---|
| UNKNOWN + tainted inputs | **REVIEW** |
| UNKNOWN + untainted | **BLOCK** |
| Registered, tier 4 | **BLOCK** |
| Registered, tier 3 | **REVIEW** (approval required) |
| Registered, tier 1–2 | **SAFE** |

Encoded centrally in `ActionGate._unknown_disposition()` so the policy cannot drift
across call sites. Two consequences are accepted deliberately:

- **Tier-4 unknowns BLOCK immediately**, with no shadow data required. Waiting for
  evidence before refusing the most consequential case would be the wrong trade.
- **A read-only unknown is refused too.** `_refuse_unknown_read_only()` exists as an
  explicit, auditable hook for when the resolver is trusted to distinguish them — the
  default path stays BLOCK. This is recorded as a **known limitation**, not an oversight.

The complementary invariant, from `docs/governance/authority-model.md`: every entry path
that can cause a capability to execute must route through `ActionGate`, and **there is no
third state** — each attempt resolves to `allowed`, `denied`, `approval-required`, or
`error`, never silent execution. Fourteen entry paths are mapped in
`docs/releases/O3-AUTHORITY-CLOSURE-PLAN.md`.

---

## 6. Fifth principle — test the invariant, not the code

**The rule.** A phase is done when a *falsifiable* invariant is green — not when the code
that implements it exists.

**Why.** The programme's first defect was an untested assumption, and the gate's own
failure mode is a green suite standing in for a measurement nobody took.

**The ten invariants, as `PLAN.md` records them, with their status at 2026-09-22:**

| # | Invariant | Status |
|---|---|---|
| 001 | BLOCK → 0 model calls | **pinned** — `tests/contracts/test_gate_contract.py` |
| 002 | UNKNOWN consequential → no execution | **pinned** — `tests/agent/test_unknown_capability.py` |
| 003 | Model output cannot directly authorize a consequential capability | not pinned |
| 004 | Every side-effecting tool call has a resolved capability | not pinned |
| 005 | Every consequential capability has explicit authority requirements | not pinned |
| 006 | Unknown tool capability cannot inherit SAFE | not pinned |
| 007 | Taint cannot disappear without an explicit trusted transition | not pinned |
| 008 | Memory cannot independently grant execution authority | not pinned |
| 009 | Conflicting evidence cannot silently become consensus | not pinned |
| 010 | Audit integrity does not equal semantic correctness | not pinned |

> **Grep trap when auditing these.** `tests/contracts/test_phase1_contract.py` pins
> `INVARIANT-003…006` — but those belong to an earlier v0.3.x "Phase 1 release" with
> *different meanings* (003 = structured message semantics, 006 = "performance claims are
> benchmark-derived"). The numbers collide; the invariants do not.

**A second reading of this principle, from the grill:** verification must test more than
"did the expected outcome occur?" — `forensic-grill` §10 ("VERIFICATION GRILL") asks what a
hash does and does not prove, and `forensic-grill` §4 ("FALSE GREEN TEST") asks whether a
system that believes it succeeded can be caught. That is why invariant 010 exists as a *named* invariant rather
than an assumption.

---

## 7. Sixth principle — shadow mode, not rewrite

**The rule.** Instrument → observe → compare → enforce. Measure disagreements *before*
anything is enforced.

**Explicitly forbidden:** swapping the gate out for the resolver and hoping.

**Where this stands (2026-09-22).** Phase 5 delivered the recorder
(`src/msb_v3/governance/shadow.py`) and a corpus run
(`scripts/probe_governance_shadow_corpus.py`, `docs/audits/governance-hardening-shadow-report.md`).
Two results are on record and both argue against enforcing yet:

- **The resolver resolves 16 of 68 corpus entries (24%)** — 0 of 15 obfuscated, 0 of 5
  multilingual. False-allow is **0/56**, but that safety is carried by the
  **UNKNOWN → BLOCK fallback**, not by resolution. Wiring this resolver in today would
  add no safety while still blocking everything but 16 entries.
- **The disagreement classification is structurally uniform** against the post-Phase-0
  baseline (65/68 in one class), because that baseline BLOCKs every unregistered raw
  string. The high-signal classes require the *pre-fix* baseline, which no longer exists
  in code and is therefore reconstructed and labelled as such in the report.

---

## 8. Seventh principle — the smallest behavioral change first

**The rule.** The smallest change that alters behaviour is the highest-value change. Do
that first; do not skip ahead because a later component looks more sophisticated.

**Where it came from.** Phase 0 is a sentinel and a policy branch — a few lines — and it
closed the actual escape. The registry, manifests, resolver and decision object that
followed are all structural, and *none of them would have helped* if `nuke` still
resolved to SAFE.

**Corollary — the ordering law.** Each phase ends in a green gate:

```
IMPLEMENT → TEST → ADVERSARIAL TEST → RESOURCE CHECK → AUDIT CHECK → REVIEW → GREEN → NEXT PHASE
```

**If any critical check fails: STOP.** Do not stack new architecture on an unverified
layer.

---

## 9. Program structure — the grill's topics are the phase plan *(synthesis)*

The correspondence between the forensic grill's 33 sections and `PLAN.md`'s phase plan is
the strongest evidence that the blueprint synthesised the grill into an ordered program.
Mapping, with the phase that addresses each topic:

| Grill section | Addressed by |
|---|---|
| `forensic-grill` §6 ACTION GATE GRILL | Phase 0 (UNKNOWN verdict) |
| `forensic-grill` §7 TOOL ESCAPE | Phase 8 (tool escape tests) |
| `forensic-grill` §8 MODEL COMPROMISE | Phase 13 (MoIE as challenge, not authority) |
| `forensic-grill` §9 ZERO-MODEL SAFETY | Phase 0 / the "Fourth principle — fail closed" disposition |
| `forensic-grill` §10 VERIFICATION GRILL | Phase 12 (green-gate expansion) |
| `forensic-grill` §11 AUDIT GRILL | Phase 18 (receipt + versioning) |
| `forensic-grill` §12 MEMORY POISONING | Phase 10 (memory governance) |
| `forensic-grill` §13 RAG GRILL | Phase 11 (RAG conflict) |
| `forensic-grill` §14 MULTI-MODEL GRILL, §15 SPECIALIST COLLUSION | Phase 14 (independence tests) |
| `forensic-grill` §17 CRASH GRILL, §19 RESOURCE GRILL, §20 88% DISK GRILL | Phase 16 (chaos + thresholds) |
| `forensic-grill` §25 MEMORYSTORE DEPRECATION | Phase 17 (MemoryStore cleanup) |
| `forensic-grill` §27 ADVERSARIAL INPUT | Phase 15 (adversarial corpus) |
| `forensic-grill` §30 THE REAL TEST, §31 THE BIGGEST FAILURE | Phase 19 (final production gate) |

Phases 6, 7 and 9 (tier-4 fail-closed, DAG capability closure, taint integration) have no
single grill section; they are the *composition* consequences of `forensic-grill` §6–§9 — what happens when
low-risk nodes compose into a consequential mission.

**Delivered state lives in `PLAN.md` → "Phase status — reconciled 2026-09-22"**, which is
the maintained record. As of that date: phases 0–6 landed, Phase 2 never reached the
executor, and phases 7–19 are unstarted.

---

## 10. Recorded limitations — what this program does *not* claim

Stated here so the blueprint cannot be read as promising more than it delivers:

1. **The gate's recall is unchanged by any of this.** Moving to capabilities does not
   make token counting smarter; it changes what is authorised, not what is recognised.
2. **UNKNOWN → BLOCK is aggressive.** A read-only unknown is refused. The
   `_refuse_unknown_read_only()` hook exists precisely because this is a known cost.
3. **The resolver's coverage is low (24%** as measured 2026-09-22**)** and its safety
   comes from the fallback, not its judgement.
4. **The governance metrics are not wired.** The instrumentation this blueprint calls for
   is, as of 2026-09-22, absent from `src/` — verified by name and by looser variants.
5. **Invariants 003–010 are assertions in prose**, not tests.
6. **Nothing here changes execution yet.** The resolver, manifests and decision object
   are deliberately *not* in the live path.

---

## 11. Relationship to the other blueprints in this repo

`msb-v3` carries **several** blueprints with **overlapping §-numbering**. They are not
interchangeable, and a bare `§N` in this codebase is ambiguous unless it names its parent:

| Blueprint | Scope | Its §-references in code |
|---|---|---|
| **This file** (reconstructed) | Governance hardening — capability-centric, UNKNOWN ≠ SAFE | none recovered — see "Citation corrections" |
| `docs/blueprints/2026-09-09-production-hardening.md` | Production readiness; **CLOSED 2026-09-12** | `§3.2`, `§3.3` (quarantined settings, keep-alive) in `src/msb_v3/core/config.py` |
| Steward blueprint (`AIL-MoIE-Project-Steward`) | Project-state layer, health vector | `§53`, `§54` in `src/msb_v3/steward/` |
| `convergence-to-12` | Convergence programme | `§12` in `docs/governance/authority-model.md` |
| `unified-architecture` (written in code as *spec*) | Tool registry / capability table | `unified-architecture` §5, §6 in `src/msb_v3/tools/` |
| `PRODUCTION-CLOSURE-001` P3 / O3 | Authority closure | `docs/releases/O3-AUTHORITY-CLOSURE-PLAN.md` |
| `docs/blueprints/plans/m1-governance-node-architecture.md` | Node architecture | `§5` in `src/msb_v3/harnesses/base.py` |
| Meta/multi-agent blueprint | Worker envelope, failure compiler | Meta-System blueprint `§7`–`§31` in `src/msb_v3/meta/` |

*(Table compiled by grepping the tree for §-references; it is a map of what exists, not a
claim about how these documents relate to one another.)*

---

## 12. Citation corrections

**Corrected here:** `PLAN.md` previously attributed its list of 15 governance metrics to
"blueprint §53". That number belongs to the **Steward blueprint** — §53 is its project
health vector (nine axes with per-axis GREEN/YELLOW/RED/UNKNOWN; Steward blueprint §54
"UNKNOWN != GREEN") — see `src/msb_v3/steward/state.py` and
`tests/steward/test_project_state.py`. It is a project-state report, not a Prometheus metric
set. The metric list is `PLAN.md`'s own and now says so.

**Left unresolved on purpose:** the reconstructed blueprint asserts **no section numbers**.
Any future `§N` citation to *this* file would be unresolvable by construction, and
inventing a numbering to make existing citations look satisfied is exactly the move this
reconstruction exists to avoid.

---

## 13. Document history

| Date | Change |
|---|---|
| 2026-09-22 | File created as a reconstruction. Closes the dangling `docs/blueprints/governance-hardening.md` citation in `PLAN.md` and `docs/audits/governance-hardening-baseline.md`, and corrects the Steward blueprint §53 misattribution. |
