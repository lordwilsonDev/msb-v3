"""S-AOS Guardian — headless repository stewardship for msb-v3.

v1 is OBSERVE-only: it inspects the repo, classifies its health, and writes
evidence + escalations/proposals to the Obsidian vault. It never mutates the
working tree. See
``~/Documents/Vault/30_Architecture/S-AOS-Guardian/`` (docs 1-4) for the
governing spec.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
