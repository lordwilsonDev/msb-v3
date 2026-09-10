# MSB v3 production gate — design

**Author:** Buffy
**Date:** 2026-09-05
**Status:** Approved
**Reviewers:** Lord Wilson

## 1. Context

MSB v3 has multiple useful verification commands and CI jobs, but no single local release-candidate command that answers whether the current checkout is releasable. The existing `close-out-gate.sh` runs lint, tests with a 70% coverage floor, pip-audit, and Docker smoke; other evidence is distributed across `make portability`, `make hygiene`, `make smoke`, `make backup-verify`, release verification, and CI workflows.

The production gate is the aggregation boundary. It must preserve the distinction between a green engineering check, an unavailable environmental check, an explicitly waived check, and an actual failure. It must also produce a durable evidence manifest so a release claim can be reconstructed from the exact checkout and environment that was tested.

This specification covers the first production-gate slice only. Replay/idempotency enforcement, dependency failure taxonomy, broader adversarial corpus, restore-service verification, SLO/load thresholds, and mutation thresholds remain separate follow-up specifications and must not be implied as completed by this gate.

## 2. Functional requirements

### FR-1 — One canonical command

`make production-gate` MUST invoke one repository-owned gate runner and MUST exit non-zero when any required leg fails.

### FR-2 — Explicit gate catalog

The runner MUST execute and report these legs. The required test leg is the hermetic core; tests marked `integration` are not silently folded into it and must be exercised by the separate conditional/live integration leg when configured:

| Leg | Default class | Existing implementation |
|---|---|---|
| Repository/version identity | required | `git`, `pyproject.toml`, `MANIFEST.md` |
| Lint/type/claims/policy | required | `make lint` |
| Core test suite + coverage | required | hermetic `pytest tests/ -m 'not integration'` plus coverage floor |
| Secret scan | required | `scripts/scan-secrets.py --tree` |
| Dependency audit | required | `pip-audit` against locked requirements |
| Migration/stamping checks | required | `pytest tests/db` |
| Package/build verification | required | `python -m build` or equivalent installed build tool |
| Standalone ledger verification | required | `msb_ledger` verifier contract, once available; until then explicit `NOT_IMPLEMENTED` failure |
| Clean-install smoke | conditional | isolated temporary environment/checkout |
| Portability | conditional | `make portability` |
| Docker build/smoke | conditional | `make close-out-gate` Docker leg or equivalent |
| Live integration/provider checks | conditional | only when configured and explicitly enabled |

The catalog MUST be versioned in the evidence manifest. Adding a leg without updating the catalog and tests is a gate contract change.

### FR-3 — No silent skips

A leg MUST have exactly one result:

```text
PASS | FAIL | CONDITIONAL | WAIVED | NOT_IMPLEMENTED
```

A missing binary, unavailable daemon, missing credential, or unconfigured external dependency MUST NOT become `PASS` or an invisible skip.

### FR-4 — Required and conditional semantics

- `PASS`: the leg ran and passed.
- `FAIL`: the leg ran and failed, or a required leg could not run.
- `CONDITIONAL`: a conditional leg could not run because its documented environmental prerequisite was absent.
- `WAIVED`: an operator explicitly waived a conditional leg using the documented waiver mechanism.
- `NOT_IMPLEMENTED`: the repository has not yet supplied the required verifier/contract for a required leg; this is always release-blocking.

The overall verdict MUST be:

```text
PASS       when every required leg is PASS and every conditional leg is PASS
CONDITIONAL when required legs are PASS and one or more conditional legs are CONDITIONAL or WAIVED
FAIL       when any required leg is FAIL or NOT_IMPLEMENTED, or any non-waived conditional leg fails
```

The command MUST exit 0 only for `PASS` or, when explicitly selected, `CONDITIONAL` release-candidate mode. It MUST exit non-zero for `FAIL` and `NOT_IMPLEMENTED`. The default mode SHOULD be strict and reject conditional evidence; an operator MAY select `--allow-conditional` for a documented local release-candidate report.

### FR-5 — Durable evidence manifest

Every invocation MUST write one JSON manifest under `artifacts/production-gate/` containing:

- Schema version and gate-run ID.
- Start/end UTC timestamps.
- Git commit, branch, dirty state, and nearest tag.
- Python executable and version.
- Operating system and architecture.
- Runtime and dev lockfile SHA-256 hashes.
- Configured model identifiers, with secret values excluded.
- Gate catalog and per-leg result.
- Exit code and bounded stdout/stderr excerpts per leg.
- Coverage result when available.
- Explicit environmental blockers and waivers.
- Overall verdict.

The manifest MUST be written atomically and MUST remain useful when the gate fails. Secret values, private keys, tokens, and unrestricted vault content MUST NOT appear in it.

### FR-6 — Reproducible invocation

The runner MUST execute from the repository root, use the configured `MSB_PYTHON` or repository virtualenv, and pass explicit repository-relative paths. It MUST not kill or borrow an unrelated server, Qdrant process, or port.

### FR-7 — Existing dirty work protection

The runner MUST NOT modify, clean, reset, stash, stage, commit, or delete the working tree. Generated gate evidence MUST be written only to the designated artifact directory. A clean-tree requirement MAY be exposed as a release-profile check, but the default audit run MUST be safe against an already-dirty checkout.

### FR-8 — Waiver auditability

Every waiver MUST require a leg name and non-empty reason. The reason, operator identity if available, timestamp, and command invocation MUST be recorded in the manifest. A waiver MUST NOT convert a required leg into a pass.

### FR-9 — Failure-state and replay follow-ups are visible

The first gate MUST include explicit status fields for the separate production blockers:

- replay/idempotency enforcement: `OPEN` until the permissive `200/200` baseline is replaced by an enforced contract;
- dependency failure taxonomy: `OPEN` until system boundaries expose the six-state outcome model;
- mutation thresholds: `OPEN` until targeted mutation tests exist;
- service-level objectives: `OPEN` until end-to-end thresholds are declared and measured.

These fields are evidence of remaining work, not passing gate legs.

## 3. Non-functional requirements

### NFR-1 — Fail closed on missing required machinery

A missing required tool or verifier MUST produce `FAIL` or `NOT_IMPLEMENTED`, never `PASS`.

### NFR-2 — Deterministic result shape

The JSON manifest schema and status vocabulary MUST be stable and machine-readable. A failed subprocess MUST retain its exit code and bounded diagnostic output.

### NFR-3 — Safety

The runner MUST be read-only with respect to source and Git history. It MAY create temporary environments and scratch state outside the checkout, but MUST clean only paths it created.

### NFR-4 — Evidence retention

The manifest MUST be independently inspectable without rerunning the gate. The artifact path and schema version MUST be printed to stdout.

### NFR-5 — Initial thresholds

The first implementation MUST preserve the currently enforced 70% coverage floor and record the proposed 85% overall / 95% safety targets as follow-up thresholds. Raising the blocking threshold requires a separate decision because it changes the release baseline.

## 4. Acceptance criteria

### AC-1 — Required failure blocks

**Given** a required leg exits non-zero
**When** `make production-gate` completes
**Then** the overall verdict is `FAIL`, the command exits non-zero, and the manifest identifies that leg and exit code. (FR-1, FR-2, FR-5)

### AC-2 — Missing required verifier blocks

**Given** the standalone ledger verifier contract is not implemented
**When** the gate runs
**Then** that leg is `NOT_IMPLEMENTED`, the overall verdict is `FAIL`, and the gate does not claim production readiness. (FR-2, FR-4, FR-9, NFR-1)

### AC-3 — Conditional dependency is explicit

**Given** Docker is unavailable
**When** the gate runs without a waiver
**Then** the Docker leg is `CONDITIONAL`, the manifest records the blocker, and strict mode exits non-zero. (FR-3, FR-4, FR-5)

### AC-4 — Explicit waiver is recorded

**Given** an operator supplies `--waive docker --reason "Docker daemon unavailable"`
**When** the gate runs
**Then** Docker is `WAIVED`, the reason is present in the manifest, and no required leg is changed to PASS. (FR-4, FR-8)

### AC-5 — Successful run has reproducibility identity

**Given** all executed required legs pass
**When** the gate writes its manifest
**Then** the manifest contains commit, Python, OS/architecture, lock hashes, model identifiers, catalog version, per-leg results, and overall verdict. (FR-5, FR-6, NFR-2)

### AC-6 — Dirty checkout is not mutated

**Given** the checkout contains pre-existing modifications and untracked files
**When** the gate runs
**Then** it does not stage, reset, stash, delete, or modify those files; only the designated evidence artifact may be newly written. (FR-7, NFR-3)

### AC-7 — No secret leakage

**Given** environment variables contain provider credentials
**When** the gate writes or prints evidence
**Then** secret values and private-key material do not appear in the manifest or bounded diagnostic output. (FR-5, NFR-3)

### AC-8 — Follow-up blockers remain visible

**Given** replay/idempotency, failure taxonomy, mutation, or SLO work is incomplete
**When** the gate writes its manifest
**Then** each item is marked OPEN and the overall result cannot imply those controls are complete. (FR-9)

## 5. Edge cases

The required test leg is the hermetic core. Tests marked `integration` are
reported outside that leg and require an explicitly configured integration
execution path; they must not be allowed to target a developer-owned fixed
port during the hermetic release check.

The required test leg is the hermetic core. Tests marked `integration` are
reported outside that leg and require an explicitly configured integration
execution path; they must not be allowed to target a developer-owned fixed
port during the hermetic release check.

- EC-1: `make lint` fails before later legs; later independent legs SHOULD still run so the manifest is complete.
- EC-2: a command is missing; required leg = FAIL/NOT_IMPLEMENTED, conditional leg = CONDITIONAL.
- EC-3: Docker daemon unavailable; report CONDITIONAL unless waived, never pass silently.
- EC-4: pip-audit unavailable; dependency leg FAIL, not a skip.
- EC-5: lockfile missing or hash changes; dependency/reproducibility leg FAIL.
- EC-6: subprocess times out; leg FAIL with timeout classification and bounded output.
- EC-7: interrupted runner; atomic manifest is either absent or complete, never a valid partial JSON document.
- EC-8: environment includes secrets; redaction applies to command output and manifest fields.
- EC-9: dirty working tree; preserve it exactly.
- EC-10: gate run itself creates files under ignored artifact paths; those files are listed as generated evidence, not treated as source changes.

## 6. API/CLI contract

```text
make production-gate

scripts/production_gate.py [options]
  --allow-conditional
  --waive <leg> --reason <reason>   # repeatable
  --output-dir <path>
  --profile <strict|candidate>
```

JSON manifest shape:

```json
{
  "schema_version": 1,
  "gate_run_id": "string",
  "started_at": "ISO-8601",
  "finished_at": "ISO-8601",
  "identity": {
    "commit": "full sha",
    "branch": "string",
    "dirty": true,
    "tag": "string|null",
    "python": "string",
    "python_version": "string",
    "os": "string",
    "architecture": "string",
    "lock_hashes": {"runtime": "sha256", "dev": "sha256"},
    "models": {"chat": "string", "embedding": "string"}
  },
  "catalog_version": 1,
  "profile": "strict|candidate",
  "legs": [{
    "name": "string",
    "class": "required|conditional",
    "status": "PASS|FAIL|CONDITIONAL|WAIVED|NOT_IMPLEMENTED",
    "exit_code": 0,
    "started_at": "ISO-8601",
    "finished_at": "ISO-8601",
    "evidence": ["relative/path"],
    "detail": "redacted bounded text"
  }],
  "follow_up_blockers": {
    "replay_idempotency": "OPEN",
    "failure_taxonomy": "OPEN",
    "mutation_thresholds": "OPEN",
    "slos": "OPEN"
  },
  "overall_verdict": "PASS|FAIL|CONDITIONAL"
}
```

## 7. Data model

| Entity | Fields | Constraints |
|---|---|---|
| GateRun | ID, timestamps, profile, identity, verdict | One manifest per invocation; atomic write |
| GateLeg | name, class, status, exit code, evidence, detail | Exactly one status; names unique per catalog |
| Waiver | leg, reason, operator, timestamp | Only conditional legs; reason required |
| FollowUpBlocker | name, status, evidence | OPEN cannot be rendered as complete |

## 8. Out of scope

- OS-level isolation or sandboxing of CLI providers.
- Implementing replay protection itself.
- Implementing a new dependency failure taxonomy across all APIs.
- Raising coverage to 85%/95% without an approved threshold migration.
- New load-test framework or SLO values.
- Changing deployment, rollback, launchd, Docker, or external-provider behavior.
- Staging, committing, tagging, pushing, resetting, or cleaning the repository.

## 9. Required implementation tests

The implementation MUST add focused tests for:

1. Manifest identity and lock hashes.
2. Required-leg failure and exit code propagation.
3. Conditional unavailable leg and strict/candidate profile semantics.
4. Explicit waiver validation and manifest recording.
5. Atomic manifest behavior on failure/interruption.
6. Secret redaction.
7. Dirty-tree preservation.
8. Catalog uniqueness and status vocabulary.
9. Follow-up blockers remaining OPEN.
10. Make target wiring to the repository-owned runner.
