# ADR-001: PLEI — Project Lifecycle Engineering Intelligence

**Status**: Accepted  
**Date**: 2026-08-25  
**Decision Maker**: Lord Wilson  

## Context

MSB v3 grew to 43K lines across 35 subsystems with 2K+ tests. As the codebase expanded, there was no systematic way to answer:
- Where is this project in its lifecycle?
- What capabilities are missing?
- What should we work on next?
- How uncertain is the path forward?
- Are our predictions about the project accurate?

The existing harness (DeepSeek agent + provider seam + ActionGate) could execute tasks but couldn't reason about the project itself.

## Decision

Build PLEI as a **non-replacing intelligence layer** above the existing harness. PLEI:

1. **Does not replace** the agent harness, provider seam, MSB governance, or skills system
2. **Decides** what engineering intelligence should be applied and when
3. **Maintains** a living project digital twin fed by 7 ingestion layers
4. **Classifies** lifecycle state from evidence, not calendar milestones
5. **Simulates** possible futures with Monte Carlo (not just generates plans)
6. **Executes** decisions through the governed bridge (Gate → Execute → Verify → Evidence)
7. **Calibrates** whether its predictions match reality

## Architecture

```
PLEI (intelligence plane)
  → DeepSeek Harness (execution plane)
    → 10-provider seam (capability plane)
      → MSB v3 (governance plane)
```

Key design choices:
- **Evidence-first**: every assertion carries provenance (VERIFIED/OBSERVED/INFERRED/CLAIMED/UNKNOWN)
- **Probabilistic**: Monte Carlo simulation, not deterministic planning
- **Self-calibrating**: prediction → outcome → error → better predictions
- **Governed**: all execution through ActionGate, never bypassing MSB

## Consequences

### Positive
- Project state is reconstructible from evidence, not assumptions
- Decisions have quantified uncertainty and alternatives
- The system learns from its own prediction errors
- Provider routing becomes project-aware (performance, specialization, risk)
- The full loop is closed: Understand → Model → Simulate → Decide → Execute → Verify → Learn

### Negative
- Adds complexity (7 phases, 28 modules, ~8K lines)
- Monte Carlo results are model outputs, not facts — must be interpreted carefully
- Calibration requires accumulated prediction/outcome pairs to be meaningful
- The project twin must stay synchronized with actual project state

### Risks
- Over-engineering: the system could become another idea-generation engine that prevents closure
- False precision: Monte Carlo outputs may be mistaken for ground truth
- Scope creep: PLEI's analysis could continuously expand without producing actionable results

## Mitigations
- The Closer skill enforces closure discipline
- All forecasts carry assumptions, data sources, and confidence levels
- PLEI recommendations must include evidence, uncertainty, and alternatives
- The calibration engine explicitly measures prediction accuracy

## Alternatives Considered
1. **Static project dashboard**: Rejected — no simulation, no prediction, no learning
2. **External project management tool**: Rejected — not integrated with the governed loop
3. **LLM-only analysis**: Rejected — no probabilistic reasoning, no calibration, no evidence chain

## References
- PLEI v1.0 spec (pasted in conversation 2026-08-25)
- 1,877 green tests at decision time
- 7 commits shipped (Phases 1-7)
