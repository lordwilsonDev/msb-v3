"""Real tool provider for the Handle-this slice — maps slice capabilities to
the live msb-v3 surfaces:

    search_query  -> RetrievalRouter (Qdrant semantic fabric, tenant-scoped)
    chat          -> ChatHarness (local model, metrics + fallback wired)
    vault_write   -> deterministic file write under the run's output dir

The output dir defaults to ~/Desktop/out (the slice's "client brief" target)
and is injectable for tests. The write returns {"path": ...} so the grounded
file_written verifier checks the actual artifact on disk.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict

from msb_v3.agent.dag import Task
from msb_v3.harnesses.base import ChatHarness
from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient


def _slug(request: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", (request or "").lower()).strip("-").split("-")
    return "-".join(words[:4]) or "brief"


def _format_sources(inputs: Dict[str, Any]) -> str:
    """Render parent search outputs into a synthesis prompt block."""
    lines: list[str] = []
    for parent_id, parent_output in inputs.items():
        for result in parent_output.values():
            if isinstance(result, list):
                for hit in result[:8]:
                    if isinstance(hit, dict):
                        text = str(hit.get("text") or hit.get("snippet") or "").strip()
                        src = str(hit.get("source") or hit.get("path") or "?")
                        if text:
                            lines.append(f"- [{src}] {text[:300]}")
            elif isinstance(result, dict) and isinstance(result.get("matches"), list):
                for hit in result["matches"][:8]:
                    text = str(hit.get("text") or hit.get("snippet") or "").strip()
                    if text:
                        lines.append(f"- {text[:300]}")
    return "\n".join(lines) if lines else "(no sources retrieved)"


def _extract_brief(inputs: Dict[str, Any]) -> str:
    """Pull the synthesis text out of the parent task outputs."""
    for parent_output in inputs.values():
        for value in parent_output.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict) and isinstance(value.get("text"), str) and value["text"].strip():
                return value["text"].strip()
    return ""


class BridgeProvider:
    """ToolProvider over the live msb-v3 surfaces.

    Phase 2: search goes through the fabric retrieval domains router
    (semantic/episodic/knowledge) and synthesis through the hybrid model
    router (frontier seam when configured, local otherwise). Both are
    injectable so tests stay hermetic and deterministic.
    """

    def __init__(
        self,
        *,
        tenant: str = "wilson-vault",
        output_dir: str | Path | None = None,
        client: LocalAIClient | LlamaCPPClient | None = None,
        router: Any | None = None,
        retrieval: Any | None = None,
    ) -> None:
        self._tenant = tenant
        self._output_dir = Path(output_dir) if output_dir else Path.home() / "Desktop" / "out"
        self._client = client
        self._router = router  # ModelRouter (None -> default hybrid)
        self._retrieval = retrieval  # FabricRetrievalRouter (None -> built lazily)

    async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
        if name == "search_query":
            return await self._search(task.goal)
        if name == "chat":
            return await asyncio.to_thread(self._synthesize, task, inputs)
        if name == "vault_write":
            return self._write(task, inputs)
        raise ValueError(f"unknown tool for the slice: {name}")

    async def _search(self, query: str) -> list[dict]:
        if self._retrieval is None:
            from msb_v3.fabric.retrieval_router import FabricRetrievalRouter

            self._retrieval = FabricRetrievalRouter(self._tenant)
        result = await self._retrieval.run(query, top_k=5)
        return result.matches

    def _synthesize(self, task: Task, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Synthesize via the routed client; return text + token usage so the
        executor's output carries the cost data (Phase 1: cost logged per run)."""
        sources = _format_sources(inputs)
        prompt = (
            f"{task.goal}\n\nSources from the vault:\n{sources}\n\n"
            "Write the brief now. Be concise, factual, and grounded only in the sources."
        )
        from msb_v3.fabric.model_router import resolve_client

        client, decision = resolve_client("verify_synth", client=self._client, router=self._router)
        if decision is not None and decision.tier == "frontier" and decision.available:
            # Frontier seam: call the routed client directly (a failure here
            # propagates to the executor's retry/fail path — never faked).
            resp = client.generate(
                prompt,
                system="You are a research brief writer.",
                temperature=0.2,
            )
            return {
                "text": resp.text,
                "prompt_tokens": int(getattr(resp, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(resp, "completion_tokens", 0) or 0),
            }
        # Local tier (or an injected test client): keep the ChatHarness path
        # so dispatcher metrics, latency histogram, and the [fallback]
        # degradation stay observable — the slice's telemetry contract.
        harness = ChatHarness(client=self._client)
        result = harness.execute(prompt, context={"system": "You are a research brief writer."})
        telemetry = result.telemetry or {}
        return {
            "text": result.payload.get("text", ""),
            "prompt_tokens": int(telemetry.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(telemetry.get("completion_tokens", 0) or 0),
        }

    def _write(self, task: Task, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Write the brief as a vault note starting with a # heading.

        Phase 1's canonical task is "produce a vault note and verify it exists
        with the expected heading" — so the note always leads with a markdown
        H1 derived from the goal, then the brief body. The grounded
        file_written_with_heading verifier reads the file back and checks the
        heading is actually there.
        """
        content = _extract_brief(inputs)
        title = _slug(task.goal).replace("-", " ").title() or "Brief"
        heading = f"# {title}"
        body = content.strip() or "(empty brief)"
        note = f"{heading}\n\n{body}\n"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / f"{_slug(task.goal)}.md"
        path.write_text(note)
        return {"path": str(path), "heading": heading}
