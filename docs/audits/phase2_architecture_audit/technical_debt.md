# Phase 2 Technical Debt

## Design Gaps
1. `core/factory.py` is synchronous but wraps `asyncio.run`; later expansion should adopt native async orchestration.
2. PrimeAgent adapter stub returns plan metadata only; real execution needs verified contract.
3. gstack integration is command-mapped, not stateful workflow execution.

## Security Debt
1. MCP bridge lacks auth boundary; treat as blocking before any capability expansion.
2. No path allowlist or traversal guard for filesystem-facing operations.
3. No actor/action/target audit schema.

## Verification Debt
1. Vertical slice tests use fake adapters; real subprocess path remains unverified.
2. Cross-skill orchestration not yet exercised by tests.
