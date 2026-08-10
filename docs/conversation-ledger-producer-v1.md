# Conversation → Ledger Evidence Producer — v1 Spec

**Status:** spec-for-review (log-hop contract; terminates the conversation envelope)
**Date:** 2026-08-10
**Depends on:** [`conversation-envelope-v1.md`](./conversation-envelope-v1.md) (the envelope it consumes)
**Schema version:** `1.0`

> The ledger's rule, applied to conversation: **an answer is a claim, and a
> claim is only as strong as the evidence beneath it.** The log hop turns every
> exchange into a canonical §8 evidence artifact with explicit polarity — so a
> conversation can never be retroactively greenwashed by a nice answer, and a
> block can never silently vanish.

---

## 1. Purpose

The producer adapter that converts conversation exchanges into ledger evidence.
It is the **log hop** of the envelope: given a completed (or blocked) exchange,
it emits a canonical §8 evidence artifact and updates the claim registry —
idempotently, content-addressed, fail-loud. It is the conversation-layer
counterpart of `replay_feedback_events.py` (the task-failure producer): same
contract, same discipline, new evidence type.

The producer is **not** part of the model path. It is a deterministic, zero-spend,
stdlib-only consumer of conversation records. Nothing in it calls a model.

---

## 2. What it consumes (input)

One **conversation record** per exchange — the log hop's payload, derived from
the envelope response (all fields already validated by the envelope):

```json
{
  "record_version": "1.0",
  "trace_id": "tr_01J2…",
  "mode": "chat",
  "query": "what did the vault say about the Fox Valley retainer?",
  "status": "answered",                      // "answered" | "blocked"
  "input_guardrail": { "verdict": "ALLOW" },
  "output_guardrail": { "verdict": "SUPPORTING", "citation_rate": 1.0 },
  "sources": [ { "source_id": "…", "score": 0.83, "source_ts": "…", "freshness": "FRESH" } ],
  "answer": { "text": "…full answer text…", "text_excerpt": "…", "claim_id": "claim:ans:9f2c…", "citations": [ … ] },
  "latency_ms": 184,                            // from the envelope, never produce-time
  "git_head": "…",                             // repo identity at log time
  "recorded_at": "2026-08-10T16:00:00Z"
}
```

The producer must be able to rebuild the **exact artifact** from this record
alone (no envelope access, no model). **The record is the only source of
truth: every artifact field derives from the record — `timestamp` =
`record.recorded_at`, `latency_ms` from the record — and NOTHING is computed
from produce-time state.** Without this pin, `evidence_id = sha256(artifact
bytes)` would differ across runs of the same record and content-addressed
idempotency would silently break.

`answer.text` is the **full answer text** — the hash base for `claim_id` (and
hence for the §4 self-consistency recompute). `text_excerpt` is only a display
cap for the artifact's input provenance; identity always uses full text.

---

## 3. The two claim types

The producer registers **two** claim families. They answer different questions
and must not be conflated. The hash bases are **pinned identically to envelope
invariant 7** — one canonical definition across both specs:

| Claim | Formula | Question it answers | Truthfulness semantics |
|---|---|---|---|
| **`claim:ans:<hash>`** | `sha256(canonical_json({query, source_ids: sorted, answer_text}))[:12]` | "Is this answer, as stated, supported by these sources?" | Epistemic — an answer claim is **supported / unsupported / inconclusive** |
| **`claim:ok:query:<hash>`** | `sha256(canonical_json({query}))[:12]` | "Is this query safely and confidently answerable?" | Availability — a blocked or degraded query is **not answerable** |

`source_ids` are sorted lexicographically before hashing (RRF tie-order
jitter-proof). The hash base is the **`source_id` list only** — never the full
source objects (score/provenance/source_ts are evidence, not identity).

- `claim:ans` is **content-addressed on the answer itself**: same query + same
  sources + same answer ⇒ same claim (replay-safe, dedupe-safe). Sources are
  sorted by `source_id` before hashing so RRF tie-order jitter can never change
  the claim id (envelope invariant 7's canonicalization, applied here).
- `claim:ok:query` is content-addressed on the **query alone** — it is the claim
  a BLOCK attacks (per envelope §5: a block is rejection evidence against the
  query's answerability, not a contradiction of an answer that was never
  produced).

Both are registered with `claim_type` so the ledger can distinguish epistemic
from availability claims — the `claims.json` registry already carries
`claim_type` (status_report derives `constellation_node_verified` today; this
adds `answer_supported` and `query_answerable`).

---

## 4. Polarity assignment (the mapping, made operational)

| Envelope outcome | Polarity | Target claim | Evidence type |
|---|---|---|---|
| `answered` + `output_guardrail == SUPPORTING` | `SUPPORTING` | `claim:ans:<hash>` | `conversation` |
| `answered` + `output_guardrail == UNSUPPORTED` | `INCONCLUSIVE` | `claim:ans:<hash>` | `conversation` |
| `answered` + `output_guardrail == BLOCKED` | `CONTRADICTING` | `claim:ok:query:<hash>` | `conversation_block` |
| `blocked` (input guardrail) | `CONTRADICTING` | `claim:ok:query:<hash>` | `conversation_block` |

A `SUPPORTING` artifact is only ever attached to the claim whose content hash it
carries — the producer recomputes `claim_id = sha256(canonical_json({query,
source_ids: sorted, answer_text}))[:12]` from the **full answer text in the
record** and verifies it equals the envelope-supplied `claim_id` before writing
(self-consistency check; mismatch ⇒ fail-loud, never a mismatched attachment).

---

## 5. The §8 evidence artifact (canonical shape)

Mirrors the task-failure producer's `build_evidence_artifact` **exactly** in
shape — same top-level keys, same five provenance layers — so the ledger has one
evidence schema, not per-producer shapes:

```json
{
  "evidence_id": "ev_9f2c…",            // sha256(canonical artifact bytes)[:12], content-addressed
  "subject_id": "trace:tr_01J2…",
  "claim_id": "claim:ans:9f2c…",
  "evidence_type": "conversation",       // or "conversation_block"
  "polarity": "SUPPORTING",              // SUPPORTING | INCONCLUSIVE | CONTRADICTING
  "git_head": "…",
  "artifact_hash": "9f2c…",              // [:16] of the canonical record hash
  "toolchain": "msb-v3/conversation-ledger",
  "timestamp": "2026-08-10T16:00:00Z",
  "result": "ANSWERED",                  // ANSWERED | BLOCKED
  "provenance": {
    "execution":   { "mode": "chat", "input_guardrail": "ALLOW", "output_guardrail": "SUPPORTING", "citation_rate": 1.0, "latency_ms": 184 },
    "environment": { "tenant_id": "default", "session_id": "sess_ab12", "model": "stub|ollama:<model>", "git_head": "…" },
    "input":       { "query": "…", "query_hash": "…", "source_ids": ["…"], "dag": null },
    "verifier":    { "tool": "msb-v3/guardrails", "version": "v1", "output_guardrail": "SUPPORTING" },
    "dependency":  { "sources": [ { "source_id": "…", "score": 0.83, "source_ts": "…", "freshness": "FRESH" } ] }
  },
  "freshness": "FRESH"
}
```

**Layout on disk** (mirrors the replay consumer's layout):

```
<ledger>/
  evidence/
    conversation/          # SUPPORTING / INCONCLUSIVE artifacts
      <ts>_ev_<hash>.json
    conversation_block/    # CONTRADICTING artifacts (blocked exchanges)
      <ts>_ev_<hash>.json
  claims.json              # registry: claim:ans:* and claim:ok:query:* entries
  replay_cursor.json       # processed record identities (content hashes)
```

`evidence_ref` in the envelope is the **deterministic path**: because
`evidence_id` is content-addressed and the timestamp is in the record, the
producer can compute the artifact path *before* writing it — so the envelope can
return a `ledger://evidence/conversation/<ts>_ev_<hash>.json` ref that is
guaranteed to exist once the producer runs (or fail loudly if it can't).

---

## 6. Idempotency (replay-safe, dedupe-safe)

The producer is safe to run twice, daily, or after every exchange:

- **Identity:** `evidence_id = sha256(canonical artifact bytes)[:12]`. Replaying
  the same record reproduces the same id and the same file path → write skipped.
- **Cursor:** `replay_cursor.json` records every processed record identity
  (`record_id = sha256(canonical record bytes)`). Already-processed records are
  skipped, never re-written.
- **Same-second distinct exchanges never conflate:** identity is content-based,
  not `(ts, trace_id)` — two different exchanges in the same second produce two
  different hashes and two artifacts (the exact fix the task-failure producer
  applied; the conversation producer inherits it from day one).
- **Deterministic claim update:** claim verdicts are **derived** from the
  artifact set by the governor (open decision 2, now decided — see §8); the
  producer never hand-writes a verdict, and re-running never double-counts.

---

## 7. Verdict transitions (the ledger's rules, applied to conversation)

| Prior claim state | Fresh evidence | Resulting verdict |
|---|---|---|
| no prior `claim:ans` | `SUPPORTING` | `VERIFIED` (supported by evidence) |
| no prior `claim:ans` | `INCONCLUSIVE` | `UNVERIFIED` (evidence preserved, nothing elevated) |
| no prior `claim:ok:query` | `CONTRADICTING` (block) | `UNVERIFIED` (never supported — a block is not a falsification of nothing) |
| `claim:ans` previously `VERIFIED` | `CONTRADICTING` (later unsupported/blocked) | `REGRESSED` (historical green + current red) |
| `claim:ans` `VERIFIED` + new `SUPPORTING` | `SUPPORTING` | stays `VERIFIED` |
| `claim:ok:query` previously supported | `CONTRADICTING` (block) | `REGRESSED` (was answerable, now not) |
| any claim | `CONTRADICTING` while prior `CONTESTED` | stays `CONTESTED` (contradiction preserved, accumulation continues) |

**Prohibited transitions (the ledger fails closed):**
- No evidence ⇒ never `VERIFIED` (a nice answer with no sources cannot elevate —
  enforced by the envelope's worst-source-wins rule at the *answer* layer, and
  by "no artifact ⇒ no verdict" at the *ledger* layer).
- `INCONCLUSIVE` evidence can never flip a claim to `VERIFIED`.
- A `CONTRADICTING` artifact against `claim:ok:query` can never be re-labeled
  `SUPPORTING` for `claim:ans` (polarity is bound at write time and verified at
  read time by the self-consistency check in §4).

---

## 8. Producer contract (the interface an implementation must satisfy)

```
produce(record: ConversationRecord, ledger_dir: Path, git_head: str) -> ArtifactRef
```

- `record` — one §2 conversation record.
- Returns the deterministic artifact ref; raises (fail-loud) on:
  - malformed record (missing required fields, wrong schema version)
  - self-consistency failure (§4 recompute mismatch)
  - ledger write failure (disk, permission) — **never a silent no-op**
- Zero spend: no network, no model, stdlib only.
- Deterministic: same record + same ledger ⇒ same artifact, same claims state.
- A **dry-run** mode (`produce(..., dry_run=True)`) computes the ref and the
  resulting claims state without writing — used by tests and the offline replay
  eval.

**CLI surface** (mirrors `replay_feedback_events.py`):

```
produce_conversation_evidence.py --records <stream.jsonl> --ledger-dir DIR
    [--git-head SHA] [--dry-run] [--self-test]
```

---

## 9. Offline replay eval (the d04 doctrine, for conversations)

Because the producer is deterministic and the records are durable, quality
evaluation is a replay, not a live experiment:

- **Replay** recorded exchanges through the producer in `--dry-run` (or against
  a scratch ledger) → measure, **with zero model spend**:
  - **guardrail pass-rate**: answered-exchanges where `output_guardrail ==
    SUPPORTING` / total answered
  - **source-citation rate**: citations present / answers that claim support
  - **block rate**: blocked exchanges / total
  - **claim verdict distribution**: how many `claim:ans` reached VERIFIED vs
    UNVERIFIED vs REGRESSED
- Same recorded stream replayed after a guardrail or freshness-threshold change
  shows the *delta* — the standing regression guard for conversation quality.

---

## 10. CI / stub profile

- The producer is zero-spend by construction — it runs in CI **live**, not just
  stubbed: the stub-model E2E produces real records, the producer ingests them
  into a scratch ledger, and the CI assertion is "E2E wrote N artifacts; all
  invariants hold; replay ingests 0 new" (the replay-consumer wiring pattern).
- The `--self-test` exercises the full path in memory: produce → verify artifact
  shape + polarity → idempotent replay → prohibited-transition rejection.

---

## 11. Conformance checklist (what "done" means)

- [ ] `produce()` writes the exact §8 shape (§5), five provenance layers present
- [ ] Both claim types registered with correct `claim_type` (`answer_supported`,
      `query_answerable`)
- [ ] Polarity mapping (§4) is the only path to an artifact — no polarity other
      than the table's four outcomes is writable
- [ ] Idempotent: replay ingests 0 new; same-second distinct exchanges produce
      distinct artifacts
- [ ] Prohibited transitions rejected (self-test asserts them)
- [ ] Offline replay eval reports guardrail pass-rate, citation rate, block rate,
      verdict distribution — zero spend
- [ ] Envelope `evidence_ref` resolves to a real artifact after `produce()` runs

---

## 12. Open decisions (resolve before implementation)

1. **Ledger location**: producer writes to the shared ledger
   (`~/.hermes/skills/sovereign-verification/ledger`, the constellation default)
   or a per-project ledger under msb-v3? (Recommendation: shared ledger — one
   claim registry, one replay story; the task-failure producer already writes
   there.)
2. **Registry writes** — **DECIDED: artifacts-only + derived claims.** The
   producer emits §8 artifacts and nothing else; claim verdicts are derived from
   the artifact set by the governor (the `status_report.py` philosophy: nothing
   is asserted, a claim can never outlive its evidence). This also makes §7 the
   shared truth table for the governor, not producer behavior.
3. **Answer text retention**: store `text_excerpt` (capped) + full-text hash in
   the artifact, or the full text? (Recommendation: excerpt + hash — the ledger
   holds evidence, not content; full text stays in the conversation store.)
4. **Block retention window**: do `claim:ok:query` entries and their
   `conversation_block` artifacts ever expire, or persist forever (blocks on the
   same query accumulate — the replay-consumer precedent says persist)?
