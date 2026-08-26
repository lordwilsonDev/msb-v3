"""Intent extraction from speech transcripts.

Maps natural language transcripts to structured MSB commands.
Uses pattern matching for common operations, with LLM fallback
for complex or ambiguous requests.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from msb_v3.speech.models import Transcript, VoiceCommand

# ── Command patterns ──────────────────────────────────────────────────────

_PATTERNS: List[Tuple[re.Pattern, str, str, Dict[str, Any]]] = [
    # Research commands
    (
        re.compile(r"research\s+(.+)", re.IGNORECASE),
        "/research/assistant/run",
        "POST",
        {"capture_group": 1, "param_key": "topic"},
    ),
    # Chat/query commands
    (
        re.compile(r"(?:ask|query|tell me about|what is|what are)\s+(.+)", re.IGNORECASE),
        "/chat",
        "POST",
        {"capture_group": 1, "param_key": "query"},
    ),
    # Deploy commands
    (
        re.compile(r"deploy\s+(?:the\s+)?(?:canary|latest|version)", re.IGNORECASE),
        "/governance/execute",
        "POST",
        {"action": "deploy_canary"},
    ),
    # Status commands
    (
        re.compile(r"(?:system\s+)?status", re.IGNORECASE),
        "/system/health",
        "GET",
        {},
    ),
    # Kill switch
    (
        re.compile(r"(?:kill|stop|halt)\s+(?:the\s+)?(?:loop|system|everything)", re.IGNORECASE),
        "/governance/killswitch/arm",
        "POST",
        {"operator": "voice", "reason": "voice command"},
    ),
    # Resume
    (
        re.compile(r"(?:resume|start|restart)\s+(?:the\s+)?(?:loop|system)", re.IGNORECASE),
        "/governance/killswitch/disarm",
        "POST",
        {"operator": "voice"},
    ),
    # Flywheel
    (
        re.compile(r"(?:start|run)\s+flywheel", re.IGNORECASE),
        "/flywheel/turns",
        "POST",
        {"problem": "voice-initiated research"},
    ),
    # Help
    (
        re.compile(r"(?:help|commands|what can you do)", re.IGNORECASE),
        None,
        "HELP",
        {},
    ),
]


def extract_intent(transcript: Transcript) -> VoiceCommand:
    """Extract a structured command from a transcript.

    Uses pattern matching first. If no pattern matches, wraps the
    full transcript as a chat query (the safest fallback).
    """
    text = transcript.text.strip()
    if not text:
        return VoiceCommand(
            command="empty",
            raw_transcript="",
            confidence=0.0,
        )

    for pattern, endpoint, method, config in _PATTERNS:
        match = pattern.search(text)
        if match:
            return _build_command(text, endpoint, method, config, match)

    # Fallback: treat as a chat query
    return VoiceCommand(
        command="chat",
        endpoint="/chat",
        method="POST",
        params={"query": text},
        raw_transcript=text,
        confidence=0.5,
    )


def list_commands() -> List[Dict[str, Any]]:
    """Return available voice commands for help/documentation."""
    return [
        {
            "pattern": p.pattern,
            "description": _describe_pattern(p),
            "endpoint": ep,
            "method": m,
        }
        for p, ep, m, _ in _PATTERNS
        if ep is not None
    ]


# ── Helpers ──────────────────────────────────────────────────────────────


def _build_command(
    text: str,
    endpoint: Optional[str],
    method: str,
    config: Dict[str, Any],
    match: re.Match,
) -> VoiceCommand:
    """Build a VoiceCommand from a pattern match."""
    params: Dict[str, Any] = {}

    if "capture_group" in config:
        captured = match.group(config["capture_group"]).strip()
        params[config["param_key"]] = captured
    if "action" in config:
        params["action"] = config["action"]
    if "operator" in config:
        params["operator"] = config["operator"]
    if "reason" in config:
        params["reason"] = config["reason"]
    if "problem" in config:
        params["problem"] = config["problem"]

    return VoiceCommand(
        command=method.lower() if endpoint else method.lower(),
        endpoint=endpoint or "",
        method=method,
        params=params,
        raw_transcript=text,
        confidence=0.8,
    )


def _describe_pattern(pattern: re.Pattern) -> str:
    """Generate a human-readable description for a pattern."""
    desc = pattern.pattern
    desc = re.sub(r"\(\?:", "", desc)
    desc = re.sub(r"\)", "", desc)
    desc = re.sub(r"\\s+", " ", desc)
    desc = re.sub(r"\.\+\.\*", "...", desc)
    return desc
