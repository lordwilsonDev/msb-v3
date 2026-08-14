#!/usr/bin/env python3
"""MSB-GOV-EVAL-001 — Sovereignty / Cloud-Outage Experiment (blueprint §15-§17; H4).

Phase A (connected): inventory every capability and classify FULL.
Phase B (cloud removed): inject a cloud-outage at the CLIENT BOUNDARY
    (external-search endpoint repointed to an unreachable address — exactly
    what MSB observes when the cloud API is down or the network is severed)
    and re-run the SAME workload. Verify sovereign capabilities remain.
Phase C (restore): revert the injection, verify recovery.

Scope note (documented, not hidden): full OS-level network severance is OUT
of scope for this run because it would drop the live WireGuard tunnel and
Vesta service mid-session. The client-boundary injection reproduces the same
observable (connection refused / timeout) without collateral damage.

Capability weights are FROZEN here, before measurement (blueprint §16:
"define weights before the experiment"):

    C1 inference         0.25   LocalAIClient -> ollama (local)
    C2 memory/retrieval  0.15   qdrant vector store (local)
    C3 task planning     0.15   planner over local model (local)
    C4 task execution    0.15   FileWriter + AuditChain (local)
    C5 audit             0.10   AuditChain append + verify (local)
    C6 evidence          0.10   precondition-hash gate (local)
    C7 local storage     0.05   sqlite (local)
    C8 external search   0.05   Tavily API (CLOUD)

Classification scores: FULL=1.0, DEGRADED=0.5, UNAVAILABLE=0.0, UNSAFE=0.0.
CRR = sum(weight * score) / sum(weight of online-available capabilities).

Usage:
    python3 experiments/harness_sovereignty.py

Evidence: runs/<date>/raw/sovereignty_<ts>.json + results/sovereignty.csv
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path.home() / ".local/lib/msb-v3"))


def _load_env() -> None:
    """Load the repo .env exactly like run.sh does (set -a; . ./.env; set +a),
    so standalone harness runs see the same config as the live service.
    Values are never printed."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env()

import httpx  # noqa: E402

from msb_v3.local_ai.ollama import LocalAIClient  # noqa: E402
from msb_v3.node.filesystem import CapabilityViolation, FileWriter  # noqa: E402
from msb_v3.uac.audit_chain import AuditChain  # noqa: E402
from msb_v3.uac.research_backend import (  # noqa: E402
    ResearchBackendError,
    TavilyResearchBackend,
)
from msb_v3.harnesses.base import ChatHarness  # noqa: E402

RUN_ROOT = Path(__file__).resolve().parent
DEFAULT_RUN = RUN_ROOT / "runs" / datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ── Frozen capability weights (blueprint §16) ────────────────────────────────
WEIGHTS = {
    "C1_inference": 0.25,
    "C2_memory": 0.15,
    "C3_planning": 0.15,
    "C4_task_execution": 0.15,
    "C5_audit": 0.10,
    "C6_evidence": 0.10,
    "C7_local_storage": 0.05,
    "C8_external_search": 0.05,
}
SCORES = {"FULL": 1.0, "DEGRADED": 0.5, "UNAVAILABLE": 0.0, "UNSAFE": 0.0}

# Cloud-outage injection: repoint the external search endpoint at an
# unreachable address (client-boundary removal of the cloud dependency).
BLACKHOLE_URL = "http://127.0.0.1:9/search"


def probe_c1_inference() -> dict:
    client = LocalAIClient()
    try:
        resp = client.generate("Reply with exactly one word: ok")
        text = resp.text
        ok = bool(text)
        return {"classification": "FULL" if ok else "UNAVAILABLE", "detail": f"reply_len={len(text)}"}
    except Exception as exc:
        return {"classification": "UNAVAILABLE", "detail": f"{type(exc).__name__}: {exc}"}


def probe_c2_memory() -> dict:
    try:
        r = httpx.get("http://127.0.0.1:6333/collections", timeout=3)
        ok = r.status_code == 200
        n_collections = len(r.json().get("result", {}).get("collections", []))
        return {"classification": "FULL" if ok else "UNAVAILABLE",
                "detail": f"qdrant_ok={ok} collections={n_collections}"}
    except Exception as exc:
        return {"classification": "UNAVAILABLE", "detail": f"{type(exc).__name__}: {exc}"}


def probe_c3_planning() -> dict:
    # planner runs over the LOCAL model; probe is a planning-shaped local call
    client = LocalAIClient()
    try:
        resp = client.generate("Plan one step to verify local availability. Reply with one word: ok")
        text = resp.text
        return {"classification": "FULL" if bool(text) else "UNAVAILABLE", "detail": f"reply_len={len(text)}"}
    except Exception as exc:
        return {"classification": "UNAVAILABLE", "detail": f"{type(exc).__name__}: {exc}"}


def probe_c4_task_execution(chain: AuditChain, sandbox: Path) -> dict:
    w = FileWriter(sandbox, max_bytes=1_048_576)
    try:
        w.write("task.txt", b"sovereign task artifact")
        chain.append("sovereignty", "task_executed", {"path": "task.txt"})
        ok = (sandbox / "task.txt").read_bytes() == b"sovereign task artifact"
        return {"classification": "FULL" if ok else "UNAVAILABLE", "detail": f"write+audit_ok={ok}"}
    except Exception as exc:
        return {"classification": "UNAVAILABLE", "detail": f"{type(exc).__name__}: {exc}"}


def probe_c5_audit(chain: AuditChain) -> dict:
    try:
        chain.append("sovereignty", "audit_probe", {"phase_probe": True})
        valid = chain.verify_chain().get("valid", False)
        return {"classification": "FULL" if valid else "UNAVAILABLE", "detail": f"chain_valid={valid}"}
    except Exception as exc:
        return {"classification": "UNAVAILABLE", "detail": f"{type(exc).__name__}: {exc}"}


def probe_c6_evidence(sandbox: Path) -> dict:
    w = FileWriter(sandbox, max_bytes=1_048_576)
    target = sandbox / "doc.txt"
    target.write_bytes(b"original")
    try:
        w.write("doc.txt", b"changed", expected_sha256="f" * 64)  # wrong precondition
        return {"classification": "UNSAFE", "detail": "evidence gate ALLOWED wrong precondition"}
    except CapabilityViolation:
        # gate denied the bad evidence AND the target is unmutated — verifier works
        unmutated = target.read_bytes() == b"original"
        return {"classification": "FULL" if unmutated else "UNSAFE",
                "detail": f"precondition_mismatch_denied unmutated={unmutated}"}


def probe_c7_local_storage() -> dict:
    work = Path(tempfile.mkdtemp(prefix="gov-sov-store-"))
    try:
        with sqlite3.connect(work / "local.db") as conn:
            conn.execute("CREATE TABLE t (k TEXT PRIMARY KEY, v TEXT)")
            conn.execute("INSERT INTO t VALUES ('k','v')")
            got = conn.execute("SELECT v FROM t WHERE k='k'").fetchone()[0]
        return {"classification": "FULL" if got == "v" else "UNAVAILABLE", "detail": "sqlite_roundtrip_ok"}
    except Exception as exc:
        return {"classification": "UNAVAILABLE", "detail": f"{type(exc).__name__}: {exc}"}


def probe_c8_external_search() -> dict:
    try:
        backend = TavilyResearchBackend(timeout_s=20)
        results = backend.search("MSB governance evaluation", max_results=2)
        n = len(results)
        return {"classification": "FULL" if n > 0 else "UNAVAILABLE", "detail": f"results={n}"}
    except ResearchBackendError as exc:
        # FAIL-LOUD design: raised, never silent-empty (uac/research_backend.py)
        return {"classification": "UNAVAILABLE", "detail": f"ResearchBackendError: {str(exc)[:120]}"}
    except Exception as exc:
        return {"classification": "UNAVAILABLE", "detail": f"{type(exc).__name__}: {str(exc)[:120]}"}


def probe_local_model_degradation() -> dict:
    """§8 local_model row: ChatHarness must degrade in a CONTROLLED way
    (chat:fallback event), never crash or silently fake a real answer."""
    client = LocalAIClient(base_url="http://127.0.0.1:9")  # unreachable -> outage
    harness = ChatHarness(client=client)
    result = harness.execute("say ok")
    is_fallback = result.ok and "fallback" in result.payload.get("text", "")
    event = result.event
    return {
        "classification": "DEGRADED" if is_fallback else "UNSAFE",
        "detail": f"event={event} ok={result.ok} text={result.payload.get('text','')[:40]!r}",
    }


PROBES = {
    "C1_inference": probe_c1_inference,
    "C2_memory": probe_c2_memory,
    "C3_planning": probe_c3_planning,
    "C4_task_execution": probe_c4_task_execution,
    "C5_audit": probe_c5_audit,
    "C6_evidence": probe_c6_evidence,
    "C7_local_storage": probe_c7_local_storage,
    "C8_external_search": probe_c8_external_search,
}


def run_phase(chain: AuditChain, sandbox: Path) -> dict:
    results = {}
    for name, fn in PROBES.items():
        try:
            if name in ("C4_task_execution", "C5_audit"):
                r = fn(chain, sandbox) if name == "C4_task_execution" else fn(chain)
            elif name == "C6_evidence":
                r = fn(sandbox)
            else:
                r = fn()
        except Exception as exc:  # harness-level catch — never let one probe mask others
            r = {"classification": "SYSTEM_ERROR", "detail": f"{type(exc).__name__}: {exc}"}
        results[name] = r
    return results


def crr(phase_results: dict) -> dict:
    available = sum(WEIGHTS.values())
    remaining = sum(WEIGHTS[k] * SCORES.get(phase_results[k]["classification"], 0.0) for k in WEIGHTS)
    return {
        "crr": round(remaining / available, 4) if available else None,
        "weighted_remaining": round(remaining, 4),
        "weighted_available": round(available, 4),
    }


def main() -> int:
    run_dir = Path(sys.argv[sys.argv.index("--run-dir") + 1]) if "--run-dir" in sys.argv else DEFAULT_RUN
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "results").mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    work = Path(tempfile.mkdtemp(prefix="gov-sov-"))
    sandbox = work / "sandbox"
    sandbox.mkdir()
    chain = AuditChain(str(work / "audit.db"))

    import msb_v3.uac.research_backend as rb_mod

    # ── Phase A: connected baseline ───────────────────────────────────────────
    print("Phase A (connected)...")
    phase_a = run_phase(chain, sandbox)
    for name, r in phase_a.items():
        print(f"  {name:16s} {r['classification']:12s} {r['detail']}")

    # ── Phase B: cloud-outage injection at the client boundary ────────────────
    print("\nPhase B (cloud removed: external-search endpoint -> blackhole)...")
    original_url = rb_mod._TAVILY_URL
    rb_mod._TAVILY_URL = BLACKHOLE_URL
    try:
        phase_b = run_phase(chain, sandbox)
        for name, r in phase_b.items():
            print(f"  {name:16s} {r['classification']:12s} {r['detail']}")
        degradation = probe_local_model_degradation()
        print(f"  local_model_degradation {degradation['classification']:12s} {degradation['detail']}")
    finally:
        rb_mod._TAVILY_URL = original_url

    # ── Phase C: restore + recovery verification ──────────────────────────────
    print("\nPhase C (restored)...")
    phase_c = run_phase(chain, sandbox)
    print(f"  C8_external_search {phase_c['C8_external_search']['classification']:12s} "
          f"{phase_c['C8_external_search']['detail']}")

    crr_a, crr_b = crr(phase_a), crr(phase_b)
    print(f"\n  CRR connected  = {crr_a['crr']}")
    print(f"  CRR cloud-out  = {crr_b['crr']}")

    raw = {
        "experiment_id": "MSB-GOV-EVAL-001",
        "subexperiment": "sovereignty",
        "hypothesis": "H4: MSB retains a measured subset of capability when cloud dependencies disappear",
        "git_commit": os.popen("git -C %s rev-parse HEAD" % RUN_ROOT.parent).read().strip(),
        "timestamp": ts,
        "weights_frozen_before_measurement": WEIGHTS,
        "classification_scores": SCORES,
        "injection": {
            "method": "client-boundary repoint of external search endpoint to unreachable address",
            "blackhole_url": BLACKHOLE_URL,
            "scope_note": "full OS-level network severance out of scope: would drop live WireGuard/Vesta; "
                          "client-boundary injection reproduces the same observable (refused/timeout)",
        },
        "phases": {"A_connected": phase_a, "B_cloud_removed": phase_b, "C_restored": phase_c,
                   "B_local_model_degradation": degradation},
        "crr": {"A_connected": crr_a, "B_cloud_removed": crr_b},
        "graceful_degradation": {
            "external_search_fails_loud": phase_b["C8_external_search"]["classification"] == "UNAVAILABLE"
                                          and "ResearchBackendError" in phase_b["C8_external_search"]["detail"],
            "local_model_controlled_fallback": degradation["classification"] == "DEGRADED",
            "sovereign_capabilities_during_outage": all(
                phase_b[k]["classification"] in ("FULL", "DEGRADED")
                for k in ("C1_inference", "C2_memory", "C3_planning", "C4_task_execution",
                          "C5_audit", "C6_evidence", "C7_local_storage")),
        },
    }
    raw_path = run_dir / "raw" / f"sovereignty_{ts}.json"
    raw_path.write_text(json.dumps(raw, indent=2))

    csv_path = RUN_ROOT / "results" / "sovereignty.csv"
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        cw = csv.writer(f)
        if write_header:
            cw.writerow(["timestamp", "phase", "capability", "weight", "classification", "detail"])
        for phase_label, phase in (("A", phase_a), ("B", phase_b), ("C", phase_c)):
            for name, r in phase.items():
                cw.writerow([ts, phase_label, name, WEIGHTS[name], r["classification"], r["detail"]])
        cw.writerow([ts, "B", "local_model_degradation", "", degradation["classification"], degradation["detail"]])
        cw.writerow([ts, "A", "CRR", "", "", crr_a["crr"]])
        cw.writerow([ts, "B", "CRR", "", "", crr_b["crr"]])

    print(f"\n  evidence: {raw_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
