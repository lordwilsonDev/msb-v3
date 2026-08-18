"""Structured audit log — the canonical event stream for governed runs.

One JSON object per ``handle()`` cycle, appended atomically to
``logs/audit.jsonl``. This is the connective tissue between the metrics
(aggregate counters), the audit chain (append-only proof-of-inclusion), and
the replay engine (per-run reconstruction): every line is a full evidence
receipt, so the receipt, the replay, and the aggregate counters are all
derivable from this one stream instead of drifting apart.

Best-effort and fail-open by design: observability must never break the run
it describes. A write failure logs a warning and returns; the audit chain
remains the authoritative record (this stream is a *projection*, not the
source of truth).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)


def audit_log_path() -> Path:
    """The resolved JSONL path (env override or the repo's logs/audit.jsonl).

    A function, not a module constant, so callers and tests observe the same
    value the emitter uses even when MSB_AUDIT_LOG_PATH is set after import.
    """
    return Path(settings.audit_log_path)


def append_receipt(receipt: Dict[str, Any]) -> None:
    """Append one receipt as a single JSON line. Never raises: a failure to
    write observability must not fail the run it describes."""
    path = audit_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(receipt, sort_keys=True, default=str) + "\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        logger.warning("audit log append failed (%s): %s", path, exc)
