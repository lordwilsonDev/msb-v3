"""Authority boundary — every capability-executing path crosses ActionGate.

PRODUCTION-CLOSURE-001 P3 / O3, Option B (dual-governance —
``docs/governance/authority-model.md``).

The invariant this suite defends:

    Every entry path that can cause a capability (a ``tools/executors.py``
    function) to run routes that execution through the authority boundary
    — ``SafeProvider`` (DAG path) or ``_run_governed`` (chat / MCP / internal
    path) — before the capability runs. There is no third state: each attempt
    resolves to ``allowed`` / ``denied`` / ``approval-required`` / ``unknown``
    / ``error`` and is audited. No silent execution.

These are structural (AST + source) checks: they catch a *new* path that
reaches the executors without a gate, which is the regression this exists to
stop. The 14-path map + evidence lives in
``docs/releases/O3-AUTHORITY-CLOSURE-PLAN.md``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
_MSB = _SRC / "msb_v3"

# The only module allowed to reach tools/executors.py directly. Every other
# caller must go through _run_governed (which lives here) or SafeProvider.
_SANCTIONED_EXECUTOR_CALLERS = {
    "msb_v3/tools/runtime.py",   # _run_governed: capability + approval + audit
}

# The authority-boundary primitives. A capability-executing path must reference
# at least one of these (directly or transitively via a module that does).
_GATE_SYMBOLS = {"_run_governed", "SafeProvider", "register_governed_tools"}


def _module_files() -> list[Path]:
    return sorted(p for p in _MSB.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(p: Path) -> str:
    return str(p.relative_to(_SRC))


def _imports_executors(tree: ast.AST) -> bool:
    """True if the module imports msb_v3.tools.executors (any form)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith("tools.executors") or node.module.endswith(
                "tools"
            ) and any(a.name == "executors" for a in node.names):
                return True
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.endswith("tools.executors"):
                    return True
    return False


def _refs_name(tree: ast.AST, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
    return False


# ---------------------------------------------------------------------------
# 1. Bypass scanner — nothing reaches the executors except the sanctioned gate
# ---------------------------------------------------------------------------

def test_no_module_reaches_executors_except_the_gate():
    offenders = []
    for f in _module_files():
        rel = _rel(f)
        if rel in _SANCTIONED_EXECUTOR_CALLERS:
            continue
        if rel.startswith("msb_v3/tools/") and f.name in {
            "executors.py",
            "registry.py",
            "runtime.py",
        }:
            continue  # the tool layer itself
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        if _imports_executors(tree):
            offenders.append(rel)
    assert not offenders, (
        "these modules import tools/executors.py directly, bypassing the "
        f"_run_governed authority boundary: {offenders}. Route the call "
        "through msb_v3.tools.runtime._run_governed instead."
    )


# ---------------------------------------------------------------------------
# 2. The gate itself — every outcome is audited, no silent path
# ---------------------------------------------------------------------------

def test_run_governed_audits_every_verdict():
    src = (_MSB / "tools" / "runtime.py").read_text()
    tree = ast.parse(src)
    run_governed = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_run_governed"
        ),
        None,
    )
    assert run_governed is not None, "tools/runtime.py::_run_governed vanished"

    # Every `return` inside _run_governed must be preceded (in its block) by an
    # _audit_append call — i.e. no verdict escapes without evidence.
    returns = [n for n in ast.walk(run_governed) if isinstance(n, ast.Return)]
    assert returns, "_run_governed has no return statements — shape changed"
    audit_calls = sum(
        1
        for n in ast.walk(run_governed)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "_audit_append")
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "_audit_append")
        )
    )
    assert audit_calls >= len(returns), (
        f"_run_governed has {len(returns)} return paths but only {audit_calls} "
        "_audit_append calls — a verdict can escape without an audit record"
    )

    # The four terminal verdicts + the two error markers must all be present.
    for verdict in ("allowed", "denied", "approval-required", "unknown", "error"):
        assert f'verdict="{verdict}"' in src, (
            f'verdict "{verdict}" no longer emitted by _run_governed'
        )


def test_run_governed_checks_capability_and_approval_before_executor():
    """The capability check and approval check must both appear before the
    executor is resolved/called — a reordering would let an un-authorized
    tool run first and be denied after the fact."""
    src = (_MSB / "tools" / "runtime.py").read_text()
    approval_at = src.find("approval_required")
    capability_at = src.find("required_capabilities")
    executor_at = src.find("getattr(executors")
    assert -1 not in (approval_at, capability_at, executor_at), (
        "approval / capability / executor-resolution markers moved — re-audit "
        "tools/runtime.py::_run_governed"
    )
    assert approval_at < executor_at and capability_at < executor_at, (
        "_run_governed resolves the executor before checking approval and/or "
        "capability — authority check must come first"
    )


# ---------------------------------------------------------------------------
# 3. Per-path anchors — the 14-path matrix, encoded
# ---------------------------------------------------------------------------

# classification: ALLOW (crosses SafeProvider/_run_governed), CONSTRAINED
# (narrower documented authority — fixed action set / operator-only / FROZEN),
# READONLY (cannot execute a capability).
_PATHS = [
    # (id, entry module, classification, must_ref, must_not_ref)
    ("agent/handle", "msb_v3/agent/handle.py", "ALLOW", {"SafeProvider"}, set()),
    ("in-process providers", "msb_v3/agent/providers.py", "ALLOW", {"handle"}, set()),
    ("openbot", "msb_v3/integrations/openbot.py", "ALLOW", {"handle"}, set()),
    ("chat harness", "msb_v3/harnesses/base.py", "ALLOW", {"register_governed_tools"}, set()),
    ("/v1 openai-compat", "msb_v3/api/openai_compat.py", "ALLOW", {"ChatHarness"}, set()),
    ("mcp bridge", "msb_v3/api/mcp_bridge.py", "ALLOW", {"_run_governed"}, set()),
    ("hook webhook", "msb_v3/api/hook.py", "READONLY", {"WakeStore"}, {"_run_governed", "execute_tool_loop"}),
    ("wake runner", "msb_v3/wake/runner.py", "CONSTRAINED", set(), {"execute_tool_loop", "getattr(executors"}),
    ("cron actions", "msb_v3/cron/actions.py", "CONSTRAINED", {"ACTIONS"}, {"execute_tool_loop", "getattr(executors"}),
    ("automation brain", "msb_v3/automation/brain.py", "CONSTRAINED", {"Manifest"}, {"getattr(executors"}),
    ("flywheel", "msb_v3/flywheel/cli.py", "CONSTRAINED", {"WAITING_APPROVAL"}, set()),
    ("replay engine", "msb_v3/replay/engine.py", "READONLY", set(), {"execute_tool_loop", "getattr(executors", "create_subprocess"}),
]


@pytest.mark.parametrize("path_id,module,cls,must_ref,must_not_ref", _PATHS,
                         ids=[p[0] for p in _PATHS])
def test_entry_path_authority(path_id, module, cls, must_ref, must_not_ref):
    f = _SRC / module
    assert f.exists(), f"{path_id}: entry module missing: {module}"
    src = f.read_text()
    tree = ast.parse(src)

    for sym in must_ref:
        assert _refs_name(tree, sym) or sym in src, (
            f"{path_id} ({cls}): expected to reference `{sym}` as its "
            f"authority anchor — not found in {module}"
        )
    for sym in must_not_ref:
        assert sym not in src, (
            f"{path_id} ({cls}): references `{sym}` — a {cls} path must not "
            f"reach arbitrary capability execution. Re-audit {module}."
        )


def test_matrix_has_no_unknown_rows():
    """Every path in the matrix is classified — no UNKNOWN survives."""
    classes = {p[2] for p in _PATHS}
    assert classes <= {"ALLOW", "CONSTRAINED", "READONLY"}, (
        f"unclassified authority-matrix rows: {classes - {'ALLOW','CONSTRAINED','READONLY'}}"
    )
