"""Wake loop — the 5-minute resident agent.

A governed cron job (``wake-agent``, schedule ``MSB_WAKE_SCHEDULE``, default
``*/5 * * * *``) wakes the resident agent to process messages left in the
wake inbox from *any* session (POST /wake); responses land in the outbox
(GET /wake/outbox). The loop is bounded (``MSB_WAKE_MAX_PER_RUN`` per cycle)
and runs under the cron scheduler's kill switch / retries / timeout / receipt
discipline — the same governed path as every other scheduled action.

When a wake message asks the agent to build an automation, the runner hands
the plan to the automation brain (``msb_v3.automation``), which dry-runs by
default and creates via n8n / Make / Zapier / GoHighLevel on explicit
approval. See docs/wake-loop.md + docs/automation-brain.md.
"""

from msb_v3.wake.runner import ensure_wake_job, run_wake_cycle
from msb_v3.wake.store import WakeStore

__all__ = ["WakeStore", "ensure_wake_job", "run_wake_cycle"]
