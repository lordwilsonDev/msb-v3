"""Meta-System worker seam: MSL -> prompt -> model -> WorkerResult.

``render_prompt`` (W1) is built by qwen3:8b, verbatim. ``parse_worker_response``
(W2) escalated to the checker after two failed worker attempts — it is
regex-heavy and self-referential (code that strips ``<think>`` blocks, written
by a model emitting ``<think>`` blocks). ``call_ollama``/``call_mlx`` (I/O) are
the checker's. The model is injected: both are implementations of the
``str -> str`` callable the driver (``meta.loop.ModelCall``) takes, never a
hard dependency — swap the worker model without touching the loop.
"""

from __future__ import annotations

import json
import re
import urllib.request

from msb_v3.meta.contracts import MSL, WorkerResult, WorkerStatus

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MLX_MODEL = "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"

# Loaded MLX (model, tokenizer) pairs, keyed by repo id. Weight loading is
# the expensive part (~seconds); caching means only the first call_mlx per
# model per process pays it. Not thread-safe by construction — the meta
# loop calls model_call sequentially (see build_module).
_mlx_cache: dict = {}


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


def call_mlx(
    prompt: str,
    *,
    model: str = DEFAULT_MLX_MODEL,
    max_tokens: int = 2048,
) -> str:
    """One completion from a local MLX model — in-process generation on
    Apple Silicon (no daemon/HTTP round trip, unlike call_ollama). ``mlx_lm``
    is imported lazily (Apple-Silicon-only; not a hard dependency of this
    module, so importing msb_v3.meta.worker stays portable to Linux CI).
    Fail-closed: any import/load/generate failure raises (the driver records
    it as a WorkerResult ERROR — same contract as call_ollama). No
    Qwen3-style ``/no_think`` suffix: Qwen2.5-Coder has no think mode."""
    from mlx_lm import generate, load

    if model not in _mlx_cache:
        _mlx_cache[model] = load(model)
    model_obj, tokenizer = _mlx_cache[model]

    messages = [{"role": "user", "content": prompt}]
    rendered = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
    return str(generate(model_obj, tokenizer, prompt=rendered, max_tokens=max_tokens, verbose=False))
