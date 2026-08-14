"""Capability Gateway — the single dispatcher between runtime and (local|remote) compute.

Maps to the *Capability Gateway* plane in
`docs/blueprints/plans/m1-governance-node-architecture.md` (Compute
Plane, §3). The M1 routes every compute call through here so the
"why is this call going to local vs frontier" decision is auditable,
and so the "this call requires human authorization" rule (§5
Experimental Plane) becomes a code check, not a moral principle.

Public surface — keep it small. Anything more belongs in a separate
`policy.py` / `audit.py` once we need it.

The gateway NEVER raises; it returns a `GatewayDecision` so callers
can branch on `.authorized` without try/except. A denied call records
its denial into the audit chain — you want to *see* the denied
attempts, not just the allowed ones.
"""

from __future__ import annotations

from msb_v3.gateway.route import (
    GatewayCall,
    GatewayContext,
    GatewayDecision,
    route,
)

__all__ = [
    "GatewayCall",
    "GatewayContext",
    "GatewayDecision",
    "route",
]
