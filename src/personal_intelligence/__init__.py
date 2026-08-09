"""Persistent personal intelligence layer (PIL) for MSB-v3.

This package implements the Personal AGI architecture pattern:
- context_engine : ingest/index/retrieve personal context
- memory_graph   : entities, relationships, timelines
- skill_engine   : parse/compile/register executable skill files
- agent_factory  : generate agent blueprints from skills/memory
- provenance     : memory ledger with verification metadata

Design rules:
- Each subpackage must be independently importable.
- Models/contracts live in each subpackage; avoid top-level coupling.
- Every write path emits provenance events.
"""

from personal_intelligence.context_engine import (  # noqa: F401
    ContextEngine,
)
from personal_intelligence.memory_graph import (  # noqa: F401
    MemoryGraph,
)
from personal_intelligence.skill_engine import (  # noqa: F401
    SkillEngine,
)
