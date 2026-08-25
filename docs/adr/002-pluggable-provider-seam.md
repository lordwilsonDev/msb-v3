# ADR-002: Pluggable Provider Seam

**Status**: Accepted  
**Date**: 2026-08-25  
**Decision Maker**: Lord Wilson  

## Context

MSB v3 needs to route AI inference across multiple providers (local Ollama, DeepSeek API, Claude, Gemini, etc.) without coupling the agent harness to any single provider. Different providers have different strengths, costs, latency characteristics, and availability.

## Decision

Implement a provider seam with:
- **10 interchangeable providers** behind a unified interface
- **Fallback chains**: primary fails → automatic fallback to next available
- **Provider intelligence**: PLEI routes based on task specialization, risk tier, latency, cost
- **Local-first**: `local.slice` (Ollama/llama.cpp) is always available as last resort
- **No vendor lock-in**: providers are configured, not hardcoded

## Consequences

### Positive
- DeepSeek 402 (payment required) → automatic fallthrough to Claude (proven in live test)
- Provider routing can be project-aware via PLEI
- Local inference always available as safety net
- New providers added by configuration, not code changes

### Negative
- Fallback chains add latency on failure paths
- Provider health monitoring adds complexity
- Cost tracking across providers requires aggregation

### Risks
- Provider API changes can break adapters
- Fallback chains may mask underlying issues

## References
- Live test 2026-08-24: DeepSeek 402 → paseo.claude fallback succeeded
- 10 providers registered at decision time
