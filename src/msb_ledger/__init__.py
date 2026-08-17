"""msb_ledger — standalone auditable-ledger library.

Extracted from ``msb_v3.uac`` (P4). This package is the most-tested,
most-differentiated asset in MSB v3: an append-only audit chain with an
external signed anchor, an off-box notary with RFC 3161 timestamping,
hardware-backed signing (Secure Enclave / YubiKey PIV / keychain), key
rotation and recovery, and the observer log. It has ZERO imports from
``msb_v3`` — the only host coupling was ``msb_v3.core.config`` (5 sites),
severed here via ``msb_ledger.config`` (env-first defaults identical to the
app's, plus an explicit ``configure()`` seam).

Modules:
    audit_chain.py     append-only hash chain + verify + repair
    chain_anchor.py    external signed tip anchor + notary + key rotation
    signing.py         algorithm-agnostic signing backends (software/SE/YubiKey)
    timestamping.py    RFC 3161 trusted timestamps
    notary.py          off-box notary (rclone push, backfill convergence)
    models.py          shared data models
    observer_log.py    operator observer log
    axiom_library.py   axiom artifact store
    research_backend.py  Tavily research feed (MSB research, kept here for uac parity)
    stage_0_knowledge_acquisition.py  stage-0 research pipeline (parked)
    transcript_requirements_extractor.py  transcript -> requirements (parked)

The ``msb_v3.uac`` package is now a thin compatibility namespace that
aliases each module here (``sys.modules`` aliasing) for EXTERNAL
consumers (the test suite, third-party callers). INTERNAL ``msb_v3`` code
imports the ledger directly (``from msb_ledger.X import ...``) — the shim
is a runtime-only alias that static mypy cannot see, so routing internal
code through it would turn ``mypy src`` red while tests stay green (the
regression found 2026-08-17, guarded by
``tests/uac/test_ledger_extraction.py``).
"""

from msb_ledger.config import configure, settings

__all__ = ["configure", "settings"]
