"""Deferred Personal Intelligence skill-discovery surface.

The former context, graph, provenance, and agent-factory modules were
retired as non-persistent duplicates of live MSB v3 systems. SkillEngine is
retained as a possible source for a future need-gated trigger-matching port;
it is not adopted by the live runtime.
"""

from personal_intelligence.skill_engine import SkillEngine

__all__ = ["SkillEngine"]
