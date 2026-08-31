# MSB v3 — Authority Model (dual-governance)

**Status:** ACCEPTED 2026-08-31 · **Blueprint:** PRODUCTION-CLOSURE-001 P3 / O3
**Decision:** Option B — dual-governance, with `ActionGate` as the enforcement
boundary. Supersedes the interchangeability-checklist wording "a production-path
capability invocation must be impossible without passing through the *Gateway*".

---

## The two layers

MSB v3 has **two orthogonal governance layers** on the canonical path. Both are
required; neither substitutes for the other.

| Layer | Module | Role | Blocking? |
|---|---|---|---|
| **Gateway** | `msb_v3/gateway/route.py` | Records the **compute decision** (which backend, estimated cost, capability set) into the audit chain *before* any model call. | No — best-effort audit. A gateway outage degrades provenance, never the run. |
| **ActionGate** | `msb_v3/agent/safety.py` (`ActionGate`), applied via `agent/handle.py`'s `SafeProvider(provider, gate, ...)` and `tools/runtime.py::run_gated`. | **Enforces** every tool / capability execution: kill-switch check, approval gate, capability whitelist, contained execution, audit verdict. | **Yes.** Fail-closed. Every execution resolves to exactly one verdict: `allowed` / `denied` / `approval-required` / `error`. |

## The production invariant (revised O3 criterion)

> **Every entry path that can cause a capability or tool to execute must route
> that execution through `ActionGate` (via `SafeProvider` / `run_gated`) before
> the capability runs. There is no third state: each attempt resolves to
> `allowed`, `denied`, `approval-required`, or `error` — never silent
> execution.**

The Gateway records the decision; the ActionGate is what makes an un-authorized
capability *impossible to run*. For a sovereign single-operator runtime, routing
compute-decision audit and capability enforcement through two purpose-built
layers is stronger than forcing both through one chokepoint — the Gateway would
otherwise become a second orchestration system (explicitly out of scope in the
convergence blueprint §12).

## Why not Option A (gateway-as-enforced-doorway)

- The canonical path already enforces at `SafeProvider` / `run_gated`; making
  `gateway_route()` also raise would add a second fail-closed point with
  overlapping responsibility and a real regression surface across every entry
  path.
- `tools/runtime.py::run_gated` already provides the "no third state" property
  (four explicit verdicts, audited).
- Option A's value — "impossible to bypass" — is delivered by proving
  `ActionGate` coverage on every path, which Option B does directly.

## What P3 must still prove (acceptance)

1. [x] Decision written with rationale — this document.
2. [ ] All 14 entry paths mapped from entry → first capability execution;
       matrix in `docs/releases/O3-AUTHORITY-CLOSURE-PLAN.md` has zero
       `UNKNOWN`.
3. [ ] Every path reaches `ActionGate` before any capability runs, or the
       path cannot execute capabilities at all (read-only) and is marked so.
4. [ ] `tests/architecture/test_authority_boundary.py` — one adversarial case
       per path asserting `allowed`/`denied`/`approval-required`/`error`,
       never silent execution — exists and is a **blocking** CI job.
5. [ ] `test_gateway_canonical.py` updated: it may keep the structural
       import check, but the authoritative per-path proof is the new suite.

## The 14 entry paths (to be mapped in step 2)

`POST /agent/handle` · `POST /chat` · MCP bridge (`/mcp/proxy`) · `cron`
scheduled job · `wake` resident loop · `POST /hook/<id>` webhook ·
`automation` brain · `factory` pipeline · `flywheel` turn · direct provider
call (`resolve_client`) · `replay` engine · internal import → tool-registry
call · background asyncio task · `/v1` OpenAI-compat adapter.

For each: does execution reach `SafeProvider` / `run_gated` / `ActionGate`?
`ALLOW-through-authority` / `DENY` / `UNKNOWN` (bug — blocks closure) /
`READ-ONLY` (path cannot execute a capability; documented).
