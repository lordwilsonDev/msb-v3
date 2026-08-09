"""Structural contracts for PIL subpackages.

- Each subpackage must expose only intended public APIs.
- PIL itself must be independently importable from src/ without touching sovereign_runtime.
"""

from __future__ import annotations

import importlib

import pytest

EXPECTED_PUBLIC = {
    "personal_intelligence.context_engine": ["ContextChunk", "ContextEngine"],
    "personal_intelligence.memory_graph": ["Entity", "MemoryGraph", "Relationship"],
    "personal_intelligence.skill_engine": ["SkillEngine", "SkillFile"],
    "personal_intelligence.agent_factory": ["AgentBlueprint", "AgentFactory"],
    "personal_intelligence.provenance": ["MemoryLedger", "ProvenanceEntry"],
}


@pytest.mark.parametrize("module,public", list(EXPECTED_PUBLIC.items()))
def test_pil_subpackage_exports(module: str, public: list[str]) -> None:
    mod = importlib.import_module(module)
    for name in public:
        assert hasattr(mod, name), f"{module} missing {name}"


def test_personal_intelligence_package_importable() -> None:
    importlib.import_module("personal_intelligence")
