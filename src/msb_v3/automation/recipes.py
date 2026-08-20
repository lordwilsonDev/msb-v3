"""Recipes — the operator's own automation language (Stage 2).

A recipe is a plain sentence msb-v3 parses deterministically — no LLM, no
API spend, no platform syntax:

    every 30 minutes, post a heartbeat to http://127.0.0.1:5678/webhook/msb-ping
    daily at 09:00, ping https://hook.make.com/abc

Grammar v1 (extended by this module, never by a platform):

    schedule  := "every <n> minute[s]" | "every <n> hour[s]" | "hourly"
               | "daily at <HH:MM>"
    action    := "post [<something>] to <url>" | "ping <url>"
    name      := "named <name>," (optional; else derived from the URL)

``parse`` returns a living-automation plan (provider="self" — the
dispatcher executes it, logic never leaves msb-v3) or None when the text is
not a recipe (callers fall back to the LLM). The plan carries ``schedule``
+ ``action`` so create_automation registers it with the dispatcher.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


def _parse_schedule(text: str) -> Optional[str]:
    m = re.search(r"every\s+(\d+)\s+minute", text, re.I)
    if m:
        return f"*/{int(m.group(1))} * * * *"
    m = re.search(r"every\s+(\d+)\s+hour", text, re.I)
    if m:
        return f"0 */{int(m.group(1))} * * *"
    if re.search(r"\bhourly\b", text, re.I):
        return "0 * * * *"
    m = re.search(r"daily\s+at\s+(\d{1,2}):(\d{2})", text, re.I)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{minute} {hour} * * *"
    return None


def _parse_url(text: str) -> Optional[str]:
    # action := "post [<something>] to <url>" | "ping <url>"
    m = re.search(r"\b(?:to|ping)\s+(https?://\S+)", text, re.I)
    if m:
        return m.group(1).rstrip(".,;")
    return None


def parse(text: str) -> Optional[Dict[str, Any]]:
    """Parse a recipe sentence into a living-automation plan, or None."""
    text = (text or "").strip()
    if not text:
        return None

    schedule = _parse_schedule(text)
    url = _parse_url(text)
    if schedule is None or url is None:
        return None

    name_m = re.search(r"named\s+[\"']?([^\"',.]+)[\"']?", text, re.I)
    if name_m:
        name = name_m.group(1).strip()[:80]
    else:
        base = url.rstrip("/").rsplit("/", 1)[-1]
        name = (base or "recipe-automation")[:80]

    return {
        "provider": "self",
        "name": name,
        "description": text[:300],
        "schedule": schedule,
        "action": {"type": "webhook_post", "url": url, "payload": {"$now": ""}},
    }
