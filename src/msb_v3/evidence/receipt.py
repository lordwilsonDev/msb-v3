'''Evidence receipt - one JSON-able record reconstructing a governed run.

Complements the Evidence Spine: the spine stores the causally-linked
vertebrae (decision -> execution -> verification); the receipt is the single
document that answers, for one request:

    what was requested -> what was allowed -> what actually happened ->
    why it was allowed -> whether it succeeded.

Built from two already-tamper-evident sources:

- the HandleResult (run_id, verdict, deterministic_hash, model_calls, and
  the trace, which carries the intent and the MoIE verdict);
- the spine trail for the run (each vertebra carries policy_version,
  policy_result, capability, timestamp, and the chain-linked content_hash).

A quick-reject BLOCK has no execution, but the gate writes a DENY decision
vertebra, so a denied request is reconstructable exactly like an executed
one - the receipt's authorization_decision reads DENY and the audit hash is
the decision vertebra's content hash.

The receipt is a pure composition function: it never writes anything itself.
Callers decide where to persist it (JSONL audit log, API response, ...).

Evidence language: every report distinguishes what was DIRECTLY RERUN from
what was INFERRED FROM LOGS. The ``verification`` section carries both
honestly:

- ``basis: "rerun"`` — grounded verification checks executed against ground
  truth (file exists, search returned hits, synthesis non-empty) and a
  deterministic hash recomputed from the recorded trace. These claims are
  re-derivable: a verifier can re-execute the checks and recompute the hash.
- ``basis: "decision-only"`` — denied before execution; nothing was rerun
  and the DENY decision vertebra is the evidence.
- ``log_inference`` — always present but explicitly labeled: state
  derivation, transition legality, projection consistency, and the decision
  trail are reconstructed from the event log (the replay engine's job, at
  /agent/tasks/{run_id}/replay), never claimed as re-execution.
'''

from __future__ import annotations

from typing import Any, Dict, List, Optional

from msb_v3.evidence.spine import DecisionEvidenceStore

# Matches handle.py's _SPINE_POLICY_VERSION (kept in sync manually - the
# receipt must not import the agent package and create a dependency cycle).
_DEFAULT_POLICY_VERSION = "handle-gate-v1"


def _moie_verdict(trace: Dict[str, Any]) -> Optional[str]:
    '''The MoIE verdict recorded on the run: from the quick-reject inversion
    detail (BLOCKED) or the executed-path moie detail (proceeded).'''
    inversion = trace.get("inversion")
    if isinstance(inversion, dict):
        verdict = inversion.get("verdict")
        if verdict:
            return str(verdict)
    moie = trace.get("moie")
    if isinstance(moie, dict):
        verdict = moie.get("verdict")
        if verdict:
            return str(verdict)
    return None


def _grounded_checks(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The per-task grounded verification receipts recorded on the trace.

    Each one is a deterministic check against ground truth (search returned
    hits, synthesis non-empty, file written) — the claims that were directly
    rerun at execution time and can be re-executed by a verifier.
    """
    checks: List[Dict[str, Any]] = []
    for entry in trace.get("execution") or []:
        if not isinstance(entry, dict):
            continue
        verification = entry.get("verification")
        if isinstance(verification, dict) and verification.get("kind") == "grounded":
            checks.append(
                {
                    "task_id": entry.get("task_id"),
                    "check": verification.get("check"),
                    "verdict": verification.get("verdict"),
                    "trust": verification.get("trust"),
                }
            )
    return checks


def _hash_recomputed(trace: Dict[str, Any], recorded: str) -> Optional[bool]:
    """Recompute the deterministic hash from the recorded trace and say
    whether it matches the recorded hash.

    The hash is a pure function of the trace (request/intent/plan/execution/
    verdict), so recomputing it is itself a rerun — a verifier can do the
    same from the stored trace. Returns None when there is no recorded hash
    to check (a denial or an error before execution) or the recompute cannot
    run.
    """
    if not recorded:
        return None
    try:
        from msb_v3.agent.trace import compute_deterministic_hash

        return compute_deterministic_hash(trace) == recorded
    except Exception:  # noqa: BLE001 — provenance must never break the receipt
        return None


def build_evidence_receipt(
    *,
    run_id: str,
    verdict: str,
    error: Optional[str],
    deterministic_hash: str,
    trace: Dict[str, Any],
    model_calls: int,
    spine: Optional[DecisionEvidenceStore] = None,
) -> Dict[str, Any]:
    '''Compose the full evidence receipt for one run.

    spine is optional and used to enrich the receipt with the stored
    vertebrae (policy version, authorization decision, capabilities,
    timestamps, and the chain-linked content hash). Without it the receipt
    still stands on the HandleResult alone, with the spine fields left as
    their honest fallbacks.
    '''
    decision = execution = verification = None
    if spine is not None:
        try:
            for record in spine.trail(run_id):
                kind = record.evidence.kind
                if kind == "decision" and decision is None:
                    decision = record
                elif kind == "execution" and execution is None:
                    execution = record
                elif kind == "verification" and verification is None:
                    verification = record
        except Exception:
            # A spine outage must never break the receipt - fall back to the
            # HandleResult-only reconstruction.
            decision = execution = verification = None

    denied = verdict == "BLOCKED"
    authorization_decision: Optional[str]
    policy_version: str
    audit_hash: Optional[str]
    if decision is not None:
        authorization_decision = decision.evidence.policy_result
        policy_version = decision.evidence.policy_version
        capability_requested = list(decision.evidence.capability_requested)
        capability_granted = list(decision.evidence.capability_granted)
        audit_hash = decision.content_hash
        decision_ts = decision.evidence.timestamp
    else:
        authorization_decision = "DENY" if denied else None
        policy_version = _DEFAULT_POLICY_VERSION
        capability_requested = []
        capability_granted = []
        audit_hash = deterministic_hash or None
        decision_ts = None

    intent = trace.get("intent")
    timestamps = {
        "decision": decision_ts,
        "execution": execution.evidence.timestamp if execution is not None else None,
        "verification": verification.evidence.timestamp if verification is not None else None,
    }

    moie_verdict = _moie_verdict(trace)
    why_allowed = (
        "MoIE quick-reject BLOCK - denied before any model call"
        if denied
        else f"MoIE verdict {moie_verdict}; authorization decision {authorization_decision}"
    )

    requested_desc = (
        "nothing (denied at gate)"
        if denied
        else (", ".join(capability_requested) if capability_requested else "not recorded (no spine trail)")
    )
    allowed_desc = authorization_decision or ("DENY" if denied else "not recorded")

    # Evidence language: what was directly rerun vs what was inferred from
    # logs (see the module docstring). Executed runs (PASS/FAIL) carry the
    # grounded checks executed against ground truth plus the hash recomputed
    # from the recorded trace (basis="rerun"); a quick-reject denial has
    # nothing to rerun (basis="decision-only" — the DENY vertebra is the
    # evidence); an error before execution has neither. The log-inference
    # surface is always labeled separately: state derivation and the decision
    # trail are reconstructed in the replay engine from the event log — never
    # claimed here as re-execution.
    executed = verdict in ("PASS", "FAIL")
    if executed:
        basis = "rerun"
        grounded_checks = _grounded_checks(trace)
        hash_recomputed = _hash_recomputed(trace, deterministic_hash)
        note = (
            "grounded checks were executed against ground truth and the deterministic hash "
            "recomputes from the recorded trace — these claims are rerun, not inferred"
        )
    elif denied:
        basis = "decision-only"
        grounded_checks = []
        hash_recomputed = None
        note = "denied before execution — no grounded checks to rerun; the DENY decision vertebra is the evidence"
    else:
        basis = "none"
        grounded_checks = []
        hash_recomputed = None
        note = "no execution occurred — nothing to rerun or infer"

    verification_section: Dict[str, Any] = {
        "basis": basis,
        "hash_recomputed": hash_recomputed,
        "grounded_checks": grounded_checks,
        "note": note,
        "log_inference": {
            "basis": "inferred-from-logs",
            "covers": ["derived state", "transition legality", "projection consistency", "decision trail"],
            "where": f"/agent/tasks/{run_id}/replay",
        },
    }

    receipt: Dict[str, Any] = {
        "request_id": run_id,
        "intent": intent,
        "moie_verdict": moie_verdict,
        "policy_version": policy_version,
        "authorization_decision": authorization_decision,
        "capability_requested": capability_requested,
        "capability_granted": capability_granted,
        "execution_result": {"ok": verdict == "PASS", "verdict": verdict, "error": error},
        "verification_result": deterministic_hash or None,
        # What was directly rerun vs inferred from logs (see module docstring).
        "verification": verification_section,
        "timestamps": timestamps,
        "model_calls": model_calls,
        "audit_hash": audit_hash,
        # The one-line reconstruction the receipt exists to answer.
        "reconstruction": (
            f"request={run_id} requested={requested_desc} "
            f"allowed={allowed_desc} happened={verdict} "
            f"why={why_allowed} succeeded={verdict == 'PASS'} "
            f"verified={basis}"
        ),
    }
    return receipt
