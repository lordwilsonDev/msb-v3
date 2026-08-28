"""SkillRegistry — the pluggable skill discovery and management layer.

Blueprint §5, §7, §11:
    Skills become interchangeable capability modules.

    The router doesn't care who created the skill.
    It only asks: What capability does this skill provide?
    What requirements does it have?  What risk does it carry?
    What worker can execute it?

The SkillRegistry discovers, registers, and queries skills.  Skills are
plugins to the Meta-System — Google skills, local skills, custom skills,
MCP skills — all register through the same contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RegisteredSkill:
    """A registered skill in the Meta-System's skill registry."""

    skill_id: str
    display_name: str = ""
    provider: str = ""  # "google" | "local" | "mcp" | "custom"
    capabilities: List[str] = field(default_factory=list)
    negative_capabilities: List[str] = field(default_factory=list)
    risk_tier: int = 1  # 1=low, 2=medium, 3=high, 4=critical
    requirements: List[str] = field(default_factory=list)  # e.g. ["explicit_authorization"]
    platforms: List[str] = field(default_factory=list)  # e.g. ["macos", "linux"]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def provides(self, capability: str) -> bool:
        """True if this skill declares the given capability."""
        return capability in self.capabilities

    def blocks(self, capability: str) -> bool:
        """True if this skill explicitly rejects the given capability."""
        return capability in self.negative_capabilities


class SkillRegistry:
    """Discovers, registers, and queries pluggable skills.

    Skills are discovered from:
        1. Explicit registration (Google skills, MCP skills)
        2. Filesystem scanning (~/.agents/skills/)
        3. Project-local skill manifests

    Usage::

        registry = SkillRegistry()
        registry.register(RegisteredSkill(
            skill_id="google.gcloud",
            capabilities=["cloud_cli", "infrastructure"],
            negative_capabilities=["local_msb_runtime", "speech_audio"],
        ))

        candidates = registry.match(capabilities=["cloud_cli"])
    """

    def __init__(self) -> None:
        self._skills: Dict[str, RegisteredSkill] = {}

    def register(self, skill: RegisteredSkill) -> None:
        """Register a skill.  Overwrites if skill_id already exists."""
        self._skills[skill.skill_id] = skill
        logger.debug("registered skill: %s", skill.skill_id)

    def unregister(self, skill_id: str) -> bool:
        """Remove a skill.  Returns True if it existed."""
        if skill_id in self._skills:
            del self._skills[skill_id]
            return True
        return False

    def get(self, skill_id: str) -> Optional[RegisteredSkill]:
        """Look up a skill by id."""
        return self._skills.get(skill_id)

    def list_all(self) -> List[RegisteredSkill]:
        """List all registered skills."""
        return list(self._skills.values())

    def list_by_provider(self, provider: str) -> List[RegisteredSkill]:
        """List skills from a specific provider."""
        return [s for s in self._skills.values() if s.provider == provider]

    def match(
        self,
        *,
        capabilities: Optional[List[str]] = None,
        negative_filter: Optional[List[str]] = None,
        max_risk_tier: int = 4,
        platforms: Optional[List[str]] = None,
    ) -> List[RegisteredSkill]:
        """Find skills matching the given criteria.

        A skill matches if:
            - it provides ALL requested capabilities
            - it does NOT provide any negative_filter capabilities
            - its risk_tier <= max_risk_tier
            - if platforms specified, it supports at least one platform
        """
        results: List[RegisteredSkill] = []
        cap_set = set(capabilities) if capabilities else set()
        neg_set = set(negative_filter) if negative_filter else set()
        plat_set = set(platforms) if platforms else set()

        for skill in self._skills.values():
            # Capability check.
            if cap_set and not cap_set.issubset(set(skill.capabilities)):
                continue
            # Negative filter.
            if neg_set and neg_set.intersection(set(skill.capabilities)):
                continue
            # Risk tier.
            if skill.risk_tier > max_risk_tier:
                continue
            # Platform.
            if plat_set and not plat_set.intersection(set(skill.platforms or [])):
                continue
            results.append(skill)

        return results

    def discover_from_directory(self, path: str) -> int:
        """Scan a directory for skill manifests and register them.

        Returns the number of skills discovered.
        """
        from pathlib import Path

        count = 0
        base = Path(path)
        if not base.is_dir():
            return 0

        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            manifest = skill_dir / "SKILL.md"
            if not manifest.exists():
                continue

            skill_id = skill_dir.name
            content = manifest.read_text(encoding="utf-8")[:2000]

            # Parse minimal metadata from SKILL.md frontmatter.
            skill = RegisteredSkill(
                skill_id=skill_id,
                display_name=skill_id.replace("-", " ").replace("_", " ").title(),
                provider="discovered",
                capabilities=self._extract_capabilities(content),
                negative_capabilities=self._extract_negative(content),
                metadata={"source_path": str(manifest)},
            )
            self.register(skill)
            count += 1

        return count

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _extract_capabilities(text: str) -> List[str]:
        """Extract capability hints from SKILL.md content."""
        caps: List[str] = []
        lower = text.lower()
        keywords = [
            "research", "cloud", "deployment", "infrastructure",
            "api", "documentation", "testing", "evaluation",
            "cli", "console", "agent", "model", "inference",
            "code", "python", "javascript", "typescript",
            "speech", "audio", "voice", "tts",
            "search", "query", "chat", "memory",
        ]
        for kw in keywords:
            if kw in lower:
                caps.append(kw)
        return caps

    @staticmethod
    def _extract_negative(text: str) -> List[str]:
        """Extract negative capability hints from SKILL.md content."""
        negatives: List[str] = []
        lower = text.lower()
        if "not for" in lower or "do not use" in lower:
            # Extract phrases after "not for" / "do not use".
            for marker in ["not for", "do not use"]:
                idx = lower.find(marker)
                if idx >= 0:
                    snippet = lower[idx:idx + 200]
                    if "local" in snippet or "msb" in snippet:
                        negatives.append("local_msb_runtime")
                    if "speech" in snippet or "audio" in snippet:
                        negatives.append("speech_audio")
        return negatives
