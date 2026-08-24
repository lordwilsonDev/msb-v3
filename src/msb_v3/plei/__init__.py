"""PLEI — Project Lifecycle Engineering Intelligence.

A governed engineering intelligence layer that reconstructs a project's
engineering state from evidence, classifies its lifecycle position, and
determines what should happen next — without replacing MSB, DeepSeek, or
the provider registry.

Phase 1: Project Twin — ingest, model, classify.
"""

from __future__ import annotations

__all__ = ["ProjectTwin", "Provenance", "Lifecycle", "ingest_all", "plei_router"]