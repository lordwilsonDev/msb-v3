# Phase 2 Risk Register

## Critical
1. MCP bridge exposes filesystem operations without authentication/authorization.
2. Bind address `0.0.0.0:8766` allows external access to privileged operations.

## High
3. Path traversal risk: no confirmed allowed-root or normalization boundary.
4. No audit log for filesystem actions; cannot reconstruct trust decisions.
5. Adapter contract tests pass, but real subprocess adapters may leak env or working directory state.

## Medium
6. Workflow execution currently wraps one agent execution; multi-step orchestration lacks rollback semantics.
7. Artifact verification is lightweight; stronger contract validation is deferred.
