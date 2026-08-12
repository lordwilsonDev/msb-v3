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
    """ToolProvider over the live msb-v3 surfaces."""

    def __init__(
        self,
        *,
        tenant: str = "wilson-vault",
        output_dir: str | Path | None = None,
        client: LocalAIClient | LlamaCPPClient | None = None,
    ) -> None:
        self._tenant = tenant
        self._output_dir = Path(output_dir) if output_dir else Path.home() / "Desktop" / "out"
        self._client = client

    async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
        if name == "search_query":
            return await self._search(task.goal)
        if name == "chat":
            return await asyncio.to_thread(self._synthesize, task, inputs)
        if name == "vault_write":
            return self._write(task, inputs)
        raise ValueError(f"unknown tool for the slice: {name}")

    async def _search(self, query: str) -> list[dict]:
        from msb_v3.retrieval.engine import RetrievalRouter

        router = RetrievalRouter(self._tenant)
        result = await router.run(query, top_k=5)
        return result.get("matches", [])

    def _synthesize(self, task: Task, inputs: Dict[str, Any]) -> str:
        sources = _format_sources(inputs)
        prompt = (
            f"{task.goal}\n\nSources from the vault:\n{sources}\n\n"
            "Write the brief now. Be concise, factual, and grounded only in the sources."
        )
        harness = ChatHarness(client=self._client)
        result = harness.execute(prompt, context={"system": "You are a research brief writer."})
        return result.payload.get("text", "")

    def _write(self, task: Task, inputs: Dict[str, Any]) -> Dict[str, Any]:
        content = _extract_brief(inputs)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / f"{_slug(task.goal)}.md"
        path.write_text(content or "# (empty brief)\n")
        return {"path": str(path)}
