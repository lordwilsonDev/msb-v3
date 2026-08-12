"""Agent intent interpreter — the first stage of the Handle-this loop.

Turns a raw request into a structured Intent (goals, constraints, permissions)
via the local model with tolerant JSON parsing and a deterministic fallback,
so the loop never crashes on a bad model response.

Design (Dream Big Blue, T1.1):
- LLM-first: the local model extracts goals/constraints/permissions/privacy/domain.
- Tolerant: markdown fences and surrounding prose are stripped; the first
  balanced {...} object is parsed. Bad output degrades, never raises.
- Deterministic fallback: any model or parse failure yields
  Intent(goals=(request,), source="fallback") — the loop can always proceed
  to planning.
- Observability: every interpretation attempt lands on
  queries_total{harness="agentic",event="intent:llm"|"intent:fallback"}.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient
from msb_v3.observability.metrics import Metrics

_INTENT_SYSTEM = (
    "You are an intent interpreter. Extract the user's intent into a strict JSON "
    'object with exactly these keys: "goals" (array of 1-3 concrete goal strings), '
    '"constraints" (array of constraint strings; empty array if none), '
    '"permissions" (array of capability hints the task requests, e.g. "read_vault", '
    '"write_file", "web_search"; empty array if none), '
    '"privacy" (boolean — true unless the task is explicitly public), '
    '"domain" (a short domain label string or null). '
    "Return ONLY the JSON object — no markdown fences, no commentary."
)


@dataclass(frozen=True)
class Intent:
    request: str
    goals: tuple[str, ...]
    constraints: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    privacy: bool = True
    domain: Optional[str] = None
    # "llm" when the model produced a parseable intent; "fallback" otherwise.
    source: str = "fallback"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "request": self.request,
            "goals": list(self.goals),
            "constraints": list(self.constraints),
            "permissions": list(self.permissions),
            "privacy": self.privacy,
            "domain": self.domain,
            "source": self.source,
        }


def _extract_json(text: str) -> Dict[str, Any] | None:
    """Tolerant JSON extraction from a model response.

    Strips markdown fences and finds the first balanced {...} region, parsing
    it as a JSON object. The brace scan is string-aware (braces inside JSON
    string values, honoring backslash escapes, do not corrupt the depth
    count). Returns None when no parseable object exists.
    """
    if not text:
        return None
    stripped = re.sub(r"```(?:json)?\s*|\s*```", "", text, flags=re.IGNORECASE).strip()
    start = stripped.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(stripped[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return data if isinstance(data, dict) else None
    return None


def _clean_str_list(value: Any) -> tuple[str, ...]:
    """Coerce a model-provided value into a tuple of non-empty strings."""
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return tuple(out)


def interpret_intent(
    request: str,
    client: LocalAIClient | LlamaCPPClient | None = None,
) -> Intent:
    """Interpret a raw request into a structured Intent.

    LLM-first with a deterministic fallback: any model or parse failure yields
    a usable fallback intent (goal = the raw request) rather than an error, so
    the Handle-this loop can always proceed to planning.
    """
    request = (request or "").strip()
    if not request:
        return Intent(request="", goals=())

    if client is None:
        from msb_v3.local_ai.client_factory import get_client

        client = get_client()

    try:
        resp = client.generate(request, system=_INTENT_SYSTEM, temperature=0.0, max_tokens=512)
        data = _extract_json(resp.text)
        goals = _clean_str_list(data.get("goals") if data else None)
        if data and goals:
            constraints = _clean_str_list(data.get("constraints"))
            permissions = _clean_str_list(data.get("permissions"))
            # Privacy defaults true unless the model emitted a real bool — a
            # string "false" must not flip the local-vs-cloud routing.
            privacy_raw = data.get("privacy", True)
            privacy = privacy_raw if isinstance(privacy_raw, bool) else True
            domain = data.get("domain")
            if not isinstance(domain, str) or not domain.strip():
                domain = None
            Metrics.inc("agentic", "intent:llm")
            return Intent(
                request=request,
                goals=goals,
                constraints=constraints,
                permissions=permissions,
                privacy=privacy,
                domain=domain,
                source="llm",
            )
    except Exception:
        pass

    Metrics.inc("agentic", "intent:fallback")
    return Intent(request=request, goals=(request,), source="fallback")
