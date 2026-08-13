"""Guardian Protocol: scanner, SBOM registry, least privilege, poison pill."""
from __future__ import annotations

import ast
import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

_RUNTIME_ROOT = Path(settings.db_path).parent / "triumvirate"
_SBOM_FILE = _RUNTIME_ROOT / "sbom_registry.json"
_POISON_PILL_FILE = _RUNTIME_ROOT / "poison_pill.json"
_LEAST_PRIVILEGE_ROLES: Dict[str, List[str]] = {
    "sub-agent": ["read", "execute"],
    "mesh-peer": ["read"],
    "human-operator": ["*"],
    # "unknown" is intentionally absent: any token that doesn't match a
    # known role or the operator secret gets no permissions (deny-by-default),
    # not the "*" wildcard human-operator used to fall through to.
}

_DANGEROUS_BUILTINS: Dict[str, str] = {
    "eval": "eval usage",
    "exec": "exec usage",
    "__import__": "dynamic import",
}
_DANGEROUS_MODULE_CALLS: Dict[Tuple[str, str], str] = {
    ("os", "system"): "direct shell execution",
    ("os", "popen"): "direct shell execution",
    ("subprocess", "run"): "subprocess execution",
    ("subprocess", "call"): "subprocess execution",
    ("subprocess", "check_call"): "subprocess execution",
    ("subprocess", "check_output"): "subprocess execution",
    ("subprocess", "Popen"): "subprocess execution",
    ("importlib", "import_module"): "dynamic import",
}
_DANGEROUS_PATH_RE = re.compile(r"rm\s+-rf\s+/")

_REGEX_FALLBACK_PATTERNS = [
    (r"os\.system\(", "direct shell execution"),
    (r"subprocess\.", "subprocess execution"),
    (r"eval\(", "eval usage"),
    (r"exec\(", "exec usage"),
    (r"__import__\(", "dynamic import"),
    (r"rm\s+-rf\s+/", "dangerous filesystem path"),
]


@dataclass
class ScanReport:
    risk: str
    findings: List[str]
    blocked: bool = False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class _ImportAliases(ast.NodeVisitor):
    """Resolves `import x as y` / `from x import y as z` to real dotted names,
    so the scanner isn't defeated by a simple rename."""

    def __init__(self) -> None:
        self.modules: Dict[str, str] = {}  # local name -> module, e.g. "o" -> "os"
        self.members: Dict[str, Tuple[str, str]] = {}  # local name -> (module, attr)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.modules[local] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            local = alias.asname or alias.name
            self.members[local] = (module, alias.name)


def _resolve_call_target(func: ast.expr, aliases: _ImportAliases) -> Optional[Tuple[Optional[str], str]]:
    """Best-effort resolution of a Call's target into (module, attr), following
    import aliases and a plain `getattr(module, "attr")(...)` obfuscation."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module = aliases.modules.get(func.value.id, func.value.id)
        return module, func.attr
    if isinstance(func, ast.Name):
        if func.id in aliases.members:
            return aliases.members[func.id]
        return None, func.id  # candidate builtin, resolved by caller
    if isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and func.func.id == "getattr":
        args = func.args
        if len(args) >= 2 and isinstance(args[0], ast.Name) and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
            module = aliases.modules.get(args[0].id, args[0].id)
            return module, args[1].value
    return None


def _check_dangerous_path_arg(arg: ast.expr, flag) -> None:
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        if _DANGEROUS_PATH_RE.search(arg.value):
            flag("dangerous filesystem path")
    elif isinstance(arg, (ast.List, ast.Tuple)):
        parts = [el.value for el in arg.elts if isinstance(el, ast.Constant) and isinstance(el.value, str)]
        if _DANGEROUS_PATH_RE.search(" ".join(parts)):
            flag("dangerous filesystem path")


def _scan_ast(script: str) -> Optional[List[str]]:
    """AST-based scan. Returns None if `script` doesn't parse as Python, so
    the caller can fall back to the regex heuristic for non-Python scripts."""
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return None

    aliases = _ImportAliases()
    aliases.visit(tree)

    findings: List[str] = []

    def _flag(label: str) -> None:
        if label not in findings:
            findings.append(label)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _resolve_call_target(node.func, aliases)
            if target is not None:
                module, attr = target
                if module is None and attr in _DANGEROUS_BUILTINS:
                    _flag(_DANGEROUS_BUILTINS[attr])
                elif module is not None and (module, attr) in _DANGEROUS_MODULE_CALLS:
                    _flag(_DANGEROUS_MODULE_CALLS[(module, attr)])
            for arg in node.args:
                _check_dangerous_path_arg(arg, _flag)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _DANGEROUS_PATH_RE.search(node.value):
                _flag("dangerous filesystem path")

    return findings


def _scan_regex(script: str) -> List[str]:
    """Substring/regex fallback for scripts that aren't valid Python (e.g.
    shell scripts) — the same heuristic the scanner used before, kept only
    for inputs the AST path can't parse."""
    findings: List[str] = []
    for pattern, label in _REGEX_FALLBACK_PATTERNS:
        if re.search(pattern, script) and label not in findings:
            findings.append(label)
    return findings


class GuardianScanner:
    def scan_script(self, script: str) -> ScanReport:
        findings = _scan_ast(script)
        if findings is None:
            findings = _scan_regex(script)
        blocked = len(findings) > 0
        risk = "HIGH" if blocked else "LOW"
        return ScanReport(risk=risk, findings=findings, blocked=blocked)

    def verify_sbom(self, mcp_server_id: str, executable_path: Optional[str] = None) -> bool:
        registry = _load_sbom()
        entry = registry.get(mcp_server_id)
        if entry is None:
            return False
        if executable_path:
            actual = _sha256(Path(executable_path))
            return actual == entry.get("sha256")
        return True

    def enforce_least_privilege(self, agent_token: str, required_scope: str) -> bool:
        if _is_locked_down():
            # Poison pill has been detonated: deny every scope for every
            # role until the system is explicitly re-armed.
            return False
        role = _role_for_token(agent_token)
        allowed = _LEAST_PRIVILEGE_ROLES.get(role, [])
        if "*" in allowed or required_scope in allowed:
            return True
        return False


class SBOMRegistry:
    def register(self, mcp_server_id: str, executable_path: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        path = Path(executable_path)
        if not path.exists():
            raise FileNotFoundError(executable_path)
        registry = _load_sbom()
        registry[mcp_server_id] = {
            "sha256": _sha256(path),
            "path": str(path),
            "metadata": metadata or {},
        }
        _write_sbom(registry)
        return registry[mcp_server_id]

    def trusted(self, mcp_server_id: str) -> bool:
        return mcp_server_id in _load_sbom()


class PoisonPill:
    def arm(self) -> Dict[str, Any]:
        payload = {
            "armed": True,
            "locked_down": False,
            "armed_at": _now_iso(),
            "proof": hashlib.sha256(_now_iso().encode()).hexdigest()[:16],
        }
        _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        _POISON_PILL_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return payload

    def detonate(self) -> Dict[str, Any]:
        """Actually lock the system down, rather than just recording intent.

        - revoke_sub_agent_tokens / disable_gateway: enforced by
          `enforce_least_privilege` consulting `_is_locked_down()` and
          denying every scope for every role while locked down.
        - pause_missions: done for real via MissionAnchor's existing
          circuit breaker, which flips current_phase to "paused".
        """
        actions_completed: List[str] = []
        errors: Dict[str, str] = {}

        payload = {
            "armed": False,
            "locked_down": True,
            "detonated_at": _now_iso(),
            "actions": ["revoke_sub_agent_tokens", "disable_gateway", "pause_missions"],
        }
        _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        # Write the lockdown flag first and independently of anything else,
        # so enforce_least_privilege starts denying immediately even if the
        # mission-pause step below fails.
        _POISON_PILL_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        actions_completed.append("revoke_sub_agent_tokens")
        actions_completed.append("disable_gateway")

        try:
            from msb_v3.triumvirate.mission_anchor import MissionAnchor

            MissionAnchor().circuit_breaker_trigger()
            actions_completed.append("pause_missions")
        except Exception as exc:
            errors["pause_missions"] = str(exc)

        payload["actions_completed"] = actions_completed
        if errors:
            payload["errors"] = errors
        return payload


def _load_sbom() -> Dict[str, Any]:
    if not _SBOM_FILE.exists():
        return {}
    try:
        return json.loads(_SBOM_FILE.read_text())
    except Exception:
        logger.debug(
            "sbom registry unreadable (corrupt or partial write); treating as empty",
            exc_info=True,
        )
        return {}


def _write_sbom(registry: Dict[str, Any]) -> None:
    _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    _SBOM_FILE.write_text(json.dumps(registry, ensure_ascii=False, indent=2))


def _role_for_token(agent_token: str) -> str:
    if agent_token.startswith("sub-"):
        return "sub-agent"
    if agent_token.startswith("mesh-"):
        return "mesh-peer"
    operator_secret = settings.operator_token
    if operator_secret and hmac.compare_digest(agent_token, operator_secret):
        return "human-operator"
    # Deny by default: an unrecognized token that isn't a verified match
    # against MSB_OPERATOR_TOKEN gets no role (and therefore no scopes),
    # instead of silently falling through to full "*" access.
    return "unknown"


def _is_locked_down() -> bool:
    if not _POISON_PILL_FILE.exists():
        return False
    try:
        data = json.loads(_POISON_PILL_FILE.read_text())
    except Exception:
        logger.debug(
            "poison-pill state unreadable; treating as not locked down",
            exc_info=True,
        )
        return False
    return bool(data.get("locked_down"))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
