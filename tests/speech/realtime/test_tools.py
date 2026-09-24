from __future__ import annotations

from msb_v3.speech.realtime.tools import ToolRegistry, ToolSpec, default_tools
from msb_v3.speech.safety import RiskLevel


def _echo(args):
    return {"echo": args}


def _boom(args):
    raise ValueError("secret detail")


def _registry():
    return ToolRegistry([
        ToolSpec("echo", "Echo args.", {"type": "object", "properties": {}}, _echo),
        ToolSpec("boom", "Fails.", {"type": "object", "properties": {}}, _boom),
        ToolSpec("delete_all", "Dangerous.", {"type": "object", "properties": {}},
                 _echo, risk=RiskLevel.CRITICAL),
    ])


def test_dispatch_runs_low_risk_tool():
    assert _registry().dispatch("echo", {"a": 1}) == {"echo": {"a": 1}}


def test_unknown_tool_is_error():
    assert _registry().dispatch("nope", {}) == {"error": "unknown tool: nope"}


def test_non_low_risk_tool_refused_without_running():
    out = _registry().dispatch("delete_all", {})
    assert out == {"error": "delete_all is not allowed by voice policy (risk CRITICAL)"}


def test_handler_exception_is_contained():
    out = _registry().dispatch("boom", {})
    assert out == {"error": "boom failed: ValueError"}


def test_openai_tool_shape():
    tools = _registry().openai_tools()
    assert tools[0] == {
        "type": "function",
        "name": "echo",
        "description": "Echo args.",
        "parameters": {"type": "object", "properties": {}},
    }


def test_only_low_risk_tools_are_advertised():
    names = [t["name"] for t in _registry().openai_tools()]
    assert "delete_all" not in names
    assert [d["name"] for d in _registry().gemini_declarations()] == names


def test_default_tools_time():
    reg = default_tools()
    out = reg.dispatch("get_current_time", {})
    assert "time" in out and "date" in out


def test_default_tools_status_unreachable_is_error_not_crash():
    reg = default_tools(base_url="http://127.0.0.1:9")
    out = reg.dispatch("get_system_status", {})
    assert out["error"].startswith("msb-v3 not reachable")
