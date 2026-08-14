"""Structural contracts for the retained Personal Intelligence surface."""

from __future__ import annotations

import importlib

import pytest

EXPECTED_PUBLIC = {
    "personal_intelligence.skill_engine": ["SkillEngine", "SkillFile"],
}


@pytest.mark.parametrize("module,public", list(EXPECTED_PUBLIC.items()))
def test_pil_subpackage_exports(module: str, public: list[str]) -> None:
    mod = importlib.import_module(module)
    for name in public:
        assert hasattr(mod, name), f"{module} missing {name}"


def test_personal_intelligence_package_importable() -> None:
    importlib.import_module("personal_intelligence")
