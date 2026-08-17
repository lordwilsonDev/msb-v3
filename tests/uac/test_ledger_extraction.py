"""P4 extraction guards: ``msb_ledger`` is standalone, the ``msb_v3.uac``
shim aliases it, and the config seam is injectable.

These tests pin the extraction contract so it cannot silently re-couple:
  1. no ``msb_ledger`` module may import ``msb_v3`` (the whole point of P4);
  2. every ``msb_v3.uac.X`` import must resolve to the ``msb_ledger.X``
     canonical module (the shim's job);
  3. the two host-coupled components (MissionAnchor / ChatHarness) must be
     injected, not imported.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

LEDGER_ROOT = Path(__file__).resolve().parents[2] / "src" / "msb_ledger"
LEDGER_MODULES = sorted(
    p.stem for p in LEDGER_ROOT.glob("*.py") if p.name != "__init__.py"
)


def _imports_msb_v3(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "msb_v3" or alias.name.startswith("msb_v3."):
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "msb_v3" or node.module.startswith("msb_v3.")):
                found.append(node.module)
    return found


@pytest.mark.parametrize("module_name", LEDGER_MODULES)
def test_ledger_module_has_no_msb_v3_imports(module_name: str) -> None:
    """The ledger library must not import the host application (P4 contract)."""
    path = LEDGER_ROOT / f"{module_name}.py"
    offenders = _imports_msb_v3(path)
    assert not offenders, f"{module_name} imports host msb_v3: {offenders}"


@pytest.mark.parametrize("module_name", LEDGER_MODULES)
def test_uac_shim_resolves_to_ledger(module_name: str) -> None:
    """``msb_v3.uac.<name>`` and ``msb_ledger.<name>`` must be the same module."""
    canonical = importlib.import_module(f"msb_ledger.{module_name}")
    shimmed = importlib.import_module(f"msb_v3.uac.{module_name}")
    assert shimmed is canonical


def test_ledger_modules_registered_in_sys_modules() -> None:
    """The shim must register every ledger module under the old name so
    ``from msb_v3.uac.X import Y`` submodule imports resolve (PEP 562 alone
    is not enough — verified during extraction)."""
    for module_name in LEDGER_MODULES:
        assert f"msb_v3.uac.{module_name}" in sys.modules, module_name
        assert sys.modules[f"msb_v3.uac.{module_name}"] is sys.modules[f"msb_ledger.{module_name}"]


def test_host_components_are_injected_not_imported() -> None:
    """stage_0 / transcript must NOT hard-import MissionAnchor / ChatHarness —
    they take them as constructor args (structural protocols)."""
    stage_0 = Path(LEDGER_ROOT / "stage_0_knowledge_acquisition.py").read_text()
    transcript = Path(LEDGER_ROOT / "transcript_requirements_extractor.py").read_text()
    assert "from msb_v3" not in stage_0
    assert "from msb_v3" not in transcript
    # The protocols exist and the defaults raise instead of fabricating a host dep.
    assert "MissionAnchorLike" in stage_0 and "ChatHarnessLike" in transcript


def test_config_seam_is_injectable() -> None:
    """configure() mutates the shared settings object the ledger modules read."""
    from msb_ledger.config import configure, settings

    original = settings.db_path
    try:
        configure(db_path="/tmp/seam-test.db")
        assert settings.db_path == "/tmp/seam-test.db"
        assert settings.db_path != original
    finally:
        configure(db_path=original)
