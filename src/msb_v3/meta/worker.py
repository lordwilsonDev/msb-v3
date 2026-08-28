"""Meta-System worker seam: MSL -> prompt -> model -> WorkerResult.

``render_prompt`` (W1) is built by qwen3:8b, verbatim. ``parse_worker_response``
(W2) escalated to the checker after two failed worker attempts — it is
regex-heavy and self-referential (code that strips ``<think>`` blocks, written
by a model emitting ``<think>`` blocks). ``call_ollama`` (I/O) is the
checker's. The model is injected: ``call_ollama`` is one implementation of the
``str -> str`` callable the driver takes, never a hard dependency.
"""

from __future__ import annotations

import json
import re
import urllib.request

from msb_v3.meta.contracts import MSL, WorkerResult, WorkerStatus

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


def render_prompt(msl: MSL) -> str:  # built by qwen3:8b (W1), verbatim
    parts = []
    if msl.objective:
        parts.append(f"objective: {msl.objective}")
    if msl.allowed_actions:
        parts.append(f"allowed actions: {', '.join(msl.allowed_actions)}")
    if msl.forbidden_actions:
        parts.append(f"forbidden actions: {', '.join(msl.forbidden_actions)}")
    if msl.constraints:
        constraints_lines = ["constraints:"]
        for key, value in msl.constraints.items():
            constraints_lines.append(f"  {key}: {value}")
        parts.append("\n".join(constraints_lines))
    if msl.verification_commands:
        parts.append(f"must pass: {', '.join(msl.verification_commands)}")
    final_line = "Output only the code, no prose, no markdown fences."
    return "\n\n".join(parts) + "\n\n" + final_line


def parse_worker_response(text: str, task_id: str, worker_id: str) -> WorkerResult:  # W2 — checker (escalated)
    body = re.sub(r"^\s*<think>.*?</think>", "", text, count=1, flags=re.DOTALL)
    fence = re.search(r"```(?:python)?\s*\n?(.*?)```", body, flags=re.DOTALL)
    code = (fence.group(1) if fence else body).strip()
    if code:
        return WorkerResult(
            task_id=task_id, worker_id=worker_id,
            status=WorkerStatus.PRODUCED, artifact_ref=code,
        )
    return WorkerResult(
        task_id=task_id, worker_id=worker_id,
        status=WorkerStatus.NO_CHANGE, artifact_ref="",
    )


def call_ollama(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_OLLAMA_URL,
    timeout: float = 600.0,
    think: bool = False,
) -> str:
    """One completion from a local Ollama model. Fail-closed: any transport or
    decode failure raises (the driver records it as a WorkerResult ERROR)."""
    body = {
        "model": model,
        "prompt": prompt if think else prompt + "\n\n/no_think",
        "stream": False,
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — local, injected
        payload = json.load(resp)
    return str(payload["response"])
