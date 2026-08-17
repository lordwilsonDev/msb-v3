"""Compatibility namespace for the standalone ``msb_ledger`` library (P4).

The auditable-ledger subsystem was extracted from ``msb_v3.uac`` into a
standalone package, ``msb_ledger``, with zero host coupling (no imports of
``msb_v3``; host components like ``MissionAnchor`` / ``ChatHarness`` are
injected structurally through protocols). This module is the thin shim that
keeps every existing consumer — the ~73 ``msb_v3.uac.*`` import sites plus
the test suite — working unchanged.

Each ``msb_ledger`` module is registered in ``sys.modules`` under its old
``msb_v3.uac.*`` name when this package is imported, so both
``from msb_v3.uac.audit_chain import AuditChain`` and
``import msb_v3.uac.chain_anchor`` resolve to the single canonical
implementation in ``msb_ledger``. Nothing lives in this directory; it is a
pure aliasing layer. (PEP 562 ``__getattr__`` is NOT sufficient here: the
import machinery resolves submodule imports via ``sys.modules`` / the path
finder, not package attribute lookup, so the aliases must be registered
eagerly.)
"""
from __future__ import annotations

import importlib
import sys

# Every module that moved from msb_v3.uac to msb_ledger. Registering the
# alias eagerly (this import runs before any consumer submodule import) is
# what makes ``from msb_v3.uac.<name> import ...`` resolve to the moved
# module. Import cost is unchanged: the old layout imported the same
# modules the moment any uac module was imported.
_LEDGER_MODULES = (
    "audit_chain",
    "axiom_library",
    "chain_anchor",
    "config",
    "merkle",
    "models",
    "notary",
    "observer_log",
    "research_backend",
    "signing",
    "stage_0_knowledge_acquisition",
    "timestamping",
    "transcript_requirements_extractor",
)

for _name in _LEDGER_MODULES:
    _module = importlib.import_module(f"msb_ledger.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

__all__ = list(_LEDGER_MODULES)

del _LEDGER_MODULES, _module, _name, importlib, sys
