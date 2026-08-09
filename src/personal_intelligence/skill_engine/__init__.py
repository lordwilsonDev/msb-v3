from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class SkillFile:
    name: str
    path: Path
    frontmatter: Dict[str, str]
    body: str

    @property
    def triggers(self) -> List[str]:
        raw = self.frontmatter.get("triggers", "")
        return [t.strip() for t in raw.split(",") if t.strip()]

    @property
    def category(self) -> Optional[str]:
        return self.frontmatter.get("category") or None


class SkillEngine:
    def __init__(self) -> None:
        self.skills: Dict[str, SkillFile] = {}

    def register(self, skill: SkillFile) -> None:
        self.skills[skill.name] = skill

    def match(self, text: str) -> List[SkillFile]:
        lower = text.lower()
        hits: List[Tuple[int, SkillFile]] = []
        for skill in self.skills.values():
            score = sum(1 for trigger in skill.triggers if trigger in lower)
            if score > 0:
                hits.append((score, skill))
        hits.sort(key=lambda item: item[0], reverse=True)
        return [skill for _, skill in hits]

    def load_directory(self, directory: Path) -> None:
        for path in directory.glob("**/SKILL.md"):
            text = path.read_text(encoding="utf-8")
            frontmatter, body = self._split_frontmatter(text)
            skill = SkillFile(name=path.parent.name, path=path, frontmatter=frontmatter, body=body)
            self.register(skill)

    @staticmethod
    def _split_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
        if not text.startswith("---"):
            return {}, text
        end = text.find("---", 3)
        if end == -1:
            return {}, text
        fm_block = text[3:end]
        body = text[end + 3 :]
        parsed: Dict[str, str] = {}
        for line in fm_block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parsed[key.strip()] = value.strip()
        return parsed, body

    @staticmethod
    def _parse_fragment(name: str, path: Path, text: str) -> SkillFile:
        frontmatter, body = SkillEngine._split_frontmatter(text)
        return SkillFile(name=name, path=path, frontmatter=frontmatter, body=body)
