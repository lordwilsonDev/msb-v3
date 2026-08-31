"""Load and resolve ``config/guardian.toml`` into a typed object.

Deliberately boring: no defaults invented at runtime beyond what the spec
fixes (doc 1 §10 — thresholds come from config, not the model).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def _expand(p: str) -> Path:
    return Path(p).expanduser()


@dataclass(frozen=True)
class FingerprintCfg:
    ignorable_globs: list[str] = field(default_factory=list)
    secret_paths: list[str] = field(default_factory=lambda: [".env"])


@dataclass(frozen=True)
class ResourcesCfg:
    disk_free_pct_min: float = 10.0
    load_1m_max: float = 16.0


@dataclass(frozen=True)
class ReasoningCfg:
    substrate: str = "claude_cli"
    claude_bin: str = "claude"
    model: str = ""
    max_retries: int = 1
    system_prompt_path: Path = field(default_factory=lambda: _expand("~"))


@dataclass(frozen=True)
class LedgerCfg:
    vault_dir: Path
    inbox_dir: Path
    local_state_dir: Path


@dataclass(frozen=True)
class GuardianConfig:
    mode: str
    timebox_seconds: int
    repo_path: Path
    fingerprint: FingerprintCfg
    resources: ResourcesCfg
    reasoning: ReasoningCfg
    ledger: LedgerCfg

    @staticmethod
    def load(path: str | Path) -> "GuardianConfig":
        raw = tomllib.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        g = raw.get("guardian", {})
        fp = raw.get("fingerprint", {})
        rs = raw.get("resources", {})
        rz = raw.get("reasoning", {})
        ld = raw.get("ledger", {})
        return GuardianConfig(
            mode=str(g.get("mode", "OBSERVE")),
            timebox_seconds=int(g.get("timebox_seconds", 300)),
            repo_path=_expand(str(g.get("repo_path", "."))),
            fingerprint=FingerprintCfg(
                ignorable_globs=list(fp.get("ignorable_globs", [])),
                secret_paths=list(fp.get("secret_paths", [".env"])),
            ),
            resources=ResourcesCfg(
                disk_free_pct_min=float(rs.get("disk_free_pct_min", 10.0)),
                load_1m_max=float(rs.get("load_1m_max", 16.0)),
            ),
            reasoning=ReasoningCfg(
                substrate=str(rz.get("substrate", "claude_cli")),
                claude_bin=str(rz.get("claude_bin", "claude")),
                model=str(rz.get("model", "")),
                max_retries=int(rz.get("max_retries", 1)),
                system_prompt_path=_expand(str(rz.get("system_prompt_path", "~"))),
            ),
            ledger=LedgerCfg(
                vault_dir=_expand(str(ld.get("vault_dir", "~"))),
                inbox_dir=_expand(str(ld.get("inbox_dir", "~"))),
                local_state_dir=_expand(str(ld.get("local_state_dir", "var/guardian"))),
            ),
        )
