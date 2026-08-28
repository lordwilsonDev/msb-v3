"""META-3: Verification Gate — independent verification of worker results.

The VerificationGate is the component that makes the system falsifiable.
Without it, no worker result can be trusted.  The worker says "I finished."
The VerificationGate says "Here's whether it actually worked."

Three verification strategies (from ExecutionPolicy):

    STANDARD — deterministic checks only (pytest, ruff, exit codes)
    STRICT   — deterministic + contract checks + import direction
    FUZZY    — deterministic + semantic validation + independent judge

The worker is NEVER allowed to verify itself.
"""

from msb_v3.meta.verification.gate import GateResult, VerificationGate
from msb_v3.meta.verification.strategies import (
    FuzzyStrategy,
    StandardStrategy,
    StrictStrategy,
)

__all__ = [
    "FuzzyStrategy",
    "GateResult",
    "StandardStrategy",
    "StrictStrategy",
    "VerificationGate",
]
