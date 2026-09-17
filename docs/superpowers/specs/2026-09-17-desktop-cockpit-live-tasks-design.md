# Desktop Cockpit — Live Task View — Design Spec

**Date:** 2026-09-17
**Status:** Proposed
**Context:** `docs/superpowers/specs/` (none prior for `desktop/` — this is its first design doc). Vault checkpoint: `10_Projects/msb-v3/MSB-v3.md` → "Checkpoint — 2026-09-17: UI planning kickoff".

---

## Purpose

MSB v3 already has a real, hardened Electron desktop app (`desktop/`) wired to live endpoints — health, identity, governance approvals, killswitch, session memory, RAG search. It's a functional prototype (~740 lines, one screen, no framework), not a finished cockpit. This spec covers finishing it as the primary single-operator command center, scoped to endpoints that already exist on the backend, plus one new panel: a live view of in-flight/recent governed tasks, driven by the SSE stream the backend already exposes (`/agent/tasks/{id}/stream`) but the desktop app doesn't yet consume.

This was chosen over two other candidate UI directions — a cross-engine task/job aggregator (tasks/cron/flywheel/factory/job-board have no shared schema; bigger lift, separate future spec) and finishing the iPhone Swift client into an installable app (different backend surface, `node/v1`/Vesta, not cockpit/governance) — because it's the lowest-lift, highest-leverage path: the desktop app is already ~25–30% of the way there and wired to the right endpoints.

## Non-goals (v1)

- **No new backend endpoints.** Evidence Spine (`DecisionEvidenceStore.trail()`/`verify_chain()`) and the replay timeline are not yet exposed over HTTP; exposing them is backend work, explicitly deferred. This spec is UI-only against what already exists.
- **No cross-engine task aggregation.** `cron/`, `flywheel/`, `factory/`, and `ai-workspace/job-board` each have their own state/storage with no shared schema. The Tasks panel in this spec covers only `src/msb_v3/tasks` (`UnifiedTask`, via `/agent/tasks*`) — the one canonical task model, used by the agent-run path. Aggregating the others is a separate, larger effort.
- **No framework/build-step migration.** Renderer stays vanilla JS, matching the app's existing hardened-minimal posture (`contextIsolation`, `sandbox`, narrow 7-method preload bridge, CSP).
- **No iPhone/mobile work.** Out of scope for this spec entirely.

## Architecture

**Refactor `desktop/src/renderer/app.js` from one monolithic re-render into named per-panel functions.** Today the renderer holds one global `state` object and re-renders the whole page (template-string `innerHTML` swap) on any change. Split `render()` into `renderHealth`, `renderApprovals`, `renderKillswitch`, `renderMemory`, `renderRAG`, and new `renderTasks` — each owns its own DOM container and re-renders only that container on its own state slice changing. This is a refactor of existing working code, not a rewrite: same file, same request/response patterns for the first five panels, just named instead of inlined into one dispatcher.

**New: SSE proxy in the main process (`desktop/src/main/`).** The renderer cannot safely hold a raw `EventSource` without punching a hole in the existing no-arbitrary-network-access posture that the preload bridge enforces today (`bridge.js` wraps Node's `http` module behind 7 named preload methods; nothing in the renderer talks to the network directly). Instead:

1. Main polls `GET /agent/tasks` on an interval (proposed 5s — matches the backend's own `_PROBE_TIMEOUT_S` cadence philosophy in `api/dashboard.py`) to get the task list.
2. For any task in a non-terminal state (`CREATED`/`PLANNED`/`EXECUTING`/`VERIFYING`, per `src/msb_v3/tasks/events.py`), main opens `GET /agent/tasks/{id}/stream` itself via the same `http` client `bridge.js` already uses, attaching `Authorization: Bearer $MSB_OPERATOR_TOKEN` as a real header — cleaner than the `?token=` query-param fallback the backend offers for browser-native `EventSource` (`require_operator_sse`), since main is not a browser page and can set headers normally.
3. Parsed SSE frames forward to the renderer over a scoped IPC channel keyed by task ID.
4. The connection closes when the task lifecycle reaches a terminal state (`COMPLETED`/`FAILED`/`QUARANTINED`/`DENIED`).

**Preload additions.** Two new narrow methods alongside the existing 7 (`attach`, `health`, `cockpit`, `approvals`, `approve`, `killswitch`, `memory`, `search`):
- `listTasks()` — request/response, same pattern as `approvals()`.
- `onTaskEvent(taskId, callback)` / `offTaskEvent(taskId)` — subscribe/unsubscribe to the forwarded stream for one task. No raw `ipcRenderer` exposure via `contextBridge`, consistent with how the bridge is hardened today.

## Components

| Component | What it does | Depends on | Used by |
|---|---|---|---|
| `main/sse-client.js` (new) | Polls task list, opens/closes per-task SSE connections, exponential backoff on drop | Node `http`, existing `bridge.js` base-URL/token config | `main/index.js` |
| `main/index.js` (existing, extended) | Forwards parsed SSE events + task list to renderer over IPC | `sse-client.js` | `preload/index.js` |
| `preload/index.js` (existing, extended) | Exposes `listTasks`/`onTaskEvent`/`offTaskEvent` via `contextBridge`, same narrow-surface pattern as the 7 existing methods | `main/index.js` IPC channels | `renderer/app.js` |
| `renderer/app.js` → `renderTasks` (new) | Renders task list + expanded per-task event log from a bounded ring buffer (~200 events/task) | `state.tasks`, preload's task methods | Tasks panel DOM container |
| `renderer/app.js` → `renderHealth`/`renderApprovals`/`renderKillswitch`/`renderMemory`/`renderRAG` (existing, extracted from inline dispatcher) | Unchanged behavior, now scoped to their own container instead of full-page re-render | Existing preload methods | Their DOM containers |

## Data flow

1. On app attach, `listTasks()` populates `state.tasks` (list view: id, status, started-at, brief intent summary).
2. `renderTasks` renders the list; selecting/expanding an in-flight task triggers `onTaskEvent(taskId, cb)`.
3. Each incoming event appends to `state.tasks[id].events` (ring buffer capped ~200 — long-running tasks can't grow memory unbounded); `renderTasks` re-renders only the expanded task's event log, not the whole panel.
4. Task reaching a terminal state: main closes its SSE connection; renderer stops appending, leaves the final event log visible until the next list refresh drops it off (or the operator collapses it).
5. Every other panel (health/approvals/killswitch/memory/RAG) keeps its existing poll-and-render request/response flow, now scoped to its own container per the refactor above.

## Error handling

- **SSE drop:** exponential backoff in `sse-client.js` (1s → 2s → 4s → 8s → 16s, capped retries e.g. 5) with a small inline "reconnecting…" indicator in the expanded task's event log — non-blocking, matching the backend's own per-panel-timeout philosophy in `/cockpit/api` (one dead probe doesn't break the page).
- **Task list poll failure:** same non-blocking inline error treatment the existing panels already use — `bridge.js` returns error objects rather than throwing, so this requires no new error-handling pattern, just wiring `renderTasks` to check for it like the others do.
- **Operator token missing/invalid (401/503):** reuse existing handling already present for the token-gated panels (approvals/killswitch); no new pattern.
- **Backpressure:** if the event ring buffer for a task hits its cap, oldest events drop silently (not an error state) — a live view, not an audit log; the audit trail itself lives in the backend's `logs/audit.jsonl`, unaffected by what the UI chooses to keep in memory.

## Testing

- Extend `desktop/test/` (`node --test`, no Electron — same pattern as the existing 41 tests): unit tests for `sse-client.js`'s frame-parsing and backoff logic against a mocked `text/event-stream` response (success, mid-stream drop, terminal-state close), and for `renderTasks` against fixture state (empty list, one in-flight task with a partial event log, a task at cap).
- Manual verification: run the app (`npm run dev` / existing smoke script) against the live backend on `:8766`, trigger a real governed run via `/agent/handle`, and confirm the Tasks panel shows its events arriving live, and that closing/reopening the panel doesn't leak SSE connections (verify via backend logs or a connection-count check in `sse-client.js` itself during dev).

## Open questions (for the implementation plan, not blocking this spec)

- Exact task-list page size / retention window for `listTasks()` — depends on what `/agent/tasks` actually supports (limit/offset, time filter); confirm against the live route during planning rather than guessing here.
- Whether `renderTasks`'s list view needs a manual "show more" vs. the existing poll-refresh being sufficient — a UX call better made once the panel is running against real task volume.
