# REPO_REQUIREMENTS

What the GitHub repository hosting `msb-v3` must provide for the CI stack to
run. This repo is currently **public** (`lordwilsonDev/msb-v3`), so hosted
Actions minutes are unlimited; the only machine-dependent piece is the
self-hosted runner that powers the browser + evidence gate.

## CI overview

| Workflow | Runs on | Hard requirements | Notes |
|---|---|---|---|
| `ci.yml` | hosted `ubuntu-latest` | Actions enabled | tests (Py 3.11/3.12), ruff, mypy, pip-audit, SBOM, licenses, claims; docker job builds the real image + container `/health` smoke; deploy job skips until secrets exist |
| `factory-gate.yml` | hosted `ubuntu-latest` | **`MSB_MCP_SECRET` secret** | pytest w/ 65% coverage gate, hygiene suite, live auth probe, conversation E2E (stub, zero-spend); the auth + E2E probes fail-closed on the secret |
| `harness-gate.yml` | **self-hosted** `[self-hosted, macOS]` | a registered runner + machine-local state | browser endpoints + video-harness evidence gate; uploads `harness-evidence-report.json` |
| `make harness-gate-dryrun` | local | nothing extra | pre-push mirror of the harness-gate steps (probes + gate, minus upload) |

## Required setup

### 1. Repo + Actions

- Repository is **public** (unlimited hosted minutes). If this changes to
  private, hosted runners drop to the free tier (~2,000 min/month) — the
  self-hosted runner is not affected.
- **Settings → Actions → General → Allow all actions and reusable workflows**.
- **Workflow permissions: Read and write** (needed for the deploy job; the
  artifact uploads work with either setting).

### 2. Self-hosted runner (the only Mac-side step)

`harness-gate.yml` targets `runs-on: [self-hosted, macOS]` — GitHub matches
ALL listed labels, so the runner must carry both.

1. **Settings → Actions → Runners → New self-hosted runner** → macOS → ARM64.
2. Copy the `./config.sh` line from that page and add the `macOS` label:
   ```bash
   ./config.sh --url https://github.com/lordwilsonDev/msb-v3 --token <TOKEN> \
     --labels self-hosted,macOS --work _work
   ```
   (`self-hosted` is applied automatically; the command line above is just
   the labeled form.)
3. Start it, preferably as a service so it survives reboots:
   ```bash
   ./run.sh                      # foreground
   # or as a launchd service:
   ./svc.sh install && ./svc.sh start
   ```
4. **Run it as the same account the dev stack lives under** (`lordwilson`).
   `$HOME` is load-bearing: `~/bin/webcheck.py`, `~/video-harness/evidence`,
   miniforge python, and system Chrome all resolve from the runner's home.
   A runner under any other account fails with confusing not-found errors.

Until the runner is registered, `harness-gate` stays queued; the hosted
workflows run immediately.

### 3. `MSB_MCP_SECRET` — the one required secret

- **Settings → Secrets and variables → Actions → New repository secret**:
  - Name: `MSB_MCP_SECRET`
  - Value: the `MCP_BRIDGE_SECRET` the server accepts — from `~/msb-v3/.env`
    (the `MCP_BRIDGE_SECRET=` line) or the shipped default in
    `scripts/webcheck.sh`.
- `factory-gate.yml`'s auth/E2E probes are **fail-closed**: they exit non-zero
  if the secret is missing, deliberately failing the job.
- `harness-gate.yml` uses it only when the live server runs a custom secret;
  a server on the shipped default needs no secret at all.

### 4. Marketplace actions

The workflows use these standard actions (fine on github.com; only relevant
if you ever mirror the marketplace):
`actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4`,
`dorny/paths-filter@v3`, `codecov/codecov-action@v4`,
`docker/setup-buildx-action@v3`, `docker/build-push-action@v5`.

## Optional setup

| Piece | Effect |
|---|---|
| `production` environment (Settings → Environments) | deploy job uses it |
| `DEPLOY_SSH_KEY`, `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH` secrets | activates the deploy job (SSH pull + restart); it **skips cleanly** until all four exist |
| Codecov connection | `ci.yml` uploads coverage (non-failing without it) |
| A root `Dockerfile` | **present since close-out Phase 1 (2026-08-15)** — the docker build + container smoke jobs are active |

## Self-hosted runner machine prerequisites

What must exist on the box running the runner (all present on this Mac):

- miniforge Python 3.12 with `playwright` installed
  (`/opt/homebrew/Caskroom/miniforge/base/bin/python`)
- system Chrome (Playwright drives it via `channel="chrome"`)
- ffmpeg 7.x (video-harness experiments)
- Qdrant healthy on `127.0.0.1:6333` (`make qdrant-start`)
- msb-v3 server healthy on `127.0.0.1:8766` (`make server-start`)
- `~/video-harness/evidence/` with **fresh PASS** evidence for
  `p0_basic`, `p1_ffmpeg`, `p2_inference` (the harness stage gates on it;
  `make run`, `make run-p1`, `make run-p2` from `~/video-harness`)
- `~/bin/webcheck.py` (the Playwright driver; not yet vendored into the repo)

The workflow treats services as shared infrastructure: it starts Qdrant and
the server best-effort (idempotent) and never stops them.

## Verification

Before pushing:

```bash
make harness-gate-dryrun     # probes :6333 + :8766, then the endpoints,harness gate
```

Expect `ALL STEPS PASSED` and exit 0 on a healthy box.

First push expectations:

- `ci.yml` + `factory-gate.yml` run on hosted runners immediately.
- `harness-gate.yml` runs once a runner with the `self-hosted` + `macOS`
  labels is registered; its artifact contains the evidence report, stage
  logs, and endpoint screenshots.
- All three workflows also have a `workflow_dispatch` trigger — you can run
  any of them manually from the **Actions** tab without pushing.

## Known gaps (not repo blockers)

- `~/video-harness` is not a git repo — the harness stage depends on the
  machine-local copy of its evidence. Making it a repo (and pushing it)
  makes the evidence portable to any runner.
- `~/bin/webcheck.py` lives outside the repo; vendoring it into
  `scripts/` would remove the last machine-local file from the gate.
