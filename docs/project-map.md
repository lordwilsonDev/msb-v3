# MSB v3 — Project Map & Scorecard

**Maintained by:** `sovereign-project-lifecycle-orchestrator`
**Evidence snapshot:** commit `1ddea5c` (2026-08-19) · tree clean · all 13 launchd agents green · ops suite 39/39

> **Evidence legend** (per the orchestrator's hierarchy — confidence rises down the list):
> `CLAIM` (unverified statement) → `DOCUMENTED` (requirement/spec) → `IMPLEMENTED` (code exists) →
> `TEST` (automated) → `ADVERSARIAL` (attack/failure testing) → `BENCHMARK` (measured) →
> `OBSERVED` (real-world run) → `LONGITUDINAL` (sustained observation).
> Where a rating depends on evidence that doesn't exist yet, it is `UNKNOWN` — and *unknown is not bad*.

---

## 1. Mission

A narrow, local-first, **governed** agent runtime: take a real task from request to a verified, evidence-backed result — and refuse, record, and recover when a model, tool, or permission fails. (`DOCUMENTED` — README + `docs/releases/MSB-v3-RELEASE.md`; `OBSERVED` — live loop.)

## 2. Users

Single operator (Lord Wilson) on a Mac Mini; enrolled signed devices (iPhone via Vesta); no external users. 30-day trial of latency evidence in progress. (`OBSERVED`)

## 3. Problem

A model cannot be trusted with consequential actions; a bare log cannot prove what happened. MSB answers both: fail-closed authorization + tamper-evident, replayable evidence. (`DOCUMENTED` — README, architecture.md)

## 4. Product

Source-available governed runtime: `/agent/handle` canonical loop, `/chat` + OpenAI-compatible `/v1` adapter, `/cron` scheduler, `/cockpit` + `/console` operator surfaces, `/vesta/*` signed-device trust perimeter, `/node/v1/*` device enrollment. (`IMPLEMENTED` + `TEST` — endpoint suite)

## 5. Architecture

FastAPI `:8766` · SQLite · Ollama/Qwen3 local models · Qdrant retrieval · Prometheus. Frozen canonical path:

```
/agent/handle → intent → task DAG → ActionGate (SAFE/REVIEW/BLOCK/FAIL)
→ governed tools → verification → evidence spine → audit chain → replay
```

The **Triumvirate** (Guardian scanner, Argus auditor, Hippocampus memory) — no actor writes durable state directly. (`DOCUMENTED` + `TEST` — architecture.md, canonical-journey.md, `tests/core/`)

## 6. Dependencies

9 pinned runtime deps (FastAPI, uvicorn, pydantic, httpx, prometheus-client, qdrant-client, cryptography, asn1crypto, python-pkcs11) — deliberately tiny; `pip-audit`-gated pins. (`IMPLEMENTED` + `TEST` — pyproject.toml, lock files)

## 7. Interfaces

REST (`/agent/handle`, `/chat`, `/cron/*`, `/vesta/*`, `/node/v1/*`), OpenAI-compatible `/v1`, CLI (`msb-v3`, `python -m msb_v3.cron`), Prometheus scrape, Open WebUI adapter, paseo adapter. (`IMPLEMENTED` + `TEST`)

## 8. Data flows

Request → intent → plan → ActionGate → governed tool → verification → evidence receipt (`logs/audit.jsonl`, one JSON line per cycle) → append-only `msb_ledger` (Merkle proof-of-inclusion, signed anchor) → replay. Retrieval is semantic with honest fallback. (`IMPLEMENTED` + `ADVERSARIAL` — `tests/uac/test_merkle.py` 23 tests)

## 9. Trust boundaries

Operator (bearer token / signed device) · ActionGate capability tiers (no cache, fail-closed) · Vesta perimeter (Phase 0–2: `model.inference` + `memory.read` only) · FILE_READ sandbox (`MSB_NODE_SANDBOX_ROOT`, traversal/symlink-escape fail-closed) · FILE_WRITE + SHELL_EXEC approval-only contracts (signed device ACK or maintenance token). (`IMPLEMENTED` + `ADVERSARIAL` — `tests/vesta/test_approval_bypass.py`, `tests/governance/test_bypass.py` 13 invariants)

## 10. Failure boundaries

Kill switch · budget caps · owner-approval queue · bounded retry + quarantine + replay · defined terminal states (never silent continuation) · failure matrix 11/11 modes, bypass 13/13, no P0/P1. (`ADVERSARIAL` + `BENCHMARK` — `tests/chaos/test_failure_matrix.py`, M5 soak: completion 1.0, unsafe-escape 0.0, recovery 1.0)

## 11. Persistence

SQLite (append-only triggers + hash chain) · `msb_ledger` standalone library (zero `msb_v3` imports, 30 guards) · Qdrant · DB backup keep-7, checksum-verified, restore drill proven over a corrupted runtime. (`IMPLEMENTED` + `TEST` + `OBSERVED` — restore drill 2026-08-17)

## 12. Models

Qwen3 8B via Ollama (served) · gemma-4-12b-it 6.9G weights present but **not served** (llama.cpp alt backend, intentional — top move-to-external-drive candidate). Frontier/cloud seam implemented, live use unverified. (`OBSERVED`)

## 13. Agents

Triumvirate (Guardian/Argus/Hippocampus) · MoIE pre-filter (keyword-based, externalized policy in `config/risk_templates.json`, fail-closed on corrupt policy — *not* the security boundary) · factory (classify → plan → build → test → review → verify → merge) with deterministic coherence scan as always-on reviewer · 6 built-in cron actions. (`IMPLEMENTED` + `TEST`)

## 14. Tools

Closed governed-tool registry behind the ActionGate — read/write/search/chat; MoIE can fail without becoming a safety failure because the authorization layer still catches what the pre-filter misses. (`IMPLEMENTED` + `ADVERSARIAL`)

## 15. Tests

172 test files, 1706 tests green from a foreign checkout (portability gate under bash 3.2) · ops suite 39/39 · failure matrix 11/11 · bypass 13/13 · Merkle 23 · ledger extraction 30 · hermetic YubiKey/Secure-Enclave suites. (`TEST` + `ADVERSARIAL`)

## 16. Deployment

Launchd (13 agents, Sunday cascade: backups 04:30/05:30 → rotation 06:00 → DB drill 06:30 → cache trim 06:40 → disk health 06:45 → self-audit 06:50 → replication 07:05; daily DB backup 03:00; heartbeat 12:00; watchdog every 15 min) · GitHub Actions on self-hosted runner (ci, factory-gate, harness-gate, release-verify) · pre-push gate (lint + portability) with signed-commit + DCO enforcement. (`OBSERVED` — all agents live and tracked green)

## 17. Operations

Weekly self-audit: regression suite + pull-signature ledger + source license → publishes dated report to `audit/` on origin (evidence is self-publishing) → watchdog alerts on any non-zero exit, in-flight false-alert protection, and missing-agent detection. License gate enforced at supervisor *and* Python entry point. (`OBSERVED` + `LONGITUDINAL` — 39/39 audit, live reports on origin, first incidents handled: ENOSPC, false alerts)

## 18. Known gaps

Dormant resilience slots (all config-driven, one line each): second witness not added · email/Telegram alerts off · heartbeat volume + replication target unset · disk at ~98% (≈5.5Gi free, ENOSPC previously hit) · `~/models` (6.9G) + Docker image (7.5G) are the durable headroom candidates, both need hardware. (`OBSERVED`)

## 19. Technical debt (prioritized — Impact × Probability × Irreversibility)

| Debt | Class | Priority |
|---|---|---|
| No DB schema versioning/migrations (documented limitation #1) | Data/Operational | HIGH — irreversible once data exists at scale |
| CLI provider is best-effort isolation, not a sandbox (L9 parked) | Security | HIGH — capability escape surface |
| Disk saturation blocks multi-modal + long-term evidence growth | Operational | HIGH — already caused an incident |
| Factory LLM reviewers miss seeded contradictions (mitigated: deterministic scan catches, hermetic-proven) | AI/Evidence | MEDIUM |
| Tenant chat routing not tenant-scoped (RAG is) | Security/Data | MEDIUM — becomes HIGH at multi-tenant (v4) |
| Deleted-file diffs not emitted by factory `compute_changes` | Code | LOW |
| Latency evidence is single-sample; grows with 30-day trial | Evidence | LOW |

## 20. Business constraints

Single operator · one Mac Mini · no cloud dependency on the core path · storage-bound (multi-modal parked on disk, v4) · MIT license + source-available access model. (`DOCUMENTED`)

## 21. Security constraints

Fail-closed everywhere · source license signed by owner key (anonymous pulls are inert) · commits signed + DCO · fork-to-request access · anchor keys in macOS keychain / Secure Enclave / YubiKey PIV (hermetic-tested) · notary: RFC 3161 timestamping implemented, off-box automation scheduled. (`IMPLEMENTED` + `TEST` + `OBSERVED`)

## 22. Evidence (the crown jewel)

Claim→evidence table in the release declaration with **machine-verified gate** (`scripts/verify-claims.py`: 13 claims, 21 evidence paths, enforced in lint/CI/pre-push) · Merkle receipts (third party with only anchor + one receipt can verify a single action) · replay from events · every run leaves an honest receipt (basis: `rerun` vs `inferred-from-logs` vs `decision-only`) · self-publishing weekly audits on origin. (`TEST` + `ADVERSARIAL` + `OBSERVED` + `LONGITUDINAL`)

---

## Phase classification (evidence-based, per subsystem)

```
Core runtime (loop, ActionGate, ledger, replay)  → HARDENED   (release v0.3.0, failure matrix,
                                                              soak, restore drill, claims gate)
Ops layer (agents, watchdog, audit)              → OPERATE    (13 agents live, self-publishing
                                                              audits, real incidents handled)
Vesta trust perimeter                            → VERIFY     (implemented, low-volume live usage,
                                                              approval paths adversarially tested)
Node device enrollment / engage                  → INTEGRATE  (first slice: scoped FILE_READ)
MoIE / evaluation harness (research)             → VERIFY     (powers factory reviewer; evidence
                                                              growing)
Factory (dogfood pipeline)                       → VERIFY     (docs-only change reached MERGED)
Product (use cases, canonical journey)           → DISCOVER   (single operator; 30-day trial is
                                                              the discovery instrument)
```

The project does **not** occupy one phase — the ops layer is a quarter turn ahead of the product layer, and that is correct for a single-operator system.

## Scorecard (Unknown ≠ Bad)

| Dimension | Rating | Evidence basis |
|---|---|---|
| Product | DEVELOPING | Mission + feature sequencing clear; single operator |
| Architecture | STRONG | Frozen canonical path, expansion freeze, standalone ledger |
| Code quality | STRONG | ruff + mypy pinned, 1706 tests, claims gate, E402 sweep |
| Testing | STRONG | 172 files, failure matrix, bypass, soak, hermetic suites |
| AI quality | DEVELOPING | MoIE pre-filter + factory reviewers; documented reviewer miss is caught deterministically; frontier seam unverified |
| Security | STRONG | ActionGate fail-closed, bypass 13/13, license gate, YubiKey/Secure Enclave, Merkle |
| Reliability | STRONG | Failure matrix 11/11, soak metrics, backup/restore drill; longitudinal capped by single machine |
| Observability | STRONG | audit.jsonl, Prometheus, cockpit/console, self-publishing audits |
| Operations | STRONG | 13 agents + watchdog + cascade; **gaps:** dormant alert/redundancy slots |
| Documentation | STRONG | README, architecture, runbook, canonical journey, verified claim table |
| Data | DEVELOPING | SQLite + Qdrant sound; no schema versioning; chat routing not tenant-scoped |
| Governance | STRONG | MoIE + ActionGate + ledger + notary + kill switch + budget caps + approval queue |
| Performance | ADEQUATE | p50 0.001s / p95 0.051s; single-sample, growing with trial |
| Cost | UNKNOWN | No measured cost model (local models, single operator) — not bad, unmeasured |
| User value | UNKNOWN | Single operator; 30-day trial in progress — not bad, unmeasured |
| Evidence | EXCELLENT | Machine-verified claims, Merkle receipts, replay, self-publishing audits |

## Orchestrator verdict

**VERDICT: YES — continue hardening operations; MEASURE before any scale decision.**

- **Next lifecycle gate:** close the single-point-of-failure gate — activate the three dormant resilience slots (second witness, out-of-band alerts, off-machine redundancy) and get durable disk headroom (move `~/models` or the Docker image to external storage). None of these need new architecture; all are config or hardware.
- **What moving to OPERATE→SCALE requires:** workload evidence that distribution is warranted (the distributed-systems role must *not* be activated merely because a second node exists).
- **DoD for the ops layer to be READY for the redundancy gate:** second witness signing ≥1 ledger entry · one out-of-band alert channel firing on a real failure · heartbeat + replication each proven once · disk ≥15% free sustained.
