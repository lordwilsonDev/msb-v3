"""Transcript -> Requirements Specification extraction, Stage -1 input helper.

**PARKED (M3 convergence, 2026-08-16, owner: Wilson).** Implemented +
tested but not wired into the live runtime — the UAC stage pipeline is not
on MSB v3's canonical core path (the governed agent handle loop), and Rule 3
freezes new wiring during M0–M3. Kept in `src/` with its test suite
(`tests/uac/test_transcript_requirements_extractor.py`); revisit with
stage_0_knowledge_acquisition when a UAC workflow is selected (post-M7).
Not advertised as a live capability.

Roadmap gap (2026-08-04): Stage 0 (`stage_0_knowledge_acquisition.py`) only
ever accepted a hand-typed `RequirementsSpecification` (profession +
jurisdiction, typed directly). There was no path from a real transcript
(e.g. a Granola discovery-call export) to that structured input anywhere in
this repo. This module is that path.

Same Human Availability Gate principle as the rest of Stage -1
(`universal-agent-creator-v1.0.md`, Stage -1 section): **never fabricate a
Requirements Specification.** If the transcript doesn't actually state a
profession or jurisdiction, this raises rather than guessing one — an
extractor that silently defaults a missing field is exactly the failure
mode the gate exists to prevent, whether the input is a human typing or a
transcript being parsed.
"""
from __future__ import annotations

import json
from typing import List, Optional, Protocol

from msb_ledger.models import RequirementsSpecification


class ChatHarnessLike(Protocol):
    """Structural contract for the chat harness used by the real extraction
    backend. msb_ledger is standalone: the host application's
    ``msb_v3.harnesses.base.ChatHarness`` satisfies this protocol, but the
    ledger never imports it — the caller injects it."""

    def execute(self, prompt: str, context: Optional[dict] = None) -> "HarnessResultLike": ...


class HarnessResultLike(Protocol):
    """The slice of the harness result the extractor reads."""

    payload: dict

_EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured requirements from a real business discovery-call "
    "transcript. Only report what the transcript actually states. Never invent, "
    "assume, or fill in a plausible-sounding value for anything not said in the "
    "transcript. Respond with ONLY a JSON object, no other text, with these keys: "
    '"profession" (string or null), "jurisdiction" (string or null), '
    '"industry" (string or null), "organization_size" (string or null), '
    '"constraints" (list of strings), "existing_tools" (list of strings), '
    '"risk_tolerance" (string or null). Use null for anything not stated — '
    "do not guess."
)


class TranscriptExtractionBackend(Protocol):
    def extract(self, transcript: str) -> dict:
        """Return the raw extracted fields (pre-validation) as a dict."""
        ...


class TranscriptExtractionError(RuntimeError):
    """Raised on empty input or an unparseable/failed extraction — never
    swallowed into an empty result, same reasoning as ResearchBackendError
    (research_backend.py): a failure that looks like 'nothing found' is a
    silent lie if it was actually 'extraction couldn't run'."""


class LocalLLMTranscriptExtractionBackend:
    """Real backend: runs the extraction prompt through the host's local
    model via an injected ChatHarnessLike (msb_v3's ChatHarness satisfies
    the protocol; the ledger does not import it). Requires the local model
    backend to actually be reachable (see the host's OLLAMA_MODEL config) —
    if it isn't, ChatHarness currently masks that as a fallback echo of the
    prompt rather than raising, so this backend detects and rejects that
    fallback marker explicitly rather than parsing it as if it were real
    output."""

    def __init__(self, chat_harness: ChatHarnessLike) -> None:
        self._chat = chat_harness

    def extract(self, transcript: str) -> dict:
        result = self._chat.execute(transcript, context={"system": _EXTRACTION_SYSTEM_PROMPT})
        text = result.payload.get("text", "")
        if text.startswith("[fallback]"):
            raise TranscriptExtractionError(
                "local model backend did not actually respond (got a fallback echo, "
                "not a real completion) — check OLLAMA_MODEL matches an installed "
                "model and the server is reachable before retrying"
            )
        try:
            start, end = text.index("{"), text.rindex("}") + 1
            return json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError) as exc:
            raise TranscriptExtractionError(
                f"model output was not valid JSON, could not extract requirements: {exc!r}"
            ) from exc


class NullTranscriptExtractionBackend:
    """Test-only backend, returns fixed canned data. Never used as a
    production default (mirrors NullResearchBackend's own rule)."""

    def __init__(self, canned: dict) -> None:
        self._canned = canned

    def extract(self, transcript: str) -> dict:
        return self._canned


def extract_requirements_from_transcript(
    transcript: str,
    backend: TranscriptExtractionBackend,
) -> RequirementsSpecification:
    """Turn a real transcript into a RequirementsSpecification. Raises
    TranscriptExtractionError if the transcript is empty, or if profession
    or jurisdiction weren't actually stated — matches Stage -1's Human
    Availability Gate: halt rather than fabricate, even here."""
    if not transcript or not transcript.strip():
        raise TranscriptExtractionError("empty transcript — nothing to extract requirements from")

    raw = backend.extract(transcript)

    profession = raw.get("profession") or None
    jurisdiction = raw.get("jurisdiction") or None
    if not profession:
        raise TranscriptExtractionError(
            "transcript did not state a profession — cannot fabricate one; "
            "confirm directly with the stakeholder before compiling"
        )
    if not jurisdiction:
        raise TranscriptExtractionError(
            "transcript did not state a jurisdiction — cannot fabricate one; "
            "confirm directly with the stakeholder before compiling"
        )

    def _str_list(value: object) -> List[str]:
        if not value:
            return []
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]

    return RequirementsSpecification(
        profession=str(profession),
        jurisdiction=str(jurisdiction),
        industry=raw.get("industry") or None,
        organization_size=raw.get("organization_size") or None,
        constraints=_str_list(raw.get("constraints")),
        existing_tools=_str_list(raw.get("existing_tools")),
        risk_tolerance=raw.get("risk_tolerance") or None,
    )
