"""Identity-wiring shadow mode (Deliverable 02 §10, K19-K22).

Answers one question without changing any decision:

    IF the kernel enforced the identity invariant (I6) on this path,
    what would it have decided about the actor?

The invariant under observation is blueprint §7 I6 —
``AUTHORIZATION(x) → AUTHENTICATED_ACTOR(x)``. Today no caller supplies an
actor, so the guards below have never been evaluated on a live path and there
is no evidence bearing on I6 either way. This module produces that evidence
before anything is enforced.

Guards mirrored from the governance kernel spec §4, in order:

    1. resolve actor        actor_id → AgentIdentity via AgentRegistry
    2. identity live        revoked == False
    3. tenant scope         identity.tenant_scope admits the request's tenant
    7. risk ceiling         tier(capability) <= identity.max_risk_tier

Two rules this module holds to, both from the spec:

* **Shadow mode never controls execution.** The verdict is recorded and
  discarded. Nothing here can change a decision, and no failure here can fail
  a request — every entry point swallows its own errors.
* **Only the actor is attributed.** When the caller supplies no actor, the
  candidate identity that a surface *might* adopt is probed and recorded as a
  *candidate* (never as the actor). A record must not be readable as an
  authorization that did not happen.

UNKNOWN is a first-class outcome here, exactly as it is in
``governance/decision.py``: a capability the risk table does not know, or a
registry that cannot be read, records UNKNOWN rather than a fabricated
ALLOW/DENY.

Records land in the same gitignored root as ``governance/shadow.py``
(``runtime/governance-shadow/``), so observing cannot dirty the R0 tree.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from msb_v3.governance.decision import DecisionValue
from msb_v3.governance.tool_registry_view import (
    TIER_SOURCE_DECLARED,
    TIER_SOURCE_UNKNOWN,
    capability_tier,
)

logger = logging.getLogger(__name__)

# Same gitignored root as governance/shadow.py — ".gitignore: /runtime/governance-shadow/".
# Regenerated per run; never versioned.
DEFAULT_IDENTITY_SHADOW_ROOT = Path("runtime/governance-shadow")
IDENTITY_SHADOW_FILE = "identity.jsonl"

# Read live at call time (same convention as api/auth.check_auth): the flag can
# be flipped without a restart. Anything in _FALSY disables observation; unset
# means observe.
_ENV_FLAG = "MSB_IDENTITY_SHADOW"
_ENV_CANDIDATE = "MSB_IDENTITY_SHADOW_CANDIDATE"
_ENV_ORIGIN = "MSB_IDENTITY_SHADOW_ORIGIN"
_FALSY = frozenset({"0", "false", "no", "off"})

# Kernel states (spec §5). Kept as strings so records stay readable and so this
# module does not need the not-yet-existing kernel to exist.
STATE_ALLOW = "ALLOW"
STATE_REJECTED = "REJECTED"
STATE_DENIED = "DENIED"
STATE_UNKNOWN = "UNKNOWN"

# The only value ``enforcement`` ever takes here. A record must never be
# mistakable for an enforcement decision.
ENFORCEMENT_SHADOW = "shadow"


def shadow_enabled() -> bool:
    """True unless ``MSB_IDENTITY_SHADOW`` is explicitly falsy."""
    raw = os.getenv(_ENV_FLAG)
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in _FALSY


# ---------------------------------------------------------------------------
# Where a record came from
# ---------------------------------------------------------------------------

# A record's origin answers a question the K22 report could not previously ask:
# did this call come from the running system, or from a test run? Without it,
# test traffic and field traffic share one corpus and no count means either.
# (Measured at JOB-019: every record in the then-current corpus was test-driven.)
#
# ``ORIGIN_RUNTIME`` is deliberately NOT called "production": it means only
# "not under a test runner". A developer running a script by hand is runtime,
# and labelling that production would be a claim this evidence cannot support.
ORIGIN_TEST = "test"
ORIGIN_RUNTIME = "runtime"
# Records written before the field existed. Its own bucket rather than assumed
# into either side: guessing the origin of existing evidence is the fabrication
# this module exists to avoid.
ORIGIN_UNRECORDED = "unrecorded"


def run_origin() -> str:
    """Classify the current process: a test run, or the runtime.

    Precedence, highest first:

    1. ``MSB_IDENTITY_SHADOW_ORIGIN`` — an explicit operator declaration. Any
       non-blank value is honoured verbatim (a lane must be able to label its
       own evidence, and a label that is visible in its own bucket is better
       than one silently discarded).
    2. pytest in progress (``PYTEST_CURRENT_TEST``, which pytest alone sets, for
       every phase of a test) -> test.
    3. otherwise -> runtime.

    Detection is a heuristic; the override exists so it never has to be argued
    with. Nothing here is security-relevant — it labels evidence, it does not
    gate anything.
    """
    raw = (os.getenv(_ENV_ORIGIN) or "").strip()
    if raw:
        return raw
    if os.getenv("PYTEST_CURRENT_TEST"):
        return ORIGIN_TEST
    return ORIGIN_RUNTIME


# ---------------------------------------------------------------------------
# What the kernel would have decided
# ---------------------------------------------------------------------------


@dataclass
class IdentityShadowVerdict:
    """The would-be outcome of the identity guards. Never applied."""

    kernel_state: str  # ALLOW | REJECTED | DENIED | UNKNOWN
    decision_value: str  # canonical DecisionValue: ALLOW | BLOCK | UNKNOWN
    reason: str
    actor_id: Optional[str] = None
    actor_fingerprint: Optional[str] = None
    tenant_scope: Optional[str] = None
    tier: Optional[int] = None
    tier_source: str = TIER_SOURCE_UNKNOWN
    max_risk_tier: Optional[int] = None
    candidate_agent_id: Optional[str] = None
    candidate_found: bool = False
    candidate_revoked: bool = False
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kernel_state": self.kernel_state,
            "decision_value": self.decision_value,
            "reason": self.reason,
            "actor_id": self.actor_id,
            "actor_fingerprint": self.actor_fingerprint,
            "tenant_scope": self.tenant_scope,
            "tier": self.tier,
            "tier_source": self.tier_source,
            "max_risk_tier": self.max_risk_tier,
            "candidate_agent_id": self.candidate_agent_id,
            "candidate_found": self.candidate_found,
            "candidate_revoked": self.candidate_revoked,
            "notes": list(self.notes),
        }


def _lookup(actor_id: str, registry: Any) -> Tuple[Any, Optional[str]]:
    """Return (identity, None) or (None, failure_reason). Never raises."""
    try:
        return registry.get(actor_id), None
    except KeyError:
        return None, f"unknown actor: {actor_id}"
    except Exception as exc:  # noqa: BLE001 — an unreadable registry is UNKNOWN, not a denial
        return None, f"identity registry unavailable: {exc}"


def evaluate_identity(
    actor_id: Optional[str],
    *,
    tenant: str,
    capability: Optional[str],
    registry: Any = None,
    candidate_agent_id: Optional[str] = None,
) -> IdentityShadowVerdict:
    """Evaluate guards 1-3 and 7 for one call. Pure: reads only, decides nothing.

    ``registry`` defaults to a lazily-constructed ``AgentRegistry``. Passing one
    in is what makes this testable without touching a live database.
    """
    notes: List[str] = []

    # --- risk, stated honestly -------------------------------------------------
    # Three situations, deliberately not conflated:
    #   no capability declared       -> guard 7 is vacuous (nothing to check)
    #   tier from the decision table -> the operator-facing value
    #   tier derived from declared risk_class -> evaluable, but via the tool
    #       registry rather than the decision table; recorded with its source so
    #       the two are never confused
    #   neither -> genuinely UNKNOWN. Never reported as ALLOW.
    tier: Optional[int] = None
    tier_source = TIER_SOURCE_UNKNOWN
    tier_unknown = False
    if capability is None:
        notes.append("tool declares no capability; ceiling not applicable")
    else:
        tier, tier_source = capability_tier(capability)
        if tier is None:
            tier_unknown = True
            notes.append(f"capability known to neither table: {capability!r} (ceiling not evaluable)")
        elif tier_source == TIER_SOURCE_DECLARED:
            notes.append(
                f"tier {tier} derived from the declaring tools' risk_class ({capability!r}); "
                "not an operator-set RISK_TIERS value"
            )

    def _verdict(
        kernel_state: str, decision_value: str, reason: str, **kw: Any
    ) -> IdentityShadowVerdict:
        """Build a verdict carrying the tier and its provenance by default, so no
        branch can forget to say where its tier came from."""
        kw.setdefault("tier", tier)
        kw.setdefault("tier_source", tier_source)
        return IdentityShadowVerdict(
            kernel_state=kernel_state, decision_value=decision_value, reason=reason, **kw
        )

    # --- guard 1: resolve the actor -------------------------------------------
    if actor_id is None:
        probe = candidate_agent_id
        probe_found = False
        probe_revoked = False
        if probe:
            if registry is None:
                registry = _default_registry()
            if registry is not None:
                candidate, failure = _lookup(probe, registry)
                if failure is None and candidate is not None:
                    probe_found = True
                    probe_revoked = bool(getattr(candidate, "revoked", False))
        return _verdict(
            STATE_REJECTED,
            DecisionValue.BLOCK,
            "no actor supplied by the caller",
            tier=tier,
            candidate_agent_id=probe or None,
            candidate_found=probe_found,
            candidate_revoked=probe_revoked,
            notes=tuple(notes + ["candidate identity is a probe, not an attribution"]),
        )

    if registry is None:
        registry = _default_registry()
    if registry is None:
        return _verdict(
            STATE_UNKNOWN,
            DecisionValue.UNKNOWN,
            "identity registry unavailable",
            actor_id=actor_id,
            tier=tier,
            notes=tuple(notes),
        )

    identity, failure = _lookup(actor_id, registry)
    if identity is None:
        state = STATE_UNKNOWN if failure and failure.startswith("identity registry") else STATE_REJECTED
        value = DecisionValue.UNKNOWN if state == STATE_UNKNOWN else DecisionValue.BLOCK
        return _verdict(state, value, failure or "actor unresolved", actor_id=actor_id, tier=tier, notes=tuple(notes))

    fingerprint = getattr(identity, "fingerprint", None)
    tenant_scope = getattr(identity, "tenant_scope", "*")
    max_risk_tier = getattr(identity, "max_risk_tier", None)

    # --- guard 2: the identity is live ----------------------------------------
    if getattr(identity, "revoked", False):
        return _verdict(
            STATE_REJECTED,
            DecisionValue.BLOCK,
            "actor is revoked",
            actor_id=actor_id,
            actor_fingerprint=fingerprint,
            tenant_scope=tenant_scope,
            tier=tier,
            max_risk_tier=max_risk_tier,
            notes=tuple(notes),
        )

    # --- guard 3: tenant scope -------------------------------------------------
    if tenant_scope != "*" and tenant_scope != tenant:
        return _verdict(
            STATE_DENIED,
            DecisionValue.BLOCK,
            f"tenant {tenant!r} outside actor scope {tenant_scope!r}",
            actor_id=actor_id,
            actor_fingerprint=fingerprint,
            tenant_scope=tenant_scope,
            tier=tier,
            max_risk_tier=max_risk_tier,
            notes=tuple(notes),
        )

    # --- guard 7: risk ceiling (vacuous when the tool declares no capability) ---
    if tier is not None and max_risk_tier is not None and tier > int(max_risk_tier):
        return _verdict(
            STATE_DENIED,
            DecisionValue.BLOCK,
            f"capability tier {tier} exceeds actor ceiling {max_risk_tier}",
            actor_id=actor_id,
            actor_fingerprint=fingerprint,
            tenant_scope=tenant_scope,
            tier=tier,
            max_risk_tier=max_risk_tier,
            notes=tuple(notes),
        )

    state = STATE_UNKNOWN if tier_unknown else STATE_ALLOW
    value = DecisionValue.UNKNOWN if tier_unknown else DecisionValue.ALLOW
    reason = "actor resolved; identity guards clear"
    return _verdict(
        state,
        value,
        reason,
        actor_id=actor_id,
        actor_fingerprint=fingerprint,
        tenant_scope=tenant_scope,
        max_risk_tier=max_risk_tier,
        notes=tuple(notes),
    )


def _default_registry() -> Any:
    """Lazily build the real registry. Returns None if it cannot be built."""
    try:
        from msb_v3.agent.identity import AgentRegistry

        return AgentRegistry()
    except Exception as exc:  # noqa: BLE001 — observation must never break a request
        logger.debug("identity shadow: registry unavailable (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# One persisted observation
# ---------------------------------------------------------------------------


@dataclass
class IdentityShadowRecord:
    """One identity observation: what was asked, and what the kernel would say."""

    ts: float
    surface: str
    tool_id: str
    tenant: str
    session: str
    actor_supplied: bool
    capability: Optional[str]
    required_capabilities: Tuple[str, ...]
    declared_risk_class: Optional[str]
    verdict: IdentityShadowVerdict
    origin: str = ORIGIN_UNRECORDED
    enforcement: str = ENFORCEMENT_SHADOW

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "surface": self.surface,
            "origin": self.origin,
            "tool_id": self.tool_id,
            "tenant": self.tenant,
            "session": self.session,
            "actor_supplied": self.actor_supplied,
            "capability": self.capability,
            "required_capabilities": list(self.required_capabilities),
            "declared_risk_class": self.declared_risk_class,
            "enforcement": self.enforcement,
            **self.verdict.as_dict(),
        }


class IdentityShadowRecorder:
    """Records would-be identity verdicts. Never controls execution."""

    def __init__(
        self,
        shadow_root: Optional[Path] = None,
        registry: Any = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self._shadow_root = shadow_root or DEFAULT_IDENTITY_SHADOW_ROOT
        self._shadow_root.mkdir(parents=True, exist_ok=True)
        self._registry = registry
        self._enabled_override = enabled

    @property
    def enabled(self) -> bool:
        if self._enabled_override is not None:
            return self._enabled_override
        return shadow_enabled()

    @property
    def path(self) -> Path:
        return self._shadow_root / IDENTITY_SHADOW_FILE

    def record(
        self,
        *,
        surface: str,
        tool_id: str,
        tenant: str,
        session: str,
        actor_id: Optional[str] = None,
        capability: Optional[str] = None,
        required_capabilities: Tuple[str, ...] = (),
        declared_risk_class: Optional[str] = None,
        candidate_agent_id: Optional[str] = None,
        origin: Optional[str] = None,
    ) -> IdentityShadowRecord:
        """Evaluate and persist one observation. Returns the record for tests."""
        if candidate_agent_id is None:
            # The fallback asks "would a bootstrap identity for THIS surface be
            # justified?" — a real question for a live surface, and noise for an
            # in-process one: an unset candidate on the `moie` path would probe
            # whether an identity literally named "moie" exists, which is not a
            # finding about criterion 4. An explicit declaration always wins.
            fallback = surface if surface in LIVE_SURFACES else None
            candidate_agent_id = os.getenv(_ENV_CANDIDATE) or fallback
        if origin is None:
            origin = run_origin()
        verdict = evaluate_identity(
            actor_id,
            tenant=tenant,
            capability=capability,
            registry=self._registry,
            candidate_agent_id=candidate_agent_id,
        )
        record = IdentityShadowRecord(
            ts=time.time(),
            surface=surface,
            tool_id=tool_id,
            tenant=tenant,
            session=session,
            actor_supplied=actor_id is not None,
            capability=capability,
            required_capabilities=tuple(required_capabilities),
            declared_risk_class=declared_risk_class,
            verdict=verdict,
            origin=origin,
        )
        self._persist(record)
        return record

    def _persist(self, record: IdentityShadowRecord) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.as_dict(), default=str) + "\n")

    def load(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        records: List[Dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records


# ---------------------------------------------------------------------------
# Hot-path entry point — fire and forget
# ---------------------------------------------------------------------------

_default_recorder: Optional[IdentityShadowRecorder] = None


def get_recorder() -> IdentityShadowRecorder:
    """The process-wide recorder (built once, on first use)."""
    global _default_recorder
    if _default_recorder is None:
        _default_recorder = IdentityShadowRecorder()
    return _default_recorder


# ---------------------------------------------------------------------------
# Read-back and the K22 exit-criteria report
# ---------------------------------------------------------------------------


# The surfaces that must each carry an actor (K22 criterion 1), and the two
# whose traffic is a property of the running system rather than of a test.
LIVE_SURFACES = ("chat", "mcp-bridge")

# What ``tools/runtime.py`` labels a governed call whose caller named no entry
# path (its ``surface`` default). A positive statement — "this call did not
# arrive through a live surface" — rather than the sentinel it used to be. The
# old default was the string "unknown", which said only "nobody filled this
# in", so no report could tell an in-process caller from a reporting bug, and
# criterion 1 could not be evaluated for either.
SURFACE_IN_PROCESS = "in-process"

# Legacy sentinel: records written before the default above existed. Read into
# its own bucket, never written. Kept so an existing corpus stays counted and
# visible instead of silently reclassified as something it never claimed.
SURFACE_UNATTRIBUTED = "unknown"

# The in-process entry paths that declare themselves. The first five are named
# after the surfaces they exercise — recorded as real API surfaces in
# docs/SURFACE.md (api/moie.py, api/context.py, api/factory.py,
# api/codegraph.py, api/memory_fabric.py); ``governed-loop`` is a caller driving
# tools/runtime.py directly rather than through any product surface.
# Deliberately NOT validated against at write time: observation must never break
# a request, so an undeclared label becomes its own visible bucket rather than a
# rejection. This tuple is the report's vocabulary, not a gate.
IN_PROCESS_SURFACES = (
    "moie",
    "context-engine",
    "factory",
    "codegraph",
    "memory-fabric",
    "governed-loop",
)

# Sentinel values a *candidate probe* can inherit from the surface fallback
# (recorder: ``candidate_agent_id = env or surface``). A probe of the string
# "unknown" asks whether an identity literally called "unknown" is registered,
# which is not a question. Excluded from the report rather than printed as a
# finding. ``in-process`` joins the set for the same reason: since the runtime
# default became that label, an unset candidate would otherwise be probed as the
# identity "in-process" — a question about a name nobody chose.
_CANDIDATE_SENTINELS = frozenset({"", "unknown", "none", "null", "-", SURFACE_IN_PROCESS})

# Criterion statuses. Deliberately no "GREEN": blueprint §14 keeps the
# conclusion with the researcher, so the best a record can report is MET.
STATUS_MET = "MET"
STATUS_NOT_MET = "NOT MET"
STATUS_INSUFFICIENT = "INSUFFICIENT DATA"
STATUS_JUDGEMENT = "JUDGEMENT REQUIRED"


def load_corpus(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the observation corpus. **Never creates a directory or a file.**

    Unlike ``IdentityShadowRecorder`` — which mkdirs its root on construction —
    this only reads. A status command must not be able to change the filesystem
    it is reporting on, or the report is about a corpus it just made up.

    Torn lines are counted, not silently dropped: this is evidence, and a
    quietly-skipped line would understate the corpus.
    """
    target = Path(path) if path is not None else (DEFAULT_IDENTITY_SHADOW_ROOT / IDENTITY_SHADOW_FILE)
    corpus: Dict[str, Any] = {
        "path": str(target),
        "exists": target.exists(),
        "records": [],
        "malformed": 0,
    }
    if not target.exists():
        return corpus
    with target.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                corpus["records"].append(json.loads(line))
            except json.JSONDecodeError:
                corpus["malformed"] += 1
    return corpus


def k22_status(
    corpus: Dict[str, Any],
    *,
    surfaces: Tuple[str, ...] = LIVE_SURFACES,
    coverage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Summarise the K22 exit criteria from accumulated shadow records.

    Reports what the corpus supports and nothing more. Where a criterion turns
    on a human judgement ("the causes are understood", "the grant is justified")
    the status is JUDGEMENT REQUIRED rather than a verdict — the system may not
    declare its own conclusion true (blueprint §14).
    """
    records: List[Dict[str, Any]] = list(corpus.get("records") or [])
    total = len(records)

    def _state(rec: Dict[str, Any]) -> str:
        return str(rec.get("kernel_state") or "UNKNOWN")

    per_surface: Dict[str, Dict[str, Any]] = {}
    for surface in surfaces:
        rows = [r for r in records if r.get("surface") == surface]
        with_actor = [r for r in rows if r.get("actor_supplied")]
        per_surface[surface] = {
            "records": len(rows),
            "with_actor": len(with_actor),
            "actor_ids": sorted({str(r.get("actor_id")) for r in with_actor if r.get("actor_id")}),
        }

    verdicts: Dict[str, int] = {}
    for rec in records:
        verdicts[_state(rec)] = verdicts.get(_state(rec), 0) + 1

    refused = [r for r in records if _state(r) != STATE_ALLOW]
    causes: Dict[str, int] = {}
    for rec in refused:
        reason = str(rec.get("reason") or "<no reason recorded>")
        causes[reason] = causes.get(reason, 0) + 1

    refused_with_actor = [r for r in refused if r.get("actor_supplied")]
    candidates = [
        {
            "surface": r.get("surface"),
            "candidate_agent_id": r.get("candidate_agent_id"),
            "candidate_found": bool(r.get("candidate_found")),
        }
        for r in records
        if not r.get("actor_supplied") and r.get("candidate_agent_id")
    ]
    candidate_summary: Dict[str, bool] = {}
    sentinel_candidate = False
    for cand in candidates:
        name = str(cand["candidate_agent_id"])
        if name.strip().lower() in _CANDIDATE_SENTINELS:
            sentinel_candidate = True
            continue
        candidate_summary[name] = bool(cand["candidate_found"])

    # --- origin: is this evidence the running system, or a test run? ---------
    def _surface_of(rec: Dict[str, Any]) -> str:
        raw = rec.get("surface")
        return str(raw) if raw not in (None, "") else SURFACE_UNATTRIBUTED

    def _is_in_process(rec: Dict[str, Any]) -> bool:
        return _surface_of(rec) not in surfaces

    def _origin_of(rec: Dict[str, Any]) -> str:
        raw = rec.get("origin")
        return str(raw) if raw not in (None, "") else ORIGIN_UNRECORDED

    origin_counts: Dict[str, int] = {}
    for rec in records:
        origin_counts[_origin_of(rec)] = origin_counts.get(_origin_of(rec), 0) + 1
    runtime_records = [r for r in records if _origin_of(r) == ORIGIN_RUNTIME]
    test_records = [r for r in records if _origin_of(r) == ORIGIN_TEST]

    def _by_surface(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for surface in surfaces:
            here = [r for r in rows if r.get("surface") == surface]
            with_actor = [r for r in here if r.get("actor_supplied")]
            out[surface] = {
                "records": len(here),
                "with_actor": len(with_actor),
                "actor_ids": sorted({str(r.get("actor_id")) for r in with_actor if r.get("actor_id")}),
            }
        return out

    runtime_by_surface = _by_surface(runtime_records)
    test_by_surface = _by_surface(test_records)

    # Every record is either a live surface, a declared non-live entry path, or
    # legacy-unattributed. Deliberately NOT dropped: a tally that omitted them
    # would understate the corpus by however many there are (previously most of
    # it). The invariant is live + other == total.
    other_surface_counts: Dict[str, int] = {}
    for rec in records:
        name = _surface_of(rec)
        if name not in surfaces:
            other_surface_counts[name] = other_surface_counts.get(name, 0) + 1
    other_total = sum(other_surface_counts.values())
    legacy_unattributed = other_surface_counts.get(SURFACE_UNATTRIBUTED, 0)

    resolved_actors = sorted(
        {str(r.get("actor_id")) for r in records if r.get("actor_supplied") and _state(r) == STATE_ALLOW and r.get("actor_id")}
    )

    # --- criterion 1: every live surface carries an actor --------------------
    # Evaluated on RUNTIME records only. A live-surface criterion is a property
    # of the running system: test traffic can prove the wiring, never the
    # deployment. Summing the two would let a passing test stand in for a
    # measurement that was never taken.
    surface_detail = []
    surfaces_ok = 0
    for surface in surfaces:
        info = runtime_by_surface[surface]
        if info["records"] == 0:
            surface_detail.append(f"{surface}: no runtime records yet")
        elif info["with_actor"] == 0:
            surface_detail.append(f"{surface}: {info['records']} runtime records, none carrying an actor")
        else:
            surfaces_ok += 1
            surface_detail.append(
                f"{surface}: {info['with_actor']}/{info['records']} runtime records carry an actor {info['actor_ids']}"
            )
    surface_detail.append(
        "evidence origin: "
        + ", ".join(f"{k}={v}" for k, v in sorted(origin_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    )
    for surface in surfaces:
        tinfo = test_by_surface[surface]
        if tinfo["records"]:
            surface_detail.append(
                f"{surface} (test-origin, mechanism evidence only — not field measurement): "
                f"{tinfo['records']} records, {tinfo['with_actor']} carrying an actor"
            )
    if other_surface_counts:
        described = ", ".join(
            f"{k}={v}" for k, v in sorted(other_surface_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        surface_detail.append(
            f"outside criterion 1's scope: {described} — no non-live caller can satisfy criterion 1, "
            f"and these now say which entry path they are instead of leaving it blank"
        )
    if legacy_unattributed:
        surface_detail.append(
            f"legacy unattributed: {legacy_unattributed} record(s) written before callers named an entry "
            f"path (surface={SURFACE_UNATTRIBUTED!r}); no longer produced, and never counted as a live surface"
        )
    if total == 0:
        c1 = STATUS_INSUFFICIENT
    elif not runtime_records:
        c1 = STATUS_INSUFFICIENT
        surface_detail.append(
            "criterion 1 is a live-system property and this corpus holds no runtime-origin record, so it "
            "has NOT been measured — the test-origin rows above prove the mechanism, not the deployment"
        )
    elif surfaces_ok == len(surfaces):
        c1 = STATUS_MET
    else:
        c1 = STATUS_NOT_MET

    # --- criterion 2: refusal rate and causes are understood -----------------
    if total == 0:
        c2 = STATUS_INSUFFICIENT
        c2_detail = ["no records"]
    else:
        c2 = STATUS_JUDGEMENT
        c2_detail = [
            f"would-be refusal rate: {len(refused)}/{total} ({100.0 * len(refused) / total:.1f}%)",
            f"distinct causes: {len(causes)}",
        ] + [f"  {n:5d}  {reason}" for reason, n in sorted(causes.items(), key=lambda kv: -kv[1])]

    # --- criterion 3: no false rejections from legitimate actors -------------
    # Scoped to IN-PROCESS callers, because that is what the criterion says —
    # a live surface's refusals are criterion 1's business, not this one's.
    in_process_records = [r for r in records if _is_in_process(r)]
    runtime_in_process = [r for r in runtime_records if _is_in_process(r)]
    runtime_in_process_with_actor = [r for r in runtime_in_process if r.get("actor_supplied")]
    in_process_refused_with_actor = [r for r in runtime_in_process_with_actor if _state(r) != STATE_ALLOW]
    if not runtime_in_process_with_actor:
        c3 = STATUS_INSUFFICIENT
        c3_detail = ["no runtime-origin in-process call carried an actor, so the property is untested"]
        test_in_process_with_actor = [r for r in in_process_records if r.get("actor_supplied")]
        if test_in_process_with_actor:
            c3_detail.append(
                f"{len(test_in_process_with_actor)} test-origin in-process call(s) carried an actor "
                "(mechanism evidence only — not counted as a measurement)"
            )
    elif in_process_refused_with_actor:
        c3 = STATUS_NOT_MET
        c3_detail = [
            f"{len(in_process_refused_with_actor)} refusal(s) came from runtime in-process calls that DID carry an actor (investigate)"
        ] + [
            f"  {r.get('surface')} {r.get('tool_id')}: {r.get('reason')}"
            for r in in_process_refused_with_actor[:10]
        ]
    else:
        c3 = STATUS_MET
        c3_detail = [
            f"{len(runtime_in_process_with_actor)} runtime in-process call(s) carried an actor and none was "
            "refused (corpus-scoped evidence, not a proof)"
        ]

    # --- criterion 4: a registered surface identity exists -------------------
    if not any(r.get("actor_supplied") for r in records):
        c4 = STATUS_INSUFFICIENT
        c4_detail = ["no surface has asserted an actor yet"]
    elif resolved_actors:
        c4 = STATUS_MET
        c4_detail = [f"resolved surface identity/ies: {resolved_actors}"]
    else:
        c4 = STATUS_NOT_MET
        c4_detail = ["an actor was asserted but never resolved (unregistered, revoked, or scope-mismatched)"]
    if candidate_summary:
        c4_detail.append(
            "candidate probes (would a bootstrap identity be justified?): "
            + ", ".join(f"{k}={'registered' if v else 'not registered'}" for k, v in sorted(candidate_summary.items()))
        )
    if sentinel_candidate and not candidate_summary:
        c4_detail.append(
            "no real candidate probe recorded (only the surface-name fallback); "
            f"set {_ENV_CANDIDATE} to a candidate id to make this meaningful"
        )

    newest = max((r.get("ts") or 0 for r in records), default=None)
    oldest = min((r.get("ts") or 0 for r in records), default=None)

    return {
        "corpus": {
            "path": corpus.get("path"),
            "exists": bool(corpus.get("exists")),
            "records": total,
            "malformed": int(corpus.get("malformed") or 0),
            "oldest_ts": oldest,
            "newest_ts": newest,
        },
        "observation_enabled": shadow_enabled(),
        "surfaces": per_surface,
        "verdicts": verdicts,
        "refusals": {
            "total": len(refused),
            "rate": (len(refused) / total) if total else None,
            "causes": causes,
            "with_actor": len(refused_with_actor),
        },
        "resolved_actors": resolved_actors,
        "origins": origin_counts,
        "runtime_records": len(runtime_records),
        "test_records": len(test_records),
        "surfaces_runtime": runtime_by_surface,
        "non_live": {
            "count": other_total,
            "by_surface": other_surface_counts,
            "declared_in_process": other_total - legacy_unattributed,
            "legacy_unattributed": legacy_unattributed,
        },
        "tier_coverage": coverage,
        "criteria": [
            {"id": 1, "name": "both live surfaces pass an actor", "status": c1, "detail": surface_detail},
            {"id": 2, "name": "refusal rate and causes understood", "status": c2, "detail": c2_detail},
            {
                "id": 3,
                "name": "zero false rejections from legitimate in-process callers",
                "status": c3,
                "detail": c3_detail,
            },
            {
                "id": 4,
                "name": "a registered default identity exists and is justified as a grant",
                "status": c4,
                "detail": c4_detail,
                "open_judgement": "whether the identity's capability set is a justified grant is Wilson's call",
            },
        ],
        "note": (
            "Not a gate. This reports what the corpus supports; no box is flipped here "
            "(blueprint §14 — the researcher remains the Evidence Authority)."
        ),
    }


def shadow_identity_decision(
    *,
    surface: str,
    tool_id: str,
    tenant: str,
    session: str,
    actor_id: Optional[str] = None,
    capability: Optional[str] = None,
    required_capabilities: Tuple[str, ...] = (),
    declared_risk_class: Optional[str] = None,
    candidate_agent_id: Optional[str] = None,
    origin: Optional[str] = None,
) -> None:
    """Observe one call. Returns nothing, changes nothing, never raises.

    This is the only function the execution paths call. It has no return value
    on purpose: there is no way for a caller to act on the verdict, which is
    what keeps shadow mode from becoming enforcement by accident.
    """
    try:
        if not shadow_enabled():
            return
        get_recorder().record(
            surface=surface,
            tool_id=tool_id,
            tenant=tenant,
            session=session,
            actor_id=actor_id,
            capability=capability,
            required_capabilities=required_capabilities,
            declared_risk_class=declared_risk_class,
            candidate_agent_id=candidate_agent_id,
            origin=origin,
        )
    except Exception as exc:  # noqa: BLE001 — observation must never break a request
        logger.debug("identity shadow record failed for %s: %s", tool_id, exc)
