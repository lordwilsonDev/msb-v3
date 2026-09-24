# Audit Remediation — Four Findings from an External Read

Date: 2026-09-23
Status: PARTIAL — **Finding 1 implemented, and the backlog it left behind is now
enforced by a ledger and a gate**; **Finding 2 implemented** (stale provider
references retired); **the tier baseline recorded** under Finding 3. Finding 4
remains open — it is a decision for the owner, not code.
Author: Buffy (Freebuff coding agent) — an outside read of the tree, not a self-assessment

> **Provenance, stated first because it changes how to read this.** This document
> is the product of an external audit run against this checkout on 2026-09-23. It
> did not come from the project's own roadmap, and it does not inherit the
> roadmap's numbering. Every number below was measured in this tree on that date,
> and each measurement carries the command that produced it, so any claim here
> can be re-run rather than believed.
>
> Two of the four findings were framed as "fixes" in the audit conversation.
> They are not, and this plan says so rather than inventing code for them:
> **the tier run is verification** (nothing to change until something is
> measured) and **the licence gate is a policy decision that belongs to the
> owner**, not to an auditor or an agent. Presenting either as a code task would
> have been the more convenient lie.

---

## 1. What the audit actually did

The audit's rule was *verify by running, never by reading*, on the grounds that a
README, a green dashboard, and a subsystem's name are all evidence of intent
rather than of behaviour. Concretely it ran the suite, the type checker, the
linter, the project's own aggregate `make lint`, an independent coverage
measurement, the DeepSeek reference census, and a collection count of the
deselected test tiers.

It did **not** read only documentation, and it did not form any conclusion from a
metric before reproducing it.

## 2. Baseline — what was measured, and what held

| Check | Command | Result |
|---|---|---|
| Test suite | `pytest -q tests/` | **3599 passed, 17 skipped, 75 deselected** in 348s |
| Coverage (independent) | `pytest --cov=msb_v3 --cov-fail-under=82` | **84.42%**, floor reached |
| Type checking | `mypy src` | clean, **379 source files** |
| Lint | `ruff check src/ tests/` | clean |
| Aggregate gate | `make lint` | PASS — 16 claims, 27 evidence paths, 18 citations, 9 declared counts |
| Gate accuracy, self-published | `make lint` → `ci-policy-gate.sh` | precision **0.68**, recall **0.425**, f1 0.5231, baseline MATCH |
| Secrets hygiene | `git check-ignore -v .env` | `.env` ignored; `.env.example` carries comments only |
| Release discipline | `git tag` | 14 tags, latest `v0.5.0`, CHANGELOG maintained |
| Cadence | `git rev-list --count HEAD` | 743 commits over 49 active days since 2026-07-24 |

Three of these deserve to be recorded as findings in their own right, because
they are the opposite of the usual failure mode:

1. **The measured coverage matched the published claim.** Commit `a72f4c4` says
   "measured 84% on the non-live suite"; an independent run measured 84.42%
   against a gate of 82. The claim was accurate.
2. **The gate publishes its own weakness.** Recall 0.425 means the keyword
   pre-filter misses most dangerous input on its own. `README.md` already scopes
   it as "not the security boundary", and `docs/what-msb-v3-is.md` states the
   0.425 figure plainly. The document and the number agree.
3. **`src/msb_v3/wrongness/` exists to falsify the author's own architecture
   claims**, and states an anti-theater rule in its module docstring: no LLM, no
   Qdrant, no new dependencies "unless they beat this deterministic baseline".

The four findings below are therefore not the shape of a repo that is hollow. They
are the shape of a repo that shipped faster than it converged.

---

## 3. Finding 1 — failed probes are silently swallowed

**Severity: highest of the four.** This is the one finding that is both a live
defect and a defect *in the layer whose job is honesty*.

### The measurement

An AST walk of `src/` (not a text search, which cannot tell a guarded handler
from a broken one) counts every `except` handler whose entire body is `pass`:

```
TOTAL pure-pass except blocks: 47
  bare (no type):    0
  except Exception: 20
  narrow typed:     27
```

The 27 narrow-typed handlers (`KeyError`, `CancelledError`, `TaskLifecycleError`,
`OSError`, `json.JSONDecodeError`) are largely legitimate. **The 20 that catch
`Exception` and discard it are the finding.** There are no bare `except:`
handlers — `ruff --select E722` is clean, which was worth checking rather than
assuming.

### The structural finding — the guard already exists and is inert

`pyproject.toml:49` reads:

```toml
select = ["E4", "E7", "E9", "F", "I"]
```

`src/` contains **139 `# noqa: BLE001` comments**, of which 130 sit on a line the
rule actually flags. `BLE001` is flake8-blind-except, the rule that flags
`except Exception:` — and it is **not in the selected set**, so it has never been
evaluated. Those annotations are decorative: a deliberate, documented swallowing
decision that no tool reads.

> **Corrected 2026-09-23, by measurement.** This paragraph first claimed the rule
> flagged 166 sites, of which "roughly 27" were unannotated. That was inverted.
> Measured with `--ignore-noqa`, the pool is **296** blind catches; **166** of
> them carry no reason and therefore fail the moment the rule is enabled, across
> **68 files** — about six times the work the first estimate implied. The hedge
> that the enforcement step "should measure rather than trust" is the line that
> caught it, and the cost of this finding is the reason the rule was staged
> rather than switched on wholesale.

The comment immediately above that line records that a *previous* silent-except
sweep landed 138 `E402`s that the narrower `E9,F,I` selection could not see. The
sweep removed the symptoms and left the rule that would have caught them off.

### The 20 sites

Grouped by whether a failure is already acknowledged in-place. Functions are named
because line numbers rot and names do not.

**Undocumented — 14 sites, the immediate defect:**

| Site | Enclosing function | What is lost when it fires |
|---|---|---|
| `plei/risk/failure_model.py:86,107,127` | `analyze_failures` | a whole failure mode leaves the risk report |
| `plei/risk/debt_model.py:181` | `score_debt` | a debt component disappears from the score |
| `plei/dependency/graph.py:115` | `build_dependency_graph` | bottleneck analysis degrades to empty |
| `plei/orchestrator.py:365` | `_extract_pyproject_version` | version silently becomes `"unknown"` |
| `plei/orchestrator.py:416` | `_simulation_section` | the simulation section is dropped |
| `plei/ingestion/evidence.py:52` | `ingest_evidence` | the project twin ingests partial facts |
| `plei/ingestion/source.py:68` | `ingest_source` | the twin ingests partial facts |
| `plei/calibration/scheduler.py:100` | `compute_schedule` | calibration loses entries |
| `plei/harness/evidence_loop.py:229` | `_auto_record_outcome` | prediction→outcome is never recorded |
| `flywheel/health_bridge.py:122` | `read_flywheel_health` | health reads stale or empty |
| `retrieval/vector_store.py:196` | `_ensure_collection` | collection silently unverified |
| `speech/bargein.py:144` | `_monitor_loop` | the VAD monitor dies quietly |

**Documented in-place — 6 sites:** `agent/executor.py:156` (`_persist`, "metrics
must never break a run"), `agent/paseo/adapter.py:355,387` (`drive_run`),
`factory/builders.py:135` (`build`), `factory/test_runner.py:103` (`run_tests`),
`speech/transcribe.py:63` (`_transcribe_auto`). These already carry
`# noqa: BLE001` and a stated reason; they need no change beyond enabling
BLE001 making the annotation meaningful.

**Why the PLEI cluster matters most.** Eleven of the twenty sit under `plei/`, and
`plei/harness/evidence_loop.py:229` is the site that records a prediction's
outcome. PLEI's seventh phase is calibration — measuring whether its predictions
matched reality. A swallow there means the system's self-assessment is quietly
incomplete, and an incomplete calibration set does not announce itself; it just
makes the calibration look better than the evidence supports.

**This is already-known work, not a new direction.** Commit `bac0a21`
("fix(plei): log provider-selection shaping failures instead of swallowing") fixed
one instance of exactly this class. This finding is the remainder of that work.

### The fix — enforce the class, not the instances

1. Add `"BLE001"` to `[tool.ruff.lint].select` at `pyproject.toml:49`. This
   converts 139 inert annotations into enforced, self-documenting decisions and
   surfaces the unannotated remainder in one step. Update the explanatory comment
   above the line, which currently justifies the narrow selection.
2. Measure the newly-flagged set; do not assume it is 27.
3. Triage each site: `logger.warning` with the exception, narrow the caught type,
   or `# noqa: BLE001 — <reason>` where swallowing is genuinely correct.
4. Prioritise the PLEI cluster, `evidence_loop.py:229` first.

> **Outcome, 2026-09-23 — done, but staged rather than wholesale.** The
> measurement in step 2 changed the shape of this work: 166 sites across 68 files
> is not one reviewable change. BLE001 was therefore enabled with the un-triaged
> remainder declared in an explicit `[tool.ruff.lint.per-file-ignores]` table —
> 60 files, named, with a burn-down note. The rule is enforced everywhere else,
> so a new blind `except` in any file *not* on that list fails `make lint`
> immediately, and a file that leaves the list is visibly triaged.
>
> What landed:
>
> - **14 silent `except Exception: pass` sites fixed** — 11 in `plei/` (the
>   priority cluster) plus `flywheel/health_bridge.py`,
>   `retrieval/vector_store.py` and `speech/bargein.py`. Each now logs the
>   reason. No control flow changed: the swallow is correct in every one of these
>   cases, and only the silence was the defect.
> - **Two sites narrowed instead of annotated.** `plei/dependency/graph.py` and
>   `plei/ingestion/source.py` both caught `Exception` around a single
>   `read_text()`; either is now `except (OSError, UnicodeDecodeError)`, so an
>   unexpected error surfaces rather than quietly deflating a reported count.
> - **8 files left the backlog**, including the whole PLEI analysis path.
> - **`tests/` exempted as a class, not as a backlog** — a test that provokes a
>   failure has to catch it, so `"tests/**" = ["BLE001"]` carries the rationale
>   rather than 21 manufactured backlog rows.
> - **Two regression tests** in `tests/plei/test_failure_visibility.py` assert
>   *both* halves — the failure must not propagate, and its reason must reach the
>   log. Asserting either one alone would have passed against the silent code.
>
> Burn-down order, by count: `api/research.py` (14), `tools/executors.py` (10).
> Worth noting that 130 sites already carried an inert `# noqa: BLE001` before
> this change — enabling the rule is what made those load-bearing.

### The backlog is now enforced

The staged backlog above had a hole, and it was not the one it looked like.
**Growth was already blocked** — a blind `except Exception:` in an undeclared file
fails ruff on its own, so ruff needed no help. The two gaps ruff *cannot* see are
the ones that let a backlog rot:

- **Staleness.** A row whose file no longer has any blind catches suppresses
  nothing, so ruff is satisfied and nothing ever removes it. The backlog can never
  be seen to shrink, and a file that was triaged still reads as outstanding.
- **Silent re-addition.** A newly declared file needed no reason, so "we
  considered this and the blanket catch is right" and "ruff went red and the row
  made it green" left an identical one-line diff.

Both are now gated by three files and one wiring line:

| File | Role |
|---|---|
| `config/ble001-backlog.json` | The ledger: 8 groups, each carrying a written reason; a ceiling of 60 files / 152 sites; and `tests/**` recorded as a class exemption **with its argument** rather than as a backlog |
| `scripts/ble001_backlog.py` | The gate — R0 armed (BLE001 still in `select`), R1 agreement in both directions, R2 reasons, R3 staleness, R4 ceilings, R5 coverage |
| `tests/hygiene/test_ble001_backlog.py` | 20 tests, including an injected case for every failure mode |
| `Makefile`, `lint` target | Runs it as its own step, so the backlog is checked on every gate |

**Verified end to end, not asserted.** Injecting the exact defect this exists for
— re-declaring a file that Finding 1 had cleaned, with no ledger entry — produced:

```
ruff:  All checks passed!            ← the hole, demonstrated
ble001: FAIL: 3 findings             ← exit 1
```

the three findings being: no ledger entry, a stale row suppressing nothing, and
the ceiling exceeded (61 files > 60). Removing the injected row returned it to
PASS.

**Staleness detection rests on one measured fact**, so it is pinned by a test
rather than trusted: `ruff check --isolated` reveals what `per-file-ignores` hides
— **152 sites isolated, 0 configured**. The difference *is* the backlog, so a
declared file with 0 isolated sites is provably suppressing nothing. If that
mechanism ever stops holding, every staleness finding would silently become a
false positive.

**Honest limits**, recorded in the script rather than buried here: it cannot tell
a good reason from a plausible one; the eight group reasons are a bulk
classification whose stated basis is measured (192 of 193 blind catches in these
files already log, re-raise, or convert to a reported value, with four files
sampled directly), so per-file refinement remains burn-down work; and the
ceilings only block growth — `--update` rewrites them after a batch, and the
report prints the gap so slack stays visible instead of inferred.

### Verification

The plan's requirement was:

- `make lint` — a set of gates, not one, and the claims/doc-record gates read
  test counts that step 3 can move.
- Full `pytest` at or above the 82 floor.
- At least one targeted test asserting that a probe which raises now surfaces
  instead of vanishing. Without that, the class can regress the moment BLE001 is
  ever deselected again.

**What actually verified this, 2026-09-23:**

| Check | Command | Result |
|---|---|---|
| Rule is enforced, not decorative | inject a blind `except` into clean code → `ruff check` | **caught** (1 error). Before this change the same probe passed silently |
| Guard catches a silent re-addition | re-declare a cleaned file in `per-file-ignores` with no ledger entry → `ruff` **and** the guard | ruff: *"All checks passed!"* — the hole, demonstrated; guard: **FAIL, exit 1**, with three independent findings |
| Guard's own tests | `pytest tests/hygiene/test_ble001_backlog.py` | **20 passed**, one per failure mode |
| Staleness premise holds | `ruff check --isolated` vs configured, on `src/` | **152 isolated vs 0 configured** — the difference *is* the backlog, so a declared file with no isolated sites is provably stale |
| Guard on the live tree | `python3 scripts/ble001_backlog.py` | **PASS** — 60/60 files, 0 stale, 152 sites, 8 groups |
| New script is itself clean | `ruff` + `mypy` on `scripts/ble001_backlog.py` | clean. Note `make lint` scopes ruff/mypy to `src/ tests/`, so `scripts/` is **not** covered by the gate |
| Aggregate gate | `make lint` | **PASS** — seven gates |
| Full suite + floor | `pytest tests/ --cov-fail-under=82` | **3601 passed** (+2 regression tests), 17 skipped, 75 deselected, **84.46%** vs floor 82 |

The third check is the one that matters. A guard that only ever passes is
ceremony, so the injected case was run against the **real** tree rather than a
synthetic dict: ruff gave the re-added row a clean bill of health, and the guard
failed it on three independent grounds. Restoring the file returned the guard to
PASS, so the failure was the guard's and not the tree's.

**One bug in the guard was found by its own tests** — `test_a_broken_ledger_is_a_gate_error_not_a_finding`
failed because `LEDGER.relative_to(ROOT)` raises `ValueError` for a path outside
the repository, so the intended `GateError` never surfaced and the error path
crashed instead of reporting. Fixed with the `label()` helper pattern that
`scripts/doc_records.py` already uses for the same reason.

---

## 4. Finding 2 — references to a provider that was retired

`CLAUDE.md` records that the remote frontier seam (DeepSeek) was retired
2026-09-09 under decision D1. **43 DeepSeek references remain under `src/`.**

A `grep -v deepseek` would be the wrong fix and would delete a live feature. The
census splits three ways, and only the first is stale.

**Verified before classifying:** `src/msb_v3/local_ai/` contains `anthropic.py`,
`client_factory.py`, `llama_client.py`, `ollama.py` — there is **no DeepSeek
client left**, and `client_factory.py` has no DeepSeek reference. The retired
seam's *code* is already gone. What remains is stale references to it.

### Class A — retire (stale runtime references)

| Site | Why it is stale |
|---|---|
| `plei/risk/failure_model.py:117` + the `deepseek_circuit` block in `_operational_state` | the risk model probes a circuit for a provider that no longer runs, and nothing produces the key |
| `plei/simulation/scenarios.py:5` | "What if provider DeepSeek goes down for 24h?" — a scenario for a retired provider |
| `docs/` and comment references to the retired seam as if it were live | description of a boundary that moved |

> **Correction, measured 2026-09-23 — this section was written from a grep census,
> and the census over-counted.** Three claims in the original table were wrong and
> are corrected above.
>
> 1. **`deepseek_circuit` is dead code, not a live probe.** `plei/risk/failure_model.py:117`
>    is the *only* occurrence in `src/`, and it is the **reader**. Three confirming
>    checks: (a) no writer for the key exists anywhere in `src/`; (b) PLEI reads
>    `GET /health` (`plei/ingestion/evidence.py:79`), whose live body is
>    `{"ok":true,"service":"msb-v3","version":"0.5.0","ts":…}` — no `circuit` key;
>    the retired key lived in `/system/health`; (c) a stale, gitignored `build/lib/`
>    tree still preserves the deleted producer (`build/lib/msb_v3/local_ai/deepseek.py`,
>    `api/health.py`). **The producer was deleted and the consumer was orphaned** —
>    so the probe is unreachable and its whole body can be deleted, not annotated.
> 2. **`meta/routing/worker_registry.py` is not stale.** The escalation ladder is a
>    historical record of routing decisions, and the rung is descriptive of a past
>    path rather than a live dispatch target. Removed from Class A. The original
>    claim assumed a reachable rung; that assumption was not verified.
> 3. **`ops/root_cause.py` stays** — a test asserts the historical 402 example, so
>    the pattern is load-bearing, not merely annotatable.
>
> **Net effect: ~4 real stale sites, not 43.** The finding is still work, but it is
> now scoped as a small correction rather than a sweep.

### Class B — keep, this is live

**DeepSeek Harness (`dsh`, `@deepseek-ai/dsh`) is a different thing from the
retired API seam** — a separately-governed subprocess agent worker, and a current
feature. Do not touch: `agent/providers.py` (`DshAgentProvider`, `kind="dsh"`),
`core/config.py:109-119` (`dsh_*` settings), `.env.example:328-334`, and
`plei/decisions/provider_selection.py:248`, which correctly reports
`available=False`.

### Class C — keep, but retarget

Comments that describe the retired seam by comparing it to code that still exists
become dangling when read forward. They are accurate history and should stay, but
should name the retirement rather than assume the reader knows:
`local_ai/anthropic.py:2,14,16,303` ("mirror of the DeepSeek client"),
`api/health.py:75`, `fabric/model_router.py:4`, `core/config.py:99,103`.

### One open decision

`ops/root_cause.py:82` holds a `deepseek` incident-detection regex, and `:18`/`:106`
use a DeepSeek 402 as the canonical incident example. Lines already written to
`logs/audit.jsonl` are immutable, so the pattern still matches real history.
**Recommendation:** keep the pattern, annotate it as historical-only, and let the
owner confirm. This is a judgement call, not a defect.

> **Outcome, 2026-09-23 — implemented.** Stale references retired; live ones left
> alone. Measured before and after: **43 → 40** lines mentioning `deepseek` under
> `src/` (`grep -rI -i -c deepseek src`, line counts, HEAD vs working tree). The
> residual is Class B (dsh, live), Class C comparisons (now naming the
> retirement), `meta/` (deliberately untouched), and the new explanatory
> comments this change itself added — which is why the count fell by three, not
> to zero, and why the count is the wrong success metric here.
>
> What landed:
>
> - **Class A — retired.** The dead `_operational_state` probe in
>   `plei/risk/failure_model.py` is deleted: it read `deepseek_circuit` from the
>   twin's `/health` evidence, a key with no producer since the seam was removed,
>   so it was unreachable and reported nothing. The audit-stream branch in the
>   same function no longer labels every circuit event `component="DeepSeek API"`;
>   the label is generic now, and the `deepseek` token is matched only so
>   immutable historical log lines still classify. The what-if example in
>   `plei/simulation/scenarios.py` no longer names a retired provider.
> - **Class C — retargeted.** Comments that compared newer code to "the DeepSeek
>   client" (`local_ai/anthropic.py`, `agent/providers.py`, `core/config.py`) now
>   say *retired* and cite D1 (2026-09-09), so a reader moving forward is not left
>   assuming a live sibling.
> - **`ops/root_cause.py` — kept, annotated.** The `deepseek` provider pattern and
>   its canonical 402 example stay: the append-only wake/audit stores are
>   immutable, so the pattern still classifies real history, and four test files
>   assert the exact observed shape. Both are marked historical-only.
> - **Automation docstrings — corrected** (found by this pass; the original census
>   missed them). `automation/__init__.py`, `api/automation.py` and
>   `automation/budget.py` described the brain as planning "via DeepSeek" and
>   budgeting against "DeepSeek pricing". The brain has planned on the local model
>   since D1, so the prose now matches `automation/brain.py`.
> - **Deliberately not touched.** `meta/` (worker_registry, failure/escalation,
>   probability/routing_matrix, routing/skill_bridge, benchmark) describes
>   historical routing tiers in zero-importer dead code; the production-hardening
>   record already scoped those out, and the correction above removed
>   `worker_registry` from Class A for the same reason.
>
> **One regression test** locks the behavioural half:
> `tests/plei/test_phase3_risk.py::test_failure_model_ignores_the_retired_circuit_probe`
> feeds a twin a live `deepseek_circuit` key and asserts the retired provider's
> name never reaches the report — asserting absence alone would have passed
> against the old code, so the stale key is supplied deliberately.
>
> **Verification.** `make lint` PASS (seven gates). Full `pytest tests/`:
> **3,622 passed, 17 skipped, 75 deselected**. Regenerating
> `docs/blueprint-index.md` was required and is itself a finding: the
> `core/config.py` comment edit shifted a cited line, *and* the plan document
> added in Finding 1 had never been indexed. One count claim moved with the edits
> and was updated in `docs/what-msb-v3-is.md`.

---

## 5. Finding 3 — the deselected test tiers (verification, not a fix)

The default run deselects three tiers by configuration, for a good reason:
`tests/conftest.py` deselects them so a gate cannot swing on whether a dev server
happens to be up. Measured collection, per tier:

```
live:         1 test
chaos:        5 tests
integration: 69 tests
             ─────
             75          ← exactly the 75 deselected in the green baseline
```

Prerequisites were verified live on 2026-09-23: server on `:8766` responding,
Ollama `:11434` serving `ornith:9b`, `ornith:9b-32k` and `nomic-embed-text`,
Qdrant `:6333` healthy.

**Step 3a — do this before touching any code.** Run the tiers and record what
already fails, so that pre-existing red is not later attributed to this
remediation:

```bash
make test-tiers          # sets MSB_RUN_TIERS=1 internally
```

Note that `pytest -m live` alone collects **zero** tests — the marker filter and
the tier deselection are separate mechanisms, and filtering by marker without
`MSB_RUN_TIERS=1` silently measures nothing.

**Step 3b — re-run after Findings 1 and 2** as a regression check.

Because this step is verification, its output is a measurement to be recorded in
this document's successor, not a change to be made.

> **Outcome, 2026-09-23 — run, and recorded in
> [docs/audits/tier-baseline-2026-09-23.md](../../audits/tier-baseline-2026-09-23.md).**
> Results: **live 0/1 passed (1 failed)** · **chaos 5/5 passed** ·
> **integration 68/69 passed, 1 deliberate skip**. The 75 collected matched the
> deselected count exactly, so no tier test is silently lost.
>
> The failure is pre-existing and was not caused by this remediation:
> `tests/local_ai/test_local_inference.py:155` hardcodes `"model": "qwen3:8b"`,
> which is no longer pulled, so Ollama answers 404. It is invisible in every
> default run because the method carries `@pytest.mark.live` — the green suite and
> the red tier are separated by exactly the deselection this section is about.
> The skip is a guard behaving correctly: `test_cold_state_verification.py:193`
> refuses to `SIGKILL` the live instance holding `:8766` unless told to.
>
> Running this **before** Findings 1 and 2 is what makes the failure attributable
> to the past rather than to the work in this document.

---

## 6. Finding 4 — the startup licence gate (a decision, not a bug)

`src/msb_v3/__main__.py` defines `_check_source_license()`, which shells out to
`scripts/verify-license.sh`, verifies `~/.msb-v3/source-license` against
`config/license-authorized-keys`, and `raise SystemExit` when it fails.
`MSB_CONTAINER=1` and `MSB_CI=1` bypass it.

**No code should be written for this section until the owner decides.** Two facts
belong in that decision:

> The startup gate in the code is spelled `license` throughout (`verify-license.sh`,
> `source-license`, `license-authorized-keys`); prose in this plan uses `licence`
> to match `docs/what-msb-v3-is.md`. The identifiers are not renamed here.

1. **It is a weaker gate than it appears.** A fork's CI sets `MSB_CI=1` and
   passes; the Docker image sets `MSB_CONTAINER=1` and passes. It stops a plain
   anonymous `git clone` plus `python -m msb_v3`, and nothing else. It is a
   *social* signal priced as a functional lockout.
2. **It contradicts the workspace constitution.** `~/CLAUDE.md` states principle
   3, *abundance*: "not artificial scarcity, manipulation, or exploitation", and
   the practical translation "avoid lock-in; preserve user ownership of work".
   `README.md` already concedes that MIT grants the right to run the code, and to
   remove the guard. This is the one place in the tree where the code argues with
   the constitution.

Three options, for the owner to choose:

- **(a) Keep as-is.** Costs the constitutional tension and surprises anyone who
  trusts the MIT badge.
- **(b) Soften to an unmissable notice.** Print the licence holder and the
  `scripts/request-access.sh` path on every start, and continue. Preserves the
  attribution and relationship intent, removes the functional lockout. **The
  audit's recommendation** — it is the only option that serves both the stated
  intent and principle 3.
- **(c) Remove entirely.** Pure MIT, zero friction.

Whichever is chosen, `README.md`'s "Access & licensing" section must be updated in
the same change or it becomes inaccurate, and `docs/what-msb-v3-is.md`'s "What is
not built yet" block names the gate explicitly and will need the same treatment.

---

## 7. Sequencing, and why it is this order

| Order | Workstream | Type | State |
|---|---|---|---|
| 3a | Tier baseline | verification | **DONE** — 1 pre-existing failure, 1 deliberate skip, recorded |
| 1 | Silent swallows (Finding 1) | defect + guard | **DONE** — 14 sites fixed, 8 files out of the backlog |
| — | BLE001 backlog guard | enforcement | **DONE** — ledger, gate, 20 tests, wired into `make lint` |
| 2 | Stale provider refs (Finding 2) | hygiene | **DONE** — Class A retired, Class C retargeted, `meta/` left by decision, 1 regression test |
| 4 | Licence gate (Finding 4) | decision, then code | **blocked on the owner** |
| 3b | Tier regression | verification | pending 1, 2, 4 |

Two real dependencies drive the order:

- **`plei/risk/failure_model.py` appears in both Findings 1 and 2** — swallows at
  `:86/:107/:127` and a dead provider probe at `:117`, in the same function.
  Landing them out of order costs a conflict. (Finding 1's half is already landed,
  so Finding 2 is now editing a file that has moved.)
- **Finding 1 can move test counts, and two gates assert on test counts.**
  `verify-claims.py` verifies that test-count claims match live collection, and
  `scripts/doc_records.py` measures a `collected`/`deselected` pair. A change that
  adds a test can therefore turn `make lint` red for a reason unrelated to the
  change itself.

**Do not land these as one change.** Finding 1 touches eleven PLEI call sites and
can shift counted numbers; Finding 2 rewrites comments across six subsystems. Kept separate,
a failing count is attributable to one of them.

---

## 8. Conventions each change must honor

From `CLAUDE.md` and `README.md` — these are enforced, not advisory:

- Branch as `<type>/<slug>`; commits signed and carrying the `Signed-off-by` DCO
  trailer; contributions fork-based.
- `make hooks-install` once per clone, or the pre-push hook runs
  `make portability` (the full suite from a foreign checkout path) and blocks.
- `make lint` is ruff **+** `mypy src` **+** lock check **+** `verify-claims.py`
  **+** `doc_records.py` **+** the MoIE policy gate **+** `ble001_backlog.py`.
  A change is not verified because pytest passed.
- Coverage floor 82. Version stays `0.5.0` unless a release is being cut, in
  which case the version sources and a declaration both move
  (`python3 scripts/doc_records.py --release <tag>` prints them).
- **Adding a markdown file under `docs/` changes a published count.** The Scale
  block in `docs/what-msb-v3-is.md` declares markdown file and line counts for
  `docs/`, and `doc_records.py` measures them live. This plan's own addition
  changed that number, which is the mechanism working as designed — but any
  further doc edit must carry the block with it.
- **Adding a numbered document under `docs/` drifts the generated
  `docs/blueprint-index.md`**, which `tests/docs/test_blueprint_index.py`
  asserts. Regenerate with `python3 scripts/blueprint_index.py --write` and
  review the diff; do not hand-edit the index, which says so itself.

---

## 9. What this plan does not claim

Recorded because an audit that lists only findings is less honest than one that
lists its own edges.

- **The deselected tiers were not run.** Their collection counts were measured;
  their results were not. The tier baseline exists precisely because that gap is real.
- **The Vesta trust perimeter, the signed-device approval paths, the desktop
  application, and PLEI's Monte Carlo calibration were read, not exercised.**
  Their contracts were inspected; they were not attacked.
- **MoIE's 0.425 recall is against this repository's own corpus**, not against an
  adversary. It is a measured floor, not a ceiling on what an attacker achieves.
- **The backlog guard's eight group reasons are a bulk classification, not eight
  per-file judgements.** Its stated basis is measured — 192 of 193 catches in the
  backlog already log, re-raise, or convert to a reported value, plus four files
  sampled directly — but a plausible reason is not distinguishable from a correct
  one by this gate. Per-file refinement stays burn-down work.
- **The guard's ceilings only block growth.** They go slack as files are triaged
  and must be lowered deliberately with `--update`; the report prints the gap so
  slack stays visible rather than inferred.
- **The audit is not a review of the author's competence**, and nothing above
  should be read as one. The finding of greatest weight is that the project's
  measured coverage matched its published claim, its gate publishes its own
  weakness, and it contains a subsystem built to falsify its own architecture
  claims. The gap named here is convergence, not quality.
