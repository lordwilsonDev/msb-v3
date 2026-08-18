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
'''

from __future__ import annotations

from typing import Any, Dict, Optional

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
        "timestamps": timestamps,
        "model_calls": model_calls,
        "audit_hash": audit_hash,
        # The one-line reconstruction the receipt exists to answer.
        "reconstruction": (
            f"request={run_id} requested={capability_requested or 'nothing (denied at gate)'} "
            f"allowed={authorization_decision} happened={verdict} "
            f"why={why_allowed} succeeded={verdict == 'PASS'}"
        ),
    }
    return receipt
