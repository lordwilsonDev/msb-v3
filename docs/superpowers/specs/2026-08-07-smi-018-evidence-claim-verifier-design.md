# SMI-018 v0.1 — Evidence Claim Verifier

Status: approved, ready for implementation plan
Date: 2026-08-07

## Origin

On 2026-08-07, a commit (`dd66dd3`, authored `lordwilson`) landed on `main`
describing a Phase 2 vertical slice — `core/factory.py`, three adapters
(`adapters/prime_agent/`, `adapters/gstack/`, `adapters/book_to_skill/`),
an orchestrator, and "53/53 tests passing" — as an already-completed,
verified checkpoint. None of it exists anywhere in the repository, on any
branch, or in any stash (confirmed directly: `git branch -a` plus a
filename search for every claimed path). Full findings in
`docs/audits/smi-017-forensic-review/RECONCILIATION.md`.

This is not the first time this repo has shipped a document claiming more
than the source tree can back up — `docs/audits/smi-017-forensic-review/production_risks.md`
independently found `artifacts/SMI-017/regression_report.json` claiming
"208 passed, 0 failed" when a clean run of the same command produced 1
failure. SMI-018 exists to make that specific failure mode — a doc says
"implemented," the repo can't prove it — mechanically detectable, in CI,
before it reaches `main`.

## Purpose

A deterministic repository-evidence gate that prevents an unsupported
`implemented` claim in a doc from being merged. It answers exactly one
question: **does the repository contain the artifacts this claim
references?**

## Non-goals (v0.1)

- Not a truth engine, semantic document reviewer, or claim-interpretation
  system. It never reads prose — only explicit, structured claim blocks.
- Not a runtime attestation system. No "verified" tier requiring execution
  proof, no test-execution, no environment/dependency/model-weight
  tracking. Deferred to a later version if ever needed.
- Not a provenance/authorship system. Commit references are parsed and
  reported, not validated for reachability, ancestry, or whether the
  commit actually touched the claimed files.
- Not a runtime subsystem. Nothing under `src/msb_v3/` changes. No new
  API endpoint, no new dependency, no persistent ledger/database. This is
  a standalone CI script, full stop — the failure it addresses happened in
  the documentation/supply-chain layer, not during application execution,
  and the fix shouldn't add coupling the failure didn't require.

## Claim format

A fenced block tagged `smi-018-claim`, anywhere in a matched `.md` file:

```
​```yaml
id: phase2-agent-factory
status: implemented

files:
  - src/msb_v3/core/factory.py
  - src/msb_v3/core/orchestrator/router.py

tests:
  - tests/test_factory.py

commit: a3149a6
​```
```

Parsed by a hand-rolled parser (no YAML dependency — repo has none today,
and adding one for this is disproportionate; see
`docs/audits/smi-017-forensic-review/technical_debt.md` on existing
dependency-hygiene debt). Grammar: each line is either `key: value`
(scalar) or `key:` followed by one or more indented `- item` lines (list).
Blank lines inside a block are ignored, for readability and to give the
parser an unambiguous field boundary.

Fields:
- `id` (required) — free-text identifier for the claim.
- `status` (required) — `planned` or `implemented`. Anything else is
  malformed.
- `files` (optional list) — paths checked with `Path.exists()`, relative
  to repo root.
- `tests` (optional list) — same check as `files`, separate field for
  readability/reporting only; mechanically identical.
- `commit` (optional scalar) — checked with `git cat-file -t <hash>`.
  **Informational only — never gates pass/fail.** It proves the object
  exists in the repo's object database, not that it's reachable from
  `main` or that it actually introduced the claimed files; treating it as
  gating evidence would be a false sense of rigor.

## Validation rules

| Claim shape | Result |
|---|---|
| missing `id` or `status` | FAIL — malformed |
| `status` not in `{planned, implemented}` | FAIL — malformed |
| `status: planned` | PASS — recorded, no repository check performed |
| `status: implemented`, no `files` and no `tests` (commit alone doesn't count) | FAIL — "implemented claim has no evidence target" |
| `status: implemented` with `files` and/or `tests` | each listed path checked with `Path.exists()`; any missing path → FAIL, listing exactly which paths are missing |

**Unknown evidence state is a failure state.** No silent skipping of
malformed blocks — a block that can't be parsed/validated fails the same
as a block with missing evidence, so a typo can't be used (accidentally or
otherwise) to dodge the gate.

## Scan scope

`docs/**/*.md`, full tree, on **every** CI run — not just files changed in
the current diff. A claim made in one commit can be falsified by an
unrelated later commit deleting the claimed file; diff-scoping would miss
that ("claim rot"). Excluded: `docs/README.md`, `docs/CHANGELOG.md`, and
any file under a directory literally named `notes/` or `research/`
anywhere in the `docs/` tree (none exist under `docs/` today, but excluded
pre-emptively since those paths are where speculative, non-claim prose
naturally accumulates).

## Architecture

```
CI (ci.yml)
 ├── test
 ├── lint
 ├── security
 ├── docker
 └── claims                          [NEW]
      │
      ▼
 scripts/verify_claims.py <docs-root> [--report-path PATH]
      │
      ├─ walk docs-root for *.md (respecting the exclusions above)
      ├─ extract every ```smi-018-claim fenced block
      ├─ parse (scalar / list grammar above)
      ├─ validate schema (id, status)
      ├─ status == planned  → record, no check
      ├─ status == implemented → check files/tests via Path.exists(),
      │                          check commit via `git cat-file -t` (report only)
      └─ write report JSON to --report-path
           (default: artifacts/smi018/claim_report.json; overridable so
           tests can point it at tmp_path instead of the real repo path)
      │
      ▼
 exit 0: no claim blocks found, or every implemented claim's files/tests exist
 exit 1: any malformed block, or any implemented claim missing a file/test
         (stderr: failing id + doc path + exactly which paths are missing)
```

## Report shape (`claim_report.json`)

```json
{
  "claims_found": 4,
  "implemented": 2,
  "planned": 2,
  "failures": [
    {
      "id": "phase2-agent-factory",
      "doc": "docs/audits/phase2_architecture_audit/phase2_blueprint.md",
      "missing_files": ["src/msb_v3/core/factory.py"],
      "missing_tests": []
    }
  ]
}
```

Written unconditionally — on both pass and fail — so CI always has a
machine-readable artifact to upload, not only on failure.

## Testing

Black-box, via `subprocess.run([sys.executable, "scripts/verify_claims.py",
str(tmp_docs_dir), "--report-path", str(tmp_report_path)])` against
fixture doc trees written into `pytest`'s `tmp_path`. Not an import of
`scripts/verify_claims.py` as a module via a `sys.path` hack — that exact
pattern is called out as a fragility root-cause in
`docs/audits/smi-017-forensic-review/production_risks.md` (#8), no reason
to repeat it here. Testing through the actual CLI contract (args in, exit
code + JSON out) is also strictly more meaningful than testing internals.

Cases:
1. No claim blocks anywhere → exit 0, `claims_found: 0`.
2. `planned`-only claim → exit 0, not checked against the filesystem.
3. `implemented` claim, all `files`/`tests` exist → exit 0.
4. `implemented` claim, one missing file → exit 1, `missing_files` lists
   exactly that path.
5. `implemented` claim with only `commit:`, no `files`/`tests` → exit 1,
   "no evidence target."
6. Malformed block (missing `id`) → exit 1.
7. Blank lines inside a block → still parses correctly.
8. Excluded paths (`README.md`, `notes/`, `research/`) containing a
   `smi-018-claim` block → not scanned, doesn't affect exit code.

## CI integration

New `claims` job in `.github/workflows/ci.yml`, parallel to the existing
`test`/`lint`/`security`/`docker` jobs, gated the same way (`needs:
preflight`). Runs `python scripts/verify_claims.py docs/` with the repo
root as the working directory — all `files`/`tests` paths in claim blocks,
and the `docs-root` / `--report-path` arguments themselves, resolve
relative to that. Uploads
`artifacts/smi018/claim_report.json` as a build artifact (matching the
existing `test-results-*` / `lint-reports` / `security-reports` upload
pattern already in the workflow) regardless of pass/fail.

## Acceptance criteria (v0.1 = done when)

- [ ] Detects a missing claimed file (the actual `dd66dd3` failure mode,
      reproduced as a fixture test case)
- [ ] Detects a missing claimed test
- [ ] Parses and reports (never gates on) referenced commits
- [ ] `planned` claims never block CI
- [ ] `implemented` claims with only a `commit:` field fail (no evidence
      target)
- [ ] Malformed blocks fail, not silently skipped
- [ ] Machine-readable `claim_report.json` written on every run
- [ ] Runs as its own CI job
- [ ] Zero changes under `src/msb_v3/`, zero new dependencies
- [ ] The introducing PR carries its own `smi-018-claim` block:
      `id: smi018-evidence-verifier`, `status: implemented`,
      `files: [scripts/verify_claims.py]`,
      `tests: [tests/test_verify_claims.py]` — and CI passes on it,
      proving the gate can verify itself before anything else depends on it

## Explicitly deferred (not v0.1)

- Commit-authorship/reachability/ancestry validation
- Execution/"verified" status tier
- Semantic or prose-based claim extraction
- Any persistent ledger (hash-chained or otherwise) — `git`/CI log
  history is the record for v0.1
- Retrofitting existing docs (including
  `docs/audits/phase2_architecture_audit/`) with claim blocks — a
  separate decision, not required for the gate to exist and work going
  forward

## Self-verification

```smi-018-claim
id: smi018-evidence-verifier
status: implemented
files:
  - scripts/verify_claims.py
tests:
  - tests/test_verify_claims.py
```
