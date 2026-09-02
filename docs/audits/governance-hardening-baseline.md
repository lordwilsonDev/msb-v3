# Governance Hardening — Baseline Freeze

**Date:** 2026-09-02  
**Git commit:** `b3cbb95`  
**Source blueprint:** `docs/blueprints/governance-hardening.md`  
**Plan:** `PLAN.md`  

> This document is intentionally preserved as the **pre-fix** evidence.  
> The known failure it captures — `ActionGate().gate("nuke") → SAFE / tier 1` — is the problem the hardening work is fixing.

---

## Runtime

| Field | Value |
|-------|-------|
| version | 0.4.2 |
| PID | 2500 |
| port | 8766 |
| health | HEALTHY |
| model | qwen3:8b |
| ollama | localhost:11434 |
| database | data/msb_v3.db |

**Endpoints (live):**

- `/health` → 200
- `/ready` → 200
- `/status` → v0.4.2, ready true, model qwen3:8b, ollama localhost:11434, db data/msb_v3.db

**Process supervision:** `launchctl print gui/$(id -u)/com.lordwilson.msb-v3` → `com.lordwilson.msb-v3`, state `running`, integrity `complete`. Supervised by launchd; `scripts/run.sh` with `KeepAlive`.

---

## Gate behavior — the known failure

```python
from msb_v3.agent.safety import ActionGate

ActionGate().gate("nuke")
# → GateVerdict(allowed=True, action='SAFE', reason='brakes clear', tier=1, tainted=False, detail={})
```

This is the architectural gap the hardening blueprint is fixing:

- `"nuke"` is not in `RISK_TIERS`.
- `RISK_TIERS.get("nuke", 1)` returns `1`.
- Tier 1 is below `REVIEW_TIER (3)` and `BLOCK_TIER (4)`.
- Verdict: `SAFE`.

Same behavior for a semantically dangerous string that isn't a registered keyword:

```python
ActionGate().gate("rm -rf production")
# → SAFE / tier 1
```

**Why this matters:** the gate keys on *registered capability names*, not on *real-world intent*. A capability the registry doesn't know about inherits Tier 1 and SAFE by default. That is the `UNKNOWN → SAFE` breach.

---

## Corpus metrics (frozen)

From `tests/contracts/gate_corpus.py` + `tests/contracts/test_gate_contract.py::test_gate_precision_recall_pinned`:

| Metric | Value |
|--------|-------|
| corpus version | 20260817-1 |
| true positive | 17 |
| false positive | 8 |
| true negative | 8 |
| false negative | 23 |
| precision | 0.68 |
| recall | 0.425 |
| f1 | 0.5231 |

Pinned assertions in `test_gate_precision_recall_pinned` must continue to pass.

The layered-boundary proof (`tests/contracts/test_layered_boundary.py`) is what makes the misses safe — the gate is a pre-filter, not the security boundary. The hardening work keeps that honest framing and adds a deterministic capability boundary on top.

---

## Test snapshot

Full suite run after the freeze:

```bash
make test
```

Snapshot command (ideally captured in CI freshness, but recorded here for the baseline):

```text
3176 passed
11 skipped
37 warnings
```

The full output is too large for this doc. Record the relevant tail here when you run the baseline run:

```text
<TBD: paste from `make test` tail>
```

Known warnings still present in the suite (from the 2026-09-02 forensic grill):

- `MemoryStore` is deprecated; call sites in `core/container.py` and `tests/test_memory.py` still use the old path. Replacement: `msb_v3.memory_fabric.store`. Not a governance-blocker, but it's technical debt before declaring the governance layer production-ready.

---

## Known config warnings (informational / degraded — not blocking today)

These were assessed in the forensic grill. They are preserved here because the hardening work must not silently depend on them being resolved.

| Warning | Classification | Effect today |
|---------|----------------|--------------|
| `DOCKER` daemon not running | INFORMATIONAL | No Docker build path exercised locally |
| `N8N_API_KEY` unset | FUNCTIONAL LIMITATION | Automation-brain create-workflow path closed (create in n8n UI instead); cron jobs already firing use the existing webhook, not the create path |
| `DEEPSEEK_API_KEY` unset | DEGRADED | Wake agent / automation brain fall back to local model |
| `OPENAI_API_KEY` unset | FUNCTIONAL LIMITATION | `/v1` adapter closed (fail-closed by design) |
| `MSB_OPERATOR_TOKEN` unset | SECURITY RISK | `/cron`, `/wake`, `/automation` control surfaces return 503; operator-approval surfaces closed |
| `MCP_BRIDGE_SECRET` unset | SECURITY RISK (dev posture) | Live-auth gate disabled (dev mode) |
| disk 88% used (24.5 GiB free) | OPERATIONAL RISK | Approaching the 85% warn threshold; governance infrastructure must not silently degrade when storage fills |

Do **not** reclassify these as "informational" by default. Each one was classified deliberately.

---

## What this baseline is NOT claiming

- It is **not** claiming MSB-v3 is safe.
- It is **not** claiming the gate catches all dangerous actions. The gate is a keyword pre-filter with recall 0.425 over the frozen corpus. The honest claim is: "the gate catches registered-keyword-dangerous actions; the layered boundary catches many of the misses; the hardening work adds a deterministic capability boundary so unknown consequential capabilities are no longer SAFE by default."
- It is **not** claiming the hash proves correctness. It proves trace integrity, not semantic truth.

---

## What changes first

See `PLAN.md` → Phase 0:

1. Make `UNKNOWN` a real verdict in `ActionGate`.
2. Add the regression test that fails under the old code: `test_unknown_capability_never_defaults_to_safe`.
3. Run the suite; confirm Invariant-001 still holds over the frozen corpus.

The baseline above records the state *before* that change.

---

## Reproduce this baseline

```bash
cd /Users/lordwilson/msb-v3
git checkout b3cbb95
python3 -c "from msb_v3.agent.safety import ActionGate; print(ActionGate().gate('nuke'))"
bash scripts/start.sh status
curl -sf -m 12 http://127.0.0.1:8766/health
curl -sf -m 12 http://127.0.0.1:8766/ready
curl -sf -m 12 http://127.0.0.1:8766/status
```
