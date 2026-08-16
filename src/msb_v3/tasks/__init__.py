"""Unified task object + event-sourced lifecycle (unified-architecture §27-28).

§27: everything eventually reduces to one durable task object — identity,
intent, context, assumptions, inversion, plan, agents, capabilities,
contracts, approvals, execution, observations, verification, evidence,
audit, memory, outcome, recovery.

§28: every important state transition becomes an event
(TASK_CREATED ... TASK_COMPLETED) and the AuditChain records the
authoritative sequence. The lifecycle store is a *derived projection*
(runtime/store.py philosophy): the hash-chained UAC chain is the record,
the sqlite store is the query convenience.
"""
