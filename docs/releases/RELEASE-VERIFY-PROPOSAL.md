# Proposal — closing O1 (`release-verify` hermeticity)

**Status:** DECIDED — **Option A**, implemented on the branch (`ba310b5`).
Locally validated; end-to-end CI validation is the first tag cut after merge (O2).
**Blueprint:** PRODUCTION-CLOSURE-001 P1 · **Branch:** `closure/p1-release-verify-hermetic`

---

## What's already done on the branch (no decision needed)

1. **`test-tier markers`** registered in `pyproject.toml`: `live`, `chaos`,
   `integration`. The hermetic core is everything unmarked:
   `-m "not live and not chaos and not integration"` → **2997 / 3058** tests
   (61 deselected: 5 chaos + 1 live + 55 integration).
2. **`test_h08_chaos_proxy`** — port-wait 10 s → 30 s + dead-child detection.
   This was the dominant virgin-clone flake.
3. **`test_ollama_chat_endpoint`** — read-timeout → `pytest.skip`, not fail.
4. **`verify-release.sh`** — now prints failing test IDs into the workflow log
   (they were previously invisible in GitHub — only a count reached it).

That removes the two *known* `release-verify` failure classes. The open
question is what the gate should verify going forward.

---

## The decision: what does `release-verify` run?

Today `release-verify.yml` runs the **full suite** from a virgin clone on the
self-hosted runner, against a **hand-managed `:8766` server** declared via
`vars.MSB_BASE_URL`. That server is Wilson's own dev stack. Consequences:

- The gate depends on a service the workflow doesn't own or provision.
- ~55 integration + 1 live + 5 chaos tests are timing-sensitive and run on a
  box that is simultaneously hosting the full sovereign stack.
- A fresh clone on **any other machine** cannot reproduce the gate.

### Option A — Core-tier gate + self-provisioned server (recommended)

`release-verify.yml` changes to:

1. Source `scripts/ci-runtime.sh` (already exists, already tested) →
   `ci_runtime_init` + `ci_runtime_start_server` boot a **run-scoped** msb-v3
   on a random port, cleaned up on EXIT, touching no existing listener.
2. Export `MSB_BASE_URL` to that scoped server.
3. Run `pytest -m "not live"` — i.e. the hermetic **core + chaos + integration**
   tiers, all now pointed at the provisioned server. Drop only `live` (real
   model generation — not a release gate; quality is measured elsewhere).
4. Keep the virgin-clone + seed-fixtures + `test_release_versions` fast-fail.

**Result:** the gate is reproducible from a clean checkout on any comparable
Mac, owns everything it needs, and still exercises the integration surface.
Acceptance target becomes **3053 / 3053** (`not live`), 0 unexpected skips.

**Cost:** ~40 lines of workflow YAML; the integration tests must tolerate the
scoped server's empty DB (most already do — they POST then assert; a few
that assume seeded rows already `return` early on empty, e.g.
`test_argus_mulch_resolve`). Any that don't get a skip-on-unreachable guard
matching `test_harness.py`.

### Option B — Keep full-suite-vs-live, just harden

Leave the workflow as-is (full suite, hand-managed `:8766`). Add
skip-on-unreachable / retry guards to the ~4 unguarded integration files so
load timeouts skip instead of fail.

**Result:** less work now; the gate still can't be reproduced off Wilson's
box, still depends on an unowned service, and stays load-sensitive. Doesn't
meet the blueprint's "0 environmental assumptions / fresh clone succeeds".

---

## Recommendation

**Option A.** `ci-runtime.sh` was built for exactly this and is currently
dead infrastructure. It converts `release-verify` from "passes on Wilson's
machine when the stack is up and the box isn't busy" into a real,
portable release gate — which is the actual content of O1, and a
prerequisite for O2 (a tag is only as trustworthy as the gate behind it).

Sequencing: land Option A → integration tests green under the scoped server
→ then O2 (reconcile version identity + cut a CI-verified tag on HEAD).

---

## Outcome (chosen 2026-08-31)

Option A implemented — `scripts/verify-release.sh` + `.github/workflows/release-verify.yml`
on branch `closure/p1-release-verify-hermetic` (`ba310b5`).

**Local validation:** run-scoped msb-v3 booted healthy on a random port with the
launchd `:8766` untouched; `pytest -m "integration or chaos"` (60 tests) ran
green against the fresh empty-DB server — **59 passed, 1 graceful skip**
(`test_harness` read-timeout under load), 0 failures. No integration test
needed warm dev-server state.

**Still to do:** merge the branch → cut the first tag (O2) → the
`release-verify` workflow validates the whole flow end-to-end from a virgin
clone. Until that tag runs, O1 is "implemented + locally validated", not
"CI-proven".
