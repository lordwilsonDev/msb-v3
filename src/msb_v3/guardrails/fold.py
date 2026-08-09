"""Minimal guardrails ported from forge: respond tool + step enforcement."""

from __future__ import annotations

from typing import Any, Callable


class Nudge:
    def __init__(self, role: str, content: str, kind: str, tier: int = 0) -> None:
        self.role = role
        self.content = content
        self.kind = kind
        self.tier = tier


class StepTracker:
    def __init__(self, required_steps: list[str]) -> None:
        self.required_steps = list(required_steps)
        self.completed_steps: dict[str, None] = {}
        self.executed_tools: dict[str, list[dict[str, Any]]] = {}

    def record(self, tool_name: str, args: dict[str, Any] | None = None) -> None:
        self.completed_steps[tool_name] = None
        self.executed_tools.setdefault(tool_name, []).append(args or {})

    def is_satisfied(self) -> bool:
        return all(s in self.completed_steps for s in self.required_steps)

    def pending(self) -> list[str]:
        return [s for s in self.required_steps if s not in self.completed_steps]

    def summary_hint(self) -> str:
        if not self.completed_steps:
            return "[No steps completed yet]"
        return f"[Steps completed: {', '.join(self.completed_steps)}]"


def _tool_name(tc: dict[str, Any]) -> str:
    if "tool" in tc:
        return tc["tool"]
    fn = tc.get("function") or {}
    return fn.get("name", "")


class StepEnforcer:
    def __init__(
        self,
        required_steps: list[str],
        terminal_tools: frozenset[str],
    ) -> None:
        self._tracker = StepTracker(required_steps)
        self.terminal_tools = terminal_tools
        self._premature_attempts = 0

    def check(self, tool_calls: list[dict[str, Any]]) -> Nudge | None:
        has_terminal = any(_tool_name(tc) in self.terminal_tools for tc in tool_calls)
        if has_terminal and not self._tracker.is_satisfied():
            self._premature_attempts += 1
            tier = min(self._premature_attempts, 3)
            attempted = next(tc["tool"] for tc in tool_calls if tc["tool"] in self.terminal_tools)
            return Nudge(
                role="user",
                content=_step_nudge(attempted, self._tracker.pending(), tier=tier),
                kind="step",
                tier=tier,
            )
        return None

    def record(self, tool_name: str, args: dict[str, Any] | None = None) -> None:
        self._tracker.record(tool_name, args)

    def is_satisfied(self) -> bool:
        return self._tracker.is_satisfied()


class RespondTool:
    @staticmethod
    def spec() -> dict[str, Any]:
        return {
            "type": "function",
            "name": "respond",
            "description": (
                "Respond to the user with a message. Use this when chatting, "
                "reporting results, or when no other tool action is needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Final message to the user"}
                },
                "required": ["message"],
            },
        }

    @staticmethod
    def callable() -> Callable[..., str]:
        def _respond(message: str) -> str:
            return message

        return _respond


def _step_nudge(terminal_tool: str, pending_steps: list[str], tier: int = 1) -> str:
    tier = max(1, min(3, tier))
    steps = ", ".join(pending_steps)
    if tier == 1:
        return (
            f"You cannot call {terminal_tool} yet. "
            f"You must first complete these required steps: {steps}. "
            "Call one of them now."
        )
    if tier == 2:
        return f"You must call one of these tools now: {steps}. Pick one."
    return (
        f"STOP. You MUST call one of: {steps}. "
        f"Do NOT call {terminal_tool}. "
        f"Your next response MUST be a tool call to one of: {steps}."
    )


def _retry_nudge(raw_response: str) -> str:
    return (
        "Your previous response was not a valid tool call. "
        "You must respond with a tool call, not free text. "
        "Please try again with a valid tool call."
    )


def _unknown_tool_nudge(tool_name: str, available_tools: list[str]) -> str:
    tools_list = ", ".join(available_tools)
    return f"Tool '{tool_name}' does not exist. Available tools: {tools_list}. Call one of them."
