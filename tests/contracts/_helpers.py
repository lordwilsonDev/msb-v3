'''Shared fakes for the Phase 1 gate-contract and layered-boundary tests.

Mirrors the fakes in tests/agent/test_handle.py so the contract tests
exercise the real handle() path with the same doubles. Kept here so the
gate, layered-boundary, and evidence-receipt suites share one definition.
'''

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


class Audit:
    '''Audit-chain double: records appends instead of touching a real chain.'''

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def append(self, component: str, event_type: str, payload: Dict[str, Any]) -> None:
        self.events.append((component, event_type, payload))


class Switch:
    def __init__(self, armed: bool = False) -> None:
        self._armed = armed

    def is_armed(self) -> bool:
        return self._armed


class Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake"
        self.latency_s = 0.0
        self.tool_calls = []


class SequenceClient:
    '''Returns texts in order (intent JSON first, then plan output).'''

    def __init__(self, *texts: str) -> None:
        self._texts = list(texts)

    def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
        text = self._texts.pop(0) if self._texts else "garbage"
        return Resp(text)


class TrackingClient:
    '''Wraps a SequenceClient and counts generate() calls — proves the intent
    model is never consulted when the quick-reject gate denies.'''

    def __init__(self, inner: SequenceClient) -> None:
        self._inner = inner
        self.generate_calls = 0

    def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
        self.generate_calls += 1
        return self._inner.generate(prompt, system=system, tools=tools, temperature=temperature, max_tokens=max_tokens)


class FakeProvider:
    '''Hermetic tool provider: search/chat/write all return canned results.'''

    def __init__(self, tmp: Path) -> None:
        self._tmp = tmp
        self.calls: list[str] = []

    async def run_tool(self, name: str, *, task, inputs: Dict[str, Any], session: str) -> Any:
        self.calls.append(name)
        if name == "search_query":
            return [{"text": "source one", "source": "vault/a.md"}]
        if name == "chat":
            return "The client-ready brief."
        if name == "vault_write":
            path = self._tmp / "brief.md"
            path.write_text("# Brief\n\nbrief\n")
            return {"path": str(path), "heading": "# Brief"}
        raise ValueError(f"unknown tool: {name}")


class FakeDecision:
    '''Canned MoIE decision double.'''

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict

    def as_dict(self) -> Dict[str, Any]:
        return {
            "claim": "",
            "verdict": self.verdict,
            "blocked": self.verdict == "BLOCK",
            "confidence": 0.9,
            "meta_critique": "blocked: danger keyword present" if self.verdict == "BLOCK" else "",
        }


class FakeMoIE:
    '''Sync MoIE double with a canned verdict or a raised error; records the
    claims analyzed. A ``malformed=True`` double returns a plain dict (no
    as_dict) to simulate a broken integration.'''

    def __init__(self, verdict: str = "APPROVE", error: Exception | None = None, malformed: bool = False) -> None:
        self.verdict = verdict
        self.error = error
        self.malformed = malformed
        self.claims: list[str] = []

    def analyze(self, claim: str, *, context: Dict[str, Any] | None = None):
        self.claims.append(claim)
        if self.error is not None:
            raise self.error
        if self.malformed:
            return {"verdict": self.verdict, "blocked": False}  # no as_dict
        return FakeDecision(self.verdict)


INTENT_WITH_WRITE = (
    '{"goals": ["research the vault"], "constraints": [], '
    '"permissions": ["read_vault", "write_file"], "privacy": true, "domain": "client-brief"}'
)

# A malicious intent that requests privileged capabilities the system must
# never grant through the LLM planner.
INTENT_WITH_PRIVILEGED = (
    '{"goals": ["delete the database"], "constraints": [], '
    '"permissions": ["permissions", "vault_delete", "read_vault"], "privacy": true, "domain": "ops"}'
)

# A malicious LLM plan payload that tries to declare a privileged capability.
MALICIOUS_PLAN = (
    '{"tasks": [{"task_id": "evil", "goal": "delete the database", "parent_id": null, '
    '"capabilities": ["permissions", "vault_delete"], "tools": ["permissions"], '
    '"expected_output": "deleted", "verification_method": "none", "timeout_s": 30, '
    '"retry_policy": "retry:0"}]}'
)
