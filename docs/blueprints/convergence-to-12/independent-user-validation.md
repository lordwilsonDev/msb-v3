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
git clone https://github.com/lordwilsonDev/msb-v3.git msb-v3 && cd msb-v3
# HTTPS, not SSH — SSH requires a configured key (dry-run friction finding,
# 2026-08-17: the plain `git clone <repo-url>` form failed on SSH).
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-runtime.lock -r requirements-dev.lock
cp .env.example .env        # set MSB_VAULT_PATH, MSB_OPERATOR_TOKEN, OLLAMA_URL
make server-start           # or: bash scripts/start.sh start
```

Notes (from the 2026-08-17 operator dry-run on a fresh clone):
- `run.sh` now prefers the checkout's own `.venv/bin/python` when present
  (the venv the guide creates), so the locks you just installed are the ones
  that run — no dependence on a machine-wide Python.
- `start.sh` renders the launchd plist from a path-neutral template
  (`__MSB_REPO__` placeholder) before bootstrap, so the agent points at THIS
  checkout wherever it lives — a hardcoded machine path made the agent
  un-startable on any other machine (the blocker this dry-run caught).
- Port collision (dry-run #2 catch, 2026-08-17): if something already serves
  :8766, set `MSB_PORT` to a free port in `.env` and use that port in the
  curl below. The launchd LABEL is machine-global, so if another checkout's
  agent is already loaded, `start.sh` REFUSES to displace it and tells you
  to run this checkout in standby instead:
  `MSB_PORT=<free> nohup bash scripts/run.sh &` (the dry-run used 8767).

### First run (the canonical task)

```bash
TOKEN=$(grep '^MSB_OPERATOR_TOKEN=' .env | cut -d= -f2-)
PORT=${MSB_PORT:-8766}
curl -X POST http://127.0.0.1:$PORT/agent/handle \
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

## Kickoff schedule (added 2026-08-17, trial rolling)

Week-1 M6 data already points the right way (86.7% completion, zero
approve/retry/bypass interventions, ~10 min saved per task, evidence
useful 15/15), so the gate is expected to pass — but the plan's rule is
30 days of evidence, so the kickoff is scheduled around the trial's end
with prep running in parallel NOW.

| When | What | Owner | Evidence |
|---|---|---|---|
| 2026-08-17 → 08-28 | **Recruit 2–3 candidates.** Technically capable; can run a terminal + clone a repo. Ask: available for one 60–90 min session in the 09-17 → 10-01 window; have a folder of markdown notes to use as a vault. | Wilson | names + session slots in the observer log |
| By 2026-09-05 | **Dry-run the kit on a clean machine.** Fresh venv + clone, run the setup guide + canonical task + governance/failure probes exactly as a user would (the operator is user #0). Every friction point found here is one a real user won't hit. | Wilson | updated setup guide (commands that actually work from a virgin checkout — same discipline as `make portability` and the M7 kit's own `make server-start` fix) |
| 2026-09-12 | **M6 gate check (4th Friday review).** Rollup: completion ≥90% supported, intervention burden not rising, time saved positive. If the gate passes → confirm kickoff. If not → delay M7 and narrow the workflow per m6-trial.md. | Wilson | `scripts/trial-rollup.py` output recorded in MILESTONES.md M6 row |
| 2026-09-17 → 10-01 | **M7 validation window.** 2–3 sessions (one per user), observer log per the protocol above; at least one user must reach a useful result without live intervention; every session records keep/change/cut findings. | Wilson | observer log rows per user |
| 2026-10-02 | **M7 exit review.** Combine the session findings into explicit keep/change/cut decisions; feed into M8 final release decision (dated). | Wilson | decisions recorded here + MILESTONES.md M7 row |

## Gate

Independent validation begins only after the 30-day personal trial shows
positive net value (completion rate up, intervention rate down, measurable
time saved). The kit is ready now; the schedule above respects that gate:
prep (recruit + dry-run) runs during the trial, sessions start 09-17, and
the 09-12 review is the formal go/no-go.
