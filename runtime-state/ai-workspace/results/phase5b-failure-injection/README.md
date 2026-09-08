# Phase 5B — Failure Injection Experiment

## Status: EXECUTED — RESULTS RECORDED

This experiment tested whether MSB `verify_build` detects injected failures in
controlled specimens. The experiment was instrumented cleanly and executed
against live MSB without modifying any verification logic.

## What was tested

| Workload | Specimen | Injection | Live execution |
|----------|----------|-----------|----------------|
| W1 Research | `claim.md` | Poisoned quantization source claim | ✅ EXECUTED |
| W2 Artifact | `manifest.json` | Schema violation (missing required field) | ✅ EXECUTED |
| W3 Operational | N/A | Not executed | ⏸ NOT STARTED |

## Result

`verify_build` returned `VERIFIED` for both W1 and W2 specimens.

This is because `verify_build` currently performs **existence checks only**:
it verifies that claimed files/tests exist on disk, then writes an echo receipt
and a vault note. It does not inspect file contents, validate schema, or
evaluate source claims.

## Acceptance Matrix

| Specimen | Expected | Actual | Verdict |
| -------- | -------- | ------ | ------- |
| Clean build artifact | VERIFIED | VERIFIED | consistent |
| W1 poisoned source | FAILED | VERIFIED | inconsistent |
| W2 schema violation | FAILED | VERIFIED | inconsistent |

## Hypothesis Registry

- **H1** (verification hypothesis): CONTRADICTED by live execution.
- **H2** (infrastructure hypothesis): PARTIALLY CONFIRMED on newer bridge instances.

## Green-Gate Note

A failed experiment and an experiment that could not execute are different.
This experiment executed. The result is scientifically valid: it shows that
`verify_build` alone is not content-aware enough to catch these failure modes.

Do not conflate this with infrastructure. The infrastructure question is
separate: newer Hermes-launched bridge processes inherit `MCP_BRIDGE_SECRET`,
older ones did not.

## Next actions (if extending)

1. Keep W1/W2 unchanged — they are controlled specimens.
2. If generalization is the goal, either:
   - add content-aware verification logic behind a new route, or
   - instrument additional MSB checks beyond `verify_build`.
3. Re-run only after the verification target changes; do not conflate this result
   with infrastructure state.
