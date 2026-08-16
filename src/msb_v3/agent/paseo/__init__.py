"""MSB ↔ Paseo adapter (unified-architecture §7).

The Paseo daemon manages Claude Code / Codex / OpenCode agents and exposes
its agent-management surface as an MCP server over Streamable HTTP at
``/mcp/agents``. This package is the MSB side: a minimal MCP client
(``client``), the six spec operations (``adapter.PaseoAdapter``), and the
operator-gated permission broker that parks a worker's permission requests
on durable Vesta approvals (``permissions``).
"""

from msb_v3.agent.paseo.adapter import PaseoAdapter
from msb_v3.agent.paseo.client import PaseoMcpClient, PaseoMcpError
from msb_v3.agent.paseo.permissions import PaseoPermissionBroker, parse_bind

__all__ = [
    "PaseoAdapter",
    "PaseoMcpClient",
    "PaseoMcpError",
    "PaseoPermissionBroker",
    "parse_bind",
]
