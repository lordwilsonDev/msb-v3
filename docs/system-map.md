# MSB-v3 System Map

Baseline: current main tree inspected 2026-09-27.

## 1. What the repository actually is

MSB-v3 is best reconstructed as a governance-and-evidence runtime with a
surrounding intelligence and execution ecosystem.

The center is the authority/evidence path:

REQUEST
  -> agent/handle.py
  -> intent + task DAG
  -> ActionGate / governance
  -> gateway + governed tools
  -> provider / local model execution
  -> grounded verification
  -> evidence receipt + Evidence Spine
  -> UAC / msb_ledger
  -> replay

## 2. Planes

| Plane | Primary systems | Role |
|---|---|---|
| Surface | api, conversation, cockpit, console, /v1 | ingress and operator-visible interfaces |
| Intelligence | moie, plei, factory, triumvirate, wrongness | propose, classify, analyze, review |
| Authority | governance, agent/safety.py, gateway, vesta | decide what may execute |
| Execution | agent, tools, local_ai, providers | bounded work |
| Evidence | evidence, uac, msb_ledger, replay, observability | prove/reconstruct |
| Persistence | SQLite, memory, memory_fabric, retrieval, Qdrant, vault | durable state |
| Operations | cron, wake, ops, runtime, launchd | residence, schedule, recovery |
| Peripheral | speech, node, device, integrations, meta, energy_matrix | optional/experimental capability |

## 3. Authority map

Models, MoIE, PLEI, factory reviewers, and providers can propose or produce work.
They do not own the final authorization decision.

ActionGate and governance are the deterministic policy surface:
capability registration, standing grants, kill switch, taint escalation,
approval requirements, and conservative unknown-capability handling.

Gateway is the auditable compute-routing entry point. It checks capabilities and
explicit authorization before choosing the local backend.

SafeProvider gates tool calls. Providers are workers, not sovereign authorities.

Operator approval remains the human authority for consequential actions and gated
mission transitions. A model PASS is not merge authority.

## 4. Evidence stack

HandleResult
  -> evidence receipt
  -> Evidence Spine
  -> UAC / msb_ledger
  -> Merkle / signed anchor / notary
  -> replay

Receipt answers what happened for one run.
Spine records causal decision provenance.
Audit chain records append-only events.
Standalone ledger supports independent verification.
Replay reconstructs from recorded events.

## 5. Why these systems exist

| Failure / need | Response |
|---|---|
| Unknown capability inherited SAFE | explicit UNKNOWN + registry + resolver + decision contract |
| Model could propose consequential action | ActionGate + governed tools |
| Compute routing lacked one audit point | Gateway |
| Ordinary logs were weak provenance | Evidence Spine + ledger + Merkle receipts |
| Planner became static ceremony | thin Triumvirate planner; real planning in agent.planner |
| Provider wiring became coupled | provider seam + configuration/plugin registration |
| Remote frontier conflicted with local-first boundary | DeepSeek/frontier retirement + local degradation |
| Docs drifted from implementation | claims gate + surface map + forensic reconciliation |
| Errors looked like success | visible failed/degraded states + tests |
| Supervisor paths died after repo move | launchd path forensic correction |
| Interactive shell differed from supervisor | portability gate + interpreter fixes |
| Runtime data needed recoverability | backups + restore drills + checksum verification |

## 6. Boundaries that must not be confused

MoIE is not the security boundary.
Worktree isolation is not a sandbox.
Surface classification is not runtime reachability proof.
A test pass is not whole-system proof.
A blueprint is not implementation.
A replay reconstruction is not a fresh execution.

## 7. Current gaps

1. Tool manifests exist, but the governance-hardening plan still records executor/gate wiring as open.
2. Shadow recording exists as a producer, but is not directly instrumented in the live handle path.
3. The 2026-09-25 Agent Control Plane blueprint remains explicitly proposed.
4. Root CONTEXT.md was absent before this organization change.
5. CLI providers remain bounded workers, not OS/process sandboxes.
6. Single-machine storage/redundancy and longitudinal performance remain material operational constraints.

## 8. Maintenance rule

When someone asks why MSB-v3 is shaped this way, start at the failure the mechanism
was designed to prevent. Then inspect the code, the test that pins the boundary,
and the observed evidence.

Preferred pattern:

OBSERVATION
  -> forensic root cause
  -> narrow correction
  -> adversarial/regression test
  -> evidence-linked documentation
  -> freeze
