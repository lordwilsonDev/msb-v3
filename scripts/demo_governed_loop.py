#!/usr/bin/env python3
"""Governed-loop demo — one run shows the canonical journey end to end.

Runs the REAL ``handle()`` path twice with canned client + tool outputs (no
model, no network, no vault required — deterministic on any machine):

  RUN 1 · DANGEROUS  "rm -rf production"
      The real MoIE policy (config/risk_templates.json) BLOCKs the danger
      keyword, so the request is DENIED before any model call. A DENY
      decision vertebra lands on the Evidence Spine and the receipt lands
      in the audit stream with 0 model calls.

  RUN 2 · SAFE  "research the vault and write a client brief"
      MoIE APPROVEs, the plan-approval is ALLOWed, the deterministic
      template DAG runs (research -> synthesize -> write), and each task is
      verified against ground truth (search hits, non-empty synthesis, file
      written with heading). The receipt records the grounded checks
      (verified=rerun) and the deterministic hash recomputes from the trace.

The governance loop, the MoIE policy, the Evidence Spine, the audit stream,
and the grounded verification are all REAL. Only the model and tool outputs
are canned, so the demo is reproducible in five minutes with zero setup.

Usage:
    python scripts/demo_governed_loop.py            # receipts in a temp log
    python scripts/demo_governed_loop.py --persist  # also append to the
                                                    # live logs/audit.jsonl
                                                    # (refresh /cockpit/audit
                                                    # to see the receipts in
                                                    # the Evidence Stream)

Exit code 0 when both receipts are produced, the dangerous run made zero
model calls, and the allowed run's deterministic hash recomputes from its
recorded trace. Importable: ``await run_demo(tmp_path)`` returns the results
+ receipts (pinned by tests/demo/test_demo_governed_loop.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from msb_ledger import audit_chain as _audit_chain_module  # noqa: E402
from msb_ledger.audit_chain import AuditChain  # noqa: E402
from msb_v3.agent.handle import handle  # noqa: E402
from msb_v3.agent.safety import ActionGate  # noqa: E402
from msb_v3.core.config import settings  # noqa: E402
from msb_v3.evidence.spine import DecisionEvidenceStore  # noqa: E402
from msb_v3.observability.audit_log import append_receipt  # noqa: E402

DANGEROUS = "rm -rf production"
SAFE = "research the vault and write a client brief"

_INTENT_JSON = (
    '{"goals": ["research the vault"], "constraints": [], '
    '"permissions": ["read_vault", "write_file"], "privacy": true, "domain": "client-brief"}'
)


# --- doubles (the same doubles the contract suite uses) ---------------------


class _Audit:
    """Audit-chain double: records appends instead of touching the live UAC
    chain, so the demo's gate records are local to the run."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def append(self, component: str, event_type: str, payload: Dict[str, Any]) -> None:
        self.events.append((component, event_type, payload))


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "canned"
        self.latency_s = 0.0
        self.tool_calls = []


class _CannedClient:
    """Model double: the intent JSON first, then garbage so the planner falls
    back to the deterministic template DAG."""

    def __init__(self) -> None:
        self._texts = [_INTENT_JSON, "garbage"]

    def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
        text = self._texts.pop(0) if self._texts else "garbage"
        return _Resp(text)


class _CannedProvider:
    """Tool double: search/chat/write return canned outputs. The governance
    loop around these tools is real."""

    def __init__(self, output_dir: Path) -> None:
        self._out = output_dir

    async def run_tool(self, name: str, *, task, inputs: Dict[str, Any], session: str) -> Any:
        if name == "search_query":
            return [{"text": "source one", "source": "vault/a.md"}]
        if name == "chat":
            return "The client-ready brief."
        if name == "vault_write":
            self._out.mkdir(parents=True, exist_ok=True)
            path = self._out / "brief.md"
            path.write_text("# Brief\n\nbrief\n", encoding="utf-8")
            return {"path": str(path), "heading": "# Brief"}
        raise ValueError(f"unknown tool: {name}")


# --- the demo ---------------------------------------------------------------


async def run_demo(tmp: Path) -> Dict[str, Any]:
    """Run the blocked + allowed pair through the REAL handle() path.

    Canned client + provider (no model, no network, no vault); real MoIE
    policy, real Evidence Spine, real audit stream. Receipts land in
    ``tmp/audit.jsonl``, the spine in ``tmp/spine.db``, and the audit chain
    in ``tmp/uac/audit_chain.db`` — the demo touches NO real production
    state (the same isolation the test suite uses: the default chain is
    pointed at a scratch file, because the live chain is anchor-protected
    and a bare process must not append to it unanchored).

    Restores ``settings.audit_log_path`` and the chain DB path afterwards so
    the caller's stream is untouched.
    """
    tmp.mkdir(parents=True, exist_ok=True)
    audit_log = tmp / "audit.jsonl"
    spine = DecisionEvidenceStore(str(tmp / "spine.db"))
    gate = ActionGate(audit_chain=_Audit())
    provider = _CannedProvider(tmp / "out")

    saved_log = settings.audit_log_path
    saved_audit_db = _audit_chain_module._AUDIT_DB
    settings.audit_log_path = str(audit_log)
    _audit_chain_module._AUDIT_DB = tmp / "uac" / "audit_chain.db"
    try:
        blocked = await handle(
            DANGEROUS,
            client=_CannedClient(),
            approve=True,
            provider=provider,
            gate=gate,
            spine=spine,
        )
        allowed = await handle(
            SAFE,
            client=_CannedClient(),
            approve=True,
            provider=provider,
            gate=gate,
            spine=spine,
        )
    finally:
        settings.audit_log_path = saved_log
        _audit_chain_module._AUDIT_DB = saved_audit_db

    receipts = [json.loads(ln) for ln in audit_log.read_text().splitlines() if ln.strip()]
    by_id = {r["request_id"]: r for r in receipts}
    chain = AuditChain(db_path=tmp / "uac" / "audit_chain.db")
    return {
        "tmp": tmp,
        "audit_log": audit_log,
        "spine": spine,
        "chain": chain,
        "chain_verify": chain.verify_chain(),
        "blocked": blocked,
        "allowed": allowed,
        "receipts": receipts,
        "blocked_receipt": by_id.get(blocked.run_id),
        "allowed_receipt": by_id.get(allowed.run_id),
    }


def _render(out: Dict[str, Any]) -> str:
    blocked, allowed = out["blocked"], out["allowed"]
    b, a = out["blocked_receipt"], out["allowed_receipt"]
    lines: list[str] = [
        "═══ MSB v3 — GOVERNED LOOP DEMO ═══════════════════════════════════",
        "",
        "The real handle() path with canned client + tool outputs (no model,",
        "no network, no vault needed). The MoIE policy, Evidence Spine, audit",
        "stream, and grounded verification are all real.",
    ]

    lines += ["", "─── RUN 1 · DANGEROUS ─────────────────────────────────────"]
    lines.append(f"  request       {DANGEROUS}")
    lines.append(f"  moie verdict  {b['moie_verdict']}")
    lines.append("  decision      DENY — denied before any model call")
    lines.append(f"  execution     none · {blocked.model_calls} model calls")
    lines.append(f"  verification  {b['verification']['basis']} — nothing rerun; the DENY vertebra is the evidence")
    lines.append(f"  evidence      run_id {blocked.run_id}")
    if b.get("audit_hash"):
        lines.append(f"                audit_hash {b['audit_hash']}")
    lines.append(f"  receipt       {b['reconstruction']}")

    lines += ["", "─── RUN 2 · SAFE ─────────────────────────────────────────"]
    lines.append(f"  request       {SAFE}")
    lines.append(f"  moie verdict  {a['moie_verdict']}")
    granted = ", ".join(a.get("capability_granted") or [])
    lines.append(f"  decision      {a['authorization_decision']} — capabilities [{granted}]")
    tasks = [t.get("task_id") for t in (allowed.trace or {}).get("tasks") or []]
    lines.append(f"  execution     {' → '.join(tasks)}" if tasks else "  execution     —")
    checks = a.get("verification", {}).get("grounded_checks") or []
    if checks:
        lines.append("  verification  rerun — " + " · ".join(f"{c['check']}:{c['verdict']}" for c in checks))
        match = "MATCH" if a["verification"].get("hash_recomputed") else "MISMATCH"
        lines.append(f"                deterministic hash recomputed from trace: {match}")
    lines.append(f"  evidence      run_id {allowed.run_id}")
    if a.get("audit_hash"):
        lines.append(f"                audit_hash {a['audit_hash']}")
    lines.append(f"  receipt       {a['reconstruction']}")

    ok = (
        blocked.verdict == "BLOCKED"
        and blocked.model_calls == 0
        and allowed.verdict == "PASS"
        and len(out["receipts"]) == 2
        and b is not None
        and a is not None
        and b["authorization_decision"] == "DENY"
        and a["authorization_decision"] == "ALLOW"
        and a["verification"]["hash_recomputed"] is True
    )

    chain_valid = bool(out["chain_verify"].get("valid"))
    lines += ["", "─── RESULT ───────────────────────────────────────────────"]
    lines.append(f"  {'✅' if blocked.verdict == 'BLOCKED' and blocked.model_calls == 0 else '❌'} dangerous run blocked ({blocked.verdict}) with 0 model calls")
    lines.append(f"  {'✅' if allowed.verdict == 'PASS' else '❌'} safe run allowed ({allowed.verdict}) with {len(checks)} grounded checks")
    lines.append(f"  {'✅' if a and a['verification']['hash_recomputed'] else '❌'} deterministic hash recomputes from the recorded trace")
    lines.append(f"  {'✅' if len(out['receipts']) == 2 else '❌'} both receipts in the audit stream ({len(out['receipts'])} lines)")
    lines.append(f"  {'✅' if chain_valid else '❌'} audit chain (scratch) verifies — {out['chain_verify'].get('record_count', 0)} records")
    lines.append("")
    lines.append(f"  receipts written to {out['audit_log']}")
    lines.append("  view them:  cat " + str(out["audit_log"]))
    lines.append("  live view:  /cockpit/audit  (re-run with --persist to append to the real stream)")
    lines.append("")
    lines.append("DEMO " + ("PASSED" if ok else "FAILED"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Governed-loop demo: block a dangerous action, allow a safe one, prove both.")
    parser.add_argument(
        "--persist",
        action="store_true",
        help="also append the receipts to the live logs/audit.jsonl (shows in the cockpit Evidence Stream)",
    )
    args = parser.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="msb-demo-"))
    out = asyncio.run(run_demo(tmp))
    print(_render(out))

    if args.persist:
        for rec in out["receipts"]:
            append_receipt(rec)  # settings.audit_log_path was restored by run_demo
        print(f"\n--persist: appended {len(out['receipts'])} receipts to {settings.audit_log_path} — refresh /cockpit/audit")

    blocked, allowed = out["blocked"], out["allowed"]
    a = out["allowed_receipt"]
    ok = (
        blocked.verdict == "BLOCKED"
        and blocked.model_calls == 0
        and allowed.verdict == "PASS"
        and len(out["receipts"]) == 2
        and a is not None
        and a["verification"]["hash_recomputed"] is True
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
