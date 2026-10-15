# MSB-v3 Domain Context

MSB-v3 is a single-operator, local-first, governed runtime.

Canonical path:
request -> intent -> task DAG -> capability/authorization
-> governed execution -> verification -> evidence -> audit -> replay

Authority:
MODEL proposes
HARNESS constrains execution
VERIFIER evaluates evidence
MSB-v3 enforces deterministic policy
HUMAN resolves authority-boundary decisions

| Term | Definition |
|---|---|
| ActionGate | Deterministic capability/taint authorization boundary. |
| Capability | Unit of authority; must be registered/granted explicitly. |
| Gateway | Auditable compute and authorization routing layer. |
| Evidence Spine | Hash-chained decision-level provenance records. |
| UAC / Audit Chain | Append-only durable audit event chain. |
| Receipt | Per-run reconstruction of request, authorization, execution, verification, and result. |
| MoIE | Inversion/risk pre-filter; not the security boundary. |
| PLEI | Project Lifecycle Engineering Intelligence and calibration layer. |
| Factory | Governed engineering pipeline from classify through merge. |
| Vesta | Signed-device trust and approval perimeter. |
| Mission | Versioned control-plane object linking objective, artifacts, tasks, verification, costs, and decisions. |
| Harness | Bounded execution wrapper binding identity, workspace, tools, permissions, budget, tests, and artifact collection. |
| Frozen | Surface intentionally held stable. |
| Optional | Present but not canonical. |
| Proposed | Design text; not implementation evidence. |
| UNKNOWN | Evidence is insufficient. UNKNOWN is not SAFE. |
