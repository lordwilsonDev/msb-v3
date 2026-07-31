# Triumvirate Next Steps — Execution Plan

## Objective
Strengthen Triumvirate operational readiness: add integration tests, wire live status into home dashboard, extend Argus with retry/backoff audit runner, and document the API surface.

## Steps
1. Add cross-endpoint integration test for Triumvirate plan→lock→verify lifecycle
2. Add `/triumvirate/status/dashboard` lightweight snapshot endpoint for home dashboard
3. Add retry/backoff wrapper to Argus `run()` for resilient audit execution
4. Create `docs/triumvirate.md` with endpoint map and quickstart
5. Verify full suite green, commit, push
