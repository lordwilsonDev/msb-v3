"""Ops background router: ``GET /ops/background``.

One read-only snapshot of what runs behind the runtime (cron, governance,
automation, wake, PLEI), for the desktop cockpit's Background section.
Operator-gated like /cron and /wake, because job ids, budgets and inbox
depth are operational detail. There are no write routes here, and there must never be.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from msb_v3.api.auth import require_operator
from msb_v3.ops.background import SnapshotCache, build_snapshot

router = APIRouter(tags=["ops"])

_cache = SnapshotCache()


@router.get("/background", dependencies=[Depends(require_operator)])
async def background() -> Dict[str, Any]:
    """Per-subsystem state (ok / warn / fail / unknown) with detail."""
    return await _cache.get(lambda: build_snapshot())
