# CI Environment Contract

Date: 2026-08-27
Status: in-progress

## Failure classes

Every non-pass result is classified as exactly one of:

- **CODE** — assertion, type, lint, runtime, or schema regression.
- **INFRASTRUCTURE** — runner, Docker registry, Qdrant process, or service startup failure.
- **ENVIRONMENT** — optional binary, dependency, credential, or configured capability is absent.
- **GOVERNANCE** — freeze, evidence, claim, history, or unauthorized-surface violation.
- **FLAKY/DETERMINISM** — race, timing, intermittent endpoint, or nondeterministic result.

“Probably infra” is not a classification; the failing preflight and captured diagnostic must identify the boundary.

## Dependency contracts

| Dependency | Default gate contract |
|---|---|
| Git history | Closure verification requires a full repository. A shallow clone fails with an explicit `Complete Git history required` diagnostic. |
| llama-server | Structural tests run without it. Live model tests run only when the live gate enables them; absence is an explicit skip outside that gate. |
| Research network | Structural request/configuration tests are offline. Live research runs only under explicit live mode; an unavailable required endpoint fails that live gate. |
| Qdrant | Retrieval unit tests use fakes/backends. Gates that require Qdrant must preflight reachability, API response, writable storage, and required collection before tests. |
| Docker | Image/container verification is a separate release gate. Registry/runner errors are infrastructure failures, not application failures. |
| Local services | CI must use an isolated process identity, temporary database/filesystem, and run-scoped port; it must never kill or reuse a developer service. |

## Evidence states

Each scenario must resolve to `PASS`, `EXPECTED FAILURE`, or `EXPECTED SKIP`. `UNKNOWN` is not an acceptable terminal state.
