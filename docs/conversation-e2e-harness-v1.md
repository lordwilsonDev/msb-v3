# Conversation E2E Harness — v1 Spec

**Status:** spec-for-review (the primary contract — the E2E probe is the
definition of "done" for the conversation interface)
**Date:** 2026-08-10
**Depends on:** [`conversation-envelope-v1.md`](./conversation-envelope-v1.md) (the
interface it drives) + [`conversation-ledger-producer-v1.md`](./conversation-ledger-producer-v1.md)
(the log hop it verifies)
**Schema version:** `1.0`

> The E2E test is not the final verification step; it **is the specification**.
> The envelope, the happy path, and the ledger integration are implementations
> measured against this probe. An exchange is "done" when this probe passes —
> under stub mode in CI, live mode locally.

---

## 1. Purpose

A deterministic, zero-spend end-to-end probe for the full conversation flow:

```
ask → retrieve → input_guardrail → [compose] → output_guardrail → answer → log
```

The probe runs against a **health-checked boot** of the real stack (model +
Qdrant + dashboard + `/conversation/ask`), with the model hop replaced by a
**stub** in CI. It asserts every envelope invariant (§4 of the envelope spec)
and every producer invariant (§11 of the producer spec), then replays the
recorded exchanges offline to measure quality — no model spend anywhere.

The harness reuses two proven patterns from this constellation:
- the **stub-binary pattern** (skill-orchestration-os smoke suite: a fake
  `claude` binary that emits shaped JSON — the probe's stub model is the same
  idea at the model boundary);
- the **recorded-response replay pattern** (domain-router d04: recorded
  responses replayed for standing regression, `--live` re-records against the
  real provider).

---

## 2. Stub model (the CI model hop)

`MSB_CONVERSATION_MODEL=stub` selects the stub. It is a **deterministic
function of the request**, not a canned string:

```
stub(query, sources, input_guardrail_verdict) -> answer_text, output_guardrail_verdict
```

| Probe path (envelope outcome) | Trigger | Purpose |
|---|---|---|
| **ALLOW/SUPPORTING** | any query not matching a fixture | exercises retrieve → compose → output_guardrail → log |
| **ALLOW/UNSUPPORTED** | fixture query (e.g. `stub://unsupported`) or no sources returned | exercises the INCONCLUSIVE polarity → UNVERIFIED claim |
| **STALE-only** | fixture query (e.g. `stub://stale-only`) with `source_ts` beyond the freshness policy | exercises worst-source-wins: answer must NOT be SUPPORTING |
| **BLOCK** | fixture query (e.g. `stub://blocked`) | **stub never invoked** — exercises input_guardrail short-circuit → `claim:ok:query` CONTRADICTING artifact |

> The BLOCK row is a probe path, **not** a stub behavior: on BLOCK the stub is
> never called (that is the point). The stub's actual domain is only the three
> ALLOW paths.

The stub emits the **full envelope shape** — `sources[]` (fake-but-shaped:
`source_id`, `score`, `source_ts`, `freshness`), both guardrail verdicts, and an
answer whose `claim_id` recomputes correctly from the pinned hash base
(`sha256(canonical_json({query, source_ids: sorted, answer_text}))[:12]`). A
stub that returns a shape the real envelope can't produce is a harness bug, not
a pass.

**Determinism:** same request ⇒ same stub response, byte-for-byte. The stub is
seeded from the request hash; no wall-clock, no randomness.

---

## 3. BLOCK-case fixture (the guardrail must actually fire)

The probe MUST include at least one `stub://blocked` query, asserting:

1. `input_guardrail.verdict == "BLOCK"` and `status == "blocked"` (short-circuit)
2. **zero model spend** — the stub was never called; a BLOCK must not reach the
   compose hop. The probe reads the stub-invocation counter through the **test
   hook**: the app exposes `GET /conversation/test-hook` (active only when
   `MSB_CONVERSATION_MODEL=stub`) returning `{"stub_invocations": N}`, and the
   probe asserts N is unchanged by the BLOCK request. Without the hook, a
   black-box HTTP probe cannot prove the negative.
3. `sources` and `answer` absent from the envelope
4. a §8 `conversation_block` artifact (polarity `CONTRADICTING`) was logged
   against `claim:ok:query:<hash>`
5. `evidence_ref` resolves to that artifact
6. **the claim's landing verdict** — the probe queries the ledger and asserts
   `claim:ok:query:<hash>` is `UNVERIFIED`-with-evidence (no prior support), or
   `REGRESSED` when a prior SUPPORTING artifact exists for the same query —
   matching the producer spec's verdict table. A block that leaves no verdict
   trace is a FAIL.

An E2E that only exercises the ALLOW path proves plumbing, not the guardrail
contract — it is a FAIL, not a partial pass.

---

## 4. Probe sequence (the happy-path boot it runs against)

The probe runs against a **verified boot** — the same sequence the happy path
spec defines:

```
start model (stub|ollama) → poll /health → start Qdrant → verify index
→ start dashboard → verify → run /research/assistant/preflight (fail-fast)
→ run E2E probe → declare ready
```

- Every step must return green evidence before the next starts; a non-green
  step fails the boot with the failing check named (no "partially up" states).
- The probe authenticates with the **seeded `x-mcp-secret` test credential**
  (the live-auth gate stays ON — CI supplies auth, never bypasses it). The
  secret is provided via `MCP_BRIDGE_SECRET` env, matching the existing gate
  (`mcp_bridge.py`: header `x-mcp-secret` vs `os.getenv("MCP_BRIDGE_SECRET")`).

---

## 5. Probe assertions (the done-definition, by spec section)

### Envelope invariants (envelope spec §4) — asserted on every response:
1. `trace_id` non-empty
2. `status == "blocked"` ⇔ `input_guardrail.verdict == "BLOCK"`
3. `answered` ⇒ sources non-empty **or** memory-ref citations
4. `SUPPORTING` ⇒ `citation_rate ≥ 0.5` and citations non-empty
5. no ghost citations (`source_id` exists in `sources[]`, or `memory_ref` set)
6. `evidence_ref` present on answered and blocked
7. `claim_id` deterministic — two identical requests ⇒ identical claim_id
8. `schema_version` echoed unchanged

### Producer invariants (producer spec §11) — asserted on the ledger:
- artifact written in the exact §8 shape (five provenance layers present)
- correct polarity per the mapping table
- **idempotency:** replaying the probe's recorded exchanges ingests 0 new
  artifacts; same-second distinct exchanges produce distinct artifacts
- **no prohibited transition** (e.g. INCONCLUSIVE can never flip a claim to
  VERIFIED) — the producer self-test asserts this independently

### Guardrail contract (this spec §3):
- at least one BLOCK path with zero model spend + `claim:ok:query` artifact

---

## 6. Offline replay eval (the standing quality gate)

After the probe, the recorded exchanges (the same records the producer
consumed) are replayed through the **deterministic producer in `--dry-run`**
against a scratch ledger — zero spend, zero network:

| Metric | Definition | Gate (v1) |
|---|---|---|
| **guardrail pass-rate** | `SUPPORTING` answers / answered exchanges | **report-only** (open decision 3) |
| **source-citation rate** | citations present / answers claiming support | **report-only** (open decision 3) |
| **block rate** | blocked / total | > 0 (the fixture must have fired — this is a wiring check, safe to gate now) |
| **claim verdict distribution** | VERIFIED / UNVERIFIED / REGRESSED across `claim:ans` | reported, not gated (learns over time) |

> **What these metrics verify under stub mode is pipeline wiring, not answer
> quality.** The stub controls its own guardrail verdicts and citations, so
> pass-rate and citation-rate are guaranteed-green by construction. They become
> meaningful *quality* signals only once real (live or recorded-human) exchanges
> are replayed — which is why open decision 3 recommends v1 runs report-only
> and gates on the measured baseline + margin.

The replay is a **standing regression guard**: the same recorded stream replayed
after any guardrail, freshness-threshold, or producer change must not regress
the gated metrics. (The d04 doctrine: recorded responses → replay → gate, with
`--live` only for re-recording.)

---

## 7. CLI surface

```
probe_conversation_e2e.py [--mode stub|live] [--base-url http://127.0.0.1:PORT]
    [--secret-env MCP_BRIDGE_SECRET] [--ledger-dir DIR] [--replay] [--self-test]
```

- `--mode stub` (default in CI) / `--mode live` (local only, requires ollama)
- `--replay` runs the offline replay eval (§6) after the probe
- `--self-test` verifies the harness itself: stub determinism, block-fixture
  wiring, invariant assertion completeness (a broken harness must fail, not
  silently pass)
- exit codes: 0 = all assertions + gated metrics pass; 1 = any assertion or
  gate fails; 2 = harness misuse (bad args)

---

## 8. CI wiring

The probe is a CI job that:

1. checks out msb-v3
2. boots the stack under `MSB_CONVERSATION_MODEL=stub` with a seeded
   `MCP_BRIDGE_SECRET`
3. runs `probe_conversation_e2e.py --mode stub --replay`
4. fails the job on non-zero exit

The probe is zero-spend by construction (stub model + deterministic producer +
recorded replay) — it runs on **every push**, not just nightly, and needs no
API keys. The factory gate's coverage/mutation legs already guard the
implementation; this job guards the **contract**.

---

## 9. Conformance checklist (what "done" means)

- [ ] Stub model: deterministic, full-envelope, seeded from request hash
- [ ] BLOCK fixture fires: short-circuit + zero model spend + `claim:ok:query`
      CONTRADICTING artifact + resolving `evidence_ref`
- [ ] All 8 envelope invariants asserted on every response
- [ ] All producer invariants asserted on the ledger (incl. idempotent replay)
- [ ] Offline replay eval reports all four metrics; gated ones pass
- [ ] Boot sequence is verified-boot (health-checked, preflight fail-fast)
- [ ] Auth: probe sends `x-mcp-secret`; CI seeds `MCP_BRIDGE_SECRET` (never bypasses)
- [ ] Probe runs in CI on every push, zero spend, no keys

---

## 10. Open decisions (resolve before implementation)

1. **Port**: the existing API listens on `:8766`; the probe needs the env port
   (recommendation: read `PORT`/`MSB_BASE`, never hardcode).
2. **Stub fixtures location**: a committed `tests/fixtures/conversation/`
   directory with the fixture queries + expected artifact shapes (recommendation:
   commit them — the probe must be reproducible from a fresh checkout).
3. **Replay gate thresholds**: 90% guardrail pass-rate / 100% citation rate —
   sane starting defaults, or should v1 be report-only for one release so the
   baseline is real before gating? (Recommendation: report-only for the first
   release, then gate on the measured baseline + margin. **Cross-ref: §6 shows
   these as gates; under this decision they are report-only in v1 — the
   implementer must not gate on a stub-guaranteed baseline.**)
4. **Live-mode in CI**: never (recommendation — CI is stub-only; live is
   local-only, matching the d04 `--live` discipline of explicit, capped,
   human-initiated re-recording).
