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

## Rejected / parked-forever (with reason)

| Idea | Reason |
|---|---|
| More agent personas / orchestration layers | Contradicts convergence: five primitives beat forty agents (blueprint §7.3) |
| Polished dashboards | No evidence they improve value; telemetry already exposed via Prometheus |
| Generalized autonomous behavior | Deliberately out of scope until M7 — autonomy without governance evidence is a liability |
