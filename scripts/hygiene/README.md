# MSB-v3 Engineering Hygiene Suite

One entry point, ten experiments, each with a **single source of truth** (its
standalone runner). `hygiene_runner.py` is a thin delegating index — it runs
the standalone runners and aggregates their verdicts. It no longer contains
its own experiment implementations.

## Single entry point

```bash
# Run everything
python scripts/hygiene/hygiene_runner.py --all

# Run a subset (short names h05 or full names h05_contract both work)
python scripts/hygiene/hygiene_runner.py --only h05 h07
python scripts/hygiene/hygiene_runner.py --only h05_contract h07_auto_healing

# Run one
python scripts/hygiene/hygiene_runner.py h05

# Machine-readable aggregate (used by the hermes factory)
python scripts/hygiene/hygiene_runner.py --all --json
```

Exit code: `0` unless any experiment reports `fail`.

## Runner table

| Experiment | Standalone runner | Tests | What "pass" means | Requires live server? |
|---|---|---|---|---|
| `h01_load` | `h01_load_runner.py` | MCP proxy/tools under 10/50/100 concurrent bursts | zero errors, zero timeouts | yes (:8766) |
| `h02_restart` | `h02_restart_runner.py` | **real** process crash: SIGKILL the :8766 listener, supervisor (scripts/run.sh) respawns, probe entity re-hydrated from disk | probe entity byte-identical (same data + stored checksum) after real SIGKILL + supervised restart | yes (needs run.sh supervisor) |
| `h03_idempotency` | `h03_idempotency_runner.py` | repeated identical GET /mcp/status | 8/8 identical status + payload hash | yes |
| `h04_race` | `h04_race_runner.py` | real concurrent same-id + distinct-id writes to truth registry | no torn writes, no checksum violations, server stays up | yes |
| `h05_contract` | `h05_contract_fuzzing_runner.py` | valid/malformed/traversal MCP payloads | valid → 200; malformed → safe 4xx; vault 404 = valid | yes |
| `h06_audit` | `h06_audit_tampering_runner.py` | disk tampering of truth entity (checksum preserved / mismatch) | checksum-mismatch tamper rejected (409) | yes |
| `h07_heal` | `h07_auto_healing_runner.py` | audit-chain tamper → quarantine → repair → verify | tamper detected, quarantine marks, repair re-anchors with auditable `chain.repaired` event, chain verifies again | no (uses AuditChain in temp DB) |
| `h08_chaos` | `h08_chaos_runner.py` (+ `h08_chaos_proxy.py`) | stdlib-only asyncio TCP proxy injects latency 500ms / connection drop / response truncation | each fault yields its expected signature (slow-success, client-failure) AND full recovery (health+register+retrieve) through a fresh transparent proxy after each | yes |
| `h09_deps` | `h09_dependency_subtraction_runner.py` | remove truth registry dir mid-run, probe degraded mode, restore | service stays alive, degraded /ready, recovers after restore | yes |
| `h10_resource` | `h10_resource_chaos_runner.py` | 500-file flood, oversized payload, 200-request burst | oversized payload rejected with 413 (limit 256 KiB); service alive; burst under budget | yes |

## Honest verdicts

- `pass` — the property was actually demonstrated.
- `partial` — some of the contract held.
- `blocked` — the experiment cannot honestly run in this environment.
  Blocked is a non-fatal non-pass: it means "not proven", never a green.
- `fail` — a genuine red light.

Current state: **10/10 experiments pass** (factory gate `PASS`, 0 schema
violations). The last two previously-blocked experiments now have real
harnesses:

- h02 — real process kill (SIGKILL on the :8766 listener) + supervised
  restart via `scripts/run.sh`, probe entity verified byte-identical after
  the crash. Safety guard: refuses to kill if no supervisor is present.
- h08 — real fault injection via `h08_chaos_proxy.py` (stdlib asyncio TCP
  proxy: latency / connection drop / response truncation), with a recovery
  probe after every fault class.

Both are genuine findings-turned-fixes (h07 quarantine+repair, h10
payload-size enforcement) with pytest coverage.

## Environment

- Repo root resolves via `MSB_REPO` env or relative to each runner file —
  no hardcoded paths.
- Python: `MSB_PYTHON` env or `/opt/homebrew/Caskroom/miniforge/base/bin/python`.
- Server URL: `MSB_BASE_URL` (default `http://127.0.0.1:8766`); secret via
  `MCP_BRIDGE_SECRET` or `.env`.

## Artifact schema

Every artifact is validated against the hermes factory schema
(`~/.hermes/skills/engineering/engineering-hygiene-factory/schemas/experiment.yaml`)
by the factory aggregator (`run_factory.py`) and standalone
(`scripts/schema_validate.py` / `validate_artifacts.py`).
