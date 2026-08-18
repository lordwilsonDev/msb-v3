#!/usr/bin/env python3
'''MSB-BENCH-001 - formal /api/chat prefix-cache benchmark.

Measures the claim "Ollama's prefix cache eliminates the repeated-prefix
latency penalty" across repeated trials, instead of a single measurement:

- cold start (model load) then warm repeated calls on an identical long
  prompt prefix (only the tail differs), so the prefix cache is exercised;
- per call: prompt tokens, generated tokens, wall time, tokens/sec, and an
  inferred cache state (cold / warm / prefix-cached);
- environment: model, Ollama version, hardware (machine/processor/memory).

The report is written to experiments/reports/ and printed as a table. The
chat function is injectable (``chat_fn``) so the harness is testable without
a live server - INVARIANT-006 asserts the report shape from a fake.

Usage:  python experiments/benchmark_prefix_cache.py [model] [n_calls]
'''

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_N_CALLS = 6
DEFAULT_MAX_TOKENS = 32

# A long, repeated prefix (a few thousand tokens) so the prefix cache has
# real work to skip, with a unique tail per call so generation differs.
_PREFIX_SENTENCE = (
    "The sovereign evidence layer must keep an append-only, externally "
    "anchorable record of every governed decision, so that a third party "
    "holding only the receipt and the anchor can verify the action without "
    "trusting the box that produced it. "
)
_PREFIX_TOKENS = 40  # approximate tokens per sentence (honest estimate, ~3.5 chars/token)


def _long_prompt(paragraphs: int = 12) -> str:
    return (_PREFIX_SENTENCE * paragraphs).strip()


def _ollama_version(base_url: str) -> str:
    try:
        with urllib.request.urlopen(f"{base_url}/api/version", timeout=5) as resp:
            return str(json.loads(resp.read().decode())["version"])
    except Exception:
        return "unknown"


def _hardware() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "system": platform.system(),
        "python": platform.python_version(),
    }
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            info["memory_bytes"] = int(out)
        except Exception:
            pass
    return info


def _chat_ollama(
    base_url: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> Dict[str, Any]:
    '''One /api/chat call (real server). Returns the parsed fields plus the
    wall time measured by the caller via the returned dict.'''
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"num_predict": max_tokens},
    }
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())


def _infer_cache_state(index: int, wall_ms: float, first_wall_ms: float) -> str:
    '''Honest inference: call 1 (or a call where the model had to load) is
    cold; a later call whose wall time collapsed vs the cold call is
    prefix-cached; otherwise warm.'''
    if index == 0:
        return "cold"
    if wall_ms < 0.6 * first_wall_ms:
        return "prefix-cached"
    return "warm"


def run_benchmark(
    *,
    model: str = DEFAULT_MODEL,
    n_calls: int = DEFAULT_N_CALLS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    base_url: str = "http://localhost:11434",
    prompt: Optional[str] = None,
    chat_fn: Optional[Callable[[List[Dict[str, Any]], str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    '''Run N repeated /api/chat calls on an identical long prefix.

    ``chat_fn`` is injectable for tests: ``chat_fn(messages, model) -> dict``
    returning the parsed Ollama response (prompt_eval_count, eval_count,
    total_duration, eval_duration, load_duration). Defaults to the live
    server via ``_chat_ollama``.
    '''
    prompt = prompt or _long_prompt()
    chat_fn = chat_fn or (lambda messages, m: _chat_ollama(base_url, m, messages, max_tokens))

    calls: List[Dict[str, Any]] = []
    first_wall_ms: Optional[float] = None
    for index in range(n_calls):
        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": f"{prompt}\n\nTrial {index}: reply with the number {index}."}
        ]
        t0 = time.perf_counter()
        data = chat_fn(messages, model)
        wall_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        if first_wall_ms is None:
            first_wall_ms = wall_ms

        prompt_eval_count = int(data.get("prompt_eval_count") or 0)
        eval_count = int(data.get("eval_count") or 0)
        eval_duration_s = (data.get("eval_duration") or 0) / 1e9
        load_duration_ms = round((data.get("load_duration") or 0) / 1e6, 2)
        tokens_per_sec = round(eval_count / eval_duration_s, 1) if eval_duration_s > 0 else None

        calls.append(
            {
                "index": index,
                "wall_ms": wall_ms,
                "prompt_tokens": prompt_eval_count,
                "generated_tokens": eval_count,
                "tokens_per_sec": tokens_per_sec,
                "load_duration_ms": load_duration_ms,
                "cache_state": _infer_cache_state(index, wall_ms, first_wall_ms),
            }
        )

    cold = next((c for c in calls if c["cache_state"] == "cold"), calls[0])
    warm = [c for c in calls if c["cache_state"] != "cold"]
    warm_median_ms = sorted(c["wall_ms"] for c in warm)[len(warm) // 2] if warm else None
    speedup = round(cold["wall_ms"] / warm_median_ms, 1) if warm_median_ms else None

    report: Dict[str, Any] = {
        "benchmark": "MSB-BENCH-001",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "model": model,
            "ollama_version": _ollama_version(base_url),
            "hardware": _hardware(),
            "prompt_estimate_tokens": len(prompt.split()),
            "max_tokens": max_tokens,
            "n_calls": n_calls,
        },
        "calls": calls,
        "summary": {
            "cold_wall_ms": cold["wall_ms"],
            "warm_median_wall_ms": warm_median_ms,
            "prefix_cache_speedup_x": speedup,
            "cache_state_counts": {s: sum(1 for c in calls if c["cache_state"] == s) for s in {"cold", "warm", "prefix-cached"}},
        },
    }
    return report


def _render(report: Dict[str, Any]) -> str:
    meta = report["meta"]
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("MSB-BENCH-001 - /api/chat prefix-cache benchmark")
    lines.append("=" * 72)
    lines.append(
        f"model={meta['model']}  ollama={meta['ollama_version']}  "
        f"hw={meta['hardware'].get('machine')} ({meta['hardware'].get('processor')})"
    )
    lines.append(
        f"prompt_estimate_tokens={meta['prompt_estimate_tokens']}  max_tokens={meta['max_tokens']}  "
        f"n_calls={meta['n_calls']}"
    )
    lines.append("")
    lines.append(f"{'#':>2} {'wall_ms':>9} {'prompt_tok':>10} {'gen_tok':>7} {'tok/s':>8} {'load_ms':>8}  cache_state")
    for c in report["calls"]:
        lines.append(
            f"{c['index']:>2} {c['wall_ms']:>9.1f} {c['prompt_tokens']:>10} {c['generated_tokens']:>7} "
            f"{str(c['tokens_per_sec']):>8} {c['load_duration_ms']:>8.1f}  {c['cache_state']}"
        )
    s = report["summary"]
    lines.append("")
    lines.append(f"cold={s['cold_wall_ms']}ms  warm_median={s['warm_median_wall_ms']}ms  "
                 f"speedup={s['prefix_cache_speedup_x']}x  "
                 f"states={s['cache_state_counts']}")
    return "\n".join(lines) + "\n"


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MODEL
    n_calls = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_N_CALLS
    report = run_benchmark(model=model, n_calls=n_calls)
    print(_render(report))
    out_dir = Path(__file__).resolve().parent / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"benchmark_prefix_cache_{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"report written: {path}")


if __name__ == "__main__":
    main()
