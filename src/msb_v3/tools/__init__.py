"""Governed tools — the canonical tool registry and its execution perimeter.

Unified-architecture §5/§6: no tool may be handed to the model unless its
execution path terminates inside the governance perimeter. The registry
(``registry.py``) is the single source of tool truth; ``runtime.py`` wraps
every advertised tool in a capability gate + audit before it reaches the
contained executors (``executors.py``).

This closes the forensic finding (2026-08-15) that /chat advertised tools
to the model but never registered any implementation — every call resolved
to ``[tool-error] unknown tool``.
"""
