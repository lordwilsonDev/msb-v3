# ADR-004: CLI Provider Isolation (C5)

**Status**: ACCEPTED (with documented risk)
**Date**: 2026-08-26
**Deciders**: Wilson + Buffy

## Decision

Accept the CLI provider's in-process execution model for sovereign single-machine operation. Do NOT build subprocess/IPC isolation at this time.

## Context

The `CliAgentProvider` runs external agents (Claude Code, Codex, OpenCode) as subprocesses. The code documents "HIGH risk by construction" because it runs in the same process space with no container/process isolation.

The safety model is:
1. CLI providers declare **zero capabilities** (empty tuple)
2. They run in **isolated worktrees** (temp directories)
3. They respect **timeout bounds** and **output bounds**
4. **Capability injection is blocked** — sovereign capabilities are explicitly excluded
5. **Operator registration with scoped capabilities** is required for real work

18 tests in `test_cli_provider_isolation.py` prove the escape surface is closed at the interface level.

## Rationale

1. **The risk is bounded**: CLI providers capture stdout/stderr from external commands. They don't execute arbitrary code in the host process. The external tool runs constrained in its own worktree.

2. **The isolation boundary is real**: The CLI provider can't grant capabilities it doesn't have. The ActionGate prevents any capability escalation. The evidence spine records everything.

3. **Sovereign single-machine context**: This runs on a Mac mini under launchd. There's no network-facing surface, no multi-tenant concern, no container orchestration. The "process space" risk is the external tool doing something bad in its own temp directory — not in the host.

4. **Building subprocess/IPC isolation is premature**: It would add complexity (subprocess management, IPC protocol, error handling) without meaningfully reducing risk in the current deployment model.

5. **The tests prove the boundary**: 18 test cases covering capabilities, worktree isolation, timeout bounds, output bounds, capability injection, and sovereign capability exclusion.

## Risk Accepted

- The CLI provider runs in the same process space as the governed runtime
- No container/process-level isolation
- External tools (Claude Code, Codex) execute in isolated worktrees but share the host process for I/O

## Reversal Condition

Revisit if any of:
- CLI provider is exposed to untrusted input (network API)
- External tool demonstrates capability escape
- Multi-tenant deployment becomes necessary
- Governance requirements tighten (SOC2, HIPAA)

## Evidence

- `tests/integrations/test_cli_provider_isolation.py` — 18 tests
- `src/msb_v3/agent/providers.py` — CliAgentProvider implementation
- ActionGate enforcement in `agent/safety.py`
