# SMI-018 Security Finding

## Source
Phase 2 architecture audit, `scale_failure_analysis.md`

## Finding
MCP bridge in `msb-v3` exposes filesystem operations without authentication, authorization, path traversal protection, or audit logging. Bind address `0.0.0.0:8766` allows external access.

## Impact
- High: unauthorized filesystem read/write/delete/move
- High: no actor attribution for operations
- High: attack surface scales with added adapters

## Recommendation
1. Bind to `127.0.0.1` or require mTLS for remote access
2. Enforce authentication at route level
3. Enforce path allowlist and normalization
4. Emit structured audit events for every filesystem action

## Status
Open. Not addressed in SMI-017 frozen release.
