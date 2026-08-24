"""Configuration ingestion — env vars, docker, settings.

Reads .env.example, pyproject.toml, docker-compose, and launchd plists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from msb_v3.plei.provenance import Provenance, Provenanced


@dataclass(slots=True)
class ConfigurationFacts:
    """Configuration-derived facts."""

    env_count: Provenanced = field(default_factory=Provenanced.unknown)
    key_env_vars: Provenanced = field(default_factory=Provenanced.unknown)
    has_docker: Provenanced = field(default_factory=Provenanced.unknown)
    has_ci: Provenanced = field(default_factory=Provenanced.unknown)
    launch_agents: Provenanced = field(default_factory=Provenanced.unknown)
    settings_class: Provenanced = field(default_factory=Provenanced.unknown)


def _read_or_none(path: Path) -> str | None:
    try:
        return path.read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return None


def ingest_configuration(project_root: str | Path) -> ConfigurationFacts:
    """Ingest configuration from the project tree."""
    root = Path(project_root).resolve()
    facts = ConfigurationFacts()
    source_tag = f"ingestion/configuration ({root.name})"

    # .env.example
    env_example = _read_or_none(root / ".env.example")
    if env_example:
        lines = [line for line in env_example.split("\n") if line.strip() and not line.strip().startswith("#")]
        facts.env_count = Provenanced.observed(len(lines), f"{root / '.env.example'}")
        # Key vars — ones used by msb-v3 itself (not Open WebUI / Tencent COS externals)
        msb_vars = [line.split("=")[0].strip() for line in lines if "=" in line and "MSB_" in line.upper()]
        facts.key_env_vars = Provenanced.observed(msb_vars[:30], "MSB_* vars from .env.example")

    # Docker
    facts.has_docker = Provenanced.observed(
        (root / "docker-compose.yml").is_file() or (root / "Dockerfile").is_file(),
        source_tag,
    )

    # CI
    ci_dir = root / ".github" / "workflows"
    ci_files = list(ci_dir.glob("*.yml")) if ci_dir.is_dir() else []
    facts.has_ci = Provenanced.observed(len(ci_files) > 0, source_tag)

    # Launchd plists
    launch_dir = root / "scripts" / "launchd"
    plists = list(launch_dir.glob("*.plist")) if launch_dir.is_dir() else []
    agent_names = [p.stem.replace("com.lordwilson.", "") for p in plists]
    facts.launch_agents = Provenanced.observed(agent_names, f"{root / 'scripts/launchd/'}")

    # Settings class (try importing)
    try:
        from msb_v3.core.config import settings
        fields = list(settings.__class__.__dataclass_fields__.keys()) if hasattr(settings, '__dataclass_fields__') else []
        facts.settings_class = Provenanced.observed(fields[:30], "msb_v3.core.config.settings")
    except ImportError:
        facts.settings_class = Provenanced(value=None, provenance=Provenance.UNKNOWN, source="import failed")

    return facts


def config_facts_as_dict(facts: ConfigurationFacts) -> dict[str, Any]:
    return {k: getattr(facts, k).as_dict() for k in (
        "env_count", "key_env_vars", "has_docker", "has_ci",
        "launch_agents", "settings_class")}