"""Automation brain — msb-v3 creates its own automations.

The brain turns a request (from the wake agent, /chat, or the /automation
API) into a structured plan via DeepSeek, then executes it through the
provider clients: n8n (self-hosted, free, live), Make / Zapier / GoHighLevel
(activated as their keys are configured). Discipline matches the rest of the
runtime: dry-run by default (creation requires explicit approval), a spend
cap on the LLM brain (MSB_AUTOMATION_BUDGET_USD — the $10 key), and every
creation recorded in a durable manifest. See docs/automation-brain.md.
"""

from msb_v3.automation.brain import create_automation, plan_automation, try_parse_plan
from msb_v3.automation.clients import (
    GhlClient,
    MakeClient,
    N8nClient,
    ZapierClient,
    get_client,
    provider_status,
)
from msb_v3.automation.manifest import Manifest

__all__ = [
    "GhlClient",
    "MakeClient",
    "Manifest",
    "N8nClient",
    "ZapierClient",
    "create_automation",
    "get_client",
    "plan_automation",
    "provider_status",
    "try_parse_plan",
]
