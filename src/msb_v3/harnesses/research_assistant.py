"""Long-horizon research harness — Phase 1 core."""
from __future__ import annotations

import datetime
import hashlib
import json
from abc import abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.harnesses.base import BaseHarness, HarnessResult


class SovereignResearchAssistant(BaseHarness):
    def __init__(
        self,
        topic: str,
        slug: Optional[str] = None,
        runtime_root: Optional[Path] = None,
        *,
        client: Any = None,
    ) -> None:
        self.topic = topic
        self.slug = slug or topic.lower().replace(" ", "-")[:48]
        self.runtime_root = Path(runtime_root or Path.cwd()) / "runtime" / "research" / self.slug
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.phases: List[Dict[str, Any]] = []
        self.guard_events: List[Dict[str, Any]] = []

    def _write(self, name: str, payload: Any) -> Path:
        p = self.runtime_root / f"{self.slug}_{name}"
        if isinstance(payload, (dict, list)):
            p.write_text(json.dumps(payload, indent=2) + "\n")
        else:
            p.write_text(str(payload) + "\n")
        return p

    def _sha256_file(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    def _local_client(self):
        if self.client is None:
            try:
                from msb_v3.local_ai.ollama import LocalAIClient
                self.client = LocalAIClient()
            except Exception:
                self.client = None
        return self.client

    def _generate(self, prompt: str) -> str:
        client = self._local_client()
        if client is None:
            return ""
        try:
            resp = client.execute_tool_loop(prompt)
            if resp and getattr(resp, "text", None):
                return resp.text.strip()
        except Exception:
            pass
        return ""

    def run_inversion(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"assumption": "", "inversion": "", "predictions": []}
        error: Optional[str] = None
        try:
            prompt = (
                "You are a research inversion assistant.\n"
                f"Topic: {self.topic}\n"
                "Return JSON with keys: assumption, inversion, predictions.\n"
                "assumption: one dominant claim to test.\n"
                "inversion: the opposite claim.\n"
                "predictions: a list of 3 measurable predictions."
            )
            text = self._generate(prompt)
            if not text:
                error = "no output from local AI client (unreachable, unconfigured, or empty response)"
            else:
                cleaned = text[text.find("{"): text.rfind("}") + 1]
                data = json.loads(cleaned)
                result = {
                    "assumption": data.get("assumption", ""),
                    "inversion": data.get("inversion", ""),
                    "predictions": data.get("predictions", []),
                }
                if not result["assumption"] and not result["inversion"]:
                    error = "model returned JSON but assumption/inversion were both empty"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        # A phase is only "ok" if it actually produced content — not merely
        # because nothing raised. A swallowed failure used to be
        # indistinguishable from a real result here.
        ok = error is None
        phase: Dict[str, Any] = {
            "phase": "inversion",
            "ok": ok,
            "result": result,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        if error is not None:
            phase["error"] = error
        self.phases.append(phase)
        self._write("UIM.json", {"topic": self.topic, "slug": self.slug, "phase1": result, "ok": ok, **({"error": error} if error else {})})
        return result

    def ground_evidence(self, sources: List[Path]) -> Dict[str, Any]:
        evidence: List[Dict[str, Any]] = []
        claims: List[Dict[str, Any]] = []
        for src in sources:
            record = {
                "path": str(src),
                "sha256": self._sha256_file(src),
                "bytes": src.stat().st_size,
            }
            evidence.append(record)
            claims.append({
                "source": record["sha256"],
                "status": "unknown",
                "text": src.read_text(errors="replace")[:1200],
            })
        payload = {
            "evidence": evidence,
            "claims": claims,
            "meta": {
                "source_count": len(evidence),
                "claim_count": len(claims),
            },
        }
        phase = {
            "phase": "evidence",
            "ok": True,
            "result": payload,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.phases.append(phase)
        self._write("evidence_ledger.json", payload)
        return payload

    def draft_report(self) -> Dict[str, Any]:
        uim: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}
        for p in self.phases:
            if p["phase"] == "inversion":
                uim = p["result"]
            elif p["phase"] == "evidence":
                evidence = p["result"]
        report = {
            "title": self.topic,
            "slug": self.slug,
            "abstract": f"Autonomous research brief for {self.topic}.",
            "background": "",
            "uim": uim,
            "methods": "AIL inversion + local evidence grounding + deterministic sha256 provenance.",
            "results": evidence.get("meta", {}),
            "discussion": "",
            "reproducibility": {
                "runtime_root": str(self.runtime_root),
                "artifacts": sorted(p.name for p in self.runtime_root.iterdir() if p.is_file()),
            },
            "guard_events": self.guard_events,
            "sovereignty_notes": "Local-first artifacts; no external publish step.",
        }
        phase = {
            "phase": "report",
            "ok": True,
            "result": report,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.phases.append(phase)
        self._write("report.md", report)
        self._write("review.md", {"status": "draft", "reviewed_at": ""})
        return report

    def record_completion(self) -> Dict[str, Any]:
        # "completed" used to be written unconditionally, even when a phase
        # (e.g. run_inversion) silently produced nothing — so a hollow run
        # and a real one were indistinguishable from status alone.
        failed_phases = [p["phase"] for p in self.phases if not p.get("ok", True)]
        status = "completed" if not failed_phases else "completed_with_errors"
        completion = {
            "topic": self.topic,
            "slug": self.slug,
            "phases": self.phases,
            "status": status,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        if failed_phases:
            completion["failed_phases"] = failed_phases
        self._write("completion.json", completion)
        self._write("state.json", {
            "topic": self.topic,
            "slug": self.slug,
            "phase": len(self.phases),
            "status": completion["status"],
            "updated_at": completion["ts"],
        })
        return completion

    def run_full_pipeline(self, sources: Optional[List[Path]] = None) -> Dict[str, Any]:
        self.run_inversion()
        self.ground_evidence(sources or [])
        self.draft_report()
        completion = self.record_completion()
        return completion

    def execute(
        self,
        query: str,
        context: Dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> HarnessResult:
        data = self.run_full_pipeline()
        text = json.dumps(data, indent=2)
        return HarnessResult(
            ok=data.get("status") == "completed",
            event="research:completed" if data.get("status") == "completed" else "research:completed_with_errors",
            payload={"query": query, "text": text},
            telemetry={"slug": self.slug},
        )

    def evidence_status(self) -> Dict[str, Any]:
        if not self.runtime_root.exists():
            return {"status": "empty", "slug": self.slug, "files": []}
        files = sorted(p.name for p in self.runtime_root.iterdir() if p.is_file())
        state = self.runtime_root / f"{self.slug}_state.json"
        status = "unknown"
        if state.exists():
            try:
                status = json.loads(state.read_text()).get("status", status)
            except Exception:
                pass
        return {"status": status, "slug": self.slug, "files": files}
