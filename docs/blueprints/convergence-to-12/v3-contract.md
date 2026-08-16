# MSB v3 — Release Contract (M0: Scope Lock)

**Status:** DRAFT · **Owner:** Wilson · **Dated:** 2026-08-16 · **Freeze active:** yes (Blueprint Rule 3)

## The one-sentence outcome

> MSB v3 is a narrow, local-first, governed agent runtime that takes a real task
> from request to a verified, evidence-backed result — and refuses, records, and
> recovers when a model, tool, or permission fails.

## Target user

A single technical operator (Wilson) running MSB on a local Mac (the sovereign
node), with Ollama/Qdrant local and optional cloud models. Not yet: multi-user,
multi-tenant SaaS, or remote deployment.

## Primary problem

Autonomous agents that touch real systems need **governance, evidence, and
recovery**, not just capability. The current runtime proves the pieces
(governed tools, evidence spine, replay, factory review, audit chain); the
convergence goal is to prove ONE workflow end-to-end through all of them.

## Canonical workflow (shape — exact selection is M1)

```
Request → classify → plan → authorize → execute → observe → verify → record → report | recover
```
Every step writes evidence; every consequential action passes the Guard before
execution; failures land in a defined terminal state, never silent continuation.

## Supported models & tools (in scope)

- **Models:** Ollama local (default), configured frontier/cloud adapters, and
  the diverse-LLM reviewer panel (builder-model ≠ reviewer-model enforced).
- **Tools:** governed tool registry (read_vault / search_query / shell / write /
  research) behind the ActionGate; CLI provider is best-effort isolation, not a
  sandbox — sandboxing is a documented limitation, not a claim.
- **MCP:** local stdio adapter + HTTP bridge (Make/n8n) exist and are gated.

## Safety boundaries (fail-closed by default)

1. No consequential action without a Guard decision (ALLOW / APPROVE / DENY).
2. Denied, indeterminate, malformed, or unavailable authorization → no action.
3. Audit chain is append-only at the SQL layer; repair() requires operator auth.
4. Chain-tip anchor + off-box notary; anchor key trust boundary documented.
5. Unknown state halts (recovery never guesses).
6. Multimodal interfaces are **out of the default surface** (flag-gated, stub).

## Evidence guarantees

Every meaningful event has: who/what/when, model + provider, policy + version,
capability + authority, result + failure context — recorded in the Evidence
Spine (decision → execution → verification), linked to the append-only audit
chain, with an independently verifiable anchor.

## Explicit non-goals (v3)

- No new subsystems during M0–M3 unless a core-path failure demands one (Rule 3).
- No more modalities, personas, orchestrators, providers, or dashboards until M7.
- No multi-node/mesh, no agent factory automation, no autonomous evolution —
  these are v4 candidates (blueprint §7).
- No claims of "production-ready" until M6 evidence exists; the README and
  metrics must match runtime behavior (M3).

## Release language

MSB v3 ships when the 12/10 statement is true (blueprint §8): used continuously
for real work, completes a valuable workflow reliably, fails safely and explains
itself, builds/reviews a change through its own factory, an independent user can
operate it, and the improvement is evidenced — not claimed.

## Exceptions to the freeze

Any new work outside this contract requires a dated written exception in the
parking-lot record (proposed value, dependency, trigger for reconsideration).
