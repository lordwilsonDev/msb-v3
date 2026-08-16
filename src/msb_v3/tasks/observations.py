"""Observation pub/sub bus (SSE live streaming).

Worker activity streams into a unified task via the lifecycle sink
(``OBSERVATION_RECORDED`` + the §27 observations section) — that is the
durable record. This module adds the *live* channel: a process-wide
registry of per-task subscriber queues that the sink also publishes to, so
dashboards can watch a run as it happens without polling.

Design notes:

- Bounded queues (``_QUEUE_MAX``): a slow subscriber is dropped (oldest
  sample discarded) rather than stalling the run with backpressure.
- Module-level registry — the same single-process-uvicorn assumption the
  permission wait registry uses (see agent/paseo/permissions.py). A
  multi-process deployment would need a cross-process channel; out of scope.
- Best-effort by construction: ``publish`` never raises; a subscriber that
  disappears mid-stream is removed on the next publish.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_QUEUE_MAX = 100

# task_id -> list of subscriber queues (process-wide).
_SUBSCRIBERS: Dict[str, List[asyncio.Queue]] = {}


def subscribe(task_id: str) -> asyncio.Queue:
    """Register a subscriber queue for a task's live observation stream."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _SUBSCRIBERS.setdefault(task_id, []).append(queue)
    return queue


def unsubscribe(task_id: str, queue: asyncio.Queue) -> None:
    """Remove a subscriber queue (idempotent; safe on disconnect)."""
    queues = _SUBSCRIBERS.get(task_id)
    if not queues:
        return
    try:
        queues.remove(queue)
    except ValueError:
        return
    if not queues:
        _SUBSCRIBERS.pop(task_id, None)


def subscriber_count(task_id: str) -> int:
    """Number of live subscribers for a task (tests + observability)."""
    return len(_SUBSCRIBERS.get(task_id, []))


async def publish(task_id: str, sample: Dict[str, Any]) -> None:
    """Push one observation sample to every live subscriber.

    Best-effort: a full queue drops its oldest sample (the stream stays
    live, the run never stalls); a stale subscriber that raises on put is
    removed. Never raises.
    """
    queues = list(_SUBSCRIBERS.get(task_id, []))
    for queue in queues:
        try:
            if queue.full():
                try:
                    queue.get_nowait()  # drop oldest
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(sample)
        except Exception as exc:  # noqa: BLE001 — never break the run for a watcher
            logger.debug("dropping observation subscriber for %s: %s", task_id, exc)
            unsubscribe(task_id, queue)
