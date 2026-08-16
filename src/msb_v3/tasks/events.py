"""Task event vocabulary and lifecycle state machine (unified-architecture §28).

Every important state transition becomes an event; the AuditChain records
the authoritative sequence (component="tasks", event_type="task.<EVENT>").
The state machine is the projection that makes "where is this task?" a
first-class answer while the chain stays the tamper-evident record.
"""

from __future__ import annotations

# §28 event vocabulary — the canonical task events. Anything outside this
# set is rejected at the emit boundary (no free-form events into the chain).
TASK_EVENTS = frozenset(
    {
        "TASK_CREATED",
        "INTENT_INTERPRETED",
        "PLAN_CREATED",
        "CONTEXT_COMPOSED",
        "INVERSION_STARTED",
        "INVERSION_COMPLETED",
        "POLICY_CHECKED",
        "CONTRACT_APPROVED",
        "APPROVAL_GRANTED",
        "AGENT_STARTED",
        "AGENT_COMPLETED",
        "TOOL_REQUESTED",
        "TOOL_EXECUTED",
        "MUTATION_COMMITTED",
        "OBSERVATION_RECORDED",
        "VERIFICATION_STARTED",
        "VERIFICATION_PASSED",
        "VERIFICATION_FAILED",
        "EVIDENCE_RECORDED",
        "AUDIT_COMMITTED",
        "MEMORY_STORED",
        "MEMORY_CONSOLIDATED",
        "TASK_COMPLETED",
        "TASK_FAILED",
        "TASK_QUARANTINED",
        "TASK_DENIED",
        "TASK_RECOVERED",
    }
)

# Lifecycle states — the projection of the event stream.
TASK_STATES = (
    "CREATED",
    "PLANNED",
    "EXECUTING",
    "VERIFYING",
    "COMPLETED",
    "FAILED",
    "QUARANTINED",
    "DENIED",
)

_ALLOWED_TRANSITIONS = {
    "CREATED": {"PLANNED", "DENIED", "QUARANTINED"},
    "PLANNED": {"EXECUTING", "DENIED", "QUARANTINED"},
    "EXECUTING": {"VERIFYING", "FAILED", "QUARANTINED"},
    "VERIFYING": {"COMPLETED", "FAILED", "QUARANTINED"},
    "COMPLETED": set(),
    "FAILED": set(),
    "QUARANTINED": set(),
    "DENIED": set(),
}

# State transitions map onto the §28 event vocabulary (a transition emits
# its canonical event). Informational events (TOOL_EXECUTED, ...) never
# change state.
STATE_EVENT = {
    "PLANNED": "PLAN_CREATED",
    "EXECUTING": "AGENT_STARTED",
    "VERIFYING": "VERIFICATION_STARTED",
    "COMPLETED": "TASK_COMPLETED",
    "FAILED": "TASK_FAILED",
    "QUARANTINED": "TASK_QUARANTINED",
    "DENIED": "TASK_DENIED",
}

# States that mean "work was in flight" — recovered (quarantined) on restart
# so in-flight work is never silently resumed or silently dropped.
RECOVERABLE_STATES = frozenset({"EXECUTING", "VERIFYING"})


class TaskLifecycleError(ValueError):
    """Raised on an invalid event, unknown task, or illegal transition."""


def validate_transition(from_state: str, to_state: str) -> None:
    if to_state not in _ALLOWED_TRANSITIONS:
        raise TaskLifecycleError(f"unknown task state: {to_state}")
    if to_state not in _ALLOWED_TRANSITIONS.get(from_state, set()):
        raise TaskLifecycleError(f"invalid transition {from_state} -> {to_state}")


def event_for_state(to_state: str) -> str:
    event = STATE_EVENT.get(to_state)
    if event is None:
        raise TaskLifecycleError(f"state has no canonical event: {to_state}")
    return event
