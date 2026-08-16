"""Software Factory (Sovereign Architecture v4.0 §4.2.6, P3; §8-10, §31
items 25-30).

    issue → classify → plan (with MoIE risks) → implement (isolated
    worktree) → test (real command, real evidence) → review (independent:
    MoIE inversion + code-graph impact) → verify (acceptance criteria vs
    observed evidence) → verdict

Core principle (§9-10, the anti-fabrication rule): **no agent may mark its
own work as fully trusted.** The builder's output is never self-certified —
an independent reviewer and a grounded verifier (evidence from executed
tests, not claims) decide the outcome. The final verdict is one of
MERGED / NEEDS_WORK / BLOCKED / FAILED, carried with a hash evidence chain.

Every stage is deterministic by default and injectable (a test injects a
patch builder, the API defaults to a CLI worker builder that fails loudly
without a funded model — never silently).
"""

from msb_v3.factory.builders import Builder, CliAgentBuilder, PatchBuilder
from msb_v3.factory.models import FactoryRun
from msb_v3.factory.pipeline import SoftwareFactory

__all__ = ["Builder", "CliAgentBuilder", "FactoryRun", "PatchBuilder", "SoftwareFactory"]
