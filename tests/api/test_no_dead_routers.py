"""Guard: no dead routers or unwired API modules can be reintroduced.

M3 convergence (2026-08-16): ``api/status.py`` was a complete, tested
router that was never mounted — a dead route nobody could reach, discovered
in the M0 surface inventory (S1) and cut in M3. This test makes that class
of regression impossible by construction:

1. Every module in ``msb_v3/api/`` that defines a module-level ``router``
   must be imported by ``app.py`` and mounted via ``include_router`` in
   ``create_app()``.
2. Every router imported into ``app.py`` must actually be mounted (catches
   import-then-forget).
3. The one documented exception is ``tenant_chat`` — a deliberately parked
   placeholder, pinned unmounted by ``tests/api/test_tenant_chat_gate.py``.

The check is static (AST on ``app.py`` + the api directory), so it needs no
database, no env, and no app boot — it runs everywhere the suite runs.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "msb_v3"
API_DIR = ROOT / "api"
APP_FILE = API_DIR / "app.py"

# Documented, dated exceptions — routers that exist but are deliberately NOT
# mounted. Each entry needs its own gate test asserting it stays unmounted.
PARKED_UNMOUNTED = {
    "tenant_chat": (
        "parked placeholder (Phase 1 hardening 2026-08-15) — pinned "
        "unmounted by tests/api/test_tenant_chat_gate.py"
    ),
}


def _module_defines_router(path: Path) -> bool:
    """True if the module assigns a module-level ``router`` (plain or annotated)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "router":
                return True
    return False


def _app_router_imports() -> dict[str, str]:
    """Map mounted alias -> source module name for every `import router` in app.py."""
    tree = ast.parse(APP_FILE.read_text(encoding="utf-8"))
    imported: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if not node.module.startswith("msb_v3."):
            continue
        module = node.module.rsplit(".", 1)[-1]
        for alias in node.names:
            if alias.name == "router":
                imported[alias.asname or "router"] = module
    return imported


def _app_mounted_aliases() -> set[str]:
    """The router aliases passed to app.include_router(...) in create_app()."""
    tree = ast.parse(APP_FILE.read_text(encoding="utf-8"))
    mounted: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "include_router"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            mounted.add(node.args[0].id)
    return mounted


def test_every_api_router_is_mounted() -> None:
    """No api module may define a router that is never wired into the app."""
    defined = {
        path.stem
        for path in sorted(API_DIR.glob("*.py"))
        if path.name not in ("__init__.py", "app.py") and _module_defines_router(path)
    }
    imported = _app_router_imports()
    mounted_modules = set(imported.values())

    unmounted = (defined - mounted_modules) - set(PARKED_UNMOUNTED)
    assert not unmounted, (
        "routers defined in msb_v3/api/ but never imported+mounted by "
        f"app.py: {sorted(unmounted)}. Mount them in create_app() or add to "
        "PARKED_UNMOUNTED with a dated reason + a gate test."
    )

    # Sanity: the allowlist entries must still exist as modules (so a parked
    # router can't be deleted while its exception lingers — or vice versa).
    for parked in PARKED_UNMOUNTED:
        assert (API_DIR / f"{parked}.py").exists(), (
            f"PARKED_UNMOUNTED references {parked} but the module is gone; "
            "remove the entry."
        )
        assert parked in defined, (
            f"PARKED_UNMOUNTED lists {parked} but it no longer defines a "
            "router; remove the entry."
        )


def test_every_imported_router_is_mounted() -> None:
    """No router may be imported into app.py and then forgotten."""
    imported = _app_router_imports()
    mounted = _app_mounted_aliases()
    forgotten = set(imported) - mounted
    assert not forgotten, (
        f"routers imported by app.py but never mounted: {sorted(forgotten)}"
    )
