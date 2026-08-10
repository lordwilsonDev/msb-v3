# Conversation Envelope — v1 Spec

**Status:** spec-for-review (governing contract for msb-v3 / moie-os conversation interface)
**Date:** 2026-08-10
**Owners:** lordwilson + Buffy (Freebuff)
**Schema version:** `1.0`

> The system is **not a chatbot with a RAG layer**. It is a conversation interface
> in which every answer is a logged, guardrailed, source-backed claim inside the
> Sovereign Verification Ledger. The E2E test is the specification, the shared
> envelope is the interface, the verified boot sequence is the happy path, and
> the ledger is the permanent log.

---

## 1. Purpose

A single request/response envelope shared by **both modes**:

| Mode | Semantics |
|---|---|
| `chat` | stateless — one query → one answer, no retained workflow state |
| `workflow` | stateful — the query participates in an explicit task graph (dependencies, step tracker, replan) |

The envelope is the only contract between client and server. It surfaces
provenance that the RetrievalRouter **already produces** (per-source score +
route provenance), adds the two guardrail verdicts, and terminates in a ledger
`evidence_ref` so every exchange is auditable.

This document is the spec the E2E test asserts against. An implementation is
"done" when the E2E probe passes, not when the code exists.

---

## 2. Canonical flow (the contract)

```
ask ──► retrieve ──► input_guardrail ──► [compose] ──► output_guardrail ──► answer ──► log
 │         │              │                                  │                │         │
 │         │              └── BLOCK → short-circuit (no retrieval needed)      │         │
 │         │                                                                   │         │
 │         ▼                                                                   ▼         ▼
 │   sources[] with score+provenance+source_ts                    claim_id         §8 evidence
 │                                                                               artifact
 ▼
trace_id issued (or echoed)
```

**Each hop emits evidence.** Nothing is dropped.

| Hop | Emits |
|---|---|
| `ask` | durable `trace_id` |
| `retrieve` | `sources[]` (score, provenance/routes, `source_ts`, computed freshness) |
| `input_guardrail` | `ALLOW` / `BLOCK` + policy + reason |
| `output_guardrail` | `SUPPORTING` / `UNSUPPORTED` / `BLOCKED` + citation rate |
| `answer` | the answer text **as a claim** (`claim_id`) |
| `log` | canonical §8 evidence artifact under the sovereign-verification contract; `evidence_ref` returned in the envelope |

---

## 3. Envelope — request

```json
{
  "schema_version": "1.0",
  "trace_id": "tr_01J2...",          // optional; server generates when absent, always echoes back
  "mode": "chat",                    // "chat" | "workflow"
  "query": "what did the vault say about the Fox Valley retainer?",
  "tenant_id": "default",
  "session_id": "sess_ab12",         // optional; workflow mode may require it
  "workflow": {                      // REQUIRED when mode == "workflow"
    "goal": "...",
    "dag": [ { "skill": "...", "args": {} } ],
    "step_tracker": { "required_steps": ["..."] }   // reuses guardrails/fold.py StepTracker
  },
  "client": { "channel": "cli", "client_id": "dev-mac" },   // rate-limit + audit key
  "sources_hint": []                 // optional file paths (legacy RunRequest.sources passthrough)
}
```

**Request validation (fail fast, 422):**
- `schema_version` must equal the server's supported version (see §10 versioning).
- `mode` must be `chat` or `workflow`.
- `query` must be a non-empty string.
- `workflow` must be present when `mode == "workflow"`.
- Unknown top-level fields are **rejected** (a forward-incompatible client must not be silently half-served).

---

## 4. Envelope — response (success)

```json
{
  "schema_version": "1.0",
  "trace_id": "tr_01J2...",          // ALWAYS present, mirrors request or newly minted
  "mode": "chat",
  "status": "answered",              // "answered" | "blocked" | "error"
  "query": "what did the vault say about the Fox Valley retainer?",

  "input_guardrail": {
    "verdict": "ALLOW",              // "ALLOW" | "BLOCK"
    "policy": "safety-blocklist-v1",
    "reason": null,                  // non-null iff BLOCK
    "checked_at": "2026-08-10T16:00:00Z"
  },

  "sources": [
    {
      "source_id": "note:2026/07/28-fox-valley",
      "score": 0.83,                 // RRF-fused score (RetrievalRouter output, unchanged)
      "source": "Documents/Vault/10_Projects/FoxValley.md",
      "text": "…excerpt…",
      "provenance": [ { "index": "vector", "weight": 0.6, "rank": 1 } ],   // routes[] passthrough
      "source_ts": "2026-07-28T09:00:00Z",   // NEW: source timestamp (from metadata or file mtime)
      "freshness": "FRESH"           // computed: FRESH | AGING | STALE (see §6)
    }
  ],

  "output_guardrail": {
    "verdict": "SUPPORTING",         // "SUPPORTING" | "UNSUPPORTED" | "BLOCKED"
    "citation_rate": 1.0,            // cited_sources / total_sources (0.0–1.0)
    "reason": null
  },

  "answer": {
    "text": "The vault notes the Fox Valley retainer…",
    "claim_id": "claim:ans:9f2c…",   // content-addressed: sha256(canonical(query + sources + text))[:12]
    "citations": [
      { "source_id": "note:2026/07/28-fox-valley" },
      { "memory_ref": "mem_ab12" }                  // memory/session citation — exempt from invariant 5
    ]
  },

  "evidence_ref": "ledger://evidence/conversation/20260810T160000Z_ev_9f2c….json",
  "latency_ms": 184
}
```

**Envelope invariants (asserted by the E2E test):**

1. `trace_id` is never empty in a response.
2. `status == "blocked"` ⇔ `input_guardrail.verdict == "BLOCK"` (short-circuit: no `sources`, no `answer`).
3. `status == "answered"` ⇒ `sources` non-empty **or** `answer.citations` reference memory/session evidence (an unsupported claim may not present itself as answered-with-sources).
4. `output_guardrail.verdict == "SUPPORTING"` ⇒ `citation_rate ≥ threshold` (default 0.5) and `answer.citations` non-empty.
5. Every `source_id` in `answer.citations` exists in `sources[]` (no ghost citations). **Memory-only citations are exempt:** a citation with `memory_ref` set is *not* required to appear in `sources[]` — it references session memory, not a retrieved document.
6. `evidence_ref` is present on every `answered` and `blocked` response.
7. `claim_id` is deterministic — same query+sources+answer ⇒ same claim_id (replay-safe, dedupe-safe).
   **Canonicalization (the "canonical" in invariant 7):** `sources` are sorted by
   `source_id` before hashing, so RRF tie-order jitter can never change a
   claim_id for the same epistemic event. Live-model text is non-deterministic,
   so cross-run stability of the *hash inputs* is guaranteed only in stub mode;
   the §8 `evidence_id` is content-hashed on the artifact itself, which is what
   replay dedupe actually relies on.
8. `schema_version` is echoed unchanged.

---

## 5. Guardrail duality (the two slots)

One contract, two named verdicts — do not collapse them.

| Slot | Guards | Verdicts | Runs | Short-circuits? |
|---|---|---|---|---|
| `input_guardrail` | the **query** | `ALLOW` / `BLOCK` | before retrieval | YES — BLOCK returns immediately (no model spend) |
| `output_guardrail` | the **drafted answer vs its sources** | `SUPPORTING` / `UNSUPPORTED` / `BLOCKED` | after composition, before release | BLOCK → no answer released (evidence still logged) |

**Polarity mapping into the ledger (log hop):**

| Envelope verdict | Ledger polarity (§8) | Claim it attacks |
|---|---|---|
| `output_guardrail == SUPPORTING` | `SUPPORTING` | `claim:ans:<hash>` |
| `output_guardrail == UNSUPPORTED` | `INCONCLUSIVE` | `claim:ans:<hash>` (answer exists, support weak) |
| `output_guardrail == BLOCKED` | `CONTRADICTING` | `claim:ok:query:<hash>` ("this query is safely answerable") |
| `input_guardrail == BLOCK` | `CONTRADICTING` | `claim:ok:query:<hash>` |

> **Design note (decided):** a BLOCK is *rejection evidence against the query's
> answerability claim*, not a contradiction of an answer that was never produced.
> This is why blocked responses get their own claim (`claim:ok:query:<hash>`),
> distinct from the answer claim. The ledger's verdict engine already handles
> CONTRADICTING against never-supported claims → UNVERIFIED-with-evidence (no
> false elevation).

---

## 6. Source freshness (computed at answer time)

`source_ts` comes from, in priority order:
1. `metadata.created_at` / `metadata.ts` if the index adapter provides it
2. file mtime for filesystem-backed sources
3. `null` (recorded as `freshness: "UNKNOWN"` — never guessed)

Freshness bands (configurable; defaults):

| Band | Age | Epistemic meaning |
|---|---|---|
| `FRESH` | ≤ 30 days | supports a claim at full weight |
| `AGING` | 31–90 days | supports, weight discounted |
| `STALE` | > 90 days | does NOT support an `answered` claim alone; must be corroborated by a FRESH source or flagged in the answer |
| `UNKNOWN` | no timestamp available | never guessed — treated as STALE for the worst-source-wins rule |

An answer citing **only** STALE/UNKNOWN sources may not return
`output_guardrail.verdict == "SUPPORTING"` (worst-source-wins, mirroring the
ledger's tier rule: no tier higher than its weakest evidence). **A memory-only
answer (no `sources[]`) may not return SUPPORTING either** — without retrieved
evidence, the best an answer can claim is `UNSUPPORTED`/`INCONCLUSIVE`; the
verdict engine treats it exactly like the replay consumer treats a
never-supported claim: evidence preserved, nothing elevated.

---

## 7. Log hop → §8 evidence artifact

Each conversation produces one canonical artifact (mirroring
`replay_feedback_events.py`'s artifact shape — same contract, new producer):

```json
{
  "evidence_id": "ev_9f2c…",
  "subject_id": "trace:tr_01J2…",
  "claim_id": "claim:ans:9f2c…",
  "evidence_type": "conversation",
  "polarity": "SUPPORTING",
  "git_head": "…",
  "artifact_hash": "…",
  "toolchain": "msb-v3/conversation-envelope",
  "timestamp": "2026-08-10T16:00:00Z",
  "result": "ANSWERED",
  "provenance": {
    "execution":   { "mode": "chat", "guardrail_input": "ALLOW", "guardrail_output": "SUPPORTING", "latency_ms": 184 },
    "environment": { "tenant_id": "default", "session_id": "sess_ab12", "model": "stub|ollama:<model>" },
    "input":       { "query": "…", "dag": null },
    "verifier":    { "tool": "msb_v3/guardrails", "version": "v1" },
    "dependency":  { "sources": [ { "source_id": "…", "score": 0.83, "freshness": "FRESH" } ] }
  },
  "freshness": "FRESH"
}
```

- The artifact lives under the ledger (`<ledger>/evidence/conversation/…`), written by a
  conversation-layer producer adapter (same discipline as the replay consumer:
  idempotent by `evidence_id` content hash, fail-loud on write failure).
- `claim_id` determinism (invariant 7) makes replay dedupe trivial.

---

## 8. Error & control-plane contract

| Case | HTTP | Body shape |
|---|---|---|
| Guardrail BLOCK | 200 | `status: "blocked"` envelope (evidence still logged) |
| Validation failure | 422 | `{ "schema_version", "trace_id", "status": "error", "error": { "code": "validation_failed", "message": "…" } }` |
| Rate limited | 429 | `{ "detail": "rate_limit_exceeded" }` (existing middleware, unchanged) |
| Model/db down | 503 | `status: "error"`, `error.code: "unavailable"`, `retryable: true` |
| Auth failure (live-auth gate) | 401 | existing x-mcp-secret behavior, unchanged |

Error responses always carry `trace_id` when one exists (best-effort otherwise).

---

## 9. CI / stub-model profile

- **Stub mode** (`MSB_CONVERSATION_MODEL=stub`): the model hop is replaced by a
  deterministic stub that emits the **full envelope shape** — fake-but-shaped
  `sources[]`, a guardrail verdict, and an answer — so CI proves the chain and
  the schema, not the model. Zero spend, zero network.
- **Live mode** (`MSB_CONVERSATION_MODEL=ollama`): local model, local only.
- The E2E probe (`ask → retrieve → input_guardrail → output_guardrail → answer →
  log`) runs in CI under stub mode against the health-checked boot (see happy
  path spec) and asserts every invariant in §4. **The probe MUST include at
  least one BLOCK-case query** — the stub returns `input_guardrail: BLOCK` for a
  fixture query — asserting the short-circuit (zero model spend) AND that a §8
  CONTRADICTING artifact is still logged for `claim:ok:query:<hash>`. An E2E
  that only exercises the ALLOW path does not prove the guardrail contract.
- CI auth: the probe authenticates with the seeded x-mcp-secret test credential
  (the live-auth gate stays on; CI never bypasses auth, it supplies it).

## 9a. Synchronous vs the existing async `run_research`

`POST /research/assistant/run` is **async** (background_tasks, returns
`notify.async: true`). The envelope is a **new synchronous endpoint**
(`POST /conversation/ask`) — it does NOT replace `run_research`. The legacy
pipeline keeps its contract; the envelope is the new conversational interface.
Workflow mode may internally invoke the same machinery but must return the full
envelope synchronously (or, for long DAGs, a `status: "deferred"` with a
polling handle — see open decision 6).

---

## 10. Versioning

- `schema_version` is `major.minor`. Server accepts the exact major it supports.
- **Additive changes** (new optional fields) bump `minor`; old clients keep working.
- **Breaking changes** (renames, removed fields, new required fields) bump `major`;
  old `schema_version` is rejected with 422 + `error.code: "schema_mismatch"`.
- The E2E test pins the current version; the factory gate fails if the pin and
  the implementation drift.

---

## 11. Open decisions (resolve before implementation)

1. **Blocked-response claim lifetime**: does `claim:ok:query:<hash>` persist or
   expire? (Recommendation: persist — repeated blocks on the same query should
   accumulate evidence, mirroring the replay consumer.) Note: a query that was
   previously answered (SUPPORTING evidence on the same hash) and later blocks
   REGRESSES per the ledger's rule — historical green + current red. The landing
   state is therefore UNVERIFIED (never supported) **or** REGRESSED (was
   supported), never ambiguous.
2. **citation_rate threshold**: hard 0.5 default vs per-mode (workflow stricter)?
3. **Freshness bands**: 30/90 days OK, or config-driven per index?
4. **`answer.text` length cap** for the log artifact (recommendation: store
   excerpt + full text hash, not unbounded text).
5. **Session memory join**: `session_id` → memory append (`/assistant/memory/append`
   exists) — should answered claims in a session auto-append a memory line?
6. **Long-workflow semantics**: for DAGs that exceed a synchronous budget, is
   `status: "deferred"` + a polling handle in scope for v1, or is v1
   synchronous-only with workflow mode capped to short DAGs? (Recommendation:
   v1 synchronous-only; deferred lands in v1.1.)

---

## 12. Conformance checklist (what "done" means)

- [ ] E2E probe passes under stub mode in CI, asserting all 8 invariants (§4)
- [ ] E2E probe includes a BLOCK-case query: short-circuit + §8 CONTRADICTING artifact logged
- [ ] Envelope implemented as one request handler, both modes
- [ ] `input_guardrail` BLOCK short-circuits with zero model spend
- [ ] `output_guardrail` maps to ledger polarity per §5
- [ ] `source_ts` + `freshness` computed per §6; STALE-only answers can't be SUPPORTING
- [ ] Log hop writes §8 artifact; replay-idempotent by `evidence_id`
- [ ] Stub mode emits full envelope; live mode via ollama
- [ ] `schema_version` pin asserted in the factory gate
