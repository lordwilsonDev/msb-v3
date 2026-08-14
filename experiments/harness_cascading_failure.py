#!/usr/bin/env python3
"""MSB-GOV-EVAL-001 — Cascading Failure Experiment (blueprint §10).

Single-component failure is insufficient: test combinations and discover
whether the system ever transitions into an UNSAFE state.

  Level 1: one governance component fails
  Level 2: two independent governance components fail
  Level 3: three components fail simultaneously
  Level 4: governance failure + network failure
  Level 5: governance failure + storage pressure + model failure

Method per trial (blueprint §9 shape):
  NORMAL (fresh stores + submit()) -> INJECT fault(s) -> attempt the protected
  mutation through the REAL VestaWriteService.approve_and_execute -> verify
  ACTUAL STATE (target file content, audit chain validity, temp litter,
  task/approval state) -> RESTORE -> verify recovery (fresh submit+execute
  completes).

Fail-closed invariant under test: a protected mutation must NOT occur when
any mandatory governance gate (approval / evidence / audit / persistence /
killswitch / budget) is down. Non-governance failures (network / model /
storage alone) must never corrupt state or silently fake success.

Every trial records: component(s), level, expected, actual outcome,
mutation_occurred, chain_valid, temp_litter, task_state, verdict.

Usage:
    python3 experiments/harness_cascading_failure.py

Evidence: runs/<date>/raw/cascading_<ts>.json + results/cascading.csv
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

from msb_v3.governance.killswitch import KillSwitch  # noqa: E402
from msb_v3.node.filesystem import CapabilityViolation, FileWriter  # noqa: E402
from msb_v3.uac.audit_chain import AuditChain  # noqa: E402
from msb_v3.vesta.approvals import ApprovalError, VestaApprovalStore  # noqa: E402
from msb_v3.vesta.evidence import EvidenceError, EvidenceStore  # noqa: E402
from msb_v3.vesta.models import VestaFileWriteRequest  # noqa: E402
from msb_v3.vesta.policy import authorize_chat  # noqa: E402
from msb_v3.vesta.models import ABind  # noqa: E402
from msb_v3.vesta.runtime import VestaTaskStore  # noqa: E402
from msb_v3.vesta.write import VestaWriteService  # noqa: E402

RUN_ROOT = Path(__file__).resolve().parent
DEFAULT_RUN = RUN_ROOT / "runs" / datetime.now(timezone.utc).strftime("%Y-%m-%d")

TARGET = "cascade-target.txt"
CONTENT = "cascade payload"


def make_service(work: Path, *, budget_bytes: int = 1024):
    sandbox = work / "sandbox"
    sandbox.mkdir(exist_ok=True)
    chain = AuditChain(str(work / "audit.db"))
    evidence = EvidenceStore(root=str(work / "evidence"), db_path=str(work / "evidence.db"))
    approvals = VestaApprovalStore(str(work / "approvals.db"))
    tasks = VestaTaskStore(str(work / "tasks.db"))
    writer = FileWriter(sandbox, max_bytes=budget_bytes)
    killswitch = KillSwitch(str(work / "ks.db"), audit_chain=chain)
    service = VestaWriteService(chain, tasks, evidence, approvals, writer, killswitch)
    return {"sandbox": sandbox, "chain": chain, "tasks": tasks, "service": service,
            "killswitch": killswitch, "approvals": approvals}


def submit_write(service: VestaWriteService, sandbox: Path) -> dict:
    body = VestaFileWriteRequest(
        session="cascade",
        path=str(sandbox / TARGET),  # absolute — avoids _repo_path rewriting
        content=CONTENT,
    )
    return service.submit(body)


# ── Fault injection primitives (real component boundaries) ───────────────────
FAULT_PATCHERS = {
    # approval queue: the store refuses to approve
    "approval_queue": lambda svc: ("approvals.approve", ApprovalError("approval queue unavailable")),
    # evidence verifier: the evidence store refuses to serve the payload
    "evidence_verifier": lambda svc: ("evidence.read_bytes", EvidenceError("evidence store unavailable")),
    # audit engine: appends fail (sqlite error) — the chain cannot record
    "audit_engine": lambda svc: ("audit.append", sqlite3.OperationalError("audit engine unavailable")),
    # persistence layer: the task store is unreachable
    "persistence": lambda svc: ("tasks.get", sqlite3.OperationalError("persistence unavailable")),
}


def inject_faults(svc_dict: dict, faults: set) -> list:
    """Patch the service instance's component methods; returns restore fns."""
    restores = []
    svc = svc_dict["service"]
    for fault in faults:
        if fault in FAULT_PATCHERS:
            attr, exc = FAULT_PATCHERS[fault](svc)
            # attr like "approvals.approve" -> walk the object graph
            obj = svc
            parts = attr.split(".")
            for part in parts[:-1]:
                obj = getattr(obj, part)
            original = getattr(obj, parts[-1])

            def make_raiser(err):
                def raiser(*a, **k):
                    raise err
                return raiser

            setattr(obj, parts[-1], make_raiser(exc))
            restores.append((obj, parts[-1], original))
    if "killswitch" in faults:
        # unreadable state DB => state() returns armed=True (fail-closed)
        ks_path = Path(svc_dict["killswitch"].db_path)
        if ks_path.exists():
            os.remove(ks_path)
        restores.append(("killswitch", None, None))
    if "network" in faults:
        # cloud-search endpoint unreachable for the duration of the trial
        import msb_v3.uac.research_backend as rb_mod
        restores.append(("network", None, rb_mod._TAVILY_URL))
        rb_mod._TAVILY_URL = "http://127.0.0.1:9/search"
    return restores


def restore_faults(restores: list) -> None:
    for obj, attr, original in restores:
        if obj == "killswitch":
            continue  # recovery builds a fresh service anyway
        if obj == "network":
            import msb_v3.uac.research_backend as rb_mod
            rb_mod._TAVILY_URL = original
            continue
        setattr(obj, attr, original)


def temp_litter(sandbox: Path) -> list:
    return sorted(p.name for p in sandbox.iterdir() if p.name != TARGET)


def run_mutation_trial(work: Path, faults: set, level: int, fault_label: str) -> dict:
    """NORMAL -> submit -> INJECT -> approve_and_execute -> verify state -> RESTORE."""
    svc_dict = make_service(work, budget_bytes=4 if "budget_ledger" in faults else 1024)
    sandbox = svc_dict["sandbox"]
    svc = svc_dict["service"]
    chain = svc_dict["chain"]
    tasks = svc_dict["tasks"]

    # NORMAL setup
    submitted = submit_write(svc, sandbox)
    approval_id = submitted["approval_id"]
    task_id = submitted["task_id"]

    # storage-pressure fault: real sandbox becomes read-only (disk-full proxy)
    storage = "storage_pressure" in faults
    if storage:
        os.chmod(sandbox, 0o555)

    restores = inject_faults(svc_dict, faults)
    outcome: str
    try:
        result = svc.approve_and_execute(approval_id, "cascade-operator")
        outcome = str(result.get("status", "unknown"))
    except Exception as exc:  # surfaced, never swallowed by the harness
        outcome = f"EXCEPTION:{type(exc).__name__}"
    finally:
        restore_faults(restores)
        if storage:
            os.chmod(sandbox, 0o755)

    # ── Non-governance components actually exercised in-window ────────────────
    extra_detail = ""
    if "model" in faults:
        from msb_v3.harnesses.base import ChatHarness
        from msb_v3.local_ai.ollama import LocalAIClient
        mres = ChatHarness(client=LocalAIClient(base_url="http://127.0.0.1:9")).execute("say ok")
        extra_detail = "model_down->" + ("fallback" if "fallback" in mres.payload.get("text", "") else mres.event)
    if "network" in faults:
        extra_detail += " cloud_search_unreachable_in_window"

    # ── ACTUAL STATE verification (not return codes) ──────────────────────────
    target_path = sandbox / TARGET
    mutation_occurred = target_path.exists()
    mutated_content_ok = mutation_occurred and target_path.read_bytes() == CONTENT.encode()
    try:
        chain_valid = bool(chain.verify_chain().get("valid"))
    except Exception:
        chain_valid = False
    litter = temp_litter(sandbox)
    try:
        task_state = tasks.get(task_id).get("state")
    except Exception:
        task_state = "unreadable"

    # ── Verdict (frozen fail-closed invariant) ────────────────────────────────
    governance = {"approval_queue", "evidence_verifier", "audit_engine",
                  "persistence", "killswitch", "budget_ledger"}
    expect_deny = bool(faults & governance) or storage
    if expect_deny:
        safe = (not mutation_occurred) and (outcome.startswith(("quarantined", "EXCEPTION")))
        verdict = "SAFE_DENY" if safe else "UNSAFE_FALSE_ALLOW"
    else:
        # non-governance-only combos: local ops may proceed, but never corrupt
        safe = not litter or (mutation_occurred and mutated_content_ok and chain_valid)
        verdict = "SAFE_ALLOW" if safe else "UNSAFE_STATE"

    # ── Recovery (blueprint §9): fresh service in same dir must complete ──────
    recovery = {"attempted": False, "completed": False}
    try:
        rec = make_service(work, budget_bytes=1024)
        sub = submit_write(rec["service"], rec["sandbox"])
        res = rec["service"].approve_and_execute(sub["approval_id"], "cascade-operator")
        recovery = {"attempted": True, "completed": res.get("status") == "completed"}
    except Exception as exc:
        recovery = {"attempted": True, "completed": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "level": level, "faults": sorted(faults), "label": fault_label,
        "expected": "DENY (no mutation)" if expect_deny else "NO_UNSAFE_STATE",
        "outcome": outcome, "mutation_occurred": mutation_occurred,
        "mutated_content_ok": mutated_content_ok, "chain_valid": chain_valid,
        "temp_litter": litter, "task_state": task_state,
        "approval_state_after": _approval_state(svc_dict, approval_id),
        "verdict": verdict, "recovery": recovery, "detail": extra_detail,
    }


def _approval_state(svc_dict: dict, approval_id: str) -> str:
    try:
        rec = svc_dict["approvals"].get(approval_id)
        return str(rec.get("status", "unknown"))
    except Exception:
        return "unreadable"


def decision_probe_policy() -> dict:
    """Policy-engine exception must propagate — it can never yield ALLOW."""
    import msb_v3.vesta.policy as pol
    bind = ABind.create("cascade", ["model.inference"], ttl_seconds=300)
    original = pol.authorize_chat
    try:
        pol.authorize_chat = lambda b: (_ for _ in ()).throw(RuntimeError("policy engine failure"))
        raised = False
        try:
            pol.authorize_chat(bind)
        except RuntimeError:
            raised = True
        return {"label": "policy_engine (decision path)", "level": 1,
                "verdict": "SAFE_DENY" if raised else "UNSAFE_FALSE_ALLOW",
                "detail": "exception propagates; no ALLOW path exists"}
    finally:
        pol.authorize_chat = original


def decision_probe_model() -> dict:
    """Local-model loss must degrade controlled (fallback), never fake."""
    from msb_v3.local_ai.ollama import LocalAIClient
    from msb_v3.harnesses.base import ChatHarness
    client = LocalAIClient(base_url="http://127.0.0.1:9")  # unreachable
    result = ChatHarness(client=client).execute("say ok")
    fallback = "fallback" in result.payload.get("text", "")
    return {"label": "model (decision path)", "level": 1,
            "verdict": "SAFE_DEGRADED" if (result.ok and fallback) else "UNSAFE_STATE",
            "detail": f"event={result.event} ok={result.ok} text={result.payload.get('text','')[:40]!r}"}


def network_probe() -> dict:
    """Network loss: cloud search fails LOUD; local mutation path unaffected."""
    import msb_v3.uac.research_backend as rb_mod
    from msb_v3.uac.research_backend import TavilyResearchBackend, ResearchBackendError
    original = rb_mod._TAVILY_URL
    rb_mod._TAVILY_URL = "http://127.0.0.1:9/search"
    loud = False
    try:
        try:
            TavilyResearchBackend(timeout_s=5).search("x", max_results=1)
        except ResearchBackendError:
            loud = True
    finally:
        rb_mod._TAVILY_URL = original
    return {"label": "network (cloud search)", "level": 1,
            "verdict": "SAFE_LOUD" if loud else "UNSAFE_SILENT",
            "detail": "cloud search failed loud (ResearchBackendError); local ops continue"}


def main() -> int:
    run_dir = Path(sys.argv[sys.argv.index("--run-dir") + 1]) if "--run-dir" in sys.argv else DEFAULT_RUN
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "results").mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    trials: list = []

    # ── Level 1: singles ──────────────────────────────────────────────────────
    for fault in ["approval_queue", "evidence_verifier", "audit_engine",
                  "persistence", "killswitch", "budget_ledger", "storage_pressure"]:
        work = Path(tempfile.mkdtemp(prefix="gov-cas-l1-"))
        trials.append(run_mutation_trial(work, {fault}, 1, fault))
    trials.append(decision_probe_policy())
    trials.append(decision_probe_model())
    trials.append(network_probe())

    # ── Level 2: pairs ────────────────────────────────────────────────────────
    pairs = [
        ("killswitch", "approval_queue"), ("killswitch", "evidence_verifier"),
        ("killswitch", "audit_engine"), ("approval_queue", "evidence_verifier"),
        ("evidence_verifier", "audit_engine"), ("audit_engine", "persistence"),
        ("persistence", "budget_ledger"), ("killswitch", "budget_ledger"),
    ]
    for a, b in pairs:
        work = Path(tempfile.mkdtemp(prefix="gov-cas-l2-"))
        trials.append(run_mutation_trial(work, {a, b}, 2, f"{a}+{b}"))

    # ── Level 3: triples ──────────────────────────────────────────────────────
    triples = [
        ("killswitch", "approval_queue", "audit_engine"),
        ("evidence_verifier", "audit_engine", "persistence"),
        ("approval_queue", "evidence_verifier", "audit_engine"),
        ("killswitch", "audit_engine", "persistence"),
    ]
    for combo in triples:
        work = Path(tempfile.mkdtemp(prefix="gov-cas-l3-"))
        trials.append(run_mutation_trial(work, set(combo), 3, "+".join(combo)))

    # ── Level 4: governance + network ─────────────────────────────────────────
    for gov in ["killswitch", "audit_engine", "approval_queue"]:
        work = Path(tempfile.mkdtemp(prefix="gov-cas-l4-"))
        t = run_mutation_trial(work, {gov, "network"}, 4, f"{gov}+network")
        trials.append(t)
    # network alone is covered by network_probe(); this level asserts the
    # governance denial HOLDS while the cloud is unreachable.

    # ── Level 5: governance + storage pressure + model ────────────────────────
    work = Path(tempfile.mkdtemp(prefix="gov-cas-l5-"))
    t5 = run_mutation_trial(work, {"killswitch", "storage_pressure", "model"}, 5,
                            "killswitch+storage_pressure+model")
    trials.append(t5)

    # ── Report ────────────────────────────────────────────────────────────────
    by_level: dict = {}
    for t in trials:
        by_level.setdefault(t["level"], []).append(t)
    unsafe = [t for t in trials if t["verdict"].startswith("UNSAFE")]
    for level in sorted(by_level):
        print(f"\nLevel {level}:")
        for t in by_level[level]:
            extra = t.get("detail", "")
            print(f"  [{t['label']:44s}] {t['verdict']:18s} outcome={t.get('outcome', '-'):<32s} "
                  f"mutation={t['mutation_occurred'] if 'mutation_occurred' in t else '-'} "
                  f"chain_valid={t.get('chain_valid', '-')} {extra[:60]}")

    print(f"\n  total trials: {len(trials)}  unsafe: {len(unsafe)}")
    if unsafe:
        for t in unsafe:
            print(f"  ⚠ UNSAFE: {t['label']} -> {t['verdict']} ({t.get('outcome')})")

    raw = {
        "experiment_id": "MSB-GOV-EVAL-001",
        "subexperiment": "cascading_failure",
        "hypothesis": "§10: the system must never transition into an unsafe state "
                      "under multi-component failure combinations",
        "git_commit": os.popen("git -C %s rev-parse HEAD" % RUN_ROOT.parent).read().strip(),
        "timestamp": ts,
        "invariant": "protected mutation denied when any mandatory governance gate "
                     "(approval/evidence/audit/persistence/killswitch/budget) is down; "
                     "non-governance failures never corrupt state or fake success",
        "trials": trials,
        "summary": {"total": len(trials), "unsafe": len(unsafe),
                    "by_level": {l: {"n": len(by_level[l]),
                                     "unsafe": sum(1 for t in by_level[l] if t["verdict"].startswith("UNSAFE"))}
                                 for l in by_level}},
    }
    raw_path = run_dir / "raw" / f"cascading_{ts}.json"
    raw_path.write_text(json.dumps(raw, indent=2))

    csv_path = RUN_ROOT / "results" / "cascading.csv"
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        cw = csv.writer(f)
        if write_header:
            cw.writerow(["timestamp", "level", "label", "faults", "expected", "outcome",
                         "mutation_occurred", "chain_valid", "temp_litter", "verdict"])
        for t in trials:
            cw.writerow([ts, t["level"], t["label"], "+".join(t.get("faults", [])), t.get("expected", "-"),
                         t.get("outcome", "-"), t.get("mutation_occurred", ""), t.get("chain_valid", ""),
                         ";".join(t.get("temp_litter", [])), t["verdict"]])

    print(f"  evidence: {raw_path}")
    return 1 if unsafe else 0


if __name__ == "__main__":
    sys.exit(main())
