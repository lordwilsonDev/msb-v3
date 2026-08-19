# The canonical journey

Every governed run in MSB v3 follows the same five stages, and every stage
leaves an inspectable artifact. This page is the map: **what happens → what
evidence it produces → where to look at it**. Read this page, run the demo,
and you have the whole system in five minutes.

```
   REQUEST          AUTHORIZATION        EXECUTION            VERIFICATION          EVIDENCE
   ┌─────────┐      ┌──────────────┐     ┌──────────────┐     ┌───────────────┐     ┌──────────────────┐
   │ you type │ ──►  │ MoIE gate    │ ──► │ governed     │ ──► │ grounded      │ ──► │ evidence receipt │
   │ a request│      │ + ActionGate │     │ tool DAG     │     │ checks (no    │     │ + spine + audit  │
   └─────────┘      └──────────────┘     └──────────────┘     │ LLM judge)    │     │ chain + replay   │
         │              │  BLOCK ────────────────┘             └───────────────┘     └──────────────────┘
         │              └──► denied before any model call (0 model calls)
```

The frozen path: `/agent/handle → intent → task DAG → ActionGate → governed
tools → verification → evidence spine → audit chain → replay`.

---

## The five stages

| # | Stage | What happens | Evidence artifact | Inspect here |
|---|-------|--------------|-------------------|--------------|
| 1 | **Request** | A raw string enters `handle()`. | The request itself, plus the interpreted intent (goals, declared permissions, privacy). | The `/console` request box; the run's `trace.intent`. |
| 2 | **Authorization** | MoIE (the keyword pre-filter) scores the request; the ActionGate decides `ALLOW` / `REVIEW` / `DENY`. A hard `BLOCK` denies **before the first model call** (0 model calls). | A **decision vertebra** on the Evidence Spine: policy version, policy result, capabilities requested vs granted, timestamp, chain-linked content hash. | The receipt's `authorization_decision`, `capability_requested/granted`, `policy_version`; the spine trail via `replay_task`. |
| 3 | **Execution** | The plan (task DAG) runs through a closed, fail-closed tool registry. Writes require pre-approval or park for review. | Per-task outputs + **execution vertebrae** (parent-linked to the decision). | The receipt's `execution_result`; the DAG in `trace.tasks`. |
| 4 | **Verification** | Every task is checked against **ground truth** — search returned ≥1 hit, synthesis is non-empty (not a fallback), the file exists with the expected heading. **No LLM judge anywhere in this path.** | Per-task verification receipts (`kind=grounded`, `trust=high`, `verdict=pass\|fail`) + a `deterministic_hash` that is a pure function of the recorded trace. | The receipt's `verification.grounded_checks` and `deterministic_hash`; `trace.execution[].verification`. |
| 5 | **Evidence** | One JSON receipt per run lands in the audit stream; vertebrae link into the tamper-evident hash chain. The replay engine can reconstruct the run from the event log. | The **receipt** (one line in `logs/audit.jsonl`), the spine trail, the audit chain, the replay reconstruction. | `/cockpit/audit` (the Evidence Stream panel); `/agent/tasks/{id}/replay`; `logs/audit.jsonl`. |

---

## The evidence language: rerun vs inferred-from-logs

Every completion report — the receipt, the console, the stream — says which
claims were **directly rerun** and which were **inferred from logs**. It
never conflates the two.

The receipt carries a `verification` section:

```json
"verification": {
  "basis": "rerun",                          // "rerun" | "decision-only" | "none"
  "hash_recomputed": true,                   // recomputed sha256 of the trace == recorded hash
  "grounded_checks": [                       // executed against ground truth — re-executable
    {"task_id": "research",  "check": "search_returned_hits",          "verdict": "pass", "trust": "high"},
    {"task_id": "synthesize","check": "synthesis_nonempty",            "verdict": "pass", "trust": "high"},
    {"task_id": "write",     "check": "file_written_with_heading",     "verdict": "pass", "trust": "high"}
  ],
  "note": "grounded checks were executed against ground truth and the deterministic hash recomputes from the recorded trace — these claims are rerun, not inferred",
  "log_inference": {
    "basis": "inferred-from-logs",           // reconstructed, never re-executed
    "covers": ["derived state", "transition legality", "projection consistency", "decision trail"],
    "where": "/agent/tasks/{run_id}/replay"
  }
}
```

The rules:

- **`basis: "rerun"`** — the run executed, the grounded checks ran against
  ground truth, and the deterministic hash recomputes from the recorded
  trace. A verifier can re-execute the checks and recompute the hash.
- **`basis: "decision-only"`** — the run was denied before execution
  (MoIE BLOCK). Nothing was rerun; the DENY decision vertebra is the
  evidence. The report says so instead of implying a re-execution.
- **`basis: "none"`** — an error before execution (e.g. an empty request).
  Nothing to rerun or infer.
- **`log_inference`** — always present but explicitly labeled. State
  derivation, transition legality, projection consistency, and the decision
  trail are **reconstructed from the event log** by the replay engine
  (`/agent/tasks/{run_id}/replay`). Reconstruction validates the *record*;
  it is not a re-run of the work. The report points at it rather than
  claiming it.

The receipt's one-line `reconstruction` summarizes with the same honesty —
it ends in `verified=<basis>`:

```
request=dbb-… requested=read_vault, write_file allowed=ALLOW happened=PASS
why=MoIE verdict APPROVE; authorization decision ALLOW succeeded=True verified=rerun
```

---

## A worked example (exactly what the demo prints)

Run `python scripts/demo_governed_loop.py` — hermetic, no model or network
needed — and you get both halves of the journey:

**RUN 1 · DANGEROUS — `rm -rf production`**

```
moie verdict  BLOCK
decision      DENY — denied before any model call
execution     none · 0 model calls
verification  decision-only — nothing rerun; the DENY vertebra is the evidence
receipt       request=… requested=nothing (denied at gate) allowed=DENY
              happened=BLOCKED why=MoIE quick-reject BLOCK - denied before any
              model call succeeded=False verified=decision-only
```

**RUN 2 · SAFE — `research the vault and write a client brief`**

```
moie verdict  APPROVE
decision      ALLOW — capabilities [read_vault, write_file]
execution     research → synthesize → write
verification  rerun — search_returned_hits:pass · synthesis_nonempty:pass
              · file_written_with_heading:pass
              deterministic hash recomputed from trace: MATCH
receipt       request=… requested=read_vault, write_file allowed=ALLOW
              happened=PASS why=MoIE verdict APPROVE; authorization decision
              ALLOW succeeded=True verified=rerun
```

The demo exits 0 only when: the dangerous run is BLOCKED with 0 model calls,
the safe run PASSes with 3 grounded checks, both receipts land in the audit
stream, the deterministic hash recomputes, and the (scratch) audit chain
verifies. It writes to temp files only — run with `--persist` to also append
the receipts to the live `logs/audit.jsonl` (they then appear in the cockpit
Evidence Stream).

---

## Seeing it live

- **`/console`** — run a request through `/agent/handle`. The result card
  renders the five stages as a **journey strip** (request → authorization →
  execution → verification → evidence) built from the run payload, and the
  replay card below is the inferred-from-logs half.
- **`/cockpit/audit`** — the Evidence Stream: one receipt per run, filtered
  by verdict / MoIE verdict / intent, each row tagged with its
  `verification.basis` (`rerun` / `decision-only` / `none`).
- **`/agent/tasks/{run_id}/replay`** — event-sourced reconstruction:
  derived state, `consistent`/`legal` flags, timeline, decision trail.
- **`logs/audit.jsonl`** — the raw stream, one JSON receipt per line.

Endpoints that run the journey: `POST /agent/handle` (operator-gated).
Endpoints that inspect it: `GET /agent/tasks/{id}/replay`,
`GET /cockpit/audit`, `GET /agent/tasks`.
