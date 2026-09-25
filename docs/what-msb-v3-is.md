# MSB v3 — what it is, in one page

*Written 2026-09-22 by inspecting the tree. Every claim names what proves it, and
where the tree contradicts itself that is stated instead of resolved. Pointers use
test names and headings, not line numbers, because line numbers rot.*

## In one sentence

MSB v3 is a single-operator, local-first runtime that takes a task, decides whether
it is allowed, runs it inside a fail-closed permission boundary, and leaves a
tamper-evident record of what happened. `pyproject.toml` gives it as `msb-v3`
`0.5.0`; `README.md` calls it "a sovereign, local-first, governed agent runtime".

## The loop

```
request → is this allowed? → run it (bounded) → prove what happened → keep the proof
```

The safety decision is **not** made by a model. It is a deterministic keyword
verdict — `MoIEController().analyze(claim).verdict` — and the gate says of itself
that it is a *pre-filter*, not the boundary (`tests/contracts/test_gate_contract.py`,
module docstring). The boundary is the capability registry behind it
(`src/msb_v3/governance/`).

## What is proven, and by what

| Property | What proves it |
|---|---|
| A **denied** request makes no model call and still leaves a receipt | `scripts/demo_governed_loop.py` drives the real `handle()` path with a canned model and asserts `blocked.model_calls == 0`; `tests/contracts/test_evidence_receipt.py::test_receipt_for_denied_request_is_reconstructable` |
| An **unknown** capability is not assumed safe | `tests/governance/test_capability_resolver.py::test_unknown_requests_stay_unknown`; `tests/governance/test_capability_registry.py::test_unknown_capability_resolves_to_none` |
| The gate's accuracy is **measured, not asserted** | `tests/contracts/test_gate_contract.py::test_gate_precision_recall_pinned` — tp 17 / fp 8 / tn 8 / fn 23 → precision **0.68**, recall **0.425**, over a frozen corpus |
| The misses are **covered by a second layer**, not ignored | `tests/contracts/test_layered_boundary.py` |
| Editing a past record is **detected** | `tests/uac/test_audit_chain.py::test_verify_chain_detects_tampered_payload` |
| Replacing the **whole** history with an older copy is detected | `tests/uac/test_chain_anchor.py::test_t7_whole_db_replacement_is_detected`, `::test_anchor_file_tamper_is_detected` |
| The record can be checked **without trusting the author** | `python -m msb_ledger.chain_anchor --verify <db>` (`src/msb_ledger/chain_anchor.py`) |
| The signing key can be moved **off the machine** | `MSB_CHAIN_ANCHOR_BACKEND` = `software` (default) / `secure-enclave` / `yubikey-piv` — `src/msb_ledger/signing.py` |
| A **research question** is being tested, with falsification written first | `research/PLAN.md`, heading "1. The question": "Authority–Intelligence Separation", arms A/B/C (direct agent / simpler governed / MSB v3 governed), and the rule that changing MSB v3 source to "make C win" stops the run |

## What it is not

- **Not a chatbot, SaaS, or dashboard product** — `README.md`, "What it is".
- **Not multi-user or cloud** — it runs on one machine, as local services the
  manifest lists (`MANIFEST.md`, "External services & binaries"): the app on
  `8766`, Ollama on `11434`, Qdrant on `6333`.
- **Not a proven safety boundary.** The measured gate recall is 0.425 — it misses
  most dangerous input on its own. Whether the layered boundary compensates is
  exactly what the research programme is set up to test, and that result does not
  exist yet.

## What is not built yet, and one thing the tree contradicts

- **17 of 33 declared deliverables exist.** Phases 0–6 landed (2 and 5 only
  partially); phases 7–19 are not started — `PLAN.md`, "Phase status —
  reconciled 2026-09-22", which reports itself as reconciled by inspecting the
  tree rather than from memory.
- **All 15 declared governance metrics are absent from `src/`**, and
  **invariants 003–010 are prose, not tests** (001 and 002 are pinned). Same block,
  same file.
- **The licence is MIT, plus a startup gate.** `LICENSE` grants use and running,
  and says so; `README.md` ("Access & licensing") and `LICENSE`'s closing note
  both state that *this build* verifies an owner-signed key before it starts
  (`src/msb_v3/__main__.py`), so an anonymous clone boots nothing. A guard in the
  build, not an extra term on the grant. Until 2026-09-22 the two files gave
  different answers; this line is the reconciled one.

## Scale, measured 2026-09-25

Python under `src/`: **404 files / 79,682 lines**. Python under `tests/`: **357
files / 62,840 lines**. Markdown under `docs/`: **152 files / 34,649 lines**.
`pytest --collect-only` collects **4,018 tests** (76 deselected by configuration).
Test source is 79% the size of product source.

## Check it in two commands

```bash
python scripts/demo_governed_loop.py     # no model, no network, no vault needed
python -m msb_ledger.chain_anchor --verify data/uac/audit_chain.db
```
