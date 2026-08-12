# Phase 0 — Substrate Hardening (delta spec)

**Status:** active · **Spec:** `Sovereign-Agentic-Runtime-Build-Spec-v1.md` §6 Phase 0
**Written:** 2026-08-12 (after the DBB slice shipped — this is the *delta*, not the full list)

## Context: what the spec asked vs. what already exists

The canonical spec's Phase 0 listed: *event-log table + hash chain · Task/Trace
tables · wire `queries_total` + `latency_seconds` · fix `/think` leak · structured
logging · config audit.* The spec was authored before the vertical slice and the
observability commit landed, so three of the six items already exist on `main`
and this spec deliberately does **not** rebuild them:

| Item | State on main | Where |
|---|---|---|
| Event-log table + hash chain | ✅ DONE | `src/msb_v3/uac/audit_chain.py` (BEGIN IMMEDIATE, verify/quarantine/repair); `agent/trace.py` writes 4 evidence events per run |
| `queries_total` + `latency_seconds` | ✅ DONE | `harnesses/base.py` (`Metrics.inc`/`Metrics.latency` on success + fallback paths); `agent/intent.py` |
| Structured logging | ✅ DONE | loguru throughout |
| Config audit | ✅ DONE | `guard_config()` single-builder across `/system/config`, cockpit, both CLIs |
| **Task/Trace tables** | ❌ DELTA | Tasks are in-memory dataclasses; traces are chain events but not queryable tables |
| **`/think` leak** | ❌ DELTA | qwen3 model template appends `/think` to the last user message when thinking-mode is on; client never pins the flag |

## Scope of THIS phase (the delta)

### D1 — Task/Trace SQLite tables (queryable persistence)

- New `src/msb_v3/runtime/store.py`: `RuntimeStore` (SQLite) with `tasks` and
  `traces` tables, alongside the existing `data/` state store.
- `traces` table: `run_id, request, intent, graph_source, tasks, execution,
  verdict, outcome, created_ts, deterministic_hash` (mirrors `AgentTrace.as_dict()`).
- `tasks` table: `run_id, task_id, parent_id, goal, capabilities, tools,
  permissions, verification_method, timeout_s, retry_policy, status, output,
  verification, error, latency_s` (mirrors the `Task` dataclass + `TaskResult`).
- Wiring: `record_trace()` additionally persists the trace row; the executor
  persists per-task rows (through a thin optional hook so pure unit tests can
  run with a null store).
- Queries: `get_trace(run_id)`, `list_traces(limit)`, `get_tasks(run_id)`,
  `latest_deterministic_hash(run_id)` — replay support without re-walking the chain.
- **Acceptance (A1):** after a `handle_this()` run with a real store, the trace
  is queryable by `run_id`, `deterministic_hash` matches the in-memory trace,
  and every task row is present with its verification receipt.

### D2 — `/think` leak fix

- Root cause: qwen3's Ollama template (`qwen3:8b`) conditionally appends
  `" /think"` or `" /no_think"` to the last user message when think-mode is set,
  and emits `<think>…</think>` blocks otherwise. `LocalAIClient.generate()` /
  `.chat()` never send a `think` option, so the server default leaks the
  control token into the visible prompt.
- Fix in `src/msb_v3/local_ai/ollama.py`:
  1. Explicitly set `"think": False` in both `/api/generate` and `/api/chat`
     payloads (Ollama ≥ 0.5.4 supports the top-level `think` field for
     thinking-enabled models).
  2. Defensively strip `<think>…</think>` blocks (incl. the empty `<think></think>`
     the template emits) from returned text in a small `_strip_think()` helper,
     applied to `generate()`, `chat()`, and `execute_tool_loop()` outputs.
- **Acceptance (A2):** a fake httpx response containing
  `<think>reasoning</think>the answer` returns `the answer`; payload assertions
  show `"think": False` sent on both endpoints; a plain answer is untouched.
  Pin: the exact chaos case — `"Say A"` never yields `"/think"` in the prompt.

### D3 — Counter pin regression tests

- Extend `tests/test_harness.py` (or a new `tests/test_observability.py`) using
  the `REGISTRY.get_sample_value` pattern from `tests/agent/test_intent.py`:
  - a successful `ChatHarness.execute()` increments `queries_total{harness="chat"}`
    and adds ≥1 sample to `latency_seconds{harness="chat"}`;
  - a fallback path increments the `chat:fallback` event and also records latency;
  - the intent extractor's `intent:llm`/`intent:fallback` counters are pinned
    (already asserted in `test_intent.py` — keep, don't duplicate).
- **Acceptance (A3):** the counters the chaos audit found dead are demonstrably
  alive under test — no regression can silently re-kill them.

## Invariants honored (from the canonical spec §5)

- I1 (no action without event before+after) — satisfied via trace events; D1 adds
  persistence, not new gating.
- I4 (every mutation append-only hash-chained) — the event log already satisfies
  this; Task/Trace tables are **derived projections** (a query convenience), not
  an alternative source of truth. The chain remains the authority.
- I7 (fail closed) — `RuntimeStore` failures must not break the run: the store is
  best-effort persistence; a store error logs and degrades to chain-only tracing.

## Out of scope for this phase

- The other dead counters (triumvirate: guardian/argus/hippocampus) — those are
  Layer 5/6 wiring, tracked by Phase 4 (Safety) in the canonical spec.
- Substring `search_query` → vector replacement — Phase 2.
- Dockerfile, secrets — operator/provisioning, not code.

## Gate for this phase

- Unit suite green: `python -m pytest tests/ -q`
- Ruff clean: `ruff check` (E9/F/I)
- Portability: `bash scripts/portability-check.sh` PASS
- Acceptance A1–A3 all asserted by name in the suite
