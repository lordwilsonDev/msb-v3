# MSB v3 Desktop - Architecture Contract

**Status:** Phase B scaffold, hardened. Dogfooding target, not a shipped product.
**Scope:** `desktop/` - an Electron shell that *observes and operates* the
governed MSB-v3 runtime. It adds no intelligence, no memory authority, no
governance logic.

---

## Authority

| Concern | Authority | Not |
| --- | --- | --- |
| Agent/tool execution, governance decisions, approvals, kill switch, budgets, receipts, audit chain, provenance | **MSB-v3** (`:8766`) | Electron |
| Execution / evidence memory (`/memory/*`, hash-chained) | **MSB-v3** | Electron, Vault |
| Project / decision / knowledge / workspace memory | **Obsidian Vault** (`~/Documents/Vault`), reached via `/rag/search` and the vault MCP | Electron, MSB-v3 |
| Desktop window lifecycle, presentation, ephemeral UI state | **Electron / React-free vanilla renderer** | - |

Electron is a **client**. It never becomes the source of truth for anything.
There is no second governance engine, no second audit ledger, no second
kill switch, no local memory database.

## Process ownership

```
launchd (com.lordwilson.msb-v3)  --supervises-->  msb-v3 :8766
                                                     ^
desktop app  --discover -> /health -> /status -> attach
```

- The desktop **attaches** to an already-running runtime. It does **not**
  spawn, kill, or restart it. Closing the window does not touch the runtime.
- If `:8766` is down, the desktop shows `OFFLINE` and stays useful only for
  reconnect. It never starts a fallback runtime.
- Identity handshake: `attach` requires `GET /status` to return
  `service == "msb-v3"`. A foreign service on `:8766` yields `BLOCKED`
  (`WRONG_RUNTIME`), never `READY`.

## Trust boundary

```
 renderer (sandboxed, isolated, CSP)         <-- untrusted surface
      |  window.msb.<named method>            <-- frozen, ~11 methods
      v
 preload (contextIsolation world)             <-- coerces args, no logic
      |  ipcRenderer.invoke('msb:<channel>')  <-- literal channels only
      v
 main process                                 <-- holds secrets, validates
      |  validate(channel, payload)           <-- allow-list per channel
      v
 bridge (attach-only HTTP client)             <-- loopback only, fail-closed
      |
      v
 msb-v3 :8766                                 <-- the authority
```

### Renderer isolation

- `nodeIntegration: false`, `contextIsolation: true`, `sandbox: true`,
  `webviewTag: false`, `app.enableSandbox()`.
- `devTools` only when `NODE_ENV=development`.
- CSP twice: renderer `<meta>` and a `onHeadersReceived` response header -
  `default-src 'none'; script-src 'self'; connect-src 'none'`. The renderer
  has **no network access**; every call is an IPC round-trip.
- Navigation locked: `setWindowOpenHandler` denies all; `will-navigate` /
  `will-redirect` / `will-attach-webview` prevented; `web-contents-created`
  re-applies the same lockdown to any child contents.
- All permission requests denied (`setPermissionRequestHandler`,
  `setPermissionCheckHandler`).

### Preload surface (exhaustive)

`attach, health, identity, cockpit, governanceStatus, approvals, approve,
killswitch, killswitchSet, memory, search` - each a named function mapping to
one `msb:<name>` IPC channel. No generic `invoke(channel, payload)`. The
exposed object is `Object.freeze`d.

### IPC validation

`src/main/validate.js` - one validator per channel, allow-list shaped:

- `approve.action` must be `approve` | `reject`; `killswitchSet.op` must be
  `arm` | `disarm`.
- `memory.session` matched against `^[A-Za-z0-9_.:-]+$` (no path traversal);
  `limit` bounded `[1, 500]`.
- `search.query` non-empty, <= 2000 chars; `limit` bounded `[1, 100]`.
- `attach.host` restricted to `127.0.0.1` / `localhost`.
- Control characters rejected everywhere.

Malformed payload -> `{ ok: false, error: 'IPC_VALIDATION_FAILED' }`, no
bridge call.

## Secrets

`MSB_OPERATOR_TOKEN`, `MCP_BRIDGE_SECRET`, `MSB_RAG_API_KEY` are read from the
environment **in the main process only** and attached to outbound requests by
the bridge:

| Header | Endpoints |
| --- | --- |
| `Authorization: Bearer <MSB_OPERATOR_TOKEN>` | `POST /governance/approvals/{id}/{approve,reject}`, `POST /governance/killswitch/{arm,disarm}` |
| `x-mcp-secret: <MCP_BRIDGE_SECRET>` | `GET /memory/{session}` |
| `X-API-Key: <MSB_RAG_API_KEY>` | `POST /rag/search` (when the server enforces it) |

No secret is ever returned across IPC. `hasOperatorToken()` returns a boolean
only; when false the renderer disables the approve/reject and kill-switch
controls and says so.

## Endpoint map (verified live against v0.3.1, 2026-08-28)

| Desktop action | Method + path | Auth | Notes |
| --- | --- | --- | --- |
| health | `GET /health` | - | liveness |
| identity | `GET /status` | - | `service`, `version`, `ready`, `model` |
| dashboard | `GET /cockpit/api` | - | JSON (`/cockpit` is HTML - not used) |
| governance summary | `GET /governance/status` | - | killswitch + budgets + governor + approvals count |
| approval queue | `GET /governance/approvals` | - | `{ items: [...] }` |
| approve / reject | `POST /governance/approvals/{id}/{approve,reject}` | operator | body `{ operator, reason }` |
| kill switch state | `GET /governance/status` -> `.killswitch` | - | no separate GET endpoint |
| arm / disarm | `POST /governance/killswitch/{arm,disarm}` | operator | |
| evidence memory | `GET /memory/{session}?limit=N` | `x-mcp-secret` | tagged `MSB-V3 EVIDENCE` in the UI |
| vault knowledge | `POST /rag/search` `{tenant_id, query, limit}` | optional `X-API-Key` | tagged `VAULT KNOWLEDGE` in the UI |

## Runtime states

`NOT_ATTACHED -> OFFLINE | DEGRADED | BLOCKED | READY`

- `OFFLINE` - `/health` failed.
- `DEGRADED` - health OK, `/status` failed or `ready: false`.
- `BLOCKED` - `/status` returned a non-`msb-v3` service (`WRONG_RUNTIME`).
- `READY` - health OK, identity verified, `ready: true`.

Failure is never silently rendered as success. Every bridge result is
`{ ok, error, status? }`; errors surface as stable tokens
(`MSB_UNREACHABLE`, `TIMEOUT`, `NOT_AUTHORISED`, `NOT_FOUND`,
`INVALID_REQUEST`, `MSB_UNAVAILABLE`, `NON_JSON_RESPONSE`).

## Memory provenance

The renderer shows evidence memory and vault knowledge in the same view but
**never merges them**. Each row keeps its source badge (`MSB-V3 EVIDENCE`
vs `VAULT KNOWLEDGE`). There is no combined store and no combined ranking.

## Experimental features

Speech, Energy Matrix, and the Meta-System are **not surfaced** by this app.
If they are added later they must carry an explicit `EXPERIMENTAL` label and
must not appear as production capabilities merely because a desktop exists.

## Tests

`npm test` (`node --test`, no Electron needed):

- `test/validate.test.js` - IPC input validation, malformed payloads.
- `test/bridge.test.js` - endpoint/method/header correctness against a mock
  server; fail-closed on 4xx / non-JSON / refused connection / missing token.
- `test/security.test.js` - source-level assertions: renderer isolation,
  preload surface, no `child_process`/`fs`/`fetch`, CSP + navigation locks.
- `test/governance.test.js` - proves desktop -> MSB only: no tool/provider/
  factory/agent path, every mutating call is a `/governance` endpoint with
  the operator token, no second kill switch.

Not yet wired: `tsc --noEmit` (needs `npm install`), Electron integration/E2E
launch tests, packaging.

## Security assumptions

- The operator running the desktop is trusted; `:8766` is loopback-only.
- `.env` holds the operator token; the desktop inherits it from the shell
  environment. It does not read `.env` itself.
- CLI-provider sandboxing risk (MSB-v3 C5) is unchanged - the desktop adds
  no new exposure to untrusted input; it is a local read/approve surface.
