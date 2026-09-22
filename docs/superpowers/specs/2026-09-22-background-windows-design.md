# Background Windows — Design

**Date:** 2026-09-22
**Status:** Approved design, not built
**Scope:** `desktop/` cockpit + one new read-only msb-v3 endpoint
**Goal:** See what is running in the background — inside the runtime and on the machine — from the desktop cockpit. Watch only.

## Decisions

| Question | Decision |
| --- | --- |
| Target UI | Desktop cockpit (`desktop/`, Electron, attach-only to `:8766`) |
| Coverage | Runtime internals **and** launchd agents, staged |
| Controls | **Watch only.** No start/stop/run/approve controls in this area |
| Data approach | Snapshot polling + one aggregate endpoint now; live audit stream later |

Constraints carried from `docs/desktop-architecture.md`: the cockpit is a client, holds no authority, never spawns or touches processes directly. Anything about the machine (launchd) reaches the cockpit only through an msb-v3 read endpoint.

## Window map

All windows live in a new **Background** section of the cockpit. Each can be popped out into its own `BrowserWindow`.

| # | Window | Shows | Source |
| --- | --- | --- | --- |
| 1 | Overview | One tile per subsystem and per launchd job, coloured ok/warn/fail/unknown, with "last seen". Tile click opens its window | `GET /ops/background` |
| 2 | Activity | Latest audit-chain entries as a timeline, filterable by source | `GET /cockpit/audit` |
| 3 | Scheduled jobs (cron) | Per job: schedule, enabled, running now, last result, next run, run history | `GET /cron/jobs`, `GET /cron/jobs/{id}/history` |
| 4 | Machine services (launchd) | Per agent: loaded, PID, last exit code + plain meaning, kind, schedule; last 50 log lines on click | `GET /ops/background` (`launchd`), `GET /ops/background/launchd/{label}/log` |
| 5 | Governance | Kill-switch state, budget used vs limit, pending approvals (read-only) | `GET /governance/budget`, `GET /governance/approvals`, existing kill-switch read |
| 6 | Automation & wake | Automation manifest status + dry-run flag; wake inbox/outbox counts, oldest pending item | `GET /automation/status`, `wake` in `/ops/background` |
| 7 | PLEI | Latest prediction, calibration status (pairs, error, recalibration due), last evidence-loop run | `GET /plei/status`, `GET /plei/calibrate` |

Colour rules:

- **fail (red):** a service that should run is down; a periodic job's last exit ≠ 0; kill switch armed; a cron job's last run failed.
- **warn (amber):** overdue (no run for > 2× its interval); budget > 80%; a running service whose last exit was a signal (restarted).
- **unknown (grey):** the data could not be read. Never shown as ok.

Out of the first version: Guardian (only an SBOM read route exists today — needs a status route first), Paseo agents.

## `GET /ops/background`

Files: `src/msb_v3/ops/background.py` (snapshot builder), `src/msb_v3/api/ops_background.py` (router). Operator token required (`require_operator`). No write routes.

Response:

```json
{
  "generated_at": "2026-09-22T20:10:00Z",
  "subsystems": {
    "cron":       {"state": "ok|warn|fail|unknown", "detail": {}, "error": null},
    "governance": {"state": "...", "detail": {}, "error": null},
    "automation": {"state": "...", "detail": {}, "error": null},
    "wake":       {"state": "...", "detail": {}, "error": null},
    "plei":       {"state": "...", "detail": {}, "error": null}
  },
  "launchd": [
    {
      "label": "ai.hermes.gateway",
      "loaded": true,
      "pid": 1987,
      "last_exit": 143,
      "exit_meaning": "killed by signal 15 (SIGTERM)",
      "kind": "service",
      "schedule": "on-demand",
      "log_path": "~/.hermes/logs/gateway.log",
      "state": "warn"
    }
  ]
}
```

Rules:

- **Subsystems** are read in-process from the same store/status functions the existing routes use — no HTTP self-calls. Each reader runs in its own `try`; a failure sets only that entry to `unknown` with the reason in `error`.
- **launchd** is built from one `launchctl list` call (PID, last exit) plus the plists in `~/Library/LaunchAgents` (`kind`: `service` if `KeepAlive`, or `RunAtLoad` without an interval; otherwise `periodic`; `schedule` from `StartInterval` / `StartCalendarInterval`, else `on-demand`).
  - Only labels matching a configured prefix allow-list are included. Default: `com.lordwilson.`, `com.blackswanlabz.`, `ai.hermes.`. Setting: `ops_launchd_label_prefixes`.
  - The `launchctl` runner is injectable (for tests) and has a 2 s timeout; on timeout or error the whole `launchd` section is `[]` with a top-level `launchd_error`, and the Overview shows it grey.
- **State for launchd entries:**

  | Case | State |
  | --- | --- |
  | `kind == service` and no PID | fail |
  | `kind == periodic` and `last_exit != 0` | fail |
  | Service running, `last_exit` is a signal (e.g. 143 / -15) | warn |
  | Periodic, log file mtime older than 2× interval | warn |
  | Staleness cannot be determined | unknown for staleness; other rules still apply |
  | Otherwise | ok |

- **Cache:** the snapshot is cached for 3 s so several polling windows don't multiply `launchctl` calls.

### `GET /ops/background/launchd/{label}/log?lines=N`

- `label` must be in the current allow-listed launchd set; otherwise 404.
- Reads only the `StandardOutPath` / `StandardErrorPath` from that label's plist. The client never supplies a path.
- `lines`: integer 1–200, default 50.
- Response passes through the existing redaction middleware.

## Cockpit side

**Bridge.** New named `window.msb` methods, each on its own literal `msb:<channel>` with an allow-list entry in `validate.js`:

| Method | Validation |
| --- | --- |
| `background()` | no args |
| `launchdLog(label, lines)` | `label` matches `^[a-z0-9.\-]+$`; `lines` integer 1–200 |
| `cronJobs()` | no args |
| `cronHistory(jobId)` | `jobId` is a slug |
| `pleiStatus()` | no args |

Activity reuses `cockpit()`; Governance reuses `governanceStatus()`, `approvals()`, `killswitch()`. The operator token stays in the main process.

**Renderer.** A "Background" section in `renderer/app.js` (vanilla JS, no framework), with a side list of the 7 windows.

- Only the visible window polls: 5 s for Overview and launchd, 10 s for the others.
- Polling pauses when the window is hidden or minimised.
- On a failed read: back off to 30 s and show "last good data: Ns ago" rather than blanking.
- Runtime OFFLINE / BLOCKED: all tiles grey, matching existing attach states.

**Pop-out windows.** Any view can open in its own `BrowserWindow` with the same preload and the same `sandbox`, `contextIsolation`, CSP and `nodeIntegration: false` settings as the main window. A pop-out polls only while open, and closes with the main window.

## Testing

Python — `tests/ops/test_background.py`:

- Each subsystem reader: empty store → valid entry; reader raises → that entry `unknown` with `error`, others unaffected.
- launchd parsing from fixture `launchctl list` output and fixture plists; the real `launchctl` is never called in tests (injected runner).
- State table: exit 0, signal exit on a running service, non-zero periodic, service down, stale periodic, unknown staleness.
- Allow-list: non-matching labels excluded from the snapshot.
- Log route: 404 for non-allow-listed label and traversal-looking labels; `lines` above 200 rejected; path comes only from the plist.
- Route returns 401 without the operator token.
- `launchctl` timeout → `launchd` empty + `launchd_error`, subsystems still returned.

Desktop — `desktop/test/`:

- `validate.js` rejects bad labels, out-of-range `lines`, non-slug `jobId`.
- Preload exposes exactly the expected method list.
- Polling stops when the window is hidden.

Manual, once built: open the cockpit against the live runtime; the Hermes gateway tile shows its real last exit code (143 on 2026-09-22) and the log tail is readable.

## Phases

1. `/ops/background` with subsystems only; Overview, Cron, Governance, Automation & wake, PLEI windows; bridge methods except `launchdLog`.
2. launchd section, log route, `launchdLog` bridge method, Machine services window, pop-out windows.
3. Activity moves from polling to a live SSE stream of the audit chain (reusing the cockpit's SSE client); Guardian status route and window.

## Non-goals

- Any control action (run, start, stop, restart, approve) from the Background section.
- Visibility into processes that aren't msb-v3 subsystems or allow-listed launchd agents.
- A second event store or ledger; the audit chain stays the only record.
