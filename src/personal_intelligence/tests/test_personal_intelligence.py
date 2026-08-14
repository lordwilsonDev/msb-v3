"""Active contract tests for the retained SkillEngine source package."""

from __future__ import annotations

from pathlib import Path

from personal_intelligence.skill_engine import SkillEngine


def test_skill_engine_parses_frontmatter() -> None:
    engine = SkillEngine()
    skill = engine._split_frontmatter("---\ntriggers: deploy,ship\n---\nbody")[0]
    assert skill["triggers"] == "deploy,ship"


def test_skill_engine_matches_by_triggers() -> None:
    engine = SkillEngine()
    engine.register(
        SkillEngine._parse_fragment(  # type: ignore[attr-defined]
            "deploy-skill",
            Path("/tmp"),
            "---\ntriggers: deploy,ship\n---\nDo deploy.",
        )
    )
    hits = engine.match("please deploy this")
    assert [skill.name for skill in hits] == ["deploy-skill"]
