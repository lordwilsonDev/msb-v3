# MSB-v3 Convergence State

Date: 2026-08-27
Status: in-progress

## Known-good baseline

- Release: `v0.3.2`
- SHA: `bf27f6a565539226015cc64b4477c71420ff8227`
- Remote: `origin/main` currently resolves to the same SHA

## Current experimental head

- SHA: `452e2b2`
- Branch: `main` (local working state)
- Divergence: 15 commits beyond the known-good baseline, primarily speech P0–P2 work

## Main status

- Release baseline remains recoverable.
- No reset, force-push, tag rewrite, or remote branch mutation was performed during this convergence pass.
- Working tree contains unrelated pre-existing modifications in `.plei/calibration.jsonl` and `artifacts/hygiene/daily_gate_events.jsonl`.

## Validation status

- Core focused validation: GREEN
- Historical verification: GREEN; all CI checkout steps now request full history
- Ruff: GREEN
- mypy: GREEN
- Closure drift: GREEN with eight non-blocking missing-SHA warnings
- Production closure: NOT CLOSED

## Blockers

1. CI runtime isolation; workflows still contain legacy port-kill behavior.
2. Centralized Qdrant environment contract and preflight.
3. Reproducible virgin-clone gate independent of developer services and state.
4. Attack matrix with controlled outcomes and evidence capture.

## Experimental surfaces

- speech
- energy_matrix

Neither surface is promoted to the release contract by this document.

## Next promotion

None until mandatory production gates are green and the evidence is reproducible from a fresh environment.
