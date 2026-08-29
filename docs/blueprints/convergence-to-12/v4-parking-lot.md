# MSB v3 → v4 Parking Lot

**Dated:** 2026-08-17 · **Rule:** no new subsystem enters the v3 shipping
surface without a dated written exception recorded here (v3-contract "Exceptions
to the freeze" + v0.3.0-rc1 baseline "Expansion freeze"). New ideas go here
with a proposed value, dependency, and trigger for reconsideration.

## Entries

| Date | Idea | Proposed value | Dependency | Trigger to reconsider |
|---|---|---|---|---|
| 2026-08-17 | Strong sandbox (filesystem root, network policy, CPU/mem/time budgets) — L9 | Real isolation instead of "best-effort" CLI provider | Core loop proven in M6 | A canonical-path tool needs write/execution beyond the current best-effort provider |
| 2026-08-17 | Full tenant isolation for chat LLM routing (L10 — RAG is scoped, chat isn't) | Adversarial tenant separation | Multi-user requirement | A second tenant actually uses MSB |
| 2026-08-17 | Multimodal (image/audio/screen/document) — currently stub-gated | Vision/audio in the governed loop | Core runtime finished; a real multimodal task exists | A real task needs it (no manufactured demo) |
| 2026-08-17 | Distributed sovereign mesh + node-to-node delegation (L14) | Horizontal sovereignty | Single-node reliability proven (M5–M6) | A workload exceeds one node |
| 2026-08-17 | Governed agent factory (mission → agent spec → build/test/red-team/register) | Automated agent creation | Factory dogfood proven on real changes | Repeated demand for new agent roles |
| 2026-08-17 | Continuous autonomous evolution (Ouroboros loop) | Self-improvement | All of the above; requires a constitutional approval boundary | Not before the factory + governance are battle-tested |
| 2026-08-17 | Formal DB schema versioning / migrations | Current schemas evolve ad hoc (known limitation #1) | Any schema change touches a production data file | A migration is actually needed |
| 2026-08-17 | Deleted-file diffs in factory `compute_changes` | Reviewer sees deletions | New-file diff fix shipped (v0.3.0-rc1) | A real change deletes a file |
| 2026-08-17 | Sensor plane / actuator plane / scientific-control engine (L13) | The system becomes a measurement-and-control substrate | Software governance mature | A real experiment needs it |

## Authorized Exceptions (with evidence)

### Meta-System (META-0 through META-8) — AUTHORIZED 2026-08-28

**Exception:** Meta-System modules exist in production surface (`msb_v3/meta/`)
with orchestration capabilities (META-5: OutcomeLedger, META-6:
AdaptiveOptimizer, META-7: SkillBridge, META-8: MultiWorkerBenchmark)
prior to formal spine prerequisite verification.

**Why Meta exists now:**
The Meta-System is the project compiler — it takes verified outcomes and
feeds them back into probability predictions and adaptive routing. Without
it, the factory pipeline operates blind: it builds and verifies, but
cannot learn from results. META-0 through META-4 established contract
types and a one-worker pipeline; META-5 through META-8 added cross-worker
comparison and self-improving routing. The system was built incrementally
with each stage verified independently.

**Prerequisite dependency chain:**
```
Spine (evidence/spine.py) → Meta-System → Factory pipeline feedback
```
The convergence blueprint stated the spine prerequisite was "not complete."
This assessment was stale. Evidence:
- `evidence/spine.py` is FROZEN in SURFACE.md (release-declared)
- 10+ modules import and use DecisionEvidenceStore (agent/handle,
  agent/providers, vesta/services, vesta/adapter, ops/discrepancy,
  replay/engine, plei/api, core/container, evidence/receipt)
- 82 test references across the test suite
- 9 dedicated spine tests in tests/evidence/test_spine.py
- The spine is actively written to on every governed execution

**What must be completed:**
- ProviderContract v1 conformance (DONE — 190 tests, CI green)
- Gateway canonical path (DONE — agent/handle.py calls gateway.route())
- Meta classification in SURFACE.md (see below)

**Who authorizes:** Operator (Wilson), convergence blueprint §20 Path B.

**What verification closes the exception:**
- Spine tests pass (✅ 9 dedicated tests)
- Spine used by production paths (✅ 10+ modules)
- Meta tests pass (✅ verified in CI)
- ProviderContract v1 exists (✅ 190 conformance tests)
- Gateway canonical path exists (✅ bypass test enforced)

**Classification:** Meta-System is classified as OPTIONAL in SURFACE.md
(experimental tier). This is honest: Meta exists and is tested, but has
not been battle-tested on real client workloads. Promotion to LOAD-BEARING
requires evidence that adaptive routing improves outcomes on production data.

## Rejected / parked-forever (with reason)

| Idea | Reason |
|---|---|
| More agent personas / orchestration layers | Contradicts convergence: five primitives beat forty agents (blueprint §7.3) |
| Polished dashboards | No evidence they improve value; telemetry already exposed via Prometheus |
| Generalized autonomous behavior | Deliberately out of scope until M7 — autonomy without governance evidence is a liability |
