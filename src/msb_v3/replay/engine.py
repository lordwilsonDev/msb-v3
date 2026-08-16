"""ReplayEngine — event-sourced state reconstruction (completion blueprint Phase 3).

The audit chain and the task event log already tell *what happened*; the
ReplayEngine turns that into *reconstruction*: it derives a task's state from
its event sequence instead of trusting the stored projection, validates every
transition against the state machine, joins the Evidence Spine decision trail,
and surfaces divergence — a projection that has drifted from its events (or an
event sequence with an illegal transition) is a corruption signal, never
silently healed.

Scope note: ``replay_task`` / ``replay_decision`` / ``replay_state`` target the
unified task (tasks/lifecycle.py — the event-sourced unit every governed run
reduces to). ``replay_mission`` and ``replay_agent`` are not yet implemented:
missions and multi-agent orchestration are Phase 7, so there is no mission
entity to replay until that lands. Crash recovery (blueprint 3.2) builds on the
stores' existing ``recover_incomplete()`` (which quarantines in-flight tasks at
restart) plus the divergence detection here — the kill-at-every-point acceptance
test is the Phase 30 demonstration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from msb_v3.evidence.spine import DecisionEvidenceStore
from msb_v3.tasks.events import (
    RECOVERABLE_STATES,
    TaskLifecycleError,
    validate_transition,
)
from msb_v3.tasks.lifecycle import TaskLifecycle


class ReplayEngine:
    """Reconstruct and verify a task's causal story from its event log.

    ``lifecycle`` is the event-sourced task store. ``spine`` (optional) adds the
    decision-level vertebrae to ``replay_task`` / ``replay_decision``; it should
    be the same store the governed paths write to (``container.spine``) so the
    reconstruction covers both the lifecycle events and the decision chain.
    """

    def __init__(
        self,
        lifecycle: TaskLifecycle,
        *,
        spine: DecisionEvidenceStore | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._spine = spine

    # -- core -------------------------------------------------------------

    def replay_state(self, task_id: str) -> Dict[str, Any]:
        """Derive a task's state from its event sequence, not its projection.

        Walks the events in order, records each state change, validates every
        transition against the §28 state machine, and compares the derived
        final state to the stored projection. Returns ``consistent=False``
        when the projection has drifted from the events and ``legal=False``
        when the event sequence contains an illegal transition (both are
        corruption signals, never healed here).
        """
        record = self._lifecycle.get(task_id)
        events: List[Dict[str, Any]] = record["events"]
        derived: Optional[str] = None
        transitions: List[Dict[str, Any]] = []
        issues: List[str] = []
        legal = True
        for event in events:
            state = event.get("state")
            if not state:
                continue
            if state != derived:
                if derived is None:
                    transitions.append(
                        {"from_state": None, "to_state": state, "event_type": event["event_type"]}
                    )
                    if state != "CREATED":
                        legal = False
                        issues.append(f"first event state {state!r} is not CREATED")
                else:
                    transitions.append(
                        {
                            "from_state": derived,
                            "to_state": state,
                            "event_type": event["event_type"],
                        }
                    )
                    try:
                        validate_transition(derived, state)
                    except TaskLifecycleError as exc:
                        legal = False
                        issues.append(f"illegal transition {derived} -> {state}: {exc}")
                derived = state
        stored = record["state"]
        consistent = derived == stored
        result: Dict[str, Any] = {
            "task_id": task_id,
            "stored_state": stored,
            "derived_state": derived,
            "consistent": consistent,
            "legal": legal,
            "transitions": transitions,
            "event_count": len(events),
        }
        if issues:
            result["issues"] = issues
        if not consistent:
            result["divergence"] = f"stored {stored!r} != event-derived {derived!r}"
        return result

    def replay_decision(self, task_id: str) -> List[Dict[str, Any]]:
        """The Evidence Spine decision trail for one task (empty without a spine)."""
        if self._spine is None:
            return []
        return [record.as_dict() for record in self._spine.trail(task_id)]

    def replay_task(self, task_id: str) -> Dict[str, Any]:
        """Full reconstruction: derived state + ordered event timeline + the
        spine decision trail. This is the WHO/WHAT/WHEN answer the North-Star
        acceptance test (#10) requires — reconstructed from the event log and
        the spine, not from the projection."""
        state = self.replay_state(task_id)
        record = self._lifecycle.get(task_id)
        timeline = [
            {
                "event_id": e["event_id"],
                "event_type": e["event_type"],
                "state": e["state"],
                "audit_seq": e["audit_seq"],
                "created_at": e["created_at"],
            }
            for e in record["events"]
        ]
        return {
            **state,
            "timeline": timeline,
            "decisions": self.replay_decision(task_id),
        }

    def reconcile(self) -> Dict[str, Any]:
        """Replay every known task; report divergences and in-flight work.

        Divergences are tasks whose projection does not match their event-
        derived state (or whose events contain an illegal transition). In-flight
        tasks are those still in a recoverable state (EXECUTING/VERIFYING) —
        the ones ``recover_incomplete()`` quarantines after a restart.
        """
        tasks = self._lifecycle.list(limit=10_000)
        divergences: List[Dict[str, Any]] = []
        in_flight: List[str] = []
        for task in tasks:
            try:
                state = self.replay_state(task["task_id"])
            except TaskLifecycleError:
                continue  # task vanished between list and read — best effort
            if not state["consistent"] or not state["legal"]:
                divergences.append(state)
            if state["derived_state"] in RECOVERABLE_STATES:
                in_flight.append(task["task_id"])
        return {
            "task_count": len(tasks),
            "divergence_count": len(divergences),
            "divergences": divergences,
            "in_flight_count": len(in_flight),
            "in_flight": in_flight,
        }
