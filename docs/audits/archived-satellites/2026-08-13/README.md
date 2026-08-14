# Archived Dormant Satellites — 2026-08-13

These files were retired after a direct reference audit showed that they were
stubs or non-persistent duplicates of live MSB v3 systems. They are retained
here for historical inspection; they are not importable production code and
are not part of the pytest test paths.

## Archived implementations

| Archived area | Disposition | Live replacement or gap |
|---|---|---|
| `sovereign_runtime/brain/ail_pipeline.py` | Placeholder output only | `triumvirate/meta_cognitive_planner.py` |
| `sovereign_runtime/brain/moie_swarm.py` | Placeholder output only | `triumvirate/meta_cognitive_planner.py` |
| `sovereign_runtime/brain/recursive_planner.py`, `planner_memory.py`, `plan_models.py` | Heuristic string bisection and in-memory planner state | `agent/planner.py`, `agent/dag.py`, `agent/executor.py` |
| `sovereign_runtime/brain/__init__.py` | `BrainService` glue around the retired planner | Live agent and flywheel paths |
| `personal_intelligence/context_engine` | In-memory substring search | `retrieval/engine.py`, `retrieval/planner.py` |
| `personal_intelligence/memory_graph` | In-memory entity graph | Durable entity/relationship memory remains a documented future gap |
| `personal_intelligence/provenance` | In-memory ledger | `uac/audit_chain.py` |
| `personal_intelligence/agent_factory` | Glue around the retired PIL components | No replacement needed |

The active `personal_intelligence/skill_engine` remains in the source tree as
a deferred source for a possible future trigger-matching port. It is not wired
into the live router. The active `sovereign_runtime.events.event_bus` also
remains available but has no concrete production consumer and is not adopted
speculatively.

Dedicated tests for the retired planner and brain glue are archived alongside
their implementations. The remaining event-bus, config, audit-chain, and
SkillEngine tests stay in the active suite.
