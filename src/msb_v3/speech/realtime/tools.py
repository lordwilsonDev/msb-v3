"""Tools the realtime model may call. Only LOW-risk, read-only tools run."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence

from msb_v3.speech.safety import RiskLevel


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], dict]
    risk: RiskLevel = RiskLevel.LOW


class ToolRegistry:
    def __init__(self, specs: Sequence[ToolSpec]) -> None:
        self.specs: Dict[str, ToolSpec] = {s.name: s for s in specs}

    def _allowed(self) -> List[ToolSpec]:
        return [s for s in self.specs.values() if s.risk is RiskLevel.LOW]

    def openai_tools(self) -> List[dict]:
        return [
            {"type": "function", "name": s.name, "description": s.description,
             "parameters": s.parameters}
            for s in self._allowed()
        ]

    def gemini_declarations(self) -> List[dict]:
        return [
            {"name": s.name, "description": s.description, "parameters": s.parameters}
            for s in self._allowed()
        ]

    def dispatch(self, name: str, args: dict) -> dict:
        spec = self.specs.get(name)
        if spec is None:
            return {"error": f"unknown tool: {name}"}
        if spec.risk is not RiskLevel.LOW:
            return {"error": f"{name} is not allowed by voice policy (risk {spec.risk.value})"}
        try:
            return spec.handler(args)
        except Exception as exc:  # noqa: BLE001 — a tool failure must become a spoken error, not a crash
            return {"error": f"{name} failed: {type(exc).__name__}"}


_EMPTY = {"type": "object", "properties": {}}


def _current_time(_args: dict) -> dict:
    now = datetime.now().astimezone()
    return {"time": now.strftime("%-I:%M %p"), "date": now.strftime("%A, %B %-d, %Y"),
            "timezone": now.strftime("%Z")}


def _make_status(base_url: str) -> Callable[[dict], dict]:
    def _status(_args: dict) -> dict:
        url = base_url.rstrip("/") + "/health"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 — local URL
                body = resp.read(4096).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            return {"error": f"msb-v3 not reachable at {base_url} ({type(exc).__name__})"}
        try:
            return {"status": json.loads(body)}
        except json.JSONDecodeError:
            return {"status": body[:500]}
    return _status


def default_tools(base_url: Optional[str] = None) -> ToolRegistry:
    base = base_url or os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766")
    return ToolRegistry([
        ToolSpec("get_current_time", "Get the current local date and time.", _EMPTY, _current_time),
        ToolSpec("get_system_status", "Get the msb-v3 system health status.", _EMPTY,
                 _make_status(base)),
    ])
