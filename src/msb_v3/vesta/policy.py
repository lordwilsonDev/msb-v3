"""Deterministic Vesta policy; MSB/model output never grants authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from msb_v3.vesta.models import ABind

ALLOWED_CHAT_CAPABILITIES = frozenset({"model.inference", "memory.read"})
ALLOWED_READ_CAPABILITIES = frozenset({"filesystem.read"})
SHELL_CAPABILITIES = frozenset({"shell.exec"})
ALLOWED_SHELL_COMMANDS = frozenset({"echo", "pwd"})
KNOWN_CAPABILITIES = frozenset(
    {
        "model.inference",
        "memory.read",
        "memory.write",
        "sensor.read",
        "evidence.create",
        "ledger.append",
        "filesystem.read",
        "filesystem.write",
        "shell.exec",
        "network.none",
        "network.allowlist",
        "mcp.invoke",
        "human.request_ack",
        "external.message",
        "external.call",
    }
)


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    risk_class: str
    capabilities: tuple[str, ...]
    reasons: tuple[str, ...]
    policy_version: str

    def as_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "risk_class": self.risk_class,
            "capabilities": list(self.capabilities),
            "reasons": list(self.reasons),
            "policy_version": self.policy_version,
        }


def authorize_chat(bind: ABind) -> PolicyDecision:
    """Authorize only the read/inference surface in the first Vesta slice."""
    requested = set(bind.capabilities)
    unknown = sorted(requested - KNOWN_CAPABILITIES)
    if unknown:
        return PolicyDecision(
            "DENY",
            "critical",
            bind.capabilities,
            (f"unknown capabilities: {', '.join(unknown)}",),
            bind.policy_version,
        )
    if not requested.issubset(ALLOWED_CHAT_CAPABILITIES):
        blocked = sorted(requested - ALLOWED_CHAT_CAPABILITIES)
        return PolicyDecision(
            "DENY",
            "high",
            bind.capabilities,
            (f"capabilities not enabled in Phase 0–2: {', '.join(blocked)}",),
            bind.policy_version,
        )
    if bind.expired():
        return PolicyDecision(
            "DENY",
            "high",
            bind.capabilities,
            ("A-BIND deadline has expired",),
            bind.policy_version,
        )
    return PolicyDecision(
        "ALLOW",
        "normal",
        bind.capabilities,
        ("chat capabilities are within the Phase 0–2 policy",),
        bind.policy_version,
    )


def authorize_file_read(bind: ABind) -> PolicyDecision:
    """Authorize only the scoped read-only filesystem capability."""
    requested = set(bind.capabilities)
    unknown = sorted(requested - KNOWN_CAPABILITIES)
    if unknown:
        return PolicyDecision(
            "DENY",
            "critical",
            bind.capabilities,
            (f"unknown capabilities: {', '.join(unknown)}",),
            bind.policy_version,
        )
    if not requested.issubset(ALLOWED_READ_CAPABILITIES):
        blocked = sorted(requested - ALLOWED_READ_CAPABILITIES)
        return PolicyDecision(
            "DENY",
            "high",
            bind.capabilities,
            (f"capabilities not enabled for FILE_READ: {', '.join(blocked)}",),
            bind.policy_version,
        )
    if bind.expired():
        return PolicyDecision(
            "DENY",
            "high",
            bind.capabilities,
            ("A-BIND deadline has expired",),
            bind.policy_version,
        )
    return PolicyDecision(
        "ALLOW",
        "low",
        bind.capabilities,
        ("scoped read-only filesystem access is enabled",),
        bind.policy_version,
    )


def authorize_shell(
    executable: str,
    args: List[str],
    expected_stdout: str | None,
    bind: ABind,
) -> PolicyDecision:
    """Return REQUIRE_APPROVAL only for the tiny non-shell command allowlist."""
    requested = set(bind.capabilities)
    if requested != SHELL_CAPABILITIES:
        return PolicyDecision(
            "DENY",
            "critical",
            bind.capabilities,
            ("SHELL_EXEC requires the exact shell.exec capability",),
            bind.policy_version,
        )
    if executable not in ALLOWED_SHELL_COMMANDS:
        return PolicyDecision(
            "DENY",
            "critical",
            bind.capabilities,
            (f"executable is not allowlisted: {executable}",),
            bind.policy_version,
        )
    if any("\\x00" in arg or len(arg) > 1024 for arg in args) or sum(len(arg) for arg in args) > 8192:
        return PolicyDecision(
            "DENY",
            "high",
            bind.capabilities,
            ("shell arguments exceed the bounded contract",),
            bind.policy_version,
        )
    if len(args) > 32:
        return PolicyDecision(
            "DENY",
            "high",
            bind.capabilities,
            ("too many shell arguments",),
            bind.policy_version,
        )
    if executable == "pwd" and args:
        return PolicyDecision(
            "DENY",
            "high",
            bind.capabilities,
            ("pwd accepts no arguments",),
            bind.policy_version,
        )
    if executable == "echo" and any(arg.startswith("-") for arg in args):
        return PolicyDecision(
            "DENY",
            "high",
            bind.capabilities,
            ("echo flags are not permitted",),
            bind.policy_version,
        )
    if expected_stdout is not None and len(expected_stdout) > 65536:
        return PolicyDecision(
            "DENY",
            "high",
            bind.capabilities,
            ("expected output exceeds the bounded contract",),
            bind.policy_version,
        )
    if bind.expired():
        return PolicyDecision(
            "DENY",
            "high",
            bind.capabilities,
            ("A-BIND deadline has expired",),
            bind.policy_version,
        )
    return PolicyDecision(
        "REQUIRE_APPROVAL",
        "high",
        bind.capabilities,
        ("shell execution requires exact owner approval",),
        bind.policy_version,
    )


def capability_catalog() -> List[dict[str, object]]:
    return [
        {
            "capability": name,
            "known": True,
            "enabled": name in (ALLOWED_CHAT_CAPABILITIES | ALLOWED_READ_CAPABILITIES),
            "phase": "0-2" if name in ALLOWED_CHAT_CAPABILITIES else ("G" if name in ALLOWED_READ_CAPABILITIES else "deferred"),
        }
        for name in sorted(KNOWN_CAPABILITIES)
    ]
