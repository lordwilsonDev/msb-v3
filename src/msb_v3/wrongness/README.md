# Wrongness Engine MVP

The epistemic termination protocol, mechanized (SPEC §VI–VII of
`~/Documents/Vault/30_Architecture/Wrongness-Engine/00_Acknowledgment.md`).

It sits **above** architecture claims and systematically tries to falsify
them.  Per the §V anti-architecture-theater rule, every component here must
beat a simpler alternative — and the by-hand retrospective
(`01_Retrospective-ByHand.md`) is the cheaper alternative this MVP is built
to beat.  Hence: **deterministic, stdlib-only, no new dependencies, no LLM.**
The LLM critic / Qdrant registry of the full §VI spec earn their place only
if they beat what's here.

## What it does

- **Claim registry** — `claims/` (JSON).  A claim is a falsifiable statement
  + the conditions that would disprove it + an optional deterministic check.
- **Seven passes** — `passes.py`.  Attack / Counterexample / Assumption /
  Boundary / Incentive / Scaling / Failure-cascade.  Boundary + Counterexample
  earned the most hits in the by-hand run; the templates mostly *systematize
  checks that exist but are skipped under ordinary acceptance*.
- **Deterministic checks** — `checks.py`.  The "5 lines of shell" power:
  call-site count, stat mode, tracked-secret scan, porcelain state, type
  probe, scorecard gate.  These are the external adjudicator the recursion
  terminates at.  Every result carries **machine-readable evidence links**
  (M6): `path:line:snippet` pointers behind the verdict, persisted by
  `save_result` — no prose-parsing to consume the evidence.
- **Human read-path** — `report.py` + the `report` CLI (M7).  Renders a run
  as actionable markdown: findings grouped by tier, evidence links, an
  explicit **investigation path** for every CHECK finding (where to look,
  what to answer), and both sides of the evidence for CONFLICTING verdicts
  so a human can actually decide.  A CHECK that only exists as JSON is as
  good as never raised.
- **Vault claims home (M8)** — claims are authored where decisions live:
  `~/Documents/Vault/30_Architecture/Wrongness-Engine/claims/` (schema
  guide + `_TEMPLATE.json` + the gate-on-the-gate claim).  The `run-all`
  CLI runs every claim in a directory in one command; the `validate` CLI
  is the authoring hook; the `claims_valid` check is the schema-conformance
  gate (underscore-prefixed files are skipped).
- **Escalation policy** — `policy.py`.  **The load-bearing piece.**  Four
  verdict states (the AVeriTeC standard): `ESCALATE` (evidence-backed
  failure-assertion), `CONFLICTING` (evidence points BOTH ways — human
  decides), `CHECK` (investigation prompt / UNKNOWN — never escalates on its
  own), `NOTE` (confirmed / below escalation).  Plus the M4 rubric:
  `urgency = severity × consequence`, and `passes_agreeing()` consensus
  (which never escalates on its own).  The CHECK-routing decision moved FP
  from 28.6% to 16.7% in the by-hand corpus.
- **Corpus replay** — `corpus/byhand_21.json` + `engine.run_replay()`.  The
  MVP must reproduce the by-hand verdict: **PEDR 1.0, FP 16.7% / 28.6%,
  decision VALIDATED** — or it loses to the cheaper alternative it was built
  to beat.
- **Held-out corpus (M9 progress)** — `corpus/heldout_fleet_r1.json`: 11
  fleet Round-0/1 decisions with outcomes recorded by the fleet harness's
  OWN deterministic gates (SPEC §10 scorecard, 5-fold CV, fresh-set
  validation), not the engine author's by-hand judgment.  Test-enforced
  pin-free (no `escalation_class`/`strongest_pass`), so recorded-routing
  and blind replay agree.  Replay: **PEDR 1.000 (6/6), FP-assertion
  0.000, VALIDATED** — every real failure flagged, no confirmed claim
  escalated.

## Usage

```bash
python -m msb_v3.wrongness replay                        # by-hand corpus verdict
python -m msb_v3.wrongness replay --blind               # recorded routing off (M3)
python -m msb_v3.wrongness replay --held-out            # score each half separately
python -m msb_v3.wrongness replay --corpus src/msb_v3/wrongness/corpus/heldout_fleet_r1.json \
    --repo ~/specialist-fleet                           # held-out (M9) replay
python -m msb_v3.wrongness run claims/self_claim.json --repo .   # the engine on itself (M1)
python -m msb_v3.wrongness run claims/fleet_bakeoff.json --repo ~/specialist-fleet
python -m msb_v3.wrongness report claims/fleet_bakeoff.json --repo ~/specialist-fleet  # human read-path (M7)
python -m msb_v3.wrongness report claims/self_claim.json --repo . --out self-report.md
python -m msb_v3.wrongness validate <claim.json>                                 # authoring hook (M8)
python -m msb_v3.wrongness run-all ~/Documents/Vault/30_Architecture/Wrongness-Engine/claims \
    --repo ~/Documents/Vault                                                     # vault claims home (M8)
```

## First live claim

`claims/fleet_bakeoff.json` — *"a specialist fleet with deterministic routing
beats a single generalist"* — carries **three adjudicated gates** against
`~/specialist-fleet/results`: router (0.964 ≥ 0.90), code (0.84 vs 0.62,
4.64×), automation (0.467 vs 0.60 — **below the 5% band**).  Current
verdict: **CONFLICTING** — the engine states the fleet thesis honestly
(true for code, false for automation) instead of defaulting to a neutral
CHECK.

## Design constraint (mandatory, from the retrospective)

> The escalation policy, not the attack passes, is where the engine lives or
> dies.  Investigation-prompts must never escalate on their own.

Tests enforce this directly (`test_investigation_prompts_never_escalate`).
