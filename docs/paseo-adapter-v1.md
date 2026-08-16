# MSB ↔ Paseo Adapter — Design v1

**Status:** IMPLEMENTED v1 + LIVE-VERIFIED (2026-08-15) — client, adapter, permission broker, provider, API, tests (25), suite 1013 → 1039. Verified against the **running daemon** (`~/paseo` built, launchd `com.lordwilson.paseo`, 127.0.0.1:6767): health `paseo: HEALTHY`, providers discovery, 401/422 gates, worktree creation in the target repo, permission park + API decision, kill. Live testing found and fixed 3 real bugs (cross-instance wake, single-forwarder contract, repo passthrough) + the delegation lifecycle transition path.
**Date:** 2026-08-15
**Spec:** Unified Sovereign Agent Architecture §7 (Paseo integration), §14–16 (provider abstraction, agent identity)
**Grounded in:** `~/paseo` @ `bf769158` (getpaseo/paseo — the repo cloned from `tianyicui/paseo`), source reads, not docs claims.

---

## 1. Ground truth: what the real Paseo exposes

Paseo is a **daemon** (`packages/server`) that manages Claude Code / Codex / OpenCode agents. Verified in source:

- **HTTP app** with an MCP endpoint mounted at **`/mcp/agents`** (`bootstrap.ts:417`, `POST`/`GET`/`DELETE`, Streamable HTTP transport, `mcp-session-id` header for sessions, `mcpEnabled` default true).
- The MCP server registered there (`agent/mcp-server.ts`, `createAgentMcpServer`) exposes the **full agent-management surface** plus worktree/terminal tools.
- A second, richer MCP server (`agent/agent-management-mcp.ts`, `createAgentManagementMcpServer`) exists **in-memory only** — wired to the voice assistant LLM in-process, **not** reachable externally. MSB must not target it.
- `AgentStatusEnum` (`mcp-shared.ts:19`): `initializing | idle | running | error | closed`.
- Providers: `claude | codex | opencode` (`AgentProviderEnum`), each with modes (`plan`, `bypassPermissions`, `read-only`, `auto`, …) and `list_models`.
- **Worktree lifecycle is first-class**: `create_agent` accepts `worktreeName` + `baseBranch` → `createAgentWorktree` (git worktree, setup/teardown hooks, per-worktree runtime env `PASEO_WORKTREE_PATH` etc.); the daemon tracks checkout diffs (`checkout-diff-manager.ts`) and exposes worktree list/delete tools. This is the spec's "isolated worktree" for Paseo agents.

**Verified MCP tool surface** (`mcp-server.ts` + `agent-management-mcp.ts`, ~25 tools). The ones the adapter needs:

| Tool | Inputs (verified) | Purpose |
|---|---|---|
| `create_agent` | `cwd`, `title`, `provider?`, `model?`, `thinking?`, `labels?`, `initialPrompt?`, `mode?`, `worktreeName?`, `baseBranch?`, `background?` | Create agent, optionally in a git worktree, optionally start a task |
| `send_agent_prompt` | `agentId`, `prompt`, `sessionMode?`, `background?` | Send task to an existing agent |
| `wait_for_agent` | `agentId` | Block until permission request or run completion |
| `get_agent_status` | `agentId` | Status + full snapshot (lifecycle, cwd, mode, permission) |
| `list_agents` | — | All live agents |
| `cancel_agent` | `agentId` | Abort current run, keep agent alive |
| `kill_agent` | `agentId` | Terminate session permanently |
| `archive_agent` | `agentId` | Soft-delete, interrupt if running |
| `update_agent` | `agentId`, `name?`, `labels?` | Rename / relabel |
| `set_agent_mode` | `agentId`, `modeId` | plan / default / acceptEdits / bypassPermissions / read-only / auto / full-access |
| `get_agent_activity` | `agentId`, `limit?` | Curated timeline summary |
| `list_pending_permissions` | — | Pending permission requests across all agents |
| `respond_to_permission` | `agentId`, `requestId`, `response` | Approve / deny a permission request |
| `list_providers` / `list_models` | `provider?` | Capability discovery |
| worktree tools | (in `mcp-server.ts`) | `create_worktree`, `list_worktrees`, `archive_worktree` |
| terminal tools | (in `mcp-server.ts`) | `create_terminal`, `kill_terminal`, `capture_terminal`, `send_terminal_keys`, `list_terminals` (not used by v1 adapter) |

---

## 2. Transport decision

**MSB connects to the daemon's `/mcp/agents` endpoint as an MCP client over Streamable HTTP.** No changes to Paseo required.

- JSON-RPC 2.0 over HTTP; session lifecycle via `mcp-session-id` header (`initialize` → `notifications/initialized`, then `tools/call`).
- Implemented on **`httpx==0.28.1`** (already a runtime dep) — no new dependencies, no `mcp` package needed.
- Configuration: `MSB_PASEO_URL` (default `http://127.0.0.1:18765/mcp/agents` — daemon's bound port discovered at runtime; configurable), `MSB_PASEO_TIMEOUT`.
- If the daemon is down: adapter reports `FAILED` (health component), never silent fallback. Consistent with §15 (no silent fallback).

---

## 3. Six-method mapping (spec §7)

| Spec method | Paseo call | Notes |
|---|---|---|
| `create_task` | `create_agent` with `title`, `cwd`, `worktreeName`+`baseBranch`, `initialPrompt` = task body, `background: true` | Worktree is **mandatory** for code tasks — that's the isolation boundary. Returns `paseo_agent_id`. |
| `assign_agent` | `create_agent` with `provider` + `model` (from `list_models`/`list_providers`); or `update_agent` on an existing agent | MSB `AgentRegistry` entry gains `paseo_agent_id`, provider_id `paseo.claude` / `paseo.codex` / `paseo.opencode`. |
| `send_task` | `send_agent_prompt` (`agentId`, `prompt`, `sessionMode`) | Default `background: true`; MSB polls or `wait_for_agent`. |
| `monitor` | `get_agent_status` + `get_agent_activity` (poll); `wait_for_agent` (block) | Status/activity polled into the unified task's observations; blocking wait used by the executor. |
| `interrupt` | `cancel_agent` (abort run, keep alive) | `kill_agent` reserved for operator-initiated revocation via `POST /agent/agents/{id}/revoke`. |
| `retrieve_result` | `get_agent_status` snapshot + checkout diff | Result = worktree diff vs `baseBranch` (the daemon's `checkout-diff-manager` already computes it) + final `lastMessage` + exit status. |

Mode mapping (spec autonomy ladder → Paseo modes):

| MSB autonomy | Paseo `modeId` | Governor posture |
|---|---|---|
| L0–L1 observe/suggest | `plan` | no execution |
| L2 plan | `plan` | plan returned for approval |
| L3 execute w/ approval | `default`/`auto` | **every `respond_to_permission` gated on MSB operator/Vesta approval** |
| L4 execute within policy | `acceptEdits`/`auto` | policy-scoped auto-approval only for whitelisted capabilities |
| L5 delegated | **never** from MSB | requires operator grant; `bypassPermissions` NOT exposed |

---

## 4. Governance integration (the non-negotiable)

Spec §5 rule: *no tool may be registered directly with the model unless its execution path terminates inside the governance perimeter.* The adapter is a **provider**, not a bypass:

- `PaseoProvider` is a new `AgentProvider` (kind `"paseo"`) in `agent/providers.py`, registered in `ProviderRegistry` like `LocalAgentProvider`/`CliAgentProvider`. It is **HIGH risk tier** (worktree isolation is not a sandbox — the honest documentation used for `CliAgentProvider` applies identically). Operator registration required.
- Every adapter call executes as a **governed tool dispatch**: capability check (`ActionGate` `granted` whitelist) → taint evaluation → AuditChain `TOOL_EXECUTED` record → event-sourced task event. A paseo call with a capability outside the agent's grant is **BLOCKED fail-closed** (`capability not granted to this agent: paseo.*`).
- **Permissions are the Governor's, not the worker's.** Paseo permission requests (`list_pending_permissions` → `respond_to_permission`) do NOT flow to the agent; they surface as **MSB approvals**:
  1. Paseo raises a permission request (read/write/shell).
  2. Adapter maps it to a durable approval request (reusing Vesta's approval machinery — same store, `source="paseo"`).
  3. Operator approves/denies via the existing operator-gated endpoint (or policy auto-approves at L4 within whitelist).
  4. Only an approved response is forwarded via `respond_to_permission`. Denial → agent run cancelled + quarantined if taint escalated.
- Kill switch integration: `STOP paseo.{agent_id}` (scoped) → `cancel_agent`; global arm → no paseo dispatch at all.

---

## 5. Module layout (msb-v3, convergent — no new subsystems)

```
src/msb_v3/agent/paseo/
  client.py      # MCP Streamable HTTP client (httpx, JSON-RPC, session mgmt)
  adapter.py     # PaseoAdapter: create_task/assign_agent/send_task/monitor/interrupt/retrieve_result
  permissions.py # permission request <-> MSB approval mapping (Vesta store reuse)
src/msb_v3/agent/providers.py   # + PaseoProvider (kind="paseo", HIGH tier)
src/msb_v3/api/agent.py         # + provider passthrough endpoints (operator-gated):
                                #   POST /agent/paseo/create, /send, /interrupt; GET /agent/paseo/status
tests/agent/test_paseo_adapter.py  # fake daemon (JSON-RPC fixture), hermetic
```

`client.py` speaks only the verified tool names/schemas from §1 — no guessing.

---

## 6. Event / audit integration

The adapter rides the existing Phase 2 lifecycle — a paseo run is a **UnifiedTask** with `agents: [{provider: paseo.claude, paseo_agent_id, autonomy_level}]`:

```
TASK_CREATED → INTENT_INTERPRETED → PLAN_CREATED → AGENT_STARTED
→ TOOL_REQUESTED(create_agent) → TOOL_EXECUTED → MUTATION_COMMITTED (worktree created)
→ PERMISSION_REQUESTED (if any) → APPROVAL_GRANTED/DENIED
→ VERIFICATION_STARTED → VERIFICATION_PASSED/FAILED → EVIDENCE_RECORDED → TASK_COMPLETED
```

All mirrored to the AuditChain (`component="paseo"`, `paseo_agent_id` in payload) — same pattern as the Phase 2 live run (seqs 9473–9491).

## 7. Verification

MSB does not trust the worker's own "done". After `retrieve_result`:
1. Diff/result retrieved from the worktree.
2. MSB's verify step runs against the diff (tests/checks per task's acceptance criteria — same `verify.py` used for the local slice).
3. `VERIFICATION_PASSED`/`FAILED` recorded with artifact hash. §9 (no consensus without evidence) applies unchanged: the paseo agent's completion claim is a claim, not evidence.

## 8. Failure model

- Daemon down → `FAILED`, health component `paseo` = `FAILED` (extends `/system/health`), no fallback to `ok=True`.
- `wait_for_agent` timeout → `FAIL`, task `FAILED`, retry policy per task (max 2), then quarantine (state + evidence preserved, no further mutation).
- Permission request unanswered (operator timeout) → agent stays parked; task status `QUARANTINED` after timeout, not silently completed.
- Chain outage → provenance degraded (best-effort), run never broken — same rule as the local lifecycle.

## 9. Deliberately NOT built (spec §32)

- No new MCP server, no new audit store, no new approval store (reuses Vesta), no new task store (reuses `tasks/`), no new registry (reuses `AgentRegistry` + `ProviderRegistry`). Paseo's own schedules/terminals/voice are left to Paseo; MSB schedules via its existing launchd/automation layer and drives Paseo as executor only.

## 10. Test plan

1. `client.py` — JSON-RPC framing, session init, error mapping (fake HTTP fixture).
2. `adapter.py` — six methods against a fake daemon (in-process JSON-RPC responder), asserting exact tool names/params.
3. `providers.py` — `PaseoProvider` capability gating: read-only agent blocked fail-closed.
4. Permission mapping — fake daemon raises permission request → approval record created → only approved response forwarded; denial path.
5. Lifecycle integration — full handle() run with a fake paseo worker emitting `TOOL_EXECUTED` events on the chain.
6. Live smoke (opt-in `MSB_LIVE_TESTS=1`) — real daemon if running.

## 11. Implementation order

1. `client.py` + hermetic tests (fake daemon).
2. `adapter.py` six methods + tests.
3. `PaseoProvider` + capability gating + tests.
4. Permission↔approval mapping + Vesta store reuse + tests.
5. Lifecycle/event wiring + API passthrough endpoints + full suite.
6. Live verification against a running daemon (if/when Paseo is running on this machine) + deploy via launchd.

**Decision needed before build:** whether to auto-approve L4 permission requests within a whitelist (default: NO — all approvals operator-gated until SAS thresholds are set, spec §21/§22).
