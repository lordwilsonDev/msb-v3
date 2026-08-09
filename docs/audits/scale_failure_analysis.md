# Scale Failure Analysis

## Finding
The system became more capable through the Phase 2 vertical slice, but trust controls did not grow with it. The most exposed gap is the MCP bridge, which currently exposes filesystem operations without authentication, authorization, or audit logging.

## Failure Mode
1. External request reaches MCP bridge over `0.0.0.0:8766`
2. No identity verification
3. Filesystem operation executes with full process permissions
4. No actor/target/result record
5. Attack surface scales linearly with added adapters and workflows

## Required Countermeasures
1. Bind to loopback or require mTLS for remote access
2. Enforce authentication boundary at route level
3. Enforce authorization with allowlisted operations per identity
4. Enforce path traversal protection for filesystem operations
5. Emit structured audit events for every filesystem action

## Growth Paths
- Intelligence: more agents, models, workflows, skills
- Trust: authentication, permissions, audit, recovery, isolation

These must advance together. The factory without trust is a swarm of privileged scripts; with trust, it becomes infrastructure.
