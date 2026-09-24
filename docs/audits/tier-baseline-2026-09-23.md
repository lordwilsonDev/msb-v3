# Tier baseline — what the live, chaos and integration tiers actually produce

Date: 2026-09-23
Status: **baseline recorded** — 1 real failure found, 1 deliberate skip. Nothing fixed here.
Recorded by: Buffy (Freebuff coding agent) — external audit, running the project's own tiers

> **Why this exists.** The default `pytest tests/` run deselects three tiers *by
> configuration* (`tests/conftest.py`), which is the right design — a gate must not
> swing on whether a dev server happens to be up — but it means a green suite says
> nothing about them. Measured 2026-09-23: **75 tests are deselected**, and this
> record is what they do when you run them. It is a baseline, not a fix: it exists
> so that pre-existing red cannot later be mistaken for a regression caused by the
> remediation work in
> `docs/blueprints/plans/2026-09-23-audit-remediation-four-findings.md`.

## The command

```bash
bash scripts/seed-research-runtime.sh
MSB_RUN_TIERS=1 pytest tests/ -m live         # each tier run separately, for isolation
MSB_RUN_TIERS=1 pytest tests/ -m chaos
MSB_RUN_TIERS=1 pytest tests/ -m integration
```

`make test-tiers` runs the hermetic core *and* all three tiers together; the tiers
were run individually here so a failure could be attributed to one of them.

> **Trap worth recording:** `pytest -m live` collects **zero** tests without
> `MSB_RUN_TIERS=1`. The marker filter and the tier deselection are separate
> mechanisms, so filtering by marker alone silently measures nothing.

## Environment

| Requirement | State at recording time |
|---|---|
| msb-v3 on `:8766` | up — `{"ok":true,"service":"msb-v3","version":"0.5.0"}` |
| Ollama on `:11434` | up |
| Models actually pulled | `ornith:9b`, `ornith:9b-32k`, `nomic-embed-text:latest` |
| Qdrant on `:6333` | up — `healthz check passed` |
| `OLLAMA_MODEL` | `ornith:9b` |

## Results

| Tier | Collected | Passed | Failed | Skipped | Verdict |
|---|---:|---:|---:|---:|---|
| live | 1 | 0 | **1** | 0 | **RED** |
| chaos | 5 | 5 | 0 | 0 | green |
| integration | 69 | 68 | 0 | 1 | green (skip is deliberate) |
| **total** | **75** | **73** | **1** | **1** | one real failure |

The 75 matches the deselected count in the default run exactly, so no tier test is
being silently lost.

## The failure — a stale hardcoded model

```
tests/local_ai/test_local_inference.py::TestOllamaIntegration::test_ollama_chat_endpoint
E  assert 404 == 200
```

**Cause.** The test hardcodes the model name at `tests/local_ai/test_local_inference.py:155`:

```python
"model": "qwen3:8b",
```

`qwen3:8b` is **not pulled on this machine** (see the environment table), so Ollama
returns 404. This is not flakiness: the test's own docstring states that a
timeout is a skip but that "real breakage still surfaces as a non-200". It got a
non-200, so the test is correctly reporting a real problem.

**This is pre-existing, and not caused by any change in this session.** Verified
three ways:

1. The audit's own diff touches no file under `src/msb_v3/local_ai/` or `tests/local_ai/`
   (`git status --short` on both paths is empty).
2. The model default moved `qwen3:8b → ornith:9b` in `c77d582` (2026-09-22).
3. The test file was last touched by `da5efe4` (2026-09-22), whose subject is
   *"test: accept ornith models; assert the configured model is pulled, not 'any
   qwen'"* — so the intent to fix this class of staleness was declared, and this
   site was missed.

**Where the red hides.** The method carries `@pytest.mark.live`, so it is
deselected in every default run and every hermetic CI job. The suite is green, the
tier is red, and the gap between the two is exactly the mechanism this baseline
exists to expose.

**Fix direction (not applied here).** Update the test to assert against the
*configured* model rather than a literal — which is what `da5efe4` says the tests
should do — or pull `qwen3:8b`. Asserting the configured model is the intended
fix; re-pulling a retired default would re-hardcode the staleness. Note also
`tests/local_ai/test_local_inference.py:166` documents that `qwen3:8b` is a
thinking model whose content may be empty, so the assertions after the status
check may be written against that model's semantics specifically and deserve a
look rather than a one-line substitution.

## The skip — a safety guard working correctly

```
SKIPPED [1] tests/security/test_cold_state_verification.py:193:
  refusing to SIGKILL whatever owns the default port (a live msb-v3):
  point the run at a standby with MSB_BASE_URL, or set
  MSB_ALLOW_RESTART_LIVE=1 to restart the default instance deliberately
```

This is **not a defect and not a coverage gap to close.** The test needs to kill a
server to verify cold-state recovery, and it refuses to kill the live instance
holding `:8766` unless told explicitly. That is the right default for a test that
otherwise destroys the running system. To exercise it, point the run at a standby
(`MSB_BASE_URL`) or opt in with `MSB_ALLOW_RESTART_LIVE=1`; neither was done here,
because restarting the live service is outside a read-only baseline.

## What this baseline does not cover

- **Only the hermetic prerequisites were checked**, not a cold machine: the
  service, Ollama and Qdrant were all already up. A fresh-boot run may surface
  ordering problems this cannot see.
- **The skip was not converted into a run.** Cold-state recovery is therefore
  still unverified in this record, deliberately.
- **Timing was not measured.** How long the tiers take under load is not recorded.
- **No conclusion is drawn about whether the tiers should run in CI.** They are
  timing-sensitive by design, and the case for keeping them out of the hermetic
  gate is not weakened by this record.

## What follows from it

One fix, one decision:

1. **Fix the live tier** — de-hardcode the model in
   `tests/local_ai/test_local_inference.py` (≈1 line plus a look at the
   thinking-model assertions), which should turn the live tier green and make
   `make test-tiers` a meaningful signal rather than a known-red one.
2. **Decide where the tiers run.** A tier that is red in every manual run and
   absent from CI has no consumer; either give it a scheduled/self-hosted job (the
   repo already has a self-hosted `harness-gate` runner) or accept it as a
   manual-only probe and say so where the tiers are documented.

Recorded as-is: the failure is real, the skip is correct, and neither was caused
by the remediation already landed.
