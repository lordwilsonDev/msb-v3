"""Steward — the canonical project-state layer (AIL–MoIE blueprint, Layer 02).

Holds a machine-readable ``project-state.json`` schema, a deterministic
validator, and a CLI that enforces the blueprint's core rules:

  - §7 canonical state: identity / objectives / constraints / health /
    execution / unknowns / contradictions / risks / opportunities;
  - §53 health vector with per-axis GREEN/YELLOW/RED/UNKNOWN;
  - §54 UNKNOWN != GREEN — an axis marked green without evidence is invalid.

The state file lives where the project lives (the vault,
``30_Architecture/AIL-MoIE-Project-Steward/state/``) — the validator here is
what keeps it honest.  Stdlib only, no network, no LLM.
"""

from .state import HealthValue, ProjectState, ValidationIssue, validate_state

__all__ = ["HealthValue", "ProjectState", "ValidationIssue", "validate_state"]