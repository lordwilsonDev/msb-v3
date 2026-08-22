"""Evidence Spine — structured, causally-linked decision provenance.

A ``DecisionEvidence`` record that links a governed decision to its task,
policy, capabilities, evidence refs, and the
AuditChain record that recorded it (``audit_seq``), with content-addressing
(``content_hash``) and a causal parent chain (``parent_hash``).
"""
