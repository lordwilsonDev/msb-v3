"""Sovereign Runtime — Event Bus.

Universal nervous system for inter-component communication.
Events are JSON with: type, payload, timestamp, agent_id, trace_id.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Event:
    """One bus event — type + payload + timestamp + agent/trace IDs.

    Trace correlation is per-emit (new uuid on publish). The bus records
    every event into history regardless of subscriber presence, so
    late-attach consumers can replay.
    """
    type: str
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    agent_id: str = "sovereign-agent-001"
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))


Handler = Callable[[Event], None]


class EventBus:
    """In-process publish/subscribe event bus.

    Not for production cross-process use — swap for Redis streams or NATS
    when scaling beyond a single container.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Handler]] = {}
        self._history: List[Event] = []
        self._lock = threading.RLock()

    def subscribe(self, event_type: str, handler: Handler) -> None:
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        with self._lock:
            handlers = self._subscribers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def emit(self, event_type: str, payload: Dict[str, Any]) -> Event:
        event = Event(type=event_type, payload=payload)
        with self._lock:
            self._history.append(event)
            for handler in list(self._subscribers.get(event_type, [])):
                handler(event)
        return event

    def history(self, event_type: Optional[str] = None) -> List[Event]:
        with self._lock:
            if event_type is None:
                return list(self._history)
            return [e for e in self._history if e.type == event_type]

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._subscribers.clear()
