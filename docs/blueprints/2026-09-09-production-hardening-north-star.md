# MSB-v3 — North Star Production Blueprint
### My (Buffy's) own blueprint · North Star method · 2026-09-09

> **Status:** proposal for owner review — my independent take on the hardening
> program, reframing the same six liabilities through the North Star discipline:
> one-sentence outcome, layers, decision cards, and baseline→result measurement.
> Companion to `2026-09-09-production-hardening.md` (the authoritative plan);
> this one adds the measurement and decision machinery that plan assumes.
> Every baseline below was measured this session, not estimated.

---

## The North Star

> **A sovereign agent runtime the operator trusts like a utility: every request
> closes a governed, evidence-backed loop; the system survives a clean rebuild;
> and every degradation is visible, alertable, and explained — so the operator's
> attention goes to the work, not the machinery.**

Breakdown:

1. **Trust like a utility.** The operator never wonders whether :8766 is up,
   whether the notary ran, or whether the model serving is the model configured.
   The system's state is knowable at a glance and consistent with what it reports.
2. **Every request closes a governed, evidence-backed loop.** The runtime's
   existing strength — request → evidence → audit — is preserved and made fully
   reconstructable from telemetry alone.
3. **The system survives a clean rebuild.** Packaging exists, provisioning is
   reproducible, restore is proven — wipe and rebuild from clone + backups works.
4. **Degradation is visible, alertable, explained.** No silent fallbacks, no dead
   seams, no 273-failed-pulse noise running behind the curtain.
5. **The operator's attention goes to the work, not the machinery.** This is the
   outcome: operator time released from babysitting, spent on the actual tasks
   the runtime exists to run.

## Why this North Star, and not another

Most hardening programs sell one of four things:

1. **Green CI** — "the pipelines pass."
2. **Packaging** — "here's a Dockerfile."
3. **New models / features** — "we upgraded the frontier."
4. **A release** — "we shipped v1.0."

Each is a thing. None is the outcome. A green CI leg is a floor, not trust — the
four pre-existing red legs on `main` are symptoms of real gaps (fresh DBs come out
unstamped, the closure plan claims SHAs that don't exist), and fixing the CI leg
without the gap just paints over it. A Dockerfile without a proven clean build is
ceremony. A frontier upgrade is capability expansion, explicitly deferred. A
version number is not trust.

The outcome is the direction: a runtime whose state is *knowable*, whose core loop
*always closes*, that *survives a rebuild*, and whose failures are *loud*. That is
the category shift — from "a project that runs on my box" to "infrastructure."

## The loop — hardening, not features

```
AUDIT          measure the actual runtime surface (jobs, models, refs, failures)
   ↓
DECIDE         per liability: five dimensions → RETIRE / QUARANTINE / FIX
   ↓
INTERVENE      simplest thing that works
   ↓
VERIFY         baseline → change → observe → compare → result (durable evidence)
   ↓
WATCH          the new state is observed continuously (metrics, alerts, jobs)
   ↓
SURFACE NEXT   re-run the audit; the next liability surfaces
   ↓
REPEAT
```

This is not a pipeline — it's a loop, and the hardening plan's six priorities are
one pass through it. Each cycle produces three things:

1. **A measurable result** — the intervention worked or it didn't, and you can see
   by how much (baseline vs. after).
2. **More knowledge about the system** — decision cards, runbook entries, evidence
   trail entries; the manifest gets more precise.
3. **More operator trust and capacity** — attention released from babysitting,
   plus a system whose own documentation has compounded.

That third point is the one most hardening programs ignore, and it's the reason the
North Star survives the next model upgrade, the next repo move, the next box.

## The three layers of the architecture

### Layer 1 — Truth (audit)

**What it is:** the live inventory of what's actually running, wired, degraded,
dormant, or external — from launchd jobs, `.env`, routing code, grep sweeps, live
process checks, and CI failure output.

**What it produces:** the runtime manifest, residual-reference reports, the job
inventory, the failure taxonomy.

**Why it's first:** you can't decide what to retire without knowing what exists, and
you can't prove a baseline without measuring the before. Memory lies — the Trinity
hot-reload daemon looked "fine" while accumulating 273 failed pulses; only the
`/tmp/trinity_hot_status.json` measurement exposed it.

**How it's measured:** every claim traceable to a check (a grep, a `launchctl list`,
a `curl /health`, a CI log line). No "we think it's dormant" — a search result or a
process listing.

### Layer 2 — Decision

**What it is:** per liability, a decision card scored on the five dimensions, with
one of three outcomes.

| Dimension | Question |
|---|---|
| Evidence | Do we actually know this is dead/degraded/live? |
| Impact | How much does it cost us to keep or remove it? |
| Readiness | Can we actually remove/replace it safely right now? |
| Economics | Is the intervention worth the effort/risk? |
| Risk | Could the change create more damage than the liability does? |

**The three outcomes (this system's version of DO NOTHING / IMPROVE / AUTOMATE):**

- **RETIRE** — remove the path entirely (code, env, status reporting), with a
  written reason, what we learned, what would change our mind, when to revisit, and
  the restore path. This is not failure; it's professional engineering.
- **QUARANTINE** — keep the path but explicitly flagged, isolated, and documented
  as dormant, with a live plan to activate or a revisit date. Used sparingly.
- **FIX** — repair the path (or replace it with something versioned with the system).

**Why decision is a separate layer:** most hardening programs skip it — they either
assume everything must be fixed or they silently delete from memory. Separating it
means every retirement and every repair is reconstructable six months later, and the
operator can challenge the reasoning.

### Layer 3 — Intervention + Verification

**What it is:** the simplest thing that works, then proof of whether it worked.

```
BASELINE → CHANGE → OBSERVATION → COMPARISON → RESULT
```

The same measurements taken at baseline are taken after. The result is
"before: X, after: Y, difference: Z, evidence: [config diff / health check / test
run / audit entry]" — never "trust us, it's fixed." If the result is less than
hoped, that's reported honestly; the system learns, it doesn't hide.

### The fourth layer — compounding (what most hardening programs miss)

The three layers deliver a measurable result per liability. The fourth layer is what
makes them accumulate: **every intervention leaves durable artifacts that make the
next one cheaper.**

- The runtime's core strengths (governance, auditability, sovereignty) survive the
  process, because every intervention keeps the evidence spine intact — retiring a
  seam is done *through* the governed loop, not around it.
- Decision cards, runbooks, and evidence entries compound into a knowledge base:
  the next hardening pass, the next CI failure, the next repo move is cheaper
  because the picture is already drawn.
- The operator's trust compounds: each verified intervention is proof the loop
  works, which is the thing the runtime is ultimately for.

Technology is swappable; accumulated evidence and documented decisions are not.
That's the moat — and it's the same moat msb-v3 sells its own operator.

---

## The decisions — application to the six priorities

Each liability gets a mini decision card, grounded in baselines measured this
session (2026-09-09).

### D1 — Frontier path
- **Baseline:** 0 successful frontier calls; circuit open since ~Aug 21 (DeepSeek
  402); `plan`/`verify_synth` task kinds still default to frontier in
  `fabric/model_router.py`; `OPENAI_FRONTIER_URL/MODEL/KEY` set; wake loop degrades
  to local with a recorded reason; 300s circuit cooldown on the native provider.
- **Evidence:** high (measured — 402 responses, circuit state, residual refs).
- **Impact:** low-to-moderate — dead seam costs routing complexity and one wake-loop
  failure per cooldown window; not costing money.
- **Options:**
  - **FIX** — fund the DeepSeek account, re-prove the seam live, restore frontier
    routing. (Capability expansion; deferred unless funding is coming.)
  - **RETIRE** — remove the seam: task-kind defaults route local-only, delete
    circuit-breaker + residual config, document the restore path. Reversible in
    ~15 minutes of config if a frontier ever returns.
  - **QUARANTINE** — keep dormant with a documented revisit date.
- **My recommendation: RETIRE unless funded frontier is coming within the quarter.**
  A dead seam is a liability, and the designed local fallback has been the actual
  product since Aug 21. Retiring is honest; keeping it "just in case" is the
  anti-pattern.
- **Owner decision required** (funding is the operator's call, not mine).

### D2 — Dormant providers
- **Baseline:** Anthropic provider (no key), llama.cpp (default
  `gemma-4-12b-it` GGUF, weights absent), Paseo adapter (daemon unreachable →
  reports FAILED). Residual env vars, imports, and status reporting present.
- **Options:** RETIRE (remove code + env + status reporting; git history preserves
  them; `git revert` restores) — or QUARANTINE only where a live activation plan
  exists.
- **My recommendation: RETIRE all three.** Nothing calls them; keeping them costs
  startup surface and status-reporting noise.

### D3 — Hot-reload sovereignty
- **Baseline:** external `~/.trinity/hot_reload.py`, launchd
  `com.lordwilson.trinity-hot-reload`, 60s pulses; was 273 failed pulses since
  Sep 5 (dead coder model), now 0 after this session's prune; still outside the
  governed repo.
- **Options:** **ABSORB** (move keep-alive into msb-v3 `scripts/` + a launchd
  template, versioned with the system, plumbed through the same supervision) or
  **REPLACE** (in-process keep-alive in the gateway).
- **My recommendation: ABSORB.** It's ~50 lines; versioned with the core, no
  external dependency, and the launchd template pattern already exists in the repo.

### D4 — Surface-area reduction
- **Baseline:** 6+ independent launchd jobs (gateway, qdrant, 2× notary,
  auto-repair, hot-reload) + GH Actions runner disabled; the two notary jobs
  duplicate one function; the gateway and qdrant are separate failure domains
  (correct); history of orphaned jobs pointing at dead paths.
- **Options:** consolidate the duplicate notary to one job; fold hot-reload under
  msb supervision; keep qdrant separate (different failure domain — right call).
- **My recommendation:** consolidate to the minimum independent failure domains
  that preserve KeepAlive + recovery semantics (target: ≤4).

### D5 — Packaging
- **Baseline:** none. launchd-only. `scripts/run.sh` hardcodes a macOS-only python
  path — this exact path broke CI on hosted runners this week.
- **Options:** minimal Dockerfile capturing runtime deps + entrypoint + config
  surface, with a clean-build acceptance test: build → boot → `/health` ok → one
  governed loop closes. Explicitly not multi-tenant / cloud-native.
- **My recommendation: build it**, minimal, with the acceptance test as the exit
  criterion (reproducible rebuild is one of the North Star's four legs).

### D6 — Governed-loop observability
- **Baseline:** `/metrics/prometheus` exists (counters were half-wired; fixed
  2026-08-12); ActionGate decisions, evidence-spine writes, and audit-chain appends
  are **not** first-class metrics; 4 pre-existing CI red legs (schema stamping,
  closure drift, factory-gate event loop, harness-gate evidence).
- **Options:** promote governance events to structured metrics; define alerts on
  BLOCK/FAIL-rate anomalies + prolonged local-inference degradation; triage the red
  legs (fix or explicitly waive each with a written reason).
- **My recommendation:** promote + alert, and fix the schema-stamping leg
  (mechanical — stamp DBs at boot or in CI); waive or repair the other three
  explicitly.

---

## The measurement discipline — the shared goal, in numbers

### Baselines (measured, 2026-09-09 — the "before")

| Liability | Baseline |
|---|---|
| Frontier | 0 successful calls; circuit open since ~Aug 21; residual refs in router defaults + `.env` |
| Dormant providers | 3 with no key/weights/daemon; status reporting present |
| Hot-reload | external `~/.trinity`; 273 failed pulses (Sep 5→9) → 0 after prune |
| Surface area | 6+ independent jobs; 2 duplicate notary jobs; GH runner disabled |
| Packaging | no Dockerfile; `run.sh` macOS-only path (broke CI) |
| Observability | governance events not metric'd; 4 pre-existing CI red legs |

### KPIs — repeated after each intervention

| KPI | Target |
|---|---|
| Residual references to retired paths | 0 (grep-verified) |
| External dependencies for critical behavior | 0 |
| Independent failure domains | ↓ from 6+ to ≤4 |
| Clean build → healthy instance | reproducible, time-bounded |
| Governance-event metric coverage | 100% of ActionGate / evidence-spine / audit-chain appends |
| Alert coverage | BLOCK/FAIL anomaly + degradation alertable |
| CI red legs | 0, or explicitly waived with written reason |

### The value-conversion engine (honest version)

Capacity released — operator attention, disk, CPU, CI minutes, startup surface —
is **not** automatically value. The conversion is: released capacity → converted
into trust (fewer surprises), uptime (fewer dead paths), and rebuild capability
(proven packaging). Never claim "production-clean" without the exit criteria met;
a leg that stays red is reported red, not painted green.

---

## The anti-patterns — what breaks this North Star

- **Skipping the baseline.** "We know what's live" is not a baseline — the whole
  point of Layer 1 is that memory lies (the Trinity daemon again).
- **Making RETIRE the default to dodge work.** Every decision card must be real;
  conversely, keeping a dead seam "just in case" is the same failure in reverse.
- **Faking the value-conversion.** "It's fixed" without the after-measurement is
  an assertion, not proof.
- **Gold-plating the packaging.** Docker creeping into multi-tenant or cloud scope
  contradicts the single-operator design.
- **Observability as a dashboard dump.** Metrics without alerts are decoration;
  the North Star's fourth leg is *alertable* degradation.
- **Reporting only the greens.** The 4 pre-existing red legs stay visible in
  writing until resolved or explicitly waived.
- **Collapsing the layers.** Auditing, deciding, and intervening in one blur makes
  it impossible to see where the chain is weak.
- **Treating CI green as production-clean.** CI passing is a floor; the North Star
  is trust, which CI alone cannot measure.

---

## Why this structure is the best one (for this system)

1. **It forces evidence before decision** — which is the runtime's own design
   axiom: never trust unverified output. The hardening program follows the same
   law it governs by.
2. **It separates finding from fixing from proving** — audit produces the manifest,
   decision produces the card, intervention produces baseline→result. Each has its
   own artifact and its own discipline; the chain is honest because the links are
   distinct.
3. **It makes RETIRE a real product.** Retiring the frontier seam with a written
   reason, revisit condition, and restore path is professional engineering — and it
   makes the FIX recommendations credible, because they're chosen from a real set
   of options, not from a default assumption that everything must be repaired.
4. **It forces measurement at the moment of intervention** — the hardening plan's
   validation gates become measurement discipline: baseline before, same
   measurement after, explicit comparison, durable evidence.
5. **It compounds internally.** Decision cards, runbooks, and evidence make the
   next hardening pass, the next CI failure, and the next repo move cheaper.
6. **It works at the scale msb-v3 is.** Single operator, no team required — the
   discipline is the architecture.

---

## What it requires to work

1. **Actually measure baselines** — the six above are measured; re-measure before
   each intervention.
2. **Actually produce a decision card per liability** — five dimensions scored,
   rationale written, alternatives considered, revisit condition defined, restore
   path stated where relevant.
3. **Actually recommend RETIRE when it's the right call** — with the restore path.
   Not avoided.
4. **Actually keep the operator in the loop** — the frontier decision is the
   operator's call; no unilateral retirement of a funded path.
5. **Actually report honestly, including the misses** — red legs stay red in
   writing until fixed or waived.
6. **Actually keep watching after each intervention** — jobs, metrics, alerts,
   grep sweeps. The continuity is the product.
7. **Actually compound** — every step lands a durable artifact (config diff,
   decision card, runbook entry, PZS note, evidence-trail entry).

---

## Summary

The North Star: **a sovereign agent runtime the operator trusts like a utility —
every request closes a governed, evidence-backed loop; the system survives a clean
rebuild; degradation is visible, alertable, and explained.**

The architecture that serves it has three layers plus compounding: **Truth** (the
audit — measured, traceable), **Decision** (five dimensions → RETIRE / QUARANTINE /
FIX, with the reasoning visible), **Intervention + Verification** (baseline → result,
durable evidence), and **Compounding** (artifacts that make the next pass cheaper,
trust that accumulates with each verified intervention).

The measurement discipline is the shared goal in numbers: the six baselines above,
the KPI table, repeated after each intervention. Not guessed. Not faked. Measured.

The anti-patterns name the ways it breaks — skipping the baseline, retiring to dodge
work, faking the conversion, gold-plating the packaging, dashboard-without-alerts,
green-only reporting, collapsed layers, CI-as-truth. The requirements make it real.

Everything else is downstream of this.

---

## Next actions

1. **Decision card #1: frontier** — operator decides FIX / RETIRE / QUARANTINE
   (funding is the operator's call).
2. **Layer 1 leg: dormant-provider audit** — extend the residual-call grep sweep to
   env vars + status reporting + imports; produce the evidence report.
3. **D3 intervention: absorb hot-reload** — move keep-alive into the repo
   (`scripts/` + launchd template), versioned; verify 0 failed pulses.
4. **D4 intervention: surface-area pass** — consolidate the duplicate notary,
   fold hot-reload under msb supervision; re-inventory jobs.
5. **D5 intervention: minimal Dockerfile** — with the clean-build acceptance test
   (build → boot → `/health` ok → one governed loop closes).
6. **D6 intervention: governance metrics + alerts** — promote ActionGate /
   evidence-spine / audit-chain events to metrics, add BLOCK/FAIL + degradation
   alerts, and fix or waive the 4 CI red legs (schema stamping is the mechanical
   one).