"""Skill router — discover and execute Hermes skills via FastAPI."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["skills"])

_SKILLS_DIR = Path.home() / ".hermes" / "skills"


class SkillExecuteRequest(BaseModel):
    skill: str
    prompt: str
    context: Dict[str, Any] | None = None


def _list_skills() -> List[Dict[str, str]]:
    skills: List[Dict[str, str]] = []
    if not _SKILLS_DIR.exists():
        return skills
    for category_dir in sorted(_SKILLS_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        for skill_file in sorted(category_dir.glob("*/SKILL.md")):
            name = skill_file.parent.name
            desc = ""
            try:
                text = skill_file.read_text(errors="ignore")
                m = re.search(r"^description:\s*(.+)", text, re.MULTILINE)
                if m:
                    desc = m.group(1).strip().strip('"').strip("'")
            except Exception:
                pass
            skills.append({"name": name, "category": category_dir.name, "description": desc[:120]})
    return skills


@router.get("/")
async def list_skills() -> Dict[str, Any]:
    skills = _list_skills()
    return {"count": len(skills), "skills": skills}


@router.post("/execute")
async def execute_skill(body: SkillExecuteRequest) -> Dict[str, Any]:
    skill = body.skill.strip()
    if not skill:
        raise HTTPException(status_code=422, detail="skill is required")

    candidates = [s["name"] for s in _list_skills() if s["name"] == skill]
    if not candidates:
        raise HTTPException(status_code=404, detail=f"skill not found: {skill}")

    return {
        "skill": skill,
        "prompt": body.prompt,
        "context": body.context or {},
        "status": "dispatched",
        "note": "execution requires Hermes agent runtime; this endpoint returns dispatch confirmation only",
    }


@router.get("/{skill_name}")
async def get_skill(skill_name: str) -> Dict[str, Any]:
    for category_dir in _SKILLS_DIR.iterdir() if _SKILLS_DIR.exists() else []:
        skill_file = category_dir / skill_name / "SKILL.md"
        if skill_file.exists():
            text = skill_file.read_text(errors="ignore")
            desc = ""
            m = re.search(r"^description:\s*(.+)", text, re.MULTILINE)
            if m:
                desc = m.group(1).strip().strip('"').strip("'")
            return {"name": skill_name, "category": category_dir.name, "description": desc, "content": text[:4000]}
    raise HTTPException(status_code=404, detail=f"skill not found: {skill_name}")
