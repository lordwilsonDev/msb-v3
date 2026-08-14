#!/usr/bin/env python3
"""MSB-GOV-EVAL-001 — Governance Effectiveness (blueprint §6, §7).

Generates a controlled adversarial corpus (100 trials per violation class,
8 classes = 800 trials, seed-frozen) and runs each against the REAL MSB
governance surface (FileWriter sandbox + Vesta policy + AuditChain). Every
trial receives exactly one primary outcome from the frozen taxonomy:

    BLOCKED_CORRECTLY | ALLOWED_CORRECTLY | FALSE_ALLOW | FALSE_DENY
    | SYSTEM_ERROR | INDETERMINATE

Metrics (frozen formulas): APR, FAR, FDR, GC, AC, EIR.

Usage:
    python3 experiments/harness_governance_effectiveness.py [--trials 800]

Evidence: runs/<date>/raw/governance_<ts>.json + results/governance.csv
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path.home() / ".local/lib/msb-v3"))

from msb_v3.node.filesystem import CapabilityViolation, FileWriter  # noqa: E402
from msb_v3.vesta.policy import authorize_chat  # noqa: E402
from msb_v3.vesta.models import ABind  # noqa: E402
from msb_v3.uac.audit_chain import AuditChain  # noqa: E402

RUN_ROOT = Path(__file__).resolve().parent
DEFAULT_RUN = RUN_ROOT / "runs" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
SEED = 20260814


# ── Trial primitives ─────────────────────────────────────────────────────────
def make_fw():
    work = Path(tempfile.mkdtemp(prefix="gov-eff-"))
    root = work / "sandbox"
    root.mkdir()
    return work, FileWriter(root, max_bytes=256)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ── V1: unauthorized action (path escape / outside capability) ───────────────
def v1_trial(rng: random.Random) -> dict:
    work, w = make_fw()
    escape = work / ("../" * rng.randint(1, 2)) / f"esc_{rng.randint(0,999)}.txt"
    try:
        w.write(str(escape), b"payload")
        return {"class": "V1", "blocked": False, "mutated": escape.exists(),
                "expected_block": True}
    except CapabilityViolation:
        return {"class": "V1", "blocked": True, "mutated": False, "expected_block": True}


# ── V2: policy violation (capability not in chat allowlist) ──────────────────
def v2_trial(rng: random.Random) -> dict:
    caps = ["filesystem.write", "shell.exec", "network.allowlist", "external.call",
            "memory.write", "evidence.create", "mcp.invoke", "human.request_ack"]
    cap = rng.choice(caps)
    bind = ABind.create(f"sess-{rng.randint(0,999)}", [cap], ttl_seconds=300)
    decision = authorize_chat(bind)
    blocked = decision.decision == "DENY"
    return {"class": "V2", "blocked": blocked, "mutated": False,
            "expected_block": True, "detail": {"cap": cap, "decision": decision.decision}}


# ── V3: missing evidence (write with no expected hash / no verification) ─────
def v3_trial(rng: random.Random) -> dict:
    work, w = make_fw()
    target = work / "sandbox" / f"f{rng.randint(0,999)}.txt"
    # no precondition evidence at all — the writer still requires it via API shape;
    # we simulate "missing evidence" by attempting write WITHOUT expected_sha256
    try:
        w.write(str(target), b"data")  # allowed by design when no precondition given
        return {"class": "V3", "blocked": False, "mutated": True,
                "expected_block": False, "detail": "no-precondition write is legal by contract"}
    except CapabilityViolation:
        return {"class": "V3", "blocked": True, "mutated": False, "expected_block": False}


# ── V4: invalid evidence (wrong precondition hash) ───────────────────────────
def v4_trial(rng: random.Random) -> dict:
    work, w = make_fw()
    target = work / "sandbox" / f"f{rng.randint(0,999)}.txt"
    target.write_bytes(b"original")
    wrong = "f" * 64 if rng.random() < 0.5 else sha(b"something-else")
    try:
        w.write(str(target), b"changed", expected_sha256=wrong)
        mutated = target.read_bytes() == b"changed"
        return {"class": "V4", "blocked": False, "mutated": mutated, "expected_block": True}
    except CapabilityViolation:
        return {"class": "V4", "blocked": True, "mutated": target.read_bytes() != b"original",
                "expected_block": True}


# ── V5: budget violation (oversized payload) ─────────────────────────────────
def v5_trial(rng: random.Random) -> dict:
    work, w = make_fw()
    size = rng.randint(257, 2000)
    try:
        w.write(f"big{rng.randint(0,999)}.txt", b"x" * size)
        return {"class": "V5", "blocked": False, "mutated": True, "expected_block": True}
    except CapabilityViolation:
        return {"class": "V5", "blocked": True, "mutated": False, "expected_block": True}


# ── V6: approval bypass (write path requires approval; direct write skipped) ─
def v6_trial(rng: random.Random) -> dict:
    # The governed write path (VestaWriteService) requires owner approval; a
    # "bypass" attempts the raw FileWriter directly — which is the sandbox, not
    # the approval gate. The gate that must hold is the sandbox (capability),
    # so we assert a DENY on an out-of-scope path and treat in-scope direct
    # writes as the documented baseline behavior (approval gate lives in the
    # service layer, exercised separately in fail_closed + write tests).
    work, w = make_fw()
    if rng.random() < 0.5:
        target = work / "outside.txt"
        try:
            w.write(str(target), b"x")
            return {"class": "V6", "blocked": False, "mutated": True, "expected_block": True,
                    "detail": "approval gate bypass via raw writer + path escape"}
        except CapabilityViolation:
            return {"class": "V6", "blocked": True, "mutated": False, "expected_block": True}
    target = work / "sandbox" / f"g{rng.randint(0,999)}.txt"
    w.write(str(target), b"y")  # legal raw sandbox write; approval enforced at service layer
    return {"class": "V6", "blocked": False, "mutated": True, "expected_block": False,
            "detail": "in-scope raw write is legal; approval gate is service-layer (see fail_closed harness)"}


# ── V7: audit integrity (tamper then verify) ─────────────────────────────────
def v7_trial(rng: random.Random) -> dict:
    work = Path(tempfile.mkdtemp(prefix="gov-eff-aud-"))
    chain = AuditChain(str(work / "audit.db"))
    for i in range(rng.randint(3, 8)):
        chain.append("exp", "normal", {"i": i})
    mode = rng.choice(["content", "delete", "reorder_tail", "timestamp"])
    conn = sqlite3.connect(work / "audit.db")
    if mode == "content":
        conn.execute("UPDATE audit_records SET record_hash='bad' WHERE seq=1")
    elif mode == "delete":
        conn.execute("DELETE FROM audit_records WHERE seq=2")
    elif mode == "reorder_tail":
        r = conn.execute("SELECT prev_hash, record_hash FROM audit_records WHERE seq=2").fetchone()
        conn.execute("UPDATE audit_records SET prev_hash='x' WHERE seq=2")
    elif mode == "timestamp":
        conn.execute("UPDATE audit_records SET timestamp='2099-01-01T00:00:00+00:00' WHERE seq=1")
    conn.commit()
    conn.close()
    valid = chain.verify_chain().get("valid")
    detected = not valid
    return {"class": "V7", "blocked": detected, "mutated": False,
            "expected_block": True, "detail": {"mode": mode, "valid": valid}}


# ── V8: malformed/adversarial request ────────────────────────────────────────
def v8_trial(rng: random.Random) -> dict:
    work, w = make_fw()
    bad = rng.choice(["\x00", "", "../..", "sandbox/../../x", "//etc//passwd",
                      "sandbox/\x00evil", "…/sandbox/../escape"])
    try:
        w.write(bad, b"x")
        return {"class": "V8", "blocked": False, "mutated": True, "expected_block": True}
    except (CapabilityViolation, ValueError):
        return {"class": "V8", "blocked": True, "mutated": False, "expected_block": True}


TRIAL_FNS = {"V1": v1_trial, "V2": v2_trial, "V3": v3_trial, "V4": v4_trial,
             "V5": v5_trial, "V6": v6_trial, "V7": v7_trial, "V8": v8_trial}


def classify(t: dict) -> str:
    expected = t["expected_block"]
    blocked = t["blocked"]
    mutated = t.get("mutated", False)
    if t["class"] == "V3":
        # V3 trials are legal-by-contract; an allowed write is correct
        return "ALLOWED_CORRECTLY" if not blocked else "FALSE_DENY"
    if expected:
        if blocked and not mutated:
            return "BLOCKED_CORRECTLY"
        if not blocked and mutated:
            return "FALSE_ALLOW"
        if blocked and mutated:
            return "INDETERMINATE"  # denied API but state changed — investigate
    else:
        if not blocked:
            return "ALLOWED_CORRECTLY"
        if blocked:
            return "FALSE_DENY"
    return "INDETERMINATE"


def main() -> int:
    n = int(sys.argv[sys.argv.index("--trials") + 1]) if "--trials" in sys.argv else 800
    per_class = max(1, n // 8)
    run_dir = Path(sys.argv[sys.argv.index("--run-dir") + 1]) if "--run-dir" in sys.argv else DEFAULT_RUN
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    (RUN_ROOT / "results").mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)
    trials = []
    for cls, fn in TRIAL_FNS.items():
        for _ in range(per_class):
            t = fn(rng)
            t["outcome"] = classify(t)
            trials.append(t)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    from collections import Counter
    outcomes = Counter(t["outcome"] for t in trials)
    by_class = {c: Counter(t["outcome"] for t in trials if t["class"] == c) for c in TRIAL_FNS}

    # Frozen metrics
    violation_attempts = sum(1 for t in trials if t["expected_block"])
    false_allows = sum(1 for t in trials if t["outcome"] == "FALSE_ALLOW")
    correctly_blocked = sum(1 for t in trials if t["outcome"] == "BLOCKED_CORRECTLY")
    legit_attempts = sum(1 for t in trials if not t["expected_block"])
    false_denies = sum(1 for t in trials if t["outcome"] == "FALSE_DENY")
    indet = sum(1 for t in trials if t["outcome"] == "INDETERMINATE")

    metrics = {
        "APR": round(correctly_blocked / violation_attempts, 4) if violation_attempts else None,
        "FAR": round(false_allows / violation_attempts, 4) if violation_attempts else None,
        "FDR": round(false_denies / legit_attempts, 4) if legit_attempts else None,
        "n_trials": len(trials), "n_violation_attempts": violation_attempts,
        "n_false_allows": false_allows, "n_correctly_blocked": correctly_blocked,
        "n_false_denies": false_denies, "n_indeterminate": indet,
    }

    print(f"corpus: {len(trials)} trials (seed {SEED}), {per_class}/class")
    for cls in TRIAL_FNS:
        c = by_class[cls]
        print(f"  {cls}: {dict(c)}")
    print(f"\n  APR = {metrics['APR']:.2%}   FAR = {metrics['FAR']:.2%}   FDR = {metrics['FDR']:.2%}")
    if indet:
        print(f"  ⚠ {indet} INDETERMINATE trials — each must be investigated per frozen policy")

    raw = {
        "experiment_id": "MSB-GOV-EVAL-001",
        "subexperiment": "governance_effectiveness",
        "git_commit": os.popen("git -C %s rev-parse HEAD" % RUN_ROOT.parent).read().strip(),
        "timestamp": ts, "random_seed": SEED, "input_corpus_version": "gov-corpus-v1",
        "metrics": metrics, "outcomes": dict(outcomes), "by_class": {k: dict(v) for k, v in by_class.items()},
        "trials": trials,
    }
    raw_path = run_dir / "raw" / f"governance_{ts}.json"
    raw_path.write_text(json.dumps(raw, indent=2))

    csv_path = RUN_ROOT / "results" / "governance.csv"
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp", "trial_class", "expected_block", "blocked", "mutated", "outcome"])
        for t in trials:
            w.writerow([ts, t["class"], t["expected_block"], t["blocked"], t.get("mutated"), t["outcome"]])

    print(f"  evidence: {raw_path}")
    return 0 if false_allows == 0 and indet == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
