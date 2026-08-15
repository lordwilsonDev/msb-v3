# MSB v3 Close-Out Blueprint — operational-for-you → walk-away-done

**Version:** 1.0
**Status:** Draft for approval
**Date:** 2026-08-14
**Owner:** Wilson (solo)
**Scope:** Close the remaining gaps between a repo that runs *on this Mac for
this operator* and one that is portable, truthful about its own capabilities,
scope-bounded, and safe to leave unattended. No new product features.

---

## 1. Context

An audit on 2026-08-14 (verified by running, not reading) scored MSB v3
**8.5/10** against the realistic solo/AI-built-repo baseline. The engineering
hygiene is top-few-percent:

| Check | Verified result |
|---|---|
| Tests | 946 passed, 3 skipped (skips are `MSB_LIVE=1`-gated live acceptance) |
| mypy | clean over 140 files, even without `--ignore-missing-imports` |
| Coverage | 83% actual, gated `--cov-fail-under=70` in `ci.yml` + `factory-gate.yml` |
| Failure visibility | 0 bare `except:`, 0 `except: pass` silent swallows |
| Dep scanning | `pip-audit --strict` (blocking) in CI |
| Secrets | `.env` untracked; `scripts/rotate_secrets.py` present |
| Releases | 5 tags, `version = "0.2.3"` in sync, Keep-a-Changelog CHANGELOG |
| Stubs | honestly excluded from work counters (`api/triumvirate.py:283`) |

The gap to 9.5+ is **not code quality**. It is three *operating/closing*
disciplines that a fast builder reaches last: **portability, truth-in-config,
and scope consolidation** — plus a short honest-ledger tail. This blueprint
closes exactly those and defines "done" so the project can be declared closed.

**Definition of "closed out":** MSB v3 can be (a) rebuilt and run from a fresh
clone on a machine that is not this one, (b) trusted to describe its own
capabilities without false fallbacks, (c) maintained at its real surface area,
and (d) left running unattended with its self-checks. When the acceptance
criteria in §6 pass, the project is closed and further work is opt-in
enhancement, not finishing.

---

## 2. Current state — verified ground truth (2026-08-14)

- **Runtime:** `msb-v3 0.2.3` live on `127.0.0.1:8766`, launchd `KeepAlive`,
  supervised by `scripts/run.sh` (restart-on-exit loop).
- **Working tree:** clean (0 uncommitted). Reproducibility gap from the
  2026-08-11 manifest ("~40 uncommitted") is **closed**.
- **`OPENAI_API_KEY`:** now **set** in `.env` — the `/v1` adapter is no longer
  fail-closed. Manifest gap #3 is **closed**.
- **Containerization:** the `msb_v3` app is **NOT** containerized. The only
  Dockerfiles (`docker/sovereign/Dockerfile.heart`, `Dockerfile.brain`) build
  the **archived** `src/sovereign_runtime/` package and their `CMD` is
  `bus.emit(...); time.sleep(3600)` — placeholder containers for retired code.
  They are a false "we have Docker" signal and must be removed or replaced.
- **llama.cpp fallback:** `LLAMA_CPP_MODEL` points at
  `~/models/gemma-4-12b-it/...gguf` which is **not on disk**. The advertised
  fallback backend cannot start — resilience theater while
  `MSB_ACTIVE_BACKEND=ollama`.
- **Scope:** 23.7K LOC src, 17K LOC tests, 32 API routers, ~20 subpackages.
  A prior scope pass ([dormant-satellites disposition, 2026-08-13](2026-08-13-dormant-satellites-disposition.md))
  already retired 6 dead modules — scope discipline exists; the remaining
  sprawl is *inside* `msb_v3` and is undocumented as to what is load-bearing.

---

## 3. Gap ledger — ranked by impact

| # | Gap | Evidence | Severity |
|---|---|---|---|
| G1 | `msb_v3` runtime not containerized; the whole system dies with this laptop and cannot be rebuilt elsewhere | no `Dockerfile` for the app; `REPO_REQUIREMENTS.md` docker job inert | **High** |
| G2 | Ceremony Dockerfiles ship for archived `sovereign_runtime` with `sleep(3600)` CMD — false capability signal | `docker/sovereign/Dockerfile.{heart,brain}` | **High** (trust) |
| G3 | Advertised llama.cpp fallback is non-functional (weights absent) | `~/models/gemma-4-12b-it/` NOT PRESENT; `config.py` `LLAMA_CPP_MODEL` | Medium |
| G4 | Load-bearing vs. optional subsystems are undocumented; 32 routers, solo-maintained | `src/msb_v3/api/` (32 files), no surface-area map | Medium |
| G5 | Fresh-clone reproducibility unproven end-to-end (build + run + smoke from a foreign path, in a container) | `make portability` exists for host; no container equivalent | Medium |
| G6 | No unattended-operation runbook consolidating the launchd jobs, daily gates, and the chain-anchor verify daemon | jobs exist but scattered across `scripts/launchd/` | Low |

---

## 4. Objectives & non-goals

**Objectives**
- O1 — MSB v3 builds into a container image and serves `/health` 200 from it.
- O2 — Every advertised capability is real or removed (no false fallbacks, no
  ceremony containers).
- O3 — The real surface area is documented: what is load-bearing, what is
  optional, what is frozen.
- O4 — A single close-out acceptance gate proves walk-away-done and is wired
  into CI so it cannot silently regress.

**Non-goals (explicitly deferred)**
- No new features, endpoints, or subsystems.
- No multi-host / Kubernetes / cloud deploy — a single portable container is
  the bar. (Remote exposure stays gated behind the existing WireGuard ADR.)
- No rewrite of working subsystems. Consolidation = document + optionally
  freeze, not refactor.

---

## 5. Phases

### Phase 1 — Portability (closes G1, G2, G5)

Make the actual runtime reproducible off this machine.

- **FR-1.1** A top-level `Dockerfile` MUST build the `msb_v3` package from a
  clean checkout (multi-stage: deps from `pyproject.toml`/`requirements.lock`,
  then `src/`), exposing `MSB_PORT` and running the uvicorn app.
- **FR-1.2** The image MUST start with `MSB_ACTIVE_BACKEND=ollama` pointing at
  a host/sibling Ollama (`OLLAMA_URL` overridable), and MUST NOT bake secrets
  or the vault into the image.
- **FR-1.3** A `docker-compose.yml` (or extension of the existing sovereign
  compose) MUST bring up `msb_v3` + `qdrant` with named volumes for
  `data/` and Qdrant `storage/`, addressing the documented Qdrant cwd footgun.
- **FR-1.4** The ceremony Dockerfiles for archived `sovereign_runtime`
  (`docker/sovereign/Dockerfile.{heart,brain}` + their compose) MUST be
  deleted or replaced by the real image; no container may ship a
  `sleep(3600)` placeholder CMD.
- **FR-1.5** `REPO_REQUIREMENTS.md`'s inert docker CI job MUST be activated to
  build the image and run a container `/health` smoke.
- **AC-1.1 [FR-1.1,1.2]:** `docker build` from a fresh clone succeeds; the
  container answers `GET /health` → `{"ok":true}` with no host Python.
- **AC-1.2 [FR-1.3]:** `docker compose up` yields a working `/chat` round-trip
  against Ollama and a `/rag/search` hit against the Qdrant volume.
- **AC-1.3 [FR-1.4]:** `grep -rn "sleep(3600)" docker/` returns nothing;
  `find . -name "Dockerfile*"` lists only real images.
- **AC-1.4 [FR-1.5]:** CI docker job is green on a clean run.

### Phase 2 — Truth-in-config (closes G3)

Every advertised capability is real or gone.

- **FR-2.1** EITHER the llama.cpp weights MUST be provisioned and the `:8080`
  backend proven to answer, OR the llama.cpp backend config
  (`LLAMA_CPP_URL`, `LLAMA_CPP_MODEL`, related `.env.example` rows) MUST be
  removed and documented as unsupported.
- **FR-2.2** `MANIFEST.md` §4/§9 and `README` MUST be updated so no row claims
  a backend/secret that is not actually available.
- **FR-2.3** `/system/health` deep check MUST report the *real* backend
  availability (green only for backends that can actually serve).
- **AC-2.1 [FR-2.1]:** No config path advertises an unreachable backend; a
  test asserts the active-backend health probe matches reality.
- **AC-2.2 [FR-2.2]:** `MANIFEST.md` "Gaps" §9 has zero open ⚠️/🔒 rows, or
  each remaining one is explicitly marked *accepted, out of close-out scope*.

### Phase 3 — Scope consolidation (closes G4)

Document the real surface; freeze the optional.

- **FR-3.1** A `docs/SURFACE.md` MUST classify every `api/` router and
  `src/msb_v3/*` subpackage as **load-bearing**, **optional**, or **frozen**,
  each with a one-line justification traced to a caller or a decision.
- **FR-3.2** Any subsystem classified **frozen** MUST be marked in-code (module
  docstring) and excluded from active-maintenance expectations; its tests stay
  green but it accrues no new work.
- **FR-3.3** The classification MUST reuse the Complexity Governor rule already
  cited in the dormant-satellites plan ("can existing infra solve it?").
- **AC-3.1 [FR-3.1]:** `docs/SURFACE.md` covers 32/32 routers and every
  subpackage; a lint/test asserts no router file is unclassified.
- **AC-3.2 [FR-3.2]:** Frozen modules carry the docstring marker and are listed
  in `SURFACE.md`.

### Phase 4 — Walk-away operations (closes G6)

One runbook, one gate, verifiable unattended.

- **FR-4.1** A `docs/operations/close-out-runbook.md` MUST consolidate: every
  launchd job (`msb-v3`, `qdrant`, chain-anchor-verify, daily/factory gates),
  its schedule, its state file, and its recovery command.
- **FR-4.2** A single `make close-out-gate` (or `scripts/close-out-gate.sh`)
  MUST run the full battery — tests, mypy `src`, ruff, coverage≥70,
  pip-audit, container `/health` smoke — and exit non-zero on any failure.
- **FR-4.3** The close-out gate MUST run in CI on `main` and be documented as
  the definition of done.
- **AC-4.1 [FR-4.2]:** `make close-out-gate` passes locally and in CI, red on
  any single-check failure.
- **AC-4.2 [FR-4.1]:** Following the runbook from a cold machine reproduces a
  running, self-verifying instance.

---

## 6. Definition of Done (close-out acceptance)

The project is **closed out** when all hold simultaneously:

- [ ] Container builds from a fresh clone and serves `/health` 200 (AC-1.1).
- [ ] `docker compose up` gives working `/chat` + `/rag/search` (AC-1.2).
- [ ] Zero ceremony containers; no `sleep(3600)` CMDs (AC-1.3).
- [ ] No advertised-but-unreachable backend or secret remains (AC-2.1/2.2).
- [ ] `docs/SURFACE.md` classifies 32/32 routers + all subpackages (AC-3.1).
- [ ] `make close-out-gate` green locally and in CI (AC-4.1).
- [ ] Close-out runbook reproduces a live instance from cold (AC-4.2).
- [ ] `CHANGELOG` entry + `v0.3.0` tag cut declaring close-out.

---

## 7. Edge cases & risks

- **EC-1:** Container can't reach host Ollama on Linux (no
  `host.docker.internal`) — compose MUST document the `--add-host` / bridge
  path, tested on at least one non-macOS target or explicitly noted as
  Docker-Desktop-only.
- **EC-2:** Qdrant volume launched from wrong cwd silently creates empty
  storage (known footgun) — compose MUST pin the working dir/volume.
- **EC-3:** Deleting llama.cpp config breaks a hidden import — guard with the
  full test suite before removal.
- **R-1:** Scope classification is subjective — mitigate by tracing each
  "load-bearing" claim to an actual caller, not intuition.
- **R-2:** Solo bandwidth — phases are independently shippable and ordered so
  value lands even if execution stops after P1.

---

## 8. Sequencing

P1 → P2 → P3 → P4, but **P1 and P2 are the release-blockers** (portability +
truth). P3 and P4 are what make it *stay* closed. Recommended: land P1 as
`v0.3.0-rc`, P2 folds in, tag `v0.3.0` at the §6 gate, P3/P4 as `v0.3.x`.

Estimated effort (solo, focused): P1 ~half a day, P2 ~1–2 hrs, P3 ~2–3 hrs,
P4 ~2 hrs. The blueprint's whole point is that none of this is hard — it is the
finishing work that expansion-mode defers.

---
*Grounded in live probes of :8766/:6333, `docker/sovereign/*`, `config.py`,
the test/mypy/coverage runs, and the git tree on 2026-08-14. Not derived from
memory or from the dashboard surface.*
