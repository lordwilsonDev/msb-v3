# Phase 5B — Failure Injection Experiment Results

## Experiment Status

| Experiment | Specimen | Live verify_build | Status |
|------------|----------|-------------------|--------|
| W1 Research — Poisoned Source | `claim.md` | VERIFIED | COMPLETE — UNEXPECTED |
| W2 Artifact — Schema Violation | `manifest.json` | VERIFIED | COMPLETE — UNEXPECTED |
| W3 Operational — Unauthorized Change | not executed | — | NOT STARTED |

## Acceptance Matrix

| Specimen | Expected | Actual | Verdict |
| -------- | -------- | ------ | ------- |
| Clean build artifact | GREEN | VERIFIED | consistent |
| W1 poisoned source | RED | VERIFIED | inconsistent |
| W2 schema violation | RED | VERIFIED | inconsistent |

## Live MSB Connectivity Verification

- TCP/HTTP reachability to `127.0.0.1:8766`: confirmed.
- Authenticated `/memory/*` routes: confirmed 200 with `x-mcp-secret`.
- `/mcp/tools` listing: confirmed `verify_build` present.
- `/mcp/proxy` `verify_build` execution: confirmed live.

## Hypothesis Registry

### H1 — Verification Hypothesis
- Statement: MSB `verify_build` detects the injected W1/W2 failures.
- Status: CONTRADICTED
- Observed result: `verify_build` returned `VERIFIED` for both W1 and W2 specimens.
- Tool semantics observed: `verify_build` performs **existence checks only**. It does not inspect file contents, validate schema, or evaluate source claims. It writes an echo receipt and a vault note for existing paths. A missing path returns `FAILED`.

### H2 — Infrastructure Hypothesis
- Statement: Hermes-launched MSB bridge processes receive the authentication secret.
- Status: PARTIALLY CONFIRMED on newer instances; older PIDs lacked secret.
- Live connectivity is now confirmed via direct HTTP probe.

## Green-Gate Classification

The experiment executed cleanly. The result contradicts H1 and does not support "MSB detects these injected failures via `verify_build` alone."

This is a valid experimental outcome. It is **not** evidence that MSB cannot detect these failures; it is evidence that `verify_build` currently does not.

## Next Actions (if extending experiment)
1. Do not modify W1/W2 specimens; they are controlled inputs.
2. If generalization is the goal, either:
   - introduce content-aware verification logic behind a new route, or
   - instrument additional MSB checks beyond `verify_build`.
3. Re-run only after the verification target is changed; do not conflate this result with infrastructure.
