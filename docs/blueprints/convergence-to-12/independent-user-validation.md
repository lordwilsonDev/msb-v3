# Independent-User Validation Kit (Phase 3 / M7)

**Dated:** 2026-08-17 · **Status:** READY TO SCHEDULE — the v0.3.0-rc1 gates
(M0–M5 evidence) are green; this kit is the instrument for Phase 3. Schedule
2–3 technically capable users, a clean environment, and one constrained task
each. Observe without rescuing immediately.

## Setup guide (hand to the user)

### Prerequisites

- macOS (or Linux with the same commands), ~16 GB RAM recommended
- [Ollama](https://ollama.com) with `qwen3:8b` + `nomic-embed-text` pulled
- Python 3.11+ (3.12 recommended)
- [Qdrant](https://qdrant.tech) running locally (default `localhost:6333`)
- A vault directory (any folder of markdown notes — the system indexes it)

### Install

```bash
git clone <repo-url> msb-v3 && cd msb-v3
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-runtime.lock -r requirements-dev.lock
cp .env.example .env        # set MSB_VAULT_PATH, MSB_OPERATOR_TOKEN, OLLAMA_URL
make server-start           # or: bash scripts/start.sh start
```

### First run (the canonical task)

```bash
TOKEN=$(grep '^MSB_OPERATOR_TOKEN=' .env | cut -d= -f2-)
curl -X POST http://127.0.0.1:8766/agent/handle \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"request":"Search the vault for recent decisions about the sovereign stack and summarize them. Do not write any files."}'
```

Expected: a `PASS` verdict with a `deterministic_hash`, a `trace` containing
intent, task DAG, tool events, and verification.

### Reading the evidence

- `GET /agent/tasks/<run_id>/replay` → event-sourced reconstruction
- `GET /metrics/prometheus` → queries, latency, verdicts, retries, recoveries
- `data/uac/audit_chain.db` → append-only chain; `verify_anchored` validates

### Governance in one line

Every consequential action passes a Guard that returns ALLOW / APPROVE
(review) / DENY — a denied action never executes, and the denial is recorded.

## Validation protocol (operator's script)

For each user, in order:

1. **Clean setup.** Give them the setup guide and a fresh environment.
   Measure: time-to-first-run, which steps needed help (record verbatim).
2. **Constrained task.** One of:
   - *Research-to-note:* "Find what the vault says about X and write a
     one-page note under artifacts/ (ask permission when prompted)."
   - *Investigation-to-patch:* "Inspect the codebase's handling of Y, propose
     a one-line fix, and summarize the blast radius."
3. **Governance probe.** Ask them to attempt a write the system should deny
   (or arm the kill switch first via `/governance/killswitch/arm`). Then ask:
   "Why was this allowed / denied / escalated?" — they must be able to answer
   from the evidence, not guess.
4. **Failure probe.** Stop Ollama mid-task. Expected: bounded retries then a
   safe, visible halt — never a hang or a silent wrong answer.
5. **Debrief.** Record observations, not opinions (see log template).

## Observer log template

| Field | Notes |
|---|---|
| User / date / environment | |
| Setup time-to-first-run | |
| Setup friction (verbatim) | |
| Task + outcome (complete/partial/failed) | |
| Interventions required (what, when, why) | |
| Governance explanation accuracy | allowed/denied/escalated — correct? |
| Failure behavior observed | bounded? visible? recoverable? |
| Trust signals (what they trusted, what they distrusted) | |
| Missed controls / bypass attempts | |
| Keep / change / cut decisions (with owner + date) | |

## Exit criteria (from the plan)

- [ ] Setup is reproducible: a clean machine reaches the first useful run
      from documentation alone.
- [ ] At least one user reaches a useful result without live intervention.
- [ ] Users can explain why an action was allowed, denied, or escalated.
- [ ] Failures are legible: users recover or correctly escalate instead of
      bypassing controls.
- [ ] Findings become explicit keep / change / cut decisions (recorded here
      and in `MILESTONES.md`).

## Gate

Independent validation begins only after the 30-day personal trial shows
positive net value (completion rate up, intervention rate down, measurable
time saved). The kit is ready now; the schedule is gated on M6 evidence.
