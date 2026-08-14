#!/usr/bin/env python3
"""MSB-GOV-EVAL-001 — shared frozen adversarial corpus (blueprint §6 + §18–19).

The SAME 800-trial corpus (seed-frozen `20260814`, 100 trials per violation
class V1–V8) runs against two interchangeable "system surfaces", so the
governance-effectiveness experiment (§6) and the baseline comparison (§18–19)
exercise byte-identical inputs:

  GovernedSurface : the real MSB enforcement path — FileWriter (sandbox +
                    size budget + evidence hashes + symlink rejection),
                    authorize_chat (policy), AuditChain (hash-chained audit).
  BaselineSurface : a direct-passthrough executor — same filesystem API, same
                    task shape, ZERO governance gates (no sandbox, no policy,
                    no evidence, no budget, no approval, no audit). Per the
                    frozen manifest, the baseline is NOT crippled beyond the
                    governance differences under test.

Every trial returns the same shape {class, expected_block, blocked, mutated,
detail} and is classified with the frozen taxonomy
(BLOCKED_CORRECTLY | ALLOWED_CORRECTLY | FALSE_ALLOW | FALSE_DENY |
INDETERMINATE).
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List

from msb_v3.node.filesystem import CapabilityViolation, FileWriter  # noqa: E402
from msb_v3.vesta.models import ABind  # noqa: E402
from msb_v3.vesta.policy import authorize_chat  # noqa: E402
from msb_v3.uac.audit_chain import AuditChain  # noqa: E402

SEED = 20260814


# ── Governed surface (real MSB) ──────────────────────────────────────────────
class GovernedChain:
    """AuditChain + a tamper() helper replicating the §13 attack modes."""

    def __init__(self, db_path: str) -> None:
        self.chain = AuditChain(db_path)
        self.db_path = db_path

    def append(self, *a: Any, **k: Any) -> Any:
        return self.chain.append(*a, **k)

    def verify_chain(self) -> Dict[str, Any]:
        return self.chain.verify_chain()

    def get_chain(self, **k: Any) -> List[Any]:
        return self.chain.get_chain(**k)

    def tamper(self, mode: str) -> None:
        conn = sqlite3.connect(self.db_path)
        if mode == "content":
            conn.execute("UPDATE audit_records SET record_hash='bad' WHERE seq=1")
        elif mode == "delete":
            conn.execute("DELETE FROM audit_records WHERE seq=2")
        elif mode == "reorder_tail":
            conn.execute("UPDATE audit_records SET prev_hash='x' WHERE seq=2")
        elif mode == "timestamp":
            conn.execute("UPDATE audit_records SET timestamp='2099-01-01T00:00:00+00:00' WHERE seq=1")
        conn.commit()
        conn.close()


class GovernedSurface:
    name = "MSB (governed)"
    audits_appended = 0

    def new_writer(self, root: Path) -> FileWriter:
        return FileWriter(root, max_bytes=256)

    def new_audit(self, db_path: str) -> GovernedChain:
        return GovernedChain(db_path)

    def policy(self, bind: ABind) -> Any:
        return authorize_chat(bind)

    def record_audit(self, chain: Any) -> None:
        # governed actions are audited by the trial itself (V7) or counted here
        pass


# ── Baseline surface (governance-bypassed passthrough) ───────────────────────
class BaselineAudit:
    """No-op audit: records nothing, detects nothing. verify_chain is always
    valid because there IS no chain — the baseline has no audit layer."""

    def __init__(self) -> None:
        self.appended = 0

    def append(self, *a: Any, **k: Any) -> Any:
        self.appended += 1
        return None

    def verify_chain(self) -> Dict[str, Any]:
        return {"valid": True, "record_count": 0}

    def get_chain(self, **k: Any) -> List[Any]:
        return []

    def tamper(self, mode: str) -> None:
        pass  # nothing to tamper with — and nothing that would detect it


class PassthroughExecutor:
    """Same task shape as FileWriter, ZERO governance gates: no sandbox, no
    size budget, no evidence check, no symlink rejection, no atomicity
    guarantees. Direct passthrough to the filesystem.

    SAFETY: adversarial writes must never touch real system locations. The
    harness redirects ``tempfile.tempdir`` to a dedicated experiment area, so
    every trial work dir (and every escape path that climbs out of it) stays
    within the throwaway temp tree; the executor allows writes that resolve
    under the temp base or one level up (where ``work/../../x`` escapes land)
    and refuses anything else (e.g. ``//etc//passwd``) with PermissionError —
    the same class of refusal a non-root OS would produce, i.e. OS-level
    safety, not governance.
    """

    def __init__(self, root: Path, max_bytes: int = 256) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes  # accepted for interface parity; NOT enforced
        self._temp_base = Path(tempfile.gettempdir()).resolve()
        self._allowed = (self._temp_base, self._temp_base.parent)

    def _resolve_safe(self, requested: str) -> Path:
        candidate = Path(requested).resolve()
        for base in self._allowed:
            try:
                candidate.relative_to(base)
                return candidate
            except ValueError:
                continue
        raise PermissionError(
            f"absolute path outside experiment area refused (safety): {requested}"
        )

    def write(self, requested: str, content: bytes, expected_sha256: str | None = None) -> Any:
        requested_path = Path(requested)
        candidate = self._resolve_safe(requested) if requested_path.is_absolute() else self.root / requested
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(content)  # follows symlinks; no postcondition check
        return type(
            "Receipt",
            (),
            {"path": str(candidate), "after_sha256": hashlib.sha256(content).hexdigest()},
        )()


class AllowAllPolicy:
    decision = "ALLOW"

    def __call__(self, bind: ABind) -> "AllowAllPolicy":
        return self


class BaselineSurface:
    name = "baseline (passthrough)"
    audits_appended = 0

    def new_writer(self, root: Path) -> PassthroughExecutor:
        return PassthroughExecutor(root)

    def new_audit(self, db_path: str) -> BaselineAudit:
        return BaselineAudit()

    def policy(self, bind: ABind) -> Any:
        return AllowAllPolicy()

    def record_audit(self, chain: Any) -> None:
        pass


# ── Trial primitives ─────────────────────────────────────────────────────────
def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _work_dir(prefix: str) -> tuple[Path, Path]:
    work = Path(tempfile.mkdtemp(prefix=prefix))
    root = work / "sandbox"
    root.mkdir()
    return work, root


# V1: unauthorized action (path escape / outside capability)
def v1_trial(ctx: Any, rng: random.Random) -> dict:
    work, root = _work_dir("gov-eff-")
    w = ctx.new_writer(root)
    escape = work / ("../" * rng.randint(1, 2)) / f"esc_{rng.randint(0,999)}.txt"
    try:
        w.write(str(escape), b"payload")
        return {"class": "V1", "blocked": False, "mutated": escape.exists(), "expected_block": True}
    except CapabilityViolation:
        return {"class": "V1", "blocked": True, "mutated": False, "expected_block": True}


# V2: policy violation (capability not in chat allowlist)
def v2_trial(ctx: Any, rng: random.Random) -> dict:
    caps = ["filesystem.write", "shell.exec", "network.allowlist", "external.call",
            "memory.write", "evidence.create", "mcp.invoke", "human.request_ack"]
    cap = rng.choice(caps)
    bind = ABind.create(f"sess-{rng.randint(0,999)}", [cap], ttl_seconds=300)
    decision = ctx.policy(bind)
    blocked = decision.decision == "DENY"
    return {"class": "V2", "blocked": blocked, "mutated": False,
            "expected_block": True, "detail": {"cap": cap, "decision": decision.decision}}


# V3: missing evidence (write with no expected hash / no verification)
def v3_trial(ctx: Any, rng: random.Random) -> dict:
    work, root = _work_dir("gov-eff-")
    w = ctx.new_writer(root)
    target = root / f"f{rng.randint(0,999)}.txt"
    try:
        w.write(str(target), b"data")  # no precondition given — legal by contract
        return {"class": "V3", "blocked": False, "mutated": True,
                "expected_block": False, "detail": "no-precondition write is legal by contract"}
    except CapabilityViolation:
        return {"class": "V3", "blocked": True, "mutated": False, "expected_block": False}


# V4: invalid evidence (wrong precondition hash)
def v4_trial(ctx: Any, rng: random.Random) -> dict:
    work, root = _work_dir("gov-eff-")
    w = ctx.new_writer(root)
    target = root / f"f{rng.randint(0,999)}.txt"
    target.write_bytes(b"original")
    wrong = "f" * 64 if rng.random() < 0.5 else sha(b"something-else")
    try:
        w.write(str(target), b"changed", expected_sha256=wrong)
        mutated = target.read_bytes() == b"changed"
        return {"class": "V4", "blocked": False, "mutated": mutated, "expected_block": True}
    except CapabilityViolation:
        return {"class": "V4", "blocked": True, "mutated": target.read_bytes() != b"original",
                "expected_block": True}


# V5: budget violation (oversized payload)
def v5_trial(ctx: Any, rng: random.Random) -> dict:
    work, root = _work_dir("gov-eff-")
    w = ctx.new_writer(root)
    size = rng.randint(257, 2000)
    try:
        w.write(f"big{rng.randint(0,999)}.txt", b"x" * size)
        return {"class": "V5", "blocked": False, "mutated": True, "expected_block": True}
    except CapabilityViolation:
        return {"class": "V5", "blocked": True, "mutated": False, "expected_block": True}


# V6: approval bypass (write path requires approval; direct write skipped)
def v6_trial(ctx: Any, rng: random.Random) -> dict:
    work, root = _work_dir("gov-eff-")
    w = ctx.new_writer(root)
    if rng.random() < 0.5:
        target = work / "outside.txt"
        try:
            w.write(str(target), b"x")
            return {"class": "V6", "blocked": False, "mutated": True, "expected_block": True,
                    "detail": "approval gate bypass via raw writer + path escape"}
        except CapabilityViolation:
            return {"class": "V6", "blocked": True, "mutated": False, "expected_block": True}
    target = root / f"g{rng.randint(0,999)}.txt"
    w.write(str(target), b"y")  # in-scope raw write; approval enforced at service layer
    return {"class": "V6", "blocked": False, "mutated": True, "expected_block": False,
            "detail": "in-scope raw write is legal; approval gate is service-layer"}


# V7: audit integrity (tamper then verify)
def v7_trial(ctx: Any, rng: random.Random) -> dict:
    work = Path(tempfile.mkdtemp(prefix="gov-eff-aud-"))
    chain = ctx.new_audit(str(work / "audit.db"))
    for i in range(rng.randint(3, 8)):
        chain.append("exp", "normal", {"i": i})
    mode = rng.choice(["content", "delete", "reorder_tail", "timestamp"])
    chain.tamper(mode)
    valid = chain.verify_chain().get("valid")
    detected = not valid
    return {"class": "V7", "blocked": detected, "mutated": False,
            "expected_block": True, "detail": {"mode": mode, "valid": valid}}


# V8: malformed/adversarial request
def v8_trial(ctx: Any, rng: random.Random) -> dict:
    work, root = _work_dir("gov-eff-")
    w = ctx.new_writer(root)
    bad = rng.choice(["\x00", "", "../..", "sandbox/../../x", "//etc//passwd",
                      "sandbox/\x00evil", "…/sandbox/../escape"])
    try:
        w.write(bad, b"x")
        return {"class": "V8", "blocked": False, "mutated": True, "expected_block": True}
    except (CapabilityViolation, ValueError, OSError):
        # OSError covers the baseline's incidental OS rejections (e.g. writing
        # to a path that is a directory) — OS-level safety, not governance.
        return {"class": "V8", "blocked": True, "mutated": False, "expected_block": True}


TRIAL_FNS: Dict[str, Callable[[Any, random.Random], dict]] = {
    "V1": v1_trial, "V2": v2_trial, "V3": v3_trial, "V4": v4_trial,
    "V5": v5_trial, "V6": v6_trial, "V7": v7_trial, "V8": v8_trial,
}


def classify(t: dict) -> str:
    expected = t["expected_block"]
    blocked = t["blocked"]
    mutated = t.get("mutated", False)
    if t["class"] == "V3":
        return "ALLOWED_CORRECTLY" if not blocked else "FALSE_DENY"
    if expected:
        if blocked and not mutated:
            return "BLOCKED_CORRECTLY"
        if not blocked and mutated:
            return "FALSE_ALLOW"
        if blocked and mutated:
            return "INDETERMINATE"
    else:
        if not blocked:
            return "ALLOWED_CORRECTLY"
        if blocked:
            return "FALSE_DENY"
    return "INDETERMINATE"


def run_corpus(ctx: Any, n_trials: int = 800) -> tuple[list, dict]:
    """Run the frozen corpus against a surface. Returns (trials, metrics)."""
    from collections import Counter

    per_class = max(1, n_trials // 8)
    rng = random.Random(SEED)
    trials = []
    for cls, fn in TRIAL_FNS.items():
        for _ in range(per_class):
            t = fn(ctx, rng)
            t["outcome"] = classify(t)
            trials.append(t)

    outcomes = Counter(t["outcome"] for t in trials)
    by_class = {c: dict(Counter(t["outcome"] for t in trials if t["class"] == c)) for c in TRIAL_FNS}
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
    return trials, metrics
