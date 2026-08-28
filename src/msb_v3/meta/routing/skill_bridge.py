"""SkillBridge — bridges installed skills into the WorkerRegistry.

Blueprint §7, §11, §12:
    Google skills become plug-in capability providers, not architectural
    dependencies.  The router doesn't care who created the skill.

    SkillBridge discovers skills from a directory (e.g. ~/.agents/skills/),
    reads their SKILL.md metadata, and registers each as a RegisteredWorker
    in the WorkerRegistry.  This makes every skill interchangeable with
    every other worker through the same contract.

Usage::

    bridge = SkillBridge()
    bridge.discover_and_register(
        registry=worker_registry,
        skills_dir="~/.agents/skills",
    )
    # or register known Google skills explicitly:
    bridge.register_google_skills(worker_registry)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from msb_v3.meta.routing.worker_registry import RegisteredWorker, WorkerRegistry

logger = logging.getLogger(__name__)


# ── Explicit Google skill definitions ────────────────────────────────────

_GOOGLE_SKILLS: List[Dict[str, Any]] = [
    {
        "worker_id": "google.gcloud",
        "display_name": "gcloud CLI",
        "kind": "skill",
        "model_id": "gcloud",
        "capabilities": [
            "cloud_cli",
            "infrastructure",
            "deployment",
            "gcp",
            "google_cloud",
        ],
        "negative_capabilities": [
            "local_msb_runtime",
            "speech_audio",
            "macos_launchd",
        ],
        "max_risk_tier": 3,
        "preferred_task_types": ["deployment", "infrastructure", "cloud"],
        "metadata": {
            "source": "google/skills",
            "requires": ["explicit_authorization"],
            "platforms": ["linux", "macos"],
        },
    },
    {
        "worker_id": "google.gemini-api",
        "display_name": "Gemini API",
        "kind": "skill",
        "model_id": "gemini",
        "capabilities": [
            "ai",
            "ml",
            "inference",
            "multimodal",
            "api",
            "google_cloud",
            "vertex_ai",
        ],
        "negative_capabilities": [
            "local_msb_runtime",
            "macos_launchd",
        ],
        "max_risk_tier": 2,
        "preferred_task_types": ["inference", "research", "api_integration"],
        "metadata": {
            "source": "google/skills",
            "requires": ["google_cloud_credentials"],
            "platforms": ["linux", "macos"],
        },
    },
    {
        "worker_id": "google.agent-platform-inference",
        "display_name": "Agent Platform Inference",
        "kind": "skill",
        "model_id": "agent-platform",
        "capabilities": [
            "ai",
            "inference",
            "agent",
            "model",
            "google_cloud",
        ],
        "negative_capabilities": [
            "local_msb_runtime",
            "macos_launchd",
        ],
        "max_risk_tier": 2,
        "preferred_task_types": ["inference", "agent_deployment"],
        "metadata": {
            "source": "google/skills",
            "requires": ["google_cloud_credentials"],
            "platforms": ["linux", "macos"],
        },
    },
    {
        "worker_id": "google.agent-platform-eval-flywheel",
        "display_name": "Agent Platform Eval Flywheel",
        "kind": "skill",
        "model_id": "eval-flywheel",
        "capabilities": [
            "evaluation",
            "testing",
            "benchmarking",
            "ai",
            "google_cloud",
        ],
        "negative_capabilities": [
            "local_msb_runtime",
            "macos_launchd",
        ],
        "max_risk_tier": 2,
        "preferred_task_types": ["evaluation", "testing", "benchmarking"],
        "metadata": {
            "source": "google/skills",
            "requires": ["google_cloud_credentials"],
            "platforms": ["linux", "macos"],
        },
    },
    {
        "worker_id": "google.google-agents-cli-onboarding",
        "display_name": "Google Agents CLI Onboarding",
        "kind": "skill",
        "model_id": "agents-cli",
        "capabilities": [
            "cli",
            "agent",
            "onboarding",
            "google_cloud",
        ],
        "negative_capabilities": [
            "local_msb_runtime",
            "macos_launchd",
        ],
        "max_risk_tier": 2,
        "preferred_task_types": ["onboarding", "setup", "cli_operations"],
        "metadata": {
            "source": "google/skills",
            "requires": ["google_cloud_credentials"],
            "platforms": ["linux", "macos"],
        },
    },
    {
        "worker_id": "google.retrieving-developer-knowledge",
        "display_name": "Google Developer Knowledge",
        "kind": "skill",
        "model_id": "dev-knowledge",
        "capabilities": [
            "research",
            "documentation",
            "search",
            "api",
            "google_cloud",
        ],
        "negative_capabilities": [
            "local_msb_runtime",
            "macos_launchd",
        ],
        "max_risk_tier": 1,
        "preferred_task_types": ["research", "documentation", "api_lookup"],
        "metadata": {
            "source": "google/skills",
            "requires": [],
            "platforms": ["linux", "macos"],
        },
    },
]


class SkillBridge:
    """Bridges installed skills into the WorkerRegistry.

    Two discovery modes:

    1. **Explicit** — ``register_google_skills()`` registers the known
       Google skill set with curated capabilities, risk tiers, and
       negative capabilities.

    2. **Filesystem** — ``discover_and_register()`` scans a skills
       directory, reads SKILL.md frontmatter, and registers each
       discovered skill as a worker with inferred capabilities.

    Both modes produce ``RegisteredWorker`` instances that compete
    through the same contract as local Qwen/DeepSeek/Claude workers.
    """

    def __init__(self) -> None:
        self._registered: List[str] = []

    # ── Explicit registration ────────────────────────────────────────

    def register_google_skills(
        self,
        registry: WorkerRegistry,
    ) -> int:
        """Register the 6 known Google skills as workers.

        Returns the number of workers registered.
        """
        count = 0
        for skill_def in _GOOGLE_SKILLS:
            worker = RegisteredWorker(**skill_def)
            registry.register(worker)
            self._registered.append(worker.worker_id)
            count += 1
            logger.info(
                "registered google skill worker: %s (%s)",
                worker.worker_id,
                worker.display_name,
            )
        return count

    # ── Filesystem discovery ─────────────────────────────────────────

    def discover_and_register(
        self,
        registry: WorkerRegistry,
        skills_dir: str = "~/.agents/skills",
        *,
        provider_prefix: str = "discovered",
    ) -> int:
        """Scan *skills_dir* for SKILL.md files and register as workers.

        Returns the number of workers registered.
        """
        base = Path(os.path.expanduser(skills_dir))
        if not base.is_dir():
            logger.warning("skills directory not found: %s", base)
            return 0

        count = 0
        for skill_dir in sorted(base.iterdir()):
            if not skill_dir.is_dir():
                continue
            manifest = skill_dir / "SKILL.md"
            if not manifest.exists():
                continue

            content = manifest.read_text(encoding="utf-8")[:3000]
            skill_id = skill_dir.name
            meta = self._parse_frontmatter(content)

            # Skip if already registered explicitly.
            worker_id = f"{provider_prefix}.{skill_id}"
            if registry.get(worker_id) is not None:
                continue

            capabilities = self._infer_capabilities(content, meta)
            negative = self._infer_negative(content)

            worker = RegisteredWorker(
                worker_id=worker_id,
                display_name=meta.get("name", skill_id).replace("-", " ").title(),
                kind="skill",
                model_id=skill_id,
                capabilities=capabilities,
                negative_capabilities=negative,
                max_risk_tier=2,
                metadata={
                    "source_path": str(manifest),
                    "source": skills_dir,
                    "category": meta.get("category", ""),
                },
            )
            registry.register(worker)
            self._registered.append(worker_id)
            count += 1

        logger.info(
            "discovered %d skills from %s", count, skills_dir
        )
        return count

    @property
    def registered_ids(self) -> List[str]:
        """Worker IDs registered by this bridge instance."""
        return list(self._registered)

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_frontmatter(text: str) -> Dict[str, str]:
        """Extract minimal YAML-like frontmatter from SKILL.md."""
        result: Dict[str, str] = {}
        if not text.startswith("---"):
            return result
        end = text.find("---", 3)
        if end < 0:
            return result
        for line in text[3:end].strip().splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and val:
                    result[key] = val
        return result

    @staticmethod
    def _infer_capabilities(text: str, meta: Dict[str, str]) -> List[str]:
        """Infer capabilities from SKILL.md content and metadata."""
        caps: List[str] = []
        lower = text.lower()

        # From category metadata.
        category = meta.get("category", "").lower()
        cat_map = {
            "cloudinfrastructureandservices": ["cloud", "infrastructure", "deployment"],
            "aiandmachinelearning": ["ai", "ml", "inference"],
            "developmenttools": ["cli", "code"],
        }
        for cat_key, cat_caps in cat_map.items():
            if cat_key in category:
                caps.extend(cat_caps)

        # From content keywords.
        keyword_caps = {
            "research": "research",
            "documentation": "documentation",
            "search": "search",
            "cloud": "cloud",
            "infrastructure": "infrastructure",
            "deployment": "deployment",
            "api": "api",
            "inference": "inference",
            "evaluation": "evaluation",
            "testing": "testing",
            "cli": "cli",
            "agent": "agent",
            "model": "model",
            "vertex ai": "vertex_ai",
            "google cloud": "google_cloud",
            "gcp": "gcp",
        }
        for kw, cap in keyword_caps.items():
            if kw in lower and cap not in caps:
                caps.append(cap)

        return caps

    @staticmethod
    def _infer_negative(text: str) -> List[str]:
        """Infer negative capabilities from SKILL.md content."""
        negatives: List[str] = []
        lower = text.lower()

        # Google skills should not handle local MSB work.
        if "not for" in lower or "do not use" in lower:
            for marker in ["not for", "do not use"]:
                idx = lower.find(marker)
                if idx >= 0:
                    snippet = lower[idx : idx + 300]
                    if "local" in snippet or "msb" in snippet:
                        negatives.append("local_msb_runtime")
                    if "speech" in snippet or "audio" in snippet:
                        negatives.append("speech_audio")
                    if "launchd" in snippet or "macos service" in snippet:
                        negatives.append("macos_launchd")

        # All external skills should not handle local runtime.
        if "google" in lower and "local_msb_runtime" not in negatives:
            negatives.append("local_msb_runtime")

        return negatives
